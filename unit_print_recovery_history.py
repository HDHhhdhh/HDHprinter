"""Read the durable dispatch journal before offering manual queue recovery.

The existing ``job_attempt`` event is fsynced before the printer process is
called. Its presence excludes the whole block from recovery as waiting work,
even if the process crashed before submission or completion was recorded.
Absence is useful only when every required daily journal is readable.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


MAX_REVIEW_DAYS = 366
MAX_RECORD_BYTES = 32 * 1024 * 1024
MAX_REVIEW_BYTES = 256 * 1024 * 1024
MAX_REVIEW_RECORDS = 500_000
_DISPATCH_EVENTS = frozenset({
    "job_attempt", "job_submitted", "job_unconfirmed", "job_physically_completed",
})
_RISKS = frozenset({"unsent", "submitted", "uncertain", "completed"})


@dataclass(frozen=True, slots=True)
class RecoveryHistoryReview:
    safe_block_ids: frozenset[str]
    risky_block_ids: frozenset[str]
    completed_block_ids: frozenset[str]
    warning: str = ""


def _aware_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("시간 정보가 없습니다.")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("시간 정보가 올바르지 않습니다.") from error
    if result.utcoffset() is None:
        raise ValueError("시간대 정보가 없습니다.")
    return result


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("필수 이력 정보가 없습니다.")
    return value


def _reject_constant(value: str) -> Any:
    raise ValueError(f"허용되지 않는 JSON 숫자: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for key, value in pairs:
        if key in record:
            raise ValueError("이력 행에 중복된 JSON 필드가 있습니다.")
        record[key] = value
    return record


def _journal_record(raw: bytes, day: date) -> tuple[dict[str, Any], datetime]:
    try:
        record = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as error:
        raise ValueError("이력 행의 JSON 내용이 손상되었습니다.") from error
    if not isinstance(record, dict):
        raise ValueError("이력 행이 JSON 객체가 아닙니다.")
    if type(record.get("schema_version")) is not int or record["schema_version"] != 1:
        raise ValueError("지원하지 않는 이력 버전입니다.")
    _text(record.get("session_id"))
    _text(record.get("event_id"))
    event = _text(record.get("event"))
    timestamp = _aware_timestamp(record.get("timestamp"))
    if timestamp.date() != day:
        raise ValueError("이력 날짜와 파일 날짜가 일치하지 않습니다.")
    if event in _DISPATCH_EVENTS:
        _text(record.get("block_id"))
        _text(record.get("job_id"))
        _text(record.get("run_id"))
    return record, timestamp


def classify_recovery_queue(
    snapshot: dict,
    history_directory: Path,
    *,
    now: datetime | None = None,
    consumed_block_ids: Iterable[str] | None = None,
) -> RecoveryHistoryReview:
    """Partition blocks without changing a queue, journal, PDF, or printer.

    A closed source session needs journals only through its closing day. A
    crashed session needs all daily journals through today. The latter may
    conservatively hold an older backup when subsequent days have no journal;
    guessing that a missing day contained no dispatch would be unsafe.

    ``completed_block_ids`` is deliberately based on the saved whole-block
    classification. One physically completed job never proves the completion
    of a multi-document block.

    Consumption receipts are checked separately under the source-session
    restore guard. They prevent a restored block with a fresh history ID from
    leaving its original backup eligible for recovery a second time. ``None``
    means that this proof was not checked; an empty iterable means that a valid
    receipt was read and no original blocks have been consumed.
    """
    block_ids: set[str] = set()
    risky: set[str] = set()
    completed: set[str] = set()

    def held(reason: str) -> RecoveryHistoryReview:
        return RecoveryHistoryReview(
            frozenset(), frozenset(block_ids), frozenset(completed),
            f"인쇄 이력을 온전히 확인하지 못해 모든 블록을 복구 보류합니다. {reason}",
        )

    try:
        entries = snapshot["queue"]
        if not isinstance(entries, list):
            raise ValueError("큐 정보가 올바르지 않습니다.")
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("block"), dict):
                raise ValueError("블록 정보가 올바르지 않습니다.")
            block_id = _text(entry["block"].get("history_id"))
            if block_id in block_ids:
                raise ValueError("블록 식별자가 중복됩니다.")
            block_ids.add(block_id)
            risk = entry.get("risk")
            if risk not in _RISKS:
                raise ValueError("블록의 전송 여부를 확인할 수 없습니다.")
            if risk != "unsent":
                risky.add(block_id)
            if risk == "completed":
                completed.add(block_id)
        if not block_ids:
            return RecoveryHistoryReview(frozenset(), frozenset(), frozenset())
        if consumed_block_ids is None:
            raise ValueError("원본 블록의 이전 복구 여부를 확인하지 못했습니다.")
        if isinstance(consumed_block_ids, (str, bytes)):
            raise ValueError("원본 블록의 복구 확인 정보가 올바르지 않습니다.")
        consumed = frozenset(_text(value) for value in consumed_block_ids)
        risky.update(block_ids & consumed)
        if type(snapshot.get("schema_version")) is not int or snapshot["schema_version"] != 1:
            raise ValueError("지원하지 않는 큐 백업 버전입니다.")
        session_id = _text(snapshot.get("session_id"))
        started_at = _aware_timestamp(snapshot.get("session_started_at"))
        created_at = _aware_timestamp(snapshot.get("created_at"))
        observed_at = now if now is not None else datetime.now().astimezone()
        if not isinstance(observed_at, datetime) or observed_at.utcoffset() is None:
            raise ValueError("현재 시간의 시간대 정보가 없습니다.")
        if started_at > created_at or created_at > observed_at:
            raise ValueError("백업의 시작·저장·확인 시간이 올바르지 않습니다.")
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        return held(str(error))

    day = started_at.date()
    last_day = observed_at.astimezone(started_at.tzinfo).date()
    total_bytes = 0
    record_count = 0
    reviewed_days = 0
    source_started = False
    source_closed = False
    directory = Path(history_directory)
    while day <= last_day:
        reviewed_days += 1
        if reviewed_days > MAX_REVIEW_DAYS:
            return held(f"확인 기간이 {MAX_REVIEW_DAYS}일을 초과합니다.")
        filename = f"{day.isoformat()}.jsonl"
        path = directory / filename
        try:
            with path.open("rb") as stream:
                while raw := stream.readline(MAX_RECORD_BYTES + 1):
                    total_bytes += len(raw)
                    if len(raw) > MAX_RECORD_BYTES or total_bytes > MAX_REVIEW_BYTES:
                        return held("이력 파일이 안전하게 확인할 수 있는 크기를 초과합니다.")
                    if not raw.strip():
                        continue
                    if not raw.endswith(b"\n"):
                        return held(f"{filename}의 마지막 이력 행이 완전하지 않습니다.")
                    record_count += 1
                    if record_count > MAX_REVIEW_RECORDS:
                        return held("이력 행 수가 안전하게 확인할 수 있는 범위를 초과합니다.")
                    record, timestamp = _journal_record(raw, day)
                    event = record["event"]
                    if event in _DISPATCH_EVENTS and record["block_id"] in block_ids:
                        risky.add(record["block_id"])
                    if record["session_id"] != session_id:
                        continue
                    if event == "session_started":
                        if source_started or timestamp != started_at:
                            return held("원래 세션의 시작 이력이 백업과 일치하지 않습니다.")
                        source_started = True
                    elif event == "session_closed":
                        if not source_started or timestamp < created_at:
                            return held("원래 세션의 종료 이력이 백업과 일치하지 않습니다.")
                        source_closed = True
        except FileNotFoundError:
            return held(f"필요한 이력 파일 {filename}이 없습니다.")
        except (OSError, UnicodeError, ValueError, TypeError, RecursionError) as error:
            # Do not expose journal content, student names, or local full paths.
            detail = str(error) if isinstance(error, ValueError) else "파일을 읽을 수 없거나 이력 행이 손상되었습니다."
            return held(f"{filename}: {detail}")
        if day == started_at.date() and not source_started:
            return held("원래 세션의 시작 이력이 없습니다.")
        if source_closed:
            break
        if day == last_day:
            break
        day += timedelta(days=1)

    return RecoveryHistoryReview(
        frozenset(block_ids - risky), frozenset(risky), frozenset(completed),
    )
