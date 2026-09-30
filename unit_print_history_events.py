"""Print history snapshots and app callbacks without Tk or app-type imports."""

from __future__ import annotations

import hashlib
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from unit_print_history import HISTORY_DIRECTORY, HistoryWriteError, PrintHistory


def block_history_data(block: Any) -> dict[str, Any]:
    """Snapshot queue metadata only; this function never opens or stats a PDF."""
    return {
        "block_id": block.history_id,
        "replaces_block_id": getattr(block, "replaces_block_id", ""),
        "student_name": block.name_separator_label,
        "subject": block.subject,
        "level": block.level,
        "round": block.round_name,
        "start": block.start,
        "end": block.end,
        "source": block.source,
        "order_id": getattr(block, "order_id", ""),
        "material_id": getattr(block, "material_id", ""),
        "copies": block.copies,
        "bundled": block.bundled,
        "pages_per_sheet": block.pages_per_sheet,
        "printer_override": block.printer_override,
        "material_kind": block.material_kind,
        "display_label": block.display_label,
        "selected_numbers": list(block.selected_numbers) if block.selected_numbers is not None else None,
        "printer_route": block.printer_route,
        "files": [
            {
                "number": item.number,
                "name": item.name,
                "path": str(item.path),
                "printer_override": item.printer_override,
                "pages_per_sheet": item.pages_per_sheet,
            }
            for item in block.files
        ],
    }


def job_history_data(job: Any, printer: str, print_path: Path | None = None) -> dict[str, Any]:
    block = job.history_block
    if job.is_name_separator:
        kind = "name_separator"
    elif job.is_omr:
        kind = "omr"
    elif job.is_separator:
        kind = "separator"
    else:
        kind = "pdf"
    return {
        "job_id": job.job_id,
        "block_id": block.get("block_id", ""),
        "student_name": block.get("student_name", ""),
        "order_id": block.get("order_id", ""),
        "material_id": block.get("material_id", ""),
        "block_summary": job.block_summary,
        "block_index": job.block_index,
        "block_total": job.block_total,
        "copies": job.copies,
        "bundle_no": job.bundle_no,
        "bundle_total": job.bundle_total,
        "kind": kind,
        "pages_per_sheet": job.pages_per_sheet,
        "printer": printer,
        "pdf_path": str(job.item.path),
        "printed_pdf_path": str(job.item.path if print_path is None else print_path),
    }


def fingerprint_pdf(path: Path) -> dict[str, Any]:
    """Hash a PDF in bounded chunks. Call only from a worker, before dispatch."""
    digest = hashlib.sha256()
    byte_count = 0
    with Path(path).open("rb") as stream:
        before = os.fstat(stream.fileno())
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            byte_count += len(chunk)
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or byte_count != after.st_size:
        raise OSError(f"이력 해시를 계산하는 동안 PDF가 변경되었습니다: {path}")
    return {"sha256": digest.hexdigest(), "size": after.st_size, "mtime_ns": after.st_mtime_ns}


class PrintHistoryMixin:
    def initialize_history(self) -> None:
        self.print_history = PrintHistory(HISTORY_DIRECTORY)
        self.history_run_id: str | None = None
        self._history_queue_snapshot: dict[str, dict[str, Any]] = {}
        self._history_state_lock = threading.RLock()
        self._history_last_error: str | None = None
        started = self.record_history("session_started")
        self.history_session_started_at = started["timestamp"] if started is not None else None

    def _history_lock(self) -> Any:
        lock = getattr(self, "_history_state_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._history_state_lock = lock
        return lock

    def record_history(self, event: str, required: bool = False, **data: Any) -> dict[str, Any] | None:
        history = getattr(self, "print_history", None)
        if history is None:
            return None
        write_error: HistoryWriteError | None = None
        should_log = False
        with self._history_lock():
            data.setdefault("run_id", getattr(self, "history_run_id", None))
            try:
                record = history.record(event, **data)
            except HistoryWriteError as error:
                write_error = error
                error_text = str(error)
                if error_text != getattr(self, "_history_last_error", None):
                    self._history_last_error = error_text
                    should_log = True
            else:
                self._history_last_error = None
                return record
        # Worker log() may wait for Tk's main thread. Release the history lock
        # before crossing that boundary, so a UI queue capture cannot deadlock.
        if should_log:
            self.log(f"인쇄 이력 저장 실패 ({event}): {write_error}")
        if required and write_error is not None:
            raise write_error
        return None

    def capture_queue_history(self) -> None:
        history = getattr(self, "print_history", None)
        if history is None:
            return
        write_error: HistoryWriteError | None = None
        should_log = False
        with self._history_lock():
            previous = dict(getattr(self, "_history_queue_snapshot", {}))
            current = {
                snapshot["block_id"]: snapshot
                for block in getattr(self, "blocks", ())
                for snapshot in (block_history_data(block),)
            }
            events: list[tuple[str, dict[str, Any]]] = []
            for index, (block_id, snapshot) in enumerate(previous.items()):
                if block_id not in current:
                    events.append(("block_removed", {
                        "run_id": None, "block": snapshot, "queue_index": index,
                    }))
            for index, (block_id, snapshot) in enumerate(current.items()):
                event = "block_added" if block_id not in previous else "block_updated"
                if block_id not in previous or snapshot != previous[block_id]:
                    events.append((event, {
                        "run_id": None, "block": snapshot, "queue_index": index,
                    }))
            same_members = previous.keys() == current.keys()
            reordered = same_members and list(previous) != list(current)
            if reordered:
                events.append(("queue_reordered", {
                    "run_id": None, "block_order": list(current), "blocks": list(current.values()),
                }))
            if not events:
                return
            if len(events) == 1:
                event, data = events[0]
                if self.record_history(event, **data) is not None:
                    self._history_queue_snapshot = current
                return
            try:
                history.record_many(events)
            except HistoryWriteError as error:
                # A failed write may have left a prefix on disk. Keep every old
                # snapshot so the next capture retries all unconfirmed changes.
                write_error = error
                error_text = str(error)
                if error_text != getattr(self, "_history_last_error", None):
                    self._history_last_error = error_text
                    should_log = True
            else:
                self._history_last_error = None
                self._history_queue_snapshot = current
        if should_log:
            self.log(f"인쇄 이력 저장 실패 (queue_changes): {write_error}")

    def begin_history_run(self, options: Any, retry: bool = False) -> None:
        if getattr(self, "print_history", None) is None:
            return
        with self._history_lock():
            previous_run_id = getattr(self, "history_run_id", None)
            self.history_run_id = str(uuid.uuid4())
            try:
                self.record_history(
                    "run_started",
                    required=True,
                    run_kind="retry" if retry else "new",
                    dry_run=options.dry_run,
                    printer=options.printer_name,
                    duplex=options.duplex,
                    engine=options.engine,
                    queue_batch_size=options.queue_batch_size,
                    queue_refill_threshold=options.queue_refill_threshold,
                )
            except HistoryWriteError:
                self.history_run_id = previous_run_id
                raise
