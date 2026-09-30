from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from unit_print_history import HistoryWriteError, PrintHistory, read_history
from unit_print_history_events import (
    PrintHistoryMixin,
    block_history_data,
    fingerprint_pdf,
    job_history_data,
)


def block(block_id: str, student: str = "김학생") -> SimpleNamespace:
    return SimpleNamespace(
        history_id=block_id, name_separator_label=student, subject="대수", level="Lv.3",
        round_name="1회차", source="catalog", order_id="order-1", material_id="material-2",
        copies=2, bundled=True, selected_numbers=(1, 3), printer_route="세로",
        start=1, end=3, pages_per_sheet=4, printer_override="Mock Printer 세로",
        material_kind="핵심문제", display_label="대수 핵심문제 묶음",
        files=[SimpleNamespace(
            number=1, name="대수 1회", path=Path("존재하지 않는 대수.pdf"),
            printer_override=None, pages_per_sheet=1,
        )],
    )


class FakeApp(PrintHistoryMixin):
    def __init__(self, directory: Path) -> None:
        self.log = Mock()
        self.blocks = []
        with patch("unit_print_history_events.HISTORY_DIRECTORY", directory):
            self.initialize_history()


def options() -> SimpleNamespace:
    return SimpleNamespace(
        printer_name="Mock Printer", dry_run=False, duplex=True, engine="sumatra",
        queue_batch_size=3, queue_refill_threshold=1,
    )


def write_error() -> HistoryWriteError:
    return HistoryWriteError(Path("history/2026-09-05.jsonl"), OSError("disk full"))


