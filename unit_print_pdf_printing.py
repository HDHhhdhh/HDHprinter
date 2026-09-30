from __future__ import annotations

import hashlib
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, Sequence

from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.errors import PyPdfError
from pypdf._page import PageObject

A4_PAPER_SETTING: Final = "paper=A4"
STANDARD_SUMATRA_SETTINGS: Final = ("fit", "monochrome", A4_PAPER_SETTING)
A4_WIDTH_POINTS: Final = 595.275590551
A4_HEIGHT_POINTS: Final = 841.88976378
FOUR_UP_LAYOUT_VERSION: Final = 1


class PdfOrientation(StrEnum):
    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"


class PdfPreflightFindingCode(StrEnum):
    MISSING = "missing"
    UNREADABLE = "unreadable"
    ENCRYPTED = "encrypted"
    ZERO_PAGES = "zero_pages"
    MIXED_ORIENTATION = "mixed_orientation"


@dataclass(frozen=True, slots=True)
class PdfPreflightPage:
    page_number: int
    orientation: PdfOrientation


@dataclass(frozen=True, slots=True)
class PdfPreflightFinding:
    path: Path
    page_number: int | None
    code: PdfPreflightFindingCode


@dataclass(frozen=True, slots=True)
class PdfPreflightResult:
    path: Path
    pages: tuple[PdfPreflightPage, ...]
    findings: tuple[PdfPreflightFinding, ...]

    @property
    def is_printable(self) -> bool:
        return bool(self.pages) and not self.findings

    @property
    def orientation(self) -> PdfOrientation | None:
        if self.is_printable:
            return self.pages[0].orientation
        return None


@dataclass(frozen=True, slots=True)
class PdfPreflightReport:
    results: tuple[PdfPreflightResult, ...]

    @property
    def is_printable(self) -> bool:
        return all(result.is_printable for result in self.results)

    @property
    def findings(self) -> tuple[PdfPreflightFinding, ...]:
        return tuple(finding for result in self.results for finding in result.findings)


@dataclass(frozen=True, slots=True)
class SumatraPdfBatch:
    paths: tuple[Path, ...]
    settings: tuple[str, ...]

    @property
    def settings_text(self) -> str:
        return ",".join(self.settings)


def pdf_has_landscape_page(pdf_path: Path) -> bool:
    result = preflight_pdf(pdf_path)
    return any(page.orientation is PdfOrientation.LANDSCAPE for page in result.pages)


def preflight_pdfs(pdf_paths: Sequence[Path]) -> PdfPreflightReport:
    paths = tuple(pdf_paths)
    if len(paths) < 4:
        results = tuple(preflight_pdf(pdf_path) for pdf_path in paths)
    else:
        with ThreadPoolExecutor(max_workers=min(6, len(paths))) as executor:
            results = tuple(executor.map(preflight_pdf, paths))
    return PdfPreflightReport(results)


def format_preflight_report(report: PdfPreflightReport) -> str:
    if report.is_printable:
        return "PDF 사전 검사를 통과했습니다."
    lines = ["인쇄할 수 없는 PDF가 있습니다."]
    for finding in report.findings:
        page_label = f" {finding.page_number}쪽" if finding.page_number is not None else ""
        lines.append(f"- {finding.path}{page_label}: {_finding_message(finding.code)}")
    return "\n".join(lines)


def preflight_pdf(pdf_path: Path) -> PdfPreflightResult:
    try:
        is_file = pdf_path.is_file()
    except OSError:
        return _preflight_failure(pdf_path, PdfPreflightFindingCode.UNREADABLE)
    if not is_file:
        return _preflight_failure(pdf_path, PdfPreflightFindingCode.MISSING)
    try:
        reader = PdfReader(str(pdf_path))
        if reader.is_encrypted:
            return _preflight_failure(pdf_path, PdfPreflightFindingCode.ENCRYPTED)
        pages = tuple(
            PdfPreflightPage(page_number=page_number, orientation=_pypdf_page_orientation(page))
            for page_number, page in enumerate(reader.pages, start=1)
        )
    except (OSError, PyPdfError, TypeError, ValueError):
        return _preflight_failure(pdf_path, PdfPreflightFindingCode.UNREADABLE)
    if not pages:
        return _preflight_failure(pdf_path, PdfPreflightFindingCode.ZERO_PAGES)
    first_orientation = pages[0].orientation
    findings = tuple(
        PdfPreflightFinding(
            path=pdf_path,
            page_number=page.page_number,
            code=PdfPreflightFindingCode.MIXED_ORIENTATION,
        )
        for page in pages
        if page.orientation is not first_orientation
    )
    return PdfPreflightResult(path=pdf_path, pages=pages, findings=findings)


def _preflight_failure(pdf_path: Path, code: PdfPreflightFindingCode) -> PdfPreflightResult:
    return PdfPreflightResult(
        path=pdf_path,
        pages=(),
        findings=(PdfPreflightFinding(path=pdf_path, page_number=None, code=code),),
    )


