from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from test_unit_print_history import NOW, WORKSPACE, close_processes, wait_until_ready
from test_unit_print_history_events import FakeApp, block, write_error
from unit_print_history import HistoryWriteError, PrintHistory, read_history, read_history_report


class PartialWriteStream:
    """Write one complete line and a torn next line to the real temporary file."""

    def __init__(self, stream, *, raise_error: bool) -> None:
        self.stream = stream
        self.raise_error = raise_error

    def __enter__(self):
        self.stream.__enter__()
        return self

    def __exit__(self, *args):
        return self.stream.__exit__(*args)

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def write(self, data: bytes) -> int:
        length = data.index(b"\n") + 8
        written = self.stream.write(data[:length])
        self.stream.flush()
        if self.raise_error:
            raise OSError("disk full after partial batch write")
        return written


class PrintHistoryBatchTests(unittest.TestCase):
    def test_batch_preserves_order_snapshots_and_syncs_once_before_return(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            history = PrintHistory(directory, session_id="batch-session")
            source = ["원본.pdf"]
            at_sync = []
            real_fsync = os.fsync
            real_open = Path.open
            opens = []

            def track_open(path, *args, **kwargs):
                opens.append(path)
                return real_open(path, *args, **kwargs)

            def sync(descriptor):
                # Read through the original open so the assertion counts writes.
                with real_open(directory / "2026-09-05.jsonl", "rb") as stream:
                    at_sync.extend(json.loads(line) for line in stream)
                real_fsync(descriptor)

            with (
                patch("unit_print_history._local_now", return_value=NOW),
                patch.object(Path, "open", new=track_open),
                patch("unit_print_history.os.fsync", side_effect=sync) as fsync,
            ):
                records = history.record_many([
                    ("block_removed", {"sequence": 0}),
                    ("block_added", {"sequence": 1, "files": source}),
                    ("queue_reordered", {"sequence": 2}),
                ])
            source.append("변경.pdf")
            self.assertEqual(list(records), at_sync)
            self.assertEqual(list(records), read_history(directory))
            self.assertEqual([0, 1, 2], [record["sequence"] for record in records])
            self.assertEqual(["원본.pdf"], records[1]["files"])
            self.assertEqual(3, len({record["event_id"] for record in records}))
            self.assertEqual({"batch-session"}, {record["session_id"] for record in records})
            self.assertEqual(1, len({record["timestamp"] for record in records}))
            self.assertEqual([".history.lock", "2026-09-05.jsonl"], [path.name for path in opens])
            fsync.assert_called_once()

    def test_empty_and_invalid_batches_do_not_create_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "absent"
            history = PrintHistory(directory)
            self.assertEqual((), history.record_many([]))
            for bad in (("", {}), ("bad", {"timestamp": "reserved"}), ("bad", {"path": Path("x")})):
                with self.subTest(bad=bad), self.assertRaises(HistoryWriteError):
                    history.record_many([("valid", {}), bad])
            self.assertFalse(directory.exists())

    def test_partial_write_recovery_retains_prefix_and_retries_whole_batch(self) -> None:
        for raises in (False, True):
            with self.subTest(raises=raises), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                history = PrintHistory(directory)
                real_open = Path.open

                def partial_open(path, *args, **kwargs):
                    stream = real_open(path, *args, **kwargs)
                    if path.suffix == ".jsonl" and args == ("a+b",):
                        return PartialWriteStream(stream, raise_error=raises)
                    return stream

                events = [("block_added", {"sequence": index}) for index in range(3)]
                with (
                    patch("unit_print_history._local_now", return_value=NOW),
                    patch.object(Path, "open", new=partial_open),
                    self.assertRaises(HistoryWriteError),
                ):
                    history.record_many(events)
                before = read_history_report(directory)
                self.assertEqual([0], [record["sequence"] for record in before.records])
                self.assertEqual(1, before.malformed_lines)
                with patch("unit_print_history._local_now", return_value=NOW):
                    history.record_many(events)
                after = read_history_report(directory)
                self.assertEqual([0, 0, 1, 2], [record["sequence"] for record in after.records])
                self.assertEqual(1, after.malformed_lines)

    def test_individual_required_record_still_syncs_before_each_return(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app = FakeApp(Path(temporary))
            real_fsync = os.fsync
            with patch("unit_print_history.os.fsync", wraps=real_fsync) as fsync:
                app.record_history("job_attempt", required=True)
                self.assertEqual(1, fsync.call_count)
                app.record_history("job_submitted", required=True)
                self.assertEqual(2, fsync.call_count)
            with patch("unit_print_history.os.fsync", side_effect=OSError("disk full")):
                with self.assertRaises(HistoryWriteError):
                    app.record_history("next_job_attempt", required=True)

    def test_concurrent_threads_keep_each_batch_contiguous_and_ordered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            histories = [PrintHistory(directory) for _ in range(4)]
            with ThreadPoolExecutor(max_workers=4) as executor:
                list(executor.map(
                    lambda batch: histories[batch % 4].record_many([
                        ("queue_change", {"batch": batch, "sequence": sequence})
                        for sequence in range(8)
                    ]),
                    range(12),
                ))
            records = read_history(directory)
            self.assertEqual(96, len(records))
            for offset in range(0, len(records), 8):
                group = records[offset:offset + 8]
                self.assertEqual(1, len({record["batch"] for record in group}))
                self.assertEqual(list(range(8)), [record["sequence"] for record in group])
            self.assertEqual(set(range(12)), {record["batch"] for record in records})

    def test_concurrent_processes_keep_each_batch_contiguous(self) -> None:
        script = """
import sys, time
from pathlib import Path
from unit_print_history import PrintHistory
directory, ready, gate = map(Path, sys.argv[1:4])
worker = int(sys.argv[4])
history = PrintHistory(directory)
ready.write_text('ready', encoding='utf-8')
deadline = time.monotonic() + 10
while not gate.exists():
    if time.monotonic() >= deadline:
        raise TimeoutError('Start gate did not open')
    time.sleep(0.005)
for batch in range(4):
    history.record_many([
        ('queue_change', {'worker': worker, 'batch': batch, 'sequence': sequence})
        for sequence in range(6)
    ])
"""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "history"
            gate = Path(temporary) / "go"
            ready = [Path(temporary) / f"ready-{worker}" for worker in range(3)]
            processes = []
            try:
                for worker, path in enumerate(ready):
                    processes.append(subprocess.Popen(
                        [sys.executable, "-c", script, str(directory), str(path), str(gate), str(worker)],
                        cwd=WORKSPACE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        text=True, encoding="utf-8", errors="replace",
                    ))
                wait_until_ready(ready, processes)
                gate.write_text("go", encoding="utf-8")
                for process in processes:
                    stdout, stderr = process.communicate(timeout=15)
                    self.assertEqual(0, process.returncode, stdout + stderr)
            finally:
                close_processes(processes)
            report = read_history_report(directory)
            self.assertEqual(72, len(report.records))
            self.assertEqual(0, report.malformed_lines)
            self.assertEqual(72, len({record["event_id"] for record in report.records}))
            for offset in range(0, len(report.records), 6):
                group = report.records[offset:offset + 6]
                self.assertEqual(1, len({(r["worker"], r["batch"]) for r in group}))
                self.assertEqual(list(range(6)), [r["sequence"] for r in group])


class QueueHistoryBatchTests(unittest.TestCase):
    def test_bulk_add_syncs_once_and_no_change_syncs_zero_times(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app = FakeApp(Path(temporary))
            app.blocks = [block(str(index)) for index in range(50)]
            with patch("unit_print_history.os.fsync", wraps=os.fsync) as fsync:
                app.capture_queue_history()
                fsync.assert_called_once()
                app.capture_queue_history()
                fsync.assert_called_once()
            records = read_history(Path(temporary))[1:]
            self.assertEqual(list(range(50)), [r["queue_index"] for r in records])
            self.assertTrue(all(r["run_id"] is None for r in records))
            self.assertTrue(all((r["block"]["start"], r["block"]["end"]) == (1, 3) for r in records))
            self.assertTrue(all(r["block"]["pages_per_sheet"] == 4 for r in records))
            self.assertTrue(all(r["block"]["printer_override"] == "Mock Printer 세로" for r in records))
            self.assertTrue(all(r["block"]["material_kind"] == "핵심문제" for r in records))
            self.assertTrue(all(r["block"]["display_label"] == "대수 핵심문제 묶음" for r in records))
            self.assertEqual(50, len(app._history_queue_snapshot))

    def test_single_change_still_uses_record_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app = FakeApp(Path(temporary))
            app.blocks = [block("one")]
            with patch.object(app, "record_history", wraps=app.record_history) as record:
                app.capture_queue_history()
            self.assertEqual("block_added", record.call_args.args[0])
            record.assert_called_once()

    def test_partial_batch_write_does_not_advance_any_queue_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            app = FakeApp(directory)
            app.blocks = [block("first"), block("second")]
            real_open = Path.open

            def partial_open(path, *args, **kwargs):
                stream = real_open(path, *args, **kwargs)
                if path.suffix == ".jsonl" and args == ("a+b",):
                    return PartialWriteStream(stream, raise_error=True)
                return stream

            with patch.object(Path, "open", new=partial_open):
                app.capture_queue_history()
            self.assertEqual({}, app._history_queue_snapshot)
            self.assertEqual(["first"], [r["block"]["block_id"] for r in read_history(directory)[1:]])
            app.log.assert_called_once()
            app.capture_queue_history()
            self.assertEqual(["first", "second"], list(app._history_queue_snapshot))
            report = read_history_report(directory)
            self.assertEqual(["first", "first", "second"], [r["block"]["block_id"] for r in report.records[1:]])
            self.assertEqual(1, report.malformed_lines)

    def test_fsync_failure_keeps_old_snapshot_and_retry_may_duplicate_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            app = FakeApp(directory)
            first, second = block("first"), block("second")
            app.blocks = [first, second]
            app.capture_queue_history()
            before = dict(app._history_queue_snapshot)
            first.copies = 7
            app.blocks = [second, first]
            with patch("unit_print_history.os.fsync", side_effect=OSError("disk full")):
                app.capture_queue_history()
                self.assertEqual(before, app._history_queue_snapshot)
                self.assertEqual(["first", "second"], list(app._history_queue_snapshot))
                app.capture_queue_history()
            app.log.assert_called_once()
            app.capture_queue_history()
            self.assertEqual(7, app._history_queue_snapshot["first"]["copies"])
            self.assertEqual(["second", "first"], list(app._history_queue_snapshot))
            self.assertIsNone(app._history_last_error)
            changes = read_history(directory)[3:]
            self.assertEqual(["block_updated", "queue_reordered"] * 3, [r["event"] for r in changes])

    def test_mixed_removal_update_addition_keep_event_order_and_capture_positions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            app = FakeApp(directory)
            first, second, third = [block(name) for name in ("first", "second", "third")]
            app.blocks = [first, second, third]
            app.capture_queue_history()
            third.copies = 4
            app.blocks = [third, block("fourth")]
            with patch.object(app.print_history, "record_many", side_effect=write_error()):
                app.capture_queue_history()
            self.assertEqual(["first", "second", "third"], list(app._history_queue_snapshot))
            app.capture_queue_history()
            changes = read_history(directory)[4:]
            self.assertEqual(["block_removed", "block_removed", "block_updated", "block_added"],
                             [r["event"] for r in changes])
            self.assertEqual(["first", "second", "third", "fourth"], [r["block"]["block_id"] for r in changes])
            self.assertEqual([0, 1, 0, 1], [r["queue_index"] for r in changes])
            self.assertEqual(["third", "fourth"], list(app._history_queue_snapshot))


if __name__ == "__main__":
    unittest.main()
