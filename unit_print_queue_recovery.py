"""Local, atomic queue snapshots; restoring and print-safety checks belong to the app.

Snapshots contain student information and must stay outside release bundles.
Saving an empty queue deliberately leaves the previous recovery candidates alone.
This module never opens a print PDF, restores a block, or starts printing.
"""
from __future__ import annotations

import errno
import json
import os
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator


RECOVERY_DIRECTORY = Path(__file__).resolve().parent / "output" / "unit_print_manager" / "recovery"
SCHEMA_VERSION = 1
MAX_QUEUE_BLOCKS = 10_000
MAX_TOTAL_FILES = 100_000
MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
RISK_VALUES = frozenset({"unsent", "submitted", "uncertain", "completed"})
_TOP_FIELDS = {"schema_version", "snapshot_id", "created_at", "session_id", "session_started_at", "reason", "queue", "run"}
_BLOCK_FIELDS = {
    "subject", "level", "round_name", "start", "end", "copies", "files", "bundled",
    "source", "printer_override", "display_label", "pages_per_sheet", "printer_route",
    "selected_numbers", "name_separator_label", "material_kind", "history_id", "order_id",
    "material_id", "replaces_block_id",
}
_FILE_FIELDS = {"number", "name", "path", "printer_override", "pages_per_sheet"}


def snapshot_print_block(block: Any) -> dict[str, Any]:
    """Capture the exact PrintBlock DTO without deepcopying immutable PDF paths.

    Keep field order and values equal to ``asdict(block)`` with paths converted
    to strings and selected numbers converted to a list. No PDF is accessed.
    """
    return {
        "subject": block.subject,
        "level": block.level,
        "round_name": block.round_name,
        "start": block.start,
        "end": block.end,
        "copies": block.copies,
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
        "bundled": block.bundled,
        "source": block.source,
        "printer_override": block.printer_override,
        "display_label": block.display_label,
        "pages_per_sheet": block.pages_per_sheet,
        "printer_route": block.printer_route,
        "selected_numbers": list(block.selected_numbers) if block.selected_numbers is not None else None,
        "name_separator_label": block.name_separator_label,
        "material_kind": block.material_kind,
        "history_id": block.history_id,
        "order_id": block.order_id,
        "material_id": block.material_id,
        "replaces_block_id": block.replaces_block_id,
    }


def _detach_captured_snapshot(value: Any) -> Any:
    """Copy JSON-shaped containers, leaving all schema checks to the writer.

    Captured strings/numbers are immutable. Avoid the caller-side JSON encode,
    decode and full schema walk while still owning every mutable container.
    Unsupported Python objects are rejected rather than borrowed by reference.
    """
    kind = type(value)
    if kind is dict:
        if any(type(key) is not str for key in value):
            raise RecoveryValidationError("자동 큐 스냅샷의 Python 필드 이름이 문자열이 아닙니다.")
        return {key: _detach_captured_snapshot(item) for key, item in value.items()}
    if kind is list:
        return [_detach_captured_snapshot(item) for item in value]
    if kind is tuple:
        return tuple(_detach_captured_snapshot(item) for item in value)
    if value is None or kind in (str, int, float, bool):
        return value
    raise RecoveryValidationError("자동 큐 스냅샷에 지원하지 않는 Python 값이 있습니다.")


class RecoveryValidationError(ValueError):
    """The whole snapshot is rejected; callers must not salvage individual rows."""


class RecoveryReadError(RuntimeError):
    pass


class RecoveryWriteError(RuntimeError):
    pass


class RecoverySessionActiveError(RecoveryReadError):
    """The source session still owns its queue and must not be recovered."""


