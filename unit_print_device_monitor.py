"""Background polling and blocking waits for physical printer status."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from unit_print_canon_status import CanonStatusClient
from unit_print_device_state import DeviceJob, DeviceSnapshot


@dataclass(frozen=True)
class DeviceWaitResult:
    snapshot: DeviceSnapshot
    job: DeviceJob | None = None
    cancelled: bool = False
    timed_out: bool = False


class PrinterDeviceMonitor:
    def __init__(
        self,
        client: CanonStatusClient,
        *,
        idle_interval_seconds: float = 12.0,
        active_interval_seconds: float = 2.0,
        on_update: Callable[[DeviceSnapshot], None] | None = None,
    ) -> None:
        self.client = client
        self.idle_interval_seconds = max(2.0, float(idle_interval_seconds))
        self.active_interval_seconds = max(0.8, float(active_interval_seconds))
        self.on_update = on_update
        self._condition = threading.Condition()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot = DeviceSnapshot.unavailable("프린터 상태 연결 전", source_url=client.base_url)
        self._active_waiters = 0
        self._fast_probe_sessions = 0

    @property
    def latest_snapshot(self) -> DeviceSnapshot:
        with self._condition:
            return self._snapshot

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="printer-device-monitor", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        self._wake_event.set()
        with self._condition:
            self._condition.notify_all()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, timeout))

    def refresh_now(self) -> None:
        self._wake_event.set()

    def pause_full_snapshots(self) -> None:
        """Reserve the Canon session for a short ordered refill burst."""

        with self._condition:
            self._fast_probe_sessions += 1

    def resume_full_snapshots(self) -> None:
        with self._condition:
            self._fast_probe_sessions = max(0, self._fast_probe_sessions - 1)
            self._condition.notify_all()
        self.refresh_now()

    def wait_for_change(self, observed_at: datetime, timeout_seconds: float = 1.0) -> DeviceSnapshot:
        deadline = time.monotonic() + max(0.05, timeout_seconds)
        with self._condition:
            while self._snapshot.observed_at == observed_at and not self._stop_event.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=remaining)
            return self._snapshot

    def _publish(self, snapshot: DeviceSnapshot) -> None:
        with self._condition:
            self._snapshot = snapshot
            self._condition.notify_all()
        if self.on_update is not None:
            try:
                self.on_update(snapshot)
            except Exception:
                # Status reporting must never kill monitoring or printing.
                pass

    def _run(self) -> None:
        while not self._stop_event.is_set():
            # A refill burst uses the lightweight active-job endpoint.  Do
            # not let a multi-page snapshot monopolize the Canon session in
            # between two adjacent submissions.
            with self._condition:
                while self._fast_probe_sessions > 0 and not self._stop_event.is_set():
                    self._condition.wait(timeout=0.25)
            if self._stop_event.is_set():
                break
            try:
                snapshot = self.client.read_snapshot()
            except Exception as exc:
                snapshot = DeviceSnapshot.unavailable(str(exc), source_url=self.client.base_url)
            self._publish(snapshot)
            with self._condition:
                active = self._active_waiters > 0 or bool(snapshot.active_jobs)
            delay = self.active_interval_seconds if active else self.idle_interval_seconds
            self._wake_event.wait(delay)
            self._wake_event.clear()

    @staticmethod
    def _find_job(jobs: tuple[DeviceJob, ...], dispatch_token: str) -> DeviceJob | None:
        wanted = dispatch_token.casefold()
        for job in jobs:
            if job.dispatch_token.casefold() == wanted:
                return job
            if wanted and wanted in job.document_name.casefold():
                return job
        return None

    def wait_for_job_acceptance(
        self,
        dispatch_token: str,
        *,
        cancel_event: threading.Event,
        timeout_seconds: float = 30.0,
        poll_interval_seconds: float = 0.2,
        recent_interval_seconds: float = 1.5,
    ) -> DeviceWaitResult:
        """Wait until Canon itself exposes an exact dispatch token.

        The active list is much cheaper than a full status snapshot.  The job
        log is sampled less often so a one-page job that completed between two
        active-list reads still counts as acknowledged.
        """

        started_at = time.monotonic()
        deadline = started_at + max(0.05, float(timeout_seconds))
        poll_interval = max(0.01, float(poll_interval_seconds))
        recent_interval = max(poll_interval, float(recent_interval_seconds))
        # Give the cheap active list first chance.  The job log endpoint is
        # intentionally delayed because it is substantially slower.
        next_recent_check = started_at + recent_interval
        with self._condition:
            self._active_waiters += 1
            self._fast_probe_sessions += 1
        try:
            while time.monotonic() < deadline:
                if cancel_event.is_set() or self._stop_event.is_set():
                    return DeviceWaitResult(self.latest_snapshot, cancelled=True)

                snapshot = self.latest_snapshot
                job = snapshot.find_job(dispatch_token)
                if job is not None:
                    return DeviceWaitResult(snapshot, job=job)

                try:
                    active_jobs = self.client.read_active_jobs()
                    job = self._find_job(active_jobs, dispatch_token)
                    if job is not None:
                        return DeviceWaitResult(snapshot, job=job)

                    now = time.monotonic()
                    if now >= next_recent_check:
                        recent_jobs = self.client.read_recent_jobs()
                        job = self._find_job(recent_jobs, dispatch_token)
                        if job is not None:
                            return DeviceWaitResult(snapshot, job=job)
                        next_recent_check = now + recent_interval
                except Exception:
                    # A transient read failure must not turn an already sent
                    # document into retryable work.  Retry until the bounded
                    # acceptance timeout, then return an uncertain result.
                    pass

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                cancel_event.wait(min(poll_interval, remaining))
            return DeviceWaitResult(self.latest_snapshot, timed_out=True)
        finally:
            with self._condition:
                self._active_waiters = max(0, self._active_waiters - 1)
                self._fast_probe_sessions = max(0, self._fast_probe_sessions - 1)
