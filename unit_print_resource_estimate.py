"""Preflight estimates for paper, staples, and physical print time.

The Canon Remote UI exposes paper as sensor bands rather than exact sheet
counts.  This module combines those bands with the documented standard drawer
capacities of the imageRUNNER ADVANCE 8585i III.  It deliberately treats the
lower edge of each band as the amount that can be relied on.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from unit_print_device_state import DeviceConsumables
from unit_print_eta import EtaEstimate, EtaTimingSample, EtaWorkload, estimate_workloads_seconds, estimated_sheets


# Canon imageRUNNER ADVANCE 8585i III standard paper supply:
# drawers 1/2 = 1,500 sheets each, cassettes 3/4 = 550 sheets each,
# multipurpose tray = 100 sheets.  See Canon's official 8500 III spec sheet.
# https://downloads.canon.com/nw/pdfs/copiers/iRADV-8500i-III-SpecSheet.pdf
STANDARD_DRAWER_CAPACITY_SHEETS = {1: 1500, 2: 1500, 3: 550, 4: 550}
MULTIPURPOSE_TRAY_CAPACITY_SHEETS = 100
DEFAULT_STAPLE_CAPACITY_SHEETS = 65


class ResourceEstimateCancelled(RuntimeError):
    pass


class PaperReadiness(StrEnum):
    SUFFICIENT = "sufficient"
    BORDERLINE = "borderline"
    INSUFFICIENT = "insufficient"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ResourceJob:
    """A lightweight job description that never materializes a PDF."""

    path: Path | None
    copies: int = 1
    pages_per_sheet: int = 1
    stapled: bool = True
    known_page_count: int | None = None
    label: str = ""
    dispatch_jobs: int = 1


@dataclass(frozen=True, slots=True)
class PaperCapacityRange:
    lower_sheets: int
    upper_sheets: int
    known_sources: int
    total_a4_sources: int

    @property
    def is_complete(self) -> bool:
        return self.known_sources > 0 and self.known_sources == self.total_a4_sources


@dataclass(frozen=True, slots=True)
class PrintResourceEstimate:
    total_sheets: int
    staple_sets: int
    output_sets: int
    unknown_documents: int
    oversize_staple_sets: int
    eta: EtaEstimate
    paper_capacity: PaperCapacityRange | None
    paper_readiness: PaperReadiness

    @property
    def is_complete(self) -> bool:
        return self.unknown_documents == 0

    @property
    def tone(self) -> str:
        if self.oversize_staple_sets or self.paper_readiness is PaperReadiness.INSUFFICIENT:
            return "danger"
        if self.unknown_documents or self.paper_readiness in {
            PaperReadiness.BORDERLINE,
            PaperReadiness.UNKNOWN,
        }:
            return "warning"
        return "success"

    @property
    def paper_label(self) -> str:
        return {
            PaperReadiness.SUFFICIENT: "용지 충분",
            PaperReadiness.BORDERLINE: "용지 경계",
            PaperReadiness.INSUFFICIENT: "용지 부족 예상",
            PaperReadiness.UNKNOWN: "용지 판정 불가",
        }[self.paper_readiness]

    @property
    def display_text(self) -> str:
        return self.display_text_with_eta()

    def display_text_with_eta(self, completion_text: str | None = None) -> str:
        sheet_prefix = "A4" if self.is_complete else "A4 최소"
        eta_text = (completion_text or self.eta.display_text) if self.is_complete else "시간 일부 계산 불가"
        return (
            f"예상 {sheet_prefix} {self.total_sheets:,}장 · "
            f"스테이플 {self.staple_sets:,}회 · {eta_text} · {self.paper_label}"
        )

    @property
    def compact_text(self) -> str:
        return self.compact_text_with_eta()

    def compact_text_with_eta(self, completion_text: str | None = None) -> str:
        sheet_prefix = "A4" if self.is_complete else "A4≥"
        eta_text = (completion_text or self.eta.display_text) if self.is_complete else "시간 확인 중"
        return (
            f"{sheet_prefix} {self.total_sheets:,} · S {self.staple_sets:,} · "
            f"{eta_text} · {self.paper_label.replace('용지 ', '')}"
        )

    @property
    def detail_text(self) -> str:
        return self.detail_text_with_eta()

    def detail_text_with_eta(self, completion_text: str | None = None) -> str:
        lines = [
            f"예상 자원: A4 {'최소 ' if not self.is_complete else ''}{self.total_sheets:,}장, "
            f"스테이플 {self.staple_sets:,}회, "
            f"{self.eta.display_text if self.is_complete else '예상 시간 일부 계산 불가'}"
        ]
        if completion_text and self.is_complete:
            lines.append(completion_text)
            if "예상 완료" in completion_text and ":" in completion_text:
                lines.append(f"예측 기준: {self.eta.confidence} · 실제 완료는 프린터 작업 이력으로 확인합니다.")
        capacity = self.paper_capacity
        if capacity is not None:
            qualifier = "" if capacity.is_complete else "확인 가능한 카세트 기준 "
            lines.append(
                f"현재 A4 잔량: {qualifier}약 {capacity.lower_sheets:,}~"
                f"{capacity.upper_sheets:,}장 · {self.paper_label}"
            )
        else:
            lines.append("현재 A4 잔량: 카세트 용량 또는 센서 구간을 확인할 수 없음")
        if self.unknown_documents:
            lines.append(f"주의: 페이지 수를 확인하지 못한 PDF {self.unknown_documents}개가 있습니다.")
        if self.oversize_staple_sets:
            lines.append(
                "주의: 한 세트가 65장을 넘는 스테이플 작업 "
                f"{self.oversize_staple_sets}세트가 있습니다."
            )
        if self.paper_readiness is PaperReadiness.BORDERLINE:
            lines.append("주의: 센서 구간의 실제 잔량에 따라 작업 중 용지가 부족할 수 있습니다.")
        elif self.paper_readiness is PaperReadiness.INSUFFICIENT:
            lines.append("주의: 표시된 최대 잔량으로도 이번 작업의 A4가 부족할 수 있습니다.")
        return "\n".join(lines)


def source_capacity_sheets(source: object) -> int | None:
    """Return a conservative standard capacity for a Canon paper source."""

    normalized = " ".join(str(source or "").split()).casefold()
    compact = re.sub(r"[\s_\-]+", "", normalized)
    if any(
        marker in compact
        for marker in (
            "수동급지",
            "다목적",
            "멀티퍼포즈",
            "multipurpose",
            "bypass",
        )
    ):
        return MULTIPURPOSE_TRAY_CAPACITY_SHEETS
    numbers = [int(value) for value in re.findall(r"(?<!\d)([1-4])(?!\d)", normalized)]
    if not numbers:
        return None
    return STANDARD_DRAWER_CAPACITY_SHEETS.get(numbers[-1])


def estimate_a4_capacity(
    consumables: DeviceConsumables | None,
) -> PaperCapacityRange | None:
    """Convert A4 sensor bands to a range of possible physical sheets."""

    if consumables is None:
        return None
    sources = consumables.a4_sources
    if not sources:
        return None
    lower = 0
    upper = 0
    known = 0
    for item in sources:
        capacity = source_capacity_sheets(item.source)
        if (
            capacity is None
            or item.level_min_percent is None
            or item.level_max_percent is None
        ):
            continue
        low_percent = max(0, min(100, int(item.level_min_percent)))
        high_percent = max(low_percent, min(100, int(item.level_max_percent)))
        lower += math.floor(capacity * low_percent / 100)
        upper += math.ceil(capacity * high_percent / 100)
        known += 1
    if not known:
        return None
    return PaperCapacityRange(
        lower_sheets=lower,
        upper_sheets=upper,
        known_sources=known,
        total_a4_sources=len(sources),
    )


def assess_paper_readiness(
    required_sheets: int,
    capacity: PaperCapacityRange | None,
    *,
    estimate_complete: bool,
) -> PaperReadiness:
    if capacity is None:
        return PaperReadiness.UNKNOWN
    required = max(0, int(required_sheets))
    if required > capacity.upper_sheets:
        return PaperReadiness.INSUFFICIENT
    if not estimate_complete or not capacity.is_complete:
        return PaperReadiness.UNKNOWN
    if required <= capacity.lower_sheets:
        return PaperReadiness.SUFFICIENT
    return PaperReadiness.BORDERLINE


def estimate_print_resources(
    jobs: Iterable[ResourceJob],
    *,
    page_counter: Callable[[Path], int],
    duplex: bool,
    consumables: DeviceConsumables | None = None,
    verified_seconds_per_sheet: Iterable[float] = (),
    timing_samples: tuple[EtaTimingSample, ...] = (),
    staple_capacity_sheets: int = DEFAULT_STAPLE_CAPACITY_SHEETS,
    cancelled: Callable[[], bool] | None = None,
) -> PrintResourceEstimate:
    """Estimate physical resources without creating separators or layouts."""

    total_sheets = 0
    staple_sets = 0
    output_sets = 0
    unknown_documents = 0
    oversize_staple_sets = 0
    page_counts: dict[Path, int | None] = {}
    workloads: list[EtaWorkload] = []

    for job in jobs:
        if cancelled is not None and cancelled():
            raise ResourceEstimateCancelled("인쇄 자원 예측이 새 작업 큐로 교체되었습니다.")
        copies = max(0, int(job.copies))
        if not copies:
            continue
        output_sets += copies
        page_count = job.known_page_count
        if page_count is None and job.path is not None:
            path = Path(job.path)
            if path not in page_counts:
                try:
                    count = int(page_counter(path))
                    page_counts[path] = count if count > 0 else None
                except Exception:
                    page_counts[path] = None
            page_count = page_counts[path]
        if page_count is None or page_count <= 0:
            unknown_documents += 1
            continue
        pages_per_sheet = max(1, int(job.pages_per_sheet))
        output_pages_per_copy = math.ceil(page_count / pages_per_sheet)
        sheets_per_copy = estimated_sheets(output_pages_per_copy, 1, duplex)
        total_sheets += sheets_per_copy * copies
        workloads.append(EtaWorkload(sheets_per_copy * copies, copies, max(1, job.dispatch_jobs), duplex))
        if job.stapled and sheets_per_copy >= 2:
            staple_sets += copies
            if sheets_per_copy > max(1, int(staple_capacity_sheets)):
                oversize_staple_sets += copies

    # Legacy scalar rates lack document sizes and can contain queue latency.
    # Keep the argument compatible, but use classified physical timings.
    eta = estimate_workloads_seconds(tuple(workloads), timing_samples)
    capacity = estimate_a4_capacity(consumables)
    readiness = assess_paper_readiness(
        total_sheets,
        capacity,
        estimate_complete=unknown_documents == 0,
    )
    return PrintResourceEstimate(
        total_sheets=total_sheets,
        staple_sets=staple_sets,
        output_sets=output_sets,
        unknown_documents=unknown_documents,
        oversize_staple_sets=oversize_staple_sets,
        eta=eta,
        paper_capacity=capacity,
        paper_readiness=readiness,
    )
