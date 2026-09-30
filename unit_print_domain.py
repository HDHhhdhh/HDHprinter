"""Queue and dispatch value types and pure print-workflow decisions.

This module has no GUI, app state, disk mutation, or Windows process calls.
The manager imports the same objects for compatibility with existing callers.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from unit_print_name_separator import normalize_name_separator_label
from unit_print_printer_domain import derive_profile_names
from unit_print_test_catalog import TEST_SOURCE_BY_KIND

DEVICE_COMPLETION_TIMEOUT_SECONDS = 2 * 60 * 60

CUSTOM_PRINTER_ROUTE_CHOICES = ("자동 감지", "가로", "세로", "A:ble")

FIXED_FORM_SOURCES = frozenset({"blank_review", "omr"})

AUTO_OMR_TEST_SOURCES = frozenset(
    {
        TEST_SOURCE_BY_KIND["학군지기출"],
        TEST_SOURCE_BY_KIND["일산기출"],
    }
)

ABLE_DOCUMENT_KIND_CHOICES = ("교재", "백지리뷰", "개념정리")

ABLE_LEGACY_BUNDLE_KIND = "전체 묶음"

ABLE_PRINT_COMPOSITION_CHOICES = (
    "풀세트",
    "교재 + 백지리뷰",
    "교재",
    "백지리뷰",
    "개념정리",
)

class PrintCancelled(RuntimeError):
    pass

def resolve_custom_pdf_printer_profile(base_printer: str, route_label: str) -> str | None:
    """Resolve a direct-PDF route to a concrete Windows printer queue."""
    choice = route_label.strip() or "자동 감지"
    if choice == "자동 감지":
        return None
    normalized_base = base_printer.strip()
    for suffix in (" - 세로", " - 가로", " - 구분지", " -A:ble"):
        if normalized_base.endswith(suffix):
            normalized_base = normalized_base[: -len(suffix)].rstrip()
            break
    try:
        profiles = derive_profile_names(normalized_base)
    except ValueError:
        return "자동 감지"
    if choice == "가로":
        return profiles.landscape_queue
    if choice == "세로":
        return profiles.portrait_queue
    if choice == "A:ble":
        return profiles.able_queue
    raise ValueError(f"알 수 없는 프린터 프로필입니다: {choice}")

def normalize_printer_route(value: object) -> str:
    route = str(value or "자동 감지").strip()
    return route if route in CUSTOM_PRINTER_ROUTE_CHOICES else "자동 감지"

def normalize_able_document_kind(value: object, default: str = "") -> str:
    compact = re.sub(r"[\s_:：-]+", "", str(value or "")).casefold()
    aliases = {
        "교재": "교재",
        "렉쳐노트": "교재",
        "강의노트": "교재",
        "합본": "교재",
        "able": "교재",
        "백지리뷰": "백지리뷰",
        "백지": "백지리뷰",
        "리뷰": "백지리뷰",
        "개념정리": "개념정리",
        "개념정리노트": "개념정리",
        "전체묶음": ABLE_LEGACY_BUNDLE_KIND,
        "풀세트": ABLE_LEGACY_BUNDLE_KIND,
        "교재+백지리뷰": ABLE_LEGACY_BUNDLE_KIND,
        "렉쳐노트+백지리뷰": ABLE_LEGACY_BUNDLE_KIND,
    }
    return aliases.get(compact, default)

def able_print_composition_settings(value: object) -> tuple[str, str]:
    compact = re.sub(r"[\s_:：-]+", "", str(value or "")).casefold()
    settings = {
        "풀세트": ("풀 세트", ABLE_LEGACY_BUNDLE_KIND),
        "교재+백지리뷰": ("차시별", ABLE_LEGACY_BUNDLE_KIND),
        "교재": ("차시별", "교재"),
        "백지리뷰": ("차시별", "백지리뷰"),
        "개념정리": ("풀 세트", "개념정리"),
    }
    return settings.get(compact, ("차시별", "교재"))

def classify_able_document_kind(names: list[str]) -> str:
    text = " ".join(names).casefold()
    has_concept = "개념정리" in text
    has_review = "백지" in text
    has_textbook = "합본" in text or "렉쳐노트" in text or ("차시" in text and not has_review)
    if sum((has_concept, has_review, has_textbook)) > 1:
        return ABLE_LEGACY_BUNDLE_KIND
    if has_review:
        return "백지리뷰"
    if has_concept:
        return "개념정리"
    return "교재"

def infer_printer_route(base_printer: str, printer_name: object) -> str:
    if not printer_name:
        return "자동 감지"
    normalized_base = base_printer.strip()
    for suffix in (" - 세로", " - 가로", " - 구분지", " -A:ble"):
        if normalized_base.endswith(suffix):
            normalized_base = normalized_base[: -len(suffix)].rstrip()
            break
    profiles = derive_profile_names(normalized_base)
    routes = {
        profiles.landscape_queue: "가로",
        profiles.portrait_queue: "세로",
        profiles.able_queue: "A:ble",
    }
    return routes.get(str(printer_name).strip(), "자동 감지")

@dataclass(frozen=True)
class ExamPdf:
    number: int
    name: str
    path: Path
    printer_override: str | None = None
    pages_per_sheet: int | None = None

    @property
    def label(self) -> str:
        return f"{self.number:02d}. {self.name}"

@dataclass
class PrintBlock:
    subject: str
    level: str
    round_name: str
    start: int
    end: int
    copies: int
    files: list[ExamPdf]
    bundled: bool = False
    source: str = "catalog"
    printer_override: str | None = None
    display_label: str = ""
    pages_per_sheet: int = 1
    printer_route: str = "자동 감지"
    selected_numbers: tuple[int, ...] | None = None
    name_separator_label: str = ""
    material_kind: str = ""
    history_id: str = field(default_factory=lambda: str(uuid.uuid4()), compare=False)
    order_id: str = ""
    material_id: str = ""
    replaces_block_id: str = ""

    @property
    def range_label(self) -> str:
        if self.selected_numbers:
            return "(" + ",".join(str(number) for number in self.selected_numbers) + ")"
        return f"{self.start:02d}~{self.end:02d}"

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def target_label(self) -> str:
        if self.display_label:
            return self.display_label
        if self.source == "custom":
            return "새 PDF"
        return f"{self.subject} / {self.level} / {self.round_name}회차"

    @property
    def summary(self) -> str:
        mode = "묶음" if self.bundled else "일반"
        effective_layouts = {
            item.pages_per_sheet if item.pages_per_sheet is not None else self.pages_per_sheet
            for item in self.files
        }
        if len(effective_layouts) > 1:
            layout = " / 파일별 배치"
        else:
            layout = " / 2x2" if effective_layouts == {4} else ""
        route = f" / {self.printer_route}" if self.printer_route != "자동 감지" else ""
        name_separator = (
            f" / 이름 구분지: {self.name_separator_label}"
            if self.name_separator_label
            else ""
        )
        if self.source != "catalog":
            return (
                f"{self.target_label} / PDF {self.file_count}개 / {self.copies}부 / "
                f"{mode}{layout}{route}{name_separator}"
            )
        return (
            f"{self.target_label} / {self.range_label} / "
            f"{self.file_count}개 / {self.copies}부 / {mode}{layout}{route}{name_separator}"
        )

def able_block_document_kind(block: PrintBlock) -> str:
    stored = normalize_able_document_kind(block.material_kind)
    if stored:
        return stored
    return classify_able_document_kind(
        [block.display_label, *(item.name for item in block.files)]
    )

def block_uses_name_separator(block: PrintBlock) -> bool:
    """Fixed blank-review and OMR stock are not assigned to a student."""
    return block.source not in FIXED_FORM_SOURCES

def block_requires_omr_sheet(block: PrintBlock) -> bool:
    """School-district and Ilsan exam blocks receive one leading OMR sheet."""
    return block.source in AUTO_OMR_TEST_SOURCES

def name_separator_group_labels(
    blocks: list[PrintBlock],
    preceding_label: object = "",
) -> tuple[str | None, ...]:
    """Return a label only where a consecutive student-name group begins."""
    previous = normalize_name_separator_label(preceding_label)
    labels: list[str | None] = []
    for block in blocks:
        if not block_uses_name_separator(block):
            labels.append(None)
            previous = ""
            continue
        current = normalize_name_separator_label(block.name_separator_label)
        labels.append(current if not current or current != previous else None)
        previous = current
    return tuple(labels)

def name_separator_group_count(
    blocks: list[PrintBlock],
    preceding_label: object = "",
) -> int:
    return sum(
        label is not None
        for label in name_separator_group_labels(blocks, preceding_label)
    )

@dataclass(frozen=True)
class PrintJob:
    block_index: int
    block_total: int
    block_uid: int
    block_summary: str
    item: ExamPdf
    copies: int
    bundle_no: int | None = None
    bundle_total: int | None = None
    is_separator: bool = False
    printer_override: str | None = None
    pages_per_sheet: int = 1
    is_name_separator: bool = False
    is_omr: bool = False
    job_id: str = field(default_factory=lambda: str(uuid.uuid4()), compare=False)
    history_block: dict[str, object] = field(default_factory=dict, compare=False)

@dataclass(frozen=True)
class PrintRunOptions:
    printer_name: str
    engine: str
    sumatra_path: str
    duplex: bool
    dry_run: bool
    queue_batch_size: int
    queue_refill_threshold: int
    device_completion_mode: bool = False
    device_completion_timeout_seconds: int = DEVICE_COMPLETION_TIMEOUT_SECONDS
    device_window_size: int = 6
    device_monitor_base_url: str = ""
    device_verified_printers: frozenset[str] = frozenset()

def resolve_exam_print_settings(block: PrintBlock, item: ExamPdf) -> tuple[str | None, int]:
    # A:ble is one queue block, but its concept/review pages intentionally use
    # the portrait profile while lecture-note pages use the A:ble profile.
    if block.source == "material_able" and item.printer_override is not None:
        printer_override = item.printer_override
    elif block.printer_route != "자동 감지" and block.printer_override is not None:
        printer_override = block.printer_override
    else:
        printer_override = (
            item.printer_override if item.printer_override is not None else block.printer_override
        )
    pages_per_sheet = (
        item.pages_per_sheet if item.pages_per_sheet is not None else block.pages_per_sheet
    )
    return printer_override, pages_per_sheet