class PrintHistoryEventsTest(unittest.TestCase):
    def test_block_snapshot_is_json_ready_and_never_accesses_files(self) -> None:
        source = block("block-1")
        source.replaces_block_id = "previous-block"
        with patch.object(Path, "stat", side_effect=AssertionError("No PDF stat on UI")):
            snapshot = block_history_data(source)
        self.assertEqual("block-1", snapshot["block_id"])
        self.assertEqual("previous-block", snapshot["replaces_block_id"])
        self.assertEqual("김학생", snapshot["student_name"])
        self.assertEqual(1, snapshot["start"])
        self.assertEqual(3, snapshot["end"])
        self.assertEqual(4, snapshot["pages_per_sheet"])
        self.assertEqual("Mock Printer 세로", snapshot["printer_override"])
        self.assertEqual("핵심문제", snapshot["material_kind"])
        self.assertEqual("대수 핵심문제 묶음", snapshot["display_label"])
        self.assertEqual([1, 3], snapshot["selected_numbers"])
        self.assertEqual("존재하지 않는 대수.pdf", snapshot["files"][0]["path"])
        self.assertEqual(snapshot, json.loads(json.dumps(snapshot, ensure_ascii=False)))
        source.selected_numbers = (2,)
        source.start = 2
        source.end = 2
        source.pages_per_sheet = 1
        source.printer_override = None
        source.material_kind = "문제"
        source.display_label = "수정된 묶음"
        source.files[0].name = "변경됨"
        self.assertEqual("대수 1회", snapshot["files"][0]["name"])
        self.assertEqual([1, 3], snapshot["selected_numbers"])
        self.assertEqual((1, 3, 4), (snapshot["start"], snapshot["end"], snapshot["pages_per_sheet"]))
        self.assertEqual("Mock Printer 세로", snapshot["printer_override"])
        self.assertEqual("핵심문제", snapshot["material_kind"])
        self.assertEqual("대수 핵심문제 묶음", snapshot["display_label"])

    def test_job_snapshot_uses_saved_block_and_distinguishes_generated_path(self) -> None:
        source = block("block-1")
        job = SimpleNamespace(
            job_id="job-1", history_block=block_history_data(source), block_summary="대수 묶음",
            block_index=2, block_total=3, copies=1, bundle_no=1, bundle_total=2,
            is_name_separator=False, is_omr=False, is_separator=False, pages_per_sheet=4,
            item=source.files[0],
        )
        source.name_separator_label = "수정된 이름"
        snapshot = job_history_data(job, "A:ble", Path("변환된 2x2.pdf"))
        self.assertEqual("김학생", snapshot["student_name"])
        self.assertEqual("order-1", snapshot["order_id"])
        self.assertEqual("material-2", snapshot["material_id"])
        self.assertEqual("pdf", snapshot["kind"])
        self.assertEqual("존재하지 않는 대수.pdf", snapshot["pdf_path"])
        self.assertEqual("변환된 2x2.pdf", snapshot["printed_pdf_path"])
        self.assertEqual(snapshot["pdf_path"], job_history_data(job, "세로")["printed_pdf_path"])
        for flag, kind in (("is_separator", "separator"), ("is_omr", "omr"), ("is_name_separator", "name_separator")):
            setattr(job, flag, True)
            self.assertEqual(kind, job_history_data(job, "세로")["kind"])

    def test_fingerprint_streams_all_bytes_and_reports_file_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "큰 자료.pdf"
            contents = b"%PDF-1.7\n" + b"0123456789" * 220000
            path.write_bytes(contents)
            result = fingerprint_pdf(path)
            self.assertEqual(hashlib.sha256(contents).hexdigest(), result["sha256"])
            self.assertEqual(len(contents), result["size"])
            self.assertEqual(path.stat().st_mtime_ns, result["mtime_ns"])
            with self.assertRaises(FileNotFoundError):
                fingerprint_pdf(path.with_name("missing.pdf"))

    def test_fingerprint_rejects_a_file_changed_during_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "changing.pdf"
            path.write_bytes(b"data")
            before = SimpleNamespace(st_size=4, st_mtime_ns=100)
            after = SimpleNamespace(st_size=4, st_mtime_ns=101)
            with patch("unit_print_history_events.os.fstat", side_effect=(before, after)):
                with self.assertRaisesRegex(OSError, "PDF가 변경"):
                    fingerprint_pdf(path)

    def test_initialization_and_new_run_record_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            app = FakeApp(directory)
            self.assertEqual("session_started", read_history(directory)[0]["event"])
            self.assertIsNone(app.history_run_id)
            app.begin_history_run(options())
            first_run = app.history_run_id
            uuid.UUID(first_run)
            app.begin_history_run(options(), retry=True)
            records = read_history(directory)
            self.assertEqual("new", records[1]["run_kind"])
            self.assertEqual("retry", records[2]["run_kind"])
            self.assertNotEqual(first_run, app.history_run_id)
            self.assertEqual(app.history_run_id, records[2]["run_id"])
            self.assertEqual("Mock Printer", records[2]["printer"])
            self.assertEqual(3, records[2]["queue_batch_size"])

    def test_explicit_run_id_override_and_uninitialized_app_are_supported(self) -> None:
        bare = FakeApp.__new__(FakeApp)
        self.assertIsNone(bare.record_history("ignored", required=True))
        bare.capture_queue_history()
        bare.begin_history_run(options())
        self.assertFalse(hasattr(bare, "history_run_id"))
        with tempfile.TemporaryDirectory() as temporary:
            app = FakeApp(Path(temporary))
            app.history_run_id = "current"
            result = app.record_history("late_event", run_id="original")
            self.assertEqual("original", result["run_id"])
            self.assertEqual("current", app.history_run_id)

    def test_write_failures_log_once_and_required_failure_restores_previous_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app = FakeApp(Path(temporary))
            app.history_run_id = "old-run"
            with patch.object(app.print_history, "record", side_effect=write_error()):
                self.assertIsNone(app.record_history("first"))
                self.assertIsNone(app.record_history("second"))
                with self.assertRaises(HistoryWriteError):
                    app.begin_history_run(options())
            self.assertEqual("old-run", app.history_run_id)
            app.log.assert_called_once()
            self.assertIn("이력 저장 실패", app.log.call_args.args[0])
            app.record_history("recovered")
            with patch.object(app.print_history, "record", side_effect=write_error()):
                app.record_history("failed_again")
            self.assertEqual(2, app.log.call_count)

    def test_worker_releases_history_lock_before_logging_write_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app = FakeApp(Path(temporary))
            peer_acquired = threading.Event()
            worker_errors = []

            def peer_ui_capture() -> None:
                if app._history_state_lock.acquire(timeout=0.5):
                    try:
                        peer_acquired.set()
                    finally:
                        app._history_state_lock.release()

            def log_while_ui_needs_lock(_message: str) -> None:
                peer = threading.Thread(target=peer_ui_capture)
                peer.start()
                peer.join(timeout=1)

            def worker_record() -> None:
                try:
                    app.record_history("worker_event", required=True)
                except HistoryWriteError as error:
                    worker_errors.append(error)

            app.log = Mock(side_effect=log_while_ui_needs_lock)
            expected_error = write_error()
            with patch.object(app.print_history, "record", side_effect=expected_error):
                worker = threading.Thread(target=worker_record)
                worker.start()
                worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            self.assertTrue(peer_acquired.is_set(), "UI could not acquire the history lock during log()")
            self.assertEqual([expected_error], worker_errors)
            app.log.assert_called_once()

    def test_capture_records_added_updated_removed_and_reordered_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            app = FakeApp(directory)
            app.history_run_id = "prior-run"
            first, second = block("first"), block("second")
            app.blocks = [first, second]
            app.capture_queue_history()
            app.capture_queue_history()
            first.copies = 5
            app.capture_queue_history()
            app.blocks = [second, first]
            app.capture_queue_history()
            app.blocks = [second]
            app.capture_queue_history()
            records = read_history(directory)
            self.assertEqual([
                "session_started", "block_added", "block_added", "block_updated",
                "queue_reordered", "block_removed",
            ], [record["event"] for record in records])
            self.assertEqual(5, records[3]["block"]["copies"])
            self.assertEqual(["second", "first"], records[4]["block_order"])
            self.assertEqual("김학생", records[4]["blocks"][0]["student_name"])
            self.assertEqual(5, records[5]["block"]["copies"])
            self.assertTrue(all(record["run_id"] is None for record in records[1:]))
            self.assertEqual(["second"], list(app._history_queue_snapshot))

    def test_failed_addition_batch_retries_every_unconfirmed_addition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            app = FakeApp(directory)
            app.blocks = [block("first"), block("second")]
            with patch.object(app.print_history, "record_many", side_effect=write_error()):
                app.capture_queue_history()
            self.assertEqual({}, app._history_queue_snapshot)
            app.capture_queue_history()
            self.assertEqual(["first", "second"], list(app._history_queue_snapshot))
            added = [record for record in read_history(directory) if record["event"] == "block_added"]
            self.assertEqual(["first", "second"], [record["block"]["block_id"] for record in added])

    def test_failed_update_removal_and_reorder_each_retry_next_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            app = FakeApp(directory)
            first, second = block("first"), block("second")
            app.blocks = [first, second]
            app.capture_queue_history()
            first.copies = 7
            app.blocks = [second, first]
            with patch.object(app.print_history, "record_many", side_effect=write_error()):
                app.capture_queue_history()
            self.assertEqual(2, app._history_queue_snapshot["first"]["copies"])
            self.assertEqual(["first", "second"], list(app._history_queue_snapshot))
            app.capture_queue_history()
            self.assertEqual(7, app._history_queue_snapshot["first"]["copies"])
            self.assertEqual(["second", "first"], list(app._history_queue_snapshot))
            app.blocks = []
            with patch.object(app.print_history, "record_many", side_effect=write_error()):
                app.capture_queue_history()
            self.assertEqual(2, len(app._history_queue_snapshot))
            app.capture_queue_history()
            self.assertEqual({}, app._history_queue_snapshot)
            removed = [record for record in read_history(directory) if record["event"] == "block_removed"]
            self.assertEqual(2, len(removed))


if __name__ == "__main__":
    unittest.main()
