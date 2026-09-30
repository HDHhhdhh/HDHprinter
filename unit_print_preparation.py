"""Bounded PDF preparation ahead of ordered printer dispatch."""

from __future__ import annotations

import itertools
import queue
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from unit_print_pdf_printing import materialize_four_up_pdf


PRINT_PREPARATION_PRIORITY = 0
MATERIAL_BUILD_PRIORITY = 100


@dataclass(frozen=True, slots=True)
class CommitToken:
    epoch: int
    sequence: int


class OrderedCommitQueue:
    """Release UI commits in request order without blocking the UI thread."""

    def __init__(self) -> None:
        self._epoch = 0
        self._next_sequence = 0
        self._next_commit = 0
        self._ready: dict[int, Callable[[], None] | None] = {}

    @property
    def pending_count(self) -> int:
        return self._next_sequence - self._next_commit

    def reserve(self) -> CommitToken:
        token = CommitToken(self._epoch, self._next_sequence)
        self._next_sequence += 1
        return token

    def complete(self, token: CommitToken, callback: Callable[[], None]) -> bool:
        if token.epoch != self._epoch or token.sequence < self._next_commit:
            return False
        self._ready[token.sequence] = callback
        self._drain()
        return True

    def cancel(self, token: CommitToken) -> bool:
        if token.epoch != self._epoch or token.sequence < self._next_commit:
            return False
        self._ready[token.sequence] = None
        self._drain()
        return True

    def invalidate(self) -> None:
        self._epoch += 1
        self._next_sequence = 0
        self._next_commit = 0
        self._ready.clear()

    def _drain(self) -> None:
        first_error: BaseException | None = None
        while self._next_commit in self._ready:
            callback = self._ready.pop(self._next_commit)
            self._next_commit += 1
            if callback is None:
                continue
            try:
                callback()
            except BaseException as error:  # keep later user requests unblocked
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error


class PriorityWorkScheduler:
    """One lazy worker shared by material builds and print look-ahead work."""

    def __init__(self, *, thread_name: str = "quick-print-pdf") -> None:
        self._thread_name = thread_name
        self._lock = threading.Lock()
        self._items: queue.PriorityQueue[
            tuple[int, int, Future[Any] | None, Callable[..., Any] | None, tuple[Any, ...], dict[str, Any]]
        ] = queue.PriorityQueue()
        self._sequence = itertools.count()
        self._thread: threading.Thread | None = None
        self._closed = False

    def _ensure_started_locked(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name=self._thread_name,
            daemon=True,
        )
        self._thread.start()

    def submit(
        self,
        callback: Callable[..., Any],
        *args: Any,
        priority: int = MATERIAL_BUILD_PRIORITY,
        **kwargs: Any,
    ) -> Future[Any]:
        future: Future[Any] = Future()
        with self._lock:
            if self._closed:
                raise RuntimeError("PDF 준비 작업기가 종료되었습니다.")
            self._ensure_started_locked()
            self._items.put((priority, next(self._sequence), future, callback, args, kwargs))
        return future

    def _run(self) -> None:
        while True:
            _priority, _sequence, future, callback, args, kwargs = self._items.get()
            try:
                if future is None or callback is None:
                    return
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    result = callback(*args, **kwargs)
                except BaseException as error:
                    future.set_exception(error)
                else:
                    future.set_result(result)
            finally:
                self._items.task_done()

    def close(self, *, wait: bool = False) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            thread = self._thread
            while True:
                try:
                    item = self._items.get_nowait()
                except queue.Empty:
                    break
                try:
                    future = item[2]
                    if future is not None:
                        future.cancel()
                finally:
                    self._items.task_done()
            if thread is not None:
                self._items.put((10**9, next(self._sequence), None, None, (), {}))
        if wait and thread is not None and thread is not threading.current_thread():
            thread.join()


