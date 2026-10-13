"""Durable-neutral state machines for guarded physical-device dispatch."""

from __future__ import annotations

from dataclasses import dataclass

from unit_print_device_state import DeviceJobState, DeviceSnapshot


STALE_READ_HOLD_THRESHOLD = 3


class DeviceDispatchHold:
    """Debounce connectivity holds without permitting stale dispatch."""

    def __init__(self) -> None:
        self.kind = ""
        self._stale_reads = 0
        self._last_observed_at = None

    def observe(self, snapshot: DeviceSnapshot, *, job_held: bool = False) -> bool:
        """Return True only when entering a hold or escalating stale to fault."""
        previous = self.kind
        if snapshot.connectivity_only and not job_held:
            if snapshot.consecutive_stale_reads:
                # This count includes normal reads the worker may have missed.
                self._stale_reads = snapshot.consecutive_stale_reads
            elif snapshot.observed_at != self._last_observed_at:
                self._stale_reads += 1
            if self._stale_reads >= STALE_READ_HOLD_THRESHOLD and not self.kind:
                self.kind = "stale"
        else:
            self._stale_reads = 0
            if not snapshot.can_dispatch or job_held:
                self.kind = "fault"
        self._last_observed_at = snapshot.observed_at
        return self.kind != previous

    def can_resume(self, snapshot: DeviceSnapshot, *, operator_resume: bool, job_held: bool = False) -> bool:
        return (
            bool(self.kind)
            and snapshot.can_dispatch
            and not job_held
            and (self.kind == "stale" or operator_resume)
        )

    def resume(self) -> None:
        self.kind = ""
        self._stale_reads = 0


@dataclass(frozen=True)
class RollingEntry:
    job_id: str
    token: str
    submitted_order: int


@dataclass(frozen=True)
class RollingReconcileResult:
    completed: tuple[RollingEntry, ...] = ()
    failed: tuple[RollingEntry, ...] = ()
    held: tuple[RollingEntry, ...] = ()
    observed: tuple[RollingEntry, ...] = ()


class RollingDispatchWindow:
    """Track the bounded set submitted to one physical printer.

    Entries are registered strictly in app queue order.  Device observations
    may finish in a different order, so reconciliation removes only the exact
    token that Canon reports and never converts an unseen token into retryable
    work.
    """

    def __init__(self, max_outstanding: int = 6) -> None:
        if not 1 <= int(max_outstanding) <= 10:
            raise ValueError("선전송 창은 1~10건이어야 합니다.")
        self.max_outstanding = int(max_outstanding)
        self._entries: list[RollingEntry] = []
        self._next_order = 1

    @property
    def entries(self) -> tuple[RollingEntry, ...]:
        return tuple(self._entries)

    @property
    def outstanding_count(self) -> int:
        return len(self._entries)

    @property
    def slots_available(self) -> int:
        return self.max_outstanding - len(self._entries)

    def unobserved_entries(self, snapshot: DeviceSnapshot) -> tuple[RollingEntry, ...]:
        """Return submissions not yet acknowledged by the physical device.

        Sumatra can finish while Windows is still handing a job to the
        printer.  In particular, separate Windows queues that target the same
        Canon device have no shared FIFO boundary.  The next app job may be
        submitted only after Canon itself has shown every earlier token in its
        active list or completion log.
        """

        return tuple(
            entry for entry in self._entries if snapshot.find_job(entry.token) is None
        )

    def register(self, job_id: str, token: str) -> RollingEntry:
        if self.slots_available <= 0:
            raise RuntimeError("선전송 창이 가득 찼습니다.")
        if any(entry.token.casefold() == token.casefold() for entry in self._entries):
            raise ValueError(f"중복 dispatch token: {token}")
        entry = RollingEntry(job_id, token, self._next_order)
        self._next_order += 1
        self._entries.append(entry)
        return entry

    def reconcile(self, snapshot: DeviceSnapshot) -> RollingReconcileResult:
        completed: list[RollingEntry] = []
        failed: list[RollingEntry] = []
        held: list[RollingEntry] = []
        observed: list[RollingEntry] = []
        for entry in self._entries:
            job = snapshot.find_job(entry.token)
            if job is None:
                continue
            observed.append(entry)
            if job.state == DeviceJobState.COMPLETED:
                completed.append(entry)
            elif job.state == DeviceJobState.FAILED:
                failed.append(entry)
            elif job.state == DeviceJobState.HELD:
                held.append(entry)
        completed_tokens = {entry.token.casefold() for entry in completed}
        if completed_tokens:
            self._entries = [
                entry
                for entry in self._entries
                if entry.token.casefold() not in completed_tokens
            ]
        return RollingReconcileResult(
            completed=tuple(completed),
            failed=tuple(failed),
            held=tuple(held),
            observed=tuple(observed),
        )
