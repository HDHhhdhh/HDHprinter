"""Canon Remote UI reader used by the quick printer.

Only documented status pages are read.  The client does not change device
settings, cancel jobs, or submit print data through the web interface.
"""

from __future__ import annotations

import hashlib
import http.cookiejar
import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from typing import Iterable

from unit_print_device_state import (
    DeviceConsumables,
    DeviceHealth,
    DeviceJob,
    DeviceJobState,
    DeviceSnapshot,
    PaperSupply,
    merge_error_texts,
)


class CanonStatusError(RuntimeError):
    pass


class CanonAuthenticationError(CanonStatusError):
    pass


class CanonProtocolError(CanonStatusError):
    pass


@dataclass(frozen=True)
class _Cell:
    text: str
    hints: tuple[str, ...] = ()


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[_Cell]]] = []
        self.all_text: list[str] = []
        self.visible_text: list[str] = []
        self.forms: list[dict[str, object]] = []
        self._script_depth = 0
        # Canon wraps the useful status grids in layout tables.  Keep every
        # table as its own context instead of accepting only the outermost one.
        self._table_stack: list[dict[str, object]] = []
        self._cell_stack: list[dict[str, object]] = []
        self._current_form: dict[str, object] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): (value or "") for key, value in attrs}
        tag = tag.casefold()
        if tag in {"script", "style"}:
            self._script_depth += 1
        if tag == "form":
            self._current_form = {
                "action": values.get("action", ""),
                "method": values.get("method", "post").casefold(),
                "inputs": [],
            }
            self.forms.append(self._current_form)
        elif tag == "input" and self._current_form is not None:
            inputs = self._current_form["inputs"]
            assert isinstance(inputs, list)
            inputs.append(values)

        if tag == "table":
            table: list[list[_Cell]] = []
            self.tables.append(table)
            self._table_stack.append({"table": table, "row": None})
        elif tag == "tr" and self._table_stack:
            self._table_stack[-1]["row"] = []
        elif tag in {"td", "th"} and self._table_stack:
            context = self._table_stack[-1]
            if isinstance(context.get("row"), list):
                self._cell_stack.append(
                    {"context": context, "text": [], "hints": []}
                )
        if tag == "img" and self._cell_stack:
            for key in ("alt", "title", "src"):
                if values.get(key):
                    for cell in self._cell_stack:
                        hints = cell["hints"]
                        assert isinstance(hints, list)
                        hints.append(values[key])

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in {"script", "style"}:
            self._script_depth = max(0, self._script_depth - 1)
        if tag == "form":
            self._current_form = None
        if tag in {"td", "th"} and self._cell_stack:
            cell = self._cell_stack.pop()
            text_parts = cell["text"]
            hint_parts = cell["hints"]
            context = cell["context"]
            assert isinstance(text_parts, list)
            assert isinstance(hint_parts, list)
            assert isinstance(context, dict)
            row = context.get("row")
            if isinstance(row, list):
                row.append(
                    _Cell(
                        text=_clean(" ".join(text_parts)),
                        hints=tuple(_clean(value) for value in hint_parts if _clean(value)),
                    )
                )
        if tag == "tr" and self._table_stack:
            context = self._table_stack[-1]
            table = context.get("table")
            row = context.get("row")
            if isinstance(table, list) and isinstance(row, list) and row:
                table.append(row)
            context["row"] = None
        elif tag == "table" and self._table_stack:
            self._table_stack.pop()

    def handle_data(self, data: str) -> None:
        clean = _clean(data)
        if not clean:
            return
        self.all_text.append(clean)
        if not self._script_depth:
            self.visible_text.append(clean)
        for cell in self._cell_stack:
            text = cell["text"]
            assert isinstance(text, list)
            text.append(clean)


def _clean(value: str) -> str:
    return " ".join(str(value).replace("\xa0", " ").split())


def _parse(html: str) -> _TableParser:
    parser = _TableParser()
    parser.feed(html)
    return parser


def _normalized(value: str) -> str:
    return re.sub(r"[\s:/()×·_-]+", "", value).casefold()


def _header_map(row: list[_Cell]) -> dict[str, int]:
    return {_normalized(cell.text): index for index, cell in enumerate(row) if cell.text}


def _find_index(headers: dict[str, int], *candidates: str) -> int | None:
    for candidate in candidates:
        wanted = _normalized(candidate)
        for header, index in headers.items():
            if wanted == header or wanted in header:
                return index
    return None