def _finding_message(code: PdfPreflightFindingCode) -> str:
    if code is PdfPreflightFindingCode.MISSING:
        return "파일을 찾을 수 없습니다"
    if code is PdfPreflightFindingCode.UNREADABLE:
        return "PDF를 읽을 수 없습니다"
    if code is PdfPreflightFindingCode.ENCRYPTED:
        return "암호가 설정된 PDF입니다"
    if code is PdfPreflightFindingCode.ZERO_PAGES:
        return "페이지가 없습니다"
    if code is PdfPreflightFindingCode.MIXED_ORIENTATION:
        return "세로와 가로 페이지가 섞여 있습니다"


def _pypdf_page_is_landscape(page: PageObject) -> bool:
    return _pypdf_page_orientation(page) is PdfOrientation.LANDSCAPE


def _pypdf_page_orientation(page: PageObject) -> PdfOrientation:
    width = float(page.mediabox.width)
    height = float(page.mediabox.height)
    rotation = int(page.get("/Rotate", 0)) % 360
    if rotation in {90, 270}:
        width, height = height, width
    return PdfOrientation.LANDSCAPE if width > height else PdfOrientation.PORTRAIT


def materialize_four_up_pdf(source: Path, cache_dir: Path) -> Path:
    """Lay out source pages left-to-right in a 2x2 grid on landscape A4 sheets."""
    stat = source.stat()
    fingerprint_source = (
        f"{source.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{FOUR_UP_LAYOUT_VERSION}"
    )
    fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()[:16]
    safe_stem = re.sub(r'[<>:"/\\|?*]+', "_", source.stem).strip(" .")[:80] or "document"
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / f"{safe_stem}_{fingerprint}_2x2.pdf"
    if destination.is_file():
        return destination

    reader = PdfReader(str(source))
    if reader.is_encrypted:
        raise ValueError(f"암호가 설정된 PDF는 2x2로 조판할 수 없습니다: {source.name}")
    if not reader.pages:
        raise ValueError(f"페이지가 없는 PDF는 2x2로 조판할 수 없습니다: {source.name}")

    sheet_width = A4_HEIGHT_POINTS
    sheet_height = A4_WIDTH_POINTS
    slot_width = sheet_width / 2
    slot_height = sheet_height / 2
    writer = PdfWriter()
    normalized_pages = PdfWriter()

    for first_page_index in range(0, len(reader.pages), 4):
        sheet = PageObject.create_blank_page(width=sheet_width, height=sheet_height)
        for slot_index, page_index in enumerate(range(first_page_index, min(first_page_index + 4, len(reader.pages)))):
            page = normalized_pages.add_page(reader.pages[page_index])
            page.transfer_rotation_to_content()
            box = page.cropbox
            page_width = float(box.width)
            page_height = float(box.height)
            if page_width <= 0 or page_height <= 0:
                raise ValueError(f"페이지 크기가 잘못된 PDF입니다: {source.name} {page_index + 1}쪽")

            column = slot_index % 2
            row = slot_index // 2
            scale = min(slot_width / page_width, slot_height / page_height)
            target_x = column * slot_width + (slot_width - page_width * scale) / 2
            target_y = (1 - row) * slot_height + (slot_height - page_height * scale) / 2
            transform = (
                Transformation()
                .translate(tx=-float(box.left), ty=-float(box.bottom))
                .scale(sx=scale, sy=scale)
                .translate(tx=target_x, ty=target_y)
            )
            sheet.merge_transformed_page(page, transform, expand=False)
        writer.add_page(sheet)

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{safe_stem}_",
            suffix=".tmp.pdf",
            dir=cache_dir,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            writer.write(temporary_file)
        temporary_path.replace(destination)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink(missing_ok=True)
    return destination


def _sumatra_print_settings_for_orientation(
    copies: int,
    duplex: bool,
    orientation: PdfOrientation,
) -> tuple[str, ...]:
    settings: list[str] = []
    if copies > 1:
        settings.append(f"{copies}x")
    if not duplex:
        settings.append("simplex")
    elif orientation is PdfOrientation.LANDSCAPE:
        settings.append("duplexshort")
    else:
        settings.append("duplexlong")
    settings.extend(STANDARD_SUMATRA_SETTINGS)
    return tuple(settings)


def sumatra_print_settings(pdf_path: Path, copies: int, duplex: bool) -> tuple[str, ...]:
    result = preflight_pdf(pdf_path)
    if not result.is_printable or result.orientation is None:
        return ()
    return _sumatra_print_settings_for_orientation(copies, duplex, result.orientation)


def sumatra_pdf_batches(pdf_paths: Sequence[Path], copies: int, duplex: bool) -> list[SumatraPdfBatch]:
    preflight = preflight_pdfs(pdf_paths)
    if not preflight.is_printable:
        return []
    batches: list[SumatraPdfBatch] = []
    current_paths: list[Path] = []
    current_settings: tuple[str, ...] | None = None
    for pdf_path, result in zip(pdf_paths, preflight.results, strict=True):
        assert result.orientation is not None
        settings = _sumatra_print_settings_for_orientation(copies, duplex, result.orientation)
        if current_settings is not None and settings != current_settings:
            batches.append(SumatraPdfBatch(tuple(current_paths), current_settings))
            current_paths = []
        current_paths.append(pdf_path)
        current_settings = settings
    if current_settings is not None:
        batches.append(SumatraPdfBatch(tuple(current_paths), current_settings))
    return batches
