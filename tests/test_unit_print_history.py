from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from unit_print_history import (
    HistoryWriteError,
    HISTORY_LOCK_FILENAME,
    PrintHistory,
    main,
    read_history,
    read_history_report,
)


NOW = datetime(2026, 9, 5, 15, 0, tzinfo=timezone(timedelta(hours=9)))
WORKSPACE = Path(__file__).resolve().parents[1]


def wait_until_ready(paths: list[Path], processes: list[subprocess.Popen[str]]) -> None:
    deadline = time.monotonic() + 10
    while not all(path.is_file() for path in paths):
        if any(process.poll() is not None for process in processes):
            raise AssertionError("History subprocess exited before becoming ready.")
        if time.monotonic() >= deadline:
            raise AssertionError("History subprocess readiness timed out.")
        time.sleep(0.01)


def close_processes(processes: list[subprocess.Popen[str]]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=5)


class PrintHistoryTest(unittest.TestCase):
    def test_unicode_metadata_persists_across_new_instances(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "history"
            first = PrintHistory(directory)
            self.assertFalse(directory.exists())
            with patch("unit_print_history._local_now", return_value=NOW):
                record = first.record("전송 확인", student="김학생", filename="대수 1회.pdf")
                second = PrintHistory(directory)
                next_record = second.record("실행 시작", run_id="두 번째")
            self.assertEqual([record, next_record], read_history(directory))
            self.assertEqual(1, record["schema_version"])
            self.assertEqual("2026-09-05T15:00:00.000+09:00", record["timestamp"])
            uuid.UUID(record["event_id"])
            uuid.UUID(record["session_id"])
            self.assertNotEqual(record["session_id"], next_record["session_id"])
            raw = (directory / "2026-09-05.jsonl").read_text(encoding="utf-8")
            self.assertIn("김학생", raw)
            self.assertNotIn("\\u", raw)

    def test_supplied_session_is_fixed_and_returned_data_is_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            history = PrintHistory(Path(temporary), session_id="known-session")
            items = ["원본.pdf"]
            with patch("unit_print_history._local_now", return_value=NOW):
                first = history.record("first", items=items)
                second = history.record("second")
            items.append("추가.pdf")
            self.assertEqual(["원본.pdf"], first["items"])
            self.assertEqual("known-session", first["session_id"])
            self.assertEqual(first["session_id"], second["session_id"])

    def test_concurrent_writers_across_instances_keep_every_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            histories = [PrintHistory(directory) for _ in range(4)]
            with patch("unit_print_history._local_now", return_value=NOW):
                with ThreadPoolExecutor(max_workers=8) as executor:
                    records = list(executor.map(
                        lambda index: histories[index % 4].record("전송", sequence=index),
                        range(80),
                    ))
            report = read_history_report(directory)
            self.assertEqual(80, len(report.records))
            self.assertEqual(0, report.malformed_lines)
            self.assertEqual(set(range(80)), {record["sequence"] for record in report.records})
            self.assertEqual(80, len({record["event_id"] for record in records}))

    def test_three_processes_preserve_every_unicode_event_and_repair_tail_once(self) -> None:
        script = """
import sys, time
from pathlib import Path
from unit_print_history import PrintHistory
directory, ready, gate = map(Path, sys.argv[1:4])
worker = int(sys.argv[4])
history = PrintHistory(directory, session_id=f'process-{worker}')
ready.write_text('ready', encoding='utf-8')
deadline = time.monotonic() + 10
while not gate.exists():
    if time.monotonic() >= deadline:
        raise TimeoutError('Start gate did not open')
    time.sleep(0.005)
for sequence in range(30):
    history.record('동시 인쇄 기록', worker=worker, sequence=sequence, student='김학생')
"""
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            directory = base / "history"
            directory.mkdir()
            today = datetime.now().astimezone().date().isoformat()
            path = directory / f"{today}.jsonl"
            path.write_bytes(b'{"event":"interrupted')
            gate = base / "go"
            ready_paths = [base / f"ready-{worker}" for worker in range(3)]
            processes = []
            try:
                for worker, ready in enumerate(ready_paths):
                    processes.append(subprocess.Popen(
                        [sys.executable, "-c", script, str(directory), str(ready), str(gate), str(worker)],
                        cwd=WORKSPACE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        text=True, encoding="utf-8", errors="replace",
                    ))
                wait_until_ready(ready_paths, processes)
                gate.write_text("go", encoding="utf-8")
                for process in processes:
                    stdout, stderr = process.communicate(timeout=20)
                    self.assertEqual(0, process.returncode, stdout + stderr)
            finally:
                close_processes(processes)
            report = read_history_report(directory)
            self.assertEqual(90, len(report.records))
            self.assertEqual(90, len({record["event_id"] for record in report.records}))
            self.assertEqual(1, report.malformed_lines)
            self.assertEqual(
                {(worker, sequence) for worker in range(3) for sequence in range(30)},
                {(record["worker"], record["sequence"]) for record in report.records},
            )
            self.assertTrue(all(record["student"] == "김학생" for record in report.records))
            self.assertEqual({"process-0", "process-1", "process-2"}, {record["session_id"] for record in report.records})
            self.assertEqual(b"", (directory / HISTORY_LOCK_FILENAME).read_bytes())

    def test_busy_process_lock_times_out_and_is_reusable_after_process_exit(self) -> None:
        script = """
import sys, time
from pathlib import Path
from unit_print_history import _exclusive_history_lock
directory, ready = map(Path, sys.argv[1:3])
with _exclusive_history_lock(directory):
    ready.write_text('ready', encoding='utf-8')
    time.sleep(30)
"""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            ready = directory / "ready"
            lock_path = directory / HISTORY_LOCK_FILENAME
            # Existing lock metadata remains outside the daily JSONL forever.
            lock_path.write_bytes(b"stale lock file")
            process = subprocess.Popen(
                [sys.executable, "-c", script, str(directory), str(ready)],
                cwd=WORKSPACE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
            )
            try:
                wait_until_ready([ready], [process])
                with patch("unit_print_history.HISTORY_LOCK_TIMEOUT_SECONDS", 0.1):
                    with self.assertRaises(HistoryWriteError) as raised:
                        PrintHistory(directory).record("blocked")
                self.assertIsInstance(raised.exception.cause, TimeoutError)
                self.assertEqual([], list(directory.glob("*.jsonl")))
            finally:
                close_processes([process])
            # Terminating the owner releases its OS lock; the file is not deleted.
            record = PrintHistory(directory).record("소유 프로세스 종료 후 복구")
            self.assertEqual([record], read_history(directory))
            self.assertEqual(b"stale lock file", lock_path.read_bytes())

    def test_partial_tail_does_not_absorb_new_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = directory / "2026-09-05.jsonl"
            prefix = b'{"event":"old"}\n{"event":"interrupted'
            path.write_bytes(prefix)
            with patch("unit_print_history._local_now", return_value=NOW):
                new = PrintHistory(directory).record("새 작업")
            report = read_history_report(directory)
            self.assertTrue(path.read_bytes().startswith(prefix + b"\n"))
            self.assertEqual(({"event": "old"}, new), report.records)
            self.assertEqual(1, report.malformed_lines)

    def test_valid_tail_without_newline_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "2026-09-05.jsonl").write_bytes(b'{"event":"old"}')
            with patch("unit_print_history._local_now", return_value=NOW):
                new = PrintHistory(directory).record("new")
            self.assertEqual([{"event": "old"}, new], read_history(directory))

    def test_fsync_happens_before_record_returns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            durable_events = []
            real_fsync = os.fsync

            def inspect_and_sync(descriptor: int) -> None:
                raw = (directory / "2026-09-05.jsonl").read_bytes()
                durable_events.append(json.loads(raw)["event"])
                real_fsync(descriptor)

            with (
                patch("unit_print_history._local_now", return_value=NOW),
                patch("unit_print_history.os.fsync", side_effect=inspect_and_sync) as sync,
                redirect_stdout(io.StringIO()) as stdout,
                redirect_stderr(io.StringIO()) as stderr,
            ):
                record = PrintHistory(directory).record("durable")
            self.assertEqual("durable", record["event"])
            self.assertEqual(["durable"], durable_events)
            sync.assert_called_once()
            self.assertEqual("", stdout.getvalue() + stderr.getvalue())

    def test_fsync_failure_is_explicit_and_does_not_print_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with (
                patch("unit_print_history._local_now", return_value=NOW),
                patch("unit_print_history.os.fsync", side_effect=OSError("disk full")),
                redirect_stdout(io.StringIO()) as stdout,
                redirect_stderr(io.StringIO()) as stderr,
            ):
                with self.assertRaises(HistoryWriteError) as raised:
                    PrintHistory(directory).record("failed sync")
            self.assertEqual(directory / "2026-09-05.jsonl", raised.exception.path)
            self.assertIsInstance(raised.exception.__cause__, OSError)
            self.assertEqual("", stdout.getvalue() + stderr.getvalue())

    def test_directory_write_failure_is_wrapped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "history"
            directory.write_text("occupied", encoding="utf-8")
            with self.assertRaises(HistoryWriteError):
                PrintHistory(directory).record("event")

    def test_invalid_payload_does_not_create_history_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "history"
            history = PrintHistory(directory)
            for payload in ({"unsupported": Path("file.pdf")}, {"number": float("nan")}, {"timestamp": "bad"}):
                with self.subTest(payload=payload):
                    with self.assertRaises(HistoryWriteError):
                        history.record("event", **payload)
            with self.assertRaises(HistoryWriteError):
                history.record("")
            self.assertFalse(directory.exists())

    def test_reader_skips_bad_json_utf8_and_nonobjects_without_modification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = directory / "2026-09-05.jsonl"
            raw = b'{"event":"valid"}\n\nnot-json\n\xff\n[]\n'
            path.write_bytes(raw)
            report = read_history_report(directory)
            self.assertEqual(({"event": "valid"},), report.records)
            self.assertEqual(3, report.malformed_lines)
            self.assertEqual(raw, path.read_bytes())

    def test_date_filter_and_local_midnight_file_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            history = PrintHistory(directory)
            with patch("unit_print_history._local_now", side_effect=(NOW, NOW + timedelta(days=1))):
                first = history.record("first day")
                second = history.record("second day")
            self.assertEqual([first], read_history(directory, "2026-09-05"))
            self.assertEqual([second], read_history(directory, "2026-09-06"))
            self.assertEqual([first, second], read_history(directory))
            for invalid in ("20260905", "2026-02-30", "../2026-09-05"):
                with self.assertRaises(ValueError):
                    read_history(directory, invalid)

    def test_cli_default_does_not_create_files_or_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "missing"
            with redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(0, main(["--directory", str(directory), "--date", "2026-09-05"]))
            self.assertEqual("", stdout.getvalue())
            self.assertFalse(directory.exists())

    def test_cli_reading_existing_history_does_not_create_lock_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = directory / "2026-09-05.jsonl"
            path.write_text('{"event":"existing"}\n', encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(0, main(["--directory", str(directory)]))
            self.assertEqual([path], list(directory.iterdir()))

    def test_cli_prints_records_and_exports_csv_without_changing_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "history"
            with patch("unit_print_history._local_now", return_value=NOW):
                record = PrintHistory(directory).record("전송", student="김학생", files=["대수.pdf"])
            path = directory / "2026-09-05.jsonl"
            raw = path.read_bytes()
            before = path.stat().st_mtime_ns
            with redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(0, main(["--directory", str(directory), "--date", "2026-09-05"]))
            self.assertEqual(record, json.loads(stdout.getvalue()))
            export = Path(temporary) / "export.csv"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(0, main(["--directory", str(directory), "--csv", str(export)]))
            with export.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual("김학생", rows[0]["student"])
            self.assertEqual(["대수.pdf"], json.loads(rows[0]["files"]))
            self.assertEqual(raw, path.read_bytes())
            self.assertEqual(before, path.stat().st_mtime_ns)

    def test_cli_warns_about_corrupt_lines_and_rejects_overwriting_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = directory / "2026-09-05.jsonl"
            raw = b'{"event":"valid"}\nbroken\n'
            path.write_bytes(raw)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as stderr:
                main(["--directory", str(directory)])
            self.assertIn("1줄", stderr.getvalue())
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    main(["--directory", str(directory), "--csv", str(path)])
            self.assertEqual(2, raised.exception.code)
            self.assertEqual(raw, path.read_bytes())

    def test_filtered_csv_export_cannot_overwrite_another_day(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            other_day = directory / "2026-09-06.jsonl"
            raw = b'{"event":"another day"}\n'
            other_day.write_bytes(raw)
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    main([
                        "--directory", str(directory), "--date", "2026-09-05",
                        "--csv", str(other_day),
                    ])
            self.assertEqual(2, raised.exception.code)
            self.assertEqual(raw, other_day.read_bytes())


if __name__ == "__main__":
    unittest.main()