def _cell_text(row: list[_Cell], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return row[index].text


_PERCENT_RANGE = re.compile(r"(?P<low>\d{1,3})\s*[-~～]\s*(?P<high>\d{1,3})\s*%")
_PERCENT_SINGLE = re.compile(r"(?P<value>\d{1,3})\s*%")


def _parse_level(text: str, hints: Iterable[str] = ()) -> tuple[int | None, int | None, str]:
    combined = _clean(" ".join((text, *hints)))
    match = _PERCENT_RANGE.search(combined)
    if match:
        low = max(0, min(100, int(match.group("low"))))
        high = max(low, min(100, int(match.group("high"))))
        return low, high, f"{low}~{high}%"
    match = _PERCENT_SINGLE.search(combined)
    if match:
        value = max(0, min(100, int(match.group("value"))))
        return value, value, f"{value}%"
    # Canon commonly expresses bands only in image filenames.
    for hint, low, high in (
        ("100", 66, 100),
        ("75", 66, 100),
        ("50", 33, 66),
        ("25", 1, 33),
        ("0", 0, 0),
    ):
        if re.search(rf"(?:^|[^0-9]){hint}(?:[^0-9]|$)", combined):
            return low, high, f"{low}~{high}%" if low != high else f"{low}%"
    lowered = combined.casefold()
    if any(word in lowered for word in ("없음", "empty", "none")):
        return 0, 0, "0%"
    return None, None, text or "알 수 없음"


def _json_assignment(html: str, variable_name: str) -> object | None:
    """Read a JSON-compatible object assigned inside Canon's portal script."""

    marker = re.search(rf"\bvar\s+{re.escape(variable_name)}\s*=\s*", html)
    if marker is None:
        return None
    try:
        value, _end = json.JSONDecoder().raw_decode(html[marker.end() :].lstrip())
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return value


def _script_paper_supplies(html: str) -> tuple[PaperSupply, ...]:
    raw = _json_assignment(html, "cstInfo")
    if not isinstance(raw, dict):
        return ()
    level_bands = {
        "0": (0, 0, "0%"),
        "1": (1, 33, "1~33%"),
        "2": (33, 66, "33~66%"),
        "3": (66, 100, "66~100%"),
    }
    paper: list[PaperSupply] = []
    for value in raw.values():
        if not isinstance(value, dict):
            continue
        source = _clean(str(value.get("cstName", "")))
        paper_size = _clean(str(value.get("pSizeName", "")))
        if not source or not paper_size:
            continue
        remain = str(value.get("remainPapVol", ""))
        total = str(value.get("totalPapVol", ""))
        # Cassette sensors on this generation report four bands.  Other
        # source types such as the manual tray only report presence/absence.
        if total == "3" and remain in level_bands:
            low, high, level_text = level_bands[remain]
        elif remain == "0":
            low, high, level_text = 0, 0, "0%"
        else:
            low, high, level_text = None, None, "알 수 없음"
        paper.append(PaperSupply(source, paper_size, low, high, level_text))
    return tuple(paper)


def _script_toner_text(html: str) -> str:
    raw = _json_assignment(html, "tonerVolInfo")
    if not isinstance(raw, dict):
        return "알 수 없음"
    labels = {
        "tonerKVol": "블랙",
        "tonerCVol": "시안",
        "tonerMVol": "마젠타",
        "tonerYVol": "옐로우",
    }
    values = [
        f"{labels.get(str(key), str(key))} {value}%"
        for key, value in raw.items()
        if str(value).strip().isdigit()
    ]
    return ", ".join(values) if values else "알 수 없음"


def parse_supplies_html(html: str) -> DeviceConsumables:
    parser = _parse(html)
    paper: list[PaperSupply] = []
    toner = "알 수 없음"
    staple = "알 수 없음"
    saddle = "알 수 없음"

    for table in parser.tables:
        if not table:
            continue
        headers = _header_map(table[0])
        source_index = _find_index(headers, "용지 공급원", "급지 위치", "카세트", "source")
        size_index = _find_index(headers, "용지 크기", "용지 사이즈", "paper size", "size")
        level_index = _find_index(headers, "잔량", "용지 잔량", "remaining", "level")
        if size_index is not None and (source_index is not None or level_index is not None):
            for row in table[1:]:
                size = _cell_text(row, size_index)
                if not size:
                    continue
                source = _cell_text(row, source_index) or f"급지함 {len(paper) + 1}"
                level_cell = row[level_index] if level_index is not None and level_index < len(row) else _Cell("")
                low, high, text = _parse_level(level_cell.text, level_cell.hints)
                paper.append(PaperSupply(source, size, low, high, text))

        for row in table:
            joined = _clean(" ".join(cell.text for cell in row))
            if not joined:
                continue
            # Layout cells can contain the portal's entire JavaScript source.
            # Those strings mention toner/staples but are not status values.
            if len(joined) > 240 or not (2 <= len(row) <= 6):
                continue
            value = row[-1].text if len(row) > 1 else joined
            normalized = _normalized(joined)
            if ("새들" in joined or "중철" in joined) and "스테이플" in joined:
                saddle = value
            elif "스테이플" in joined or "staple" in normalized:
                staple = value
            elif "토너" in joined or "toner" in normalized:
                toner = value

    # Some firmware uses definition-like rows without table headers.
    all_text = " | ".join(parser.visible_text)
    if toner == "알 수 없음":
        match = re.search(r"토너[^|]{0,40}[|:]\s*([^|]+)", all_text)
        if match:
            toner = _clean(match.group(1))
    script_paper = _script_paper_supplies(html)
    if script_paper:
        by_source = {item.source: item for item in paper}
        for item in script_paper:
            by_source[item.source] = item
        paper = list(by_source.values())
    script_toner = _script_toner_text(html)
    if script_toner != "알 수 없음":
        toner = script_toner
    return DeviceConsumables(tuple(paper), toner, staple, saddle)


def _merge_consumables(
    primary: DeviceConsumables,
    fallback: DeviceConsumables,
) -> DeviceConsumables:
    paper = primary.paper or fallback.paper
    toner = primary.toner_text if primary.toner_text != "알 수 없음" else fallback.toner_text
    staple = primary.staple_text if primary.staple_text != "알 수 없음" else fallback.staple_text
    saddle = (
        primary.saddle_staple_text
        if primary.saddle_staple_text != "알 수 없음"
        else fallback.saddle_staple_text
    )
    return DeviceConsumables(paper, toner, staple, saddle)


_ERROR_FREE_PHRASES = (
    "에러가 없습니다",
    "오류가 없습니다",
    "no error",
    "no errors",
)
_READY_PHRASES = ("인쇄 가능합니다", "인쇄할 수 있습니다", "ready to print")
_BUSY_PHRASES = ("인쇄 중", "처리 중", "준비 중", "printing", "processing", "preparing")
_ERROR_PHRASES = (
    "스테이플을 보급",
    "스테이플 없음",
    "용지를 보급",
    "용지 없음",
    "용지가 없습니다",
    "토너를 교환",
    "걸린 용지",
    "용지 걸림",
    "커버를 닫",
    "서비스 담당자",
    "an error has occurred",
    "printer error",
)

# Observed on the operator's iR-ADV 8585 III with cassette 1 empty and
# cassette 2 still at 66-100%.  Canon reports this paper warning in
# errCodeTbl as well as setting prt_status=0x20.  An arbitrary non-null
# errCodeTbl entry must continue to block dispatch.
_A4_PAPER_WARNING_CODES = {"0x1060202"}
# Physically matched on this Canon: drawer 1 open/close, and drawer 2 open
# while one test sheet printed from the other drawer (2026-10-01).
# Do not generalize the firmware's variable mask to untested feed positions.
_CASSETTE_OPEN_CODES = {0x01180102: 1, 0x01180202: 2}
_CASSETTE_OPEN_MESSAGE = "A4 카세트가 열려 있습니다. 용지를 보충한 뒤 닫아 주세요."


def _portal_status_values(html: str) -> tuple[int | None, tuple[str, ...]] | None:
    """Extract current values only; static error-message templates are ignored."""
    status_match = re.search(
        r"\bvar\s+prt_status\s*=\s*['\"]?((?:0[xX])?[0-9A-Fa-f]+)['\"]?",
        html,
    )
    errors_match = re.search(
        r"\bvar\s+errCodeTbl\s*=\s*new\s+Array\s*\((.*?)\)\s*;",
        html,
        re.I | re.S,
    )
    if status_match is None and errors_match is None:
        return None

    error_codes: list[str] = []
    if errors_match is not None:
        values = [value.strip() for value in errors_match.group(1).split(",")]
        empty_values = {"", "null", "undefined", "''", '""'}
        error_codes = [value.strip("'\"") for value in values if value.casefold() not in empty_values]

    status_code: int | None = None
    if status_match is not None:
        raw_status = status_match.group(1)
        try:
            status_code = int(raw_status, 16 if raw_status.casefold().startswith("0x") else 10)
        except ValueError:
            pass

    return status_code, tuple(error_codes)


def _open_cassettes(status_code: int | None, error_codes: tuple[str, ...]) -> tuple[int, ...]:
    # 0x20/0x21 were observed during idle/printing with a drawer open.
    # Missing, reserved or additional hard-error bits never open the gate.
    if status_code not in (0x20, 0x21) or not error_codes:
        return ()
    try:
        codes = tuple(int(code, 16) for code in error_codes if re.fullmatch(r"0[xX][0-9a-fA-F]+", code))
    except ValueError:
        return ()
    if len(codes) != len(error_codes) or any(code not in _CASSETTE_OPEN_CODES for code in codes):
        return ()
    return tuple(sorted({_CASSETTE_OPEN_CODES[code] for code in codes}))


def _script_health(html: str) -> tuple[DeviceHealth, str, tuple[str, ...]] | None:
    """Read current portal status variables, ignoring the message catalogue."""
    values = _portal_status_values(html)
    if values is None:
        return None
    status_code, error_codes = values
    if opened := _open_cassettes(status_code, error_codes):
        labels = ", ".join(f"카세트 {number}" for number in opened)
        # Still blocking until the full snapshot proves another A4 drawer can feed.
        return DeviceHealth.HELD, f"{labels} 용지 보충 중", (_CASSETTE_OPEN_MESSAGE,)
    if error_codes:
        if (
            {code.casefold() for code in error_codes} <= _A4_PAPER_WARNING_CODES
            and (status_code is None or not status_code & 0xC4)
        ):
            message = "A4 용지 공급 상태를 확인해 주세요."
            return DeviceHealth.ERROR, "A4 용지 공급 확인", (message,)
        codes = ", ".join(error_codes)
        message = f"프린터가 장비 오류를 보고했습니다. (코드: {codes})"
        return DeviceHealth.ERROR, "프린터 오류", (message,)

    if status_code is None:
        return DeviceHealth.UNKNOWN, "상태 확인됨", ()

    # Canon's portal maps 0x20 to paper-supply attention.  It can remain set
    # when one configured cassette is empty even though another A4 cassette
    # can continue the job.  Reconcile that bit with per-cassette sensors once
    # supplies have been parsed.  Other high bits and 0x04 remain hard errors.
    if status_code & 0xC4:
        message = "프린터가 장비 오류를 보고했습니다."
        return DeviceHealth.ERROR, "프린터 오류", (message,)
    if status_code & 0x20:
        message = "A4 용지 공급 상태를 확인해 주세요."
        return DeviceHealth.ERROR, "A4 용지 공급 확인", (message,)
    if status_code & 0x10:
        return DeviceHealth.BUSY, "인쇄 준비 중", ()
    if status_code & 0x01:
        return DeviceHealth.BUSY, "인쇄 중", ()
    if status_code == 0x08:
        return DeviceHealth.READY, "절전 중 · 인쇄 가능", ()
    if status_code == 0:
        return DeviceHealth.READY, "인쇄 가능", ()
    # Unlabelled/reserved states must not open the dispatch gate.
    return DeviceHealth.UNKNOWN, f"알 수 없는 장비 상태 ({status_code})", ()


def parse_health_html(html: str) -> tuple[DeviceHealth, str, tuple[str, ...], str]:
    parser = _parse(html)
    # Script bodies contain the complete message catalogue rather than the
    # current status.  Only rendered text may participate in phrase matching.
    text = _clean(" ".join(parser.visible_text))
    lowered = text.casefold()
    error_free = any(phrase in lowered for phrase in _ERROR_FREE_PHRASES)
    errors: list[str] = []
    for phrase in _ERROR_PHRASES:
        index = lowered.find(phrase.casefold())
        if index >= 0:
            start = max(0, index - 35)
            end = min(len(text), index + len(phrase) + 80)
            errors.append(text[start:end])
    errors_tuple = merge_error_texts(errors)
    if errors_tuple:
        health = DeviceHealth.ERROR
        status = errors_tuple[0]
    elif (script_health := _script_health(html)) is not None:
        health, status, script_errors = script_health
        errors_tuple = script_errors
    elif any(phrase in lowered for phrase in _BUSY_PHRASES):
        health = DeviceHealth.BUSY
        status = "인쇄 중"
    elif any(phrase in lowered for phrase in _READY_PHRASES) or error_free:
        health = DeviceHealth.READY
        status = "인쇄 가능" if any(phrase in lowered for phrase in _READY_PHRASES) else "오류 없음"
    else:
        health = DeviceHealth.UNKNOWN
        status = "상태 확인됨"

    serial_match = re.search(r"(?:시리얼|serial(?: number)?)\s*[:：]?\s*([A-Z0-9-]{5,})", text, re.I)
    serial = serial_match.group(1) if serial_match else ""
    return health, status, errors_tuple, serial


_PAPER_SUPPLY_MARKERS = (
    "용지를 보급",
    "용지 없음",
    "용지가 없습니다",
    "용지 공급",
    "load paper",
    "out of paper",
    "paper supply",
)
_NON_PAPER_BLOCKING_MARKERS = (
    "스테이플",
    "토너",
    "걸린 용지",
    "용지 걸림",
    "커버",
    "서비스 담당자",
    "printer error",
    "an error has occurred",
)


def _is_paper_supply_only(message: str) -> bool:
    lowered = _clean(message).casefold()
    return (
        any(marker in lowered for marker in _PAPER_SUPPLY_MARKERS)
        and not any(marker in lowered for marker in _NON_PAPER_BLOCKING_MARKERS)
    )


def _a4_cassette_sources(consumables: DeviceConsumables) -> tuple[PaperSupply, ...]:
    a4_sources = consumables.a4_sources
    cassettes = tuple(
        source
        for source in a4_sources
        if any(
            marker in source.source.casefold()
            for marker in ("카세트", "cassette", "drawer")
        )
    )
    return cassettes or a4_sources


def reconcile_a4_paper_health(
    health: DeviceHealth,
    status: str,
    errors: tuple[str, ...],
    consumables: DeviceConsumables,
    *,
    has_active_jobs: bool = False,
) -> tuple[DeviceHealth, str, tuple[str, ...]]:
    """Treat one empty A4 cassette as a warning while another can feed.

    Unknown sensor values never prove that paper is available or exhausted.
    Non-paper faults remain blocking even when an A4 cassette has paper.
    """

    sources = _a4_cassette_sources(consumables)
    if not sources:
        return health, status, errors
    empty = tuple(source for source in sources if source.level_max_percent == 0)
    available = tuple(
        source
        for source in sources
        if source.level_max_percent is not None and source.level_max_percent > 0
    )

    if len(empty) == len(sources):
        message = "A4 용지가 모든 카세트에서 비었습니다."
        other_errors = tuple(error for error in errors if not _is_paper_supply_only(error))
        return DeviceHealth.ERROR, "A4 용지 없음", merge_error_texts((message, *other_errors))

    paper_errors = tuple(error for error in errors if _is_paper_supply_only(error))
    other_errors = tuple(error for error in errors if not _is_paper_supply_only(error))
    paper_status = _is_paper_supply_only(status)
    if empty and available and (paper_errors or paper_status):
        if other_errors:
            return DeviceHealth.ERROR, other_errors[0], other_errors
        empty_labels = ", ".join(source.source for source in empty)
        prefix = "인쇄 중" if has_active_jobs else "인쇄 가능"
        return (
            DeviceHealth.BUSY if has_active_jobs else DeviceHealth.READY,
            f"{prefix} · {empty_labels} 비어 있음",
            (),
        )
    return health, status, errors


def _reconcile_cassette_open_health(
    html: str,
    health: DeviceHealth,
    status: str,
    errors: tuple[str, ...],
    consumables: DeviceConsumables,
    *,
    has_active_jobs: bool,
) -> tuple[DeviceHealth, str, tuple[str, ...]] | None:
    """Resolve only verified drawer-open attention against closed A4 drawers.

    The open drawer's 0% is not proof of empty paper. Visible faults and mixed
    or unknown codes retain their existing blocking behavior.
    """
    if health != DeviceHealth.HELD or errors != (_CASSETTE_OPEN_MESSAGE,):
        return None
    values = _portal_status_values(html)
    if values is None:
        return None
    status_code, codes = values
    opened = _open_cassettes(status_code, codes)
    if not opened:
        return None
    closed_numbers = {1, 2}.difference(opened)
    closed_labels = {f"카세트 {number}" for number in closed_numbers}
    available = any(
        source.source.strip() in closed_labels
        and source.level_max_percent is not None
        and source.level_max_percent > 0
        for source in consumables.a4_sources
    )
    if not available:
        return health, status, errors
    busy = has_active_jobs or status_code == 0x21
    return (
        DeviceHealth.BUSY if busy else DeviceHealth.READY,
        f"{'인쇄 중' if busy else '인쇄 가능'} · {status}",
        (),
    )


_TOKEN_RE = re.compile(r"QP[_-]([A-Z0-9]{8,32})", re.I)


def _token_from_name(name: str) -> str:
    match = _TOKEN_RE.search(name)
    return match.group(1).upper() if match else ""


def _parse_datetime(value: str) -> datetime | None:
    value = _clean(value)
    for fmt in ("%Y %m/%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).astimezone()
        except ValueError:
            pass
    return None


def _parse_int(value: str) -> int | None:
    match = re.search(r"\d+", value.replace(",", ""))
    return int(match.group()) if match else None


def _parse_sets_copies(value: str) -> tuple[int | None, int | None]:
    numbers = [int(item) for item in re.findall(r"\d+", value.replace(",", ""))]
    if len(numbers) >= 2:
        return numbers[0], numbers[1]
    if len(numbers) == 1:
        return numbers[0], None
    return None, None


def _active_job_state(value: str) -> DeviceJobState:
    """Convert rendered text or Canon's inline ``job_s(code, ...)`` call."""

    code_match = re.search(r"\bjob_s\s*\(\s*['\"]?(\d+)", value, re.I)
    if code_match is not None:
        code = int(code_match.group(1))
        if code in {1, 4, 5, 6, 7, 8, 9}:
            return DeviceJobState.HELD
        if code in {2, 10}:
            return DeviceJobState.PRINTING
        if code == 3:
            return DeviceJobState.QUEUED
    normalized = _normalized(value)
    if any(
        word in normalized
        for word in ("보류", "hold", "중지", "오류", "error", "일시정지", "용지없음", "잠김")
    ):
        return DeviceJobState.HELD
    if any(word in normalized for word in ("인쇄", "printing", "처리", "대기")):
        return DeviceJobState.PRINTING
    return DeviceJobState.QUEUED


def parse_jobs_html(html: str, *, active: bool) -> tuple[DeviceJob, ...]:
    parser = _parse(html)
    jobs: list[DeviceJob] = []
    for table in parser.tables:
        if not table:
            continue
        headers = _header_map(table[0])
        name_index = _find_index(headers, "파일명", "문서명", "작업명", "document name", "file name")
        if name_index is None:
            # This Canon generation writes the column headers through
            # JavaScript.  The response therefore contains only fixed-order
            # data rows even though the browser displays a normal table.
            for row in table:
                if active and len(row) >= 5:
                    # Blank selector, date/time, job name, user, status,
                    # operation.  This is the observed six-column layout.
                    started_text = _cell_text(row, 1)
                    name = _cell_text(row, 2)
                    raw_state = _cell_text(row, 4)
                    if not name or _parse_datetime(started_text) is None:
                        continue
                    jobs.append(
                        DeviceJob(
                            document_name=name,
                            state=_active_job_state(raw_state),
                            started_at=_parse_datetime(started_text),
                            dispatch_token=_token_from_name(name),
                        )
                    )
                elif not active and len(row) >= 12:
                    receipt_no = _cell_text(row, 0)
                    start_text = _cell_text(row, 2)
                    end_text = _cell_text(row, 3)
                    name = _cell_text(row, 6)
                    end_code = _cell_text(row, 11)
                    if (
                        not receipt_no.isdigit()
                        or not name
                        or _parse_datetime(start_text) is None
                    ):
                        continue
                    state = (
                        DeviceJobState.COMPLETED
                        if end_code.casefold() == "ok"
                        else DeviceJobState.FAILED
                    )
                    sets, copies = _parse_sets_copies(_cell_text(row, 10))
                    jobs.append(
                        DeviceJob(
                            document_name=name,
                            state=state,
                            receipt_no=receipt_no,
                            result="확인" if state == DeviceJobState.COMPLETED else "실패",
                            end_code=end_code,
                            started_at=_parse_datetime(start_text),
                            ended_at=_parse_datetime(end_text),
                            source_pages=_parse_int(_cell_text(row, 8)),
                            output_pages=_parse_int(_cell_text(row, 9)),
                            sets=sets,
                            copies=copies,
                            dispatch_token=_token_from_name(name),
                        )
                    )
            continue
        receipt_index = _find_index(headers, "접수번호", "receipt")
        result_index = _find_index(headers, "결과", "result")
        state_index = _find_index(headers, "상황", "상태", "status")
        start_index = _find_index(headers, "시작 시각", "시작일시", "start time")
        end_index = _find_index(headers, "종료 시각", "종료일시", "end time")
        source_index = _find_index(headers, "원고 페이지", "원고매수", "original pages")
        output_index = _find_index(headers, "출력 페이지", "출력매수", "output pages")
        copies_index = _find_index(headers, "부수", "세트", "sets", "copies")
        code_index = _find_index(headers, "종료 코드", "end code")
        for row in table[1:]:
            name = _cell_text(row, name_index)
            if not name:
                continue
            result = _cell_text(row, result_index)
            end_code = _cell_text(row, code_index)
            raw_state = _cell_text(row, state_index)
            normalized = _normalized(" ".join((result, end_code, raw_state)))
            if active:
                state = _active_job_state(raw_state)
            elif end_code.casefold() == "ok" or any(word in normalized for word in ("정상", "확인", "complete", "success")):
                state = DeviceJobState.COMPLETED
            else:
                state = DeviceJobState.FAILED
            sets, copies = _parse_sets_copies(_cell_text(row, copies_index))
            jobs.append(
                DeviceJob(
                    document_name=name,
                    state=state,
                    receipt_no=_cell_text(row, receipt_index),
                    result=result,
                    end_code=end_code,
                    started_at=_parse_datetime(_cell_text(row, start_index)),
                    ended_at=_parse_datetime(_cell_text(row, end_index)),
                    source_pages=_parse_int(_cell_text(row, source_index)),
                    output_pages=_parse_int(_cell_text(row, output_index)),
                    sets=sets,
                    copies=copies,
                    dispatch_token=_token_from_name(name),
                )
            )
    return tuple(jobs)


def _decode_response(response: object, payload: bytes) -> str:
    headers = getattr(response, "headers", None)
    charset = headers.get_content_charset() if headers is not None else None
    for encoding in (charset, "utf-8", "cp949", "euc-kr"):
        if not encoding:
            continue
        try:
            return payload.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            pass
    return payload.decode("utf-8", errors="replace")


def _looks_like_login(html: str) -> bool:
    parser = _parse(html)
    for form in parser.forms:
        inputs = form.get("inputs", [])
        if not isinstance(inputs, list):
            continue
        names = " ".join(
            f"{item.get('name', '')} {item.get('id', '')} {item.get('type', '')}"
            for item in inputs
            if isinstance(item, dict)
        ).casefold()
        if "dept" in names or "password" in names or "pin" in names:
            return True
    return False


class CanonStatusClient:
    DEFAULT_PATHS = {
        "portal": "/rps/",
        "health": "/rps/dstatus.cgi?CorePGTAG=9&PageFlag=d_sts2.tpl",
        "supplies": "/rps/dstatus.cgi?CorePGTAG=11",
        "active_jobs": "/rps/jstatpri.cgi?Flag=Init_Data&CorePGTAG=1",
        "job_log": "/rps/jlp.cgi?Flag=Html_Data&LogType=0&CorePGTAG=2",
    }

    def __init__(
        self,
        base_url: str,
        *,
        department_id: str = "",
        pin: str = "",
        timeout_seconds: float = 6.0,
        paths: dict[str, str] | None = None,
    ) -> None:
        clean = base_url.strip().rstrip("/")
        if not clean.startswith(("http://", "https://")):
            raise ValueError("프린터 상태 주소는 http:// 또는 https://로 시작해야 합니다.")
        self.base_url = clean
        self.department_id = department_id
        self.pin = pin
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.paths = {**self.DEFAULT_PATHS, **(paths or {})}
        jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(jar),
        )
        self._authenticated = False
        self._job_monitor_initialized = False
        # Remote UI navigation mutates cookies and the current sysmonitor
        # context.  The background snapshot reader and the dispatch worker
        # therefore share one re-entrant session lock.
        self._session_lock = threading.RLock()

    def _url(self, key_or_path: str) -> str:
        path = self.paths.get(key_or_path, key_or_path)
        return urllib.parse.urljoin(f"{self.base_url}/", path.lstrip("/"))

    def _request(self, key_or_path: str, data: dict[str, str] | None = None) -> str:
        encoded = urllib.parse.urlencode(data).encode("ascii") if data is not None else None
        request = urllib.request.Request(
            self._url(key_or_path),
            data=encoded,
            headers={
                "User-Agent": "QuickPrinter-DeviceMonitor/1.0",
                "Accept-Language": "ko,en;q=0.7",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                payload = response.read(4 * 1024 * 1024)
                return _decode_response(response, payload)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise CanonStatusError(f"프린터 상태 연결 실패: {exc}") from exc

    def login(self) -> None:
        with self._session_lock:
            self._login_unlocked()

    def _login_unlocked(self) -> None:
        portal = self._request("portal")
        if not _looks_like_login(portal):
            self._authenticated = True
            self._job_monitor_initialized = False
            return
        if not self.department_id or not self.pin:
            raise CanonAuthenticationError("부서 ID와 PIN이 필요합니다.")
        parser = _parse(portal)
        login_form: dict[str, object] | None = None
        for form in parser.forms:
            inputs = form.get("inputs", [])
            if not isinstance(inputs, list):
                continue
            joined = " ".join(
                f"{item.get('name', '')} {item.get('id', '')} {item.get('type', '')}"
                for item in inputs
                if isinstance(item, dict)
            ).casefold()
            if "dept" in joined or "password" in joined or "pin" in joined:
                login_form = form
                break
        if login_form is None:
            raise CanonProtocolError("Canon 로그인 양식을 찾지 못했습니다.")
        fields: dict[str, str] = {}
        inputs = login_form.get("inputs", [])
        assert isinstance(inputs, list)
        for item in inputs:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", ""))
            if not name:
                continue
            kind = str(item.get("type", "text")).casefold()
            marker = f"{name} {item.get('id', '')}".casefold()
            value = str(item.get("value", ""))
            if kind == "hidden":
                fields[name] = value
            elif kind == "password" or "pin" in marker or "pass" in marker:
                fields[name] = self.pin
            elif "dept" in marker or "department" in marker:
                fields[name] = self.department_id
            elif kind in {"submit", "button"} and value:
                fields[name] = value
        if self.department_id not in fields.values() or self.pin not in fields.values():
            raise CanonProtocolError("Canon 로그인 필드 이름을 확인하지 못했습니다.")
        action = str(login_form.get("action", "")) or self.paths["portal"]
        result = self._request(action, fields)
        if _looks_like_login(result):
            raise CanonAuthenticationError("부서 ID 또는 PIN을 확인해 주세요.")
        self._authenticated = True
        self._job_monitor_initialized = False

    @staticmethod
    def _expired_page(html: str) -> bool:
        return (
            "페이지를 열 수 없습니다" in html
            or "유효기한이 만료되었습니다" in html
        )

    def _initialize_job_monitor(self) -> None:
        html = self._request(
            "/sysmonitor",
            {
                "RUIPNxBundle": "",
                "CorePGTAG": "PGTAG_JOB_PRT_STAT",
            },
        )
        if _looks_like_login(html):
            self._authenticated = False
            self.login()
            html = self._request(
                "/sysmonitor",
                {
                    "RUIPNxBundle": "",
                    "CorePGTAG": "PGTAG_JOB_PRT_STAT",
                },
            )
        if _looks_like_login(html) or self._expired_page(html):
            raise CanonProtocolError("Canon 작업 모니터 세션을 초기화하지 못했습니다.")
        self._job_monitor_initialized = True

    def _request_job_page(self, key: str) -> str:
        for _attempt in range(3):
            if not self._job_monitor_initialized:
                self._initialize_job_monitor()
            html = self._request(key)
            if _looks_like_login(html):
                self._authenticated = False
                self.login()
                continue
            if self._expired_page(html):
                self._job_monitor_initialized = False
                continue
            return html
        raise CanonProtocolError("Canon 작업 상태 페이지의 세션을 복구하지 못했습니다.")

    def read_active_jobs(self) -> tuple[DeviceJob, ...]:
        """Read only Canon's active print list for a fast dispatch barrier."""

        with self._session_lock:
            if not self._authenticated:
                self.login()
            return parse_jobs_html(self._request_job_page("active_jobs"), active=True)

    def read_recent_jobs(self) -> tuple[DeviceJob, ...]:
        """Read only Canon's completed-job list for very short print jobs."""

        with self._session_lock:
            if not self._authenticated:
                self.login()
            return parse_jobs_html(self._request_job_page("job_log"), active=False)

    def read_snapshot(self) -> DeviceSnapshot:
        with self._session_lock:
            return self._read_snapshot_unlocked()

    def _read_snapshot_unlocked(self) -> DeviceSnapshot:
        if not self._authenticated:
            self.login()
        portal = self._request("portal")
        if _looks_like_login(portal):
            self._authenticated = False
            self.login()
            portal = self._request("portal")
        if _looks_like_login(portal):
            raise CanonAuthenticationError("Canon Remote UI 로그인이 만료되었습니다.")

        # This firmware exposes current health and cassette/toner data on the
        # portal itself.  Prefer that single source and request legacy detail
        # pages only when portal data is genuinely absent.
        health, status, errors, serial = parse_health_html(portal)
        if health == DeviceHealth.UNKNOWN:
            try:
                detail_health = parse_health_html(self._request("health"))
            except CanonStatusError:
                detail_health = (DeviceHealth.UNKNOWN, "상태 확인됨", (), "")
            if detail_health[0] != DeviceHealth.UNKNOWN:
                health, status, errors, detail_serial = detail_health
                serial = serial or detail_serial

        consumables = parse_supplies_html(portal)
        if (
            not consumables.paper
            and consumables.toner_text == "알 수 없음"
            and consumables.staple_text == "알 수 없음"
            and consumables.saddle_staple_text == "알 수 없음"
        ):
            try:
                consumables = _merge_consumables(
                    parse_supplies_html(self._request("supplies")),
                    consumables,
                )
            except CanonStatusError:
                pass

        active_html = self._request_job_page("active_jobs")
        log_html = self._request_job_page("job_log")
        active_jobs = parse_jobs_html(active_html, active=True)
        recent_jobs = parse_jobs_html(log_html, active=False)
        if active_jobs and health == DeviceHealth.READY:
            health = DeviceHealth.BUSY
            status = "인쇄 중"
        loading_health = _reconcile_cassette_open_health(
            portal, health, status, errors, consumables,
            has_active_jobs=bool(active_jobs),
        )
        if loading_health is not None:
            health, status, errors = loading_health
        else:
            health, status, errors = reconcile_a4_paper_health(
                health,
                status,
                errors,
                consumables,
                has_active_jobs=bool(active_jobs),
            )
        identity = serial or self.base_url
        device_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        return DeviceSnapshot(
            observed_at=datetime.now().astimezone(),
            health=health,
            status_text=status,
            errors=errors,
            consumables=consumables,
            active_jobs=active_jobs,
            recent_jobs=recent_jobs,
            device_key=device_key,
            authenticated=True,
            source_url=self.base_url,
        )
