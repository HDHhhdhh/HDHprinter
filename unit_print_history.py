"""Durable local print-event history and a read-only-by-default history CLI.

``record`` and ``record_many`` return only after flush/fsync. A write error can
still leave some or all bytes on disk. Batches are not atomic transactions, so
callers must not infer that a failed event or print action did not happen.
Thread locks serialize local access. A separate OS lock serializes writers
across app processes; a crashed process releases that lock automatically.
"""

from __future__ import annotations

import argparse
import csv
import errno
import json
import os
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from contextlib import contextmanager
from datetime import date as calendar_date, datetime
from pathlib import Path
from typing import Any, Iterator, Sequence


APP_DIR = Path(__file__).resolve().parent
HISTORY_DIRECTORY = APP_DIR / "output" / "unit_print_manager" / "history"
SCHEMA_VERSION = 1
HISTORY_LOCK_TIMEOUT_SECONDS = 5.0
HISTORY_LOCK_FILENAME = ".history.lock"
METADATA_FIELDS = ("timestamp", "schema_version", "event_id", "session_id", "event")
_IO_LOCK = threading.RLock()
_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


class HistoryWriteError(RuntimeError):
    """An event could not be durably written; its bytes may already be present."""

    def __init__(self, path: Path, cause: Exception) -> None:
        self.path = path
        self.cause = cause
        super().__init__(f"인쇄 이력을 저장하지 못했습니다: {path} ({cause})")


@dataclass(frozen=True, slots=True)
class HistoryReadReport:
    records: tuple[dict[str, Any], ...]
    malformed_lines: int
    files_read: tuple[Path, ...]


def _local_now() -> datetime:
    return datetime.now().astimezone()


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


@contextmanager
def _exclusive_history_lock(directory: Path) -> Iterator[None]:
    """Keep a reusable lock file separate from JSONL; never delete its inode.

    Windows permits locking a byte beyond EOF, so the lock file can stay empty.
    Nonblocking acquisition permits a bounded wait instead of freezing callers.
    The directory must already exist; read-only APIs never invoke this helper.
    """
    with (directory / HISTORY_LOCK_FILENAME).open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt

            def acquire() -> None:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)

            def release() -> None:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def acquire() -> None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            def release() -> None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

        deadline = time.monotonic() + HISTORY_LOCK_TIMEOUT_SECONDS
        while True:
            try:
                acquire()
                break
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"다른 프로세스의 인쇄 이력 저장이 끝나기를 "
                        f"{HISTORY_LOCK_TIMEOUT_SECONDS:g}초 동안 기다렸습니다: {directory}"
                    ) from error
                time.sleep(min(0.025, remaining))
        try:
            yield
        finally:
            release()


class PrintHistory:
    """Append events to daily UTF-8 JSONL files; construction does not write."""

    def __init__(self, directory: Path, session_id: str | None = None) -> None:
        if session_id is not None and (not isinstance(session_id, str) or not session_id.strip()):
            raise ValueError("session_id는 비어 있지 않은 문자열이어야 합니다.")
        self.directory = Path(directory)
        self.session_id = session_id if session_id is not None else str(uuid.uuid4())

    def record(self, event: str, **data: Any) -> dict[str, Any]:
        return self.record_many(((event, data),))[0]

    def record_many(
        self, events: Sequence[tuple[str, dict[str, Any]]],
    ) -> tuple[dict[str, Any], ...]:
        """Persist an ordered batch with one lock/open/flush/fsync.

        All payloads are validated before opening a history file. One batch
        shares its capture timestamp and daily file, even across midnight.
        Failure may leave a complete prefix or even the whole batch on disk;
        retry can therefore duplicate events and must not imply reprinting.
        """
        if not events:
            return ()
        with _IO_LOCK:
            now = _local_now()
            path = self.directory / f"{now.date().isoformat()}.jsonl"
            try:
                encoded_records = []
                snapshots = []
                for event, data in events:
                    if not isinstance(event, str) or not event.strip():
                        raise ValueError("event는 비어 있지 않은 문자열이어야 합니다.")
                    collisions = set(data).intersection(METADATA_FIELDS)
                    if collisions:
                        raise ValueError(f"예약된 이력 필드입니다: {', '.join(sorted(collisions))}")
                    record = {
                        "timestamp": now.isoformat(timespec="milliseconds"),
                        "schema_version": SCHEMA_VERSION,
                        "event_id": str(uuid.uuid4()),
                        "session_id": self.session_id,
                        "event": event,
                        **data,
                    }
                    encoded = (_json_text(record) + "\n").encode("utf-8")
                    encoded_records.append(encoded)
                    # Normalize each snapshot to the exact JSON representation.
                    snapshots.append(json.loads(encoded))
                encoded_batch = b"".join(encoded_records)
                self.directory.mkdir(parents=True, exist_ok=True)
                with _exclusive_history_lock(self.directory):
                    with path.open("a+b") as stream:
                        stream.seek(0, os.SEEK_END)
                        if stream.tell():
                            stream.seek(-1, os.SEEK_END)
                            if stream.read(1) != b"\n":
                                if stream.write(b"\n") != 1:
                                    raise OSError("이력의 마지막 줄을 복구하지 못했습니다.")
                        if stream.write(encoded_batch) != len(encoded_batch):
                            raise OSError("이력 내용의 일부만 기록되었습니다.")
                        stream.flush()
                        os.fsync(stream.fileno())
                return tuple(snapshots)
            except (OSError, TypeError, ValueError, OverflowError, RecursionError) as error:
                raise HistoryWriteError(path, error) from error