class BackgroundBuildService:
    """Run material construction without exposing Tk objects to the worker."""

    def __init__(self, scheduler: PriorityWorkScheduler) -> None:
        self._scheduler = scheduler
        self._lock = threading.Lock()
        self._futures: set[Future[Any]] = set()
        self._closed = False

    def submit(
        self,
        work: Callable[[], Any],
        deliver: Callable[[Future[Any]], None],
    ) -> Future[Any]:
        with self._lock:
            if self._closed:
                raise RuntimeError("자료 준비 서비스가 종료되었습니다.")
            future = self._scheduler.submit(work, priority=MATERIAL_BUILD_PRIORITY)
            self._futures.add(future)

        def finished(done: Future[Any]) -> None:
            with self._lock:
                self._futures.discard(done)
            deliver(done)

        future.add_done_callback(finished)
        return future

    def cancel_pending(self) -> None:
        with self._lock:
            futures = tuple(self._futures)
        for future in futures:
            future.cancel()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.cancel_pending()


@dataclass(frozen=True, slots=True)
class SourceSignature:
    path: Path
    size: int
    mtime_ns: int

    @classmethod
    def read(cls, path: Path) -> "SourceSignature":
        resolved = Path(path).resolve()
        stat = resolved.stat()
        return cls(resolved, stat.st_size, stat.st_mtime_ns)


@dataclass(frozen=True, slots=True)
class PreparationKey:
    source: SourceSignature
    pages_per_sheet: int


class PrintPreparationService:
    """Prepare a tiny look-ahead window without changing dispatch order."""

    def __init__(
        self,
        cache_dir: Path,
        *,
        worker_count: int = 1,
        four_up: Callable[[Path, Path], Path] = materialize_four_up_pdf,
        scheduler: PriorityWorkScheduler | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.four_up = four_up
        self._scheduler = scheduler
        self._executor = (
            None
            if scheduler is not None
            else ThreadPoolExecutor(
                max_workers=max(1, worker_count),
                thread_name_prefix="quick-print-prepare",
            )
        )
        self._lock = threading.Lock()
        self._futures: dict[PreparationKey, Future[Path]] = {}
        self._closed = False

    @staticmethod
    def key_for(job: Any) -> PreparationKey | None:
        pages_per_sheet = int(job.pages_per_sheet)
        if pages_per_sheet == 1:
            return None
        if pages_per_sheet != 4:
            raise ValueError(f"지원하지 않는 한 면 페이지 수입니다: {pages_per_sheet}")
        return PreparationKey(SourceSignature.read(job.item.path), pages_per_sheet)

    def _submit(self, key: PreparationKey) -> Future[Path]:
        with self._lock:
            if self._closed:
                raise RuntimeError("PDF 준비 서비스가 종료되었습니다.")
            future = self._futures.get(key)
            if future is None:
                if self._scheduler is not None:
                    future = self._scheduler.submit(
                        self.four_up,
                        key.source.path,
                        self.cache_dir,
                        priority=PRINT_PREPARATION_PRIORITY,
                    )
                else:
                    assert self._executor is not None
                    future = self._executor.submit(
                        self.four_up,
                        key.source.path,
                        self.cache_dir,
                    )
                self._futures[key] = future
            return future

    def prefetch_jobs(self, jobs: list[Any] | tuple[Any, ...], *, limit: int = 2) -> None:
        """Schedule at most ``limit`` jobs after the current one."""

        # Put the current job first so look-ahead work can never delay it.
        for job in tuple(jobs)[:1 + max(0, limit)]:
            key = self.key_for(job)
            if key is not None:
                self._submit(key)

    def resolve_job(self, job: Any) -> Path:
        key = self.key_for(job)
        if key is None:
            return Path(job.item.path)
        result = self._submit(key).result()
        if SourceSignature.read(key.source.path) != key.source:
            raise OSError(f"PDF 준비 중 원본이 변경되었습니다: {key.source.path}")
        if not result.is_file():
            raise OSError(f"준비된 PDF를 찾을 수 없습니다: {result}")
        return result

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            futures = tuple(self._futures.values())
        for future in futures:
            future.cancel()
        if self._executor is not None:
            # This service owns the executor, so close is the lifecycle
            # boundary: wait for an already-running preparation to finish
            # before its cache directory can be removed by the caller.
            self._executor.shutdown(wait=True, cancel_futures=True)