def _acquire_lease(stream: Any) -> None:
    if os.name == "nt":
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_lease(stream: Any) -> None:
    if os.name == "nt":
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _object(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise RecoveryValidationError(f"{label}: 필수 필드가 누락되었거나 알 수 없는 필드가 있습니다.")
    return value


def _string(value: Any, label: str, *, nonempty: bool = False, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if type(value) is not str or len(value) > 32_768 or "\x00" in value:
        raise RecoveryValidationError(f"{label}: 올바른 문자열이 아닙니다.")
    try:
        # Strict UTF-8 rejects the same surrogate range in native code. Avoid
        # a Python call per character across every field of a large queue.
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise RecoveryValidationError(f"{label}: 올바르지 않은 유니코드 문자가 있습니다.") from error
    if nonempty and not value.strip():
        raise RecoveryValidationError(f"{label}: 빈 문자열은 사용할 수 없습니다.")


def _positive(value: Any, label: str) -> None:
    if type(value) is not int or value < 1:
        raise RecoveryValidationError(f"{label}: 양의 정수가 필요합니다.")


def _session_id(value: Any) -> None:
    _string(value, "session_id", nonempty=True)
    try:
        canonical = str(uuid.UUID(value))
    except ValueError as error:
        raise RecoveryValidationError("session_id: 올바른 UUID가 필요합니다.") from error
    if canonical != value:
        raise RecoveryValidationError("session_id: 정규 UUID 표기가 필요합니다.")


def _timestamp(value: Any, label: str) -> datetime:
    _string(value, label, nonempty=True)
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as error:
        raise RecoveryValidationError(f"{label}: 올바른 ISO 날짜가 필요합니다.") from error
    if timestamp.utcoffset() is None:
        raise RecoveryValidationError(f"{label}: 시간대가 포함되어야 합니다.")
    return timestamp


def validate_snapshot(payload: Any) -> None:
    """Validate the exact versioned DTO without normalizing names, paths, or routes.

    ``risk`` is captured evidence, not authorization to print. The app must also
    reconcile the durable dispatch history before exposing restored waiting work.
    """
    data = _object(payload, _TOP_FIELDS, "스냅샷")
    if type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA_VERSION:
        raise RecoveryValidationError("지원하지 않는 큐 스냅샷 버전입니다.")
    for key in ("snapshot_id", "reason"):
        _string(data[key], key, nonempty=True)
    _session_id(data["session_id"])
    captured = _timestamp(data["created_at"], "created_at")
    started = _timestamp(data["session_started_at"], "session_started_at")
    if started > captured:
        raise RecoveryValidationError("세션 시작 시간이 스냅샷 생성 시간보다 늦습니다.")
    run = _object(data["run"], {"is_printing", "run_id"}, "run")
    if type(run["is_printing"]) is not bool:
        raise RecoveryValidationError("run.is_printing: 불리언이 필요합니다.")
    _string(run["run_id"], "run.run_id", nonempty=True, nullable=True)
    entries = data["queue"]
    if type(entries) is not list or len(entries) > MAX_QUEUE_BLOCKS:
        raise RecoveryValidationError("queue: 허용된 크기의 목록이 필요합니다.")
    block_ids: set[str] = set()
    total_files = 0
    for index, entry in enumerate(entries):
        label = f"queue[{index}]"
        _object(entry, {"block", "state", "detail", "risk"}, label)
        _string(entry["state"], f"{label}.state", nonempty=True)
        _string(entry["detail"], f"{label}.detail")
        if type(entry["risk"]) is not str or entry["risk"] not in RISK_VALUES:
            raise RecoveryValidationError(f"{label}.risk: 알 수 없는 전송 위험 표시입니다.")
        block = _object(entry["block"], _BLOCK_FIELDS, f"{label}.block")
        for key in (
            "subject", "level", "round_name", "display_label", "name_separator_label",
            "material_kind", "order_id", "material_id", "replaces_block_id",
        ):
            _string(block[key], f"{label}.block.{key}")
        for key in ("source", "history_id", "printer_route"):
            _string(block[key], f"{label}.block.{key}", nonempty=True)
        if block["history_id"] in block_ids:
            raise RecoveryValidationError(f"{label}: 블록 이력 ID가 중복되었습니다.")
        block_ids.add(block["history_id"])
        for key in ("start", "end", "copies"):
            _positive(block[key], f"{label}.block.{key}")
        if block["end"] < block["start"]:
            raise RecoveryValidationError(f"{label}: 끝 범위가 시작 범위보다 작습니다.")
        if type(block["bundled"]) is not bool:
            raise RecoveryValidationError(f"{label}.block.bundled: 불리언이 필요합니다.")
        if type(block["pages_per_sheet"]) is not int or block["pages_per_sheet"] not in {1, 4}:
            raise RecoveryValidationError(f"{label}: 지원하지 않는 페이지 배치입니다.")
        _string(block["printer_override"], f"{label}.block.printer_override", nonempty=True, nullable=True)
        numbers = block["selected_numbers"]
        if numbers is not None:
            if type(numbers) is not list or not numbers:
                raise RecoveryValidationError(f"{label}: 개별 번호는 비어 있지 않은 목록이어야 합니다.")
            for number in numbers:
                _positive(number, f"{label}.block.selected_numbers")
            if len(set(numbers)) != len(numbers):
                raise RecoveryValidationError(f"{label}: 개별 번호가 중복되었습니다.")
        files = block["files"]
        if type(files) is not list or not files:
            raise RecoveryValidationError(f"{label}: 출력 파일 목록이 비어 있습니다.")
        total_files += len(files)
        if total_files > MAX_TOTAL_FILES:
            raise RecoveryValidationError("출력 파일 목록이 허용된 크기를 초과했습니다.")
        for file_index, item in enumerate(files):
            item_label = f"{label}.files[{file_index}]"
            _object(item, _FILE_FIELDS, item_label)
            _positive(item["number"], f"{item_label}.number")
            _string(item["name"], f"{item_label}.name")
            _string(item["path"], f"{item_label}.path", nonempty=True)
            _string(item["printer_override"], f"{item_label}.printer_override", nonempty=True, nullable=True)
            layout = item["pages_per_sheet"]
            if layout is not None and (type(layout) is not int or layout not in {1, 4}):
                raise RecoveryValidationError(f"{item_label}: 지원하지 않는 파일별 배치입니다.")


def _encoded_snapshot(payload: Any) -> bytes:
    validate_snapshot(payload)
    try:
        content = (json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise RecoveryValidationError(f"큐 스냅샷을 JSON으로 저장할 수 없습니다: {error}") from error
    if len(content) > MAX_SNAPSHOT_BYTES:
        raise RecoveryValidationError("큐 스냅샷이 허용된 파일 크기를 초과했습니다.")
    return content


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise RecoveryValidationError(f"JSON 필드가 중복되었습니다: {key}")
        value[key] = item
    return value


def _decode_snapshot(content: bytes) -> dict[str, Any]:
    try:
        def invalid_constant(value: str) -> None:
            raise RecoveryValidationError(f"JSON 숫자가 올바르지 않습니다: {value}")

        payload = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=invalid_constant)
        validate_snapshot(payload)
    except (UnicodeError, ValueError, TypeError, RecursionError) as error:
        if isinstance(error, RecoveryValidationError):
            raise
        raise RecoveryValidationError(f"큐 스냅샷을 읽을 수 없습니다: {error}") from error
    return payload


class RecoveryStore:
    """Keep one latest nonempty snapshot per session, with no automatic expiry.

    A new empty launch or several edits in a new session cannot overwrite a
    prior crashed session. Candidate listing never hides a corrupt snapshot.
    """

    def __init__(self, directory: Path = RECOVERY_DIRECTORY) -> None:
        self.directory = Path(directory)
        self._lock = threading.RLock()

    def _atomic_write(self, path: Path, content: bytes) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", prefix=".snapshot-", suffix=".tmp", dir=self.directory, delete=False) as stream:
                temporary_path = Path(stream.name)
                if stream.write(content) != len(content):
                    raise OSError("큐 스냅샷 일부만 기록되었습니다.")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def save(self, payload: dict[str, Any]) -> bool:
        """Return False for an empty queue, preserving pre-crash candidates."""
        content = _encoded_snapshot(payload)
        return self._save_encoded_snapshot(payload, content)

    def _save_encoded_snapshot(self, payload: dict[str, Any], content: bytes) -> bool:
        """Publish bytes already validated and encoded by this module."""
        if not payload["queue"]:
            return False
        path = self.directory / f"session-{payload['session_id']}.json"
        try:
            with self._lock:
                self._atomic_write(path, content)
        except OSError as error:
            raise RecoveryWriteError(f"작업 큐 복구본을 저장하지 못했습니다: {path} ({error})") from error
        return True

    def load(self, path: Path | None = None) -> dict[str, Any] | None:
        """Read exactly the chosen file; no silent fallback to another candidate."""
        with self._lock:
            candidates = self.list_candidates() if path is None else ()
            if path is None and not candidates:
                return None
            target = candidates[0] if path is None else Path(path)
            try:
                if not target.exists():
                    return None
                with target.open("rb") as stream:
                    content = stream.read(MAX_SNAPSHOT_BYTES + 1)
                if len(content) > MAX_SNAPSHOT_BYTES:
                    raise RecoveryValidationError("큐 스냅샷이 허용된 파일 크기를 초과했습니다.")
                snapshot = _decode_snapshot(content)
                if target.name.startswith("session-") and target.name != f"session-{snapshot['session_id']}.json":
                    raise RecoveryValidationError("파일명과 저장된 세션 ID가 일치하지 않습니다.")
                return snapshot
            except (OSError, RecoveryValidationError) as error:
                raise RecoveryReadError(f"작업 큐 복구본 전체를 읽지 못했습니다: {target} ({error})") from error

    def list_candidates(self) -> tuple[Path, ...]:
        with self._lock:
            candidates = (path for path in self.directory.glob("session-*.json")
                          if path.is_file() and not path.name.endswith(".consumed.json"))
            return tuple(sorted(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True))

    def _receipt_path(self, session_id: str) -> Path:
        _session_id(session_id)
        return self.directory / f"session-{session_id}.consumed.json"

    def _receipt_content(self, session_id: str, block_ids: Iterable[str]) -> bytes:
        ids = sorted(set(block_ids))
        for block_id in ids:
            _string(block_id, "consumed.block_ids", nonempty=True)
        if len(ids) > MAX_TOTAL_FILES:
            raise RecoveryValidationError("복구 기록의 블록 수가 허용된 크기를 초과했습니다.")
        content = (json.dumps({"schema_version": 1, "session_id": session_id, "block_ids": ids},
                              ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if len(content) > MAX_SNAPSHOT_BYTES:
            raise RecoveryValidationError("복구 기록이 허용된 파일 크기를 초과했습니다.")
        return content

    def consumed_block_ids(self, session_id: str) -> frozenset[str]:
        """Read mandatory prior-recovery evidence; missing/corrupt is fail-closed.

        The caller must hold the source restore guard through review and commit.
        This separate receipt survives sparse/deleted subsequent daily journals.
        """
        path = self._receipt_path(session_id)
        try:
            with self._lock, path.open("rb") as stream:
                content = stream.read(MAX_SNAPSHOT_BYTES + 1)
            if len(content) > MAX_SNAPSHOT_BYTES:
                raise RecoveryValidationError("복구 기록이 허용된 파일 크기를 초과했습니다.")
            data = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object)
            _object(data, {"schema_version", "session_id", "block_ids"}, "복구 기록")
            if type(data["schema_version"]) is not int or data["schema_version"] != 1 or data["session_id"] != session_id:
                raise RecoveryValidationError("복구 기록의 버전 또는 세션 ID가 일치하지 않습니다.")
            ids = data["block_ids"]
            if type(ids) is not list or len(ids) > MAX_TOTAL_FILES:
                raise RecoveryValidationError("복구 기록의 블록 목록이 올바르지 않습니다.")
            for block_id in ids:
                _string(block_id, "consumed.block_ids", nonempty=True)
            if len(ids) != len(set(ids)):
                raise RecoveryValidationError("복구 기록의 블록 식별자가 중복되었습니다.")
            return frozenset(ids)
        except (OSError, UnicodeError, ValueError, TypeError, RecursionError) as error:
            raise RecoveryReadError(f"이전 복구 여부를 확인하지 못했습니다: {path} ({error})") from error

    def _initialize_consumption_receipt(self, session_id: str) -> None:
        """Publish empty evidence once, before the session's first snapshot."""
        path = self._receipt_path(session_id)
        with self._lock:
            if path.exists():
                self.consumed_block_ids(session_id)
                return
            if (self.directory / f"session-{session_id}.json").exists():
                raise RecoveryReadError("이전 스냅샷의 복구 기록이 없습니다. 기록을 초기화할 수 없습니다.")
            self._atomic_write(path, self._receipt_content(session_id, ()))

    def mark_recovered(self, session_id: str, source_block_ids: Iterable[str]) -> frozenset[str]:
        """Durably consume selected IDs before queue commit, under restore_guard.

        A later queue/UI failure may leave a conservative consumed receipt.
        Callers must never clear it or infer that a write failure means no bytes
        reached disk. Only genuinely new source IDs may be added to the set.
        """
        path = self._receipt_path(session_id)
        with self._lock:
            current = self.consumed_block_ids(session_id)
            selected = tuple(source_block_ids)
            for block_id in selected:
                _string(block_id, "source_block_ids", nonempty=True)
            selected_ids = frozenset(selected)
            if len(selected_ids) != len(selected):
                raise RecoveryValidationError("복구할 원본 블록 식별자가 중복되었습니다.")
            if current & selected_ids:
                raise RecoveryReadError("이미 복구한 원본 블록입니다. 최신 세션의 큐 백업을 선택하세요.")
            updated = current | selected_ids
            try:
                self._atomic_write(path, self._receipt_content(session_id, updated))
            except OSError as error:
                raise RecoveryWriteError(f"원본 블록의 복구 기록을 저장하지 못했습니다: {path} ({error})") from error
            return updated

    def _lease_path(self, session_id: str) -> Path:
        _session_id(session_id)
        return self.directory / f"session-{session_id}.lock"

    @contextmanager
    def restore_guard(self, session_id: str) -> Iterator[None]:
        """Hold source-session exclusion across review and the manual UI commit.

        The reader never creates a lease file. Its absence removes the evidence
        that this snapshot was published by a monitored session, so recovery
        fails closed. A simple probe before commit would leave a race window.
        """
        path = self._lease_path(session_id)
        try:
            stream = path.open("r+b")
        except OSError as error:
            raise RecoveryReadError(f"원본 세션 종료를 확인할 수 없습니다: {path} ({error})") from error
        try:
            try:
                _acquire_lease(stream)
            except OSError as error:
                if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise RecoverySessionActiveError("원본 빠른 인쇄기가 실행 중입니다. 원본을 종료한 뒤 큐를 복구하세요.") from error
                raise RecoveryReadError(f"원본 세션 상태를 확인하지 못했습니다: {path} ({error})") from error
            try:
                yield
            finally:
                _release_lease(stream)
        finally:
            stream.close()

    def is_session_active(self, session_id: str) -> bool:
        """An advisory probe only; restore_guard must protect the eventual commit."""
        try:
            with self.restore_guard(session_id):
                return False
        except RecoverySessionActiveError:
            return True


class RecoveryWriter:
    """One lazy writer coalesces queue edits while retaining ordered disk commits.

    A submitted payload is copied before return. Only the most recent pending
    payload is retained; a write already running finishes before the next one.
    Errors are available both through flush() and a one-shot UI error poll.
    """

    def __init__(self, store: RecoveryStore) -> None:
        self.store = store
        self._condition = threading.Condition()
        self._pending: tuple[int, dict[str, Any], bytes | None] | None = None
        self._revision = 0
        self._processed = 0
        self._last_result_error: RecoveryWriteError | None = None
        self._notification_error: RecoveryWriteError | None = None
        self._thread: threading.Thread | None = None
        self._closed = False
        self._running = False
        self._session_id: str | None = None
        self._lease_owned = False

    @property
    def busy(self) -> bool:
        with self._condition:
            return self._running or self._pending is not None

    def submit(self, payload: dict[str, Any]) -> int:
        content = _encoded_snapshot(payload)
        snapshot = json.loads(content)
        return self._enqueue_snapshot(snapshot, content)

    def submit_captured(self, payload: dict[str, Any]) -> int:
        """Detach an app capture now, fully validate it on the worker before I/O.

        Unlike ``submit``, schema/size errors are reported by ``pop_error`` and
        ``flush``. The unvalidated payload cannot create a directory, lease,
        receipt or snapshot. Manual required saves retain synchronous checks.
        """
        try:
            snapshot = _detach_captured_snapshot(payload)
        except RecursionError as error:
            raise RecoveryValidationError("자동 큐 스냅샷의 중첩이 너무 깊습니다.") from error
        return self._enqueue_snapshot(snapshot, None)

    def _enqueue_snapshot(self, snapshot: dict[str, Any], content: bytes | None) -> int:
        with self._condition:
            if self._closed:
                raise RuntimeError("작업 큐 복구 저장기가 종료되었습니다.")
            if content is not None:
                if not snapshot["queue"]:
                    return self._revision
                if self._session_id is not None and snapshot["session_id"] != self._session_id:
                    raise ValueError("하나의 저장기는 한 실행 세션의 큐만 저장할 수 있습니다.")
                self._session_id = snapshot["session_id"]
            self._revision += 1
            self._pending = (self._revision, snapshot, content)
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="quick-print-recovery", daemon=True)
                self._thread.start()
            self._condition.notify_all()
            return self._revision

    def _run(self) -> None:
        lease = None
        receipt_initialized = False
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._pending is not None or self._closed)
                    if self._pending is None:
                        return
                    revision, payload, content = self._pending
                    self._pending = None
                    self._running = True
                error: RecoveryWriteError | None = None
                try:
                    if content is None:
                        content = _encoded_snapshot(payload)
                    # The complete schema and byte limit must pass before even
                    # acquiring a lease or publishing consumption evidence.
                    if payload["queue"]:
                        with self._condition:
                            if self._session_id is not None and payload["session_id"] != self._session_id:
                                raise ValueError("하나의 저장기는 한 실행 세션의 큐만 저장할 수 있습니다.")
                            self._session_id = payload["session_id"]
                        if lease is None:
                            self.store.directory.mkdir(parents=True, exist_ok=True)
                            lease = self.store._lease_path(payload["session_id"]).open("a+b")
                            try:
                                _acquire_lease(lease)
                            except OSError:
                                lease.close()
                                lease = None
                                raise
                            with self._condition:
                                self._lease_owned = True
                        if not receipt_initialized:
                            self.store._initialize_consumption_receipt(payload["session_id"])
                            receipt_initialized = True
                        if getattr(self.store.save, "__func__", None) is RecoveryStore.save:
                            self.store._save_encoded_snapshot(payload, content)
                        else:
                            # Keep custom Store implementations and test gates
                            # on the existing save override boundary.
                            self.store.save(payload)
                except Exception as caught:
                    error = caught if isinstance(caught, RecoveryWriteError) else RecoveryWriteError(f"작업 큐 복구 저장 실패: {caught}")
                with self._condition:
                    self._processed = revision
                    self._last_result_error = error
                    self._running = False
                    if error is not None:
                        self._notification_error = error
                    self._condition.notify_all()
        finally:
            if lease is not None:
                try:
                    _release_lease(lease)
                finally:
                    lease.close()
                    with self._condition:
                        self._lease_owned = False

    def pop_error(self) -> RecoveryWriteError | None:
        with self._condition:
            error = self._notification_error
            self._notification_error = None
            return error

    @contextmanager
    def restore_guard(self, session_id: str) -> Iterator[None]:
        """Borrow our lifetime lease, or exclusively guard a different source.

        Borrowing is valid only when the app has verified an idle, empty queue
        with no pending material builds and holds its modal restore UI. It does
        not release the writer's lease, authorize reprinting, or replace the
        caller's history reconciliation. Other source sessions use the store's
        exclusive guard so an active printer instance is always refused.
        """
        _session_id(session_id)
        self.flush()
        with self._condition:
            owned = self._session_id == session_id and self._lease_owned and not self._closed
        if owned:
            yield
        else:
            with self.store.restore_guard(session_id):
                yield

    def flush(self, revision: int | None = None, *, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        with self._condition:
            target = self._revision if revision is None else revision
            if type(target) is not int or not 0 <= target <= self._revision:
                raise ValueError("올바른 복구 저장 순번이 필요합니다.")
            while self._processed < target:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._condition.wait(timeout=remaining):
                    raise TimeoutError("작업 큐 복구본 저장 완료를 기다리다 시간이 초과되었습니다.")
            if self._last_result_error is not None:
                raise self._last_result_error

    def save_required(self, payload: dict[str, Any], *, timeout: float = 5.0) -> None:
        self.flush(self.submit(payload), timeout=timeout)

    def close(self, *, timeout: float = 5.0) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
            if thread.is_alive():
                raise TimeoutError("작업 큐 복구 저장기가 아직 종료되지 않았습니다.")
        self.flush(timeout=timeout)