def _validate_date(value: str) -> str:
    if not _DATE_PATTERN.fullmatch(value):
        raise ValueError("날짜는 YYYY-MM-DD 형식이어야 합니다.")
    try:
        calendar_date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"올바르지 않은 날짜입니다: {value}") from error
    return value


def _history_files(directory: Path, date: str | None) -> tuple[Path, ...]:
    if date is not None:
        filename = f"{_validate_date(date)}.jsonl"
    if not directory.exists():
        return ()
    if not directory.is_dir():
        raise NotADirectoryError(f"이력 폴더가 아닙니다: {directory}")
    if date is not None:
        path = directory / filename
        return (path,) if path.is_file() else ()
    paths = []
    for path in sorted(directory.glob("*.jsonl")):
        try:
            _validate_date(path.stem)
        except ValueError:
            continue
        if path.is_file():
            paths.append(path)
    return tuple(paths)


def read_history_report(directory: Path, date: str | None = None) -> HistoryReadReport:
    """Read valid JSON objects, skipping blank and malformed lines without edits.

    Invalid UTF-8, invalid JSON, and non-object JSON count as malformed lines.
    File access errors propagate so a failed read is distinguishable from no data.
    """
    records = []
    malformed_lines = 0
    with _IO_LOCK:
        paths = _history_files(Path(directory), date)
        for path in paths:
            with path.open("rb") as stream:
                for raw_line in stream:
                    if not raw_line.strip():
                        continue
                    try:
                        record = json.loads(raw_line.decode("utf-8"))
                        if not isinstance(record, dict):
                            raise ValueError("이력 레코드는 JSON 객체여야 합니다.")
                    except (UnicodeError, ValueError, RecursionError):
                        malformed_lines += 1
                        continue
                    records.append(record)
    return HistoryReadReport(tuple(records), malformed_lines, paths)


def read_history(directory: Path, date: str | None = None) -> list[dict[str, Any]]:
    return list(read_history_report(directory, date).records)


def _export_csv(report: HistoryReadReport, destination: Path, history_directory: Path) -> None:
    resolved_destination = destination.resolve()
    source_files = _history_files(history_directory, None)
    if (
        resolved_destination.parent == history_directory.resolve()
        and resolved_destination.suffix.lower() == ".jsonl"
    ) or any(path.resolve() == resolved_destination for path in source_files):
        raise ValueError("CSV 출력 경로로 원본 이력 파일을 덮어쓸 수 없습니다.")
    extra_fields = sorted({key for record in report.records for key in record} - set(METADATA_FIELDS))
    fields = [*METADATA_FIELDS, *extra_fields]
    with destination.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for record in report.records:
            row = {
                key: (_json_text(value) if isinstance(value, (dict, list)) else value)
                for key, value in record.items()
            }
            writer.writerow(row)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="빠른 인쇄기 영구 이력 조회 및 CSV 추출")
    parser.add_argument("--directory", type=Path, default=HISTORY_DIRECTORY, help="이력 폴더")
    parser.add_argument("--date", help="조회 날짜 YYYY-MM-DD (생략하면 전체)")
    parser.add_argument("--csv", type=Path, metavar="OUTPUT", help="CSV 파일로 저장")
    arguments = parser.parse_args(argv)
    try:
        report = read_history_report(arguments.directory, arguments.date)
        if arguments.csv is not None:
            _export_csv(report, arguments.csv, arguments.directory)
            print(f"{len(report.records)}건 저장: {arguments.csv}")
        else:
            for record in report.records:
                print(_json_text(record))
        if report.malformed_lines:
            print(f"경고: 손상된 이력 {report.malformed_lines}줄을 건너뛰었습니다.", file=sys.stderr)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
