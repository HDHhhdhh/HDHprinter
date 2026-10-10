"""Latest-only preview formatting. No Tk calls and no print dispatch."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable

from unit_print_domain import (
    ExamPdf, PrintBlock, block_requires_omr_sheet,
    name_separator_group_labels, resolve_exam_print_settings,
)
from unit_print_pdf_printing import PdfOrientation, PdfPreflightResult
from unit_print_printer_domain import PrintRouteKind, derive_profile_names, route_print_job


PREVIEW_CHUNK_CHARS = 24_000


@dataclass(frozen=True)
class PreviewRequest:
    # Blocks and the two dictionaries are detached on the UI thread. ExamPdf,
    # Path, preflight results and scalar metadata are immutable values.
    blocks: tuple[PrintBlock, ...]
    printer: str
    name_separator: bool
    separator: bool
    automatic_omr: ExamPdf | None
    cached_results: dict[Path, PdfPreflightResult]
    cached_signatures: dict[Path, tuple[int, int] | None]


@dataclass(frozen=True)
class PreviewResult:
    chunks: tuple[str, ...]
    unresolved_paths: frozenset[Path]


def build_preview(request: PreviewRequest, cancelled: Callable[[], bool]) -> PreviewResult | None:
    """Keep all original preview lines; check files once per unique path."""
    if not request.blocks:
        return PreviewResult(("작업 큐가 비어 있습니다. 범위를 선택하고 [블록 추가]를 누르세요.\n",), frozenset())
    profiles = derive_profile_names(request.printer)
    unresolved: set[Path] = set()
    missing_by_path: dict[Path, str] = {}
    route_by_key: dict[tuple[Path, str | None], str] = {}
    result_by_path: dict[Path, PdfPreflightResult | None] = {}
    chunks: list[str] = []
    lines: list[str] = []
    char_count = 0

    def emit(line: str) -> None:
        nonlocal char_count
        lines.append(line)
        char_count += len(line)
        if char_count >= PREVIEW_CHUNK_CHARS:
            chunks.append("".join(lines))
            lines.clear()
            char_count = 0

    def missing(path: Path) -> str:
        if path not in missing_by_path:
            missing_by_path[path] = "" if path.exists() else "  [파일 없음]"
        return missing_by_path[path]

    def route(item: ExamPdf, override: str | None) -> str:
        key = (item.path, override)
        if key in route_by_key:
            return route_by_key[key]
        if item.path not in result_by_path:
            result = request.cached_results.get(item.path)
            if result is not None:
                try:
                    stat = item.path.stat()
                    signature = (stat.st_size, stat.st_mtime_ns)
                except OSError:
                    signature = None
                if request.cached_signatures.get(item.path) != signature:
                    result = None
            result_by_path[item.path] = result
        result = result_by_path[item.path]
        if result is None:
            unresolved.add(item.path)
        if override:
            text = f"  [전용 -> {override}]"
        elif result is None:
            text = "  [자동 감지 확인 중]"
        elif not result.is_printable or result.orientation is None:
            finding = result.findings[0] if result.findings else None
            text = f"  [인쇄 차단: {finding.code.value if finding is not None else '검사 실패'}]"
        else:
            kind = PrintRouteKind.PORTRAIT if result.orientation is PdfOrientation.PORTRAIT else PrintRouteKind.LANDSCAPE
            name = route_print_job(profiles, kind).queue_name
            text = f"  [{'세로' if kind is PrintRouteKind.PORTRAIT else '가로'} -> {name}]"
        route_by_key[key] = text
        return text

    labels = name_separator_group_labels(request.blocks) if request.name_separator else (None,) * len(request.blocks)
    name_route = f"  [이름 구분지·세로 -> {route_print_job(profiles, PrintRouteKind.PORTRAIT).queue_name}]"
    separator_route = f"  [구분지 -> {route_print_job(profiles, PrintRouteKind.SEPARATOR).queue_name}]"
    seq = 1
    for block_index, block in enumerate(request.blocks, start=1):
        if cancelled():
            return None
        emit(f"[블록 {block_index}] {block.summary}\n")
        label = labels[block_index - 1]
        if request.name_separator and label is not None:
            emit(f"  {seq:03d}. [이름 구분지] {label or '[이름 미입력]'} x 1부{name_route}\n")
            seq += 1
        omr = request.automatic_omr
        if omr is not None and block_requires_omr_sheet(block):
            emit(f"  {seq:03d}. [자동 OMR] {omr.path.name} x 1부{missing(omr.path)}{route(omr, omr.printer_override)}\n")
            seq += 1
        details = []
        for item in block.files:
            if cancelled():
                return None
            override, pages_per_sheet = resolve_exam_print_settings(block, item)
            details.append((item, missing(item.path), "  [A4 가로 2x2]" if pages_per_sheet == 4 else "", route(item, override)))
        if block.bundled:
            for bundle_no in range(1, block.copies + 1):
                if cancelled():
                    return None
                emit(f"  - 묶음 {bundle_no:03d}/{block.copies:03d}\n")
                for item, absent, layout, destination in details:
                    emit(f"    {seq:03d}. {item.path.name} x 1부{absent}{layout}{destination}\n")
                    seq += 1
        else:
            for item, absent, layout, destination in details:
                emit(f"  {seq:03d}. {item.path.name} x {block.copies}부{absent}{layout}{destination}\n")
                seq += 1
        if request.separator:
            emit(f"  {seq:03d}. [구분지] 고정 표지 x 1부{separator_route}\n")
            seq += 1
        emit("\n")
    if lines:
        chunks.append("".join(lines))
    return PreviewResult(tuple(chunks), frozenset(unresolved))


class PreviewWorker:
    """One finite worker. A newer request replaces pending work and cancels old work."""

    def __init__(self) -> None:
        self.results: queue.SimpleQueue[tuple[int, PreviewResult | Exception]] = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._revision = 0
        self._pending: tuple[int, PreviewRequest] | None = None
        self._thread: threading.Thread | None = None

    def cancel(self, revision: int) -> None:
        with self._lock:
            self._revision = revision
            self._pending = None
        # Do not retain obsolete large previews when rapid edits keep replacing
        # the UI poll. An in-flight publication is still rejected by revision.
        while True:
            try:
                self.results.get_nowait()
            except queue.Empty:
                break

    def submit(self, revision: int, request: PreviewRequest) -> None:
        with self._lock:
            self._revision = revision
            self._pending = (revision, request)
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="quick-print-preview", daemon=True)
                try:
                    self._thread.start()
                except Exception as error:
                    self._thread = None
                    self._pending = None
                    self.results.put((revision, error))

    def _run(self) -> None:
        while True:
            with self._lock:
                pending = self._pending
                self._pending = None
                if pending is None:
                    self._thread = None
                    return
            revision, request = pending

            def cancelled() -> bool:
                with self._lock:
                    return revision != self._revision

            try:
                result = build_preview(request, cancelled)
            except Exception as error:
                result = error
            if result is not None and not cancelled():
                self.results.put((revision, result))
