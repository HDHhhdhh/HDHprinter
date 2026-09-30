"""CSV order-sheet parsing for the Quick Printer application.

This module deliberately does not import Tkinter or the print manager.  It
turns an Excel-friendly CSV into validated, typed rows; the manager then maps
those rows through the same block builders used by the interactive UI.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path


ORDER_CSV_HEADERS = (
    "주문번호",
    "자료유형",
    "과목",
    "레벨",
    "회차",
    "문서",
    "인쇄구성",
    "시작번호",
    "끝번호",
    "개별번호",
    "부수",
    "묶음",
    "프린터프로필",
    "파일경로",
    "메모",
    "자료ID",
    "학생이름",
)


_HEADER_ALIASES = {
    "주문": "주문번호",
    "주문id": "주문번호",
    "자료": "자료유형",
    "종류": "자료유형",
    "구분": "자료유형",
    "자료종류": "자료유형",
    "문서종류": "문서",
    "파트": "인쇄구성",
    "구성": "인쇄구성",
    "시작": "시작번호",
    "끝": "끝번호",
    "개별": "개별번호",
    "차시": "개별번호",
    "개별차시": "개별번호",
    "개별파일": "개별번호",
    "수량": "부수",
    "프로필": "프린터프로필",
    "프린터": "프린터프로필",
    "경로": "파일경로",
    "파일": "파일경로",
    "pdf경로": "파일경로",
    "비고": "메모",
    "자료id": "자료ID",
    "테스트id": "자료ID",
    "시험지id": "자료ID",
    "시험id": "자료ID",
    "서버시험id": "자료ID",
    "파일코드": "자료ID",
    "서버파일코드": "자료ID",
    "testid": "자료ID",
    "servertestid": "자료ID",
    "filecode": "자료ID",
    "콘텐츠id": "자료ID",
    "학생": "학생이름",
    "학생명": "학생이름",
    "이름": "학생이름",
    "성이름": "학생이름",
    "이름구분지": "학생이름",
}


_SOURCE_ALIASES = {
    "단원평가": "단원평가",
    "단평": "단원평가",
    "복습테스트": "복습테스트",
    "복습": "복습테스트",
    "학군지기출": "학군지기출",
    "학군지": "학군지기출",
    "일산기출": "일산기출",
    "일산": "일산기출",
    "apex": "APEX",
    "에이펙스": "APEX",
    "apexboost": "APEX Boost",
    "에이펙스boost": "APEX Boost",
    "에이펙스부스트": "APEX Boost",
    "able": "A:ble",
    "에이블": "A:ble",
    "트레이닝팩": "트레이닝팩",
    "트팩": "트레이닝팩",
    "백지리뷰": "백지리뷰",
    "백지": "백지리뷰",
    "pdf": "PDF",
    "pdf파일": "PDF",
    "직접pdf": "PDF",
}


_TRUE_VALUES = {"1", "y", "yes", "true", "on", "예", "켜짐", "묶음"}
_FALSE_VALUES = {"", "0", "n", "no", "false", "off", "아니오", "꺼짐", "일반"}
_ROUTE_ALIASES = {
    "": "자동 감지",
    "자동": "자동 감지",
    "자동감지": "자동 감지",
    "가로": "가로",
    "세로": "세로",
    "able": "A:ble",
    "에이블": "A:ble",
}


def name_separator_order_key(value: object) -> tuple[int, str]:
    """Sort Korean student labels 가나다순 and leave unnamed rows last."""

    normalized = unicodedata.normalize("NFC", " ".join(str(value or "").split()))
    return (0, normalized.casefold()) if normalized else (1, "")


_ABLE_DOCUMENT_KIND_ALIASES = {
    "교재": "교재",
    "렉쳐노트": "교재",
    "강의노트": "교재",
    "합본": "교재",
    "백지리뷰": "백지리뷰",
    "백지": "백지리뷰",
    "리뷰": "백지리뷰",
    "개념정리": "개념정리",
    "개념정리노트": "개념정리",
}


def _compact(value: object) -> str:
    return re.sub(r"[\s_:：-]+", "", str(value or "")).casefold()


def _canonical_header(value: object) -> str:
    raw = str(value or "").lstrip("\ufeff").strip()
    if raw in ORDER_CSV_HEADERS:
        return raw
    return _HEADER_ALIASES.get(_compact(raw), raw)


def normalize_order_source(value: object) -> str:
    source = _SOURCE_ALIASES.get(_compact(value))
    if source is None:
        raise ValueError(
            "자료유형은 단원평가, 복습테스트, 학군지기출, 일산기출, "
            "APEX, APEX Boost, A:ble, 트레이닝팩, 백지리뷰, PDF 중 하나여야 합니다."
        )
    return source


def normalize_order_mode(source: str, value: object) -> str:
    compact = _compact(value)
    if source in {"APEX", "APEX Boost"}:
        if not compact or compact in {"차시", "차시별"}:
            return "차시 별"
        if compact in {"전체", "풀세트", "풀셋"}:
            return "전체"
        if compact in {"part.1", "part1", "파트1"}:
            return "part.1"
        if compact in {"part.2", "part2", "파트2"}:
            return "part.2"
        raise ValueError("인쇄구성은 차시 별, 전체, part.1, part.2 중 하나여야 합니다.")
    if source in {"A:ble", "트레이닝팩"}:
        if compact in {"풀세트", "풀셋", "전체"}:
            return "풀 세트"
        if not compact or compact in {"차시", "차시별"}:
            return "차시별"
        raise ValueError("인쇄구성은 차시별 또는 풀 세트여야 합니다.")
    return ""


def normalize_order_route(value: object) -> str:
    route = _ROUTE_ALIASES.get(_compact(value))
    if route is None:
        raise ValueError("프린터프로필은 자동 감지, 가로, 세로, A:ble 중 하나여야 합니다.")
    return route


def parse_order_number_list(value: object) -> tuple[int, ...]:
    normalized = str(value or "").translate(
        str.maketrans(
            "０１２３４５６７８９，；－–—（）［］",
            "0123456789,,---()[]",
        )
    ).strip()
    if not normalized:
        return ()
    if normalized[0] in "([":
        closing = ")" if normalized[0] == "(" else "]"
        if not normalized.endswith(closing):
            raise ValueError("괄호를 닫아 주세요. 예: (1-3,6,7-9)")
        normalized = normalized[1:-1].strip()
    elif normalized[-1:] in ")]":
        raise ValueError("여는 괄호를 확인해 주세요. 예: (1-3,6,7-9)")
    if not normalized:
        return ()

    numbers: list[int] = []
    seen: set[int] = set()
    tokens = re.split(r"\s*[,;]\s*", normalized)
    if any(not token for token in tokens):
        raise ValueError("1-5 또는 1-3,6,7-9처럼 입력해 주세요.")
    for token in tokens:
        single_match = re.fullmatch(r"\d+", token)
        range_match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", token)
        if single_match:
            expanded = (int(token),)
        elif range_match:
            start = int(range_match.group(1))
            end = int(range_match.group(2))
            if end < start:
                raise ValueError("번호 범위는 작은 번호-큰 번호 순서로 입력해 주세요.")
            expanded = range(start, end + 1)
        else:
            raise ValueError("1-5 또는 1-3,6,7-9처럼 입력해 주세요.")
        for number in expanded:
            if number < 1:
                raise ValueError("번호는 1 이상이어야 합니다.")
            if number in seen:
                raise ValueError("번호 또는 번호 범위를 중복해서 입력할 수 없습니다.")
            seen.add(number)
            numbers.append(number)
    return tuple(numbers)


@dataclass(frozen=True, slots=True)
class OrderCsvIssue:
    row_number: int
    field: str
    message: str

    def format(self) -> str:
        location = f"{self.row_number}행"
        if self.field:
            location += f" {self.field}"
        return f"{location}: {self.message}"


@dataclass(frozen=True, slots=True)
class OrderRow:
    row_number: int
    order_id: str
    source: str
    subject: str
    level: str
    round_name: str
    document_kind: str
    mode: str
    start: int | None
    end: int | None
    selected_numbers: tuple[int, ...]
    copies: int
    bundled: bool
    printer_profile: str
    file_paths: tuple[Path, ...]
    note: str
    material_id: str
    student_name: str

    @property
    def target_label(self) -> str:
        if self.material_id:
            return self.material_id
        if self.source == "백지리뷰":
            return "고정 백지리뷰"
        if self.source == "PDF":
            if len(self.file_paths) == 1:
                return self.file_paths[0].name
            return f"PDF {len(self.file_paths)}개"
        fields = [self.subject, self.level, self.round_name or self.document_kind, self.mode]
        target = " / ".join(field for field in fields if field)
        if self.selected_numbers:
            target += " / (" + ",".join(str(number) for number in self.selected_numbers) + ")"
        elif self.start is not None and self.end is not None:
            target += f" / {self.start}~{self.end}"
        return target


@dataclass(frozen=True, slots=True)
class OrderCsvResult:
    rows: tuple[OrderRow, ...]
    issues: tuple[OrderCsvIssue, ...]
    encoding: str

    @property
    def is_valid(self) -> bool:
        return bool(self.rows) and not self.issues


def _read_csv_text(path: Path) -> tuple[str, str]:
    payload = path.read_bytes()
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return payload.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("CSV를 UTF-8 또는 CP949로 저장해 주세요.")


def _parse_optional_int(raw: str, field: str) -> int | None:
    value = raw.strip()
    if not value:
        return None
    translated = value.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if not translated.isdigit() or int(translated) < 1:
        raise ValueError(f"{field}는 1 이상의 정수여야 합니다.")
    return int(translated)


def _parse_copies(raw: str) -> int:
    if not raw.strip():
        return 1
    copies = _parse_optional_int(raw, "부수")
    assert copies is not None
    return copies


def _parse_bundled(raw: str) -> bool:
    compact = _compact(raw)
    if compact in _TRUE_VALUES:
        return True
    if compact in _FALSE_VALUES:
        return False
    raise ValueError("묶음은 예/아니오 또는 Y/N으로 입력해 주세요.")


def _parse_paths(raw: str, csv_path: Path) -> tuple[Path, ...]:
    paths: list[Path] = []
    for token in (part.strip().strip('"') for part in raw.split("|")):
        if not token:
            continue
        path = Path(token).expanduser()
        if not path.is_absolute():
            path = csv_path.parent / path
        paths.append(path)
    return tuple(paths)


def _row_values(raw_row: dict[str | None, str | list[str] | None]) -> dict[str, str]:
    values = {header: "" for header in ORDER_CSV_HEADERS}
    for raw_header, raw_value in raw_row.items():
        if raw_header is None:
            continue
        header = _canonical_header(raw_header)
        if header not in values:
            continue
        if isinstance(raw_value, list):
            value = ",".join(raw_value)
        else:
            value = str(raw_value or "")
        values[header] = value.strip()
    return values


def _validate_row(values: dict[str, str], csv_path: Path, row_number: int) -> OrderRow:
    material_id = values["자료ID"].strip()
    raw_source = values["자료유형"].strip()
    source = normalize_order_source(raw_source) if raw_source else ""
    if not material_id and not source:
        raise ValueError("자료ID 또는 자료유형 중 하나가 필요합니다.")
    mode = "" if material_id else normalize_order_mode(source, values["인쇄구성"])
    start = _parse_optional_int(values["시작번호"], "시작번호")
    end = _parse_optional_int(values["끝번호"], "끝번호")
    selected_numbers = parse_order_number_list(values["개별번호"])
    copies = _parse_copies(values["부수"])
    bundled = _parse_bundled(values["묶음"])
    route = normalize_order_route(values["프린터프로필"])
    paths = _parse_paths(values["파일경로"], csv_path)
    subject = values["과목"].strip()
    level = values["레벨"].strip()
    round_name = values["회차"].strip()
    document_kind = values["문서"].strip() or "교재"
    if source == "A:ble" and not material_id:
        document_kind = _ABLE_DOCUMENT_KIND_ALIASES.get(_compact(document_kind), "")
        if not document_kind:
            raise ValueError("A:ble 문서는 교재, 백지리뷰, 개념정리 중 하나여야 합니다.")

    if (start is None) != (end is None):
        raise ValueError("시작번호와 끝번호는 함께 입력해 주세요.")
    if start is not None and end is not None and start > end:
        start, end = end, start

    if material_id:
        pass
    elif source == "단원평가":
        if not subject or not level or not round_name:
            raise ValueError("단원평가는 과목, 레벨, 회차가 모두 필요합니다.")
        if not selected_numbers and start is None:
            raise ValueError("단원평가는 시작/끝번호 또는 개별번호가 필요합니다.")
    elif source in {"APEX", "APEX Boost"}:
        if not subject:
            raise ValueError(f"{source}는 과목이 필요합니다.")
        if mode == "차시 별" and not selected_numbers and start is None:
            raise ValueError("차시 별 인쇄는 시작/끝번호 또는 개별번호가 필요합니다.")
    elif source in {"A:ble", "트레이닝팩"}:
        if not subject:
            raise ValueError(f"{source}는 과목이 필요합니다.")
        if mode == "차시별" and not selected_numbers and start is None:
            raise ValueError("차시별 인쇄는 시작/끝번호 또는 개별번호가 필요합니다.")
        if source == "A:ble" and document_kind == "개념정리" and mode != "풀 세트":
            raise ValueError("A:ble 개념정리는 풀 세트로만 인쇄할 수 있습니다.")
    elif source == "PDF" and not paths:
        raise ValueError("PDF 자료유형은 파일경로가 필요합니다. 여러 파일은 | 로 구분하세요.")
    elif source in {"복습테스트", "학군지기출", "일산기출"}:
        raise ValueError(f"{source} 주문은 등록 테스트 목록의 자료ID를 입력해 주세요.")

    return OrderRow(
        row_number=row_number,
        order_id=values["주문번호"].strip(),
        source=source,
        subject=subject,
        level=level,
        round_name=round_name,
        document_kind=document_kind,
        mode=mode,
        start=start,
        end=end,
        selected_numbers=selected_numbers,
        copies=copies,
        bundled=bundled,
        printer_profile=route,
        file_paths=paths,
        note=values["메모"].strip(),
        material_id=material_id,
        student_name=values["학생이름"].strip(),
    )


def load_order_csv(path: Path) -> OrderCsvResult:
    path = Path(path)
    try:
        text, encoding = _read_csv_text(path)
    except Exception as error:
        return OrderCsvResult((), (OrderCsvIssue(1, "파일", str(error)),), "")

    rows: list[OrderRow] = []
    issues: list[OrderCsvIssue] = []
    try:
        reader = csv.DictReader(text.splitlines())
        raw_headers = reader.fieldnames or []
        canonical_headers = tuple(_canonical_header(header) for header in raw_headers)
        if "자료유형" not in canonical_headers and "자료ID" not in canonical_headers:
            return OrderCsvResult(
                (),
                (
                    OrderCsvIssue(
                        1,
                        "자료ID/자료유형",
                        "자료ID 또는 자료유형 열이 없습니다. 앱의 양식을 사용해 주세요.",
                    ),
                ),
                encoding,
            )
        for row_number, raw_row in enumerate(reader, start=2):
            values = _row_values(raw_row)
            if not any(values.values()):
                continue
            try:
                rows.append(_validate_row(values, path, row_number))
            except Exception as error:
                issues.append(OrderCsvIssue(row_number, "", str(error)))
    except csv.Error as error:
        issues.append(OrderCsvIssue(1, "CSV", str(error)))

    if not rows and not issues:
        issues.append(OrderCsvIssue(1, "CSV", "주문 행이 없습니다."))
    return OrderCsvResult(tuple(rows), tuple(issues), encoding)


def write_order_csv_template(path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as output_file:
        csv.writer(output_file).writerow(ORDER_CSV_HEADERS)
    return path
