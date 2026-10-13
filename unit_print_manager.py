from __future__ import annotations

# Direct execution shares the hidden launcher's validation and instance lease.
if __name__ == "__main__":
    from unit_print_bootstrap import main as bootstrap_main
    raise SystemExit(bootstrap_main())

import ctypes
from contextvars import ContextVar
import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import font as tkfont
from tkinter import BooleanVar, END, IntVar, StringVar, Text, Tk, filedialog, messagebox
from tkinter import ttk

from pypdf import PdfReader
from unit_print_domain import (
    AUTO_OMR_TEST_SOURCES, FIXED_FORM_SOURCES,
    ABLE_DOCUMENT_KIND_CHOICES, ABLE_LEGACY_BUNDLE_KIND, ABLE_PRINT_COMPOSITION_CHOICES,
    CUSTOM_PRINTER_ROUTE_CHOICES, DEVICE_COMPLETION_TIMEOUT_SECONDS,
    ExamPdf, PrintBlock, PrintCancelled, PrintJob, PrintRunOptions,
    able_block_document_kind, able_print_composition_settings,
    block_requires_omr_sheet, block_uses_name_separator, classify_able_document_kind,
    infer_printer_route, name_separator_group_count, name_separator_group_labels,
    normalize_able_document_kind, normalize_printer_route,
    resolve_custom_pdf_printer_profile, resolve_exam_print_settings,
)
from unit_print_execution import (
    PrintExecutionMixin, PrintExecutionServices,
    QUEUE_EMPTY_TIMEOUT_SECONDS, QUEUE_REFILL_TIMEOUT_SECONDS,
)
from unit_print_pdf_metadata import get_pdf_page_count
from unit_print_preview import PreviewRequest, PreviewWorker
from unit_print_queue_recovery import RECOVERY_DIRECTORY, RecoveryStore, RecoveryWriter
from unit_print_recovery_history import classify_recovery_queue
from unit_print_history import HISTORY_DIRECTORY
from unit_print_canon_status import CanonStatusClient, CanonStatusError
from unit_print_device_credentials import (
    CredentialStoreError,
    delete_device_credential,
    load_device_credential,
    save_device_credential,
)
from unit_print_device_monitor import PrinterDeviceMonitor
from unit_print_device_scheduler import (
    new_dispatch_token,
    prepare_dispatch_copy,
    remove_dispatch_copy,
)
from unit_print_device_state import DeviceHealth, DeviceJob, DeviceJobState, DeviceSnapshot
from unit_print_device_ui import build_device_status_view
from unit_print_theme import WORKBENCH_COLORS, QUEUE_CARD_RADIUS, apply_workbench_styles
from unit_print_typography import korean_label
from unit_print_eta import (
    CompletionTimeProjection,
    PrintEtaObservation,
    ETA_PRINT_RATE_CONFIG_KEY,
    ETA_SAMPLE_LIMIT,
    ETA_TIMING_CONFIG_KEY,
    ETA_TIMING_DEVICE_KEY,
    ETA_TIMING_SAMPLE_LIMIT,
    EtaTimingSample,
    completed_job_timing,
    initialize_timing_samples,
    timing_device_key,
    validated_timing_samples,
)
from unit_print_notifications import (
    NotificationGate,
    NotificationLevel,
    WindowsBalloonNotifier,
)
from unit_print_resource_estimate import (
    PrintResourceEstimate,
    ResourceJob,
    estimate_print_resources,
)
from unit_print_source_paths import SourceFolderOpenResult, open_source_folders, source_folder_targets

from unit_print_pdf_printing import (
    A4_HEIGHT_POINTS,
    A4_WIDTH_POINTS,
    PdfOrientation,
    PdfPreflightReport,
    PdfPreflightResult,
    format_preflight_report,
    materialize_four_up_pdf,
    preflight_pdf,
    preflight_pdfs,
    sumatra_pdf_batches,
)
from unit_print_materials import (
    DEFAULT_ABLE_ROOT,
    DEFAULT_APEX_ROOT,
    DEFAULT_FREQUENT_TYPE_ROOT,
    DEFAULT_TRAINING_PACK_ROOT,
    AbleSubject,
    ApexDocument,
    PageSpan,
    TrainingPackSubject,
    apex_boost_page_numbers,
    apex_manual_page_numbers,
    able_document_print_settings,
    combined_span,
    frequent_type_page_numbers,
    load_apex_catalog,
    materialize_pdf_documents,
    materialize_pdf_pages,
    materialize_pdf_span,
    scan_able_catalog,
    scan_frequent_type_catalog,
    scan_training_pack_catalog,
    training_pack_page_numbers,
)
from unit_print_name_separator import (
    materialize_name_separator_pdf,
    name_separator_fill_plan,
    normalize_name_separator_label,
)
from unit_print_gyopyeong import (
    DEFAULT_GYOPYEONG_ROOT,
    GyopyeongLesson,
    GyopyeongPart,
    GyopyeongSubject,
    scan_gyopyeong_catalog,
)
from unit_print_selected_exams import (
    DEFAULT_SELECTED_EXAM_ROOT,
    SelectedExamLesson,
    SelectedExamSubject,
    scan_selected_exam_catalog,
)
from unit_print_textbooks import (
    TEXTBOOK_DOCUMENT_KINDS,
    TextbookEdition,
    TextbookLesson,
    load_textbook_catalog,
    selected_textbook_page_numbers,
    selected_textbook_variant_page_numbers,
    selected_textbook_variant_problem_numbers,
    verify_textbook_asset,
)
from unit_print_printer_domain import PrintRouteKind, derive_profile_names, route_print_job
from unit_print_printer_binding import (
    read_printer_binding_inventory,
    resolve_printer_binding_hosts,
    validate_printer_binding,
)
from unit_print_printer_setup import (
    PowerShellResult,
    ProfileSetupBlocked,
    execute_profile_setup,
    inspect_base_queue,
    plan_profile_setup,
)
from unit_print_retry import RetryBatch, retry_from_failure
from unit_print_progress import (
    PersistentAlertSpec,
    PrintProgressState,
    RunPhase,
    persistent_alert_for_status,
    transition_taskbar_state,
)
from unit_print_preparation import (
    BackgroundBuildService,
    CommitToken,
    OrderedCommitQueue,
    PrintPreparationService,
    PriorityWorkScheduler,
)
from unit_print_queue_state import (
    PendingNameRepair,
    QueueReorderResult,
    QueueSnapshot,
    editable_waiting_block_ids,
    queue_drop_index,
    queue_hit_index,
    reconcile_pending_name_groups,
    replace_unstarted_block_jobs,
    reorder_selected_items,
    retain_visible_retry_jobs,
    waiting_blocks,
)
from unit_print_history import HistoryWriteError
from unit_print_history_events import (
    PrintHistoryMixin, block_history_data, fingerprint_pdf,
)
from unit_print_toggle_help import ToggleHoverCard
from unit_print_keyboard import (
    KeyboardInteraction, KeyboardState, KeyboardZone,
    queue_click_selection, queue_navigation_selection, queue_navigation_target, shortcut_help,
)
from unit_print_text_shortcuts import TextShortcutBindings
from unit_print_queue_render import (
    queue_card_intersects_viewport,
    queue_content_height,
    visible_queue_indices,
)
from unit_print_orders import (
    OrderCsvIssue,
    OrderCsvResult,
    OrderRow,
    load_order_csv,
    name_separator_order_key,
    parse_order_number_list,
    write_order_csv_template,
)
from unit_print_server_catalog import (
    RegisteredTestAsset,
    build_registered_test_assets,
    registered_test_asset_index,
)
from unit_print_test_catalog import (
    TEST_KIND_BY_SOURCE,
    TEST_KIND_ORDER,
    TEST_SOURCE_BY_KIND,
    TestCatalog,
    scan_default_test_catalogs,
    test_filter_choices,
    test_filter_label,
)
from unit_print_paths import DATA_ROOT as LOCAL_DATA_ROOT, select_configured_root

try:
    import ttkbootstrap
except Exception:
    ttkbootstrap = None

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except Exception:
    DND_FILES = None
    TkinterDnD = None


APP_TITLE = "빠른 인쇄기"
QUEUE_EDITOR_VARIABLE_NAMES = (
    "edit_subject_var", "edit_level_var", "edit_round_var",
    "edit_start_var", "edit_end_var", "edit_selected_numbers_var",
    "edit_name_separator_var", "edit_copies_var", "edit_bundle_var",
    "edit_printer_route_var",
)
APP_DIR = Path(__file__).resolve().parent
BUNDLED_DATA_ROOT = LOCAL_DATA_ROOT
LEGACY_DEFAULT_ROOT = Path(r"D:\테스트\단원평가")
LEGACY_BLANK_REVIEW_PATH = Path(r"D:\교재\APEX\백지 리뷰 짧.pdf")
LEGACY_OMR_PATH = Path(r"D:\테스트\OMR 내신.pdf")
BUNDLED_BLANK_REVIEW_PATH = BUNDLED_DATA_ROOT / "백지리뷰" / "백지 리뷰 짧.pdf"
BUNDLED_OMR_PATH = BUNDLED_DATA_ROOT / "OMR" / "OMR 내신.pdf"
DEFAULT_ROOT = (
    BUNDLED_DATA_ROOT / "단원평가"
    if (BUNDLED_DATA_ROOT / "단원평가").is_dir()
    else LEGACY_DEFAULT_ROOT
)
DEFAULT_PRINTER = "iR-ADV 8585 III"
DEFAULT_ENGINE = "SumatraPDF"
CONFIG_PATH = APP_DIR / "unit_print_manager_config.json"
CATALOG_PATH = APP_DIR / "unit_print_catalog.json"
SEPARATOR_PAGE_PATH = APP_DIR / "unit_print_separator_a4.pdf"
APP_ICON_PATH = APP_DIR / "output" / "unit_print_manager" / "quick_print_icon.ico"
APP_ICON_PNG_PATH = APP_DIR / "output" / "unit_print_manager" / "quick_print_icon.png"
APP_USER_MODEL_ID = "HDH.QuickPrinter"
KOREAN_KEYBOARD_LAYOUT_ID = "00000412"
KLF_ACTIVATE = 0x00000001
WM_INPUTLANGCHANGEREQUEST = 0x0050
IME_CMODE_NATIVE = 0x0001
MATERIAL_CATALOG_PATH = APP_DIR / "unit_print_material_catalog.json"
MATERIAL_RANGE_CACHE_DIR = APP_DIR / "output" / "unit_print_manager" / "material_ranges"
PRINT_LAYOUT_CACHE_DIR = APP_DIR / "output" / "unit_print_manager" / "print_layouts"

# Background order construction reuses the existing domain builders.  These
# context-local snapshots keep those builders away from live Tk variables.
_ORDER_ROOT_OVERRIDE: ContextVar[Path | None] = ContextVar(
    "quick_printer_order_root",
    default=None,
)
_ORDER_PRINTER_OVERRIDE: ContextVar[str | None] = ContextVar(
    "quick_printer_order_printer",
    default=None,
)
DEVICE_DISPATCH_SPOOL_DIR = APP_DIR / "output" / "unit_print_manager" / "device_spool"
NAME_SEPARATOR_CACHE_DIR = APP_DIR / "output" / "unit_print_manager" / "name_separators"
DEVICE_ACCEPTANCE_TIMEOUT_SECONDS = 30
DEVICE_DISPATCH_POLICY_VERSION = 2
DEVICE_IDLE_STATUS_INTERVAL_SECONDS = 30.0
TASKBAR_REFRESH_INTERVAL_MS = 30_000
MATERIAL_TAB_APEX = 0
MATERIAL_TAB_ABLE = 1
MATERIAL_TAB_SELECTED_EXAM = 2
MATERIAL_TAB_LESSON = 3
MATERIAL_TAB_GYOPYEONG = 4
MATERIAL_TAB_APEX_BOOST = 5
MATERIAL_TAB_TEXTBOOK = 6
IME_SHORTCUT_DELAY_MS = 8
IME_SHORTCUT_SETTLE_CYCLES = 1
IME_SHORTCUT_TIMEOUT_MS = 240
NAME_IME_IDLE_COMMIT_MS = 700
PRINTER_INVENTORY_TTL_SECONDS = 5 * 60


def find_blank_review_pdf() -> Path:
    """Return the portable review asset when present, then the operator copy."""
    for candidate in (BUNDLED_BLANK_REVIEW_PATH, LEGACY_BLANK_REVIEW_PATH):
        if candidate.is_file():
            return candidate
    return BUNDLED_BLANK_REVIEW_PATH


def find_omr_pdf() -> Path:
    """Return the portable OMR asset when present, then the operator copy."""
    for candidate in (BUNDLED_OMR_PATH, LEGACY_OMR_PATH):
        if candidate.is_file():
            return candidate
    return BUNDLED_OMR_PATH


ORDER_SUBJECT_ALIASES = {
    "공수1": "공통수학1",
    "공수2": "공통수학2",
    "미적1": "미적분1",
    "미적2": "미적분2",
    "확통": "확률과 통계",
}


def order_lookup_key(value: object) -> str:
    text = str(value or "").strip()
    text = ORDER_SUBJECT_ALIASES.get(text, text)
    return re.sub(r"[\s._:：-]+", "", text).casefold()


def match_order_choice(value: object, choices: tuple[str, ...] | list[str], field: str) -> str:
    wanted = order_lookup_key(value)
    for choice in choices:
        if order_lookup_key(choice) == wanted:
            return choice
    available = ", ".join(choices[:12])
    suffix = " ..." if len(choices) > 12 else ""
    raise ValueError(f"{field} '{value}'을(를) 찾을 수 없습니다. 선택 가능: {available}{suffix}")













def make_queue_bundle_block(
    blocks: list[PrintBlock], cache_dir: Path | None = None, *, base_printer: str | None = None,
) -> PrintBlock:
    """Repeat the original document sequence once per copy, without PDF merging.

    ``cache_dir`` remains accepted for existing callers; no PDF is created here.
    """
    if len(blocks) < 2:
        raise ValueError("묶을 일반 블록을 두 개 이상 선택해 주세요.")
    if any(block.bundled for block in blocks):
        raise ValueError("이미 묶음인 블록은 다시 묶을 수 없습니다.")
    if any(block_requires_omr_sheet(block) for block in blocks):
        raise ValueError("자동 OMR이 붙는 시험 블록은 이 기능으로 묶을 수 없습니다.")
    copies = {block.copies for block in blocks}
    if len(copies) != 1:
        raise ValueError("묶을 블록의 부수를 모두 같게 맞춰 주세요.")
    names = {normalize_name_separator_label(block.name_separator_label) for block in blocks}
    if len(names) != 1:
        raise ValueError("묶을 블록의 이름 구분지를 모두 같게 맞춰 주세요.")

    files = [item for block in blocks for item in block.files]
    if not files:
        raise ValueError("묶을 PDF가 없습니다.")
    settings = [
        resolve_exam_print_settings(block, item)
        for block in blocks for item in block.files
    ]
    if any(pages_per_sheet != 1 for _, pages_per_sheet in settings):
        raise ValueError("2×2 등 페이지 배치가 적용된 블록은 일반 PDF 묶음에 넣을 수 없습니다.")
    overrides = {printer for printer, _ in settings if printer}
    if overrides and any(not printer for printer, _ in settings) and not base_printer:
        raise ValueError("자동 감지와 지정 프린터를 함께 묶으려면 기본 프린터를 선택해 주세요.")
    routes = {block.printer_route for block in blocks if block.printer_route != "자동 감지"}
    if len(routes) > 1:
        raise ValueError("서로 다른 프린터 경로의 블록은 묶을 수 없습니다.")

    paths = list(dict.fromkeys(item.path for item in files))
    report = preflight_pdfs(paths)
    if not report.is_printable:
        raise ValueError(format_preflight_report(report))
    orientations = {result.orientation for result in report.results}
    if len(orientations) != 1:
        raise ValueError("가로와 세로 PDF를 한 묶음에 섞을 수 없습니다.")
    route = next(iter(routes), "자동 감지")
    orientation = next(iter(orientations))
    if (route == "세로" and orientation is PdfOrientation.LANDSCAPE) or (
        route == "가로" and orientation is PdfOrientation.PORTRAIT
    ):
        raise ValueError("PDF 방향과 프린터 경로가 다른 블록은 묶을 수 없습니다.")

    # Compare the destinations used by dispatch, including automatic routing.
    # Ignoring None here would silently redirect an automatic job to an unrelated queue.
    if base_printer:
        preflight_by_path = {result.path: result for result in report.results}
        destinations = {
            route_job_to_profile(
                base_printer,
                PrintJob(1, 1, id(block), block.summary, item, block.copies,
                         printer_override=printer, pages_per_sheet=pages_per_sheet),
                preflight_by_path,
            )
            for (block, item), (printer, pages_per_sheet) in zip(
                ((block, item) for block in blocks for item in block.files), settings,
            )
        }
    else:
        destinations = overrides
    if len(destinations) > 1:
        raise ValueError("서로 다른 프린터로 전송되는 블록은 묶을 수 없습니다.")

    title = f"선택 묶음 ({len(blocks)}개 블록)"
    ordered_files = [
        replace(item, printer_override=printer, pages_per_sheet=pages_per_sheet)
        for item, (printer, pages_per_sheet) in zip(files, settings)
    ]
    return PrintBlock(
        subject="선택 묶음", level="", round_name="직접", start=1, end=len(ordered_files),
        copies=blocks[0].copies,
        files=ordered_files,
        bundled=True, source="custom", printer_override=next(iter(overrides), None),
        display_label=title, printer_route=route,
        name_separator_label=next(iter(names)), material_kind="queue_bundle",
    )


def make_able_exam_pdf(number: int, name: str, path: Path) -> ExamPdf:
    printer_override, pages_per_sheet = able_document_print_settings(path, name)
    return ExamPdf(
        number,
        name,
        path,
        printer_override=printer_override,
        pages_per_sheet=pages_per_sheet,
    )


def run_powershell(script: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


class AppPowerShellRunner:
    def run(self, script: str) -> PowerShellResult:
        try:
            proc = run_powershell(script, timeout=30)
        except (OSError, subprocess.SubprocessError) as error:
            return PowerShellResult(1, "", str(error))
        return PowerShellResult(proc.returncode, proc.stdout, proc.stderr)


def route_job_to_profile(
    printer_name: str,
    job: PrintJob,
    preflight_by_path: dict[Path, PdfPreflightResult],
) -> str:
    if job.printer_override:
        return job.printer_override
    profiles = derive_profile_names(printer_name)
    if job.is_name_separator:
        return route_print_job(profiles, PrintRouteKind.PORTRAIT).queue_name
    if job.is_separator:
        return route_print_job(profiles, PrintRouteKind.SEPARATOR).queue_name
    result = preflight_by_path.get(job.item.path)
    if result is None:
        result = preflight_pdf(job.item.path)
        preflight_by_path[job.item.path] = result
    if not result.is_printable or result.orientation is None:
        raise ValueError(format_preflight_report(preflight_pdfs([job.item.path])))
    kind = PrintRouteKind.PORTRAIT if result.orientation is PdfOrientation.PORTRAIT else PrintRouteKind.LANDSCAPE
    return route_print_job(profiles, kind).queue_name


def activate_windows_korean_input(window_handle: int | None = None) -> bool:
    """Activate the Korean keyboard and Hangul composition for this UI thread."""
    if os.name != "nt":
        return False
    try:
        user32 = ctypes.windll.user32
        imm32 = ctypes.windll.imm32
        user32.LoadKeyboardLayoutW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
        user32.LoadKeyboardLayoutW.restype = ctypes.c_void_p
        user32.ActivateKeyboardLayout.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user32.ActivateKeyboardLayout.restype = ctypes.c_void_p
        user32.GetFocus.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
        ]
        user32.SendMessageW.restype = ctypes.c_ssize_t
        imm32.ImmGetContext.argtypes = [ctypes.c_void_p]
        imm32.ImmGetContext.restype = ctypes.c_void_p
        imm32.ImmSetOpenStatus.argtypes = [ctypes.c_void_p, ctypes.c_int]
        imm32.ImmSetOpenStatus.restype = ctypes.c_int
        imm32.ImmGetConversionStatus.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_uint),
        ]
        imm32.ImmGetConversionStatus.restype = ctypes.c_int
        imm32.ImmSetConversionStatus.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_uint,
        ]
        imm32.ImmSetConversionStatus.restype = ctypes.c_int
        imm32.ImmReleaseContext.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        imm32.ImmReleaseContext.restype = ctypes.c_int

        keyboard_layout = user32.LoadKeyboardLayoutW(
            KOREAN_KEYBOARD_LAYOUT_ID,
            KLF_ACTIVATE,
        )
        if not keyboard_layout:
            return False
        user32.ActivateKeyboardLayout(keyboard_layout, 0)

        target_window = window_handle or user32.GetFocus()
        if not target_window:
            return True
        user32.SendMessageW(
            target_window,
            WM_INPUTLANGCHANGEREQUEST,
            0,
            int(keyboard_layout),
        )
        input_context = imm32.ImmGetContext(target_window)
        if not input_context:
            return True
        try:
            imm32.ImmSetOpenStatus(input_context, True)
            conversion_mode = ctypes.c_uint()
            sentence_mode = ctypes.c_uint()
            if imm32.ImmGetConversionStatus(
                input_context,
                ctypes.byref(conversion_mode),
                ctypes.byref(sentence_mode),
            ):
                imm32.ImmSetConversionStatus(
                    input_context,
                    conversion_mode.value | IME_CMODE_NATIVE,
                    sentence_mode.value,
                )
        finally:
            imm32.ImmReleaseContext(target_window, input_context)
        return True
    except Exception:
        return False


def complete_windows_ime_composition(window_handle: int | None = None) -> bool:
    """Commit the active Windows IME preedit string and report whether one existed."""
    if os.name != "nt":
        return False
    try:
        user32 = ctypes.windll.user32
        imm32 = ctypes.windll.imm32
        user32.GetFocus.restype = ctypes.c_void_p
        imm32.ImmGetContext.argtypes = [ctypes.c_void_p]
        imm32.ImmGetContext.restype = ctypes.c_void_p
        imm32.ImmNotifyIME.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
        ]
        imm32.ImmNotifyIME.restype = ctypes.c_int
        imm32.ImmGetCompositionStringW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_uint,
        ]
        imm32.ImmGetCompositionStringW.restype = ctypes.c_long
        imm32.ImmReleaseContext.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        imm32.ImmReleaseContext.restype = ctypes.c_int
        focused_window = window_handle or user32.GetFocus()
        if not focused_window:
            return False
        input_context = imm32.ImmGetContext(focused_window)
        if not input_context:
            return False
        try:
            composition_length = imm32.ImmGetCompositionStringW(
                input_context,
                0x0008,  # GCS_COMPSTR
                None,
                0,
            )
            # NI_COMPOSITIONSTR + CPS_COMPLETE converts the current Korean
            # composition string into the result string without cancelling it.
            imm32.ImmNotifyIME(input_context, 0x0015, 0x0001, 0)
            return composition_length > 0
        finally:
            imm32.ImmReleaseContext(focused_window, input_context)
    except Exception:
        return False


def take_windows_ime_composition(window_handle: int | None = None) -> str:
    """Remove and return the active Windows IME preedit string.

    ``CPS_COMPLETE`` publishes the result through a later Windows message.  A
    Ctrl+Enter handler can run before Tk consumes that message, so shortcut
    paths use this synchronous take-and-insert variant instead.
    """
    if os.name != "nt":
        return ""
    try:
        user32 = ctypes.windll.user32
        imm32 = ctypes.windll.imm32
        user32.GetFocus.restype = ctypes.c_void_p
        imm32.ImmGetContext.argtypes = [ctypes.c_void_p]
        imm32.ImmGetContext.restype = ctypes.c_void_p
        imm32.ImmGetCompositionStringW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_uint,
        ]
        imm32.ImmGetCompositionStringW.restype = ctypes.c_long
        imm32.ImmNotifyIME.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
        ]
        imm32.ImmNotifyIME.restype = ctypes.c_int
        imm32.ImmReleaseContext.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        imm32.ImmReleaseContext.restype = ctypes.c_int

        focused_window = user32.GetFocus()
        candidates: list[int] = []
        for candidate in (window_handle, focused_window):
            if candidate and int(candidate) not in candidates:
                candidates.append(int(candidate))

        for target_window in candidates:
            input_context = imm32.ImmGetContext(target_window)
            if not input_context:
                continue
            try:
                byte_length = imm32.ImmGetCompositionStringW(
                    input_context,
                    0x0008,  # GCS_COMPSTR
                    None,
                    0,
                )
                if byte_length <= 0:
                    continue
                buffer = ctypes.create_string_buffer(byte_length + 2)
                copied = imm32.ImmGetCompositionStringW(
                    input_context,
                    0x0008,  # GCS_COMPSTR
                    buffer,
                    byte_length,
                )
                if copied <= 0:
                    continue
                composition = bytes(buffer.raw[:copied]).decode("utf-16-le")

                cancelled = bool(
                    imm32.ImmNotifyIME(
                        input_context,
                        0x0015,  # NI_COMPOSITIONSTR
                        0x0004,  # CPS_CANCEL
                        0,
                    )
                )
                remaining = imm32.ImmGetCompositionStringW(
                    input_context,
                    0x0008,  # GCS_COMPSTR
                    None,
                    0,
                )
                if not cancelled and remaining > 0:
                    continue
                return composition
            finally:
                imm32.ImmReleaseContext(target_window, input_context)
        return ""
    except Exception:
        return ""


def windows_shortcut_keys_pressed() -> bool:
    """Return True while Ctrl or Enter is physically held on Windows."""
    if os.name != "nt":
        return False
    try:
        get_key_state = ctypes.windll.user32.GetAsyncKeyState
        get_key_state.argtypes = [ctypes.c_int]
        get_key_state.restype = ctypes.c_short
        return any(get_key_state(key_code) & 0x8000 for key_code in (0x11, 0x0D))
    except Exception:
        return False


class _LogFontW(ctypes.Structure):
    _fields_ = [
        ("lfHeight", ctypes.c_long),
        ("lfWidth", ctypes.c_long),
        ("lfEscapement", ctypes.c_long),
        ("lfOrientation", ctypes.c_long),
        ("lfWeight", ctypes.c_long),
        ("lfItalic", ctypes.c_ubyte),
        ("lfUnderline", ctypes.c_ubyte),
        ("lfStrikeOut", ctypes.c_ubyte),
        ("lfCharSet", ctypes.c_ubyte),
        ("lfOutPrecision", ctypes.c_ubyte),
        ("lfClipPrecision", ctypes.c_ubyte),
        ("lfQuality", ctypes.c_ubyte),
        ("lfPitchAndFamily", ctypes.c_ubyte),
        ("lfFaceName", ctypes.c_wchar * 32),
    ]


def set_windows_ime_composition_font(
    window_handle: int,
    family: str,
    point_size: int,
) -> bool:
    """Match the native IME composition font to the Tk entry font."""
    if os.name != "nt" or not window_handle:
        return False
    try:
        user32 = ctypes.windll.user32
        imm32 = ctypes.windll.imm32
        imm32.ImmGetContext.argtypes = [ctypes.c_void_p]
        imm32.ImmGetContext.restype = ctypes.c_void_p
        imm32.ImmSetCompositionFontW.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_LogFontW),
        ]
        imm32.ImmSetCompositionFontW.restype = ctypes.c_int
        imm32.ImmReleaseContext.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        imm32.ImmReleaseContext.restype = ctypes.c_int
        try:
            user32.GetDpiForWindow.argtypes = [ctypes.c_void_p]
            user32.GetDpiForWindow.restype = ctypes.c_uint
            dpi = int(user32.GetDpiForWindow(window_handle)) or 96
        except Exception:
            dpi = 96
        input_context = imm32.ImmGetContext(window_handle)
        if not input_context:
            return False
        try:
            logical_font = _LogFontW()
            logical_font.lfHeight = -max(1, round(point_size * dpi / 72))
            logical_font.lfWeight = 400
            logical_font.lfCharSet = 129  # HANGEUL_CHARSET
            logical_font.lfQuality = 5  # CLEARTYPE_QUALITY
            logical_font.lfPitchAndFamily = 0x22  # VARIABLE_PITCH | FF_SWISS
            logical_font.lfFaceName = family[:31]
            return bool(
                imm32.ImmSetCompositionFontW(
                    input_context,
                    ctypes.byref(logical_font),
                )
            )
        finally:
            imm32.ImmReleaseContext(window_handle, input_context)
    except Exception:
        return False


def set_windows_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def set_windows_app_user_model_id() -> None:
    """Keep the running window grouped under the quick-printer identity."""
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        pass


def ps_single_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def get_print_queue_count(printer_name: str) -> int:
    script = (
        "$ErrorActionPreference = 'Stop'; "
        f"$jobs = @(Get-PrintJob -PrinterName {ps_single_quote(printer_name)}); "
        "$jobs.Count"
    )
    proc = run_powershell(script, timeout=20)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"프린터 대기열 확인 실패: {detail}")
    for line in reversed(proc.stdout.splitlines()):
        line = line.strip()
        if line:
            return int(line)
    return 0


def load_config(*, startup_report=None) -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeError):
        from unit_print_bootstrap import show_notice
        backup = CONFIG_PATH.with_name(
            f"{CONFIG_PATH.stem}.corrupt-{datetime.now():%Y%m%d_%H%M%S_%f}.json"
        )
        try:
            shutil.copy2(CONFIG_PATH, backup)
        except OSError:
            if startup_report is not None:
                startup_report("config_corrupt_backup_failed")
            show_notice("설정 파일이 손상되었고 백업에 실패했습니다.\n설정 파일과 폴더 권한을 확인해 주세요.", error=True)
            # Do not allow a later defaults save to overwrite the only copy.
            raise
        if startup_report is not None:
            startup_report("config_corrupt_backed_up")
        show_notice(
            f"설정 파일이 손상되어 기본 설정으로 시작합니다.\n손상 파일 백업: {backup.name}\n프린터와 장비 연결 설정을 다시 확인해 주세요."
        )
        return {}


def save_config(config: dict) -> None:
    content = json.dumps(config, ensure_ascii=False, indent=2)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix=".config-", suffix=".tmp",
            dir=CONFIG_PATH.parent, delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, CONFIG_PATH)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def ensure_separator_pdf() -> Path:
    if not SEPARATOR_PAGE_PATH.is_file():
        raise FileNotFoundError(f"고정 구분지 PDF를 찾을 수 없습니다: {SEPARATOR_PAGE_PATH}")
    result = preflight_pdf(SEPARATOR_PAGE_PATH)
    page = None
    if result.is_printable:
        reader = PdfReader(str(SEPARATOR_PAGE_PATH))
        page = reader.pages[0] if len(reader.pages) == 1 else None
    is_a4 = page is not None and (
        abs(float(page.mediabox.width) - A4_HEIGHT_POINTS) < 1
        and abs(float(page.mediabox.height) - A4_WIDTH_POINTS) < 1
    )
    has_separator_label = page is not None and (page.extract_text() or "").strip() == "구분지"
    if (
        not result.is_printable
        or len(result.pages) != 1
        or result.orientation is not PdfOrientation.LANDSCAPE
        or not is_a4
        or not has_separator_label
    ):
        raise RuntimeError("고정 구분지 PDF는 중앙에 '구분지'가 표시된 A4 가로 1쪽이어야 합니다.")
    return SEPARATOR_PAGE_PATH


def natural_key(text: str) -> tuple:
    parts = re.split(r"(\d+)", text.casefold())
    return tuple(int(part) if part.isdigit() else part for part in parts)


def parse_exam_number(filename: str) -> int | None:
    match = re.match(r"^\s*0*(\d+)\s*[.]", filename)
    if match:
        return int(match.group(1))
    match = re.match(r"^\s*0*(\d+)\s+", filename)
    if match:
        return int(match.group(1))
    return None


def scan_subjects(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted([p.name for p in root.iterdir() if p.is_dir()], key=natural_key)


def scan_levels(root: Path, subject: str) -> list[str]:
    subject_dir = root / subject
    if not subject_dir.exists():
        return []
    return sorted([p.name for p in subject_dir.iterdir() if p.is_dir()], key=natural_key)


def scan_rounds(root: Path, subject: str, level: str) -> list[str]:
    level_dir = root / subject / level
    if not level_dir.exists():
        return []
    return sorted([p.name for p in level_dir.iterdir() if p.is_dir()], key=natural_key)


def scan_exam_files(root: Path, subject: str, level: str, round_name: str) -> list[ExamPdf]:
    round_dir = root / subject / level / round_name
    if not round_dir.exists():
        return []

    files: list[ExamPdf] = []
    for path in round_dir.glob("*.pdf"):
        if path.parent.name == "해설" or "_해설" in path.stem:
            continue
        number = parse_exam_number(path.name)
        if number is None:
            continue
        title = re.sub(r"^\s*0*\d+\s*[.]\s*", "", path.stem)
        files.append(ExamPdf(number=number, name=title, path=path))
    return sorted(files, key=lambda item: (item.number, natural_key(item.name)))


def load_unit_catalog() -> dict:
    if not CATALOG_PATH.exists():
        return {}
    try:
        return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def count_catalog_files(catalog: dict) -> int:
    total = 0
    for subject in catalog.get("subjects", []):
        for level in subject.get("levels", []):
            for round_info in level.get("rounds", []):
                total += len(round_info.get("files", []))
    return total


def catalog_subjects(catalog: dict) -> list[str]:
    return [item["name"] for item in catalog.get("subjects", [])]


def find_named(items: list[dict], name: str) -> dict | None:
    for item in items:
        if item.get("name") == name:
            return item
    return None


def catalog_levels(catalog: dict, subject: str) -> list[str]:
    subject_info = find_named(catalog.get("subjects", []), subject)
    if not subject_info:
        return []
    return [item["name"] for item in subject_info.get("levels", [])]


def catalog_rounds(catalog: dict, subject: str, level: str) -> list[str]:
    subject_info = find_named(catalog.get("subjects", []), subject)
    if not subject_info:
        return []
    level_info = find_named(subject_info.get("levels", []), level)
    if not level_info:
        return []
    return [item["name"] for item in level_info.get("rounds", [])]


def catalog_exam_files(
    catalog: dict,
    root: Path,
    subject: str,
    level: str,
    round_name: str,
) -> list[ExamPdf]:
    subject_info = find_named(catalog.get("subjects", []), subject)
    if not subject_info:
        return []
    level_info = find_named(subject_info.get("levels", []), level)
    if not level_info:
        return []
    round_info = find_named(level_info.get("rounds", []), round_name)
    if not round_info:
        return []

    files: list[ExamPdf] = []
    for item in round_info.get("files", []):
        rel_path = item.get("relative_path", "")
        if not rel_path:
            continue
        files.append(
            ExamPdf(
                number=int(item["number"]),
                name=str(item["name"]),
                path=root / Path(rel_path),
            )
        )
    return sorted(files, key=lambda item: (item.number, natural_key(item.name)))


def parse_choice_number(value: str) -> int:
    match = re.match(r"^\s*0*(\d+)", value)
    if not match:
        raise ValueError("시작/끝 번호를 선택해 주세요.")
    return int(match.group(1))


def parse_file_number_list(value: str) -> tuple[int, ...]:
    """Parse file numbers and inclusive ranges while preserving input order."""
    return parse_order_number_list(value)


def compact_text(value: str, limit: int = 44) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "…"


def compact_text_pixels(value: str, max_width: float, font: tkfont.Font) -> str:
    if font.measure(value) <= max_width:
        return value
    ellipsis = "…"
    if max_width <= font.measure(ellipsis):
        return ellipsis
    low = 0
    high = len(value)
    while low < high:
        mid = (low + high + 1) // 2
        if font.measure(value[:mid] + ellipsis) <= max_width:
            low = mid
        else:
            high = mid - 1
    return value[:low] + ellipsis


def ease_out_cubic(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return 1 - pow(1 - value, 3)


def find_sumatra_pdf(configured_path: str = "") -> str:
    candidates = []
    if configured_path:
        candidates.append(Path(configured_path))
    for env_name in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
        base = os.environ.get(env_name)
        if base:
            candidates.append(Path(base) / "SumatraPDF" / "SumatraPDF.exe")
    candidates.append(Path(__file__).with_name("SumatraPDF.exe"))

    on_path = shutil.which("SumatraPDF.exe") or shutil.which("SumatraPDF")
    if on_path:
        candidates.insert(0, Path(on_path))

    for candidate in candidates:
        if candidate and candidate.exists():
            return str(candidate)
    return ""


def load_printers() -> list[str]:
    try:
        proc = run_powershell(
            "Get-Printer | Select-Object -ExpandProperty Name | ConvertTo-Json -Compress",
            timeout=15,
        )
        if proc.returncode != 0:
            return [DEFAULT_PRINTER]
        data = json.loads(proc.stdout or "[]")
        if isinstance(data, str):
            data = [data]
        printers = [str(name) for name in data if str(name).strip()]
        if DEFAULT_PRINTER not in printers:
            printers.insert(0, DEFAULT_PRINTER)
        return printers
    except Exception:
        return [DEFAULT_PRINTER]


def shell_print_pdf(pdf_path: Path, printer_name: str) -> None:
    result = ctypes.windll.shell32.ShellExecuteW(
        None,
        "printto",
        str(pdf_path),
        f'"{printer_name}"',
        None,
        0,
    )
    if result <= 32:
        raise RuntimeError(f"Windows printto failed with code {result}: {pdf_path}")


def sumatra_print_pdf(
    sumatra_path: str,
    pdf_path: Path,
    printer_name: str,
    copies: int,
    duplex: bool,
    timeout: int = 180,
) -> None:
    sumatra_enqueue_pdfs(sumatra_path, [pdf_path], printer_name, copies, duplex, timeout=timeout)


def sumatra_enqueue_pdf(
    sumatra_path: str,
    pdf_path: Path,
    printer_name: str,
    copies: int,
    duplex: bool,
) -> None:
    sumatra_enqueue_pdfs(sumatra_path, [pdf_path], printer_name, copies, duplex)


def sumatra_enqueue_pdfs(
    sumatra_path: str,
    pdf_paths: list[Path],
    printer_name: str,
    copies: int,
    duplex: bool,
    timeout: int | None = None,
) -> int:
    if not pdf_paths:
        return 0
    if timeout is None:
        timeout = max(300, len(pdf_paths) * 60)
    call_count = 0
    for batch in sumatra_pdf_batches(pdf_paths, copies, duplex):
        command = [
            sumatra_path,
            "-silent",
            "-print-to",
            printer_name,
            "-print-settings",
            batch.settings_text,
        ]
        command.extend(str(path) for path in batch.paths)
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(detail or f"SumatraPDF exited with {proc.returncode}")
        call_count += 1
    return call_count


class PrintManagerApp(PrintHistoryMixin, PrintExecutionMixin):
    def print_execution_services(self) -> PrintExecutionServices:
        # The application boundary keeps platform functions replaceable for
        # dry-run/production-worker tests without importing the GUI in workers.
        return PrintExecutionServices(
            app_title=APP_TITLE,
            layout_cache_dir=PRINT_LAYOUT_CACHE_DIR,
            name_separator_cache_dir=NAME_SEPARATOR_CACHE_DIR,
            dispatch_spool_dir=DEVICE_DISPATCH_SPOOL_DIR,
            device_acceptance_timeout_seconds=DEVICE_ACCEPTANCE_TIMEOUT_SECONDS,
            preparation_factory=PrintPreparationService,
            materialize_four_up_pdf=materialize_four_up_pdf,
            materialize_name_separator_pdf=materialize_name_separator_pdf,
            ensure_separator_pdf=ensure_separator_pdf,
            get_print_queue_count=get_print_queue_count,
            fingerprint_pdf=fingerprint_pdf,
            sumatra_enqueue_pdfs=sumatra_enqueue_pdfs,
            get_pdf_page_count=get_pdf_page_count,
            new_dispatch_token=new_dispatch_token,
            prepare_dispatch_copy=prepare_dispatch_copy,
            remove_dispatch_copy=remove_dispatch_copy,
        )

    def __init__(self, root: Tk) -> None:
        self.root = root
        self.config = load_config(startup_report=getattr(root, "startup_report", None))
        self.catalog = load_unit_catalog()
        self.test_catalogs: dict[str, TestCatalog] = scan_default_test_catalogs()
        self.apex_material_catalog = load_apex_catalog(DEFAULT_APEX_ROOT, MATERIAL_CATALOG_PATH)
        self.able_material_catalog = scan_able_catalog(DEFAULT_ABLE_ROOT)
        self.training_pack_material_catalog = scan_training_pack_catalog(DEFAULT_TRAINING_PACK_ROOT)
        self.frequent_type_material_catalog = scan_frequent_type_catalog(DEFAULT_FREQUENT_TYPE_ROOT)
        self.gyopyeong_material_catalog = scan_gyopyeong_catalog(DEFAULT_GYOPYEONG_ROOT)
        self.selected_exam_material_catalog = scan_selected_exam_catalog(
            DEFAULT_SELECTED_EXAM_ROOT
        )
        self.textbook_catalog = load_textbook_catalog()
        self.exam_files: list[ExamPdf] = []
        self.blocks: list[PrintBlock] = []
        self.selected_block_index_value: int | None = None
        self.selected_block_indices: set[int] = set()
        self.block_clipboard: tuple[PrintBlock, ...] = ()
        self.queue_selection_anchor: int | None = None
        self.drag_start_index: int | None = None
        self.drag_selected_indices: tuple[int, ...] = ()
        self.drag_drop_index: int | None = None
        self.drag_origin_y: float = 0
        self.drag_pointer_y: float | None = None
        self.drag_pointer_offset_y: float = 0
        self.drag_active: bool = False
        self.drag_lift_progress: float = 0
        self.drag_lift_started_at: float = 0
        self.drag_lift_after_id: str | None = None
        self.drop_animation_after_id: str | None = None
        self.compact_layout_active: bool | None = None
        self.drop_animation_started_at: float = 0
        self.drop_animation_progress: float = 0
        self.drop_animation_positions: dict[int, tuple[float, float]] = {}
        self.drop_animation_duration = 0.18
        self.queue_card_height = 62
        self.queue_card_gap = 8
        self.queue_padding = 8
        self.queue_header_height = 30
        self.left_panel_width = 540
        self.queue_panel_width = 520
        self.queue_status_pixel_width = 160
        self.colors = dict(WORKBENCH_COLORS)
        self.ui_font_family = self.resolve_ui_font_family()
        self.queue_line_font = tkfont.Font(family=self.ui_font_family, size=11)
        self.queue_line_bold_font = tkfont.Font(family=self.ui_font_family, size=11, weight="bold")
        self.queue_meta_font = tkfont.Font(family=self.ui_font_family, size=10)
        self.queue_status_font = tkfont.Font(family=self.ui_font_family, size=10, weight="bold")
        self.is_printing = False
        self.is_closing = False
        self.pdf_work_scheduler = PriorityWorkScheduler()
        self.resource_estimate_scheduler = PriorityWorkScheduler(
            thread_name="quick-print-resource"
        )
        self.background_build_service = BackgroundBuildService(self.pdf_work_scheduler)
        self.block_commit_queue = OrderedCommitQueue()
        self.block_preparation_epoch = 0
        self.background_build_results: queue.SimpleQueue[
            tuple[int, Callable[[object], None], object]
        ] = queue.SimpleQueue()
        self.background_build_poll_after_id: str | None = None
        self.resource_estimate_revision = 0
        self.resource_estimate_future: object | None = None
        self.resource_estimate_results: queue.SimpleQueue[
            tuple[int, object]
        ] = queue.SimpleQueue()
        self.resource_estimate_poll_after_id: str | None = None
        self.latest_resource_estimate: PrintResourceEstimate | None = None
        self.latest_resource_estimate_key: object | None = None
        self.resource_completion_projection: CompletionTimeProjection | None = None
        self.resource_completion_key: object | None = None
        self.resource_completion_override: str | None = None
        self.resource_completion_run_key: object | None = None
        self.active_resource_eta: PrintEtaObservation | None = None
        self.able_build_pending = False
        self.order_build_pending = False
        self.group_build_pending = False
        self.recovery_load_pending = False
        self.recovery_poll_after_id: str | None = None
        self.print_state_lock = threading.Lock()
        self.print_cancel_event = threading.Event()
        self.device_resume_event = threading.Event()
        self.device_resume_event.set()
        self.device_hold_active = False
        self.device_hold_message = ""
        self.device_monitor: PrinterDeviceMonitor | None = None
        configured_device_monitor = self.config.get("device_monitor", {})
        self.device_monitor_config: dict[str, object] = (
            dict(configured_device_monitor) if isinstance(configured_device_monitor, dict) else {}
        )
        initialize_timing_samples(
            self.device_monitor_config,
            APP_DIR / "output" / "unit_print_manager" / "cache" / "eta_job_timing_bootstrap.json",
        )
        try:
            dispatch_policy_version = int(
                self.device_monitor_config.get("dispatch_policy_version", 1)
            )
            stored_window_size = int(
                self.device_monitor_config.get("dispatch_window_size", 3)
            )
        except (TypeError, ValueError):
            dispatch_policy_version = 1
            stored_window_size = 3
        if dispatch_policy_version < DEVICE_DISPATCH_POLICY_VERSION:
            # Version 1 shipped with 3 as its default.  Migrate only that old
            # default; deliberate 1/2/4/5 operator choices remain intact.
            if stored_window_size == 3:
                self.device_monitor_config["dispatch_window_size"] = 6
            self.device_monitor_config[
                "dispatch_policy_version"
            ] = DEVICE_DISPATCH_POLICY_VERSION
        self.print_thread: threading.Thread | None = None
        self.print_run_options: PrintRunOptions | None = None
        self.print_pending_jobs: list[PrintJob] = []
        self.retry_batch: RetryBatch[PrintJob] | None = None
        self.pdf_preflight_by_path: dict[Path, PdfPreflightResult] = {}
        self.pdf_preflight_signature_by_path: dict[Path, tuple[int, int] | None] = {}
        self.preview_preflight_lock = threading.Lock()
        self.preview_preflight_pending_paths: set[Path] = set()
        self.preview_preflight_active = False
        self.preview_preflight_completed: tuple[
            PdfPreflightReport | Exception,
            dict[Path, tuple[int, int] | None],
        ] | None = None
        self.preview_preflight_poll_after_id: str | None = None
        self.printer_inventory_lock = threading.Lock()
        self.printer_inventory_cache: frozenset[str] | None = None
        self.printer_inventory_cached_at = 0.0
        self.printer_inventory_warming = False
        self.print_scheduled_block_ids: set[int] = set()
        self.print_started_block_ids: set[int] = set()
        self.print_next_block_index = 1
        self.print_include_separator_pages = False
        self.print_include_name_separator_pages = False
        self.print_last_name_separator_label = ""
        self.print_queue_revision = 0
        self.print_accepting_appends = False
        self.block_status_by_id: dict[int, str] = {}
        self.block_detail_by_id: dict[int, str] = {}
        self.active_print_block_uid: int | None = None
        self.undo_stack: list[tuple[str, QueueSnapshot]] = []
        self.redo_stack: list[tuple[str, QueueSnapshot]] = []
        self.keyboard_state = KeyboardState()
        self.keyboard_latches: set[str] = set()
        self.editor_syncing = False
        self.editor_block: PrintBlock | None = None
        self.editor_baseline: tuple[str, ...] | None = None
        self.editor_save_in_progress = False

        root.title(APP_TITLE)
        root.geometry("1280x820")
        root.minsize(1280, 720)
        root.configure(bg=self.colors["app_bg"])
        self.apply_app_icon()
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        initial_root = select_configured_root(
            self.config.get("root_dir"),
            portable_root=BUNDLED_DATA_ROOT / "단원평가",
            legacy_root=LEGACY_DEFAULT_ROOT,
        )
        self.root_dir_var = StringVar(value=str(initial_root))
        self.printer_var = StringVar(value=self.config.get("printer", DEFAULT_PRINTER))
        self.sumatra_var = StringVar(value=find_sumatra_pdf(self.config.get("sumatra_path", "")))
        self.subject_var = StringVar()
        self.level_var = StringVar()
        self.round_var = StringVar()
        self.start_var = StringVar()
        self.end_var = StringVar()
        self.unit_selected_numbers_var = StringVar()
        self.block_copies_var = IntVar(value=1)
        self.bundle_var = BooleanVar(value=False)
        self.new_block_name_var = StringVar()
        self.new_block_name_entries: list[ttk.Entry] = []
        self.name_ime_after_ids: dict[str, str] = {}
        self.custom_pdf_files: list[Path] = []
        self.custom_pdf_status_var = StringVar(value="PDF 파일을 여기로 드래그하거나 [파일 선택]을 누르세요.")
        self.custom_copies_var = IntVar(value=1)
        self.custom_bundle_var = BooleanVar(value=bool(self.config.get("custom_bundle", False)))
        configured_custom_route = str(self.config.get("custom_printer_route", "자동 감지"))
        if configured_custom_route not in CUSTOM_PRINTER_ROUTE_CHOICES:
            configured_custom_route = "자동 감지"
        self.custom_printer_route_var = StringVar(value=configured_custom_route)
        self.blank_review_copies_var = IntVar(value=1)
        self.blank_review_status_var = StringVar(value="")
        self.omr_copies_var = IntVar(value=1)
        self.omr_status_var = StringVar(value="")
        self.order_csv_path: Path | None = None
        self.order_csv_result = OrderCsvResult((), (), "")
        self.order_csv_status_var = StringVar(value="CSV 주문서를 선택해 주세요.")
        self.apex_variant_var = StringVar(value="APEX")
        self.apex_subject_var = StringVar()
        self.apex_document_kind_var = StringVar(value="교재")
        self.apex_part_var = StringVar(value="차시 별")
        self.apex_start_var = StringVar()
        self.apex_end_var = StringVar()
        self.apex_selected_numbers_var = StringVar()
        self.apex_copies_var = IntVar(value=1)
        self.apex_status_var = StringVar(value="APEX 자료를 선택하세요.")
        self.able_subject_var = StringVar()
        self.able_document_kind_var = StringVar(value="교재 + 백지리뷰")
        self.able_mode_var = StringVar(value="차시별")
        self.able_start_var = StringVar()
        self.able_end_var = StringVar()
        self.able_selected_numbers_var = StringVar()
        self.able_copies_var = IntVar(value=1)
        self.able_status_var = StringVar(value="A:ble 자료를 선택하세요.")
        self.training_pack_subject_var = StringVar()
        self.training_pack_mode_var = StringVar(value="차시별")
        self.training_pack_start_var = StringVar()
        self.training_pack_end_var = StringVar()
        self.training_pack_selected_numbers_var = StringVar()
        self.training_pack_copies_var = IntVar(value=1)
        self.training_pack_status_var = StringVar(value="트레이닝팩 자료를 선택하세요.")
        self.frequent_type_subject_var = StringVar()
        self.frequent_type_mode_var = StringVar(value="차시별")
        self.frequent_type_start_var = StringVar()
        self.frequent_type_end_var = StringVar()
        self.frequent_type_selected_numbers_var = StringVar()
        self.frequent_type_copies_var = IntVar(value=1)
        self.frequent_type_status_var = StringVar(value="빈출유형 자료를 선택하세요.")
        self.gyopyeong_subject_var = StringVar()
        self.gyopyeong_part_var = StringVar()
        self.gyopyeong_lesson_var = StringVar()
        self.gyopyeong_copies_var = IntVar(value=1)
        self.gyopyeong_status_var = StringVar(value="교평 기출 자료를 선택하세요.")
        self.selected_exam_subject_var = StringVar()
        self.selected_exam_lesson_var = StringVar()
        self.selected_exam_selected_numbers_var = StringVar()
        self.selected_exam_copies_var = IntVar(value=1)
        self.selected_exam_status_var = StringVar(value="기출엄선 자료를 선택하세요.")
        self.textbook_subject_var = StringVar()
        self.textbook_publisher_var = StringVar()
        self.textbook_document_kind_var = StringVar(value="문제")
        self.textbook_start_var = StringVar()
        self.textbook_end_var = StringVar()
        self.textbook_selected_numbers_var = StringVar()
        self.textbook_copies_var = IntVar(value=1)
        self.textbook_status_var = StringVar(value="교과서 페이지 매핑을 선택하세요.")
        self.textbook_variant_subject_var = StringVar()
        self.textbook_variant_publisher_var = StringVar()
        self.textbook_variant_start_var = StringVar()
        self.textbook_variant_end_var = StringVar()
        self.textbook_variant_selected_numbers_var = StringVar()
        self.textbook_variant_copies_var = IntVar(value=1)
        self.textbook_variant_status_var = StringVar(value="교과서 단원을 선택하세요.")
        self.test_builder_vars: dict[str, dict[str, tk.Variable]] = {}
        self.test_builder_controls: dict[str, dict[str, tk.Misc]] = {}
        self.test_exam_files: dict[str, list[ExamPdf]] = {
            kind: [] for kind in TEST_KIND_ORDER
        }
        self.edit_subject_var = StringVar()
        self.edit_level_var = StringVar()
        self.edit_round_var = StringVar()
        self.edit_start_var = StringVar()
        self.edit_end_var = StringVar()
        self.edit_selected_numbers_var = StringVar()
        self.edit_name_separator_var = StringVar()
        self.edit_copies_var = IntVar(value=1)
        self.edit_bundle_var = BooleanVar(value=False)
        self.edit_printer_route_var = StringVar(value="자동 감지")
        self.edit_exam_files: list[ExamPdf] = []
        self.queue_batch_size_var = IntVar(value=int(self.config.get("queue_batch_size", 12)))
        self.queue_refill_threshold_var = IntVar(value=int(self.config.get("queue_refill_threshold", 6)))
        self.separator_page_var = BooleanVar(value=bool(self.config.get("separator_page", False)))
        self.name_separator_page_var = BooleanVar(
            value=bool(self.config.get("name_separator_page", False))
        )
        self.duplex_var = BooleanVar(value=True)
        self.dry_run_var = BooleanVar(value=False)
        configured_engine = self.config.get("engine", DEFAULT_ENGINE)
        if configured_engine == "자동":
            configured_engine = DEFAULT_ENGINE
        self.engine_var = StringVar(value=configured_engine)
        self.builder_status_var = StringVar(value="")
        self.queue_status_var = StringVar(value="")
        self.app_status_var = StringVar(value="대기 중")
        self.focus_context_var = StringVar(value="현재 작업 영역  교재 선택")
        self.shortcut_help_context_var = StringVar(value="교재 선택 · 단원평가")
        self.shortcut_help_var = StringVar(
            value=(
                "←→  같은 행 이동\n"
                "↑↓  다음 행 이동\n"
                "Enter  드롭다운 열기/실행\n"
                "Ctrl+Enter  블록 추가\n"
                "Ctrl+Tab  자료 탭 이동\n"
                "Tab  작업 큐 이동"
            )
        )
        self.current_work_var = StringVar(value="현재 작업: 대기")
        self.device_status_var = StringVar(value="프린터 상태: 연결 설정 필요")
        self.resource_estimate_var = StringVar(value="예상 자원  작업 큐 없음")
        self.resource_estimate_full_text = "예상 자원  작업 큐 없음"
        self.resource_estimate_tone = "secondary"
        self.device_status_health: DeviceHealth | None = None
        self.device_status_observed_at: datetime | None = None
        self.last_device_notification_health: DeviceHealth | None = None
        self.device_fault_notification_active = False
        self.taskbar_indicator: object | None = None
        self.taskbar_refresh_after_id: str | None = None
        self.taskbar_display_state: tuple[str, int, int] = ("idle", 0, 1)
        self.print_progress_state = PrintProgressState()
        self.taskbar_completion_pending = False
        self.notification_gate = NotificationGate()
        self.windows_notifier: WindowsBalloonNotifier | None = None
        self.windows_notification_cleanup_after_id: str | None = None
        self.persistent_alert_window: tk.Toplevel | None = None
        self.persistent_alert_key: str | None = None
        self.persistent_alert_dismissed_key: str | None = None
        self.persistent_alert_title_var: StringVar | None = None
        self.persistent_alert_message_var: StringVar | None = None
        self.persistent_alert_accent_bar: tk.Frame | None = None
        self.persistent_alert_resume_button: ttk.Button | None = None
        self.persistent_alert_confirm_button: ttk.Button | None = None
        self.persistent_alert_allow_resume = False
        self.readiness_status_var = StringVar(value="프린터 드라이버의 스테이플 설정을 확인하세요.")
        self.profile_status_var = StringVar(value="방향 프로필: 설정 필요")
        self.toggle_buttons: list[tuple[tk.Button, str, BooleanVar]] = []
        self.toggle_help_cards: list[ToggleHoverCard] = []
        self.status_badge: tk.Label | None = None
        self.device_status_badge: tk.Label | None = None
        self.resource_estimate_badge: tk.Label | None = None
        self.device_resume_button: ttk.Button | None = None
        self.focus_context_badge: tk.Label | None = None
        self.active_workspace = "builder"
        self.material_dropdown_widget: ttk.Combobox | None = None
        for variable in (
            self.edit_subject_var,
            self.edit_level_var,
            self.edit_round_var,
            self.edit_start_var,
            self.edit_end_var,
            self.edit_selected_numbers_var,
            self.edit_name_separator_var,
            self.edit_copies_var,
            self.edit_bundle_var,
            self.edit_printer_route_var,
        ):
            variable.trace_add("write", self.handle_editor_variable_change)

        self.configure_style()
        self.build_ui_reset()
        # The nested material notebook can emit its initial tab event while
        # the outer notebook is still on the unit-assessment page.  Keep a
        # fresh window anchored to the actual unit-assessment workflow and do
        # not let the APEX material tab become the initial keyboard context.
        self.builder_notebook.select(0)
        self.test_tabs.select(0)
        self.keyboard_state.enter_builder(tab=0)
        self.keyboard_state.test_tab = 0
        self.keyboard_state.material_tab = 0
        self.refresh_shortcut_help()
        self.refresh_custom_pdf_list()
        self.refresh_order_csv_list()
        self.refresh_all()
        self.initialize_history()
        self.queue_recovery_store = RecoveryStore(RECOVERY_DIRECTORY)
        self.queue_recovery_writer = RecoveryWriter(self.queue_recovery_store)
        self.update_queue_view()
        self.update_preview()
        self.root.after(250, self.warm_printer_inventory_cache)
        # Apply taskbar state only from Tk's main thread.  A native window
        # subclass callback can re-enter Python while _tkinter has released
        # the GIL and terminate Python 3.12 during startup.
        self.root.after_idle(self.initialize_taskbar_indicator)
        self.root.after_idle(self.initialize_windows_notifier)
        if bool(self.device_monitor_config.get("enabled", False)):
            self.set_device_status_text("프린터 상태: 연결 준비 중", DeviceHealth.UNKNOWN)
        # Network login and the first multi-page Canon snapshot compete with
        # Tk while the first frame is being assembled.  Start them only after
        # the operator window is visible; print start still forces a fresh
        # device observation before any document is submitted.
        self.root.after(700, self.initialize_device_monitor_from_config)
        self.root.after(1000, self.poll_device_status)
        self.maximize_on_start()

    @property
    def root_path(self) -> Path:
        override = _ORDER_ROOT_OVERRIDE.get()
        if override is not None:
            return override
        return Path(self.root_dir_var.get().strip())

    def apply_app_icon(self) -> None:
        # Windows taskbar uses the ICO's small image/mask pair more reliably
        # than a large transparent PNG.  Keep the PNG as a fallback for
        # platforms where Tk cannot load the Windows icon.
        self.app_icon_image: tk.PhotoImage | None = None
        if os.name == "nt" and APP_ICON_PATH.exists():
            try:
                self.root.iconbitmap(default=str(APP_ICON_PATH))
                self.root.iconbitmap(str(APP_ICON_PATH))
                return
            except tk.TclError:
                pass
        if APP_ICON_PNG_PATH.exists():
            try:
                self.app_icon_image = tk.PhotoImage(file=str(APP_ICON_PNG_PATH))
                self.root.iconphoto(True, self.app_icon_image)
            except tk.TclError:
                self.app_icon_image = None
        elif APP_ICON_PATH.exists():
            try:
                self.root.iconbitmap(str(APP_ICON_PATH))
            except tk.TclError:
                pass

    def maximize_on_start(self) -> None:
        try:
            self.root.state("zoomed")
        except tk.TclError:
            try:
                self.root.attributes("-zoomed", True)
            except tk.TclError:
                pass

    def refresh_status_badge(self) -> None:
        if self.status_badge is None:
            return
        message = self.app_status_var.get()
        colors = self.colors
        if any(marker in message for marker in ("오류", "실패", "경고", "중지")):
            bg, fg, label = colors["danger_soft"], colors["danger"], f"상태  {message}"
        elif any(marker in message for marker in ("인쇄 중", "전송 중", "확인 중", "준비 중", "처리 중")):
            bg, fg, label = colors["success_soft"], colors["success"], f"상태  {message}"
        elif "완료" in message:
            bg, fg, label = colors["complete_soft"], colors["complete"], f"상태  {message}"
        else:
            bg, fg, label = colors["complete_soft"], colors["complete"], f"상태  {message}"
        self.status_badge.configure(text=label, bg=bg, fg=fg, highlightbackground=fg)

    def refresh_device_status_badge(self, health: DeviceHealth | None = None) -> None:
        if self.device_status_badge is None:
            return
        message = self.device_status_var.get()
        colors = self.colors
        if health in {DeviceHealth.ERROR, DeviceHealth.HELD, DeviceHealth.OFFLINE} or any(
            marker in message for marker in ("오류", "실패", "경고", "오프라인")
        ):
            bg, fg = colors["danger_soft"], colors["danger"]
        elif health == DeviceHealth.BUSY or (health != DeviceHealth.READY and any(
            marker in message for marker in ("연결 중", "인쇄 중", "확인 중", "준비 중")
        )):
            bg, fg = colors["success_soft"], colors["success"]
        else:
            bg, fg = colors["complete_soft"], colors["complete"]
        self.device_status_badge.configure(
            text=message,
            bg=bg,
            fg=fg,
            highlightbackground=fg,
        )

    def set_device_status_text(
        self,
        message: str,
        health: DeviceHealth | None = None,
    ) -> None:
        if (
            self.device_status_var.get() == message
            and getattr(self, "device_status_health", None) == health
        ):
            return
        self.device_status_health = health
        self.device_status_var.set(message)
        self.refresh_device_status_badge(health)

    def set_resource_estimate_text(self, message: str, tone: str = "secondary") -> None:
        self.resource_estimate_full_text = message
        self.resource_estimate_tone = tone
        self.refresh_resource_estimate_display()
        badge = getattr(self, "resource_estimate_badge", None)
        if badge is None:
            return
        colors = self.colors
        palette = {
            "success": (colors["success_soft"], colors["success"]),
            "warning": (colors["danger_soft"], colors["danger"]),
            "waiting": (colors["warning_soft"], colors["warning"]),
            "danger": (colors["danger_soft"], colors["danger"]),
            "secondary": (colors["panel_soft"], colors["muted"]),
            "info": (colors["complete_soft"], colors["complete"]),
        }
        background, foreground = palette.get(tone, palette["secondary"])
        badge.configure(
            bg=background,
            fg=foreground,
            highlightbackground=foreground,
        )

    def refresh_resource_estimate_display(self, _event: tk.Event | None = None) -> None:
        variable = getattr(self, "resource_estimate_var", None)
        if variable is None:
            return
        full_text = getattr(self, "resource_estimate_full_text", variable.get())
        bar = getattr(self, "resource_estimate_bar", None)
        width = int(bar.winfo_width()) if bar is not None else 0
        estimate = getattr(self, "latest_resource_estimate", None)
        if width >= 1900 or width <= 1:
            display = full_text
        elif width >= 1600 and estimate is not None:
            display = self.resource_estimate_display_text(compact=True)
        elif "계산 중" in full_text:
            display = "자원 계산 중"
        elif "실패" in full_text:
            display = "자원 계산 실패"
        elif "작업 큐 없음" in full_text:
            display = "예상 자원 없음"
        elif self.resource_completion_text() is not None:
            display = self.resource_completion_text().replace("예상 완료 시간 ", "예상 완료 ")
        else:
            display = "예상 자원 보기"
        if variable.get() != display:
            variable.set(display)

    def show_resource_estimate_details(self, _event: tk.Event | None = None) -> None:
        estimate = getattr(self, "latest_resource_estimate", None)
        message = (
            self.resource_estimate_detail_text()
            if estimate is not None
            else getattr(self, "resource_estimate_full_text", "예상 자원 정보가 없습니다.")
        )
        messagebox.showinfo(APP_TITLE, message, parent=self.root)

    def resource_completion_now(self) -> datetime:
        return datetime.now().astimezone()

    def resource_completion_text(self) -> str | None:
        override = getattr(self, "resource_completion_override", None)
        if override is not None:
            return override
        observation = getattr(self, "active_resource_eta", None)
        if observation is None and getattr(self, "latest_resource_estimate", None) is None:
            return None
        projection = getattr(self, "resource_completion_projection", None)
        if projection is None:
            return None
        now = self.resource_completion_now()
        if observation is not None:
            return observation.display_text(projection, now)
        text = projection.display_text(
            now, overdue_text="완료 확인 중" if getattr(self, "is_printing", False) else "시작 시 재계산",
        )
        if getattr(self, "retry_batch", None) is not None:
            text = text.replace("예상 완료 시간", "미전송 작업 예상 완료 시간")
        return text

    def resource_estimate_display_text(self, *, compact: bool = False) -> str:
        estimate = getattr(self, "latest_resource_estimate", None)
        completion = self.resource_completion_text()
        observation = getattr(self, "active_resource_eta", None)
        if observation is not None:
            prefix = "남은 A4" if observation.complete else "남은 A4 최소"
            jobs = len(observation.remaining_job_ids)
            paper = f" · {estimate.paper_label}" if isinstance(estimate, PrintResourceEstimate) else ""
            return (f"{prefix} {observation.remaining_sheets:,}장 · 미완료 {jobs:,}건 · "
                    f"{completion}{paper}")
        if getattr(self, "resource_completion_override", None) is not None:
            return completion
        if isinstance(estimate, PrintResourceEstimate):
            formatter = estimate.compact_text_with_eta if compact else estimate.display_text_with_eta
            return formatter(completion)
        if estimate is not None:
            return estimate.compact_text if compact else estimate.display_text
        return completion or getattr(self, "resource_estimate_full_text", "예상 자원 정보가 없습니다.")

    def resource_estimate_detail_text(self) -> str:
        estimate = getattr(self, "latest_resource_estimate", None)
        if isinstance(estimate, PrintResourceEstimate):
            detail = estimate.detail_text_with_eta(self.resource_completion_text())
            observation = getattr(self, "active_resource_eta", None)
            if observation is not None:
                detail += (f"\n현재 미완료 작업: {len(observation.remaining_job_ids):,}건, "
                           f"A4 {'최소 ' if not observation.complete else ''}{observation.remaining_sheets:,}장")
                if observation.complete and observation.remaining_job_ids:
                    detail += f"\n현재 남은 소요시간 범위: {observation.eta.display_text} (관측 시점 기준)"
            return detail
        return estimate.detail_text if estimate is not None else self.resource_estimate_display_text()

    def refresh_resource_completion_clock(self) -> None:
        """Refresh dates/overdue wording without moving the anchored deadline."""
        if getattr(self, "is_closing", False) or self.resource_completion_text() is None:
            return
        text = self.resource_estimate_display_text()
        if text != getattr(self, "resource_estimate_full_text", None):
            self.set_resource_estimate_text(text, getattr(self, "resource_estimate_tone", "secondary"))

    def adopt_resource_estimate(self, estimate: PrintResourceEstimate, request_key: object) -> None:
        retain_run = getattr(self, "resource_completion_run_key", None) == (
            getattr(self, "resource_estimate_revision", 0), request_key,
        )
        self.latest_resource_estimate = estimate
        self.latest_resource_estimate_key = request_key
        if not getattr(self, "is_printing", False) and not retain_run:
            self.active_resource_eta = None
            self.resource_completion_override = None
            if isinstance(estimate, PrintResourceEstimate) and estimate.is_complete:
                key = ("waiting", getattr(self, "resource_estimate_duration_key", request_key), estimate.eta)
                if key != getattr(self, "resource_completion_key", None):
                    self.resource_completion_projection = CompletionTimeProjection(estimate.eta, self.resource_completion_now())
                    self.resource_completion_key = key
            else:
                self.resource_completion_projection = None
                self.resource_completion_key = None
        tone = (getattr(self, "resource_estimate_tone", estimate.tone)
                if getattr(self, "is_printing", False) or retain_run else estimate.tone)
        self.set_resource_estimate_text(self.resource_estimate_display_text(), tone)

    def begin_resource_estimate_run(self, options: PrintRunOptions) -> None:
        self.resource_completion_run_key = (
            getattr(self, "resource_estimate_revision", 0),
            getattr(self, "resource_estimate_request_key", getattr(self, "latest_resource_estimate_key", None)),
        )
        self.active_resource_eta = None
        self.resource_completion_key = None
        self.resource_completion_projection = None
        self.resource_completion_override = None
        estimate = getattr(self, "latest_resource_estimate", None)
        if options.dry_run:
            self.resource_completion_override = "미리보기 · 완료 시각 계산 안 함"
        elif options.device_completion_mode:
            self.resource_completion_override = "예상 완료 시간 계산 중"
        elif isinstance(estimate, PrintResourceEstimate) and estimate.is_complete:
            self.resource_completion_projection = CompletionTimeProjection(estimate.eta, self.resource_completion_now())
        else:
            self.resource_completion_override = "예상 완료 시간 일부 계산 불가"
        tone = "waiting" if options.dry_run else "danger" if self.resource_completion_override == "예상 완료 시간 일부 계산 불가" else "success"
        self.set_resource_estimate_text(self.resource_estimate_display_text(), tone)

    def update_print_resource_eta(self, observation: PrintEtaObservation) -> None:
        if getattr(self, "is_closing", False):
            return
        def update() -> None:
            state = getattr(self, "print_progress_state", None)
            current_run = state.run_id if state is not None else getattr(self, "history_run_id", None)
            cancel = getattr(self, "print_cancel_event", None)
            if (getattr(self, "is_closing", False) or observation.run_id != current_run
                    or (cancel is not None and cancel.is_set())
                    or (state is not None and state.phase in {RunPhase.COMPLETE, RunPhase.ERROR, RunPhase.STOPPED, RunPhase.STOPPING, RunPhase.IDLE})):
                return
            self.active_resource_eta = observation
            self.resource_completion_override = None
            if observation.anchor_key != getattr(self, "resource_completion_key", None):
                self.resource_completion_projection = CompletionTimeProjection(observation.eta, observation.observed_at)
                self.resource_completion_key = observation.anchor_key
            tone = "danger" if not observation.complete or observation.held else "waiting" if not observation.remaining_job_ids else "success"
            estimate = getattr(self, "latest_resource_estimate", None)
            if isinstance(estimate, PrintResourceEstimate) and estimate.tone in {"warning", "danger"}:
                tone = "danger"
            self.set_resource_estimate_text(self.resource_estimate_display_text(), tone)

        if threading.current_thread() is threading.main_thread():
            update()
        else:
            try:
                self.root.after(0, update)
            except tk.TclError:
                pass

    def invalidate_resource_completion_for_append(self) -> None:
        options = getattr(self, "print_run_options", None)
        if options is not None and options.device_completion_mode:
            self.resource_completion_override = "예상 완료 시간 갱신 중"
        else:
            # Windows submitted counts do not identify physically unfinished
            # pages. Do not pretend the old run's finish time covers an append.
            self.resource_completion_override = "예상 완료 시간 재계산 필요"
        self.set_resource_estimate_text(self.resource_estimate_display_text(), "waiting")

    def update_resource_completion_status(self, message: str) -> None:
        text_and_tone = {
            "실제 인쇄 완료": ("실제 인쇄 완료", "info"),
            "미리보기 완료": ("미리보기 완료", "info"),
            "전송 완료 / 인쇄 완료": ("Windows 대기열 비움 · 실제 완료 시각 확인 불가", "waiting"),
            "전송 완료 / 인쇄 중": ("완료 확인 중", "waiting"),
            "프린터 오류 / 인쇄 일시 중지": ("인쇄 중지 · 재개 시 재계산", "danger"),
            "전송 중지 요청됨": ("인쇄 중지 · 재개 시 재계산", "waiting"),
            "전송 중지됨": ("인쇄 중지 · 재개 시 재계산", "waiting"),
            "완료 확인 중지됨": ("완료 확인 중지 · 실제 완료 시각 확인 불가", "waiting"),
            "전송 완료 / 완료 확인 중지": ("완료 확인 중지 · 실제 완료 시각 확인 불가", "waiting"),
            "오류 발생": ("예상 완료 시간 계산 중단", "danger"),
            "전송 완료 / 대기열 확인 실패": ("예상 완료 시간 계산 중단", "danger"),
        }
        if message not in text_and_tone:
            return
        self.resource_completion_override, tone = text_and_tone[message]
        self.set_resource_estimate_text(self.resource_estimate_display_text(), tone)

    def resource_estimate_jobs(self, blocks: list[PrintBlock]) -> tuple[ResourceJob, ...]:
        """Capture a Tk-free resource recipe without building separator PDFs."""

        jobs: list[ResourceJob] = []
        include_names = bool(self.name_separator_page_var.get())
        include_separator = bool(self.separator_page_var.get())
        grouped_name_labels = (
            name_separator_group_labels(blocks)
            if include_names
            else (None,) * len(blocks)
        )
        omr_path = (
            find_omr_pdf()
            if any(block_requires_omr_sheet(block) for block in blocks)
            else None
        )
        for index, block in enumerate(blocks):
            if include_names and grouped_name_labels[index] is not None:
                jobs.append(
                    ResourceJob(
                        None,
                        known_page_count=1,
                        stapled=True,
                        label="이름 구분지",
                    )
                )
            if omr_path is not None and block_requires_omr_sheet(block):
                jobs.append(ResourceJob(omr_path, label="자동 OMR"))
            for item in block.files:
                printer_override, pages_per_sheet = resolve_exam_print_settings(block, item)
                separator_route = bool(
                    printer_override
                    and "구분지" in str(printer_override).replace(" ", "")
                )
                jobs.append(
                    ResourceJob(
                        item.path,
                        copies=block.copies,
                        pages_per_sheet=pages_per_sheet,
                        stapled=not separator_route,
                        label=item.name,
                        dispatch_jobs=block.copies if block.bundled else 1,
                    )
                )
            if include_separator:
                jobs.append(
                    ResourceJob(
                        None,
                        known_page_count=1,
                        stapled=False,
                        label="구분지",
                    )
                )
        return tuple(jobs)

    def resource_estimate_retry_jobs(
        self,
        jobs: tuple[PrintJob, ...],
    ) -> tuple[ResourceJob, ...]:
        return tuple(
            ResourceJob(
                job.item.path,
                copies=job.copies,
                pages_per_sheet=job.pages_per_sheet,
                stapled=not (job.is_separator and not job.is_name_separator),
                label=job.item.name,
            )
            for job in jobs
        )

    def resource_estimate_consumables(self):
        monitor = getattr(self, "device_monitor", None)
        if monitor is None:
            return None
        snapshot = monitor.latest_snapshot
        if not snapshot.authenticated or snapshot.stale:
            return None
        return snapshot.consumables

    def eta_print_rate_samples(self) -> tuple[float, ...]:
        """Retain legacy scalar samples for config and callback compatibility."""

        raw_samples = self.device_monitor_config.get(ETA_PRINT_RATE_CONFIG_KEY, [])
        if not isinstance(raw_samples, list):
            return ()
        return tuple(
            float(value)
            for value in raw_samples
            if isinstance(value, (int, float)) and 0.05 <= float(value) <= 5.0
        )[-ETA_SAMPLE_LIMIT:]

    def learn_completed_device_job_rate(
        self,
        device_job: DeviceJob,
        sheet_count: int,
        *,
        copies: int = 1,
        duplex: bool = True,
        previous_completed_at: datetime | None = None,
    ) -> EtaTimingSample | None:
        """Learn print-engine speed without including printer queue wait."""

        if device_job.state != DeviceJobState.COMPLETED:
            return None
        if previous_completed_at is None and sheet_count < 20:
            # A cold first short job is dominated by wake-up and preparation.
            # It must not become the per-document cost for a whole warm run.
            return None
        timing = completed_job_timing(
            device_job.started_at,
            device_job.ended_at,
            sheet_count,
            copies,
            previous_completed_at=previous_completed_at,
            duplex=duplex,
        )
        if timing is None:
            return None
        samples = [*self.eta_job_timing_samples(), timing][-ETA_TIMING_SAMPLE_LIMIT:]
        self.device_monitor_config[ETA_TIMING_CONFIG_KEY] = [
            {"sheets": item.sheets, "copies": item.copies, "seconds": item.seconds, "duplex": item.duplex}
            for item in samples
        ]
        self.device_monitor_config[ETA_TIMING_DEVICE_KEY] = timing_device_key(str(self.device_monitor_config.get("base_url", "")))
        return timing

    def eta_job_timing_samples(self) -> tuple[EtaTimingSample, ...]:
        config = getattr(self, "device_monitor_config", {})
        if config.get(ETA_TIMING_DEVICE_KEY) != timing_device_key(str(config.get("base_url", ""))):
            return ()
        return validated_timing_samples(config.get(ETA_TIMING_CONFIG_KEY))

    def schedule_resource_estimate(self) -> None:
        """Queue the latest forecast behind print-critical PDF work."""

        if getattr(self, "is_closing", False) or getattr(self, "is_printing", False):
            return
        scheduler = getattr(self, "resource_estimate_scheduler", None)
        root = getattr(self, "root", None)
        if scheduler is None or root is None or not hasattr(self, "resource_estimate_results"):
            return
        retry_batch = getattr(self, "retry_batch", None)
        if retry_batch is not None and retry_batch.jobs:
            jobs = self.resource_estimate_retry_jobs(tuple(retry_batch.jobs))
        else:
            blocks = self.blocks_waiting_for_print()
            jobs = self.resource_estimate_jobs(blocks) if blocks else ()
        if not jobs:
            previous = getattr(self, "resource_estimate_future", None)
            if previous is not None and hasattr(previous, "cancel"):
                previous.cancel()
            self.resource_estimate_revision = getattr(self, "resource_estimate_revision", 0) + 1
            self.resource_estimate_future = None
            self.resource_estimate_request_key = ("empty",)
            self.latest_resource_estimate = None
            self.latest_resource_estimate_key = None
            self.resource_completion_projection = None
            self.resource_completion_key = None
            self.resource_completion_override = None
            self.resource_completion_run_key = None
            self.active_resource_eta = None
            self.set_resource_estimate_text("예상 자원  작업 큐 없음", "secondary")
            return

        consumables = self.resource_estimate_consumables()
        samples = self.eta_print_rate_samples()
        timings = self.eta_job_timing_samples()
        paper_key = (
            tuple(consumables.a4_sources)
            if consumables is not None
            else ()
        )
        duplex = bool(self.duplex_var.get())
        request_key = (jobs, paper_key, timings, duplex)
        self.resource_estimate_duration_key = (jobs, timings, duplex)
        if request_key == getattr(self, "resource_estimate_request_key", None):
            return

        previous = getattr(self, "resource_estimate_future", None)
        if previous is not None and hasattr(previous, "cancel"):
            previous.cancel()
        revision = getattr(self, "resource_estimate_revision", 0) + 1
        self.resource_estimate_revision = revision
        self.resource_estimate_request_key = request_key
        self.resource_completion_run_key = None
        self.latest_resource_estimate = None
        self.latest_resource_estimate_key = None
        self.active_resource_eta = None
        self.resource_completion_override = None
        self.set_resource_estimate_text("예상 자원  계산 중", "secondary")

        def work() -> PrintResourceEstimate:
            return estimate_print_resources(
                jobs,
                page_counter=get_pdf_page_count,
                duplex=duplex,
                consumables=consumables,
                verified_seconds_per_sheet=samples,
                timing_samples=timings,
                cancelled=lambda: (
                    revision != getattr(self, "resource_estimate_revision", -1)
                    or getattr(self, "is_closing", False)
                ),
            )

        future = scheduler.submit(work, priority=0)
        self.resource_estimate_future = future

        def finished(done: object) -> None:
            if getattr(self, "is_closing", False):
                return
            self.resource_estimate_results.put((revision, done))

        future.add_done_callback(finished)
        if self.resource_estimate_poll_after_id is None:
            self.resource_estimate_poll_after_id = root.after(
                20,
                self.poll_resource_estimate_results,
            )

    def poll_resource_estimate_results(self) -> None:
        self.resource_estimate_poll_after_id = None
        while True:
            try:
                revision, future = self.resource_estimate_results.get_nowait()
            except queue.Empty:
                break
            if revision != self.resource_estimate_revision or self.is_closing:
                continue
            try:
                estimate = future.result()
            except Exception as exc:
                retain_run = getattr(self, "resource_completion_run_key", None) == (
                    revision, getattr(self, "resource_estimate_request_key", None),
                )
                self.resource_estimate_request_key = None
                self.latest_resource_estimate = None
                self.latest_resource_estimate_key = None
                if not retain_run:
                    self.resource_completion_projection = None
                    self.resource_completion_key = None
                    self.set_resource_estimate_text("예상 자원  계산 실패", "warning")
                self.log(f"인쇄 자원 예측 실패: {exc}")
            else:
                self.adopt_resource_estimate(estimate, self.resource_estimate_request_key)
        current = getattr(self, "resource_estimate_future", None)
        if (
            not self.is_closing
            and current is not None
            and hasattr(current, "done")
            and not current.done()
        ):
            self.resource_estimate_poll_after_id = self.root.after(
                20,
                self.poll_resource_estimate_results,
            )

    def resource_estimate_confirmation_text(self) -> str:
        estimate = getattr(self, "latest_resource_estimate", None)
        if (
            estimate is None
            or getattr(self, "latest_resource_estimate_key", None)
            != getattr(self, "resource_estimate_request_key", None)
        ):
            return "\n\n예상 자원: 계산 결과를 아직 확인할 수 없습니다."
        return f"\n\n{self.resource_estimate_detail_text()}"

    def complete_resource_estimate_for_confirmation(self, timeout: float = 15.0) -> None:
        """Finish the already scheduled forecast before the final print prompt."""

        if (
            getattr(self, "latest_resource_estimate", None) is not None
            and getattr(self, "latest_resource_estimate_key", None)
            == getattr(self, "resource_estimate_request_key", None)
        ):
            return
        self.schedule_resource_estimate()
        future = getattr(self, "resource_estimate_future", None)
        revision = getattr(self, "resource_estimate_revision", 0)
        request_key = getattr(self, "resource_estimate_request_key", None)
        if future is None or not hasattr(future, "result"):
            return
        try:
            estimate = future.result(timeout=max(0.0, float(timeout)))
        except Exception as exc:
            self.log(f"인쇄 시작 전 자원 예측을 완료하지 못했습니다: {exc}")
            return
        if (
            revision != getattr(self, "resource_estimate_revision", -1)
            or request_key != getattr(self, "resource_estimate_request_key", None)
        ):
            return
        self.adopt_resource_estimate(estimate, request_key)

    def refresh_toggle_buttons(self) -> None:
        for button, text, variable in self.toggle_buttons:
            enabled = bool(variable.get())
            marker = "●" if enabled else "○"
            state_text = "켜짐" if enabled else "꺼짐"
            label = "양면" if text == "양면 인쇄" else text
            button.configure(
                text=f"{marker} {label} {state_text}",
                bg=self.colors["accent"] if enabled else self.colors["panel_soft"],
                fg=self.colors["on_accent"] if enabled else self.colors["muted"],
                activebackground=self.colors["accent_hover"] if enabled else self.colors["hover"],
                activeforeground=self.colors["on_accent"] if enabled else self.colors["text"],
                disabledforeground=self.colors["disabled_fg"],
                highlightbackground=self.colors["accent"] if enabled else self.colors["border"],
                highlightcolor=self.colors["focus"],
                highlightthickness=2,
                relief="solid",
            )

    def toggle_boolean_var(self, variable: BooleanVar, command: callable | None = None) -> None:
        variable.set(not variable.get())
        if command is not None:
            command()
        self.refresh_toggle_buttons()

    def add_toggle_button(
        self,
        parent: tk.Misc,
        text: str,
        variable: BooleanVar,
        *,
        command: callable | None = None,
        help_text: str = "",
    ) -> tk.Button:
        button = tk.Button(
            parent,
            command=lambda: self.toggle_boolean_var(variable, command),
            bd=0,
            padx=10,
            pady=5,
            relief="flat",
            font=(self.ui_font_family, 11, "bold"),
            cursor="hand2",
            highlightthickness=1,
        )
        self.toggle_buttons.append((button, text, variable))
        if help_text:
            self.toggle_help_cards.append(
                ToggleHoverCard(
                    button,
                    text,
                    help_text,
                    background=self.colors["panel"],
                    border=self.colors["success"],
                    title_color=self.colors["text"],
                    body_color=self.colors["muted"],
                )
            )
        self.refresh_toggle_buttons()
        return button

    def refresh_name_separator_editor_state(self) -> None:
        variable = getattr(self, "name_separator_page_var", None)
        enabled = bool(variable is not None and variable.get())
        editor_entry = getattr(self, "edit_name_separator_entry", None)
        if editor_entry is not None:
            selected_index = self.selected_block_index()
            selected_block = (
                self.blocks[selected_index]
                if selected_index is not None and 0 <= selected_index < len(self.blocks)
                else None
            )
            editor_enabled = bool(
                enabled
                and selected_block is not None
                and block_uses_name_separator(selected_block)
            )
            editor_entry.configure(
                state="normal" if editor_enabled else "disabled",
                style="StudentNameOn.TEntry" if editor_enabled else "StudentNameOff.TEntry",
            )
        try:
            is_manual_tab = self.builder_notebook.index("current") != 4
        except (AttributeError, tk.TclError):
            is_manual_tab = False
        entries = list(getattr(self, "new_block_name_entries", ()))
        if not entries:
            fallback = getattr(self, "new_block_name_entry", None)
            entries = [fallback] if fallback is not None else []
        builder_enabled = enabled and is_manual_tab
        for builder_entry in entries:
            builder_entry.configure(
                state="normal" if builder_enabled else "disabled",
                style="StudentNameOn.TEntry" if builder_enabled else "StudentNameOff.TEntry",
            )

    def handle_name_separator_toggle(self) -> None:
        self.refresh_name_separator_editor_state()
        if hasattr(self, "preview_text"):
            self.update_preview()
        self.refresh_shortcut_help()

    def resolve_ui_font_family(self) -> str:
        """Prefer the requested Yoon Gothic face, with an installed Korean fallback."""
        try:
            families = {name.casefold(): name for name in tkfont.families(self.root)}
        except tk.TclError:
            families = {}
        for candidate in (
            "Pretendard",
            "Pretendard Variable",
            "윤고딕 320",
            "Yoon Gothic 320",
            "Yu Gothic UI",
            "Malgun Gothic",
        ):
            resolved = families.get(candidate.casefold())
            if resolved:
                return resolved
        return "TkDefaultFont"

    def configure_style(self) -> None:
        colors = self.colors
        family = self.ui_font_family
        if ttkbootstrap is not None:
            style = ttkbootstrap.Style(theme="flatly")
        else:
            style = ttk.Style()
            try:
                style.theme_use("clam")
            except Exception:
                pass
        self.style = style
        # ttk's combobox popdown list is a Tk listbox with its own option
        # database entry, so match it to the enlarged field controls.
        self.root.option_add("*TCombobox*Listbox.font", f"{{{family}}} 13")
        self.root.option_add("*TCombobox*Listbox.selectBorderWidth", 0)
        style.configure(
            ".",
            font=(family, 11),
            background=colors["app_bg"],
            foreground=colors["text"],
        )
        style.configure("TFrame", background=colors["app_bg"])
        style.configure("Shell.TFrame", background=colors["app_bg"])
        style.configure(
            "Panel.TFrame",
            background=colors["panel"],
            borderwidth=1,
            relief="solid",
            bordercolor=colors["border"],
        )
        style.configure("Card.TFrame", background=colors["panel"], borderwidth=1, relief="solid")
        style.configure("Toolbar.TFrame", background=colors["panel_soft"], relief="flat")
        style.configure("Footer.TFrame", background=colors["panel"], borderwidth=1, relief="solid")
        style.configure("StatusBar.TFrame", background=colors["panel"], relief="flat")
        style.configure(
            "Soft.TFrame",
            background=colors["panel_soft"],
            borderwidth=1,
            relief="solid",
            bordercolor=colors["border_soft"],
        )

        style.configure(
            "Header.TLabel",
            background=colors["panel"],
            foreground=colors["text"],
            font=(family, 18, "bold"),
        )
        style.configure(
            "Subtle.TLabel",
            background=colors["panel"],
            foreground=colors["muted"],
            font=(family, 11),
        )
        style.configure(
            "ShellSubtle.TLabel",
            background=colors["app_bg"],
            foreground=colors["muted"],
            font=(family, 11),
        )
        style.configure(
            "PanelTitle.TLabel",
            background=colors["panel"],
            foreground=colors["text"],
            font=(family, 15, "bold"),
        )
        style.configure(
            "CardTitle.TLabel",
            background=colors["panel"],
            foreground=colors["text"],
            font=(family, 15, "bold"),
        )
        style.configure("CardText.TLabel", background=colors["panel"], foreground=colors["muted"])
        style.configure("ToolbarText.TLabel", background=colors["panel_soft"], foreground=colors["muted"])
        style.configure(
            "StatusChip.TLabel",
            background=colors["success_soft"],
            foreground=colors["success"],
            font=(family, 11, "bold"),
            padding=(18, 7),
        )
        style.configure(
            "WarningChip.TLabel",
            background=colors["panel"],
            foreground=colors["link"],
            font=(family, 10, "bold"),
            padding=(8, 4),
        )
        style.configure(
            "StatusBar.TLabel",
            background=colors["panel"],
            foreground=colors["muted"],
            font=(family, 11),
        )
        style.configure(
            "Field.TLabel",
            background=colors["panel"],
            foreground="#334155",
            font=(family, 12, "bold"),
            anchor="w",
            justify="left",
        )
        style.configure(
            "FieldSoft.TLabel",
            background=colors["panel_soft"],
            foreground="#334155",
            font=(family, 12, "bold"),
            anchor="w",
            justify="left",
        )
        style.configure(
            "Status.TLabel",
            background=colors["panel"],
            foreground=colors["muted"],
            font=(family, 11),
        )

        style.configure(
            "TButton",
            padding=(10, 6),
            background=colors["panel_soft"],
            foreground="#1f2937",
            borderwidth=1,
            relief="flat",
            focusthickness=1,
            focuscolor=colors["accent"],
        )
        style.map(
            "TButton",
            background=[("active", "#eef2f7"), ("pressed", "#e2e8f0"), ("disabled", "#f1f5f9")],
            foreground=[("disabled", colors["muted_soft"])],
        )
        style.configure(
            "Compact.TButton",
            padding=(6, 3),
            background=colors["panel"],
            foreground="#334155",
            borderwidth=0,
            font=(family, 10, "bold"),
        )
        style.map("Compact.TButton", background=[("active", "#eef2f7"), ("pressed", "#e2e8f0")])
        style.configure(
            "Accent.TButton",
            padding=(14, 7),
            background=colors["accent"],
            foreground="#ffffff",
            borderwidth=0,
            font=(family, 12, "bold"),
        )
        style.map(
            "Accent.TButton",
            background=[("active", colors["accent_hover"]), ("pressed", "#1e3a8a"), ("disabled", "#93c5fd")],
            foreground=[("disabled", "#eff6ff")],
        )
        style.configure(
            "Danger.TButton",
            padding=(7, 3),
            background=colors["danger_soft"],
            foreground=colors["danger"],
            borderwidth=0,
            font=(family, 11, "bold"),
        )
        style.map("Danger.TButton", background=[("active", "#ffe4e6"), ("pressed", "#fecdd3")])
        style.configure(
            "CompactDanger.TButton",
            padding=(6, 3),
            background=colors["danger_soft"],
            foreground=colors["danger"],
            borderwidth=0,
            font=(family, 10, "bold"),
        )
        style.map("CompactDanger.TButton", background=[("active", "#ffe4e6"), ("pressed", "#fecdd3")])
        style.configure(
            "Ghost.TButton",
            padding=(8, 4),
            background=colors["panel"],
            foreground="#334155",
            borderwidth=0,
        )
        style.map("Ghost.TButton", background=[("active", colors["panel_soft"]), ("pressed", "#e2e8f0")])

        style.configure(
            "TEntry",
            padding=(6, 5),
            font=(family, 12),
            fieldbackground="#ffffff",
            foreground=colors["text"],
            bordercolor=colors["border"],
            lightcolor=colors["border"],
            darkcolor=colors["border"],
        )
        style.map("TEntry", bordercolor=[("focus", colors["accent"])])
        style.configure(
            "StudentNameOff.TEntry",
            padding=(6, 5),
            font=(family, 12),
            fieldbackground="#e4e9ee",
            foreground="#8997a4",
            bordercolor="#c8d1da",
            lightcolor="#d8e0e7",
            darkcolor="#bcc7d0",
            borderwidth=1,
        )
        style.map(
            "StudentNameOff.TEntry",
            fieldbackground=[("disabled", "#e4e9ee")],
            foreground=[("disabled", "#8997a4")],
            bordercolor=[("disabled", "#c8d1da")],
            lightcolor=[("disabled", "#d8e0e7")],
            darkcolor=[("disabled", "#bcc7d0")],
        )
        style.configure(
            "StudentNameOn.TEntry",
            padding=(6, 5),
            font=(family, 12),
            fieldbackground="#fffafb",
            foreground=colors["text"],
            bordercolor="#d66a76",
            lightcolor="#f3b9bf",
            darkcolor="#c65360",
            borderwidth=2,
        )
        style.map(
            "StudentNameOn.TEntry",
            fieldbackground=[("focus", "#fff7f8")],
            bordercolor=[("focus", "#bd4351")],
            lightcolor=[("focus", "#ef9ca5")],
            darkcolor=[("focus", "#b83b49")],
        )
        style.configure(
            "TCombobox",
            padding=(7, 6),
            font=(family, 13),
            fieldbackground="#ffffff",
            foreground=colors["text"],
            bordercolor=colors["border"],
            lightcolor=colors["border"],
            darkcolor=colors["border"],
            arrowcolor=colors["muted"],
        )
        style.map("TCombobox", bordercolor=[("focus", colors["accent"])])
        style.configure(
            "Blank.TCombobox",
            padding=(7, 6),
            font=(family, 13),
            background="#ffffff",
            fieldbackground="#ffffff",
            foreground="#ffffff",
            bordercolor="#ffffff",
            lightcolor="#ffffff",
            darkcolor="#ffffff",
            arrowcolor="#ffffff",
        )
        style.map(
            "Blank.TCombobox",
            background=[("disabled", "#ffffff")],
            fieldbackground=[("disabled", "#ffffff")],
            foreground=[("disabled", "#ffffff")],
            bordercolor=[("disabled", "#ffffff")],
            lightcolor=[("disabled", "#ffffff")],
            darkcolor=[("disabled", "#ffffff")],
            arrowcolor=[("disabled", "#ffffff")],
        )
        style.configure(
            "TSpinbox",
            padding=(7, 6),
            font=(family, 13),
            fieldbackground="#ffffff",
            foreground=colors["text"],
            bordercolor=colors["border"],
            lightcolor=colors["border"],
            darkcolor=colors["border"],
            arrowcolor=colors["muted"],
        )
        style.map("TSpinbox", bordercolor=[("focus", colors["accent"])])
        style.configure("TCheckbutton", background=colors["panel"], foreground=colors["text"])
        style.map("TCheckbutton", foreground=[("disabled", colors["muted_soft"])])
        style.configure("Toolbar.TCheckbutton", background=colors["panel_soft"], foreground=colors["text"])
        style.map("Toolbar.TCheckbutton", foreground=[("disabled", colors["muted_soft"])])
        toggle_layout = [
            (
                "Checkbutton.border",
                {
                    "sticky": "nswe",
                    "children": [
                        (
                            "Checkbutton.padding",
                            {"sticky": "nswe", "children": [("Checkbutton.label", {"sticky": "nswe"})]},
                        )
                    ],
                },
            )
        ]
        for style_name, bg in (
            ("Toolbar.Toggle.TCheckbutton", colors["panel_soft"]),
            ("Panel.Toggle.TCheckbutton", colors["panel"]),
        ):
            style.layout(style_name, toggle_layout)
            style.configure(
                style_name,
                padding=(10, 5),
                background=bg,
                foreground=colors["muted"],
                borderwidth=1,
                relief="flat",
                focuscolor=colors["accent"],
                font=(family, 11, "bold"),
            )
            style.map(
                style_name,
                background=[
                    ("selected", colors["accent"]),
                    ("active", "#eef2f7"),
                    ("pressed", "#e2e8f0"),
                    ("disabled", "#f1f5f9"),
                ],
                foreground=[("selected", "#ffffff"), ("disabled", colors["muted_soft"])],
                bordercolor=[("selected", colors["accent"]), ("focus", colors["link"])],
            )
        style.configure(
            "TNotebook",
            background=colors["app_bg"],
            borderwidth=0,
            tabmargins=(0, 0, 0, 0),
        )
        style.configure(
            "TNotebook.Tab",
            # ttk renders notebook tabs through this shared tab style, even
            # when the notebook itself uses a custom style name.
            padding=(44, 10),
            background=colors["panel_soft"],
            foreground=colors["muted"],
            font=(family, 11, "bold"),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", colors["panel"])],
            foreground=[("selected", colors["text"])],
        )
        style.configure(
            "Builder.TNotebook",
            background=colors["app_bg"],
            borderwidth=0,
            tabmargins=(0, 0, 0, 0),
        )
        style.configure(
            "Builder.TNotebook.Tab",
            padding=(3, 8),
            anchor="center",
            background=colors["panel_soft"],
            foreground=colors["muted"],
            font=(family, 11, "bold"),
        )
        style.map(
            "Builder.TNotebook.Tab",
            background=[("selected", colors["accent_soft"])],
            foreground=[("selected", colors["accent"])],
        )
        style.configure(
            "Material.TNotebook",
            background=colors["app_bg"],
            borderwidth=0,
        )
        style.configure(
            "Material.TNotebook.Tab",
            padding=(4, 7),
            width=96,
            anchor="center",
            background=colors["panel_soft"],
            foreground=colors["muted"],
            font=(family, 9, "bold"),
        )
        style.map(
            "Material.TNotebook.Tab",
            background=[("selected", colors["panel"])],
            foreground=[("selected", colors["text"])],
        )
        style.configure(
            "LessonMaterial.TNotebook",
            background=colors["panel"],
            borderwidth=0,
        )
        style.configure(
            "LessonMaterial.TNotebook.Tab",
            padding=(28, 6),
            anchor="center",
            background=colors["panel_soft"],
            foreground=colors["muted"],
            font=(family, 8, "bold"),
        )
        style.map(
            "LessonMaterial.TNotebook.Tab",
            background=[("selected", colors["panel"])],
            foreground=[("selected", colors["text"])],
        )

        # The UI is intentionally quiet: one cool paper canvas, one ink color,
        # and blue reserved for actions and confirmed output settings.
        style.configure("Surface.TFrame", background=colors["panel"], borderwidth=1, relief="solid", bordercolor=colors["border"])
        style.configure("Sidebar.TFrame", background=colors["panel"], borderwidth=1, relief="solid", bordercolor=colors["border"])
        style.configure(
            "BuilderWorkspace.TFrame",
            background=colors["panel"],
            borderwidth=2,
            relief="solid",
            bordercolor=colors["border"],
        )
        style.configure(
            "QueueWorkspace.TFrame",
            background=colors["panel"],
            borderwidth=2,
            relief="solid",
            bordercolor=colors["border"],
        )
        style.configure(
            "FocusBuilderWorkspace.TFrame",
            background=colors["panel"],
            borderwidth=2,
            relief="solid",
            bordercolor=colors["accent"],
        )
        style.configure(
            "FocusQueueWorkspace.TFrame",
            background=colors["panel"],
            borderwidth=2,
            relief="solid",
            bordercolor=colors["accent"],
        )
        style.configure("Inset.TFrame", background=colors["panel_soft"], borderwidth=1, relief="solid", bordercolor=colors["border_soft"])
        style.configure("Kicker.TLabel", background=colors["panel"], foreground=colors["accent"], font=(family, 9, "bold"))
        style.configure("PageTitle.TLabel", background=colors["panel"], foreground=colors["text"], font=(family, 18, "bold"))
        style.configure("SectionTitle.TLabel", background=colors["panel"], foreground=colors["text"], font=(family, 15, "bold"))
        style.configure("SectionHint.TLabel", background=colors["panel"], foreground=colors["muted"], font=(family, 10))
        style.configure("InsetTitle.TLabel", background=colors["panel_soft"], foreground=colors["text"], font=(family, 12, "bold"))
        style.configure("InsetText.TLabel", background=colors["panel_soft"], foreground=colors["muted"], font=(family, 10))
        style.configure("ShortcutHelpTitle.TLabel", background=colors["panel_soft"], foreground=colors["text"], font=(family, 11, "bold"))
        style.configure("ShortcutHelpContext.TLabel", background=colors["panel_soft"], foreground=colors["accent"], font=(family, 10, "bold"))
        style.configure("ShortcutHelpText.TLabel", background=colors["panel_soft"], foreground=colors["muted"], font=(family, 11), justify="left")
        style.configure("QueueMeta.TLabel", background=colors["panel"], foreground=colors["muted"], font=(family, 10))
        style.configure("FooterText.TLabel", background=colors["app_bg"], foreground=colors["muted"], font=(family, 10))
        style.configure("Primary.TButton", padding=(14, 7), background=colors["accent"], foreground="#ffffff", borderwidth=0, font=(family, 11, "bold"))
        style.map("Primary.TButton", background=[("active", colors["accent_hover"]), ("pressed", "#21496a"), ("disabled", "#b9c9d7")], foreground=[("disabled", "#eef5fa")])
        style.configure("SoftAction.TButton", padding=(9, 5), background=colors["panel_soft"], foreground=colors["text"], borderwidth=1, bordercolor=colors["border"], font=(family, 10, "bold"))
        style.map("SoftAction.TButton", background=[("active", colors["accent_soft"]), ("pressed", "#d7e7f2")])
        style.configure("DangerAction.TButton", padding=(9, 5), background=colors["danger_soft"], foreground=colors["danger"], borderwidth=0, font=(family, 10, "bold"))
        style.map("DangerAction.TButton", background=[("active", "#f5dcd8"), ("pressed", "#efc7c1")])
        apply_workbench_styles(style, family)
        # Entry-family controls own their font option; ttk's style font alone
        # does not replace TkTextFont. Set it before constructing the fields.
        for widget_class in ("TEntry", "TCombobox", "TSpinbox"):
            self.root.option_add(f"*{widget_class}.font", (family, 11))
        self.root.option_add("*TCombobox*Listbox.font", f"{{{family}}} 11")

    @staticmethod
    def name_entry_window_handle(widget: tk.Misc) -> int:
        try:
            return int(widget.winfo_id())
        except (AttributeError, tk.TclError, TypeError, ValueError):
            return 0

    def cancel_name_ime_commit(self, widget: tk.Misc) -> None:
        key = str(widget)
        pending = getattr(self, "name_ime_after_ids", None)
        if pending is None:
            return
        after_id = pending.pop(key, None)
        if after_id is None:
            return
        try:
            self.root.after_cancel(after_id)
        except (AttributeError, tk.TclError):
            pass

    def commit_name_entry_ime(self, widget: tk.Misc) -> bool:
        self.cancel_name_ime_commit(widget)
        window_handle = self.name_entry_window_handle(widget)
        if not window_handle:
            return False
        return complete_windows_ime_composition(window_handle)

    def take_name_entry_ime(self, widget: tk.Misc) -> bool:
        """Synchronously move the native IME preedit text into a Tk entry."""
        if not callable(getattr(widget, "insert", None)) or not callable(
            getattr(widget, "index", None)
        ):
            return False
        self.cancel_name_ime_commit(widget)
        composition = take_windows_ime_composition(
            self.name_entry_window_handle(widget)
        )
        if not composition:
            return False
        try:
            insert_index = int(widget.index("insert"))
            try:
                if widget.selection_present():
                    insert_index = int(widget.index("sel.first"))
                    widget.delete("sel.first", "sel.last")
            except (AttributeError, tk.TclError):
                pass
            widget.insert(insert_index, composition)
            try:
                widget.icursor(insert_index + len(composition))
            except (AttributeError, tk.TclError):
                pass
            return True
        except (AttributeError, tk.TclError, TypeError, ValueError):
            return False

    def schedule_name_ime_commit(self, widget: tk.Misc) -> None:
        self.cancel_name_ime_commit(widget)
        key = str(widget)

        def commit_after_idle() -> None:
            self.name_ime_after_ids.pop(key, None)
            window_handle = self.name_entry_window_handle(widget)
            if window_handle:
                complete_windows_ime_composition(window_handle)

        after_id = self.root.after(NAME_IME_IDLE_COMMIT_MS, commit_after_idle)
        self.name_ime_after_ids[key] = after_id

    def handle_name_ime_focus_in(self, event: tk.Event) -> None:
        activate_windows_korean_input(
            self.name_entry_window_handle(event.widget)
        )
        set_windows_ime_composition_font(
            self.name_entry_window_handle(event.widget),
            self.ui_font_family,
            11,
        )

    def handle_name_ime_key_release(self, event: tk.Event) -> None:
        if event.keysym in {
            "Alt_L",
            "Alt_R",
            "Control_L",
            "Control_R",
            "Shift_L",
            "Shift_R",
            "Return",
            "KP_Enter",
            "Tab",
        }:
            return
        self.schedule_name_ime_commit(event.widget)

    def handle_name_ime_commit_event(self, event: tk.Event) -> None:
        self.commit_name_entry_ime(event.widget)

    def handle_name_ime_modifier_press(self, event: tk.Event) -> None:
        # Ctrl+Enter must see the final Korean syllable before its shortcut
        # binding reads the StringVar.  Cancelling and inserting is synchronous.
        self.take_name_entry_ime(event.widget)

    def bind_name_entry_ime(self, entry: ttk.Entry) -> None:
        entry.bind("<FocusIn>", self.handle_name_ime_focus_in, add="+")
        entry.bind("<KeyRelease>", self.handle_name_ime_key_release, add="+")
        entry.bind("<KeyPress-Control_L>", self.handle_name_ime_modifier_press, add="+")
        entry.bind("<KeyPress-Control_R>", self.handle_name_ime_modifier_press, add="+")
        entry.bind("<KeyPress-Alt_L>", self.handle_name_ime_modifier_press, add="+")
        entry.bind("<KeyPress-Alt_R>", self.handle_name_ime_modifier_press, add="+")
        entry.bind("<FocusOut>", self.handle_name_ime_commit_event, add="+")

    def add_new_block_name_field(
        self,
        parent: tk.Misc,
        *,
        row: int,
        label_style: str,
        entry_columnspan: int = 1,
        pady: int = 5,
    ) -> ttk.Entry:
        """Place the shared new-block name beside the form fields that create it."""
        ttk.Label(parent, text="학생 이름", style=label_style).grid(
            row=row,
            column=0,
            sticky="w",
            padx=(0, 10),
            pady=pady,
        )
        entry = ttk.Entry(
            parent,
            textvariable=self.new_block_name_var,
            style="StudentNameOff.TEntry",
        )
        entry.grid(
            row=row,
            column=1,
            columnspan=entry_columnspan,
            sticky="ew",
            pady=pady,
        )
        self.bind_name_entry_ime(entry)
        self.new_block_name_entries.append(entry)
        if not hasattr(self, "new_block_name_entry"):
            self.new_block_name_entry = entry
        return entry

    def create_scrollable_test_page(self, tab_text: str) -> ttk.Frame:
        """Create a fixed test-tab viewport whose form scrolls when height is tight."""
        page = ttk.Frame(self.test_tabs, style="Panel.TFrame")
        self.test_tabs.add(page, text=tab_text, padding=(0, 0), sticky="nsew")
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)

        canvas = tk.Canvas(
            page,
            bg=self.colors["panel"],
            highlightthickness=0,
            bd=0,
            width=1,
            height=1,
        )
        scrollbar = ttk.Scrollbar(page, orient="vertical", command=canvas.yview)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scrollbar.set)

        content = ttk.Frame(canvas, style="Panel.TFrame", padding=(20, 18))
        window_id = canvas.create_window((0, 0), window=content, anchor="nw")

        def sync_scroll_region(_event: tk.Event | None = None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def sync_content_width(event: tk.Event) -> None:
            canvas.itemconfigure(window_id, width=max(1, event.width))

        def scroll_page(event: tk.Event) -> str:
            canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
            return "break"

        content.bind("<Configure>", sync_scroll_region, add="+")
        canvas.bind("<Configure>", sync_content_width, add="+")
        canvas.bind("<MouseWheel>", scroll_page, add="+")
        page.bind("<MouseWheel>", scroll_page, add="+")
        if not hasattr(self, "test_page_canvases"):
            self.test_page_canvases: dict[str, tk.Canvas] = {}
        self.test_page_canvases[tab_text] = canvas
        return content

    def create_scrollable_builder_page(self, tab_text: str) -> tuple[ttk.Frame, tk.Canvas]:
        """Create a fixed outer-tab viewport for compact operator forms."""
        page = ttk.Frame(self.builder_notebook, style="Panel.TFrame")
        self.builder_notebook.add(page, text=tab_text, padding=(0, 0), sticky="nsew")
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)

        canvas = tk.Canvas(
            page,
            bg=self.colors["panel"],
            highlightthickness=0,
            bd=0,
            width=1,
            height=1,
        )
        scrollbar = ttk.Scrollbar(page, orient="vertical", command=canvas.yview)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scrollbar.set)

        content = ttk.Frame(canvas, style="Panel.TFrame", padding=(20, 18))
        window_id = canvas.create_window((0, 0), window=content, anchor="nw")

        def sync_scroll_region(_event: tk.Event | None = None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def sync_content_width(event: tk.Event) -> None:
            canvas.itemconfigure(window_id, width=max(1, event.width))

        def scroll_page(event: tk.Event) -> str:
            canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
            return "break"

        content.bind("<Configure>", sync_scroll_region, add="+")
        canvas.bind("<Configure>", sync_content_width, add="+")
        canvas.bind("<MouseWheel>", scroll_page, add="+")
        page.bind("<MouseWheel>", scroll_page, add="+")
        return content, canvas

    def build_ui_reset(self) -> None:
        """Build the operator-facing workspace while keeping the print API intact."""
        colors = self.colors
        outer = ttk.Frame(self.root, style="Shell.TFrame", padding=(12, 10, 12, 9))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        header = ttk.Frame(outer, style="Surface.TFrame", padding=(14, 9))
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.header_frame = header
        header.columnconfigure(0, weight=1)
        header.columnconfigure(1, weight=0)
        ttk.Label(header, text="빠른 인쇄기  /  QUICK PRINT", style="Kicker.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(header, text="빠른 인쇄기", style="PageTitle.TLabel").grid(
            row=1, column=0, sticky="w", pady=(3, 0)
        )
        ttk.Label(
            header,
            text="자료를 고르고 범위를 정한 뒤, 오른쪽 큐에서 순서만 확인하세요.",
            style="SectionHint.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.status_badge = tk.Label(
            header,
            text="",
            bg=colors["success_soft"],
            fg=colors["success"],
            padx=13,
            pady=5,
            bd=0,
            font=(self.ui_font_family, 11, "bold"),
            highlightthickness=1,
            highlightbackground=colors["success"],
        )
        self.status_badge.grid(row=0, column=1, sticky="e", padx=(18, 0))
        self.refresh_status_badge()
        self.focus_context_badge = tk.Label(
            header,
            textvariable=self.focus_context_var,
            bg=colors["accent_soft"],
            fg=colors["accent"],
            padx=12,
            pady=0,
            bd=0,
            font=(self.ui_font_family, 10, "bold"),
            highlightthickness=1,
            highlightbackground=colors["accent"],
        )
        self.focus_context_badge.grid(row=1, column=1, sticky="e", padx=(18, 0))
        self.device_status_badge = tk.Label(
            header,
            text="",
            bg=colors["accent_soft"],
            fg=colors["accent"],
            padx=12,
            pady=0,
            bd=0,
            font=(self.ui_font_family, 10, "bold"),
            justify="right",
            wraplength=760,
            highlightthickness=1,
            highlightbackground=colors["accent"],
        )
        self.device_status_badge.grid(
            row=2,
            column=1,
            sticky="e",
            padx=(18, 0),
        )
        self.refresh_device_status_badge()

        def resize_device_status_badge(event: tk.Event) -> None:
            # Keep the normal maximized header at its original three-row
            # height.  On narrower windows, wrap only the long printer line so
            # it cannot cover the left-side operator hint.
            target_wrap = 1150 if int(event.width) >= 1800 else 760
            if int(float(self.device_status_badge.cget("wraplength"))) != target_wrap:
                self.device_status_badge.configure(wraplength=target_wrap)

        header.bind("<Configure>", resize_device_status_badge, add="+")

        option_bar = ttk.Frame(outer, style="Inset.TFrame", padding=(8, 4))
        option_bar.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.resource_estimate_bar = option_bar
        ttk.Label(option_bar, text="양면 기본", style="InsetTitle.TLabel").pack(side="left", padx=(0, 14))
        self.add_toggle_button(
            option_bar,
            "구분지",
            self.separator_page_var,
            command=self.update_preview,
            help_text="켜짐: 각 블록 뒤에 중앙에 '구분지'가 크게 표시된 A4 가로 한 장을 추가합니다.",
        ).pack(side="left", padx=(0, 14))
        self.add_toggle_button(
            option_bar,
            "이름 구분지",
            self.name_separator_page_var,
            command=self.handle_name_separator_toggle,
            help_text="켜짐: 이름 묶음 앞에 학생 이름이 상단 중앙에 표시된 A4 세로 한 장을 추가합니다.",
        ).pack(side="left", padx=(0, 14))
        ttk.Separator(option_bar, orient="vertical").pack(side="left", fill="y", padx=(0, 14))
        ttk.Label(option_bar, text="전송 묶음", style="InsetText.TLabel").pack(side="left", padx=(0, 6))
        ttk.Spinbox(
            option_bar, from_=1, to=100, textvariable=self.queue_batch_size_var, width=5
        ).pack(side="left", padx=(0, 4))
        ttk.Label(option_bar, text="개", style="InsetText.TLabel").pack(side="left", padx=(0, 12))
        ttk.Label(option_bar, text="재충전 기준", style="InsetText.TLabel").pack(side="left", padx=(0, 6))
        ttk.Spinbox(
            option_bar, from_=0, to=100, textvariable=self.queue_refill_threshold_var, width=5
        ).pack(side="left", padx=(0, 4))
        ttk.Label(option_bar, text="건 이하", style="InsetText.TLabel").pack(side="left")
        self.resource_estimate_badge = tk.Label(
            option_bar,
            textvariable=self.resource_estimate_var,
            bg=colors["panel_soft"],
            fg=colors["muted"],
            padx=8,
            pady=2,
            bd=0,
            font=(self.ui_font_family, 9, "bold"),
            cursor="hand2",
            highlightthickness=1,
            highlightbackground=colors["border"],
        )
        self.resource_estimate_badge.pack(side="right", padx=(12, 0))
        self.resource_estimate_badge.bind(
            "<Button-1>",
            self.show_resource_estimate_details,
            add="+",
        )
        option_bar.bind(
            "<Configure>",
            self.refresh_resource_estimate_display,
            add="+",
        )

        self.main_frame = main = ttk.Frame(outer, style="Shell.TFrame")
        main.grid(row=2, column=0, sticky="nsew", pady=(0, 6))
        # Keep the source builder and print queue in the requested 3:7 split.
        # Both columns may still grow with the window, so narrow displays do not
        # force the left card or the queue off-screen.
        # Keep the requested 3:7 growth ratio while reserving enough room for
        # all five one-line builder tabs on narrower/high-DPI displays.
        main.columnconfigure(0, weight=3, minsize=640)
        main.columnconfigure(1, weight=7, minsize=640)
        main.rowconfigure(0, weight=1)

        self.builder_frame = builder_shell = ttk.Frame(self.main_frame, style="BuilderWorkspace.TFrame", width=1)
        builder_shell.grid(row=0, column=0, sticky="nsew", padx=(0, 2))
        builder_shell.grid_propagate(False)
        builder_shell.columnconfigure(0, weight=1)
        builder_shell.rowconfigure(0, weight=1)
        builder_shell.rowconfigure(1, weight=0)
        self.builder_notebook = ttk.Notebook(builder_shell, style="Builder.TNotebook")
        self.builder_notebook.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)

        test_builder = ttk.Frame(self.builder_notebook, style="Panel.TFrame", padding=(10, 8))
        self.builder_notebook.add(test_builder, text="테스트")
        test_builder.columnconfigure(0, weight=1)
        test_builder.rowconfigure(0, weight=1)
        self.test_tabs = ttk.Notebook(test_builder, style="Material.TNotebook")
        self.test_tabs.grid(row=0, column=0, sticky="nsew")

        builder = self.create_scrollable_test_page("단원평가")
        builder.columnconfigure(1, weight=1)
        builder.columnconfigure(3, weight=1)
        ttk.Label(builder, text="단원평가 범위", style="SectionTitle.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w"
        )
        ttk.Label(
            builder,
            text="과목, 레벨, 회차를 고르면 시작과 끝 범위가 채워집니다.",
            style="SectionHint.TLabel",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 18))
        ttk.Label(builder, text="과목", style="Field.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 8), pady=6
        )
        self.subject_combo = ttk.Combobox(builder, textvariable=self.subject_var, state="readonly")
        self.subject_combo.grid(row=2, column=1, columnspan=3, sticky="ew", pady=6)
        ttk.Label(builder, text="레벨", style="Field.TLabel").grid(
            row=3, column=0, sticky="w", padx=(0, 8), pady=6
        )
        self.level_combo = ttk.Combobox(builder, textvariable=self.level_var, state="readonly")
        self.level_combo.grid(row=3, column=1, sticky="ew", padx=(0, 10), pady=6)
        ttk.Label(builder, text="회차", style="Field.TLabel").grid(
            row=3, column=2, sticky="w", padx=(0, 8), pady=6
        )
        self.round_combo = ttk.Combobox(builder, textvariable=self.round_var, state="readonly")
        self.round_combo.grid(row=3, column=3, sticky="ew", pady=6)
        ttk.Label(builder, text="시작", style="Field.TLabel").grid(
            row=4, column=0, sticky="w", padx=(0, 8), pady=6
        )
        self.start_combo = ttk.Combobox(builder, textvariable=self.start_var, state="readonly")
        self.start_combo.grid(row=4, column=1, columnspan=3, sticky="ew", pady=6)
        ttk.Label(builder, text="끝", style="Field.TLabel").grid(
            row=5, column=0, sticky="w", padx=(0, 8), pady=6
        )
        self.end_combo = ttk.Combobox(builder, textvariable=self.end_var, state="readonly")
        self.end_combo.grid(row=5, column=1, columnspan=3, sticky="ew", pady=6)

        ttk.Label(builder, text="개별 파일", style="Field.TLabel").grid(
            row=6, column=0, sticky="w", padx=(0, 8), pady=6
        )
        self.unit_selected_numbers_entry = ttk.Entry(
            builder, textvariable=self.unit_selected_numbers_var
        )
        self.unit_selected_numbers_entry.grid(row=6, column=1, columnspan=3, sticky="ew", pady=6)
        ttk.Label(
            builder,
            text="예: 1-3, 6, 7-9 (공백 무관) · 지정 파일만 선택",
            style="SectionHint.TLabel",
        ).grid(row=7, column=1, columnspan=3, sticky="w", pady=(0, 2))
        self.unit_new_block_name_entry = self.add_new_block_name_field(
            builder,
            row=8,
            label_style="Field.TLabel",
            entry_columnspan=3,
            pady=6,
        )

        builder_action = ttk.Frame(builder, style="Inset.TFrame", padding=(12, 12))
        builder_action.grid(row=9, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        builder_action.columnconfigure(3, weight=1)
        ttk.Label(builder_action, text="부수", style="InsetTitle.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 7)
        )
        self.block_copies_spinbox = ttk.Spinbox(
            builder_action, from_=1, to=9999, textvariable=self.block_copies_var, width=7
        )
        self.block_copies_spinbox.grid(
            row=0, column=1, sticky="w", padx=(0, 8)
        )
        self.bundle_button = self.add_toggle_button(
            builder_action,
            "묶음",
            self.bundle_var,
            command=self.update_builder_status,
            help_text="켜짐: 1부씩 묶어서 전체 범위를 반복 전송합니다. 꺼짐: 각 PDF를 지정 부수만큼 전송합니다.",
        )
        self.bundle_button.grid(row=0, column=2, sticky="w", padx=(0, 8))
        add_unit_button = ttk.Button(builder_action, text="블록 추가", style="Primary.TButton", command=self.add_current_block)
        self.add_unit_button = add_unit_button
        add_unit_button.grid(
            row=1, column=0, columnspan=4, sticky="ew", pady=(8, 0)
        )
        self.bind_block_add_button(add_unit_button, self.add_current_block)
        korean_label(builder, textvariable=self.builder_status_var, style="SectionHint.TLabel", wraplength=390).grid(
            row=10, column=0, columnspan=4, sticky="w", pady=(14, 0)
        )
        self.unit_selected_numbers_entry.bind("<KeyRelease>", lambda _event: self.update_builder_status(), add="+")

        for kind in TEST_KIND_ORDER:
            self.build_test_catalog_builder(kind)

        material_builder = ttk.Frame(self.builder_notebook, style="Panel.TFrame", padding=(18, 16))
        self.builder_notebook.add(material_builder, text="교재자료")
        self.build_material_builder_responsive(material_builder)

        blank_review, self.blank_review_canvas = self.create_scrollable_builder_page(
            "백지리뷰/OMR"
        )
        blank_review.columnconfigure(0, weight=1)
        ttk.Label(blank_review, text="백지리뷰 / OMR", style="SectionTitle.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            blank_review,
            text="자주 쓰는 고정 양식을 부수만 정해 바로 인쇄합니다.",
            style="SectionHint.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(6, 18))
        blank_review_file = ttk.Frame(blank_review, style="Inset.TFrame", padding=(12, 12))
        blank_review_file.grid(row=2, column=0, sticky="ew")
        blank_review_file.columnconfigure(0, weight=1)
        ttk.Label(blank_review_file, text="백지 리뷰 짧.pdf", style="InsetTitle.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        blank_review_path = find_blank_review_pdf()
        if blank_review_path.is_file():
            try:
                blank_review_pages = get_pdf_page_count(blank_review_path)
                blank_review_text = f"{blank_review_pages}쪽 · 세로 고정 · 양면"
            except Exception:
                blank_review_text = "세로 고정 · 양면"
        else:
            blank_review_text = "파일을 찾을 수 없습니다."
        self.blank_review_status_var.set(blank_review_text)
        ttk.Label(
            blank_review_file,
            textvariable=self.blank_review_status_var,
            style="InsetText.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(5, 0))
        blank_review_action = ttk.Frame(blank_review, style="Inset.TFrame", padding=(12, 12))
        blank_review_action.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        blank_review_action.columnconfigure(3, weight=1)
        ttk.Label(blank_review_action, text="부수", style="InsetTitle.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 7)
        )
        self.blank_review_copies_spinbox = ttk.Spinbox(
            blank_review_action,
            from_=1,
            to=9999,
            textvariable=self.blank_review_copies_var,
            width=7,
        )
        self.blank_review_copies_spinbox.grid(row=0, column=1, sticky="w", padx=(0, 8))
        self.blank_review_add_button = ttk.Button(
            blank_review_action,
            text="백지리뷰 추가",
            style="Primary.TButton",
            command=self.add_blank_review_block,
        )
        self.blank_review_add_button.grid(row=0, column=3, sticky="e")
        self.bind_block_add_button(self.blank_review_add_button, self.add_blank_review_block)

        omr_file = ttk.Frame(blank_review, style="Inset.TFrame", padding=(12, 12))
        omr_file.grid(row=4, column=0, sticky="ew", pady=(18, 0))
        omr_file.columnconfigure(0, weight=1)
        ttk.Label(omr_file, text="OMR 내신.pdf", style="InsetTitle.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        omr_path = find_omr_pdf()
        if omr_path.is_file():
            try:
                omr_pages = get_pdf_page_count(omr_path)
                omr_text = f"{omr_pages}쪽 · 가로 고정 · 양면"
            except Exception:
                omr_text = "가로 고정 · 양면"
        else:
            omr_text = "파일을 찾을 수 없습니다."
        self.omr_status_var.set(omr_text)
        ttk.Label(
            omr_file,
            textvariable=self.omr_status_var,
            style="InsetText.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(5, 0))
        omr_action = ttk.Frame(blank_review, style="Inset.TFrame", padding=(12, 12))
        omr_action.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        omr_action.columnconfigure(3, weight=1)
        ttk.Label(omr_action, text="부수", style="InsetTitle.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 7)
        )
        self.omr_copies_spinbox = ttk.Spinbox(
            omr_action,
            from_=1,
            to=9999,
            textvariable=self.omr_copies_var,
            width=7,
        )
        self.omr_copies_spinbox.grid(row=0, column=1, sticky="w", padx=(0, 8))
        self.omr_add_button = ttk.Button(
            omr_action,
            text="OMR 추가",
            style="Primary.TButton",
            command=self.add_omr_block,
        )
        self.omr_add_button.grid(row=0, column=3, sticky="e")
        self.bind_block_add_button(self.omr_add_button, self.add_omr_block)

        custom = ttk.Frame(self.builder_notebook, style="Panel.TFrame", padding=(20, 18))
        self.builder_notebook.add(custom, text="PDF파일")
        custom.columnconfigure(0, weight=1)
        custom.rowconfigure(2, weight=0)
        ttk.Label(custom, text="PDF 직접 인쇄", style="SectionTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            custom,
            text="파일을 끌어 놓거나 선택하면 하나의 인쇄 블록으로 묶습니다.",
            style="SectionHint.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(6, 16))
        custom_pdf_list_frame = ttk.Frame(custom, style="Inset.TFrame", height=300)
        self.custom_pdf_list_frame = custom_pdf_list_frame
        custom_pdf_list_frame.grid(row=2, column=0, sticky="ew")
        custom_pdf_list_frame.grid_propagate(False)
        custom_pdf_list_frame.columnconfigure(0, weight=1)
        custom_pdf_list_frame.rowconfigure(0, weight=1)
        self.custom_drop_list = tk.Listbox(
            custom_pdf_list_frame,
            height=8,
            activestyle="none",
            selectmode=tk.EXTENDED,
            exportselection=False,
            relief="flat",
            bg=colors["panel"],
            fg=colors["text"],
            highlightthickness=1,
            highlightbackground=colors["border"],
            selectbackground=colors["accent_soft"],
            selectforeground=colors["text"],
            font=(self.ui_font_family, 11),
        )
        custom_pdf_scroll = ttk.Scrollbar(custom_pdf_list_frame, orient="vertical", command=self.custom_drop_list.yview)
        self.custom_drop_list.configure(yscrollcommand=custom_pdf_scroll.set)
        self.custom_drop_list.grid(row=0, column=0, sticky="nsew")
        custom_pdf_scroll.grid(row=0, column=1, sticky="ns")
        self.register_custom_drop_target(custom_pdf_list_frame)
        self.register_custom_drop_target(self.custom_drop_list)
        custom_meta = ttk.Frame(custom, style="Inset.TFrame", padding=(10, 10))
        custom_meta.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(custom_meta, text="부수", style="InsetTitle.TLabel").pack(side="left", padx=(0, 7))
        self.custom_copies_spinbox = ttk.Spinbox(
            custom_meta, from_=1, to=9999, textvariable=self.custom_copies_var, width=7
        )
        self.custom_copies_spinbox.pack(side="left", padx=(0, 8))
        self.custom_bundle_button = self.add_toggle_button(
            custom_meta,
            "묶음",
            self.custom_bundle_var,
            help_text="켜짐: 선택한 PDF 묶음을 1부씩 반복합니다. 꺼짐: 각 PDF를 지정 부수만큼 전송합니다.",
        )
        self.custom_bundle_button.pack(side="left")
        custom_route = ttk.Frame(custom, style="Inset.TFrame", padding=(10, 8))
        custom_route.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(custom_route, text="프린터 프로필", style="InsetTitle.TLabel").pack(side="left", padx=(0, 10))
        self.custom_printer_route_combo = ttk.Combobox(
            custom_route,
            textvariable=self.custom_printer_route_var,
            values=CUSTOM_PRINTER_ROUTE_CHOICES,
            state="readonly",
        )
        self.custom_printer_route_combo.pack(side="left", fill="x", expand=True)
        custom_name = ttk.Frame(custom, style="Inset.TFrame", padding=(10, 6))
        custom_name.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        custom_name.columnconfigure(1, weight=1)
        self.custom_new_block_name_entry = self.add_new_block_name_field(
            custom_name,
            row=0,
            label_style="InsetTitle.TLabel",
        )
        custom_actions = ttk.Frame(custom, style="Panel.TFrame")
        custom_actions.grid(row=6, column=0, sticky="ew", pady=(10, 0))
        custom_actions.columnconfigure(0, weight=1)
        custom_actions.columnconfigure(1, weight=1)
        self.custom_pdf_file_button = ttk.Button(
            custom_actions, text="파일 선택", style="SoftAction.TButton", command=self.choose_custom_pdfs
        )
        self.custom_pdf_file_button.grid(
            row=0, column=0, sticky="ew", padx=(0, 6)
        )
        self.custom_pdf_clear_button = ttk.Button(
            custom_actions, text="목록 비우기", style="SoftAction.TButton", command=self.clear_custom_pdfs
        )
        self.custom_pdf_clear_button.grid(
            row=0, column=1, sticky="ew"
        )
        add_custom_button = ttk.Button(
            custom_actions, text="PDF 블록 추가", style="Primary.TButton", command=self.add_custom_pdf_block
        )
        self.custom_pdf_add_button = add_custom_button
        add_custom_button.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.bind_block_add_button(add_custom_button, self.add_custom_pdf_block)
        self.register_custom_drop_target(custom)

        order_builder = ttk.Frame(self.builder_notebook, style="Panel.TFrame", padding=(20, 18))
        self.builder_notebook.add(order_builder, text="대량주문서")
        order_builder.columnconfigure(0, weight=1)
        ttk.Label(order_builder, text="대량 주문서", style="SectionTitle.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            order_builder,
            text="CSV 한 행을 인쇄 블록 하나로 검증해 작업 큐에 추가합니다.",
            style="SectionHint.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(6, 16))
        order_list_frame = ttk.Frame(order_builder, style="Inset.TFrame", height=300)
        order_list_frame.grid(row=2, column=0, sticky="ew")
        order_list_frame.grid_propagate(False)
        order_list_frame.columnconfigure(0, weight=1)
        order_list_frame.rowconfigure(0, weight=1)
        self.order_csv_list = tk.Listbox(
            order_list_frame,
            height=8,
            activestyle="none",
            selectmode=tk.EXTENDED,
            exportselection=False,
            relief="flat",
            bg=colors["panel"],
            fg=colors["text"],
            highlightthickness=1,
            highlightbackground=colors["border"],
            selectbackground=colors["accent_soft"],
            selectforeground=colors["text"],
            font=(self.ui_font_family, 10),
        )
        order_csv_scroll = ttk.Scrollbar(order_list_frame, orient="vertical", command=self.order_csv_list.yview)
        self.order_csv_list.configure(yscrollcommand=order_csv_scroll.set)
        self.order_csv_list.grid(row=0, column=0, sticky="nsew")
        order_csv_scroll.grid(row=0, column=1, sticky="ns")
        korean_label(
            order_builder,
            textvariable=self.order_csv_status_var,
            style="SectionHint.TLabel",
            wraplength=430,
        ).grid(row=3, column=0, sticky="w", pady=(10, 0))
        order_actions = ttk.Frame(order_builder, style="Panel.TFrame")
        order_actions.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        for column in range(3):
            order_actions.columnconfigure(column, weight=1)
        self.order_csv_choose_button = ttk.Button(
            order_actions,
            text="CSV 선택",
            style="SoftAction.TButton",
            command=self.choose_order_csv,
        )
        self.order_csv_choose_button.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.order_csv_template_button = ttk.Button(
            order_actions,
            text="양식 저장",
            style="SoftAction.TButton",
            command=self.save_order_csv_template,
        )
        self.order_csv_template_button.grid(row=0, column=1, sticky="ew", padx=5)
        self.order_csv_clear_button = ttk.Button(
            order_actions,
            text="목록 비우기",
            style="SoftAction.TButton",
            command=self.clear_order_csv,
        )
        self.order_csv_clear_button.grid(row=0, column=2, sticky="ew", padx=(5, 0))
        self.order_csv_add_button = ttk.Button(
            order_actions,
            text="주문서 블록 추가",
            style="Primary.TButton",
            command=self.add_order_csv_blocks,
        )
        self.order_csv_add_button.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        self.bind_block_add_button(self.order_csv_add_button, self.add_order_csv_blocks)

        # Reserve a stable strip below the builder for context-sensitive keyboard
        # help.  The notebook can change content without changing this strip's
        # height or pushing the rest of the workspace around.
        self.shortcut_help_frame = ttk.Frame(builder_shell, style="Inset.TFrame", height=390)
        self.shortcut_help_frame.grid(row=1, column=0, sticky="ew", padx=1, pady=(8, 1))
        self.shortcut_help_frame.grid_propagate(False)
        self.shortcut_help_frame.columnconfigure(0, weight=1)
        self.shortcut_help_frame.rowconfigure(1, weight=1)
        shortcut_help_head = ttk.Frame(self.shortcut_help_frame, style="Inset.TFrame")
        shortcut_help_head.grid(row=0, column=0, sticky="ew", padx=10, pady=(7, 0))
        shortcut_help_head.columnconfigure(0, weight=1)
        ttk.Label(shortcut_help_head, text="단축키 도움말", style="ShortcutHelpTitle.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            shortcut_help_head,
            textvariable=self.shortcut_help_context_var,
            style="ShortcutHelpContext.TLabel",
        ).grid(row=0, column=1, sticky="e")
        self.shortcut_help_label = ttk.Label(
            self.shortcut_help_frame,
            textvariable=self.shortcut_help_var,
            style="ShortcutHelpText.TLabel",
            anchor="nw",
            justify="left",
            wraplength=420,
        )
        self.shortcut_help_label.grid(row=1, column=0, sticky="nsew", padx=10, pady=(4, 7))

        right = ttk.Frame(main, style="Shell.TFrame", width=1)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_propagate(False)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=3)
        right.rowconfigure(1, weight=1)

        self.queue_frame = queue = ttk.Frame(right, style="QueueWorkspace.TFrame", padding=(15, 14))
        queue.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        queue.columnconfigure(0, weight=1)
        queue.rowconfigure(1, weight=1)
        queue_head = ttk.Frame(queue, style="Surface.TFrame")
        queue_head.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        queue_head.columnconfigure(0, weight=1)
        ttk.Label(queue_head, text="작업 큐", style="SectionTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(queue_head, text="순서를 드래그하거나 선택한 블록을 수정하세요.", style="SectionHint.TLabel").grid(
            row=1, column=0, sticky="w", pady=(3, 0)
        )
        queue_button_row = ttk.Frame(queue_head, style="Surface.TFrame")
        queue_button_row.grid(row=0, column=1, rowspan=2, sticky="e")
        ttk.Button(queue_button_row, text="위로", style="SoftAction.TButton", command=lambda: self.move_selected_block(-1)).pack(side="left", padx=(0, 5))
        ttk.Button(queue_button_row, text="아래로", style="SoftAction.TButton", command=lambda: self.move_selected_block(1)).pack(side="left", padx=(0, 5))
        ttk.Button(queue_button_row, text="가나다 정렬", style="SoftAction.TButton", command=self.sort_blocks_by_name).pack(side="left", padx=(0, 5))
        ttk.Button(queue_button_row, text="삭제", style="DangerAction.TButton", command=self.delete_selected_block).pack(side="left", padx=(0, 5))
        ttk.Button(queue_button_row, text="전체 비우기", style="SoftAction.TButton", command=self.clear_blocks).pack(side="left", padx=(0, 5))
        self.queue_tools_button = ttk.Button(queue_button_row, text="큐 도구", style="SoftAction.TButton", command=self.show_queue_tools_menu)
        self.queue_tools_button.pack(side="left")

        self.queue_canvas = tk.Canvas(
            queue,
            bg=colors["panel_soft"],
            highlightthickness=1,
            highlightbackground=colors["border"],
            highlightcolor=colors["accent"],
            bd=0,
            width=1,
            takefocus=True,
        )
        self.queue_canvas.grid(row=1, column=0, sticky="nsew")
        queue_scroll = ttk.Scrollbar(queue, orient="vertical", command=self.queue_canvas.yview)
        queue_scroll.grid(row=1, column=1, sticky="ns", padx=(6, 0))
        self.queue_scrollbar = queue_scroll
        self.queue_canvas.configure(yscrollcommand=self.on_queue_canvas_scroll)
        self.queue_canvas.bind("<Configure>", self.on_queue_canvas_configure)
        self.queue_canvas.bind("<ButtonPress-1>", self.on_queue_drag_start)
        self.queue_canvas.bind("<B1-Motion>", self.on_queue_drag_motion)
        self.queue_canvas.bind("<ButtonRelease-1>", self.on_queue_drag_release)
        self.queue_canvas.bind("<Double-Button-1>", self.on_queue_double_click)
        self.queue_canvas.bind("<Button-3>", self.on_queue_context_menu)
        self.queue_canvas.bind("<Enter>", self.bind_queue_mousewheel)
        self.queue_canvas.bind("<Leave>", self.unbind_queue_mousewheel)

        self.queue_edit_panel = edit_panel = ttk.Frame(queue, style="Inset.TFrame", padding=(10, 8))
        edit_panel.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        edit_top = ttk.Frame(edit_panel, style="Inset.TFrame")
        edit_top.pack(fill="x")
        ttk.Label(edit_top, text="선택 블록 수정", style="InsetTitle.TLabel").pack(side="left", padx=(0, 8))
        self.queue_edit_status_label = ttk.Label(edit_top, text="선택 없음", style="InsetText.TLabel")
        self.queue_edit_status_label.pack(side="left")
        self.edit_save_button = ttk.Button(
            edit_top, text="수정 저장", style="Primary.TButton", command=self.save_editor_from_keyboard
        )
        self.edit_save_button.pack(side="right")
        self.edit_bundle_button = self.add_toggle_button(
            edit_top, "묶음", self.edit_bundle_var, help_text="선택한 블록의 묶음 전송 방식을 바꿉니다."
        )
        self.edit_bundle_button.pack(side="right", padx=(0, 8))
        self.edit_copies_spinbox = ttk.Spinbox(edit_top, from_=1, to=9999, textvariable=self.edit_copies_var, width=7)
        self.edit_copies_spinbox.pack(side="right", padx=(0, 8))
        ttk.Label(edit_top, text="부수", style="InsetText.TLabel").pack(side="right", padx=(0, 6))
        self.edit_printer_route_combo = ttk.Combobox(
            edit_top,
            textvariable=self.edit_printer_route_var,
            values=CUSTOM_PRINTER_ROUTE_CHOICES,
            state="readonly",
            width=10,
        )
        self.edit_printer_route_label = ttk.Label(edit_top, text="프린터", style="InsetText.TLabel")
        edit_name_separator = ttk.Frame(edit_panel, style="Inset.TFrame")
        edit_name_separator.pack(fill="x", pady=(7, 0))
        ttk.Label(
            edit_name_separator,
            text="이름 구분지",
            style="InsetText.TLabel",
        ).pack(side="left", padx=(0, 8))
        self.edit_name_separator_entry = ttk.Entry(
            edit_name_separator,
            textvariable=self.edit_name_separator_var,
        )
        self.edit_name_separator_entry.pack(side="left", fill="x", expand=True)
        self.bind_name_entry_ime(self.edit_name_separator_entry)
        edit_meta = ttk.Frame(edit_panel, style="Inset.TFrame")
        edit_meta.pack(fill="x", pady=(7, 0))
        ttk.Label(edit_meta, text="과목", style="InsetText.TLabel").pack(side="left", padx=(0, 6))
        self.edit_subject_combo = ttk.Combobox(edit_meta, textvariable=self.edit_subject_var, state="readonly", width=14)
        self.edit_subject_combo.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.edit_level_label = ttk.Label(edit_meta, text="레벨", style="InsetText.TLabel")
        self.edit_level_label.pack(side="left", padx=(0, 6))
        self.edit_level_combo = ttk.Combobox(edit_meta, textvariable=self.edit_level_var, state="readonly", width=7)
        self.edit_level_combo.pack(side="left", padx=(0, 8))
        self.edit_round_label = ttk.Label(edit_meta, text="회차", style="InsetText.TLabel")
        self.edit_round_label.pack(side="left", padx=(0, 6))
        self.edit_round_combo = ttk.Combobox(edit_meta, textvariable=self.edit_round_var, state="readonly", width=11)
        self.edit_round_combo.pack(side="left")
        edit_range = ttk.Frame(edit_panel, style="Inset.TFrame")
        edit_range.pack(fill="x", pady=(7, 0))
        ttk.Label(edit_range, text="시작", style="InsetText.TLabel").pack(side="left", padx=(0, 6))
        self.edit_start_combo = ttk.Combobox(edit_range, textvariable=self.edit_start_var, state="readonly", width=16)
        self.edit_start_combo.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Label(edit_range, text="끝", style="InsetText.TLabel").pack(side="left", padx=(0, 6))
        self.edit_end_combo = ttk.Combobox(edit_range, textvariable=self.edit_end_var, state="readonly", width=16)
        self.edit_end_combo.pack(side="left", fill="x", expand=True)

        edit_selected_row = ttk.Frame(edit_panel, style="Inset.TFrame")
        edit_selected_row.pack(fill="x", pady=(7, 0))
        self.edit_selected_numbers_label = ttk.Label(
            edit_selected_row, text="개별 파일", style="InsetText.TLabel"
        )
        self.edit_selected_numbers_label.pack(side="left", padx=(0, 6))
        self.edit_selected_numbers_entry = ttk.Entry(
            edit_selected_row,
            textvariable=self.edit_selected_numbers_var,
        )
        self.edit_selected_numbers_entry.pack(side="left", fill="x", expand=True)
        self.edit_selected_numbers_hint_label = ttk.Label(
            edit_panel,
            text="예: 1-3, 6, 7-9 (공백 무관) · 지정 파일만 선택",
            style="InsetText.TLabel",
        )
        self.edit_selected_numbers_hint_label.pack(fill="x", pady=(3, 0))

        self.queue_status_slot = ttk.Frame(queue, style="Surface.TFrame")
        self.queue_status_slot.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.queue_status_slot.columnconfigure(0, weight=1)
        self.queue_status_label = ttk.Label(
            self.queue_status_slot, textvariable=self.queue_status_var, style="QueueMeta.TLabel", anchor="e"
        )
        self.queue_status_label.grid(row=0, column=0, sticky="e", pady=(7, 0))

        output = ttk.Frame(right, style="Surface.TFrame", padding=(10, 9))
        output.grid(row=1, column=0, sticky="nsew")
        output.columnconfigure(0, weight=1)
        output.rowconfigure(0, weight=1)
        self.output_notebook = ttk.Notebook(output)
        self.output_notebook.grid(row=0, column=0, sticky="nsew")
        self.detail_notebook = self.output_notebook
        self.preview_frame = preview = ttk.Frame(self.output_notebook, style="Surface.TFrame", padding=6)
        self.output_notebook.add(preview, text="미리보기")
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(0, weight=1)
        self.preview_text = Text(
            preview,
            wrap="none",
            height=4,
            bg=colors["panel_soft"],
            fg=colors["text"],
            relief="flat",
            padx=10,
            pady=6,
            font=("Consolas", 9),
            insertbackground=colors["accent"],
        )
        self.preview_text.grid(row=0, column=0, sticky="nsew")
        log_tab = ttk.Frame(self.output_notebook, style="Surface.TFrame", padding=6)
        self.output_notebook.add(log_tab, text="로그")
        log_tab.columnconfigure(0, weight=1)
        log_tab.rowconfigure(0, weight=1)
        self.log_text = Text(
            log_tab,
            wrap="word",
            height=4,
            bg=colors["log_bg"],
            fg=colors["log_text"],
            relief="flat",
            padx=10,
            pady=6,
            font=("Consolas", 9),
            insertbackground="#ffffff",
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")

        footer = ttk.Frame(outer, style="Shell.TFrame")
        footer.grid(row=3, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.current_work_var, style="FooterText.TLabel").grid(row=0, column=0, sticky="w")
        footer_actions = ttk.Frame(footer, style="Shell.TFrame")
        footer_actions.grid(row=0, column=1, sticky="e")
        ttk.Button(
            footer_actions,
            text="프린터 상태",
            style="SoftAction.TButton",
            command=self.open_device_monitor_dialog,
        ).pack(side="left", padx=(0, 7))
        self.device_resume_button = ttk.Button(
            footer_actions,
            text="해결 후 이어서",
            style="SoftAction.TButton",
            command=self.resume_device_print,
            state="disabled",
        )
        self.device_resume_button.pack(side="left", padx=(0, 7))
        ttk.Button(footer_actions, text="큐 비우기", style="SoftAction.TButton", command=self.clear_blocks).pack(side="left", padx=(0, 7))
        self.retry_button = ttk.Button(
            footer_actions,
            text="남은 작업 재시도",
            style="SoftAction.TButton",
            command=self.retry_remaining_jobs,
            state="disabled",
        )
        self.retry_button.pack(side="left", padx=(0, 7))
        self.cancel_print_button = ttk.Button(
            footer_actions,
            text="전송 중지",
            style="DangerAction.TButton",
            command=self.cancel_current_print,
            state="disabled",
        )
        self.cancel_print_button.pack(side="left", padx=(0, 7))
        self.start_print_button = ttk.Button(
            footer_actions, text="인쇄 시작", width=10,
            style="Primary.TButton", command=self.start_print_thread,
        )
        self.start_print_button.pack(side="left")

        self.subject_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_levels())
        self.level_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_rounds())
        self.round_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_files())
        self.start_combo.bind("<<ComboboxSelected>>", lambda _event: self.update_builder_status())
        self.end_combo.bind("<<ComboboxSelected>>", lambda _event: self.update_builder_status())
        self.edit_subject_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_edit_levels())
        self.edit_level_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_edit_rounds())
        self.edit_round_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_edit_files())
        self.builder_notebook.bind("<<NotebookTabChanged>>", self.handle_builder_tab_changed, add="+")
        self.builder_notebook.bind("<Configure>", self.handle_builder_notebook_configure, add="+")
        self.test_tabs.bind("<<NotebookTabChanged>>", self.handle_test_tab_changed, add="+")
        self.material_tabs.bind("<<NotebookTabChanged>>", self.handle_material_tab_changed, add="+")
        self.lesson_material_tabs.bind(
            "<<NotebookTabChanged>>",
            self.handle_lesson_material_tab_changed,
            add="+",
        )
        for kind in TEST_KIND_ORDER:
            self.refresh_test_catalog_options(kind)
        self.refresh_apex_material_options()
        self.refresh_able_material_options()
        self.refresh_training_pack_material_options()
        self.refresh_frequent_type_material_options()
        self.refresh_gyopyeong_material_options()
        self.refresh_selected_exam_material_options()
        self.refresh_textbook_material_options()
        self.root.after_idle(self.refresh_builder_tab_geometry)
        self.bind_shortcuts()
        self.refresh_name_separator_editor_state()
        self.refresh_print_action_buttons()

    def build_test_catalog_builder(self, kind: str) -> None:
        """Build one filtered test catalog page inside the Test tab."""
        page = self.create_scrollable_test_page(kind)
        page.columnconfigure(1, weight=1)
        page.columnconfigure(3, weight=1)
        filter_choices = test_filter_choices(kind)
        filter_label_text = test_filter_label(kind)

        variables: dict[str, tk.Variable] = {
            "subject": StringVar(),
            "level": StringVar(value=filter_choices[0]),
            "start": StringVar(),
            "end": StringVar(),
            "selected": StringVar(),
            "copies": IntVar(value=1),
            "bundle": BooleanVar(value=False),
            "status": StringVar(value=f"{kind} 자료를 선택하세요."),
        }
        self.test_builder_vars[kind] = variables

        ttk.Label(page, text=kind, style="SectionTitle.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w"
        )
        ttk.Label(
            page,
            text=f"과목과 {filter_label_text}을 고른 뒤 인쇄할 파일 범위를 선택하세요.",
            style="SectionHint.TLabel",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 18))

        ttk.Label(page, text="과목", style="Field.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 8), pady=6
        )
        subject_combo = ttk.Combobox(
            page, textvariable=variables["subject"], state="readonly"
        )
        subject_combo.grid(row=2, column=1, columnspan=3, sticky="ew", pady=6)

        filter_label_widget = ttk.Label(page, text=filter_label_text, style="Field.TLabel")
        filter_label_widget.grid(
            row=3, column=0, sticky="w", padx=(0, 8), pady=6
        )
        level_combo = ttk.Combobox(
            page,
            textvariable=variables["level"],
            state="readonly",
            values=filter_choices,
        )
        level_combo.grid(row=3, column=1, columnspan=3, sticky="ew", pady=6)

        ttk.Label(page, text="시작", style="Field.TLabel").grid(
            row=4, column=0, sticky="w", padx=(0, 8), pady=6
        )
        start_combo = ttk.Combobox(
            page, textvariable=variables["start"], state="readonly"
        )
        start_combo.grid(row=4, column=1, columnspan=3, sticky="ew", pady=6)

        ttk.Label(page, text="끝", style="Field.TLabel").grid(
            row=5, column=0, sticky="w", padx=(0, 8), pady=6
        )
        end_combo = ttk.Combobox(
            page, textvariable=variables["end"], state="readonly"
        )
        end_combo.grid(row=5, column=1, columnspan=3, sticky="ew", pady=6)

        ttk.Label(page, text="개별 파일", style="Field.TLabel").grid(
            row=6, column=0, sticky="w", padx=(0, 8), pady=6
        )
        selected_entry = ttk.Entry(page, textvariable=variables["selected"])
        selected_entry.grid(row=6, column=1, columnspan=3, sticky="ew", pady=6)
        ttk.Label(
            page,
            text="예: 1-3, 6, 7-9 (공백 무관) · 지정 파일만 선택",
            style="SectionHint.TLabel",
        ).grid(row=7, column=1, columnspan=3, sticky="w", pady=(0, 2))

        name_entry = self.add_new_block_name_field(
            page,
            row=8,
            label_style="Field.TLabel",
            entry_columnspan=3,
            pady=6,
        )

        action = ttk.Frame(page, style="Inset.TFrame", padding=(12, 12))
        action.grid(row=9, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        action.columnconfigure(3, weight=1)
        ttk.Label(action, text="부수", style="InsetTitle.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 7)
        )
        copies_spinbox = ttk.Spinbox(
            action, from_=1, to=9999, textvariable=variables["copies"], width=7
        )
        copies_spinbox.grid(row=0, column=1, sticky="w", padx=(0, 8))
        bundle_button = self.add_toggle_button(
            action,
            "묶음",
            variables["bundle"],
            command=lambda current_kind=kind: self.update_test_catalog_status(current_kind),
            help_text="켜짐: 1부씩 묶어서 전체 범위를 반복 전송합니다. 꺼짐: 각 PDF를 지정 부수만큼 전송합니다.",
        )
        bundle_button.grid(row=0, column=2, sticky="w", padx=(0, 8))
        add_button = ttk.Button(
            action,
            text="블록 추가",
            style="Primary.TButton",
            command=lambda current_kind=kind: self.add_test_catalog_block(current_kind),
        )
        add_button.grid(
            row=1, column=0, columnspan=4, sticky="ew", pady=(8, 0)
        )
        self.bind_block_add_button(
            add_button,
            lambda current_kind=kind: self.add_test_catalog_block(current_kind),
        )
        korean_label(
            page,
            textvariable=variables["status"],
            style="SectionHint.TLabel",
            wraplength=390,
        ).grid(row=10, column=0, columnspan=4, sticky="w", pady=(14, 0))

        self.test_builder_controls[kind] = {
            "page": page,
            "subject": subject_combo,
            "filter_label": filter_label_widget,
            "level": level_combo,
            "start": start_combo,
            "end": end_combo,
            "selected": selected_entry,
            "name": name_entry,
            "copies": copies_spinbox,
            "bundle": bundle_button,
            "add": add_button,
        }
        subject_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event, current_kind=kind: self.refresh_test_catalog_files(current_kind),
            add="+",
        )
        level_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event, current_kind=kind: self.refresh_test_catalog_files(current_kind),
            add="+",
        )
        start_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event, current_kind=kind: self.update_test_catalog_status(current_kind),
            add="+",
        )
        end_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event, current_kind=kind: self.update_test_catalog_status(current_kind),
            add="+",
        )
        selected_entry.bind(
            "<KeyRelease>",
            lambda _event, current_kind=kind: self.update_test_catalog_status(current_kind),
            add="+",
        )

    def test_kind_for_tab(self, tab_index: int | None = None) -> str | None:
        try:
            current = self.test_tabs.index("current") if tab_index is None else tab_index
        except (AttributeError, tk.TclError):
            return None
        return TEST_KIND_ORDER[current - 1] if 1 <= current <= len(TEST_KIND_ORDER) else None

    def test_catalog_exam_files(self, kind: str, subject: str, level: str) -> list[ExamPdf]:
        catalog = self.test_catalogs.get(kind)
        if catalog is None:
            return []
        return [
            ExamPdf(document.number, document.label, document.path)
            for document in catalog.documents(subject, level)
        ]

    def refresh_test_catalog_options(self, kind: str) -> None:
        catalog = self.test_catalogs[kind]
        variables = self.test_builder_vars[kind]
        controls = self.test_builder_controls[kind]
        subjects = catalog.subject_names
        controls["subject"]["values"] = subjects
        current_subject = str(variables["subject"].get())
        variables["subject"].set(
            current_subject if current_subject in subjects else (subjects[0] if subjects else "")
        )
        filter_choices = test_filter_choices(kind)
        controls["level"]["values"] = filter_choices
        current_level = str(variables["level"].get())
        variables["level"].set(
            current_level if current_level in filter_choices else filter_choices[0]
        )
        self.refresh_test_catalog_files(kind)

    def refresh_test_catalog_files(self, kind: str) -> None:
        variables = self.test_builder_vars[kind]
        controls = self.test_builder_controls[kind]
        subject = str(variables["subject"].get())
        level = str(variables["level"].get())
        files = self.test_catalog_exam_files(kind, subject, level)
        self.test_exam_files[kind] = files
        values = [item.label for item in files]
        controls["start"]["values"] = values
        controls["end"]["values"] = values
        variables["selected"].set("")
        if values:
            current_start = str(variables["start"].get())
            current_end = str(variables["end"].get())
            variables["start"].set(current_start if current_start in values else values[0])
            variables["end"].set(
                current_end if current_end in values else values[min(4, len(values) - 1)]
            )
        else:
            variables["start"].set("")
            variables["end"].set("")
        self.update_test_catalog_status(kind)

    def current_test_catalog_files(
        self, kind: str
    ) -> tuple[tuple[int, ...] | None, list[ExamPdf], int, int]:
        variables = self.test_builder_vars[kind]
        files = self.test_exam_files.get(kind, [])
        selected_numbers = parse_file_number_list(str(variables["selected"].get()))
        if selected_numbers:
            files_by_number = {item.number: item for item in files}
            missing = [number for number in selected_numbers if number not in files_by_number]
            if missing:
                missing_label = ", ".join(str(number) for number in missing)
                raise ValueError(f"현재 {kind} 목록에 없는 파일 번호입니다: {missing_label}")
            selected = [files_by_number[number] for number in selected_numbers]
            return selected_numbers, selected, min(selected_numbers), max(selected_numbers)
        if not files:
            raise ValueError(
                f"선택한 과목·{test_filter_label(kind)}에 {kind} 자료가 없습니다."
            )
        start = parse_choice_number(str(variables["start"].get()))
        end = parse_choice_number(str(variables["end"].get()))
        if start > end:
            start, end = end, start
        selected = [item for item in files if start <= item.number <= end]
        return None, selected, start, end

    def make_test_catalog_block(self, kind: str) -> PrintBlock:
        variables = self.test_builder_vars[kind]
        selected_numbers, files, start, end = self.current_test_catalog_files(kind)
        copies = int(variables["copies"].get())
        if copies < 1:
            raise ValueError("부수는 1 이상이어야 합니다.")
        if not files:
            raise ValueError("선택된 범위에 시험지 파일이 없습니다.")
        subject = str(variables["subject"].get())
        level = str(variables["level"].get())
        selection_label = (
            "(" + ",".join(str(number) for number in selected_numbers) + ")"
            if selected_numbers
            else f"{start:02d}~{end:02d}"
        )
        printer_route, printer_override = self.resolve_block_printer_route("세로")
        return PrintBlock(
            subject=subject,
            level=level,
            round_name=kind,
            start=start,
            end=end,
            copies=copies,
            files=files,
            bundled=bool(variables["bundle"].get()),
            source=TEST_SOURCE_BY_KIND[kind],
            printer_override=printer_override,
            display_label=f"{kind} / {subject} / {level} / {selection_label}",
            printer_route=printer_route,
            selected_numbers=selected_numbers,
        )

    def add_test_catalog_block(self, kind: str) -> bool:
        variables = self.test_builder_vars[kind]
        captured_copies = variables["copies"].get()
        captured_selected = variables["selected"].get()
        captured_name = self.new_block_name_var.get()
        try:
            block = self.apply_new_block_name(self.make_test_catalog_block(kind))
        except Exception as exc:
            messagebox.showwarning(APP_TITLE, str(exc))
            return False
        token = self.reserve_block_commit()

        def commit() -> None:
            self.push_undo_state(f"{kind} 블록 추가")
            self.blocks.append(block)
            self.block_status_by_id[id(block)] = "대기"
            self.update_queue_view(select_index=len(self.blocks) - 1)
            self.update_preview()
            if variables["copies"].get() == captured_copies:
                variables["copies"].set(1)
            if variables["selected"].get() == captured_selected:
                variables["selected"].set("")
            if self.new_block_name_var.get() == captured_name:
                self.clear_new_block_name()
            self.update_test_catalog_status(kind)
            self.log(f"{kind} 블록 추가: {block.summary}")

        self.complete_block_commit(token, commit)
        return True

    def update_test_catalog_status(self, kind: str) -> None:
        variables = self.test_builder_vars[kind]
        status = variables["status"]
        try:
            selected_numbers, files, _start, _end = self.current_test_catalog_files(kind)
            copies = int(variables["copies"].get())
            selection_label = (
                " (" + ",".join(str(number) for number in selected_numbers) + ")"
                if selected_numbers
                else ""
            )
            mode = f"{copies}묶음" if variables["bundle"].get() else f"각 {copies}부"
            status.set(f"{len(files)}개{selection_label} · {mode} · 세로 고정")
        except ValueError as exc:
            status.set(str(exc))
        except Exception:
            status.set(f"{kind} 범위를 선택하세요.")


    def refresh_material_card_wrap(self, _event: tk.Event | None = None) -> None:
        width = max(250, min(340, self.builder_frame.winfo_width() - 44))
        for name in (
            "apex_status_label",
            "apex_boost_status_label",
            "able_status_label",
            "training_pack_status_label",
            "frequent_type_status_label",
            "selected_exam_status_label",
            "gyopyeong_status_label",
            "textbook_status_label",
        ):
            label = getattr(self, name, None)
            if label is not None:
                label.configure(wraplength=width)

    def handle_builder_notebook_configure(self, _event: tk.Event | None = None) -> None:
        """Coalesce resize events before redistributing the main tabs."""
        if getattr(self, "_builder_tab_resize_after_id", None) is not None:
            return
        self._builder_tab_resize_after_id = self.root.after_idle(
            self.refresh_builder_tab_geometry
        )

    def refresh_builder_tab_geometry(self) -> None:
        """Expand only the main tab buttons without padding their page content."""
        self._builder_tab_resize_after_id = None
        notebook = getattr(self, "builder_notebook", None)
        if notebook is None:
            return
        try:
            tabs = notebook.tabs()
            available_width = notebook.winfo_width() - 2
        except tk.TclError:
            return
        if not tabs or available_width <= len(tabs):
            return

        labels = tuple(str(notebook.tab(tab_id, "text")) for tab_id in tabs)
        signature = (available_width, labels)
        if getattr(self, "_builder_tab_layout_signature", None) == signature:
            return
        self._builder_tab_layout_signature = signature

        try:
            tab_font = tkfont.Font(
                root=self.root,
                font=self.style.lookup("Builder.TNotebook.Tab", "font"),
            )
        except tk.TclError:
            tab_font = tkfont.Font(root=self.root, family=self.ui_font_family, size=11)

        # A per-tab ``padding`` option belongs to the notebook page itself on
        # Windows and therefore narrows every nested form.  Transparent sizing
        # images enlarge only the clickable tab labels, leaving all page and
        # subtab geometry untouched.
        tab_chrome_width = 14
        natural_widths = [tab_font.measure(label) + tab_chrome_width for label in labels]
        extra_width = max(0, available_width - sum(natural_widths))
        extra_per_tab, remainder = divmod(extra_width, len(tabs))
        sizing_images: list[tk.PhotoImage] = []
        for index, (tab_id, natural_width) in enumerate(zip(tabs, natural_widths)):
            target_width = natural_width + extra_per_tab + (1 if index < remainder else 0)
            image = tk.PhotoImage(
                master=self.root,
                width=max(1, target_width - tab_chrome_width),
                height=37,
            )
            sizing_images.append(image)
            notebook.tab(
                tab_id,
                image=image,
                compound="center",
                padding=(0, 0, 0, 0),
            )
        self._builder_tab_sizing_images = sizing_images

    @staticmethod
    def set_material_lesson_range_state(
        start_combo: ttk.Combobox,
        end_combo: ttk.Combobox,
        *,
        enabled: bool,
    ) -> None:
        style = "TCombobox" if enabled else "Blank.TCombobox"
        state = "readonly" if enabled else "disabled"
        start_combo.configure(style=style, state=state)
        end_combo.configure(style=style, state=state)

    def _build_apex_material_page(self, variant: str) -> None:
        """Build one fixed-variant APEX page inside the material notebook."""
        page = ttk.Frame(self.material_tabs, style="Panel.TFrame", padding=(16, 14))
        self.material_tabs.add(page, text=variant, padding=(0, 0), sticky="nsew")
        page.columnconfigure(1, weight=1)
        ttk.Label(page, text=variant, style="InsetTitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        ttk.Label(page, text="과목", style="FieldSoft.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=5
        )
        subject_combo = ttk.Combobox(page, textvariable=self.apex_subject_var, state="readonly")
        subject_combo.grid(row=1, column=1, sticky="ew", pady=5)
        ttk.Label(page, text="문서", style="FieldSoft.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 10), pady=5
        )
        document_combo = ttk.Combobox(
            page, textvariable=self.apex_document_kind_var, state="readonly"
        )
        document_combo.grid(row=2, column=1, sticky="ew", pady=5)
        ttk.Label(page, text="파트", style="FieldSoft.TLabel").grid(
            row=3, column=0, sticky="w", padx=(0, 10), pady=5
        )
        part_combo = ttk.Combobox(page, textvariable=self.apex_part_var, state="readonly")
        part_combo.grid(row=3, column=1, sticky="ew", pady=5)
        ttk.Label(page, text="시작 차시", style="FieldSoft.TLabel").grid(
            row=4, column=0, sticky="w", padx=(0, 10), pady=5
        )
        start_combo = ttk.Combobox(page, textvariable=self.apex_start_var, state="readonly")
        start_combo.grid(row=4, column=1, sticky="ew", pady=5)
        ttk.Label(page, text="끝 차시", style="FieldSoft.TLabel").grid(
            row=5, column=0, sticky="w", padx=(0, 10), pady=5
        )
        end_combo = ttk.Combobox(page, textvariable=self.apex_end_var, state="readonly")
        end_combo.grid(row=5, column=1, sticky="ew", pady=5)
        ttk.Label(page, text="개별 차시", style="FieldSoft.TLabel").grid(
            row=6, column=0, sticky="w", padx=(0, 10), pady=5
        )
        selected_numbers_entry = ttk.Entry(page, textvariable=self.apex_selected_numbers_var)
        selected_numbers_entry.grid(row=6, column=1, sticky="ew", pady=5)
        ttk.Label(
            page,
            text="예: 1-3, 6, 7-9 (공백 무관) · 지정 차시만 선택",
            style="SectionHint.TLabel",
        ).grid(row=7, column=1, sticky="w", pady=(0, 2))
        name_entry = self.add_new_block_name_field(
            page,
            row=8,
            label_style="FieldSoft.TLabel",
        )

        actions = ttk.Frame(page, style="Inset.TFrame")
        actions.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        actions.columnconfigure(1, weight=1)
        ttk.Label(actions, text="부수", style="InsetTitle.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        copies_spinbox = ttk.Spinbox(
            actions, from_=1, to=9999, textvariable=self.apex_copies_var, width=7
        )
        copies_spinbox.grid(row=0, column=1, sticky="w")
        add_button = ttk.Button(
            actions,
            text="블록 추가",
            style="Primary.TButton",
            command=self.add_apex_material_block,
        )
        add_button.grid(row=0, column=2, sticky="e")
        self.bind_block_add_button(add_button, self.add_apex_material_block)
        status_label = korean_label(
            page,
            textvariable=self.apex_status_var,
            style="InsetText.TLabel",
            justify="left",
            wraplength=380,
        )
        status_label.grid(row=10, column=0, columnspan=2, sticky="ew", pady=(12, 0))

        controls: dict[str, tk.Misc] = {
            "page": page,
            "subject": subject_combo,
            "document": document_combo,
            "part": part_combo,
            "start": start_combo,
            "end": end_combo,
            "selected_numbers": selected_numbers_entry,
            "name": name_entry,
            "copies": copies_spinbox,
            "add": add_button,
            "status": status_label,
        }
        self.apex_material_controls[variant] = controls
        if variant == "APEX Boost":
            self.apex_boost_status_label = status_label
        subject_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event, selected_variant=variant: self.handle_apex_variant_selection(
                selected_variant, "subject"
            ),
            add="+",
        )
        document_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event, selected_variant=variant: self.handle_apex_variant_selection(
                selected_variant, "document"
            ),
            add="+",
        )
        part_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event, selected_variant=variant: self.handle_apex_variant_selection(
                selected_variant, "part"
            ),
            add="+",
        )
        start_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event, selected_variant=variant: self.handle_apex_variant_selection(
                selected_variant, "range"
            ),
            add="+",
        )
        end_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event, selected_variant=variant: self.handle_apex_variant_selection(
                selected_variant, "range"
            ),
            add="+",
        )
        selected_numbers_entry.bind(
            "<KeyRelease>",
            lambda _event, selected_variant=variant: self.handle_apex_variant_selection(
                selected_variant, "range"
            ),
            add="+",
        )

    def active_apex_variant(self) -> str:
        """Return the APEX variant represented by the current material tab."""
        variants_by_tab = {
            MATERIAL_TAB_APEX: "APEX",
            MATERIAL_TAB_APEX_BOOST: "APEX Boost",
        }
        material_tabs = getattr(self, "material_tabs", None)
        builder_notebook = getattr(self, "builder_notebook", None)
        if material_tabs is not None:
            try:
                is_fixed_variant_notebook = len(material_tabs.tabs()) > MATERIAL_TAB_APEX_BOOST
                if (
                    is_fixed_variant_notebook
                    and (builder_notebook is None or builder_notebook.index("current") == 1)
                ):
                    index = material_tabs.index("current")
                    if index in variants_by_tab:
                        return variants_by_tab[index]
            except tk.TclError:
                pass
        current = str(self.apex_variant_var.get()).strip()
        return current if current in variants_by_tab.values() else "APEX"

    def sync_active_apex_controls(self, variant: str | None = None) -> None:
        """Expose the controls for the active APEX tab to shared handlers."""
        controls_by_variant = getattr(self, "apex_material_controls", {})
        selected_variant = variant or self.active_apex_variant()
        controls = controls_by_variant.get(selected_variant)
        if controls is None:
            return
        self.apex_variant_var.set(selected_variant)
        for attribute, key in (
            ("apex_subject_combo", "subject"),
            ("apex_document_combo", "document"),
            ("apex_part_combo", "part"),
            ("apex_start_combo", "start"),
            ("apex_end_combo", "end"),
            ("apex_selected_numbers_entry", "selected_numbers"),
            ("apex_new_block_name_entry", "name"),
            ("apex_copies_spinbox", "copies"),
            ("apex_add_button", "add"),
            ("apex_status_label", "status"),
        ):
            setattr(self, attribute, controls[key])

    def handle_apex_variant_selection(self, variant: str, changed: str) -> None:
        """Refresh only the active fixed-variant APEX page after a choice."""
        if variant != self.active_apex_variant():
            return
        self.sync_active_apex_controls(variant)
        if changed == "subject":
            self.refresh_apex_document_options()
        elif changed == "document":
            self.refresh_apex_part_options()
        elif changed == "part":
            self.refresh_apex_lesson_options()
        else:
            self.refresh_apex_status()

    def _build_lesson_material_page(
        self,
        parent: ttk.Frame,
        *,
        prefix: str,
        title: str,
        add_text: str,
        refresh_options: Callable[[], object],
        refresh_lessons: Callable[[], object],
        refresh_status: Callable[[], object],
        add_command: Callable[[], object],
    ) -> None:
        """Build the shared full-set/lesson selector used by workbook PDFs."""
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text=title, style="InsetTitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        ttk.Label(parent, text="과목", style="FieldSoft.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=5
        )
        subject_combo = ttk.Combobox(
            parent,
            textvariable=getattr(self, f"{prefix}_subject_var"),
            state="readonly",
        )
        subject_combo.grid(row=1, column=1, sticky="ew", pady=5)
        setattr(self, f"{prefix}_subject_combo", subject_combo)

        ttk.Label(parent, text="인쇄 구성", style="FieldSoft.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 10), pady=5
        )
        mode_combo = ttk.Combobox(
            parent,
            textvariable=getattr(self, f"{prefix}_mode_var"),
            state="readonly",
        )
        mode_combo.grid(row=2, column=1, sticky="ew", pady=5)
        setattr(self, f"{prefix}_mode_combo", mode_combo)

        ttk.Label(parent, text="시작 차시", style="FieldSoft.TLabel").grid(
            row=3, column=0, sticky="w", padx=(0, 10), pady=5
        )
        start_combo = ttk.Combobox(
            parent,
            textvariable=getattr(self, f"{prefix}_start_var"),
            state="readonly",
        )
        start_combo.grid(row=3, column=1, sticky="ew", pady=5)
        setattr(self, f"{prefix}_start_combo", start_combo)

        ttk.Label(parent, text="끝 차시", style="FieldSoft.TLabel").grid(
            row=4, column=0, sticky="w", padx=(0, 10), pady=5
        )
        end_combo = ttk.Combobox(
            parent,
            textvariable=getattr(self, f"{prefix}_end_var"),
            state="readonly",
        )
        end_combo.grid(row=4, column=1, sticky="ew", pady=5)
        setattr(self, f"{prefix}_end_combo", end_combo)

        ttk.Label(parent, text="개별 차시", style="FieldSoft.TLabel").grid(
            row=5, column=0, sticky="w", padx=(0, 10), pady=5
        )
        selected_entry = ttk.Entry(
            parent,
            textvariable=getattr(self, f"{prefix}_selected_numbers_var"),
        )
        selected_entry.grid(row=5, column=1, sticky="ew", pady=5)
        setattr(self, f"{prefix}_selected_numbers_entry", selected_entry)
        ttk.Label(
            parent,
            text="차시별: 예 1-3, 6, 7-9 (공백 무관) · 풀 세트: PDF 전체",
            style="SectionHint.TLabel",
        ).grid(row=6, column=1, sticky="w", pady=(0, 2))
        name_entry = self.add_new_block_name_field(
            parent,
            row=7,
            label_style="FieldSoft.TLabel",
        )
        setattr(self, f"{prefix}_new_block_name_entry", name_entry)

        actions = ttk.Frame(parent, style="Inset.TFrame")
        actions.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        actions.columnconfigure(1, weight=1)
        ttk.Label(actions, text="부수", style="InsetTitle.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        copies_spinbox = ttk.Spinbox(
            actions,
            from_=1,
            to=9999,
            textvariable=getattr(self, f"{prefix}_copies_var"),
            width=7,
        )
        copies_spinbox.grid(row=0, column=1, sticky="w")
        setattr(self, f"{prefix}_copies_spinbox", copies_spinbox)
        add_button = ttk.Button(
            actions,
            text=add_text,
            style="Primary.TButton",
            command=add_command,
        )
        add_button.grid(row=0, column=2, sticky="e")
        setattr(self, f"{prefix}_add_button", add_button)
        self.bind_block_add_button(add_button, add_command)

        status_label = korean_label(
            parent,
            textvariable=getattr(self, f"{prefix}_status_var"),
            style="InsetText.TLabel",
            justify="left",
            wraplength=380,
        )
        status_label.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        setattr(self, f"{prefix}_status_label", status_label)

        subject_combo.bind("<<ComboboxSelected>>", lambda _event: refresh_options())
        mode_combo.bind("<<ComboboxSelected>>", lambda _event: refresh_lessons())
        start_combo.bind("<<ComboboxSelected>>", lambda _event: refresh_status())
        end_combo.bind("<<ComboboxSelected>>", lambda _event: refresh_status())
        selected_entry.bind("<KeyRelease>", lambda _event: refresh_status(), add="+")

    def _build_gyopyeong_material_page(self, parent: ttk.Frame) -> None:
        """Build the subject/part/lesson selector for high-school exam PDFs."""
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text="교평 기출", style="InsetTitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        ttk.Label(parent, text="과목", style="FieldSoft.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=5
        )
        self.gyopyeong_subject_combo = ttk.Combobox(
            parent,
            textvariable=self.gyopyeong_subject_var,
            state="readonly",
        )
        self.gyopyeong_subject_combo.grid(row=1, column=1, sticky="ew", pady=5)

        ttk.Label(parent, text="파트", style="FieldSoft.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 10), pady=5
        )
        self.gyopyeong_part_combo = ttk.Combobox(
            parent,
            textvariable=self.gyopyeong_part_var,
            state="readonly",
        )
        self.gyopyeong_part_combo.grid(row=2, column=1, sticky="ew", pady=5)

        ttk.Label(parent, text="차시", style="FieldSoft.TLabel").grid(
            row=3, column=0, sticky="w", padx=(0, 10), pady=5
        )
        self.gyopyeong_lesson_combo = ttk.Combobox(
            parent,
            textvariable=self.gyopyeong_lesson_var,
            state="readonly",
        )
        self.gyopyeong_lesson_combo.grid(row=3, column=1, sticky="ew", pady=5)

        self.gyopyeong_new_block_name_entry = self.add_new_block_name_field(
            parent,
            row=4,
            label_style="FieldSoft.TLabel",
        )

        actions = ttk.Frame(parent, style="Inset.TFrame")
        actions.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        actions.columnconfigure(1, weight=1)
        ttk.Label(actions, text="부수", style="InsetTitle.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        self.gyopyeong_copies_spinbox = ttk.Spinbox(
            actions,
            from_=1,
            to=9999,
            textvariable=self.gyopyeong_copies_var,
            width=7,
        )
        self.gyopyeong_copies_spinbox.grid(row=0, column=1, sticky="w")
        self.gyopyeong_add_button = ttk.Button(
            actions,
            text="교평 기출 블록 추가",
            style="Primary.TButton",
            command=self.add_gyopyeong_material_block,
        )
        self.gyopyeong_add_button.grid(row=0, column=2, sticky="e")
        self.bind_block_add_button(
            self.gyopyeong_add_button,
            self.add_gyopyeong_material_block,
        )

        self.gyopyeong_status_label = korean_label(
            parent,
            textvariable=self.gyopyeong_status_var,
            style="InsetText.TLabel",
            justify="left",
            wraplength=380,
        )
        self.gyopyeong_status_label.grid(
            row=6, column=0, columnspan=2, sticky="ew", pady=(12, 0)
        )

        self.gyopyeong_subject_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.refresh_gyopyeong_part_options(),
        )
        self.gyopyeong_part_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.refresh_gyopyeong_lesson_options(),
        )
        self.gyopyeong_lesson_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.refresh_gyopyeong_status(),
        )

    def _build_selected_exam_material_page(self, parent: ttk.Frame) -> None:
        """Build the fixed subject/lesson selector for 기출엄선 PDFs."""
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text="기출엄선", style="InsetTitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        ttk.Label(parent, text="과목", style="FieldSoft.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=5
        )
        self.selected_exam_subject_combo = ttk.Combobox(
            parent,
            textvariable=self.selected_exam_subject_var,
            state="readonly",
        )
        self.selected_exam_subject_combo.grid(row=1, column=1, sticky="ew", pady=5)

        ttk.Label(parent, text="차시", style="FieldSoft.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 10), pady=5
        )
        self.selected_exam_lesson_combo = ttk.Combobox(
            parent,
            textvariable=self.selected_exam_lesson_var,
            state="readonly",
        )
        self.selected_exam_lesson_combo.grid(row=2, column=1, sticky="ew", pady=5)

        ttk.Label(parent, text="개별 차시", style="FieldSoft.TLabel").grid(
            row=3, column=0, sticky="w", padx=(0, 10), pady=5
        )
        self.selected_exam_selected_numbers_entry = ttk.Entry(
            parent,
            textvariable=self.selected_exam_selected_numbers_var,
        )
        self.selected_exam_selected_numbers_entry.grid(
            row=3, column=1, sticky="ew", pady=5
        )
        ttk.Label(
            parent,
            text="예: 1-3, 6, 7-9 (공백 무관) · 지정 차시만 선택",
            style="SectionHint.TLabel",
        ).grid(row=4, column=1, sticky="w", pady=(0, 2))

        self.selected_exam_new_block_name_entry = self.add_new_block_name_field(
            parent,
            row=5,
            label_style="FieldSoft.TLabel",
        )

        actions = ttk.Frame(parent, style="Inset.TFrame")
        actions.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        actions.columnconfigure(1, weight=1)
        ttk.Label(actions, text="부수", style="InsetTitle.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        self.selected_exam_copies_spinbox = ttk.Spinbox(
            actions,
            from_=1,
            to=9999,
            textvariable=self.selected_exam_copies_var,
            width=7,
        )
        self.selected_exam_copies_spinbox.grid(row=0, column=1, sticky="w")
        self.selected_exam_add_button = ttk.Button(
            actions,
            text="기출엄선 블록 추가",
            style="Primary.TButton",
            command=self.add_selected_exam_material_block,
        )
        self.selected_exam_add_button.grid(row=0, column=2, sticky="e")
        self.bind_block_add_button(
            self.selected_exam_add_button,
            self.add_selected_exam_material_block,
        )

        self.selected_exam_status_label = korean_label(
            parent,
            textvariable=self.selected_exam_status_var,
            style="InsetText.TLabel",
            justify="left",
            wraplength=380,
        )
        self.selected_exam_status_label.grid(
            row=7, column=0, columnspan=2, sticky="ew", pady=(12, 0)
        )

        self.selected_exam_subject_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.handle_selected_exam_subject_changed(),
        )
        self.selected_exam_lesson_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.refresh_selected_exam_status(),
        )
        self.selected_exam_selected_numbers_entry.bind(
            "<KeyRelease>",
            lambda _event: self.refresh_selected_exam_status(),
            add="+",
        )

    def _build_textbook_material_page(self, parent: ttk.Frame) -> None:
        """Build the verified textbook edition and small-unit selector."""
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text="교과서", style="InsetTitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        fields = (
            ("과목", "textbook_subject_combo", self.textbook_subject_var),
            ("출판사", "textbook_publisher_combo", self.textbook_publisher_var),
            ("문서", "textbook_document_kind_combo", self.textbook_document_kind_var),
            ("시작 파트", "textbook_start_combo", self.textbook_start_var),
            ("끝 파트", "textbook_end_combo", self.textbook_end_var),
        )
        for row, (label, attribute, variable) in enumerate(fields, start=1):
            ttk.Label(parent, text=label, style="FieldSoft.TLabel").grid(
                row=row, column=0, sticky="w", padx=(0, 10), pady=5
            )
            combo = ttk.Combobox(parent, textvariable=variable, state="readonly")
            combo.grid(row=row, column=1, sticky="ew", pady=5)
            setattr(self, attribute, combo)

        ttk.Label(parent, text="개별 파트", style="FieldSoft.TLabel").grid(
            row=6, column=0, sticky="w", padx=(0, 10), pady=5
        )
        self.textbook_selected_numbers_entry = ttk.Entry(
            parent, textvariable=self.textbook_selected_numbers_var
        )
        self.textbook_selected_numbers_entry.grid(row=6, column=1, sticky="ew", pady=5)
        ttk.Label(
            parent,
            text="예: 1-3, 6, 7-9 (공백 무관) · 지정 파트만 선택",
            style="SectionHint.TLabel",
        ).grid(row=7, column=1, sticky="w", pady=(0, 2))
        self.textbook_new_block_name_entry = self.add_new_block_name_field(
            parent,
            row=8,
            label_style="FieldSoft.TLabel",
        )

        actions = ttk.Frame(parent, style="Inset.TFrame")
        actions.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        actions.columnconfigure(1, weight=1)
        ttk.Label(actions, text="부수", style="InsetTitle.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        self.textbook_copies_spinbox = ttk.Spinbox(
            actions,
            from_=1,
            to=9999,
            textvariable=self.textbook_copies_var,
            width=7,
        )
        self.textbook_copies_spinbox.grid(row=0, column=1, sticky="w")
        self.textbook_add_button = ttk.Button(
            actions,
            text="교과서 블록 추가",
            style="Primary.TButton",
            command=self.add_textbook_material_block,
            state="disabled",
        )
        self.textbook_add_button.grid(row=0, column=2, sticky="e")
        self.bind_block_add_button(
            self.textbook_add_button, self.add_textbook_material_block
        )
        self.textbook_status_label = korean_label(
            parent,
            textvariable=self.textbook_status_var,
            style="InsetText.TLabel",
            justify="left",
            wraplength=380,
        )
        self.textbook_status_label.grid(
            row=10, column=0, columnspan=2, sticky="ew", pady=(12, 0)
        )

        self.textbook_subject_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.refresh_textbook_publisher_options(),
        )
        self.textbook_publisher_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.refresh_textbook_document_options(),
        )
        self.textbook_document_kind_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.refresh_textbook_lesson_options(),
        )
        self.textbook_start_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self.refresh_textbook_status()
        )
        self.textbook_end_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self.refresh_textbook_status()
        )
        self.textbook_selected_numbers_entry.bind(
            "<KeyRelease>", lambda _event: self.refresh_textbook_status(), add="+"
        )

    def _build_textbook_variant_page(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text="핵심문제 변형", style="InsetTitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        fields = (
            ("과목", "textbook_variant_subject_combo", self.textbook_variant_subject_var),
            ("출판사", "textbook_variant_publisher_combo", self.textbook_variant_publisher_var),
            ("시작 파트", "textbook_variant_start_combo", self.textbook_variant_start_var),
            ("끝 파트", "textbook_variant_end_combo", self.textbook_variant_end_var),
        )
        for row, (label, attribute, variable) in enumerate(fields, start=1):
            ttk.Label(parent, text=label, style="FieldSoft.TLabel").grid(
                row=row, column=0, sticky="w", padx=(0, 10), pady=5
            )
            combo = ttk.Combobox(parent, textvariable=variable, state="readonly")
            combo.grid(row=row, column=1, sticky="ew", pady=5)
            setattr(self, attribute, combo)
        ttk.Label(parent, text="개별 파트", style="FieldSoft.TLabel").grid(
            row=5, column=0, sticky="w", padx=(0, 10), pady=5
        )
        self.textbook_variant_selected_numbers_entry = ttk.Entry(
            parent, textvariable=self.textbook_variant_selected_numbers_var
        )
        self.textbook_variant_selected_numbers_entry.grid(row=5, column=1, sticky="ew", pady=5)
        ttk.Label(
            parent,
            text="예: 1-3, 6 · 지정 파트와 해당 대단원 평가 포함",
            style="SectionHint.TLabel",
        ).grid(row=6, column=1, sticky="w", pady=(0, 2))
        self.textbook_variant_new_block_name_entry = self.add_new_block_name_field(
            parent, row=7, label_style="FieldSoft.TLabel"
        )
        actions = ttk.Frame(parent, style="Inset.TFrame")
        actions.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        actions.columnconfigure(1, weight=1)
        ttk.Label(actions, text="부수", style="InsetTitle.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        self.textbook_variant_copies_spinbox = ttk.Spinbox(
            actions, from_=1, to=9999, textvariable=self.textbook_variant_copies_var, width=7
        )
        self.textbook_variant_copies_spinbox.grid(row=0, column=1, sticky="w")
        self.textbook_variant_add_button = ttk.Button(
            actions, text="변형 문제 블록 추가", style="Primary.TButton",
            command=self.add_textbook_variant_block, state="disabled",
        )
        self.textbook_variant_add_button.grid(row=0, column=2, sticky="e")
        self.bind_block_add_button(
            self.textbook_variant_add_button, self.add_textbook_variant_block
        )
        self.textbook_variant_status_label = korean_label(
            parent, textvariable=self.textbook_variant_status_var,
            style="InsetText.TLabel", justify="left", wraplength=380,
        )
        self.textbook_variant_status_label.grid(
            row=9, column=0, columnspan=2, sticky="ew", pady=(12, 0)
        )
        self.textbook_variant_subject_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self.refresh_textbook_variant_publishers()
        )
        self.textbook_variant_publisher_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self.refresh_textbook_variant_lesson_options()
        )
        for combo in (self.textbook_variant_start_combo, self.textbook_variant_end_combo):
            combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_textbook_variant_status())
        self.textbook_variant_selected_numbers_entry.bind(
            "<KeyRelease>", lambda _event: self.refresh_textbook_variant_status(), add="+"
        )

    def active_textbook_focus_widgets(self) -> list[tk.Misc | None]:
        prefix = (
            "textbook_variant"
            if getattr(self, "textbook_subtabs", None) is not None
            and self.textbook_subtabs.index("current") == 1
            else "textbook"
        )
        names = ["subject_combo", "publisher_combo"]
        if prefix == "textbook":
            names.append("document_kind_combo")
        names.extend((
            "start_combo", "end_combo", "selected_numbers_entry",
            "new_block_name_entry", "copies_spinbox", "add_button",
        ))
        return [getattr(self, f"{prefix}_{name}", None) for name in names]

    def build_material_builder_responsive(self, parent: ttk.Frame) -> None:
        """Use one full-width control per row so material cards never clip at 3:7."""
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        self.material_tabs = material_tabs = ttk.Notebook(parent, style="Material.TNotebook")
        material_tabs.grid(row=0, column=0, sticky="nsew")
        self.apex_material_controls: dict[str, dict[str, tk.Misc]] = {}
        self._build_apex_material_page("APEX")

        able = ttk.Frame(material_tabs, style="Panel.TFrame", padding=(16, 14))
        material_tabs.add(able, text="A:ble", padding=(0, 0), sticky="nsew")
        able.columnconfigure(1, weight=1)
        ttk.Label(able, text="A:ble", style="InsetTitle.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(able, text="과목", style="FieldSoft.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=5)
        self.able_subject_combo = ttk.Combobox(able, textvariable=self.able_subject_var, state="readonly")
        self.able_subject_combo.grid(row=1, column=1, sticky="ew", pady=5)
        ttk.Label(able, text="인쇄 구성", style="FieldSoft.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 10), pady=5
        )
        self.able_document_kind_combo = ttk.Combobox(
            able, textvariable=self.able_document_kind_var, state="readonly"
        )
        self.able_document_kind_combo.grid(row=2, column=1, sticky="ew", pady=5)
        ttk.Label(able, text="시작 차시", style="FieldSoft.TLabel").grid(row=3, column=0, sticky="w", padx=(0, 10), pady=5)
        self.able_start_combo = ttk.Combobox(able, textvariable=self.able_start_var, state="readonly")
        self.able_start_combo.grid(row=3, column=1, sticky="ew", pady=5)
        ttk.Label(able, text="끝 차시", style="FieldSoft.TLabel").grid(row=4, column=0, sticky="w", padx=(0, 10), pady=5)
        self.able_end_combo = ttk.Combobox(able, textvariable=self.able_end_var, state="readonly")
        self.able_end_combo.grid(row=4, column=1, sticky="ew", pady=5)

        ttk.Label(able, text="개별 차시", style="FieldSoft.TLabel").grid(
            row=5, column=0, sticky="w", padx=(0, 10), pady=5
        )
        self.able_selected_numbers_entry = ttk.Entry(
            able, textvariable=self.able_selected_numbers_var
        )
        self.able_selected_numbers_entry.grid(row=5, column=1, sticky="ew", pady=5)
        ttk.Label(
            able,
            text="예: 1-3, 6, 7-9 (공백 무관) · 지정 차시만 선택",
            style="SectionHint.TLabel",
        ).grid(row=6, column=1, sticky="w", pady=(0, 2))
        self.able_new_block_name_entry = self.add_new_block_name_field(
            able,
            row=7,
            label_style="FieldSoft.TLabel",
        )

        able_actions = ttk.Frame(able, style="Inset.TFrame")
        able_actions.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        able_actions.columnconfigure(1, weight=1)
        ttk.Label(able_actions, text="부수", style="InsetTitle.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.able_copies_spinbox = ttk.Spinbox(
            able_actions, from_=1, to=9999, textvariable=self.able_copies_var, width=7
        )
        self.able_copies_spinbox.grid(row=0, column=1, sticky="w")
        add_able_button = ttk.Button(
            able_actions, text="A:ble 블록 추가", style="Primary.TButton", command=self.add_able_material_block
        )
        self.able_add_button = add_able_button
        add_able_button.grid(row=0, column=2, sticky="e")
        self.bind_block_add_button(add_able_button, self.add_able_material_block)
        self.able_status_label = korean_label(
            able, textvariable=self.able_status_var, style="InsetText.TLabel", justify="left", wraplength=380
        )
        self.able_status_label.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(12, 0))

        selected_exam = ttk.Frame(
            material_tabs,
            style="Panel.TFrame",
            padding=(16, 14),
        )
        material_tabs.add(
            selected_exam,
            text="기출엄선",
            padding=(0, 0),
            sticky="nsew",
        )
        self._build_selected_exam_material_page(selected_exam)

        training = ttk.Frame(material_tabs, style="Panel.TFrame")
        material_tabs.add(training, text="트레이닝팩", padding=(0, 0), sticky="nsew")
        training.columnconfigure(0, weight=1)
        training.rowconfigure(0, weight=1)
        self.lesson_material_tabs = ttk.Notebook(training, style="LessonMaterial.TNotebook")
        self.lesson_material_tabs.grid(row=0, column=0, sticky="nsew")

        training_pack_page = ttk.Frame(
            self.lesson_material_tabs,
            style="Panel.TFrame",
            padding=(16, 14),
        )
        frequent_type_page = ttk.Frame(
            self.lesson_material_tabs,
            style="Panel.TFrame",
            padding=(16, 14),
        )
        self.lesson_material_tabs.add(training_pack_page, text="트레이닝팩", sticky="nsew")
        self.lesson_material_tabs.add(frequent_type_page, text="빈출유형", sticky="nsew")
        self._build_lesson_material_page(
            training_pack_page,
            prefix="training_pack",
            title="트레이닝팩",
            add_text="트팩 블록 추가",
            refresh_options=self.refresh_training_pack_material_options,
            refresh_lessons=self.refresh_training_pack_lesson_options,
            refresh_status=self.refresh_training_pack_status,
            add_command=self.add_training_pack_block,
        )
        self._build_lesson_material_page(
            frequent_type_page,
            prefix="frequent_type",
            title="빈출유형",
            add_text="빈출유형 블록 추가",
            refresh_options=self.refresh_frequent_type_material_options,
            refresh_lessons=self.refresh_frequent_type_lesson_options,
            refresh_status=self.refresh_frequent_type_status,
            add_command=self.add_frequent_type_block,
        )

        gyopyeong = ttk.Frame(material_tabs, style="Panel.TFrame", padding=(16, 14))
        material_tabs.add(gyopyeong, text="교평 기출", padding=(0, 0), sticky="nsew")
        self._build_gyopyeong_material_page(gyopyeong)

        self._build_apex_material_page("APEX Boost")

        textbook = ttk.Frame(material_tabs, style="Panel.TFrame", padding=(16, 14))
        material_tabs.add(textbook, text="교과서", padding=(0, 0), sticky="nsew")
        self.textbook_subtabs = ttk.Notebook(textbook, style="Material.TNotebook")
        self.textbook_subtabs.pack(fill="both", expand=True)
        textbook_standard = ttk.Frame(self.textbook_subtabs, style="Panel.TFrame", padding=(8, 8))
        textbook_variant = ttk.Frame(self.textbook_subtabs, style="Panel.TFrame", padding=(8, 8))
        self.textbook_subtabs.add(textbook_standard, text="교과서")
        self.textbook_subtabs.add(textbook_variant, text="핵심문제 변형")
        self._build_textbook_material_page(textbook_standard)
        self._build_textbook_variant_page(textbook_variant)
        self.textbook_subtabs.bind(
            "<<NotebookTabChanged>>", lambda _event: self.refresh_shortcut_help(), add="+"
        )
        self.sync_active_apex_controls("APEX")

        self.able_subject_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_able_material_options())
        self.able_document_kind_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self.handle_able_document_kind_changed()
        )
        self.able_start_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_able_status())
        self.able_end_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_able_status())
        self.able_selected_numbers_entry.bind("<KeyRelease>", lambda _event: self.refresh_able_status(), add="+")
        parent.bind("<Configure>", self.refresh_material_card_wrap, add="+")
        self.root.after_idle(self.refresh_material_card_wrap)


    def current_apex_document(self) -> ApexDocument | None:
        variant = self.active_apex_variant()
        return self.apex_material_catalog.find(
            variant,
            self.apex_subject_var.get(),
            self.apex_document_kind_var.get(),
        )

    def refresh_apex_material_options(self) -> None:
        variant = self.active_apex_variant()
        self.sync_active_apex_controls(variant)
        legacy_variant_combo = getattr(self, "apex_variant_combo", None)
        if legacy_variant_combo is not None:
            variants = self.apex_material_catalog.variants
            legacy_variant_combo["values"] = variants
            if variants and variant not in variants:
                variant = variants[0]
                self.apex_variant_var.set(variant)
        subjects = self.apex_material_catalog.subjects(variant)
        self.apex_subject_combo["values"] = subjects
        if subjects and self.apex_subject_var.get() not in subjects:
            self.apex_subject_var.set(subjects[0])
        self.refresh_apex_document_options()

    def refresh_apex_document_options(self) -> None:
        variant = self.active_apex_variant()
        self.sync_active_apex_controls(variant)
        kinds = tuple(
            kind
            for kind in ("교재", "해설")
            if self.apex_material_catalog.find(variant, self.apex_subject_var.get(), kind)
        )
        self.apex_document_combo["values"] = kinds
        if kinds and self.apex_document_kind_var.get() not in kinds:
            self.apex_document_kind_var.set(kinds[0])
        self.refresh_apex_part_options()

    def refresh_apex_part_options(self) -> None:
        document = self.current_apex_document()
        values = ("차시 별", "전체") if document is None else tuple(
            dict.fromkeys(
                (
                    "차시 별",
                    "전체",
                    *(part.part for part in document.parts),
                )
            )
        )
        self.apex_part_combo["values"] = values
        if self.apex_part_var.get() not in values:
            self.apex_part_var.set(
                "차시 별" if "차시 별" in values else (values[0] if values else "차시 별")
            )
        selected_numbers_var = getattr(self, "apex_selected_numbers_var", None)
        if selected_numbers_var is not None:
            selected_numbers_var.set("")
        self.refresh_apex_lesson_options()

    def refresh_apex_lesson_options(self) -> None:
        document = self.current_apex_document()
        selection_mode = self.apex_part_var.get()
        spans = ()
        if document is not None:
            spans = document.lessons if selection_mode == "차시 별" else document.lessons_for_part(selection_mode)
        if selection_mode != "차시 별":
            self.set_material_lesson_range_state(
                self.apex_start_combo,
                self.apex_end_combo,
                enabled=False,
            )
            self.apex_start_combo["values"] = ()
            self.apex_end_combo["values"] = ()
            self.apex_start_var.set("")
            self.apex_end_var.set("")
            self.refresh_apex_status()
            return

        self.set_material_lesson_range_state(
            self.apex_start_combo,
            self.apex_end_combo,
            enabled=True,
        )
        values = tuple(self.material_span_choice(span) for span in spans)
        self.apex_start_combo["values"] = values
        self.apex_end_combo["values"] = values
        self.apex_start_var.set(values[0] if values else "")
        self.apex_end_var.set(values[-1] if values else "")
        self.refresh_apex_status()

    def refresh_apex_status(self) -> None:
        document = self.current_apex_document()
        if document is None:
            self.apex_status_var.set("선택 가능한 APEX PDF가 없습니다.")
            return
        try:
            selected_numbers = self.selected_apex_numbers(document)
            spans = self.selected_apex_spans(document)
            page_numbers: tuple[int, ...] = ()
            if document.document_kind == "교재" and document.variant == "APEX Boost":
                try:
                    page_numbers = apex_boost_page_numbers(document.subject, (span.number for span in spans))
                except ValueError:
                    page_numbers = ()
            elif document.document_kind == "교재" and document.variant == "APEX":
                try:
                    page_numbers = apex_manual_page_numbers(
                        document.subject,
                        (span.number for span in spans),
                    )
                except ValueError:
                    page_numbers = ()
            selection_label = (
                "지정 차시 (" + ",".join(str(number) for number in selected_numbers) + ")"
                if selected_numbers
                else f"{len(spans)}차시" if self.apex_part_var.get() == "차시 별" else self.apex_part_var.get()
            )
            if page_numbers:
                self.apex_status_var.set(f"{selection_label} · 본문 병합+빠른정답 {len(page_numbers)}쪽 · 세로 고정")
            elif selected_numbers:
                selected_page_numbers = self.apex_span_page_numbers(spans)
                self.apex_status_var.set(
                    f"{selection_label} · PDF {len(selected_page_numbers)}쪽 · 세로 고정"
                )
            else:
                page_span = combined_span(spans, "선택 범위", self.apex_part_var.get())
                self.apex_status_var.set(
                    f"{selection_label} · PDF {page_span.start_page}~{page_span.end_page}쪽 · 세로 고정"
                )
        except Exception:
            self.apex_status_var.set("차시 범위를 선택하세요.")

    def selected_apex_numbers(self, document: ApexDocument | None = None) -> tuple[int, ...] | None:
        value = getattr(self, "apex_selected_numbers_var", None)
        raw_value = str(value.get()).strip() if value is not None else ""
        numbers = parse_file_number_list(raw_value)
        if not numbers:
            return None
        document = document or self.current_apex_document()
        if document is None:
            raise ValueError("APEX 문서를 선택해 주세요.")
        selection_mode = self.apex_part_var.get()
        available_spans = (
            document.lessons
            if selection_mode in {"차시 별", "전체"}
            else document.lessons_for_part(selection_mode)
        )
        available = {span.number for span in available_spans}
        missing = [number for number in numbers if number not in available]
        if missing:
            missing_label = ", ".join(str(number) for number in missing)
            raise ValueError(f"현재 APEX 파트에 없는 차시입니다: {missing_label}")
        return numbers

    @staticmethod
    def apex_span_page_numbers(spans: tuple[PageSpan, ...]) -> tuple[int, ...]:
        pages: list[int] = []
        for span in spans:
            for start_page, end_page in span.effective_page_ranges:
                pages.extend(range(start_page, end_page + 1))
        return tuple(pages)

    def selected_apex_spans(self, document: ApexDocument) -> tuple[PageSpan, ...]:
        selected_numbers = self.selected_apex_numbers(document)
        selection_mode = self.apex_part_var.get()
        spans = document.lessons if selection_mode in {"차시 별", "전체"} else document.lessons_for_part(selection_mode)
        if selected_numbers:
            spans_by_number = {span.number: span for span in spans}
            return tuple(spans_by_number[number] for number in selected_numbers)
        start_value = str(self.apex_start_var.get()).strip()
        end_value = str(self.apex_end_var.get()).strip()
        if selection_mode != "차시 별" and not start_value and not end_value:
            if not spans:
                raise ValueError("선택한 APEX 파트가 비어 있습니다.")
            return spans
        if not start_value or not end_value:
            raise ValueError("선택한 APEX 차시 범위가 비어 있습니다.")

        start = parse_choice_number(start_value)
        end = parse_choice_number(end_value)
        if start > end:
            start, end = end, start
        selected = tuple(span for span in spans if start <= span.number <= end)
        if not selected:
            raise ValueError("선택한 APEX 차시 범위가 비어 있습니다.")
        return selected

    def make_apex_block_for_selection(
        self,
        document: ApexDocument,
        selection_mode: str,
        spans: tuple[PageSpan, ...],
        copies: int,
        selected_numbers: tuple[int, ...] | None = None,
        bundled: bool = False,
    ) -> PrintBlock:
        if copies < 1:
            raise ValueError("부수는 1 이상이어야 합니다.")
        if not spans:
            raise ValueError("선택한 APEX 차시 범위가 비어 있습니다.")
        all_spans = document.lessons if selection_mode in {"차시 별", "전체"} else document.lessons_for_part(selection_mode)
        range_label = (
            "(" + ",".join(str(number) for number in selected_numbers) + ")"
            if selected_numbers
            else f"{spans[0].number:02d}~{spans[-1].number:02d}차시"
        )
        mapped_page_numbers: tuple[int, ...] = ()
        if document.document_kind == "교재" and document.variant == "APEX Boost":
            try:
                mapped_page_numbers = apex_boost_page_numbers(
                    document.subject,
                    (span.number for span in spans),
                )
            except ValueError:
                mapped_page_numbers = ()
        elif document.document_kind == "교재" and document.variant == "APEX":
            try:
                mapped_page_numbers = apex_manual_page_numbers(
                    document.subject,
                    (span.number for span in spans),
                )
            except ValueError:
                mapped_page_numbers = ()
        if mapped_page_numbers:
            output_path = materialize_pdf_pages(
                document.path,
                mapped_page_numbers,
                MATERIAL_RANGE_CACHE_DIR,
                f"{document.variant}_{document.subject}_{document.document_kind}_{selection_mode}_"
                f"{range_label}_본문_빠른정답",
            )
        elif selected_numbers:
            output_path = materialize_pdf_pages(
                document.path,
                self.apex_span_page_numbers(spans),
                MATERIAL_RANGE_CACHE_DIR,
                f"{document.variant}_{document.subject}_{document.document_kind}_{selection_mode}_"
                f"{range_label}_지정차시",
            )
        elif selection_mode == "전체" and spans == all_spans:
            output_path = document.path
        else:
            page_span = combined_span(spans, range_label, selection_mode)
            output_path = materialize_pdf_span(
                document.path,
                page_span,
                MATERIAL_RANGE_CACHE_DIR,
                f"{document.variant}_{document.subject}_{document.document_kind}_{selection_mode}_{range_label}",
            )
        display_label = (
            f"{document.variant} / {document.subject} / {document.document_kind} / "
            f"{selection_mode} / {range_label}"
        )
        if mapped_page_numbers:
            display_label += " / 본문+빠른정답"
        item = ExamPdf(1, display_label, output_path)
        printer_route, printer_override = self.resolve_block_printer_route("세로")
        return PrintBlock(
            subject=document.subject,
            level=document.variant,
            round_name=document.document_kind,
            start=spans[0].number,
            end=spans[-1].number,
            copies=copies,
            files=[item],
            bundled=bundled,
            source="material_apex",
            printer_override=printer_override,
            display_label=display_label,
            printer_route=printer_route,
            selected_numbers=selected_numbers,
            material_kind=selection_mode,
        )

    def make_apex_material_block(self) -> PrintBlock:
        document = self.current_apex_document()
        if document is None:
            raise ValueError("APEX 문서를 선택해 주세요.")
        copies = int(self.apex_copies_var.get())
        selected_numbers = self.selected_apex_numbers(document)
        spans = self.selected_apex_spans(document)
        return self.make_apex_block_for_selection(
            document,
            self.apex_part_var.get(),
            spans,
            copies,
            selected_numbers,
        )

    def add_apex_material_block(self) -> bool:
        try:
            block = self.apply_new_block_name(self.make_apex_material_block())
        except Exception as error:
            messagebox.showwarning(APP_TITLE, str(error))
            return False
        self.add_material_block(block)
        self.clear_new_block_name()
        self.apex_copies_var.set(1)
        self.apex_selected_numbers_var.set("")
        return True

    def current_textbook_edition(self) -> TextbookEdition | None:
        return self.textbook_catalog.find_edition(
            self.textbook_subject_var.get(),
            self.textbook_publisher_var.get(),
        )

    def textbook_pending_status_note(self) -> str:
        pending = tuple(
            edition
            for subject in self.textbook_catalog.subjects
            for edition in subject.editions
            if not edition.is_verified
        )
        if not pending:
            return ""
        labels = ", ".join(
            f"{edition.subject}/{edition.publisher}" for edition in pending[:3]
        )
        suffix = " 외" if len(pending) > 3 else ""
        return f" · 원본 대기 {len(pending)}판본: {labels}{suffix}"

    def refresh_textbook_material_options(self) -> None:
        subjects = tuple(
            subject.label
            for subject in self.textbook_catalog.subjects
            if subject.verified_editions
        )
        self.textbook_subject_combo["values"] = subjects
        if self.textbook_subject_var.get() not in subjects:
            self.textbook_subject_var.set(subjects[0] if subjects else "")
        self.refresh_textbook_publisher_options()
        self.refresh_textbook_variant_options()

    def current_textbook_variant_edition(self) -> TextbookEdition | None:
        return self.textbook_catalog.find_edition(
            self.textbook_variant_subject_var.get(), self.textbook_variant_publisher_var.get()
        )

    def refresh_textbook_variant_options(self) -> None:
        subjects = tuple(
            subject.label for subject in self.textbook_catalog.subjects
            if any(edition.variant is not None for edition in subject.verified_editions)
        )
        self.textbook_variant_subject_combo["values"] = subjects
        if self.textbook_variant_subject_var.get() not in subjects:
            self.textbook_variant_subject_var.set(subjects[0] if subjects else "")
        self.refresh_textbook_variant_publishers()

    def refresh_textbook_variant_publishers(self) -> None:
        subject = self.textbook_catalog.find_subject(self.textbook_variant_subject_var.get())
        publishers = (
            tuple(edition.publisher for edition in subject.verified_editions
                  if edition.variant is not None)
            if subject is not None else ()
        )
        self.textbook_variant_publisher_combo["values"] = publishers
        if self.textbook_variant_publisher_var.get() not in publishers:
            self.textbook_variant_publisher_var.set(publishers[0] if publishers else "")
        self.refresh_textbook_variant_lesson_options()

    def refresh_textbook_variant_lesson_options(self) -> None:
        edition = self.current_textbook_variant_edition()
        lessons = edition.available_lessons("핵심문제") if edition is not None else ()
        labels = tuple(lesson.display_label for lesson in lessons)
        for combo, variable, default_index in (
            (self.textbook_variant_start_combo, self.textbook_variant_start_var, 0),
            (self.textbook_variant_end_combo, self.textbook_variant_end_var,
             min(4, len(labels) - 1)),
        ):
            combo["values"] = labels
            if variable.get() not in labels:
                variable.set(labels[default_index] if labels else "")
        self.textbook_variant_selected_numbers_var.set("")
        self.refresh_textbook_variant_status()

    def selected_textbook_variant_lesson_numbers(self) -> tuple[tuple[int, ...], tuple[int, ...] | None]:
        selected = parse_file_number_list(self.textbook_variant_selected_numbers_var.get().strip())
        if selected:
            return selected, selected
        start = parse_choice_number(self.textbook_variant_start_var.get())
        end = parse_choice_number(self.textbook_variant_end_var.get())
        return tuple(range(min(start, end), max(start, end) + 1)), None

    def refresh_textbook_variant_status(self) -> None:
        button = getattr(self, "textbook_variant_add_button", None)

        def update(message: str, enabled: bool = False) -> None:
            self.textbook_variant_status_var.set(message)
            if button is not None:
                button.configure(state="normal" if enabled else "disabled")

        edition = self.current_textbook_variant_edition()
        if edition is None or edition.variant is None:
            update("등록된 핵심문제 변형 자료가 없습니다.")
            return
        if not edition.variant.path.is_file():
            update(f"PDF를 찾을 수 없습니다: {edition.variant.relative_path}")
            return
        try:
            lesson_numbers, _ = self.selected_textbook_variant_lesson_numbers()
            problem_numbers = selected_textbook_variant_problem_numbers(edition, lesson_numbers)
            pages = selected_textbook_variant_page_numbers(edition, problem_numbers)
        except (ValueError, TypeError) as error:
            update(str(error))
            return
        update(
            f"{edition.subject} / {edition.publisher} · {len(lesson_numbers)}파트 · "
            f"변형 {len(problem_numbers)}문항(A/B/C) · 표지 포함 {len(pages)}쪽 · 세로 프로필", True
        )

    def make_textbook_variant_block_for_selection(
        self, edition: TextbookEdition, lesson_numbers: tuple[int, ...], copies: int,
        selected_numbers: tuple[int, ...] | None = None, bundled: bool = False,
    ) -> PrintBlock:
        if edition.variant is None:
            raise ValueError("핵심문제 변형 PDF가 등록되지 않았습니다.")
        if copies < 1:
            raise ValueError("부수는 1 이상이어야 합니다.")
        problem_numbers = selected_textbook_variant_problem_numbers(edition, lesson_numbers)
        page_numbers = selected_textbook_variant_page_numbers(edition, problem_numbers)
        verify_textbook_asset(edition.variant, verify_sha256=True)
        selection_label = (
            "(" + ",".join(str(number) for number in lesson_numbers) + ")"
            if selected_numbers else f"{min(lesson_numbers):02d}~{max(lesson_numbers):02d}파트"
        )
        display_label = (
            f"교과서 / {edition.subject} / {edition.publisher} / "
            f"핵심문제 변형 / {selection_label}"
        )
        output_path = materialize_pdf_pages(
            edition.variant.path, page_numbers, MATERIAL_RANGE_CACHE_DIR,
            f"교과서_{edition.subject}_{edition.publisher}_핵심문제 변형_{selection_label}",
        )
        printer_route, printer_override = self.resolve_block_printer_route("세로")
        return PrintBlock(
            subject=edition.subject, level=edition.publisher, round_name="핵심문제 변형",
            start=min(lesson_numbers), end=max(lesson_numbers), copies=copies,
            files=[ExamPdf(1, display_label, output_path)], bundled=bundled,
            source="material_textbook_variant", printer_override=printer_override,
            display_label=display_label, printer_route=printer_route,
            selected_numbers=selected_numbers,
        )

    def add_textbook_variant_block(self) -> bool:
        try:
            edition = self.current_textbook_variant_edition()
            if edition is None:
                raise ValueError("검수 완료된 핵심문제 변형 판본을 선택해 주세요.")
            numbers, selected = self.selected_textbook_variant_lesson_numbers()
            block = self.apply_new_block_name(
                self.make_textbook_variant_block_for_selection(
                    edition, numbers, int(self.textbook_variant_copies_var.get()), selected
                )
            )
        except Exception as error:
            messagebox.showwarning(APP_TITLE, str(error))
            return False
        self.add_material_block(block)
        self.clear_new_block_name()
        self.textbook_variant_copies_var.set(1)
        self.textbook_variant_selected_numbers_var.set("")
        self.refresh_textbook_variant_status()
        return True

    def refresh_textbook_publisher_options(self) -> None:
        subject = self.textbook_catalog.find_subject(self.textbook_subject_var.get())
        publishers = (
            tuple(edition.publisher for edition in subject.verified_editions)
            if subject is not None
            else ()
        )
        self.textbook_publisher_combo["values"] = publishers
        if self.textbook_publisher_var.get() not in publishers:
            self.textbook_publisher_var.set(publishers[0] if publishers else "")
        self.textbook_selected_numbers_var.set("")
        self.refresh_textbook_document_options()

    def refresh_textbook_document_options(self) -> None:
        edition = self.current_textbook_edition()
        document_kinds = (
            tuple(
                kind
                for kind in TEXTBOOK_DOCUMENT_KINDS
                if edition.available_lessons(kind)
            )
            if edition is not None
            else ()
        )
        self.textbook_document_kind_combo["values"] = document_kinds
        if self.textbook_document_kind_var.get() not in document_kinds:
            self.textbook_document_kind_var.set(document_kinds[0] if document_kinds else "")
        self.textbook_selected_numbers_var.set("")
        self.refresh_textbook_lesson_options()

    def refresh_textbook_lesson_options(self) -> None:
        edition = self.current_textbook_edition()
        document_kind = self.textbook_document_kind_var.get()
        lessons = (
            edition.available_lessons(document_kind)
            if edition is not None and document_kind in TEXTBOOK_DOCUMENT_KINDS
            else ()
        )
        labels = tuple(lesson.display_label for lesson in lessons)
        for combo, variable, default_index in (
            (self.textbook_start_combo, self.textbook_start_var, 0),
            (self.textbook_end_combo, self.textbook_end_var, min(4, len(labels) - 1)),
        ):
            combo["values"] = labels
            if variable.get() not in labels:
                variable.set(labels[default_index] if labels else "")
        self.textbook_selected_numbers_var.set("")
        self.refresh_textbook_status()

    def selected_textbook_lessons(
        self,
        edition: TextbookEdition,
        document_kind: str,
    ) -> tuple[tuple[TextbookLesson, ...], tuple[int, ...] | None]:
        raw_value = self.textbook_selected_numbers_var.get().strip()
        selected_numbers = parse_file_number_list(raw_value)
        if selected_numbers:
            numbers = selected_numbers
        else:
            start = parse_choice_number(self.textbook_start_var.get())
            end = parse_choice_number(self.textbook_end_var.get())
            if start > end:
                start, end = end, start
            numbers = tuple(
                lesson.number for lesson in edition.lessons if start <= lesson.number <= end
            )
        if not numbers:
            raise ValueError("교과서 파트를 선택해 주세요.")

        lessons: list[TextbookLesson] = []
        for number in numbers:
            lesson = edition.lesson(number)
            if lesson is None:
                raise ValueError(
                    f"{edition.subject} / {edition.publisher}에 없는 파트입니다: {number}"
                )
            if not edition.lesson_available(lesson, document_kind):
                raise ValueError(
                    f"{lesson.display_label} {document_kind} 자료 누락: "
                    f"{edition.lesson_unavailable_reason(lesson, document_kind)}"
                )
            lessons.append(lesson)
        return tuple(lessons), selected_numbers or None

    def refresh_textbook_status(self) -> None:
        add_button = getattr(self, "textbook_add_button", None)
        pending_note = self.textbook_pending_status_note()

        def update(message: str, *, enabled: bool = False) -> None:
            self.textbook_status_var.set(message)
            if add_button is not None:
                add_button.configure(state="normal" if enabled else "disabled")

        edition = self.current_textbook_edition()
        if edition is None:
            issue_note = (
                f" · 카탈로그 오류 {len(self.textbook_catalog.issues)}건"
                if self.textbook_catalog.issues
                else ""
            )
            update(f"선택 가능한 교과서 판본이 없습니다{pending_note}{issue_note} · 블록 추가 불가")
            return
        document_kind = self.textbook_document_kind_var.get()
        if document_kind not in TEXTBOOK_DOCUMENT_KINDS:
            update("인쇄할 핵심문제, 문제 또는 해설 문서를 선택해 주세요.")
            return
        asset = edition.asset(document_kind)
        if not asset.path.is_file():
            update(f"교과서 PDF를 찾을 수 없습니다: {asset.relative_path}{pending_note}")
            return
        unavailable = edition.unavailable_lessons(document_kind)
        unavailable_note = ""
        if unavailable:
            labels = ", ".join(lesson.display_label for lesson in unavailable[:3])
            suffix = " 외" if len(unavailable) > 3 else ""
            unavailable_note = f" · {document_kind} 자료 누락 {len(unavailable)}파트: {labels}{suffix}"
        try:
            lessons, _selected_numbers = self.selected_textbook_lessons(
                edition, document_kind
            )
            page_numbers = selected_textbook_page_numbers(
                edition,
                document_kind,
                tuple(lesson.number for lesson in lessons),
            )
        except Exception as error:
            update(str(error) + unavailable_note + pending_note)
            return
        update(
            f"{edition.subject} / {edition.publisher} / {document_kind} · "
            f"{len(lessons)}파트 · {len(page_numbers)}쪽 · 세로 프로필"
            f"{unavailable_note}{pending_note}",
            enabled=True,
        )

    def make_textbook_block_for_selection(
        self,
        edition: TextbookEdition,
        document_kind: str,
        lessons: tuple[TextbookLesson, ...],
        copies: int,
        selected_numbers: tuple[int, ...] | None = None,
        bundled: bool = False,
    ) -> PrintBlock:
        if not edition.is_verified:
            raise ValueError("검수 완료되지 않은 교과서 판본입니다.")
        if copies < 1:
            raise ValueError("부수는 1 이상이어야 합니다.")
        if not lessons:
            raise ValueError("교과서 파트를 선택해 주세요.")
        asset = edition.asset(document_kind)
        verify_textbook_asset(asset)
        lesson_numbers = tuple(lesson.number for lesson in lessons)
        page_numbers = selected_textbook_page_numbers(
            edition, document_kind, lesson_numbers
        )
        selection_label = (
            "(" + ",".join(str(number) for number in lesson_numbers) + ")"
            if selected_numbers
            else f"{min(lesson_numbers):02d}~{max(lesson_numbers):02d}파트"
        )
        display_label = (
            f"교과서 / {edition.subject} / {edition.publisher} / "
            f"{document_kind} / {selection_label}"
        )
        output_path = materialize_pdf_pages(
            asset.path,
            page_numbers,
            MATERIAL_RANGE_CACHE_DIR,
            f"교과서_{edition.subject}_{edition.publisher}_{document_kind}_{selection_label}",
        )
        printer_route, printer_override = self.resolve_block_printer_route("세로")
        return PrintBlock(
            subject=edition.subject,
            level=edition.publisher,
            round_name=document_kind,
            start=min(lesson_numbers),
            end=max(lesson_numbers),
            copies=copies,
            files=[ExamPdf(1, display_label, output_path)],
            bundled=bundled,
            source="material_textbook",
            printer_override=printer_override,
            display_label=display_label,
            printer_route=printer_route,
            selected_numbers=selected_numbers,
        )

    def make_textbook_material_block(self) -> PrintBlock:
        edition = self.current_textbook_edition()
        if edition is None:
            raise ValueError("검수 완료된 교과서 판본을 선택해 주세요.")
        document_kind = self.textbook_document_kind_var.get()
        lessons, selected_numbers = self.selected_textbook_lessons(
            edition, document_kind
        )
        return self.make_textbook_block_for_selection(
            edition,
            document_kind,
            lessons,
            int(self.textbook_copies_var.get()),
            selected_numbers,
        )

    def add_textbook_material_block(self) -> bool:
        try:
            block = self.apply_new_block_name(self.make_textbook_material_block())
        except Exception as error:
            messagebox.showwarning(APP_TITLE, str(error))
            return False
        self.add_material_block(block)
        self.clear_new_block_name()
        self.textbook_copies_var.set(1)
        self.textbook_selected_numbers_var.set("")
        self.refresh_textbook_status()
        return True

    def current_able_subject(self) -> AbleSubject | None:
        return self.able_material_catalog.find(self.able_subject_var.get())

    def able_available_lesson_numbers(
        self,
        subject: AbleSubject,
        document_kind: str | None = None,
    ) -> tuple[int, ...]:
        kind = document_kind or able_print_composition_settings(
            self.able_document_kind_var.get()
        )[1]
        if kind == "개념정리":
            return ()
        lesson_numbers = subject.lesson_numbers
        if kind not in {"백지리뷰", ABLE_LEGACY_BUNDLE_KIND} or subject.review_document is None:
            return lesson_numbers
        try:
            review_page_count = get_pdf_page_count(subject.review_document)
        except Exception:
            return ()
        return tuple(number for number in lesson_numbers if number <= review_page_count)

    def refresh_able_material_options(self) -> None:
        subjects = tuple(
            item.subject
            for item in self.able_material_catalog.subjects
            if item.full_documents or item.lesson_documents or item.review_document is not None
        )
        self.able_subject_combo["values"] = subjects
        if subjects and self.able_subject_var.get() not in subjects:
            self.able_subject_var.set(subjects[0])
        # A numbered lesson list belongs to one subject; do not carry it into
        # the next subject after a combobox selection.
        self.able_selected_numbers_var.set("")
        self.able_document_kind_combo["values"] = ABLE_PRINT_COMPOSITION_CHOICES
        if self.able_document_kind_var.get() not in ABLE_PRINT_COMPOSITION_CHOICES:
            self.able_document_kind_var.set("교재 + 백지리뷰")
        self.refresh_able_document_options(reset_to_lesson=False)

    def handle_able_document_kind_changed(self) -> None:
        self.able_selected_numbers_var.set("")
        self.refresh_able_document_options(reset_to_lesson=True)

    def refresh_able_document_options(self, *, reset_to_lesson: bool) -> None:
        del reset_to_lesson
        mode, _document_kind = able_print_composition_settings(
            self.able_document_kind_var.get()
        )
        self.able_mode_var.set(mode)
        self.refresh_able_lesson_options()

    def refresh_able_lesson_options(self) -> None:
        subject = self.current_able_subject()
        if self.able_mode_var.get() == "풀 세트":
            self.able_selected_numbers_var.set("")
            self.set_material_lesson_range_state(
                self.able_start_combo,
                self.able_end_combo,
                enabled=False,
            )
            if hasattr(self, "able_selected_numbers_entry"):
                self.able_selected_numbers_entry.configure(state="disabled")
            self.able_start_combo["values"] = ()
            self.able_end_combo["values"] = ()
            self.able_start_var.set("")
            self.able_end_var.set("")
        else:
            self.set_material_lesson_range_state(
                self.able_start_combo,
                self.able_end_combo,
                enabled=True,
            )
            if hasattr(self, "able_selected_numbers_entry"):
                self.able_selected_numbers_entry.configure(state="normal")
            lesson_numbers = () if subject is None else self.able_available_lesson_numbers(subject)
            lesson_values: list[str] = []
            for number in lesson_numbers:
                span = subject.lesson_span(number) if subject is not None else None
                if span is None:
                    lesson_values.append(f"{number:02d}. {number}차시")
                else:
                    lesson_values.append(f"{number:02d}. {number}차시 (p.{span.start_page}~{span.end_page})")
            values = tuple(lesson_values)
            self.able_start_combo["values"] = values
            self.able_end_combo["values"] = values
            self.able_start_var.set(values[0] if values else "")
            self.able_end_var.set(values[-1] if values else "")
        self.refresh_able_status()

    def refresh_able_status(self) -> None:
        subject = self.current_able_subject()
        if subject is None:
            self.able_status_var.set("선택 가능한 A:ble PDF가 없습니다.")
            return
        mode, document_kind = able_print_composition_settings(
            self.able_document_kind_var.get()
        )
        self.able_mode_var.set(mode)
        if mode == "풀 세트":
            if document_kind == ABLE_LEGACY_BUNDLE_KIND:
                if not subject.full_set_documents:
                    self.able_status_var.set("풀 세트에는 개념정리·교재·백지리뷰가 모두 필요합니다.")
                    return
                self.able_status_var.set(
                    "PDF 3개 · 개념정리(세로) + 교재(A:ble 2x2) + 백지리뷰(세로)"
                )
                return
            document = self.able_full_document(subject, document_kind)
            if document is None:
                self.able_status_var.set(f"{subject.subject} {document_kind} PDF를 찾을 수 없습니다.")
                return
            self.able_status_var.set(f"{document_kind} 전체 PDF 1개 · 세로")
            return
        try:
            selected_numbers = self.selected_able_numbers(subject)
            if selected_numbers:
                count = len(selected_numbers)
                selection_label = "지정 차시 (" + ",".join(str(number) for number in selected_numbers) + ")"
            else:
                start, end = self.selected_able_range()
                available = self.able_available_lesson_numbers(subject, document_kind)
                count = len([number for number in available if start <= number <= end])
                selection_label = "차시 범위"
            if document_kind == ABLE_LEGACY_BUNDLE_KIND:
                if subject.review_document is None:
                    self.able_status_var.set("교재 + 백지리뷰 구성에는 백지리뷰 PDF가 필요합니다.")
                    return
                self.able_status_var.set(
                    f"{selection_label} · {count}차시 · "
                    "교재 1묶음(A:ble 2x2·스테이플 1회) + "
                    "백지리뷰 1묶음(세로·스테이플 1회)"
                )
            elif document_kind == "백지리뷰":
                if subject.review_document is None:
                    self.able_status_var.set("백지리뷰 PDF를 찾을 수 없습니다.")
                    return
                self.able_status_var.set(
                    f"{selection_label} · {count}개 · 백지리뷰 1묶음 · 세로 · 스테이플 1회"
                )
            else:
                self.able_status_var.set(
                    f"{selection_label} · {count}개 · 교재 렉쳐노트 1묶음 · "
                    "A:ble 2x2 · 스테이플 1회"
                )
        except Exception:
            self.able_status_var.set("차시 범위 또는 개별 차시를 선택하세요.")

    def selected_able_range(self) -> tuple[int, int]:
        start = parse_choice_number(self.able_start_var.get())
        end = parse_choice_number(self.able_end_var.get())
        return (start, end) if start <= end else (end, start)

    def selected_able_numbers(self, subject: AbleSubject | None = None) -> tuple[int, ...] | None:
        value = getattr(self, "able_selected_numbers_var", None)
        raw_value = str(value.get()).strip() if value is not None else ""
        numbers = parse_file_number_list(raw_value)
        if not numbers:
            return None
        subject = subject or self.current_able_subject()
        if subject is None:
            raise ValueError("A:ble 과목을 선택해 주세요.")
        document_kind_var = getattr(self, "able_document_kind_var", None)
        document_kind = able_print_composition_settings(
            document_kind_var.get() if document_kind_var is not None else "교재"
        )[1]
        available = set(self.able_available_lesson_numbers(subject, document_kind))
        missing = [number for number in numbers if number not in available]
        if missing:
            missing_label = ", ".join(str(number) for number in missing)
            raise ValueError(f"현재 A:ble 과목에 없는 차시입니다: {missing_label}")
        return numbers

    @staticmethod
    def able_full_document(subject: AbleSubject, document_kind: str) -> Path | None:
        if document_kind == "교재":
            return subject.combined_document
        if document_kind == "백지리뷰":
            return subject.review_document
        if document_kind == "개념정리":
            return subject.concept_document
        return None

    def make_able_lesson_files(
        self,
        subject: AbleSubject,
        lesson_numbers: tuple[int, ...],
        document_kind: str,
    ) -> list[ExamPdf]:
        """Build one stapled print document for each selected A:ble document type."""
        if not lesson_numbers:
            raise ValueError("선택한 A:ble 차시 범위가 비어 있습니다.")
        document_kind = normalize_able_document_kind(document_kind)
        if document_kind == "개념정리":
            raise ValueError("개념정리는 차시별 페이지 맵이 없어 풀 세트로만 인쇄할 수 있습니다.")
        if document_kind not in {"교재", "백지리뷰", ABLE_LEGACY_BUNDLE_KIND}:
            raise ValueError(f"알 수 없는 A:ble 자료 종류입니다: {document_kind}")

        include_textbook = document_kind in {"교재", ABLE_LEGACY_BUNDLE_KIND}
        include_review = document_kind in {"백지리뷰", ABLE_LEGACY_BUNDLE_KIND}
        review_page_count = 0
        if include_review:
            if subject.review_document is None:
                raise ValueError(f"{subject.subject} 백지리뷰 PDF를 찾을 수 없습니다.")
            review_page_count = get_pdf_page_count(subject.review_document)
            if max(lesson_numbers) > review_page_count:
                raise ValueError(
                    f"{subject.subject} 백지리뷰는 {review_page_count}차시까지만 있습니다."
                )

        selection_label = ",".join(str(number) for number in lesson_numbers)
        combined_document = subject.combined_document if include_textbook else None
        files: list[ExamPdf] = []
        if include_textbook:
            lesson_spans = tuple(subject.lesson_span(number) for number in lesson_numbers)
            textbook_label = f"Able_{subject.subject}_{selection_label}차시_렉쳐노트_묶음"
            if combined_document is not None and all(span is not None for span in lesson_spans):
                combined_pages: list[int] = []
                for span in lesson_spans:
                    assert span is not None
                    for start_page, end_page in span.effective_page_ranges:
                        combined_pages.extend(range(start_page, end_page + 1))
                textbook_path = materialize_pdf_pages(
                    combined_document,
                    combined_pages,
                    MATERIAL_RANGE_CACHE_DIR,
                    textbook_label,
                )
            else:
                textbook_paths: list[Path] = []
                for number in lesson_numbers:
                    path = subject.lesson_path(number)
                    if path is None:
                        raise ValueError(
                            f"{subject.subject} {number}차시 렉쳐노트 PDF를 찾을 수 없습니다."
                        )
                    textbook_paths.append(path)
                textbook_path = materialize_pdf_documents(
                    textbook_paths,
                    MATERIAL_RANGE_CACHE_DIR,
                    textbook_label,
                )
            files.append(
                make_able_exam_pdf(
                    len(files) + 1,
                    f"{subject.subject} {selection_label}차시 교재",
                    textbook_path,
                )
            )

        if include_review:
            assert subject.review_document is not None
            review_path = materialize_pdf_pages(
                subject.review_document,
                lesson_numbers,
                MATERIAL_RANGE_CACHE_DIR,
                f"Able_{subject.subject}_{selection_label}차시_백지리뷰_묶음",
            )
            files.append(
                make_able_exam_pdf(
                    len(files) + 1,
                    f"{subject.subject} {selection_label}차시 백지리뷰",
                    review_path,
                )
            )
        return files

    def make_able_block_for_selection(
        self,
        subject: AbleSubject,
        mode: str,
        copies: int,
        *,
        document_kind: str = ABLE_LEGACY_BUNDLE_KIND,
        selected_numbers: tuple[int, ...] | None = None,
        start: int | None = None,
        end: int | None = None,
        bundled: bool = True,
        resolved_route: tuple[str, str | None] | None = None,
    ) -> PrintBlock:
        if copies < 1:
            raise ValueError("부수는 1 이상이어야 합니다.")
        document_kind = normalize_able_document_kind(document_kind)
        if document_kind not in {*ABLE_DOCUMENT_KIND_CHOICES, ABLE_LEGACY_BUNDLE_KIND}:
            raise ValueError("A:ble 자료 종류를 선택해 주세요.")
        files: list[ExamPdf] = []
        if mode == "풀 세트":
            if document_kind == ABLE_LEGACY_BUNDLE_KIND:
                source_paths = subject.full_set_documents
                if not source_paths:
                    raise ValueError(
                        f"{subject.subject} 풀 세트에 필요한 개념정리노트·A:ble 합본·백지리뷰를 모두 찾을 수 없습니다."
                    )
            else:
                document = self.able_full_document(subject, document_kind)
                if document is None:
                    raise ValueError(f"{subject.subject} {document_kind} PDF를 찾을 수 없습니다.")
                source_paths = (document,)
            files = [
                make_able_exam_pdf(index, path.stem, path)
                for index, path in enumerate(source_paths, start=1)
            ]
            start, end = 1, len(files)
            kind_label = "전체 자료" if document_kind == ABLE_LEGACY_BUNDLE_KIND else document_kind
            display_label = f"A:ble / {subject.subject} / {kind_label} / 풀 세트"
            selected_numbers = None
        else:
            if document_kind == "개념정리":
                raise ValueError("개념정리는 풀 세트로만 인쇄할 수 있습니다.")
            if selected_numbers:
                lesson_numbers = selected_numbers
                start, end = min(lesson_numbers), max(lesson_numbers)
                selection_label = "(" + ",".join(str(number) for number in lesson_numbers) + ")"
            else:
                if start is None or end is None:
                    raise ValueError("선택한 A:ble 차시 범위가 비어 있습니다.")
                if start > end:
                    start, end = end, start
                available_numbers = self.able_available_lesson_numbers(subject, document_kind)
                lesson_numbers = tuple(number for number in available_numbers if start <= number <= end)
                selection_label = f"{start:02d}~{end:02d}차시"
            files = self.make_able_lesson_files(subject, lesson_numbers, document_kind)
            kind_label = (
                "교재+백지리뷰"
                if document_kind == ABLE_LEGACY_BUNDLE_KIND
                else document_kind
            )
            display_label = f"A:ble / {subject.subject} / {kind_label} / {selection_label}"
        route_label = "세로" if document_kind in {"백지리뷰", "개념정리"} else "A:ble"
        printer_route, printer_override = (
            resolved_route
            if resolved_route is not None
            else self.resolve_block_printer_route(route_label)
        )
        return PrintBlock(
            subject=subject.subject,
            level="A:ble",
            round_name=mode,
            start=start,
            end=end,
            copies=copies,
            files=files,
            bundled=bundled,
            source="material_able",
            printer_override=printer_override,
            display_label=display_label,
            pages_per_sheet=1,
            printer_route=printer_route,
            selected_numbers=selected_numbers,
            material_kind=document_kind,
        )

    def make_able_material_block(self) -> PrintBlock:
        subject = self.current_able_subject()
        if subject is None:
            raise ValueError("A:ble 과목을 선택해 주세요.")
        copies = int(self.able_copies_var.get())
        document_kind_var = getattr(self, "able_document_kind_var", None)
        mode, document_kind = able_print_composition_settings(
            document_kind_var.get() if document_kind_var is not None else "교재 + 백지리뷰"
        )
        if mode == "풀 세트":
            return self.make_able_block_for_selection(
                subject,
                mode,
                copies,
                document_kind=document_kind,
            )
        selected_numbers = self.selected_able_numbers(subject)
        start: int | None = None
        end: int | None = None
        if not selected_numbers:
            start, end = self.selected_able_range()
        return self.make_able_block_for_selection(
            subject,
            mode,
            copies,
            document_kind=document_kind,
            selected_numbers=selected_numbers,
            start=start,
            end=end,
        )

    def add_able_material_block(self) -> bool:
        if getattr(self, "able_build_pending", False):
            messagebox.showinfo(APP_TITLE, "A:ble 자료를 준비 중입니다. 완료 후 다시 눌러 주세요.")
            return False
        try:
            subject = self.current_able_subject()
            if subject is None:
                raise ValueError("A:ble 과목을 선택해 주세요.")
            copies = int(self.able_copies_var.get())
            if copies < 1:
                raise ValueError("부수는 1 이상이어야 합니다.")
            mode, document_kind = able_print_composition_settings(
                self.able_document_kind_var.get()
            )
            selected_text = self.able_selected_numbers_var.get()
            selected_numbers = (
                parse_file_number_list(selected_text)
                if mode != "풀 세트"
                else None
            )
            start: int | None = None
            end: int | None = None
            if mode != "풀 세트" and not selected_numbers:
                start, end = self.selected_able_range()
            route_label = "세로" if document_kind in {"백지리뷰", "개념정리"} else "A:ble"
            resolved_route = self.resolve_block_printer_route(route_label)
            name_enabled = bool(self.name_separator_page_var.get())
            captured_name = self.new_block_name_var.get()
            normalized_name = (
                normalize_name_separator_label(captured_name)
                if name_enabled
                else ""
            )
        except Exception as error:
            messagebox.showwarning(APP_TITLE, str(error))
            return False

        token = self.reserve_block_commit()
        epoch = self.block_preparation_epoch
        self.able_build_pending = True
        self.able_status_var.set("A:ble PDF 준비 중 · 다른 화면과 작업 큐는 계속 사용할 수 있습니다.")

        def work() -> object:
            if mode != "풀 세트" and selected_numbers:
                available = set(self.able_available_lesson_numbers(subject, document_kind))
                missing = [number for number in selected_numbers if number not in available]
                if missing:
                    raise ValueError(
                        "현재 A:ble 과목에 없는 차시입니다: "
                        + ", ".join(str(number) for number in missing)
                    )
            block = self.make_able_block_for_selection(
                subject,
                mode,
                copies,
                document_kind=document_kind,
                selected_numbers=selected_numbers,
                start=start,
                end=end,
                resolved_route=resolved_route,
            )
            block.name_separator_label = normalized_name
            return block

        def finish(future: object) -> None:
            self.able_build_pending = False
            try:
                block = future.result()  # type: ignore[attr-defined]
            except Exception as error:
                message = str(error)

                def show_error() -> None:
                    self.refresh_able_status()
                    messagebox.showwarning(APP_TITLE, message)

                self.complete_block_commit(token, show_error)
                return

            def commit() -> None:
                self._commit_material_block(block)
                if self.able_copies_var.get() == copies:
                    self.able_copies_var.set(1)
                if self.able_selected_numbers_var.get() == selected_text:
                    self.able_selected_numbers_var.set("")
                if self.new_block_name_var.get() == captured_name:
                    self.clear_new_block_name()
                self.refresh_able_status()

            self.complete_block_commit(token, commit)

        try:
            self.submit_background_block_build(work, finish, epoch=epoch)
        except Exception as error:
            self.able_build_pending = False
            self.cancel_block_commit(token)
            self.refresh_able_status()
            messagebox.showwarning(APP_TITLE, str(error))
            return False
        return True

    @staticmethod
    def lesson_material_metadata(prefix: str) -> tuple[str, str, str, str]:
        if prefix == "frequent_type":
            return "빈출유형", "빈출유형", "빈출유형", "material_frequent_type"
        if prefix == "training_pack":
            return "트레이닝팩", "트팩", "트팩", "material_training_pack"
        raise ValueError(f"알 수 없는 차시형 교재 종류입니다: {prefix}")

    def lesson_material_catalog(self, prefix: str):
        return getattr(self, f"{prefix}_material_catalog")

    def current_lesson_material_subject(self, prefix: str) -> TrainingPackSubject | None:
        catalog = self.lesson_material_catalog(prefix)
        subject_var = getattr(self, f"{prefix}_subject_var")
        return catalog.find(subject_var.get())

    def refresh_lesson_material_options(self, prefix: str) -> None:
        catalog = self.lesson_material_catalog(prefix)
        subject_var = getattr(self, f"{prefix}_subject_var")
        subject_combo = getattr(self, f"{prefix}_subject_combo")
        mode_var = getattr(self, f"{prefix}_mode_var")
        mode_combo = getattr(self, f"{prefix}_mode_combo")
        selected_var = getattr(self, f"{prefix}_selected_numbers_var")
        subjects = catalog.subject_names
        subject_combo["values"] = subjects
        if subjects and subject_var.get() not in subjects:
            subject_var.set(subjects[0])
        mode_combo["values"] = ("풀 세트", "차시별")
        if mode_var.get() not in mode_combo["values"]:
            mode_var.set("차시별")
        selected_var.set("")
        self.refresh_lesson_material_lesson_options(prefix)

    def refresh_lesson_material_lesson_options(self, prefix: str) -> None:
        subject = self.current_lesson_material_subject(prefix)
        mode_var = getattr(self, f"{prefix}_mode_var")
        start_combo = getattr(self, f"{prefix}_start_combo")
        end_combo = getattr(self, f"{prefix}_end_combo")
        selected_entry = getattr(self, f"{prefix}_selected_numbers_entry")
        start_var = getattr(self, f"{prefix}_start_var")
        end_var = getattr(self, f"{prefix}_end_var")
        selected_var = getattr(self, f"{prefix}_selected_numbers_var")
        if mode_var.get() == "풀 세트":
            self.set_material_lesson_range_state(start_combo, end_combo, enabled=False)
            selected_entry.configure(state="disabled")
            start_combo["values"] = ()
            end_combo["values"] = ()
            start_var.set("")
            end_var.set("")
            selected_var.set("")
            self.refresh_lesson_material_status(prefix)
            return

        self.set_material_lesson_range_state(start_combo, end_combo, enabled=True)
        selected_entry.configure(state="normal")
        spans = () if subject is None else subject.lessons
        values = tuple(self.material_span_choice(span) for span in spans)
        start_combo["values"] = values
        end_combo["values"] = values
        start_var.set(values[0] if values else "")
        end_var.set(values[-1] if values else "")
        self.refresh_lesson_material_status(prefix)

    def selected_lesson_material_range(self, prefix: str) -> tuple[int, int]:
        start = parse_choice_number(getattr(self, f"{prefix}_start_var").get())
        end = parse_choice_number(getattr(self, f"{prefix}_end_var").get())
        return (start, end) if start <= end else (end, start)

    def selected_lesson_material_numbers(
        self,
        prefix: str,
        subject: TrainingPackSubject | None = None,
    ) -> tuple[int, ...] | None:
        material_label, _short_label, _level, _source = self.lesson_material_metadata(prefix)
        raw_value = getattr(self, f"{prefix}_selected_numbers_var").get().strip()
        numbers = parse_file_number_list(raw_value)
        if not numbers:
            return None
        subject = subject or self.current_lesson_material_subject(prefix)
        if subject is None:
            raise ValueError(f"{material_label} 과목을 선택해 주세요.")
        available = set(subject.lesson_numbers)
        missing = [number for number in numbers if number not in available]
        if missing:
            missing_label = ", ".join(str(number) for number in missing)
            raise ValueError(f"현재 {material_label} 과목에 없는 차시입니다: {missing_label}")
        return numbers

    def lesson_material_page_numbers(
        self,
        prefix: str,
        subject: TrainingPackSubject,
        lesson_numbers: tuple[int, ...],
    ) -> tuple[int, ...]:
        if prefix == "frequent_type":
            return frequent_type_page_numbers(subject, lesson_numbers)
        return training_pack_page_numbers(subject, lesson_numbers)

    def refresh_lesson_material_status(self, prefix: str) -> None:
        material_label, short_label, _level, _source = self.lesson_material_metadata(prefix)
        subject = self.current_lesson_material_subject(prefix)
        status_var = getattr(self, f"{prefix}_status_var")
        mode_var = getattr(self, f"{prefix}_mode_var")
        if subject is None:
            status_var.set(f"선택 가능한 {material_label} PDF가 없습니다.")
            return
        if mode_var.get() == "풀 세트":
            status_var.set(
                f"풀 세트 · PDF 1~{subject.page_count}쪽 · {subject.page_count}쪽 · 세로 고정"
            )
            return
        try:
            selected_numbers = self.selected_lesson_material_numbers(prefix, subject)
            if selected_numbers:
                lesson_numbers = selected_numbers
                selection_label = "지정 차시 (" + ",".join(str(number) for number in selected_numbers) + ")"
            else:
                start, end = self.selected_lesson_material_range(prefix)
                lesson_numbers = tuple(number for number in subject.lesson_numbers if start <= number <= end)
                selection_label = f"{start:02d}~{end:02d}차시"
            page_count = len(self.lesson_material_page_numbers(prefix, subject, lesson_numbers))
            exclusion_label = "표지/해설 제외" if prefix == "training_pack" else "해설 제외"
            status_var.set(
                f"{selection_label} · {short_label} 문제지 {page_count}쪽 · "
                f"세로 고정 · {exclusion_label}"
            )
        except Exception:
            status_var.set("차시 범위 또는 개별 차시를 선택하세요.")

    def make_lesson_material_full_block(
        self,
        prefix: str,
        subject: TrainingPackSubject,
        copies: int,
        bundled: bool = False,
    ) -> PrintBlock:
        _material_label, short_label, level, source = self.lesson_material_metadata(prefix)
        if copies < 1:
            raise ValueError("부수는 1 이상이어야 합니다.")
        display_label = f"{short_label} / {subject.subject} / 풀 세트"
        printer_route, printer_override = self.resolve_block_printer_route("세로")
        item = ExamPdf(
            1,
            display_label,
            subject.path,
            printer_override=printer_override,
            pages_per_sheet=1,
        )
        return PrintBlock(
            subject=subject.subject,
            level=level,
            round_name="풀 세트",
            start=1,
            end=subject.page_count,
            copies=copies,
            files=[item],
            bundled=bundled,
            source=source,
            printer_override=printer_override,
            display_label=display_label,
            pages_per_sheet=1,
            printer_route=printer_route,
        )

    def make_lesson_material_block_for_selection(
        self,
        prefix: str,
        subject: TrainingPackSubject,
        lesson_numbers: tuple[int, ...],
        copies: int,
        selected_numbers: tuple[int, ...] | None,
        selection_label: str,
        bundled: bool = False,
    ) -> PrintBlock:
        material_label, short_label, level, source = self.lesson_material_metadata(prefix)
        if not lesson_numbers:
            raise ValueError(f"선택한 {material_label} 차시 범위가 비어 있습니다.")
        page_numbers = self.lesson_material_page_numbers(prefix, subject, lesson_numbers)
        output_path = materialize_pdf_pages(
            subject.path,
            page_numbers,
            MATERIAL_RANGE_CACHE_DIR,
            f"{short_label}_{subject.subject}_{selection_label}_문제지",
        )
        display_label = f"{short_label} / {subject.subject} / {selection_label} / 문제지"
        printer_route, printer_override = self.resolve_block_printer_route("세로")
        item = ExamPdf(
            1,
            display_label,
            output_path,
            printer_override=printer_override,
            pages_per_sheet=1,
        )
        return PrintBlock(
            subject=subject.subject,
            level=level,
            round_name="차시별",
            start=min(lesson_numbers),
            end=max(lesson_numbers),
            copies=copies,
            files=[item],
            bundled=bundled,
            source=source,
            printer_override=printer_override,
            display_label=display_label,
            pages_per_sheet=1,
            printer_route=printer_route,
            selected_numbers=selected_numbers,
        )

    def make_lesson_material_block(self, prefix: str) -> PrintBlock:
        material_label, _short_label, _level, _source = self.lesson_material_metadata(prefix)
        subject = self.current_lesson_material_subject(prefix)
        if subject is None:
            raise ValueError(f"{material_label} 과목을 선택해 주세요.")
        copies = int(getattr(self, f"{prefix}_copies_var").get())
        if copies < 1:
            raise ValueError("부수는 1 이상이어야 합니다.")
        if getattr(self, f"{prefix}_mode_var").get() == "풀 세트":
            return self.make_lesson_material_full_block(prefix, subject, copies)
        selected_numbers = self.selected_lesson_material_numbers(prefix, subject)
        if selected_numbers:
            lesson_numbers = selected_numbers
            selection_label = "(" + ",".join(str(number) for number in lesson_numbers) + ")"
        else:
            start, end = self.selected_lesson_material_range(prefix)
            lesson_numbers = tuple(number for number in subject.lesson_numbers if start <= number <= end)
            selection_label = f"{start:02d}~{end:02d}차시"
        if not lesson_numbers:
            raise ValueError(f"선택한 {material_label} 차시 범위가 비어 있습니다.")
        return self.make_lesson_material_block_for_selection(
            prefix,
            subject,
            lesson_numbers,
            copies,
            selected_numbers,
            selection_label,
        )

    def add_lesson_material_block(self, prefix: str) -> bool:
        try:
            block = self.apply_new_block_name(self.make_lesson_material_block(prefix))
        except Exception as error:
            messagebox.showwarning(APP_TITLE, str(error))
            return False
        self.add_material_block(block)
        self.clear_new_block_name()
        getattr(self, f"{prefix}_copies_var").set(1)
        getattr(self, f"{prefix}_selected_numbers_var").set("")
        self.refresh_lesson_material_status(prefix)
        return True

    def current_training_pack_subject(self) -> TrainingPackSubject | None:
        return self.current_lesson_material_subject("training_pack")

    def refresh_training_pack_material_options(self) -> None:
        self.refresh_lesson_material_options("training_pack")

    def refresh_training_pack_lesson_options(self) -> None:
        self.refresh_lesson_material_lesson_options("training_pack")

    def selected_training_pack_range(self) -> tuple[int, int]:
        return self.selected_lesson_material_range("training_pack")

    def selected_training_pack_numbers(
        self,
        subject: TrainingPackSubject | None = None,
    ) -> tuple[int, ...] | None:
        return self.selected_lesson_material_numbers("training_pack", subject)

    def refresh_training_pack_status(self) -> None:
        self.refresh_lesson_material_status("training_pack")

    def make_training_pack_full_block(
        self,
        subject: TrainingPackSubject,
        copies: int,
        bundled: bool = False,
    ) -> PrintBlock:
        return self.make_lesson_material_full_block("training_pack", subject, copies, bundled)

    def make_training_pack_block_for_selection(
        self,
        subject: TrainingPackSubject,
        lesson_numbers: tuple[int, ...],
        copies: int,
        selected_numbers: tuple[int, ...] | None,
        selection_label: str,
        bundled: bool = False,
    ) -> PrintBlock:
        return self.make_lesson_material_block_for_selection(
            "training_pack",
            subject,
            lesson_numbers,
            copies,
            selected_numbers,
            selection_label,
            bundled,
        )

    def make_training_pack_material_block(self) -> PrintBlock:
        return self.make_lesson_material_block("training_pack")

    def add_training_pack_block(self) -> bool:
        return self.add_lesson_material_block("training_pack")

    def current_frequent_type_subject(self) -> TrainingPackSubject | None:
        return self.current_lesson_material_subject("frequent_type")

    def refresh_frequent_type_material_options(self) -> None:
        self.refresh_lesson_material_options("frequent_type")

    def refresh_frequent_type_lesson_options(self) -> None:
        self.refresh_lesson_material_lesson_options("frequent_type")

    def refresh_frequent_type_status(self) -> None:
        self.refresh_lesson_material_status("frequent_type")

    def make_frequent_type_material_block(self) -> PrintBlock:
        return self.make_lesson_material_block("frequent_type")

    def add_frequent_type_block(self) -> bool:
        return self.add_lesson_material_block("frequent_type")

    def current_gyopyeong_subject(self) -> GyopyeongSubject | None:
        return self.gyopyeong_material_catalog.find_subject(
            self.gyopyeong_subject_var.get()
        )

    def current_gyopyeong_part(self) -> GyopyeongPart | None:
        subject = self.current_gyopyeong_subject()
        if subject is None:
            return None
        return subject.find_part(self.gyopyeong_part_var.get())

    def current_gyopyeong_lesson(self) -> GyopyeongLesson | None:
        part = self.current_gyopyeong_part()
        if part is None:
            return None
        try:
            number = parse_choice_number(self.gyopyeong_lesson_var.get())
        except ValueError:
            return None
        return part.find_lesson(number)

    def refresh_gyopyeong_material_options(self) -> None:
        subjects = self.gyopyeong_material_catalog.subject_names
        self.gyopyeong_subject_combo["values"] = subjects
        if subjects and self.gyopyeong_subject_var.get() not in subjects:
            self.gyopyeong_subject_var.set(subjects[0])
        if not subjects:
            self.gyopyeong_subject_var.set("")
        self.refresh_gyopyeong_part_options()

    def refresh_gyopyeong_part_options(self) -> None:
        subject = self.current_gyopyeong_subject()
        part_names = () if subject is None else subject.part_names
        self.gyopyeong_part_combo.configure(state="readonly")
        self.gyopyeong_part_combo["values"] = part_names
        if part_names and self.gyopyeong_part_var.get() not in part_names:
            self.gyopyeong_part_var.set(part_names[0])
        if not part_names:
            self.gyopyeong_part_var.set("")
        if subject is not None and subject.has_full_range_only:
            self.gyopyeong_part_combo.configure(state="disabled")
        self.refresh_gyopyeong_lesson_options()

    def refresh_gyopyeong_lesson_options(self) -> None:
        part = self.current_gyopyeong_part()
        values = () if part is None else tuple(lesson.choice_label for lesson in part.lessons)
        self.gyopyeong_lesson_combo["values"] = values
        if values and self.gyopyeong_lesson_var.get() not in values:
            self.gyopyeong_lesson_var.set(values[0])
        if not values:
            self.gyopyeong_lesson_var.set("")
        self.refresh_gyopyeong_status()

    def refresh_gyopyeong_status(self) -> None:
        subject = self.current_gyopyeong_subject()
        part = self.current_gyopyeong_part()
        lesson = self.current_gyopyeong_lesson()
        if subject is None or part is None or lesson is None:
            self.gyopyeong_status_var.set("선택 가능한 교평 기출 PDF가 없습니다.")
            return
        self.gyopyeong_status_var.set(
            f"{subject.name} · {part.name} · {lesson.label} · "
            "문제지 PDF 1개 · 세로 고정 · 해설 제외"
        )

    def make_gyopyeong_material_block(self) -> PrintBlock:
        subject = self.current_gyopyeong_subject()
        part = self.current_gyopyeong_part()
        lesson = self.current_gyopyeong_lesson()
        if subject is None:
            raise ValueError("교평 기출 과목을 선택해 주세요.")
        if part is None:
            raise ValueError("교평 기출 파트를 선택해 주세요.")
        if lesson is None:
            raise ValueError("교평 기출 차시를 선택해 주세요.")
        if not lesson.path.is_file():
            raise ValueError(f"교평 기출 PDF를 찾을 수 없습니다: {lesson.path}")
        copies = int(self.gyopyeong_copies_var.get())
        if copies < 1:
            raise ValueError("부수는 1 이상이어야 합니다.")
        printer_route, printer_override = self.resolve_block_printer_route("세로")
        display_label = (
            f"교평 기출 / {subject.name} / {part.name} / "
            f"{lesson.choice_label} / 문제지"
        )
        item = ExamPdf(
            number=1,
            name=display_label,
            path=lesson.path,
            printer_override=printer_override,
            pages_per_sheet=1,
        )
        return PrintBlock(
            subject=subject.name,
            level=part.name,
            round_name=lesson.label,
            start=lesson.number,
            end=lesson.number,
            copies=copies,
            files=[item],
            bundled=False,
            source="material_gyopyeong",
            printer_override=printer_override,
            display_label=display_label,
            pages_per_sheet=1,
            printer_route=printer_route,
        )

    def add_gyopyeong_material_block(self) -> bool:
        try:
            block = self.apply_new_block_name(self.make_gyopyeong_material_block())
        except Exception as error:
            messagebox.showwarning(APP_TITLE, str(error))
            return False
        self.add_material_block(block)
        self.clear_new_block_name()
        self.gyopyeong_copies_var.set(1)
        self.refresh_gyopyeong_status()
        return True

    def current_selected_exam_subject(self) -> SelectedExamSubject | None:
        return self.selected_exam_material_catalog.find_subject(
            self.selected_exam_subject_var.get()
        )

    def current_selected_exam_lesson(self) -> SelectedExamLesson | None:
        subject = self.current_selected_exam_subject()
        if subject is None:
            return None
        try:
            number = parse_choice_number(self.selected_exam_lesson_var.get())
        except ValueError:
            return None
        return subject.find_lesson(number)

    def refresh_selected_exam_material_options(self) -> None:
        subjects = self.selected_exam_material_catalog.subject_names
        self.selected_exam_subject_combo["values"] = subjects
        if subjects and self.selected_exam_subject_var.get() not in subjects:
            self.selected_exam_subject_var.set(subjects[0])
        if not subjects:
            self.selected_exam_subject_var.set("")
        self.refresh_selected_exam_lesson_options()

    def handle_selected_exam_subject_changed(self) -> None:
        self.selected_exam_selected_numbers_var.set("")
        self.refresh_selected_exam_lesson_options()

    def refresh_selected_exam_lesson_options(self) -> None:
        subject = self.current_selected_exam_subject()
        values = () if subject is None else tuple(
            lesson.choice_label for lesson in subject.lessons
        )
        self.selected_exam_lesson_combo["values"] = values
        if values and self.selected_exam_lesson_var.get() not in values:
            self.selected_exam_lesson_var.set(values[0])
        if not values:
            self.selected_exam_lesson_var.set("")
        self.refresh_selected_exam_status()

    def selected_exam_form_selection(
        self,
    ) -> tuple[SelectedExamSubject, tuple[SelectedExamLesson, ...], tuple[int, ...] | None]:
        subject = self.current_selected_exam_subject()
        if subject is None:
            raise ValueError("기출엄선 과목을 선택해 주세요.")
        selected_numbers = parse_file_number_list(
            self.selected_exam_selected_numbers_var.get()
        )
        if selected_numbers:
            lessons: list[SelectedExamLesson] = []
            missing_numbers: list[int] = []
            unavailable_numbers: list[int] = []
            for number in selected_numbers:
                lesson = subject.find_lesson(number)
                if lesson is None:
                    missing_numbers.append(number)
                elif not lesson.is_available:
                    unavailable_numbers.append(number)
                else:
                    lessons.append(lesson)
            if missing_numbers:
                label = ", ".join(str(number) for number in missing_numbers)
                raise ValueError(f"기출엄선 {subject.name}에 없는 차시입니다: {label}")
            if unavailable_numbers:
                label = ", ".join(str(number) for number in unavailable_numbers)
                raise ValueError(
                    f"기출엄선 {subject.name}에 아직 PDF가 등록되지 않은 차시입니다: {label}"
                )
            return subject, tuple(lessons), selected_numbers

        lesson = self.current_selected_exam_lesson()
        if lesson is None:
            raise ValueError("기출엄선 차시를 선택해 주세요.")
        if not lesson.is_available:
            raise ValueError(
                f"기출엄선 {subject.name} {lesson.label} PDF가 아직 등록되지 않았습니다."
            )
        return subject, (lesson,), None

    def make_selected_exam_block_for_lessons(
        self,
        subject: SelectedExamSubject,
        lessons: tuple[SelectedExamLesson, ...],
        copies: int,
        *,
        selected_numbers: tuple[int, ...] | None,
        bundled: bool = False,
    ) -> PrintBlock:
        if copies < 1:
            raise ValueError("부수는 1 이상이어야 합니다.")
        if not lessons:
            raise ValueError("선택된 기출엄선 차시가 없습니다.")
        unavailable = [lesson.number for lesson in lessons if not lesson.is_available]
        if unavailable:
            label = ", ".join(str(number) for number in unavailable)
            raise ValueError(
                f"기출엄선 {subject.name}에 아직 PDF가 등록되지 않은 차시입니다: {label}"
            )

        lesson_numbers = tuple(lesson.number for lesson in lessons)
        start, end = min(lesson_numbers), max(lesson_numbers)
        if selected_numbers:
            number_label = ",".join(str(number) for number in selected_numbers)
            selection_label = f"({number_label})차시"
            round_name = selection_label
        elif len(lessons) == 1:
            selection_label = lessons[0].choice_label
            round_name = lessons[0].label
        else:
            selection_label = f"{start:02d}~{end:02d}차시"
            round_name = selection_label

        printer_route, printer_override = self.resolve_block_printer_route("세로")
        files = [
            ExamPdf(
                number=lesson.number,
                name=f"기출엄선 / {subject.name} / {lesson.choice_label} / 문제지",
                path=lesson.path,
                printer_override=printer_override,
                pages_per_sheet=1,
            )
            for lesson in lessons
        ]
        return PrintBlock(
            subject=subject.name,
            level="기출엄선",
            round_name=round_name,
            start=start,
            end=end,
            copies=copies,
            files=files,
            bundled=bundled,
            source="material_selected_exam",
            printer_override=printer_override,
            display_label=f"기출엄선 / {subject.name} / {selection_label} / 문제지",
            pages_per_sheet=1,
            printer_route=printer_route,
            selected_numbers=selected_numbers,
        )

    def refresh_selected_exam_status(self) -> None:
        try:
            subject, lessons, selected_numbers = self.selected_exam_form_selection()
            if selected_numbers:
                number_label = ",".join(str(number) for number in selected_numbers)
                selection_label = f"({number_label})차시"
            else:
                selection_label = lessons[0].label
            self.selected_exam_status_var.set(
                f"{subject.name} · {selection_label} · 문제지 PDF {len(lessons)}개 · 세로 고정"
            )
            self.selected_exam_add_button.configure(state="normal")
        except ValueError as error:
            self.selected_exam_status_var.set(f"{error} · 블록 추가 불가")
            self.selected_exam_add_button.configure(state="disabled")

    def make_selected_exam_material_block(self) -> PrintBlock:
        subject, lessons, selected_numbers = self.selected_exam_form_selection()
        copies = int(self.selected_exam_copies_var.get())
        return self.make_selected_exam_block_for_lessons(
            subject,
            lessons,
            copies,
            selected_numbers=selected_numbers,
        )

    def add_selected_exam_material_block(self) -> bool:
        try:
            block = self.apply_new_block_name(self.make_selected_exam_material_block())
        except Exception as error:
            messagebox.showwarning(APP_TITLE, str(error))
            return False
        self.add_material_block(block)
        self.clear_new_block_name()
        self.selected_exam_copies_var.set(1)
        self.selected_exam_selected_numbers_var.set("")
        self.refresh_selected_exam_status()
        return True

    def reserve_block_commit(self) -> CommitToken | None:
        commit_queue = getattr(self, "block_commit_queue", None)
        return commit_queue.reserve() if commit_queue is not None else None

    def complete_block_commit(
        self,
        token: CommitToken | None,
        callback: Callable[[], None],
    ) -> bool:
        commit_queue = getattr(self, "block_commit_queue", None)
        if token is None or commit_queue is None:
            callback()
            return True
        return commit_queue.complete(token, callback)

    def cancel_block_commit(self, token: CommitToken | None) -> None:
        commit_queue = getattr(self, "block_commit_queue", None)
        if token is not None and commit_queue is not None:
            commit_queue.cancel(token)

    def cancel_pending_block_preparations(self) -> None:
        self.block_preparation_epoch = getattr(self, "block_preparation_epoch", 0) + 1
        service = getattr(self, "background_build_service", None)
        if service is not None:
            service.cancel_pending()
        commit_queue = getattr(self, "block_commit_queue", None)
        if commit_queue is not None:
            commit_queue.invalidate()
        self.able_build_pending = False
        self.order_build_pending = False
        self.group_build_pending = False
        self.recovery_load_pending = False

    def submit_background_block_build(
        self,
        work: Callable[[], object],
        finish: Callable[[object], None],
        *,
        epoch: int,
    ) -> None:
        service = getattr(self, "background_build_service", None)
        if service is None:
            raise RuntimeError("자료 준비 서비스를 사용할 수 없습니다.")

        def deliver(future: object) -> None:
            # Worker callbacks only publish immutable completion handles.  Tk
            # access happens in poll_background_build_results on the UI thread.
            if epoch != getattr(self, "block_preparation_epoch", -1):
                return
            if getattr(self, "is_closing", False):
                return
            self.background_build_results.put((epoch, finish, future))

        service.submit(work, deliver)
        if self.background_build_poll_after_id is None:
            self.background_build_poll_after_id = self.root.after(
                20,
                self.poll_background_build_results,
            )

    def poll_background_build_results(self) -> None:
        self.background_build_poll_after_id = None
        while True:
            try:
                epoch, finish, future = self.background_build_results.get_nowait()
            except queue.Empty:
                break
            if epoch == self.block_preparation_epoch and not self.is_closing:
                finish(future)
        if (self.able_build_pending or self.order_build_pending or self.group_build_pending or self.recovery_load_pending) and not self.is_closing:
            self.background_build_poll_after_id = self.root.after(
                20,
                self.poll_background_build_results,
            )

    def close_pdf_work_services(self) -> None:
        self.cancel_source_paths_open()
        self.cancel_pending_print_preparation(restore=False)
        print_preparation_worker = getattr(self, "_print_preparation_worker", None)
        if print_preparation_worker is not None:
            print_preparation_worker.close()
        cancel_preview = getattr(self, "cancel_preview_render", None)
        if cancel_preview is not None:
            cancel_preview()
        after_id = getattr(self, "background_build_poll_after_id", None)
        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except tk.TclError:
                pass
            self.background_build_poll_after_id = None
        resource_after_id = getattr(self, "resource_estimate_poll_after_id", None)
        if resource_after_id is not None:
            try:
                self.root.after_cancel(resource_after_id)
            except tk.TclError:
                pass
            self.resource_estimate_poll_after_id = None
        resource_future = getattr(self, "resource_estimate_future", None)
        if resource_future is not None and hasattr(resource_future, "cancel"):
            resource_future.cancel()
        self.resource_estimate_future = None
        self.resource_estimate_revision = getattr(self, "resource_estimate_revision", 0) + 1
        self.cancel_pending_block_preparations()
        service = getattr(self, "background_build_service", None)
        if service is not None:
            service.close()
        scheduler = getattr(self, "pdf_work_scheduler", None)
        if scheduler is not None:
            scheduler.close(wait=False)
        resource_scheduler = getattr(self, "resource_estimate_scheduler", None)
        if resource_scheduler is not None:
            resource_scheduler.close(wait=False)

    def _commit_material_block(self, block: PrintBlock) -> None:
        self.push_undo_state("교재 블록 추가")
        self.blocks.append(block)
        self.block_status_by_id[id(block)] = "대기"
        self.update_queue_view(select_index=len(self.blocks) - 1)
        self.update_preview()
        route_label = (
            block.printer_route
            if block.printer_route != "자동 감지"
            else (block.printer_override or "파일별 프로필")
        )
        self.log(f"교재 블록 추가: {block.summary} -> {route_label}")

    def add_material_block(self, block: PrintBlock) -> None:
        token = self.reserve_block_commit()
        self.complete_block_commit(token, lambda: self._commit_material_block(block))

    def material_span_choice(self, span: PageSpan) -> str:
        return f"{span.number:02d}. {span.label} (p.{span.page_range_label})"

    def handle_builder_tab_changed(self, _event: tk.Event) -> None:
        try:
            self.keyboard_state.builder_tab = self.builder_notebook.index("current")
            if self.keyboard_state.builder_tab == 0:
                self.keyboard_state.test_tab = self.test_tabs.index("current")
            elif self.keyboard_state.builder_tab == 1:
                self.keyboard_state.material_tab = self.material_tabs.index("current")
                if self.keyboard_state.material_tab == MATERIAL_TAB_LESSON:
                    self.keyboard_state.lesson_material_tab = self.lesson_material_tabs.index("current")
        except tk.TclError:
            pass
        self.keyboard_state.enter_builder(tab=self.keyboard_state.builder_tab)
        self.refresh_name_separator_editor_state()
        self.refresh_shortcut_help()

    def handle_test_tab_changed(self, _event: tk.Event) -> None:
        try:
            if self.builder_notebook.index("current") != 0:
                self.refresh_shortcut_help()
                return
            self.keyboard_state.test_tab = self.test_tabs.index("current")
        except tk.TclError:
            return
        self.keyboard_state.enter_builder(tab=0)
        self.refresh_shortcut_help()

    def handle_material_tab_changed(self, _event: tk.Event) -> None:
        try:
            if self.builder_notebook.index("current") != 1:
                self.refresh_shortcut_help()
                return
        except tk.TclError:
            return
        try:
            self.keyboard_state.material_tab = self.material_tabs.index("current")
            if self.keyboard_state.material_tab == MATERIAL_TAB_LESSON:
                self.keyboard_state.lesson_material_tab = self.lesson_material_tabs.index("current")
        except tk.TclError:
            pass
        if self.keyboard_state.material_tab in {
            MATERIAL_TAB_APEX,
            MATERIAL_TAB_APEX_BOOST,
        }:
            self.sync_active_apex_controls()
            self.refresh_apex_material_options()
        elif self.keyboard_state.material_tab == MATERIAL_TAB_SELECTED_EXAM:
            self.refresh_selected_exam_status()
        elif self.keyboard_state.material_tab == MATERIAL_TAB_GYOPYEONG:
            self.refresh_gyopyeong_status()
        elif self.keyboard_state.material_tab == MATERIAL_TAB_TEXTBOOK:
            if self.textbook_subtabs.index("current") == 1:
                self.refresh_textbook_variant_status()
            else:
                self.refresh_textbook_status()
        self.keyboard_state.enter_builder(tab=1)
        self.refresh_shortcut_help()

    def handle_lesson_material_tab_changed(self, _event: tk.Event) -> None:
        try:
            if self.builder_notebook.index("current") != 1:
                return
            if self.material_tabs.index("current") != MATERIAL_TAB_LESSON:
                return
            self.keyboard_state.lesson_material_tab = self.lesson_material_tabs.index("current")
        except tk.TclError:
            return
        self.keyboard_state.enter_builder(tab=1)
        self.refresh_shortcut_help()

    def refresh_shortcut_help(self) -> None:
        """Keep the fixed help strip generated from the active keyboard state."""
        context, message = shortcut_help(self.keyboard_state)
        textbook_subtabs = getattr(self, "textbook_subtabs", None)
        if (
            self.keyboard_state.builder_tab == 1
            and self.keyboard_state.material_tab == MATERIAL_TAB_TEXTBOOK
            and textbook_subtabs is not None
            and textbook_subtabs.index("current") == 1
        ):
            context = "교재 선택 · 핵심문제 변형"
            message = message.replace("교과서 블록 추가", "변형 문제 블록 추가")
        self.shortcut_help_context_var.set(context)
        self.shortcut_help_var.set(message)

    def handle_editor_variable_change(self, *_args: object) -> None:
        if self.editor_syncing or getattr(self, "editor_block", None) is None:
            return
        self.keyboard_state.editor_dirty = self.queue_editor_values() != self.editor_baseline
        self.refresh_editor_status()

    def queue_editor_values(self) -> tuple[str, ...]:
        # Read raw Tcl values so a temporarily blank/invalid IntVar remains a
        # draft. Typed validation belongs to saving, not the variable trace.
        return tuple(
            str(tk.Variable.get(variable) if isinstance(variable, tk.Variable) else variable.get())
            for name in QUEUE_EDITOR_VARIABLE_NAMES
            if (variable := getattr(self, name, None)) is not None
        )

    def take_queue_editor_ime(self) -> None:
        if getattr(self, "editor_block", None) is None:
            return
        try:
            widget = self.root.focus_get()
        except (AttributeError, tk.TclError, KeyError):
            return
        if widget is not None and self.widget_is_in_queue_editor(widget):
            self.take_name_entry_ime(widget)

    def save_pending_queue_editor(self) -> bool:
        """Commit the bound draft before navigation; never save into its target."""
        if getattr(self, "editor_save_in_progress", False):
            return False
        state = getattr(self, "keyboard_state", None)
        if state is None:
            return True
        bound_block = getattr(self, "editor_block", None)
        self.editor_save_in_progress = True
        try:
            # Capture Hangul preedit before the cursor or keyboard zone moves.
            self.take_queue_editor_ime()
            if not state.editor_dirty:
                return True
            index = self.selected_block_index()
            if bound_block is not None and (index is None or self.blocks[index] is not bound_block):
                self.queue_status_var.set("편집 중인 블록이 변경되었습니다. Esc로 다시 불러와 주세요.")
                return False
            if not self.replace_selected_block_from_editor():
                self.queue_status_var.set("수정 저장 실패 · 입력을 고치거나 Esc로 취소해 주세요.")
                self.focus_queue_editor()
                return False
            state.editor_dirty = False
            self.refresh_editor_status()
            return True
        finally:
            self.editor_save_in_progress = False

    def save_queue_editor_before_selection(self, focus: int | None, indices: set[int]) -> bool:
        self.take_queue_editor_ime()
        bound_block = getattr(self, "editor_block", None)
        target = self.blocks[focus] if focus is not None else None
        current = self.selected_block_index()
        if (
            target is not bound_block
            or (current in self.selected_block_indices and current not in indices)
        ):
            return self.save_pending_queue_editor()
        return True

    def refresh_editor_status(self) -> None:
        label = getattr(self, "queue_edit_status_label", None)
        if label is None:
            self.refresh_shortcut_help()
            return
        index = self.selected_block_index()
        if index is None:
            label.configure(text="선택 없음")
        else:
            suffix = " · 미저장" if self.keyboard_state.editor_dirty else ""
            label.configure(text=f"#{index + 1}{suffix}")
        self.refresh_shortcut_help()

    def safe_focus_path(self) -> str:
        """Read Tk's focus path without resolving internal popdown widgets."""
        try:
            return str(self.root.tk.call("focus"))
        except tk.TclError:
            return ""

    def remember_builder_focus(self, widget: tk.Misc) -> None:
        if not self.widget_is_in_builder(widget):
            return
        try:
            tab_index = self.builder_notebook.index("current")
            test_index = self.test_tabs.index("current") if tab_index == 0 else -1
            material_index = self.material_tabs.index("current") if tab_index == 1 else -1
            lesson_index = (
                self.lesson_material_tabs.index("current")
                if tab_index == 1 and material_index == MATERIAL_TAB_LESSON
                else -1
            )
        except tk.TclError:
            return
        key = f"{tab_index}:{test_index}:{material_index}:{lesson_index}"
        self.keyboard_state.last_builder_focus[key] = str(widget)

    def builder_focus_key(self) -> str:
        try:
            tab_index = self.builder_notebook.index("current")
            test_index = self.test_tabs.index("current") if tab_index == 0 else -1
            material_index = self.material_tabs.index("current") if tab_index == 1 else -1
            lesson_index = (
                self.lesson_material_tabs.index("current")
                if tab_index == 1 and material_index == MATERIAL_TAB_LESSON
                else -1
            )
        except tk.TclError:
            return "0:0:-1:-1"
        return f"{tab_index}:{test_index}:{material_index}:{lesson_index}"

    def restore_builder_focus(self, widgets: list[tk.Misc]) -> tk.Misc | None:
        preferred_path = self.keyboard_state.last_builder_focus.get(self.builder_focus_key())
        if preferred_path:
            try:
                preferred = self.root.nametowidget(preferred_path)
                if preferred in widgets:
                    return preferred
            except (KeyError, tk.TclError):
                pass
        return widgets[0] if widgets else None

    def run_keyboard_once(self, token: str, command: callable) -> str:
        if token in self.keyboard_latches:
            return "break"
        self.keyboard_latches.add(token)
        self.root.after(300, lambda: self.keyboard_latches.discard(token))
        command()
        return "break"

    @staticmethod
    def keyboard_widget_text(widget: tk.Misc | None) -> str | None:
        getter = getattr(widget, "get", None)
        if not callable(getter):
            return None
        try:
            return str(getter())
        except (tk.TclError, TypeError):
            return None

    def run_keyboard_after_ime(
        self,
        token: str,
        command: callable,
        *,
        source_widget: tk.Misc | None = None,
    ) -> str:
        if token in self.keyboard_latches:
            return "break"
        self.keyboard_latches.add(token)
        widget = source_widget
        if widget is None:
            try:
                widget = self.root.focus_get()
            except (AttributeError, tk.TclError):
                widget = None
        initial_text = self.keyboard_widget_text(widget)
        took_composition = bool(widget is not None and self.take_name_entry_ime(widget))
        had_composition = took_composition or complete_windows_ime_composition()
        restore_builder_focus = (
            getattr(getattr(self, "keyboard_state", None), "zone", None)
            is KeyboardZone.BUILDER
        )

        state = {
            "elapsed": 0,
            "last_text": initial_text,
            "changed": False,
            "stable_cycles": 0,
        }

        def finish() -> None:
            try:
                command()
            finally:
                self.root.after(300, lambda: self.keyboard_latches.discard(token))
                if restore_builder_focus and widget is not None:
                    try:
                        self.root.after(0, widget.focus_set)
                    except (AttributeError, tk.TclError):
                        pass

        def wait_for_committed_text() -> None:
            current_text = self.keyboard_widget_text(widget)
            if current_text != state["last_text"]:
                state["last_text"] = current_text
                state["changed"] = True
                state["stable_cycles"] = 0
            else:
                state["stable_cycles"] += 1
            state["elapsed"] += IME_SHORTCUT_DELAY_MS

            text_is_settled = state["stable_cycles"] >= IME_SHORTCUT_SETTLE_CYCLES
            if (
                state["changed"] and text_is_settled
            ) or state["elapsed"] >= IME_SHORTCUT_TIMEOUT_MS:
                finish()
                return
            self.root.after(IME_SHORTCUT_DELAY_MS, wait_for_committed_text)

        # A successful synchronous take already inserted the final Hangul
        # syllable. With no native composition there is nothing to wait for.
        # Only the asynchronous ImmNotifyIME path needs a short settling poll.
        if took_composition or not had_composition:
            self.root.after(0, finish)
        else:
            self.root.after(IME_SHORTCUT_DELAY_MS, wait_for_committed_text)
        self.root.after(
            IME_SHORTCUT_TIMEOUT_MS + 500,
            lambda: self.keyboard_latches.discard(token),
        )
        return "break"

    def set_active_workspace(self, workspace: str) -> None:
        if workspace not in {"builder", "queue"}:
            return
        self.active_workspace = workspace
        builder = getattr(self, "builder_frame", None)
        queue = getattr(self, "queue_frame", None)
        if builder is not None:
            builder.configure(
                style="FocusBuilderWorkspace.TFrame" if workspace == "builder" else "BuilderWorkspace.TFrame"
            )
        if queue is not None:
            queue.configure(
                style="FocusQueueWorkspace.TFrame" if workspace == "queue" else "QueueWorkspace.TFrame"
            )
        self.focus_context_var.set(
            "현재 작업 영역  교재 선택" if workspace == "builder" else "현재 작업 영역  작업 큐"
        )
        if workspace == "builder":
            try:
                self.keyboard_state.enter_builder(tab=self.builder_notebook.index("current"))
                if self.keyboard_state.builder_tab == 0:
                    self.keyboard_state.test_tab = self.test_tabs.index("current")
                elif self.keyboard_state.builder_tab == 1:
                    self.keyboard_state.material_tab = self.material_tabs.index("current")
            except tk.TclError:
                self.keyboard_state.enter_builder()
        elif self.keyboard_state.zone is not KeyboardZone.QUEUE_EDITOR:
            self.keyboard_state.enter_queue(cursor=self.selected_block_index())
        self.refresh_shortcut_help()
        badge = getattr(self, "focus_context_badge", None)
        if badge is not None:
            colors = self.colors
            badge.configure(
                bg=colors["accent_soft"],
                fg=colors["accent"],
                highlightbackground=colors["accent"],
            )

    def handle_global_focus_in(self, event: tk.Event) -> None:
        widget = getattr(event, "widget", None)
        if widget is None or isinstance(widget, str):
            return
        if self.widget_accepts_typed_input(widget):
            self.root.after(
                0,
                lambda target=widget: self.activate_default_korean_input(target),
            )
        if widget is getattr(self, "custom_drop_list", None):
            if not self.save_pending_queue_editor():
                return
            self.set_active_workspace("builder")
            self.keyboard_state.enter_pdf_list()
            self.refresh_shortcut_help()
            return
        if self.widget_is_in_queue_editor(widget):
            self.keyboard_state.enter_editor(cursor=self.selected_block_index())
            self.set_active_workspace("queue")
        elif self.widget_is_in_queue(widget):
            self.keyboard_state.enter_queue(cursor=self.selected_block_index())
            self.set_active_workspace("queue")
        elif self.widget_is_in_builder(widget):
            if not self.save_pending_queue_editor():
                return
            self.keyboard_state.enter_builder(tab=self.builder_notebook.index("current"))
            if self.keyboard_state.builder_tab == 0:
                self.keyboard_state.test_tab = self.test_tabs.index("current")
            elif self.keyboard_state.builder_tab == 1:
                self.keyboard_state.material_tab = self.material_tabs.index("current")
            self.remember_builder_focus(widget)
            self.set_active_workspace("builder")

    @staticmethod
    def widget_accepts_typed_input(widget: tk.Misc) -> bool:
        if isinstance(widget, ttk.Combobox):
            return False
        if not isinstance(widget, (tk.Entry, tk.Spinbox, ttk.Entry, ttk.Spinbox)):
            return False
        try:
            return str(widget.cget("state")) not in {"disabled", "readonly"}
        except (AttributeError, tk.TclError):
            return False

    def activate_default_korean_input(self, widget: tk.Misc | None = None) -> bool:
        target = widget or self.root
        try:
            if widget is not None and not bool(widget.winfo_exists()):
                return False
        except (AttributeError, tk.TclError):
            return False
        return activate_windows_korean_input(
            self.name_entry_window_handle(target)
        )

    def handle_app_activate(self, _event: tk.Event | None = None) -> None:
        self.root.after(0, self.activate_default_korean_input)

    def handle_global_pointer(self, event: tk.Event) -> None:
        widget = getattr(event, "widget", None)
        if widget is None or isinstance(widget, str):
            return
        self.clear_taskbar_completion_notification()
        if widget is getattr(self, "custom_drop_list", None):
            if not self.save_pending_queue_editor():
                return
            self.set_active_workspace("builder")
            self.keyboard_state.enter_pdf_list()
            self.refresh_shortcut_help()
            return
        if self.widget_is_in_queue_editor(widget):
            self.keyboard_state.enter_editor(cursor=self.selected_block_index())
            self.set_active_workspace("queue")
        elif self.widget_is_in_queue(widget):
            self.keyboard_state.enter_queue(cursor=self.selected_block_index())
            self.set_active_workspace("queue")
        elif self.widget_is_in_builder(widget):
            if not self.save_pending_queue_editor():
                return
            self.keyboard_state.enter_builder(tab=self.builder_notebook.index("current"))
            if self.keyboard_state.builder_tab == 0:
                self.keyboard_state.test_tab = self.test_tabs.index("current")
            elif self.keyboard_state.builder_tab == 1:
                self.keyboard_state.material_tab = self.material_tabs.index("current")
            self.remember_builder_focus(widget)
            self.set_active_workspace("builder")

    def test_focus_widgets(self) -> list[tk.Misc]:
        test_index = self.test_tabs.index("current")
        if test_index == 0:
            widgets: list[tk.Misc | None] = [
                self.subject_combo,
                self.level_combo,
                self.round_combo,
                self.start_combo,
                self.end_combo,
                getattr(self, "unit_selected_numbers_entry", None),
                getattr(self, "unit_new_block_name_entry", None),
                getattr(self, "block_copies_spinbox", None),
                getattr(self, "bundle_button", None),
                getattr(self, "add_unit_button", None),
            ]
        else:
            kind = self.test_kind_for_tab(test_index)
            controls = self.test_builder_controls.get(kind or "", {})
            widgets = [
                controls.get("subject"),
                controls.get("level"),
                controls.get("start"),
                controls.get("end"),
                controls.get("selected"),
                controls.get("name"),
                controls.get("copies"),
                controls.get("bundle"),
                controls.get("add"),
            ]
        return self.focusable_widget_list(widgets)

    def all_test_focus_widgets(self) -> list[tk.Misc]:
        widgets: list[tk.Misc | None] = [
            self.subject_combo,
            self.level_combo,
            self.round_combo,
            self.start_combo,
            self.end_combo,
            getattr(self, "unit_selected_numbers_entry", None),
            getattr(self, "unit_new_block_name_entry", None),
            getattr(self, "block_copies_spinbox", None),
            getattr(self, "bundle_button", None),
            getattr(self, "add_unit_button", None),
        ]
        for kind in TEST_KIND_ORDER:
            controls = self.test_builder_controls.get(kind, {})
            widgets.extend(
                controls.get(key)
                for key in (
                    "subject",
                    "level",
                    "start",
                    "end",
                    "selected",
                    "name",
                    "copies",
                    "bundle",
                    "add",
                )
            )
        return self.bindable_widget_list(widgets)

    def active_lesson_material_prefix(self) -> str:
        tabs = getattr(self, "lesson_material_tabs", None)
        if tabs is not None:
            try:
                return "frequent_type" if tabs.index("current") == 1 else "training_pack"
            except tk.TclError:
                pass
        return "training_pack"

    def lesson_material_focus_widgets(self, prefix: str) -> list[tk.Misc | None]:
        return [
            getattr(self, f"{prefix}_subject_combo", None),
            getattr(self, f"{prefix}_mode_combo", None),
            getattr(self, f"{prefix}_start_combo", None),
            getattr(self, f"{prefix}_end_combo", None),
            getattr(self, f"{prefix}_selected_numbers_entry", None),
            getattr(self, f"{prefix}_new_block_name_entry", None),
            getattr(self, f"{prefix}_copies_spinbox", None),
            getattr(self, f"{prefix}_add_button", None),
        ]

    def material_focus_widgets(self) -> list[tk.Misc]:
        material_index = self.material_tabs.index("current")
        if material_index in {MATERIAL_TAB_APEX, MATERIAL_TAB_APEX_BOOST}:
            widgets: list[tk.Misc | None] = [
                self.apex_subject_combo,
                self.apex_document_combo,
                self.apex_part_combo,
                self.apex_start_combo,
                self.apex_end_combo,
                getattr(self, "apex_selected_numbers_entry", None),
                getattr(self, "apex_new_block_name_entry", None),
                getattr(self, "apex_copies_spinbox", None),
                getattr(self, "apex_add_button", None),
            ]
        elif material_index == MATERIAL_TAB_ABLE:
            widgets = [
                self.able_subject_combo,
                self.able_document_kind_combo,
                self.able_start_combo,
                self.able_end_combo,
                getattr(self, "able_selected_numbers_entry", None),
                getattr(self, "able_new_block_name_entry", None),
                getattr(self, "able_copies_spinbox", None),
                getattr(self, "able_add_button", None),
            ]
        elif material_index == MATERIAL_TAB_SELECTED_EXAM:
            widgets = [
                getattr(self, "selected_exam_subject_combo", None),
                getattr(self, "selected_exam_lesson_combo", None),
                getattr(self, "selected_exam_selected_numbers_entry", None),
                getattr(self, "selected_exam_new_block_name_entry", None),
                getattr(self, "selected_exam_copies_spinbox", None),
                getattr(self, "selected_exam_add_button", None),
            ]
        elif material_index == MATERIAL_TAB_LESSON:
            widgets = self.lesson_material_focus_widgets(self.active_lesson_material_prefix())
        elif material_index == MATERIAL_TAB_TEXTBOOK:
            widgets = self.active_textbook_focus_widgets()
        else:
            widgets = [
                getattr(self, "gyopyeong_subject_combo", None),
                getattr(self, "gyopyeong_part_combo", None),
                getattr(self, "gyopyeong_lesson_combo", None),
                getattr(self, "gyopyeong_new_block_name_entry", None),
                getattr(self, "gyopyeong_copies_spinbox", None),
                getattr(self, "gyopyeong_add_button", None),
            ]
        return self.focusable_widget_list(widgets)

    def all_material_focus_widgets(self) -> list[tk.Misc]:
        apex_widgets: list[tk.Misc | None] = []
        controls_by_variant = getattr(self, "apex_material_controls", {})
        for variant in ("APEX", "APEX Boost"):
            controls = controls_by_variant.get(variant)
            if controls is None:
                continue
            apex_widgets.extend(
                controls.get(key)
                for key in (
                    "subject",
                    "document",
                    "part",
                    "start",
                    "end",
                    "selected_numbers",
                    "name",
                    "copies",
                    "add",
                )
            )
        if not apex_widgets:
            apex_widgets = [
                getattr(self, "apex_subject_combo", None),
                getattr(self, "apex_document_combo", None),
                getattr(self, "apex_part_combo", None),
                getattr(self, "apex_start_combo", None),
                getattr(self, "apex_end_combo", None),
                getattr(self, "apex_selected_numbers_entry", None),
                getattr(self, "apex_new_block_name_entry", None),
                getattr(self, "apex_copies_spinbox", None),
                getattr(self, "apex_add_button", None),
            ]
        return self.bindable_widget_list([
            *apex_widgets,
            self.able_subject_combo,
            self.able_document_kind_combo,
            self.able_start_combo,
            self.able_end_combo,
            getattr(self, "able_selected_numbers_entry", None),
            getattr(self, "able_new_block_name_entry", None),
            self.able_copies_spinbox,
            self.able_add_button,
            getattr(self, "selected_exam_subject_combo", None),
            getattr(self, "selected_exam_lesson_combo", None),
            getattr(self, "selected_exam_selected_numbers_entry", None),
            getattr(self, "selected_exam_new_block_name_entry", None),
            getattr(self, "selected_exam_copies_spinbox", None),
            getattr(self, "selected_exam_add_button", None),
            getattr(self, "training_pack_subject_combo", None),
            getattr(self, "training_pack_mode_combo", None),
            getattr(self, "training_pack_start_combo", None),
            getattr(self, "training_pack_end_combo", None),
            getattr(self, "training_pack_selected_numbers_entry", None),
            getattr(self, "training_pack_new_block_name_entry", None),
            getattr(self, "training_pack_copies_spinbox", None),
            getattr(self, "training_pack_add_button", None),
            *self.lesson_material_focus_widgets("frequent_type"),
            getattr(self, "gyopyeong_subject_combo", None),
            getattr(self, "gyopyeong_part_combo", None),
            getattr(self, "gyopyeong_lesson_combo", None),
            getattr(self, "gyopyeong_new_block_name_entry", None),
            getattr(self, "gyopyeong_copies_spinbox", None),
            getattr(self, "gyopyeong_add_button", None),
            getattr(self, "textbook_subject_combo", None),
            getattr(self, "textbook_publisher_combo", None),
            getattr(self, "textbook_document_kind_combo", None),
            getattr(self, "textbook_start_combo", None),
            getattr(self, "textbook_end_combo", None),
            getattr(self, "textbook_selected_numbers_entry", None),
            getattr(self, "textbook_new_block_name_entry", None),
            getattr(self, "textbook_copies_spinbox", None),
            getattr(self, "textbook_add_button", None),
            getattr(self, "textbook_variant_subject_combo", None),
            getattr(self, "textbook_variant_publisher_combo", None),
            getattr(self, "textbook_variant_start_combo", None),
            getattr(self, "textbook_variant_end_combo", None),
            getattr(self, "textbook_variant_selected_numbers_entry", None),
            getattr(self, "textbook_variant_new_block_name_entry", None),
            getattr(self, "textbook_variant_copies_spinbox", None),
            getattr(self, "textbook_variant_add_button", None),
        ])

    def builder_combo_widgets(self) -> list[ttk.Combobox]:
        test_combos: list[ttk.Combobox] = []
        for kind in TEST_KIND_ORDER:
            controls = self.test_builder_controls.get(kind, {})
            test_combos.extend(
                controls[key]
                for key in ("subject", "level", "start", "end")
                if isinstance(controls.get(key), ttk.Combobox)
            )
        apex_combos: list[ttk.Combobox] = []
        controls_by_variant = getattr(self, "apex_material_controls", {})
        for variant in ("APEX", "APEX Boost"):
            controls = controls_by_variant.get(variant)
            if controls is None:
                continue
            apex_combos.extend(
                controls[key]
                for key in ("subject", "document", "part", "start", "end")
                if isinstance(controls.get(key), ttk.Combobox)
            )
        if not apex_combos:
            apex_combos = [
                self.apex_subject_combo,
                self.apex_document_combo,
                self.apex_part_combo,
                self.apex_start_combo,
                self.apex_end_combo,
            ]
        return [
            self.subject_combo,
            self.level_combo,
            self.round_combo,
            self.start_combo,
            self.end_combo,
            *test_combos,
            *apex_combos,
            self.able_subject_combo,
            self.able_document_kind_combo,
            self.able_start_combo,
            self.able_end_combo,
            self.selected_exam_subject_combo,
            self.selected_exam_lesson_combo,
            self.training_pack_subject_combo,
            self.training_pack_mode_combo,
            self.training_pack_start_combo,
            self.training_pack_end_combo,
            self.frequent_type_subject_combo,
            self.frequent_type_mode_combo,
            self.frequent_type_start_combo,
            self.frequent_type_end_combo,
            self.gyopyeong_subject_combo,
            self.gyopyeong_part_combo,
            self.gyopyeong_lesson_combo,
            self.textbook_subject_combo,
            self.textbook_publisher_combo,
            self.textbook_document_kind_combo,
            self.textbook_start_combo,
            self.textbook_end_combo,
            self.textbook_variant_subject_combo,
            self.textbook_variant_publisher_combo,
            self.textbook_variant_start_combo,
            self.textbook_variant_end_combo,
            self.custom_printer_route_combo,
        ]

    def editor_combo_widgets(self) -> list[ttk.Combobox]:
        return [
            self.edit_printer_route_combo,
            self.edit_subject_combo,
            self.edit_level_combo,
            self.edit_round_combo,
            self.edit_start_combo,
            self.edit_end_combo,
        ]

    def keyboard_combo_widgets(self) -> list[ttk.Combobox]:
        return [*self.builder_combo_widgets(), *self.editor_combo_widgets()]

    def is_managed_combo(self, widget: tk.Misc) -> bool:
        return widget in self.keyboard_combo_widgets()

    def is_material_combo(self, widget: tk.Misc) -> bool:
        """Compatibility alias for older callback paths."""
        return self.is_managed_combo(widget)

    def focus_material_control_delta(self, widget: tk.Misc, delta: int) -> str:
        return self.focus_control_direction(widget, 0, delta)

    def keyboard_control_rows(self) -> list[list[tk.Misc]]:
        """Return focusable controls grouped by their visual keyboard rows.

        The builder deliberately has a few horizontal pairs (level/round and
        start/end).  A flat list makes Up/Down work, but makes Left/Right either
        do nothing or move through an unrelated field.  Keeping the geometry
        here gives every keyboard command one consistent map of the visible UI.
        """
        if self.keyboard_state.zone is KeyboardZone.QUEUE_LIST:
            raw_rows: list[list[tk.Misc | None]] = [[getattr(self, "queue_canvas", None)]]
        elif self.keyboard_state.zone is KeyboardZone.QUEUE_EDITOR:
            raw_rows = [
                [
                    getattr(self, "edit_printer_route_combo", None),
                    getattr(self, "edit_copies_spinbox", None),
                    getattr(self, "edit_bundle_button", None),
                    getattr(self, "edit_save_button", None),
                ],
                [getattr(self, "edit_name_separator_entry", None)],
                [
                    getattr(self, "edit_subject_combo", None),
                    getattr(self, "edit_level_combo", None),
                    getattr(self, "edit_round_combo", None),
                ],
                [getattr(self, "edit_start_combo", None), getattr(self, "edit_end_combo", None)],
                [getattr(self, "edit_selected_numbers_entry", None)],
            ]
        else:
            tab_index = self.builder_notebook.index("current")
            if tab_index == 0:
                test_index = self.test_tabs.index("current")
                if test_index == 0:
                    raw_rows = [
                        [self.subject_combo],
                        [self.level_combo, self.round_combo],
                        [self.start_combo],
                        [self.end_combo],
                        [getattr(self, "unit_selected_numbers_entry", None)],
                        [getattr(self, "unit_new_block_name_entry", None)],
                        [
                            getattr(self, "block_copies_spinbox", None),
                            getattr(self, "bundle_button", None),
                            getattr(self, "add_unit_button", None),
                        ],
                    ]
                else:
                    kind = self.test_kind_for_tab(test_index)
                    controls = self.test_builder_controls.get(kind or "", {})
                    raw_rows = [
                        [controls.get("subject")],
                        [controls.get("level")],
                        [controls.get("start")],
                        [controls.get("end")],
                        [controls.get("selected")],
                        [controls.get("name")],
                        [controls.get("copies"), controls.get("bundle"), controls.get("add")],
                    ]
            elif tab_index == 1:
                material_index = self.material_tabs.index("current")
                if material_index in {MATERIAL_TAB_APEX, MATERIAL_TAB_APEX_BOOST}:
                    raw_rows = [
                        [self.apex_subject_combo],
                        [self.apex_document_combo],
                        [self.apex_part_combo],
                        [self.apex_start_combo],
                        [self.apex_end_combo],
                        [getattr(self, "apex_selected_numbers_entry", None)],
                        [getattr(self, "apex_new_block_name_entry", None)],
                        [getattr(self, "apex_copies_spinbox", None), getattr(self, "apex_add_button", None)],
                    ]
                elif material_index == MATERIAL_TAB_ABLE:
                    raw_rows = [
                        [self.able_subject_combo],
                        [self.able_document_kind_combo],
                        [self.able_start_combo],
                        [self.able_end_combo],
                        [getattr(self, "able_selected_numbers_entry", None)],
                        [getattr(self, "able_new_block_name_entry", None)],
                        [getattr(self, "able_copies_spinbox", None), getattr(self, "able_add_button", None)],
                    ]
                elif material_index == MATERIAL_TAB_SELECTED_EXAM:
                    raw_rows = [
                        [getattr(self, "selected_exam_subject_combo", None)],
                        [getattr(self, "selected_exam_lesson_combo", None)],
                        [getattr(self, "selected_exam_selected_numbers_entry", None)],
                        [getattr(self, "selected_exam_new_block_name_entry", None)],
                        [
                            getattr(self, "selected_exam_copies_spinbox", None),
                            getattr(self, "selected_exam_add_button", None),
                        ],
                    ]
                elif material_index == MATERIAL_TAB_LESSON:
                    prefix = self.active_lesson_material_prefix()
                    raw_rows = [
                        [getattr(self, f"{prefix}_subject_combo", None)],
                        [getattr(self, f"{prefix}_mode_combo", None)],
                        [getattr(self, f"{prefix}_start_combo", None)],
                        [getattr(self, f"{prefix}_end_combo", None)],
                        [getattr(self, f"{prefix}_selected_numbers_entry", None)],
                        [getattr(self, f"{prefix}_new_block_name_entry", None)],
                        [
                            getattr(self, f"{prefix}_copies_spinbox", None),
                            getattr(self, f"{prefix}_add_button", None),
                        ],
                    ]
                elif material_index == MATERIAL_TAB_TEXTBOOK:
                    textbook_widgets = self.active_textbook_focus_widgets()
                    raw_rows = [[widget] for widget in textbook_widgets[:-2]] + [textbook_widgets[-2:]]
                else:
                    raw_rows = [
                        [getattr(self, "gyopyeong_subject_combo", None)],
                        [getattr(self, "gyopyeong_part_combo", None)],
                        [getattr(self, "gyopyeong_lesson_combo", None)],
                        [getattr(self, "gyopyeong_new_block_name_entry", None)],
                        [
                            getattr(self, "gyopyeong_copies_spinbox", None),
                            getattr(self, "gyopyeong_add_button", None),
                        ],
                    ]
            elif tab_index == 2:
                raw_rows = [
                    [
                        getattr(self, "blank_review_copies_spinbox", None),
                        getattr(self, "blank_review_add_button", None),
                    ],
                    [
                        getattr(self, "omr_copies_spinbox", None),
                        getattr(self, "omr_add_button", None),
                    ],
                ]
            elif tab_index == 3:
                raw_rows = [
                    [getattr(self, "custom_drop_list", None)],
                    [getattr(self, "custom_copies_spinbox", None), getattr(self, "custom_bundle_button", None)],
                    [self.custom_printer_route_combo],
                    [getattr(self, "custom_new_block_name_entry", None)],
                    [getattr(self, "custom_pdf_file_button", None), getattr(self, "custom_pdf_clear_button", None)],
                    [getattr(self, "custom_pdf_add_button", None)],
                ]
            elif tab_index == 4:
                raw_rows = [
                    [getattr(self, "order_csv_list", None)],
                    [
                        getattr(self, "order_csv_choose_button", None),
                        getattr(self, "order_csv_template_button", None),
                        getattr(self, "order_csv_clear_button", None),
                    ],
                    [getattr(self, "order_csv_add_button", None)],
                ]
            else:
                raw_rows = []
        return [row for raw_row in raw_rows if (row := self.focusable_widget_list(raw_row))]

    def active_keyboard_controls(self) -> list[tk.Misc]:
        return [widget for row in self.keyboard_control_rows() for widget in row]

    def ensure_test_widget_visible(self, widget: tk.Misc) -> None:
        try:
            builder_tab = self.builder_notebook.index("current")
            if builder_tab == 0:
                tab_text = str(self.test_tabs.tab("current", "text"))
                canvas = self.test_page_canvases.get(tab_text)
            elif builder_tab == 2:
                canvas = getattr(self, "blank_review_canvas", None)
            else:
                return
            if canvas is None:
                return
            canvas.update_idletasks()
            scroll_region = canvas.bbox("all")
            if scroll_region is None or scroll_region[3] <= 0:
                return
            visible_top = float(canvas.canvasy(0))
            visible_bottom = visible_top + canvas.winfo_height()
            widget_top = visible_top + widget.winfo_rooty() - canvas.winfo_rooty()
            widget_bottom = widget_top + widget.winfo_height()
            content_height = float(scroll_region[3])
            if widget_top < visible_top:
                canvas.yview_moveto(max(0.0, widget_top / content_height))
            elif widget_bottom > visible_bottom:
                target_top = max(0.0, widget_bottom - canvas.winfo_height())
                canvas.yview_moveto(min(1.0, target_top / content_height))
        except (AttributeError, tk.TclError):
            return

    def handle_test_page_mousewheel(self, event: tk.Event) -> str | None:
        try:
            if self.builder_notebook.index("current") != 0:
                return None
            tab_text = str(self.test_tabs.tab("current", "text"))
            canvas = self.test_page_canvases.get(tab_text)
            if canvas is None:
                return None
            canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
            return "break"
        except (AttributeError, tk.TclError):
            return None

    def focus_control_delta(self, widget: tk.Misc, delta: int) -> str:
        return self.focus_control_direction(widget, 0, delta)

    def focus_control_direction(self, widget: tk.Misc, dx: int, dy: int) -> str:
        rows = self.keyboard_control_rows()
        position = next(
            ((row_index, column_index)
             for row_index, row in enumerate(rows)
             for column_index, candidate in enumerate(row)
             if candidate is widget),
            None,
        )
        if position is None:
            return "break"
        row_index, column_index = position
        if dx:
            target_row = row_index
            target_column = max(0, min(len(rows[row_index]) - 1, column_index + dx))
        else:
            target_row = max(0, min(len(rows) - 1, row_index + dy))
            target_column = min(column_index, len(rows[target_row]) - 1)
        target_widget = rows[target_row][target_column]
        self.clear_combobox_selection(widget)
        target_widget.focus_set()
        self.ensure_test_widget_visible(target_widget)
        self.clear_combobox_selection(target_widget)
        self.keyboard_state.mark_keyboard_activity()
        if self.keyboard_state.zone is KeyboardZone.QUEUE_EDITOR:
            self.set_active_workspace("queue")
        elif self.keyboard_state.zone is KeyboardZone.QUEUE_LIST:
            self.set_active_workspace("queue")
        else:
            self.set_active_workspace("builder")
            if target_widget is getattr(self, "custom_drop_list", None):
                self.keyboard_state.enter_pdf_list()
                self.refresh_shortcut_help()
        return "break"

    def handle_control_arrow(self, event: tk.Event) -> str | None:
        if self.keyboard_state.interaction is KeyboardInteraction.COMBO_OPEN:
            return None
        if self.widget_is_queue_canvas(event.widget):
            return None
        if isinstance(event.widget, tk.Listbox):
            return None
        if isinstance(event.widget, (tk.Spinbox, ttk.Spinbox)) and event.keysym in {"Up", "Down", "KP_Up", "KP_Down"}:
            # Numeric fields retain Tk's usual increment/decrement behavior.
            return None
        if event.state & 0x0005:
            # Selection extension and word/caret movement belong to the control.
            return None
        if (
            self.keyboard_state.interaction is KeyboardInteraction.PDF_LIST
            and event.widget is self.custom_drop_list
        ):
            return None
        if event.keysym in {"Left", "KP_Left"}:
            if isinstance(event.widget, (tk.Entry, ttk.Entry)) and not isinstance(
                event.widget, ttk.Combobox
            ):
                return None
            return self.focus_control_direction(event.widget, -1, 0)
        if event.keysym in {"Right", "KP_Right"}:
            if isinstance(event.widget, (tk.Entry, ttk.Entry)) and not isinstance(
                event.widget, ttk.Combobox
            ):
                return None
            return self.focus_control_direction(event.widget, 1, 0)
        if event.keysym in {"Up", "KP_Up"}:
            return self.focus_control_direction(event.widget, 0, -1)
        if event.keysym in {"Down", "KP_Down"}:
            return self.focus_control_direction(event.widget, 0, 1)
        return None

    def handle_material_combo_arrow(self, event: tk.Event) -> str | None:
        # Once posted, the native combobox list owns Up/Down and Enter.
        return self.handle_control_arrow(event)

    def material_dropdown_is_open(self, widget: ttk.Combobox | None = None) -> bool:
        combo = widget or self.material_dropdown_widget
        if combo is None:
            return False
        try:
            popdown = combo.tk.call("ttk::combobox::PopdownWindow", combo._w)
            return bool(int(combo.tk.call("winfo", "ismapped", popdown)))
        except (tk.TclError, ValueError):
            return False

    def open_material_dropdown(self, widget: ttk.Combobox) -> str:
        if self.widget_is_in_queue_editor(widget):
            self.keyboard_state.enter_editor(cursor=self.selected_block_index())
            self.set_active_workspace("queue")
        else:
            self.set_active_workspace("builder")
        self.material_dropdown_widget = widget
        self.keyboard_state.open_combo(str(widget))
        self.refresh_shortcut_help()
        try:
            widget.tk.call("ttk::combobox::Post", widget._w)
        except tk.TclError:
            widget.event_generate("<Alt-Down>")
        return "break"

    def handle_material_combo_return(self, event: tk.Event) -> str | None:
        widget = event.widget
        if (
            self.keyboard_state.interaction is KeyboardInteraction.COMBO_OPEN
            and widget is self.material_dropdown_widget
        ):
            return None
        if isinstance(widget, ttk.Combobox):
            return self.open_material_dropdown(widget)
        return "break"

    def handle_combo_toggle(self, event: tk.Event) -> str:
        if self.material_dropdown_is_open(event.widget):
            return self.close_keyboard_popup()
        return self.open_material_dropdown(event.widget)

    @staticmethod
    def clear_combobox_selection(widget: tk.Misc | None) -> None:
        """Remove Tk's retained entry selection after a readonly choice."""
        if not isinstance(widget, ttk.Combobox):
            return
        try:
            widget.selection_clear()
            widget.icursor("end")
        except tk.TclError:
            pass

    def handle_material_combo_selected(self, event: tk.Event) -> None:
        in_editor = self.widget_is_in_queue_editor(event.widget)
        self.clear_combobox_selection(event.widget)
        if event.widget is self.material_dropdown_widget:
            self.material_dropdown_widget = None
        self.keyboard_state.close_popup()
        if in_editor:
            self.keyboard_state.enter_editor(cursor=self.selected_block_index())
            self.set_active_workspace("queue")
        else:
            self.set_active_workspace("builder")

    def close_keyboard_popup(self) -> str:
        widget = self.material_dropdown_widget
        if widget is not None:
            try:
                widget.tk.call("ttk::combobox::Unpost", widget._w)
            except tk.TclError:
                pass
            widget.focus_set()
            self.clear_combobox_selection(widget)
        self.material_dropdown_widget = None
        self.keyboard_state.close_popup()
        self.refresh_shortcut_help()
        return "break"

    def focus_builder_area(self) -> str:
        if not self.save_pending_queue_editor():
            return "break"
        widgets = self.builder_focus_widgets()
        self.set_active_workspace("builder")
        if widgets:
            self.builder_notebook.focus_set()
            target = self.restore_builder_focus(widgets)
            if target is not None:
                target.focus_set()
                self.remember_builder_focus(target)
        return "break"

    def focus_initial_keyboard_control(self) -> None:
        """Give a fresh window a deterministic keyboard starting point."""
        try:
            self.root.focus_force()
        except tk.TclError:
            pass
        self.focus_builder_area()

    def focus_queue_canvas(self) -> str:
        canvas = getattr(self, "queue_canvas", None)
        if canvas is None:
            return "break"
        canvas.focus_set()
        self.keyboard_state.enter_queue(cursor=self.selected_block_index())
        self.set_active_workspace("queue")
        index = self.selected_block_index()
        if index is not None:
            canvas.update_idletasks()
            top = self.queue_card_top(index)
            bottom = top + self.queue_card_height
            view_top = canvas.canvasy(0)
            view_bottom = canvas.canvasy(max(1, canvas.winfo_height()))
            # Canvas items cover only visible cards. The model extent used by
            # draw_queue, rather than bbox('all'), defines scroll fractions.
            content_height = max(180, canvas.winfo_height(), queue_content_height(
                len(self.blocks), card_height=self.queue_card_height,
                gap=self.queue_card_gap, padding=self.queue_padding,
            ))
            if top < view_top:
                canvas.yview_moveto(max(0, top / content_height))
            elif bottom > view_bottom:
                target = max(0, (bottom - canvas.winfo_height()) / content_height)
                canvas.yview_moveto(min(1, target))
        return "break"

    def focus_queue_editor(self) -> str:
        if self.selected_block_index() is None:
            return "break"
        self.populate_queue_editor(self.selected_block_index())
        self.keyboard_state.enter_editor(cursor=self.selected_block_index())
        widgets = self.editor_focus_widgets()
        if widgets:
            widgets[0].focus_set()
        self.set_active_workspace("queue")
        self.refresh_editor_status()
        return "break"

    def handle_workspace_tab(self, event: tk.Event) -> str:
        if event.state & 0x0004:
            return self.handle_next_builder_tab(event, backwards=bool(event.state & 0x0001))
        if self.material_dropdown_widget is not None:
            if self.material_dropdown_is_open():
                self.close_keyboard_popup()
            else:
                self.material_dropdown_widget = None
                self.keyboard_state.close_popup()
        backwards = bool(event.state & 0x0001) or event.keysym == "ISO_Left_Tab"
        if self.widget_is_in_queue_editor(event.widget):
            self.focus_next_in_order(self.editor_focus_widgets(), event.widget, backwards=backwards)
            return "break"
        if self.widget_is_queue_canvas(event.widget):
            return self.focus_builder_area() if backwards else self.focus_queue_editor()
        if self.widget_is_in_queue(event.widget):
            return self.focus_builder_area()
        if self.widget_is_in_builder(event.widget):
            self.focus_next_in_order(self.builder_focus_widgets(), event.widget, backwards=backwards)
            return "break"
        if self.material_dropdown_widget is not None:
            self.material_dropdown_widget = None
        return "break"

    def handle_workspace_cycle(self, event: tk.Event) -> str:
        if not self.save_pending_queue_editor():
            return "break"
        if self.material_dropdown_widget is not None:
            self.close_keyboard_popup()
        backwards = bool(event.state & 0x0001)
        if self.widget_is_in_queue_editor(event.widget):
            return self.focus_queue_canvas() if backwards else self.focus_builder_area()
        if self.widget_is_in_queue(event.widget):
            return self.focus_builder_area() if backwards else self.focus_queue_editor()
        return self.focus_queue_editor() if backwards and self.blocks else self.focus_queue_canvas()

    def bind_workspace_tab(self, widget: tk.Misc) -> None:
        for sequence in ("<Tab>", "<ISO_Left_Tab>"):
            widget.bind(sequence, self.handle_workspace_tab, add="+")

    def bind_material_keyboard_controls(self) -> None:
        for widget in self.keyboard_combo_widgets():
            widget.bind("<Return>", self.handle_material_combo_return, add="+")
            widget.bind("<KP_Enter>", self.handle_material_combo_return, add="+")
            widget.bind("<F4>", self.handle_combo_toggle, add="+")
            widget.bind("<Alt-Down>", self.handle_combo_toggle, add="+")
            widget.bind("<Up>", self.handle_material_combo_arrow, add="+")
            widget.bind("<KP_Up>", self.handle_material_combo_arrow, add="+")
            widget.bind("<Down>", self.handle_material_combo_arrow, add="+")
            widget.bind("<KP_Down>", self.handle_material_combo_arrow, add="+")
            widget.bind("<Left>", self.handle_material_combo_arrow, add="+")
            widget.bind("<KP_Left>", self.handle_material_combo_arrow, add="+")
            widget.bind("<Right>", self.handle_material_combo_arrow, add="+")
            widget.bind("<KP_Right>", self.handle_material_combo_arrow, add="+")
            widget.bind("<<ComboboxSelected>>", self.handle_material_combo_selected, add="+")
            widget.bind("<Escape>", self.handle_escape_shortcut, add="+")

        for widget in self.all_material_focus_widgets():
            self.bind_workspace_tab(widget)

    def bind_workspace_navigation_controls(self) -> None:
        builder_widgets: list[tk.Misc] = [
            *self.all_test_focus_widgets(),
            *self.all_material_focus_widgets(),
            self.blank_review_copies_spinbox,
            self.blank_review_add_button,
            self.omr_copies_spinbox,
            self.omr_add_button,
            self.custom_drop_list,
            self.custom_copies_spinbox,
            self.custom_bundle_button,
            self.custom_printer_route_combo,
            self.custom_pdf_file_button,
            self.custom_pdf_clear_button,
            self.custom_pdf_add_button,
            self.custom_new_block_name_entry,
            self.order_csv_list,
            self.order_csv_choose_button,
            self.order_csv_template_button,
            self.order_csv_clear_button,
            self.order_csv_add_button,
        ]
        for widget in builder_widgets:
            self.bind_workspace_tab(widget)
            widget.bind("<MouseWheel>", self.handle_test_page_mousewheel, add="+")
            for sequence in (
                "<Up>",
                "<KP_Up>",
                "<Down>",
                "<KP_Down>",
                "<Left>",
                "<KP_Left>",
                "<Right>",
                "<KP_Right>",
            ):
                widget.bind(sequence, self.handle_control_arrow, add="+")
            for sequence in ("<Control-Return>", "<Control-KP_Enter>"):
                widget.bind(sequence, self.handle_ctrl_enter_shortcut, add="+")
            for sequence in ("<Control-Shift-Return>", "<Control-Shift-KP_Enter>"):
                widget.bind(sequence, self.handle_ctrl_shift_enter_shortcut, add="+")
        for widget in [
            self.queue_canvas,
            self.queue_edit_panel,
            self.edit_printer_route_combo,
            self.edit_subject_combo,
            self.edit_level_combo,
            self.edit_round_combo,
            self.edit_start_combo,
            self.edit_end_combo,
            self.edit_selected_numbers_entry,
            self.edit_name_separator_entry,
            self.edit_copies_spinbox,
            self.edit_bundle_button,
            self.edit_save_button,
        ]:
            self.bind_workspace_tab(widget)
            for sequence in (
                "<Up>",
                "<KP_Up>",
                "<Down>",
                "<KP_Down>",
                "<Left>",
                "<KP_Left>",
                "<Right>",
                "<KP_Right>",
            ):
                widget.bind(sequence, self.handle_control_arrow, add="+")
            for sequence in ("<Control-Shift-Return>", "<Control-Shift-KP_Enter>"):
                widget.bind(sequence, self.handle_ctrl_shift_enter_shortcut, add="+")
        for widget in [
            self.edit_printer_route_combo,
            self.edit_subject_combo,
            self.edit_level_combo,
            self.edit_round_combo,
            self.edit_start_combo,
            self.edit_end_combo,
            self.edit_selected_numbers_entry,
            self.edit_name_separator_entry,
            self.edit_copies_spinbox,
            self.edit_bundle_button,
            self.edit_save_button,
        ]:
            for sequence in ("<Control-Return>", "<Control-KP_Enter>"):
                widget.bind(sequence, self.handle_ctrl_enter_shortcut, add="+")
            if not isinstance(widget, ttk.Combobox):
                for sequence in ("<Return>", "<KP_Enter>"):
                    widget.bind(sequence, self.handle_return_shortcut, add="+")
        for key in ("Up", "Down", "Home", "End", "Prior", "Next", "KP_Up", "KP_Down", "KP_Home", "KP_End", "KP_Prior", "KP_Next"):
            for modifiers in ("", "Shift-", "Control-", "Control-Shift-"):
                self.queue_canvas.bind(f"<{modifiers}{key}>", self.handle_queue_arrow, add="+")
        self.queue_canvas.bind("<F2>", lambda _event: self.focus_queue_editor(), add="+")
        for sequence in ("<Shift-F10>", "<Menu>"):
            self.queue_canvas.bind(sequence, self.handle_queue_context_key, add="+")
        self.queue_canvas.bind("<Return>", self.handle_return_shortcut, add="+")
        self.queue_canvas.bind("<KP_Enter>", self.handle_return_shortcut, add="+")
        self.queue_canvas.bind("<Control-space>", self.handle_ctrl_space_shortcut, add="+")
        self.queue_canvas.bind("<Alt-Up>", lambda _event: self.shortcut_move_selected(-1), add="+")
        self.queue_canvas.bind("<Alt-Down>", lambda _event: self.shortcut_move_selected(1), add="+")
        self.custom_drop_list.bind("<Return>", self.handle_pdf_list_return, add="+")
        self.custom_drop_list.bind("<KP_Enter>", self.handle_pdf_list_return, add="+")
        self.custom_drop_list.bind("<Delete>", self.handle_pdf_list_delete, add="+")
        self.custom_drop_list.bind("<Control-a>", self.handle_pdf_list_select_all, add="+")
        self.custom_drop_list.bind("<Control-A>", self.handle_pdf_list_select_all, add="+")
        for sequence in ("<Control-a>", "<Control-A>"):
            self.order_csv_list.bind(sequence, self.handle_native_list_select_all, add="+")
        for widget in (self.custom_drop_list, self.order_csv_list):
            for sequence in ("<Control-c>", "<Control-C>"):
                widget.bind(sequence, self.handle_native_list_copy, add="+")

    def shortcut_focus_is_editor(self, event: tk.Event) -> bool:
        try:
            widget_class = event.widget.winfo_class()
        except Exception:
            return False
        return widget_class in {"Entry", "TEntry", "TCombobox", "Spinbox", "TSpinbox", "Text"}

    def widget_is_in_builder(self, widget: tk.Misc) -> bool:
        current: tk.Misc | None = widget
        while current is not None:
            if current is self.builder_notebook or current is self.builder_frame:
                return True
            try:
                parent_name = current.winfo_parent()
            except tk.TclError:
                return False
            if not parent_name:
                return False
            try:
                current = self.root.nametowidget(parent_name)
            except (KeyError, tk.TclError):
                return False
        return False

    def widget_has_ancestor(self, widget: tk.Misc, ancestor: tk.Misc | None) -> bool:
        if ancestor is None or isinstance(widget, str):
            return False
        current: tk.Misc | None = widget
        while current is not None:
            if current is ancestor:
                return True
            try:
                parent_name = current.winfo_parent()
            except tk.TclError:
                return False
            if not parent_name:
                return False
            try:
                current = self.root.nametowidget(parent_name)
            except (KeyError, tk.TclError):
                return False
        return False

    def widget_is_in_queue(self, widget: tk.Misc) -> bool:
        return self.widget_has_ancestor(widget, getattr(self, "queue_frame", None))

    def widget_is_in_queue_editor(self, widget: tk.Misc) -> bool:
        return self.widget_has_ancestor(widget, getattr(self, "queue_edit_panel", None))

    def widget_is_queue_canvas(self, widget: tk.Misc) -> bool:
        return widget is getattr(self, "queue_canvas", None)

    def focusable_widget_list(self, widgets: list[tk.Misc | None]) -> list[tk.Misc]:
        result: list[tk.Misc] = []
        for widget in widgets:
            if widget is None:
                continue
            try:
                if widget.winfo_exists() and widget.winfo_viewable() and str(widget.cget("state")) != "disabled":
                    result.append(widget)
            except (tk.TclError, TypeError):
                continue
        return result

    def bindable_widget_list(self, widgets: list[tk.Misc | None]) -> list[tk.Misc]:
        """Return existing controls, including controls on currently hidden tabs."""
        result: list[tk.Misc] = []
        for widget in widgets:
            if widget is None:
                continue
            try:
                if widget.winfo_exists():
                    result.append(widget)
            except (tk.TclError, TypeError):
                continue
        return result

    def builder_focus_widgets(self) -> list[tk.Misc]:
        tab_index = self.builder_notebook.index("current")
        if tab_index == 0:
            return self.test_focus_widgets()
        elif tab_index == 1:
            material_index = self.material_tabs.index("current")
            if material_index in {MATERIAL_TAB_APEX, MATERIAL_TAB_APEX_BOOST}:
                widgets = [
                    self.apex_subject_combo,
                    self.apex_document_combo,
                    self.apex_part_combo,
                    self.apex_start_combo,
                    self.apex_end_combo,
                    getattr(self, "apex_selected_numbers_entry", None),
                    getattr(self, "apex_new_block_name_entry", None),
                    getattr(self, "apex_copies_spinbox", None),
                    getattr(self, "apex_add_button", None),
                ]
            elif material_index == MATERIAL_TAB_ABLE:
                widgets = [
                    self.able_subject_combo,
                    self.able_document_kind_combo,
                    self.able_start_combo,
                    self.able_end_combo,
                    getattr(self, "able_selected_numbers_entry", None),
                    getattr(self, "able_new_block_name_entry", None),
                    getattr(self, "able_copies_spinbox", None),
                    getattr(self, "able_add_button", None),
                ]
            elif material_index == MATERIAL_TAB_SELECTED_EXAM:
                widgets = [
                    getattr(self, "selected_exam_subject_combo", None),
                    getattr(self, "selected_exam_lesson_combo", None),
                    getattr(self, "selected_exam_selected_numbers_entry", None),
                    getattr(self, "selected_exam_new_block_name_entry", None),
                    getattr(self, "selected_exam_copies_spinbox", None),
                    getattr(self, "selected_exam_add_button", None),
                ]
            elif material_index == MATERIAL_TAB_LESSON:
                widgets = self.lesson_material_focus_widgets(self.active_lesson_material_prefix())
            elif material_index == MATERIAL_TAB_TEXTBOOK:
                widgets = self.active_textbook_focus_widgets()
            else:
                widgets = [
                    getattr(self, "gyopyeong_subject_combo", None),
                    getattr(self, "gyopyeong_part_combo", None),
                    getattr(self, "gyopyeong_lesson_combo", None),
                    getattr(self, "gyopyeong_new_block_name_entry", None),
                    getattr(self, "gyopyeong_copies_spinbox", None),
                    getattr(self, "gyopyeong_add_button", None),
                ]
        elif tab_index == 2:
            widgets = [
                getattr(self, "blank_review_copies_spinbox", None),
                getattr(self, "blank_review_add_button", None),
                getattr(self, "omr_copies_spinbox", None),
                getattr(self, "omr_add_button", None),
            ]
        elif tab_index == 3:
            widgets = [
                getattr(self, "custom_drop_list", None),
                getattr(self, "custom_copies_spinbox", None),
                getattr(self, "custom_bundle_button", None),
                self.custom_printer_route_combo,
                getattr(self, "custom_new_block_name_entry", None),
                getattr(self, "custom_pdf_file_button", None),
                getattr(self, "custom_pdf_clear_button", None),
                getattr(self, "custom_pdf_add_button", None),
            ]
        elif tab_index == 4:
            widgets = [
                getattr(self, "order_csv_list", None),
                getattr(self, "order_csv_choose_button", None),
                getattr(self, "order_csv_template_button", None),
                getattr(self, "order_csv_clear_button", None),
                getattr(self, "order_csv_add_button", None),
            ]
        else:
            widgets = []
        return self.focusable_widget_list(widgets)

    def editor_focus_widgets(self) -> list[tk.Misc]:
        return self.focusable_widget_list(
            [
                getattr(self, "edit_name_separator_entry", None),
                self.edit_printer_route_combo,
                self.edit_subject_combo,
                self.edit_level_combo,
                self.edit_round_combo,
                self.edit_start_combo,
                self.edit_end_combo,
                getattr(self, "edit_selected_numbers_entry", None),
                getattr(self, "edit_copies_spinbox", None),
                getattr(self, "edit_bundle_button", None),
                getattr(self, "edit_save_button", None),
            ]
        )

    def select_queue_indices(
        self,
        indices: set[int],
        *,
        focus_index: int | None = None,
        anchor_index: int | None = None,
        load_form: bool = False,
    ) -> bool:
        valid = {index for index in indices if 0 <= index < len(self.blocks)}
        focus = focus_index if focus_index is not None and 0 <= focus_index < len(self.blocks) else (min(valid) if valid else None)
        if not self.save_queue_editor_before_selection(focus, valid):
            return False
        self.selected_block_indices = valid
        self.selected_block_index_value = focus
        self.queue_selection_anchor = anchor_index if anchor_index in range(len(self.blocks)) else focus
        self.keyboard_state.queue_cursor = focus
        self.keyboard_state.mark_keyboard_activity()
        # Loading the builder also populates the editor. Avoid filling it twice
        # for a click while retaining the ordinary keyboard-only refresh path.
        if load_form and focus is not None:
            self.refresh_queue_selection(update_editor=False)
        else:
            self.refresh_queue_selection()
        if load_form:
            self.load_selected_block_to_form()
        return True

    def select_queue_with_modifiers(self, index: int, state: int) -> bool:
        if not 0 <= index < len(self.blocks):
            return False
        selection = queue_click_selection(
            len(self.blocks), self.selected_block_indices, self.selected_block_index(),
            self.queue_selection_anchor, index,
            control=bool(state & 0x0004), shift=bool(state & 0x0001),
        )
        return self.select_queue_indices(
            set(selection.selected_indices), focus_index=selection.focus_index,
            anchor_index=selection.anchor_index, load_form=True,
        )

    def select_all_queue(self) -> str:
        if self.blocks:
            focus = self.selected_block_index() if self.selected_block_index() is not None else 0
            self.select_queue_indices(set(range(len(self.blocks))), focus_index=focus, anchor_index=focus)
        return "break"

    def copy_selected_blocks(self) -> str:
        indices = sorted(self.selected_block_indices)
        indices = [index for index in indices if 0 <= index < len(self.blocks)]
        if not indices:
            self.queue_status_var.set("복사할 블록을 선택하세요.")
            return "break"
        if not self.save_pending_queue_editor():
            return "break"
        self.block_clipboard = tuple(
            replace(self.blocks[index], files=list(self.blocks[index].files))
            for index in indices
        )
        self.queue_status_var.set(f"블록 {len(indices)}개 복사됨 · Ctrl+V로 선택 블록 아래에 붙여넣기")
        self.log(f"블록 {len(indices)}개 복사")
        return "break"

    def duplicate_selected_blocks(self) -> str:
        if not self.selected_block_indices:
            return "break"
        previous_clipboard = getattr(self, "block_clipboard", ())
        try:
            self.copy_selected_blocks()
            return self.paste_copied_blocks()
        finally:
            self.block_clipboard = previous_clipboard

    def paste_copied_blocks(self) -> str:
        copied_blocks = tuple(getattr(self, "block_clipboard", ()))
        if not copied_blocks:
            self.queue_status_var.set("먼저 작업 큐에서 블록을 복사하세요.")
            return "break"
        if not self.save_pending_queue_editor():
            return "break"
        selected_indices = sorted(
            index for index in self.selected_block_indices
            if 0 <= index < len(self.blocks)
        )
        anchor_block = self.blocks[selected_indices[-1]] if selected_indices else None
        retry = getattr(self, "retry_batch", None)
        append_only = self.is_printing or bool(retry is not None and retry.jobs)
        token = self.reserve_block_commit()
        pasted_blocks = [
            replace(
                block,
                files=list(block.files),
                history_id=str(uuid.uuid4()),
                replaces_block_id="",
            )
            for block in copied_blocks
        ]

        def commit() -> None:
            anchor_index = next(
                (index for index, block in enumerate(self.blocks) if block is anchor_block), None,
            )
            if not self.save_pending_queue_editor():
                return
            current_anchor = self.blocks[anchor_index] if anchor_index is not None else anchor_block
            self.push_undo_state("블록 붙여넣기")
            first_index = len(self.blocks)
            current_retry = getattr(self, "retry_batch", None)
            must_append = (
                append_only or self.is_printing
                or bool(current_retry is not None and current_retry.jobs)
            )
            if not must_append and current_anchor is not None:
                first_index = next(
                    (index + 1 for index, block in enumerate(self.blocks) if block is current_anchor),
                    first_index,
                )
            self.blocks[first_index:first_index] = pasted_blocks
            for block in pasted_blocks:
                self.block_status_by_id[id(block)] = "대기"
                self.block_detail_by_id.pop(id(block), None)
            pasted_indices = set(range(first_index, first_index + len(pasted_blocks)))
            self.selected_block_indices = pasted_indices
            self.selected_block_index_value = first_index
            self.queue_selection_anchor = first_index
            self.update_queue_view()
            self.update_preview()
            location = "맨 아래" if first_index == len(self.blocks) - len(pasted_blocks) else f"{first_index}번 블록 아래"
            self.log(f"블록 {len(pasted_blocks)}개 붙여넣기: {location}에 대기 작업으로 추가")
            if must_append and anchor_block is not None and first_index != 0:
                self.queue_status_var.set("인쇄·재시도 순서 보호: 붙여넣은 블록을 작업 큐 맨 아래에 추가했습니다.")

        self.complete_block_commit(token, commit)
        return "break"

    def handle_queue_arrow(self, event: tk.Event) -> str | None:
        if not self.widget_is_in_queue(event.widget) or self.widget_is_in_queue_editor(event.widget):
            return None
        if not self.blocks:
            return "break"
        page_size = max(1, self.queue_canvas.winfo_height() // (self.queue_card_height + self.queue_card_gap))
        target = queue_navigation_target(len(self.blocks), self.selected_block_index(), event.keysym, page_size)
        if target is None:
            return None
        selection = queue_navigation_selection(
            len(self.blocks), self.selected_block_indices, self.selected_block_index(),
            self.queue_selection_anchor, target,
            control=bool(event.state & 0x0004), shift=bool(event.state & 0x0001),
        )
        if not self.select_queue_indices(
            set(selection.selected_indices), focus_index=selection.focus_index,
            anchor_index=selection.anchor_index,
        ):
            return "break"
        self.focus_queue_canvas()
        return "break"

    def focus_next_in_order(self, widgets: list[tk.Misc], current: tk.Misc | None, *, wrap: bool = True, backwards: bool = False) -> bool:
        if not widgets:
            return False
        try:
            index = widgets.index(current) if current is not None else -1
        except ValueError:
            index = -1
        next_index = index - 1 if backwards else index + 1
        if next_index >= len(widgets) or next_index < 0:
            if not wrap:
                return False
            next_index = len(widgets) - 1 if backwards else 0
        widgets[next_index].focus_set()
        return True

    def focus_next_builder_control(self, current: tk.Misc | None = None) -> str:
        self.focus_next_in_order(self.builder_focus_widgets(), current, wrap=True)
        return "break"

    def focus_next_editor_control(self, current: tk.Misc | None = None) -> str:
        widgets = self.editor_focus_widgets()
        self.focus_next_in_order(widgets, current, wrap=True)
        return "break"

    def invoke_block_add_shortcut(self, command: callable) -> str:
        command()
        return "break"

    def bind_block_add_button(self, button: ttk.Button, command: callable) -> None:
        for sequence in ("<Return>", "<KP_Enter>"):
            button.bind(
                sequence,
                lambda _event, action=command: self.invoke_block_add_shortcut(action),
                add="+",
            )

    def handle_add_shortcut(self, event: tk.Event) -> str | None:
        if not self.widget_is_in_builder(event.widget):
            return None
        tab_index = self.builder_notebook.index("current")
        if tab_index == 0:
            test_index = self.test_tabs.index("current")
            kind = self.test_kind_for_tab(test_index)
            if kind is None:
                self.add_current_block()
            else:
                self.add_test_catalog_block(kind)
        elif tab_index == 1:
            material_tabs = getattr(self, "material_tabs", None)
            if material_tabs is None:
                return None
            material_index = material_tabs.index("current")
            if material_index in {MATERIAL_TAB_APEX, MATERIAL_TAB_APEX_BOOST}:
                self.add_apex_material_block()
            elif material_index == MATERIAL_TAB_ABLE:
                self.add_able_material_block()
            elif material_index == MATERIAL_TAB_SELECTED_EXAM:
                self.add_selected_exam_material_block()
            elif material_index == MATERIAL_TAB_LESSON:
                if self.active_lesson_material_prefix() == "frequent_type":
                    self.add_frequent_type_block()
                else:
                    self.add_training_pack_block()
            elif material_index == MATERIAL_TAB_TEXTBOOK:
                if getattr(self, "textbook_subtabs", None) is not None and self.textbook_subtabs.index("current") == 1:
                    self.add_textbook_variant_block()
                else:
                    self.add_textbook_material_block()
            else:
                self.add_gyopyeong_material_block()
        elif tab_index == 2:
            if event.widget in {
                getattr(self, "omr_copies_spinbox", None),
                getattr(self, "omr_add_button", None),
            }:
                self.add_omr_block()
            else:
                self.add_blank_review_block()
        elif tab_index == 3:
            self.add_custom_pdf_block()
        elif tab_index == 4:
            self.add_order_csv_blocks()
        return "break"

    def handle_next_builder_tab(self, _event: tk.Event | None = None, *, backwards: bool = False) -> str:
        if self.material_dropdown_widget is not None:
            self.close_keyboard_popup()
        tabs = self.builder_notebook.tabs()
        if len(tabs) > 1:
            current = self.builder_notebook.index("current")
            step = -1 if backwards else 1
            test_tabs = getattr(self, "test_tabs", None)
            material_tabs = getattr(self, "material_tabs", None)
            if current == 0 and test_tabs is not None:
                test_pages = test_tabs.tabs()
                if len(test_pages) > 1:
                    test_current = test_tabs.index("current")
                    test_next = test_current + step
                    if 0 <= test_next < len(test_pages):
                        test_tabs.select(test_pages[test_next])
                        self.keyboard_state.builder_tab = 0
                        self.keyboard_state.test_tab = test_next
                        self.set_active_workspace("builder")
                        self.root.after(0, self.focus_builder_area)
                        return "break"
            if current == 1 and material_tabs is not None:
                material_pages = material_tabs.tabs()
                if len(material_pages) > 1:
                    material_current = material_tabs.index("current")
                    lesson_tabs = getattr(self, "lesson_material_tabs", None)
                    if material_current == MATERIAL_TAB_LESSON and lesson_tabs is not None:
                        lesson_pages = lesson_tabs.tabs()
                        lesson_current = lesson_tabs.index("current")
                        lesson_next = lesson_current + step
                        if 0 <= lesson_next < len(lesson_pages):
                            lesson_tabs.select(lesson_pages[lesson_next])
                            self.keyboard_state.lesson_material_tab = lesson_next
                            self.set_active_workspace("builder")
                            self.root.after(0, self.focus_builder_area)
                            return "break"
                    textbook_subtabs = getattr(self, "textbook_subtabs", None)
                    if material_current == MATERIAL_TAB_TEXTBOOK and textbook_subtabs is not None:
                        textbook_pages = textbook_subtabs.tabs()
                        textbook_next = textbook_subtabs.index("current") + step
                        if 0 <= textbook_next < len(textbook_pages):
                            textbook_subtabs.select(textbook_pages[textbook_next])
                            self.set_active_workspace("builder")
                            self.root.after(0, self.focus_builder_area)
                            return "break"
                    material_next = material_current + step
                    if 0 <= material_next < len(material_pages):
                        material_tabs.select(material_pages[material_next])
                        self.keyboard_state.builder_tab = 1
                        self.keyboard_state.material_tab = material_next
                        if material_next == MATERIAL_TAB_LESSON and lesson_tabs is not None:
                            lesson_pages = lesson_tabs.tabs()
                            if lesson_pages:
                                lesson_index = 0 if step > 0 else len(lesson_pages) - 1
                                lesson_tabs.select(lesson_pages[lesson_index])
                                self.keyboard_state.lesson_material_tab = lesson_index
                        if material_next == MATERIAL_TAB_TEXTBOOK:
                            textbook_subtabs = getattr(self, "textbook_subtabs", None)
                            if textbook_subtabs is not None:
                                textbook_pages = textbook_subtabs.tabs()
                                if textbook_pages:
                                    textbook_subtabs.select(
                                        textbook_pages[0 if step > 0 else -1]
                                    )
                        self.set_active_workspace("builder")
                        self.root.after(0, self.focus_builder_area)
                        return "break"
            next_index = (current + step) % len(tabs)
            self.builder_notebook.select(tabs[next_index])
            self.keyboard_state.builder_tab = next_index
            if next_index == 0 and test_tabs is not None:
                test_pages = test_tabs.tabs()
                if test_pages:
                    test_index = 0 if step > 0 else len(test_pages) - 1
                    test_tabs.select(test_pages[test_index])
                    self.keyboard_state.test_tab = test_index
            elif next_index == 1 and material_tabs is not None:
                material_pages = material_tabs.tabs()
                if material_pages:
                    material_index = 0 if step > 0 else len(material_pages) - 1
                    material_tabs.select(material_pages[material_index])
                    self.keyboard_state.material_tab = material_index
                    if material_index == MATERIAL_TAB_LESSON:
                        lesson_tabs = getattr(self, "lesson_material_tabs", None)
                        if lesson_tabs is not None:
                            lesson_pages = lesson_tabs.tabs()
                            if lesson_pages:
                                lesson_index = 0 if step > 0 else len(lesson_pages) - 1
                                lesson_tabs.select(lesson_pages[lesson_index])
                                self.keyboard_state.lesson_material_tab = lesson_index
                    elif material_index == MATERIAL_TAB_TEXTBOOK:
                        textbook_subtabs = getattr(self, "textbook_subtabs", None)
                        if textbook_subtabs is not None:
                            textbook_pages = textbook_subtabs.tabs()
                            if textbook_pages:
                                textbook_subtabs.select(
                                    textbook_pages[0 if step > 0 else -1]
                                )
            self.set_active_workspace("builder")
            self.root.after(0, self.focus_builder_area)
        return "break"

    def handle_return_shortcut(self, event: tk.Event) -> str | None:
        if self.keyboard_state.interaction is KeyboardInteraction.COMBO_OPEN:
            return None
        if self.is_managed_combo(event.widget):
            return self.handle_material_combo_return(event)
        if event.widget is self.custom_drop_list:
            return self.handle_pdf_list_return(event)
        if self.widget_is_in_queue_editor(event.widget):
            if isinstance(event.widget, (tk.Button, ttk.Button)):
                event.widget.invoke()
                return "break"
            return self.run_keyboard_after_ime(
                "save-editor", self.save_editor_from_keyboard, source_widget=event.widget,
            )
        if self.widget_is_in_queue(event.widget):
            return self.focus_queue_editor()
        if self.widget_is_in_builder(event.widget):
            if isinstance(event.widget, (tk.Button, ttk.Button)):
                event.widget.invoke()
                return "break"
            return self.focus_next_builder_control(event.widget)
        return None

    def handle_ctrl_enter_shortcut(self, event: tk.Event) -> str | None:
        if self.keyboard_state.interaction is KeyboardInteraction.COMBO_OPEN:
            return "break"
        if self.keyboard_state.zone is KeyboardZone.QUEUE_EDITOR and self.widget_is_in_queue_editor(event.widget):
            return self.run_keyboard_after_ime(
                "save-editor",
                lambda: self.save_editor_from_keyboard(),
                source_widget=event.widget,
            )
        if self.keyboard_state.zone is KeyboardZone.BUILDER and self.widget_is_in_builder(event.widget):
            return self.run_keyboard_after_ime(
                "add-block",
                lambda: self.handle_add_shortcut(event),
                source_widget=event.widget,
            )
        return None

    def save_editor_from_keyboard(self) -> None:
        if self.replace_selected_block_from_editor():
            self.keyboard_state.editor_dirty = False
            self.focus_queue_canvas()
            self.refresh_editor_status()

    def handle_ctrl_shift_enter_shortcut(self, _event: tk.Event) -> str:
        if self.keyboard_state.interaction is not KeyboardInteraction.FIELDS:
            return "break"
        if self.keyboard_state.zone is KeyboardZone.QUEUE_EDITOR and self.keyboard_state.editor_dirty:
            self.queue_status_var.set("먼저 Enter로 수정 저장하거나 Esc로 취소하세요.")
            return "break"
        return self.run_keyboard_once("start-print", self.start_print_thread)

    def handle_ctrl_a_shortcut(self, event: tk.Event) -> str | None:
        if self.shortcut_focus_is_editor(event) or self.widget_is_in_queue_editor(event.widget):
            return None
        if self.keyboard_state.zone is KeyboardZone.QUEUE_LIST and self.widget_is_in_queue(event.widget):
            return self.select_all_queue()
        return None

    def handle_ctrl_c_shortcut(self, event: tk.Event) -> str | None:
        if (
            self.keyboard_state.zone is not KeyboardZone.QUEUE_LIST
            or not self.widget_is_in_queue(event.widget)
            or self.widget_is_in_queue_editor(event.widget)
            or self.shortcut_focus_is_editor(event)
        ):
            return None
        return self.copy_selected_blocks()

    def handle_ctrl_v_shortcut(self, event: tk.Event) -> str | None:
        if (
            self.keyboard_state.zone is not KeyboardZone.QUEUE_LIST
            or not self.widget_is_in_queue(event.widget)
            or self.widget_is_in_queue_editor(event.widget)
            or self.shortcut_focus_is_editor(event)
        ):
            return None
        return self.paste_copied_blocks()

    def handle_ctrl_space_shortcut(self, event: tk.Event) -> str | None:
        if self.keyboard_state.zone is not KeyboardZone.QUEUE_LIST or not self.widget_is_in_queue(event.widget) or self.shortcut_focus_is_editor(event):
            return None
        index = self.selected_block_index()
        if index is None:
            return "break"
        indices = set(self.selected_block_indices)
        if index in indices:
            indices.remove(index)
        else:
            indices.add(index)
        self.select_queue_indices(indices, focus_index=index, anchor_index=index)
        return "break"

    def handle_queue_command_shortcut(self, event: tk.Event, command: Callable) -> str | None:
        if not self.widget_is_in_queue(event.widget) or self.widget_is_in_queue_editor(event.widget) or self.shortcut_focus_is_editor(event):
            return None
        if not self.save_pending_queue_editor():
            return "break"
        command()
        return "break"

    def handle_undo_shortcut(self, event: tk.Event, *, redo: bool = False) -> str | None:
        if self.shortcut_focus_is_editor(event):
            return None
        if self.widget_is_in_queue(event.widget) or self.widget_is_in_builder(event.widget):
            return self.redo_last_action() if redo else self.undo_last_action()
        return None

    def handle_native_list_select_all(self, event: tk.Event) -> str:
        if event.widget.size():
            event.widget.selection_set(0, END)
        return "break"

    def handle_native_list_copy(self, event: tk.Event) -> str:
        rows = [event.widget.get(index) for index in event.widget.curselection()]
        if rows:
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(rows))
        return "break"

    def handle_pdf_list_return(self, _event: tk.Event) -> str:
        self.keyboard_state.enter_pdf_list()
        self.custom_drop_list.focus_set()
        self.refresh_shortcut_help()
        return "break"

    def handle_pdf_list_select_all(self, _event: tk.Event) -> str:
        if self.custom_drop_list.size():
            self.custom_drop_list.selection_set(0, END)
        self.keyboard_state.enter_pdf_list()
        self.refresh_shortcut_help()
        return "break"

    def handle_pdf_list_delete(self, _event: tk.Event) -> str:
        selected = list(self.custom_drop_list.curselection())
        if selected:
            for index in reversed(selected):
                if 0 <= index < len(self.custom_pdf_files):
                    self.custom_pdf_files.pop(index)
            self.refresh_custom_pdf_list()
            self.custom_pdf_status_var.set(f"PDF {len(self.custom_pdf_files)}개 선택됨")
        self.keyboard_state.enter_pdf_list()
        self.refresh_shortcut_help()
        return "break"

    def handle_escape_shortcut(self, event: tk.Event) -> str | None:
        if self.keyboard_state.interaction is KeyboardInteraction.COMBO_OPEN:
            return self.close_keyboard_popup()
        if self.keyboard_state.interaction is KeyboardInteraction.PDF_LIST:
            self.keyboard_state.leave_nested_interaction()
            widgets = self.builder_focus_widgets()
            if self.custom_drop_list in widgets:
                index = widgets.index(self.custom_drop_list)
                if index + 1 < len(widgets):
                    widgets[index + 1].focus_set()
            self.refresh_shortcut_help()
            return "break"
        if self.keyboard_state.zone is KeyboardZone.QUEUE_EDITOR or self.widget_is_in_queue_editor(event.widget):
            if self.keyboard_state.editor_dirty:
                self.populate_queue_editor(self.selected_block_index(), discard_draft=True)
            self.keyboard_state.editor_dirty = False
            self.focus_queue_canvas()
            self.refresh_editor_status()
            return "break"
        if self.keyboard_state.zone is KeyboardZone.QUEUE_LIST or self.widget_is_in_queue(event.widget):
            return self.focus_builder_area()
        if self.widget_is_in_builder(event.widget):
            return "break"
        return None

    def bind_shortcuts(self) -> None:
        self.root.bind("<Delete>", self.handle_delete_shortcut)
        self.root.bind("<Control-z>", self.handle_undo_shortcut)
        self.root.bind("<Control-Z>", lambda event: self.handle_undo_shortcut(event, redo=bool(event.state & 0x0001)))
        for sequence in ("<Control-y>", "<Control-Y>", "<Control-Shift-z>", "<Control-Shift-Z>"):
            self.root.bind(sequence, lambda event: self.handle_undo_shortcut(event, redo=True))
        for sequence in ("<F6>", "<Shift-F6>"):
            self.root.bind(sequence, self.handle_workspace_cycle)
        for sequence in ("<Control-d>", "<Control-D>"):
            self.root.bind(sequence, lambda event: self.handle_queue_command_shortcut(event, lambda: self.run_keyboard_once("duplicate-blocks", self.duplicate_selected_blocks)))
        for sequence in ("<Control-g>", "<Control-G>"):
            self.root.bind(sequence, lambda event: self.handle_queue_command_shortcut(event, self.group_selected_blocks))
        for sequence in ("<Control-Shift-n>", "<Control-Shift-N>"):
            self.root.bind(sequence, lambda event: self.handle_queue_command_shortcut(event, self.fill_selected_block_names))
        self.root.bind("<Control-Shift-Return>", self.handle_ctrl_shift_enter_shortcut)
        self.root.bind("<Control-Shift-KP_Enter>", self.handle_ctrl_shift_enter_shortcut)
        self.root.bind("<Control-Return>", self.handle_ctrl_enter_shortcut)
        self.root.bind("<Control-KP_Enter>", self.handle_ctrl_enter_shortcut)
        self.root.bind("<Control-Tab>", self.handle_next_builder_tab)
        self.root.bind("<Control-Shift-Tab>", lambda event: self.handle_next_builder_tab(event, backwards=True))
        self.root.bind("<Control-ISO_Left_Tab>", lambda event: self.handle_next_builder_tab(event, backwards=True))
        self.root.bind("<Return>", self.handle_return_shortcut)
        self.root.bind("<KP_Enter>", self.handle_return_shortcut)
        self.root.bind("<Up>", self.handle_queue_arrow)
        self.root.bind("<Down>", self.handle_queue_arrow)
        self.root.bind("<Control-a>", self.handle_ctrl_a_shortcut)
        self.root.bind("<Control-A>", self.handle_ctrl_a_shortcut)
        self.root.bind("<Control-c>", self.handle_ctrl_c_shortcut)
        self.root.bind("<Control-C>", self.handle_ctrl_c_shortcut)
        self.root.bind("<Control-v>", self.handle_ctrl_v_shortcut)
        self.root.bind("<Control-V>", self.handle_ctrl_v_shortcut)
        self.root.bind("<Escape>", self.handle_escape_shortcut)
        self.root.bind("<Control-s>", self.handle_save_shortcut)
        self.root.bind("<Control-S>", self.handle_save_shortcut)
        for sequence in ("<Control-Shift-o>", "<Control-Shift-O>"):
            self.root.bind(sequence, self.handle_queue_recovery_shortcut)
        self.root.bind("<Control-l>", self.handle_load_shortcut)
        self.root.bind("<Control-L>", self.handle_load_shortcut)
        self.queue_canvas.bind("<Delete>", self.handle_delete_shortcut)
        self.root.bind_all("<FocusIn>", self.handle_global_focus_in, add="+")
        self.root.bind_all("<ButtonPress-1>", self.handle_global_pointer, add="+")
        self.root.bind("<Activate>", self.handle_app_activate, add="+")
        self.bind_material_keyboard_controls()
        self.bind_workspace_navigation_controls()
        self.text_shortcuts = TextShortcutBindings(self.root)
        pending_widgets = [self.root]
        while pending_widgets:
            widget = pending_widgets.pop()
            self.text_shortcuts.install(widget)
            pending_widgets.extend(widget.winfo_children())
        # Let Tk finish mapping the window before forcing the first operator focus.
        self.root.after(0, self.activate_default_korean_input)
        self.root.after(120, self.focus_initial_keyboard_control)

    def handle_delete_shortcut(self, event: tk.Event) -> str | None:
        if not self.widget_is_in_queue(event.widget) or self.widget_is_in_queue_editor(event.widget):
            return None
        self.delete_selected_block()
        return "break"

    def handle_save_shortcut(self, event: tk.Event) -> str | None:
        if self.keyboard_state.interaction is KeyboardInteraction.COMBO_OPEN:
            return "break"
        if self.keyboard_state.zone is KeyboardZone.QUEUE_EDITOR and self.widget_is_in_queue_editor(event.widget):
            return self.run_keyboard_after_ime("save-editor", self.save_editor_from_keyboard, source_widget=event.widget)
        if self.keyboard_state.zone is KeyboardZone.QUEUE_LIST and self.widget_is_in_queue(event.widget):
            self.save_queue_recovery()
            return "break"
        if self.keyboard_state.zone is KeyboardZone.BUILDER and self.widget_is_in_builder(event.widget):
            self.save_current_config()
            return "break"
        return None

    def handle_load_shortcut(self, event: tk.Event) -> str | None:
        if self.keyboard_state.zone is KeyboardZone.QUEUE_LIST and self.widget_is_in_queue(event.widget):
            return self.focus_queue_editor()
        return None

    def handle_queue_recovery_shortcut(self, event: tk.Event) -> str | None:
        if self.keyboard_state.zone is KeyboardZone.QUEUE_LIST and self.widget_is_in_queue(event.widget):
            self.show_queue_recovery()
            return "break"
        return None

    def shortcut_move_selected(self, delta: int) -> str:
        self.move_selected_block(delta)
        return "break"

    def register_custom_drop_target(self, widget: tk.Widget) -> None:
        if DND_FILES is None or not hasattr(widget, "drop_target_register"):
            return
        try:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self.on_custom_pdf_drop)
        except Exception:
            pass

    def parse_dropped_paths(self, data: str) -> list[Path]:
        paths: list[Path] = []
        try:
            raw_items = self.root.tk.splitlist(data)
        except tk.TclError:
            raw_items = data.split()
        for item in raw_items:
            path = Path(str(item).strip())
            if path.is_file() and path.suffix.lower() == ".pdf":
                paths.append(path)
        return paths

    def add_custom_pdf_paths(self, paths: list[Path]) -> None:
        existing = {path.resolve() for path in self.custom_pdf_files if path.exists()}
        added = 0
        for path in paths:
            resolved = path.resolve()
            if resolved in existing:
                continue
            self.custom_pdf_files.append(path)
            existing.add(resolved)
            added += 1
        self.refresh_custom_pdf_list()
        if added:
            self.log(f"새 PDF 추가: {added}개")

    def on_custom_pdf_drop(self, event: tk.Event) -> str:
        paths = self.parse_dropped_paths(getattr(event, "data", ""))
        if not paths:
            self.custom_pdf_status_var.set("PDF 파일만 추가할 수 있습니다.")
            return "break"
        self.add_custom_pdf_paths(paths)
        return "break"

    def choose_custom_pdfs(self) -> None:
        selected = filedialog.askopenfilenames(
            title="대량 인쇄할 PDF 선택",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        self.add_custom_pdf_paths([Path(path) for path in selected])

    def clear_custom_pdfs(self) -> None:
        self.custom_pdf_files.clear()
        self.refresh_custom_pdf_list()

    def refresh_custom_pdf_list(self) -> None:
        if hasattr(self, "custom_drop_list"):
            self.custom_drop_list.delete(0, END)
            for index, path in enumerate(self.custom_pdf_files, start=1):
                self.custom_drop_list.insert(END, f"{index:02d}. {path.name}")
        count = len(self.custom_pdf_files)
        if count:
            self.custom_pdf_status_var.set(f"PDF {count}개 선택됨")
        elif DND_FILES is None:
            self.custom_pdf_status_var.set("드래그앤드롭 패키지가 없어 [파일 선택]으로 PDF를 추가하세요.")
        else:
            self.custom_pdf_status_var.set("PDF 파일을 여기로 드래그하거나 [파일 선택]을 누르세요.")

    def make_blank_review_block(self, copies: int) -> PrintBlock:
        if copies < 1:
            raise ValueError("부수는 1 이상이어야 합니다.")
        path = find_blank_review_pdf()
        if not path.is_file():
            raise FileNotFoundError(f"고정 백지리뷰 PDF를 찾을 수 없습니다: {path}")
        if path.suffix.casefold() != ".pdf":
            raise ValueError(f"고정 백지리뷰 파일이 PDF가 아닙니다: {path}")
        try:
            page_count = get_pdf_page_count(path)
        except Exception as error:
            raise ValueError(f"백지리뷰 PDF를 읽을 수 없습니다: {error}") from error
        printer_route, printer_override = self.resolve_block_printer_route("세로")
        display_label = f"백지리뷰 / {page_count}쪽"
        return PrintBlock(
            subject="백지리뷰",
            level="고정",
            round_name="단일",
            start=1,
            end=1,
            copies=copies,
            files=[ExamPdf(1, path.stem, path)],
            source="blank_review",
            printer_override=printer_override,
            display_label=display_label,
            printer_route=printer_route,
        )

    def add_blank_review_block(self) -> bool:
        captured_copies = int(self.blank_review_copies_var.get())
        try:
            block = self.make_blank_review_block(captured_copies)
        except Exception as error:
            messagebox.showwarning(APP_TITLE, str(error))
            return False
        token = self.reserve_block_commit()

        def commit() -> None:
            self.push_undo_state("백지리뷰 블록 추가")
            self.blocks.append(block)
            self.block_status_by_id[id(block)] = "대기"
            self.update_queue_view(select_index=len(self.blocks) - 1)
            self.update_preview()
            if self.blank_review_copies_var.get() == captured_copies:
                self.blank_review_copies_var.set(1)
            self.log(f"백지리뷰 블록 추가: {block.copies}부 / 세로")

        self.complete_block_commit(token, commit)
        return True

    def make_automatic_omr_item(self) -> ExamPdf:
        """Build the fixed landscape OMR item inserted before eligible test blocks."""
        _printer_route, printer_override = self.resolve_block_printer_route("가로")
        return ExamPdf(
            0,
            "OMR 내신",
            find_omr_pdf(),
            printer_override=printer_override,
            pages_per_sheet=1,
        )

    def make_omr_block(self, copies: int) -> PrintBlock:
        if copies < 1:
            raise ValueError("부수는 1 이상이어야 합니다.")
        path = find_omr_pdf()
        if not path.is_file():
            raise FileNotFoundError(f"고정 OMR PDF를 찾을 수 없습니다: {path}")
        if path.suffix.casefold() != ".pdf":
            raise ValueError(f"고정 OMR 파일이 PDF가 아닙니다: {path}")
        try:
            page_count = get_pdf_page_count(path)
        except Exception as error:
            raise ValueError(f"OMR PDF를 읽을 수 없습니다: {error}") from error
        printer_route, printer_override = self.resolve_block_printer_route("가로")
        return PrintBlock(
            subject="OMR 내신",
            level="고정",
            round_name="단일",
            start=1,
            end=1,
            copies=copies,
            files=[ExamPdf(1, path.stem, path)],
            source="omr",
            printer_override=printer_override,
            display_label=f"OMR 내신 / {page_count}쪽",
            printer_route=printer_route,
        )

    def add_omr_block(self) -> bool:
        captured_copies = int(self.omr_copies_var.get())
        try:
            block = self.make_omr_block(captured_copies)
        except Exception as error:
            messagebox.showwarning(APP_TITLE, str(error))
            return False
        token = self.reserve_block_commit()

        def commit() -> None:
            self.push_undo_state("OMR 블록 추가")
            self.blocks.append(block)
            self.block_status_by_id[id(block)] = "대기"
            self.update_queue_view(select_index=len(self.blocks) - 1)
            self.update_preview()
            if self.omr_copies_var.get() == captured_copies:
                self.omr_copies_var.set(1)
            self.log(f"OMR 블록 추가: {block.copies}부 / 가로")

        self.complete_block_commit(token, commit)
        return True

    def choose_order_csv(self) -> None:
        selected = filedialog.askopenfilename(
            title="대량 주문서 CSV 선택",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not selected:
            return
        self.order_csv_path = Path(selected)
        self.order_csv_result = load_order_csv(self.order_csv_path)
        self.refresh_order_csv_list()

    def save_order_csv_template(self) -> None:
        selected = filedialog.asksaveasfilename(
            title="대량 주문서 CSV 양식 저장",
            defaultextension=".csv",
            initialfile="빠른인쇄기_대량주문서_양식.csv",
            filetypes=[("CSV files", "*.csv")],
        )
        if not selected:
            return
        path = write_order_csv_template(Path(selected))
        self.order_csv_status_var.set(f"양식을 저장했습니다: {path.name}")
        self.log(f"대량 주문서 양식 저장: {path}")

    def clear_order_csv(self) -> None:
        if getattr(self, "order_build_pending", False):
            self.cancel_pending_block_preparations()
            self.refresh_able_status()
        self.order_csv_path = None
        self.order_csv_result = OrderCsvResult((), (), "")
        self.order_csv_status_var.set("CSV 주문서를 선택해 주세요.")
        self.refresh_order_csv_list()

    def refresh_order_csv_list(self) -> None:
        widget = getattr(self, "order_csv_list", None)
        result = self.order_csv_result
        if widget is not None:
            widget.delete(0, END)
            for row in result.rows:
                order_label = row.order_id or "-"
                source_label = row.source or "등록 테스트"
                widget.insert(
                    END,
                    f"{row.row_number:03d}행  {order_label}  {source_label} · {row.target_label} · {row.copies}부",
                )
            for issue in result.issues:
                widget.insert(END, f"오류  {issue.format()}")
        order_csv_path = getattr(self, "order_csv_path", None)
        if order_csv_path is None:
            return
        encoding_label = result.encoding.upper() if result.encoding else "읽기 실패"
        if result.issues:
            self.order_csv_status_var.set(
                f"{order_csv_path.name} · 정상 {len(result.rows)}행 · 오류 {len(result.issues)}건 · {encoding_label}"
            )
        else:
            self.order_csv_status_var.set(
                f"{order_csv_path.name} · 주문 {len(result.rows)}행 · {encoding_label}"
            )

    @staticmethod
    def order_issue_message(issues: list[OrderCsvIssue] | tuple[OrderCsvIssue, ...]) -> str:
        visible = list(issues[:20])
        lines = [issue.format() for issue in visible]
        if len(issues) > len(visible):
            lines.append(f"... 그 외 {len(issues) - len(visible)}건")
        return "주문서를 수정한 뒤 다시 시도해 주세요.\n\n" + "\n".join(lines)

    @staticmethod
    def validate_order_profile(row: OrderRow, fixed_profile: str) -> None:
        if row.printer_profile not in {"자동 감지", fixed_profile}:
            raise ValueError(
                f"{row.source} 프린터 프로필은 {fixed_profile} 고정입니다. "
                f"현재 값: {row.printer_profile}"
            )

    def make_unit_block_from_order(self, row: OrderRow) -> PrintBlock:
        self.validate_order_profile(row, "세로")
        subjects = catalog_subjects(self.catalog)
        subject = match_order_choice(row.subject, subjects, "과목")
        level = match_order_choice(row.level, catalog_levels(self.catalog, subject), "레벨")
        round_name = match_order_choice(
            row.round_name,
            catalog_rounds(self.catalog, subject, level),
            "회차",
        )
        files = catalog_exam_files(self.catalog, self.root_path, subject, level, round_name)
        if not files:
            files = scan_exam_files(self.root_path, subject, level, round_name)
        if not files:
            raise ValueError(f"{subject} / {level} / {round_name} 시험지 PDF를 찾을 수 없습니다.")
        files_by_number = {item.number: item for item in files}
        if row.selected_numbers:
            missing = [number for number in row.selected_numbers if number not in files_by_number]
            if missing:
                raise ValueError("현재 회차에 없는 파일 번호입니다: " + ", ".join(map(str, missing)))
            selected_files = [files_by_number[number] for number in row.selected_numbers]
            start, end = min(row.selected_numbers), max(row.selected_numbers)
            selected_numbers: tuple[int, ...] | None = row.selected_numbers
        else:
            assert row.start is not None and row.end is not None
            selected_files = [item for item in files if row.start <= item.number <= row.end]
            start, end = row.start, row.end
            selected_numbers = None
        if not selected_files:
            raise ValueError("선택된 단원평가 범위에 PDF가 없습니다.")
        printer_route, printer_override = self.resolve_block_printer_route("세로")
        return PrintBlock(
            subject=subject,
            level=level,
            round_name=round_name,
            start=start,
            end=end,
            copies=row.copies,
            files=selected_files,
            bundled=row.bundled,
            printer_override=printer_override,
            printer_route=printer_route,
            selected_numbers=selected_numbers,
        )

    def make_apex_block_from_order(self, row: OrderRow) -> PrintBlock:
        self.validate_order_profile(row, "세로")
        subjects = list(self.apex_material_catalog.subjects(row.source))
        subject_name = match_order_choice(row.subject, subjects, "과목")
        document_kinds = sorted(
            {
                document.document_kind
                for document in self.apex_material_catalog.documents
                if document.variant == row.source and document.subject == subject_name
            }
        )
        document_kind = match_order_choice(row.document_kind, document_kinds, "문서")
        document = self.apex_material_catalog.find(row.source, subject_name, document_kind)
        if document is None:
            raise ValueError(f"{row.source} / {subject_name} / {document_kind} PDF를 찾을 수 없습니다.")
        available = (
            document.lessons
            if row.mode in {"차시 별", "전체"}
            else document.lessons_for_part(row.mode)
        )
        by_number = {span.number: span for span in available}
        if row.selected_numbers:
            missing = [number for number in row.selected_numbers if number not in by_number]
            if missing:
                raise ValueError("현재 APEX 구성에 없는 차시입니다: " + ", ".join(map(str, missing)))
            spans = tuple(by_number[number] for number in row.selected_numbers)
            selected_numbers: tuple[int, ...] | None = row.selected_numbers
        elif row.start is not None and row.end is not None:
            spans = tuple(span for span in available if row.start <= span.number <= row.end)
            selected_numbers = None
        else:
            spans = available
            selected_numbers = None
        return self.make_apex_block_for_selection(
            document,
            row.mode,
            spans,
            row.copies,
            selected_numbers,
            row.bundled,
        )

    def make_able_block_from_order(self, row: OrderRow) -> PrintBlock:
        document_kind = normalize_able_document_kind(row.document_kind, "교재")
        expected_route = "A:ble" if document_kind == "교재" else "세로"
        self.validate_order_profile(row, expected_route)
        subject_name = match_order_choice(
            row.subject,
            list(self.able_material_catalog.subject_names),
            "과목",
        )
        subject = self.able_material_catalog.find(subject_name)
        if subject is None:
            raise ValueError(f"{subject_name} A:ble PDF를 찾을 수 없습니다.")
        if row.mode == "풀 세트":
            return self.make_able_block_for_selection(
                subject,
                row.mode,
                row.copies,
                document_kind=document_kind,
                bundled=row.bundled,
            )
        if row.selected_numbers:
            missing = [number for number in row.selected_numbers if number not in set(subject.lesson_numbers)]
            if missing:
                raise ValueError("현재 A:ble 과목에 없는 차시입니다: " + ", ".join(map(str, missing)))
        return self.make_able_block_for_selection(
            subject,
            row.mode,
            row.copies,
            document_kind=document_kind,
            selected_numbers=row.selected_numbers or None,
            start=row.start,
            end=row.end,
            bundled=row.bundled,
        )

    def make_training_pack_block_from_order(self, row: OrderRow) -> PrintBlock:
        self.validate_order_profile(row, "세로")
        subject_name = match_order_choice(
            row.subject,
            list(self.training_pack_material_catalog.subject_names),
            "과목",
        )
        subject = self.training_pack_material_catalog.find(subject_name)
        if subject is None:
            raise ValueError(f"{subject_name} 트레이닝팩 PDF를 찾을 수 없습니다.")
        if row.mode == "풀 세트":
            return self.make_training_pack_full_block(subject, row.copies, row.bundled)
        if row.selected_numbers:
            missing = [number for number in row.selected_numbers if number not in set(subject.lesson_numbers)]
            if missing:
                raise ValueError("현재 트레이닝팩 과목에 없는 차시입니다: " + ", ".join(map(str, missing)))
            lesson_numbers = row.selected_numbers
            selected_numbers: tuple[int, ...] | None = row.selected_numbers
            selection_label = "(" + ",".join(map(str, lesson_numbers)) + ")"
        else:
            assert row.start is not None and row.end is not None
            lesson_numbers = tuple(
                number for number in subject.lesson_numbers if row.start <= number <= row.end
            )
            selected_numbers = None
            selection_label = f"{row.start:02d}~{row.end:02d}차시"
        return self.make_training_pack_block_for_selection(
            subject,
            lesson_numbers,
            row.copies,
            selected_numbers,
            selection_label,
            row.bundled,
        )

    def make_pdf_block_from_order(self, row: OrderRow) -> PrintBlock:
        missing = [path for path in row.file_paths if not path.is_file()]
        if missing:
            raise FileNotFoundError("PDF 파일을 찾을 수 없습니다: " + ", ".join(map(str, missing)))
        invalid = [path for path in row.file_paths if path.suffix.casefold() != ".pdf"]
        if invalid:
            raise ValueError("PDF가 아닌 파일이 포함되어 있습니다: " + ", ".join(map(str, invalid)))
        files = [
            ExamPdf(index, path.stem, path)
            for index, path in enumerate(row.file_paths, start=1)
        ]
        printer_route, printer_override = self.resolve_block_printer_route(row.printer_profile)
        return PrintBlock(
            subject="새 PDF",
            level="",
            round_name="직접",
            start=1,
            end=len(files),
            copies=row.copies,
            files=files,
            bundled=row.bundled,
            source="custom",
            printer_override=printer_override,
            printer_route=printer_route,
        )

    def make_registered_test_block_from_order(
        self,
        row: OrderRow,
        asset_index: dict[str, RegisteredTestAsset] | None = None,
    ) -> PrintBlock:
        self.validate_order_profile(row, "세로")
        if asset_index is None:
            assets = build_registered_test_assets(
                self.catalog,
                self.root_path,
                self.test_catalogs,
            )
            asset_index = registered_test_asset_index(assets)
        asset = asset_index.get(row.material_id.casefold())
        if asset is None:
            raise ValueError(
                f"등록 테스트 목록에 없는 자료ID입니다: {row.material_id}"
            )
        if row.source and row.source != asset.source:
            raise ValueError(
                f"자료ID는 {asset.source} 자료인데 CSV 자료유형은 {row.source}입니다."
            )
        if not asset.path.is_file():
            raise FileNotFoundError(
                f"등록 테스트 PDF를 찾을 수 없습니다: {asset.relative_path}"
            )

        printer_route, printer_override = self.resolve_block_printer_route("세로")
        source = "catalog" if asset.source == "단원평가" else asset.source_key
        round_name = asset.round_name if asset.source == "단원평가" else asset.source
        display_label = (
            f"단원평가 / {asset.subject} / {asset.filter_value} / "
            f"{asset.round_name} / ({asset.number})"
            if asset.source == "단원평가"
            else f"{asset.source} / {asset.subject} / {asset.filter_value} / ({asset.number})"
        )
        block = PrintBlock(
            subject=asset.subject,
            level=asset.filter_value,
            round_name=round_name,
            start=asset.number,
            end=asset.number,
            copies=row.copies,
            files=[ExamPdf(asset.number, asset.display_name, asset.path)],
            bundled=row.bundled,
            source=source,
            printer_override=printer_override,
            display_label=display_label,
            printer_route=printer_route,
            selected_numbers=(asset.number,),
        )
        block.material_id = asset.material_id
        return block

    def make_block_from_order_row(
        self,
        row: OrderRow,
        asset_index: dict[str, RegisteredTestAsset] | None = None,
    ) -> PrintBlock:
        if row.material_id:
            block = self.make_registered_test_block_from_order(row, asset_index)
        elif row.source == "단원평가":
            block = self.make_unit_block_from_order(row)
        elif row.source in {"APEX", "APEX Boost"}:
            block = self.make_apex_block_from_order(row)
        elif row.source == "A:ble":
            block = self.make_able_block_from_order(row)
        elif row.source == "트레이닝팩":
            block = self.make_training_pack_block_from_order(row)
        elif row.source == "백지리뷰":
            self.validate_order_profile(row, "세로")
            block = self.make_blank_review_block(row.copies)
        elif row.source == "PDF":
            block = self.make_pdf_block_from_order(row)
        else:
            raise ValueError(f"지원하지 않는 자료유형입니다: {row.source}")
        if row.student_name:
            if not block_uses_name_separator(block):
                raise ValueError(f"{row.source} 자료에는 학생이름을 지정할 수 없습니다.")
            block.name_separator_label = normalize_name_separator_label(row.student_name)
        if row.order_id:
            base_label = block.display_label or block.target_label
            block.display_label = f"주문 {row.order_id} / {base_label}"
        block.order_id = row.order_id
        if not block.material_id:
            block.material_id = row.material_id
        return block

    def resolve_order_csv_blocks(
        self,
        rows: tuple[OrderRow, ...],
    ) -> tuple[list[PrintBlock], list[OrderCsvIssue]]:
        blocks: list[PrintBlock] = []
        issues: list[OrderCsvIssue] = []
        asset_index: dict[str, RegisteredTestAsset] | None = None
        if any(row.material_id for row in rows):
            try:
                assets = build_registered_test_assets(
                    self.catalog,
                    self.root_path,
                    self.test_catalogs,
                )
                asset_index = registered_test_asset_index(assets)
            except Exception as error:
                first_row = next(row for row in rows if row.material_id)
                return [], [OrderCsvIssue(first_row.row_number, "자료ID", str(error))]
        for row in rows:
            try:
                blocks.append(self.make_block_from_order_row(row, asset_index))
            except Exception as error:
                issues.append(OrderCsvIssue(row.row_number, row.source, str(error)))
        return blocks, issues

    def commit_order_csv_blocks(
        self,
        blocks: list[PrintBlock],
        *,
        source_name: str,
        clear_source: bool,
    ) -> None:
        blocks.sort(key=lambda block: name_separator_order_key(block.name_separator_label))
        self.push_undo_state("대량 주문서 블록 추가")
        self.blocks.extend(blocks)
        if any(block.name_separator_label for block in blocks):
            self.name_separator_page_var.set(True)
        for block in blocks:
            self.block_status_by_id[id(block)] = "대기"
        self.update_queue_view(select_index=len(self.blocks) - 1)
        self.update_preview()
        self.log(
            f"대량 주문서 추가: {source_name} / 블록 {len(blocks)}개 / "
            "이름 구분지 가나다순"
        )
        if clear_source:
            self.clear_order_csv()

    def add_order_csv_blocks(self) -> bool:
        if getattr(self, "order_build_pending", False):
            messagebox.showinfo(APP_TITLE, "대량 주문서 자료를 준비 중입니다. 완료 후 다시 눌러 주세요.")
            return False
        result = self.order_csv_result
        if not result.rows and not result.issues:
            messagebox.showwarning(APP_TITLE, "먼저 대량 주문서 CSV를 선택해 주세요.")
            return False
        if result.issues:
            messagebox.showwarning(APP_TITLE, self.order_issue_message(result.issues))
            return False
        if not hasattr(self, "background_build_service"):
            blocks, issues = self.resolve_order_csv_blocks(result.rows)
            if issues:
                self.order_csv_result = OrderCsvResult(result.rows, tuple(issues), result.encoding)
                self.refresh_order_csv_list()
                messagebox.showwarning(APP_TITLE, self.order_issue_message(issues))
                return False
            if not blocks:
                messagebox.showwarning(APP_TITLE, "추가할 주문 블록이 없습니다.")
                return False
            order_path = getattr(self, "order_csv_path", None)
            source_name = order_path.name if order_path is not None else "주문서"
            self.commit_order_csv_blocks(
                blocks,
                source_name=source_name,
                clear_source=True,
            )
            return True
        captured_path = self.order_csv_path
        captured_root = self.root_path
        captured_printer = self.printer_var.get()
        token = self.reserve_block_commit()
        epoch = self.block_preparation_epoch
        self.order_build_pending = True
        source_name = captured_path.name if captured_path is not None else "주문서"
        self.order_csv_status_var.set(
            f"{source_name} · 주문 {len(result.rows)}행 자료 준비 중 · 다른 화면은 계속 사용할 수 있습니다."
        )

        def work() -> object:
            root_token = _ORDER_ROOT_OVERRIDE.set(captured_root)
            printer_token = _ORDER_PRINTER_OVERRIDE.set(captured_printer)
            try:
                return self.resolve_order_csv_blocks(result.rows)
            finally:
                _ORDER_PRINTER_OVERRIDE.reset(printer_token)
                _ORDER_ROOT_OVERRIDE.reset(root_token)

        def finish(future: object) -> None:
            self.order_build_pending = False
            try:
                blocks, issues = future.result()  # type: ignore[attr-defined]
            except Exception as error:
                message = str(error)

                def show_error() -> None:
                    if self.order_csv_result is result:
                        self.refresh_order_csv_list()
                    messagebox.showwarning(APP_TITLE, message)

                self.complete_block_commit(token, show_error)
                return

            if issues:
                issue_tuple = tuple(issues)

                def show_issues() -> None:
                    if self.order_csv_result is result:
                        self.order_csv_result = OrderCsvResult(
                            result.rows,
                            issue_tuple,
                            result.encoding,
                        )
                        self.refresh_order_csv_list()
                    messagebox.showwarning(APP_TITLE, self.order_issue_message(issue_tuple))

                self.complete_block_commit(token, show_issues)
                return
            if not blocks:
                self.complete_block_commit(
                    token,
                    lambda: messagebox.showwarning(APP_TITLE, "추가할 주문 블록이 없습니다."),
                )
                return
            # Python's sort is stable: same-name rows retain CSV order and
            # unnamed rows stay after every named group.
            blocks.sort(key=lambda block: name_separator_order_key(block.name_separator_label))

            def commit() -> None:
                self.commit_order_csv_blocks(
                    blocks,
                    source_name=source_name,
                    clear_source=(
                        self.order_csv_result is result
                        and self.order_csv_path == captured_path
                    ),
                )

            self.complete_block_commit(token, commit)

        try:
            self.submit_background_block_build(work, finish, epoch=epoch)
        except Exception as error:
            self.order_build_pending = False
            self.cancel_block_commit(token)
            self.refresh_order_csv_list()
            messagebox.showwarning(APP_TITLE, str(error))
            return False
        return True

    def make_custom_pdf_block(self) -> PrintBlock:
        if not self.custom_pdf_files:
            raise ValueError("추가할 PDF 파일을 먼저 선택해 주세요.")
        copies = int(self.custom_copies_var.get())
        if copies < 1:
            raise ValueError("부수는 1 이상이어야 합니다.")
        files = [
            ExamPdf(number=index, name=path.stem, path=path)
            for index, path in enumerate(self.custom_pdf_files, start=1)
        ]
        route_var = getattr(self, "custom_printer_route_var", None)
        route_value = route_var.get() if route_var is not None else "자동 감지"
        printer_route, printer_override = self.resolve_block_printer_route(route_value)
        return PrintBlock(
            subject="새 PDF",
            level="",
            round_name="직접",
            start=1,
            end=len(files),
            copies=copies,
            files=files,
            bundled=self.custom_bundle_var.get(),
            source="custom",
            printer_override=printer_override,
            printer_route=printer_route,
        )

    def add_custom_pdf_block(self) -> bool:
        captured_files = tuple(self.custom_pdf_files)
        captured_copies = self.custom_copies_var.get()
        captured_name = self.new_block_name_var.get()
        try:
            block = self.apply_new_block_name(self.make_custom_pdf_block())
        except Exception as exc:
            messagebox.showwarning(APP_TITLE, str(exc))
            return False
        token = self.reserve_block_commit()

        def commit() -> None:
            self.push_undo_state("새 PDF 블록 추가")
            self.blocks.append(block)
            self.block_status_by_id[id(block)] = "대기"
            self.update_queue_view(select_index=len(self.blocks) - 1)
            self.update_preview()
            if self.custom_copies_var.get() == captured_copies:
                self.custom_copies_var.set(1)
            if self.new_block_name_var.get() == captured_name:
                self.clear_new_block_name()
            if tuple(self.custom_pdf_files) == captured_files:
                self.clear_custom_pdfs()
            self.log(
                f"새 PDF 블록 추가: {block.file_count}개 / {block.copies}부 / "
                f"{'묶음' if block.bundled else '일반'}"
            )

        self.complete_block_commit(token, commit)
        return True

    def log(self, message: str) -> None:
        def append() -> None:
            timestamp = time.strftime("%H:%M:%S")
            self.log_text.insert(END, f"[{timestamp}] {message}\n")
            self.log_text.see(END)

        if threading.current_thread() is threading.main_thread():
            append()
        else:
            self.root.after(0, append)

    def set_status(self, message: str) -> None:
        def update() -> None:
            if self.app_status_var.get() == message:
                self.update_persistent_operator_alert(message)
                return
            self.app_status_var.set(message)
            self.refresh_status_badge()
            self.update_taskbar_from_status(message)
            self.handle_status_notification(message)
            self.update_persistent_operator_alert(message)

        if threading.current_thread() is threading.main_thread():
            update()
        else:
            self.root.after(0, update)

    def update_persistent_operator_alert(self, status: str) -> None:
        spec = persistent_alert_for_status(
            status,
            device_detail=getattr(self, "device_hold_message", ""),
        )
        if spec is None:
            self.close_persistent_operator_alert(reset_dismissal=True)
            return
        if getattr(self, "persistent_alert_dismissed_key", None) == spec.key:
            return
        self.show_persistent_operator_alert(spec)

    def show_persistent_operator_alert(self, spec: PersistentAlertSpec) -> None:
        if self.is_closing:
            return
        window = getattr(self, "persistent_alert_window", None)
        is_new_alert = getattr(self, "persistent_alert_key", None) != spec.key
        if window is None or not window.winfo_exists():
            window = tk.Toplevel(self.root)
            self.persistent_alert_window = window
            window.withdraw()
            window.resizable(False, False)
            window.configure(bg=self.colors["border"])
            window.protocol("WM_DELETE_WINDOW", self.dismiss_persistent_operator_alert)
            window.bind("<Return>", self.activate_persistent_operator_alert)
            window.bind("<Escape>", lambda _event: self.dismiss_persistent_operator_alert())
            try:
                window.attributes("-topmost", True)
            except tk.TclError:
                pass

            outer = tk.Frame(
                window,
                bg=self.colors["panel"],
                highlightthickness=1,
                highlightbackground=self.colors["border"],
            )
            outer.pack(fill="both", expand=True)
            self.persistent_alert_accent_bar = tk.Frame(
                outer,
                bg=self.colors["accent"],
                height=4,
            )
            self.persistent_alert_accent_bar.pack(fill="x")
            content = tk.Frame(
                outer,
                bg=self.colors["panel"],
                padx=22,
                pady=18,
            )
            content.pack(fill="both", expand=True)
            self.persistent_alert_title_var = StringVar()
            korean_label(
                content,
                native=True,
                mode="balance",
                textvariable=self.persistent_alert_title_var,
                bg=self.colors["panel"],
                fg=self.colors["text"],
                font=(self.ui_font_family, 13, "bold"),
                anchor="w",
                justify="left",
                wraplength=408,
            ).pack(fill="x")
            self.persistent_alert_message_var = StringVar()
            korean_label(
                content,
                native=True,
                textvariable=self.persistent_alert_message_var,
                bg=self.colors["panel"],
                fg=self.colors["muted"],
                font=(self.ui_font_family, 10),
                justify="left",
                anchor="w",
                wraplength=408,
            ).pack(fill="x", pady=(8, 18))
            actions = tk.Frame(content, bg=self.colors["panel"])
            actions.pack(fill="x")
            self.persistent_alert_resume_button = ttk.Button(
                actions,
                text="해결 후 이어서",
                style="Primary.TButton",
                command=self.resume_device_print,
            )
            self.persistent_alert_confirm_button = ttk.Button(
                actions,
                text="확인",
                style="Primary.TButton",
                command=self.dismiss_persistent_operator_alert,
            )

        self.persistent_alert_key = spec.key
        window.title(spec.title)
        if self.persistent_alert_title_var is not None:
            self.persistent_alert_title_var.set(spec.title)
        if self.persistent_alert_message_var is not None:
            self.persistent_alert_message_var.set(spec.message)
        palette = {
            "danger": self.colors["danger"],
            "warning": self.colors["danger"],
            "success": self.colors["complete"],
        }
        accent = palette.get(
            spec.tone,
            self.colors["complete"],
        )
        if self.persistent_alert_accent_bar is not None:
            self.persistent_alert_accent_bar.configure(bg=accent)
        self.persistent_alert_allow_resume = spec.allow_resume
        resume_button = self.persistent_alert_resume_button
        confirm_button = self.persistent_alert_confirm_button
        if resume_button is not None and confirm_button is not None:
            resume_button.pack_forget()
            confirm_button.pack_forget()
            confirm_button.configure(
                style=("SoftAction.TButton" if spec.allow_resume else (
                    "Completion.TButton" if spec.tone == "success" else "Primary.TButton"
                ))
            )
            confirm_button.pack(side="right")
            if spec.allow_resume:
                resume_button.pack(side="right", padx=(0, 8))

        window.update_idletasks()
        width = 460
        height = max(164, window.winfo_reqheight())
        screen_width = window.winfo_screenwidth()
        screen_height = window.winfo_screenheight()
        self.root.update_idletasks()
        root_x = self.root.winfo_rootx()
        root_y = self.root.winfo_rooty()
        root_width = max(width, self.root.winfo_width())
        root_height = max(height, self.root.winfo_height())
        x = min(max(20, root_x + (root_width - width) // 2), screen_width - width - 20)
        y = min(max(20, root_y + (root_height - height) // 2), screen_height - height - 48)
        window.geometry(f"{width}x{height}+{x}+{y}")
        window.deiconify()
        window.lift()
        if is_new_alert:
            try:
                window.bell()
                window.focus_force()
                primary_button = resume_button if spec.allow_resume else confirm_button
                if primary_button is not None:
                    primary_button.focus_set()
            except tk.TclError:
                pass

    def activate_persistent_operator_alert(self, _event: tk.Event | None = None) -> str:
        if getattr(self, "persistent_alert_allow_resume", False):
            self.resume_device_print()
        else:
            self.dismiss_persistent_operator_alert()
        return "break"

    def dismiss_persistent_operator_alert(self) -> None:
        key = getattr(self, "persistent_alert_key", None)
        if key is not None:
            self.persistent_alert_dismissed_key = key
        self.close_persistent_operator_alert(reset_dismissal=False)

    def close_persistent_operator_alert(self, *, reset_dismissal: bool) -> None:
        window = getattr(self, "persistent_alert_window", None)
        self.persistent_alert_window = None
        self.persistent_alert_key = None
        self.persistent_alert_title_var = None
        self.persistent_alert_message_var = None
        self.persistent_alert_accent_bar = None
        self.persistent_alert_resume_button = None
        self.persistent_alert_confirm_button = None
        self.persistent_alert_allow_resume = False
        if reset_dismissal:
            self.persistent_alert_dismissed_key = None
        if window is not None:
            try:
                if window.winfo_exists():
                    window.destroy()
            except tk.TclError:
                pass

    def set_current_work(self, message: str) -> None:
        def update() -> None:
            if self.current_work_var.get() == message:
                return
            self.current_work_var.set(message)

        if threading.current_thread() is threading.main_thread():
            update()
        else:
            self.root.after(0, update)

    def initialize_taskbar_indicator(self) -> None:
        if (
            self.is_closing
            or os.name != "nt"
            or getattr(self, "taskbar_indicator", None) is not None
        ):
            return
        try:
            from unit_print_taskbar import TaskbarIndicator

            self.root.update_idletasks()
            self.taskbar_indicator = TaskbarIndicator(self.root.winfo_id())
            self.apply_taskbar_display_state()
            self.schedule_taskbar_refresh()
        except Exception as exc:
            self.taskbar_indicator = None
            self.log(f"작업 표시줄 상태 표시를 사용할 수 없습니다: {exc}")

    def initialize_windows_notifier(self) -> None:
        if (
            self.is_closing
            or os.name != "nt"
            or getattr(self, "windows_notifier", None) is not None
        ):
            return
        try:
            self.root.update_idletasks()
            self.windows_notifier = WindowsBalloonNotifier(
                self.root.winfo_id(),
                icon_path=APP_ICON_PATH if APP_ICON_PATH.is_file() else None,
            )
        except Exception as exc:
            self.windows_notifier = None
            self.log(f"Windows 알림을 사용할 수 없습니다: {exc}")

    def notify_operator(
        self,
        category: str,
        key: object,
        title: str,
        message: str,
        level: NotificationLevel = NotificationLevel.INFO,
    ) -> bool:
        if self.is_closing or os.name != "nt":
            return False
        notifier = getattr(self, "windows_notifier", None)
        if notifier is None:
            self.initialize_windows_notifier()
            notifier = getattr(self, "windows_notifier", None)
        if notifier is None:
            return False
        gate = getattr(self, "notification_gate", None)
        if gate is None:
            gate = NotificationGate()
            self.notification_gate = gate
        if not gate.allow(category, key):
            return False
        try:
            shown = notifier.show(title, message, level)
        except Exception as exc:
            self.log(f"Windows 알림 표시 실패: {exc}")
            return False
        if not shown:
            return False
        previous = getattr(self, "windows_notification_cleanup_after_id", None)
        if previous is not None:
            try:
                self.root.after_cancel(previous)
            except tk.TclError:
                pass
        self.windows_notification_cleanup_after_id = self.root.after(
            15_000,
            self.hide_windows_notification,
        )
        return True

    def hide_windows_notification(self) -> None:
        self.windows_notification_cleanup_after_id = None
        notifier = getattr(self, "windows_notifier", None)
        if notifier is None:
            return
        try:
            notifier.hide()
        except Exception as exc:
            self.log(f"Windows 알림 정리 실패: {exc}")

    def close_windows_notifier(self) -> None:
        after_id = getattr(self, "windows_notification_cleanup_after_id", None)
        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except tk.TclError:
                pass
            self.windows_notification_cleanup_after_id = None
        notifier = getattr(self, "windows_notifier", None)
        self.windows_notifier = None
        if notifier is not None:
            try:
                notifier.close()
            except Exception:
                pass

    def handle_status_notification(self, message: str) -> None:
        if message == "실제 인쇄 완료":
            self.notify_operator(
                "run-terminal",
                "physical-complete",
                "빠른 인쇄 완료",
                "프린터에서 이번 작업의 실제 인쇄 완료를 확인했습니다.",
            )
        elif message == "전송 완료 / 인쇄 완료":
            self.notify_operator(
                "run-terminal",
                "windows-queue-complete",
                "빠른 인쇄 대기열 완료",
                "Windows 인쇄 대기열이 비었습니다. 실물 출력은 프린터에서 확인해 주세요.",
            )
        elif message == "전송 완료 / 대기열 확인 실패":
            self.notify_operator(
                "run-terminal",
                "queue-check-failed",
                "인쇄 완료 확인 실패",
                "전송은 끝났지만 Windows 인쇄 대기열의 완료 상태를 확인하지 못했습니다.",
                NotificationLevel.ERROR,
            )
        elif message == "오류 발생":
            self.notify_operator(
                "run-terminal",
                "run-error",
                "빠른 인쇄 오류",
                "인쇄 작업이 중단되었습니다. 앱의 현재 작업과 로그에서 원인을 확인해 주세요.",
                NotificationLevel.ERROR,
            )

    def schedule_taskbar_refresh(self) -> None:
        if self.is_closing or getattr(self, "taskbar_refresh_after_id", None) is not None:
            return
        self.taskbar_refresh_after_id = self.root.after(
            TASKBAR_REFRESH_INTERVAL_MS,
            self.refresh_taskbar_indicator,
        )

    def refresh_taskbar_indicator(self) -> None:
        """Safely restore visible taskbar state after an Explorer restart."""

        self.taskbar_refresh_after_id = None
        if self.is_closing:
            return
        indicator = getattr(self, "taskbar_indicator", None)
        kind = getattr(self, "taskbar_display_state", ("idle", 0, 1))[0]
        if indicator is not None and kind != "idle":
            try:
                indicator.invalidate_cache()
                self.apply_taskbar_display_state()
            except Exception as exc:
                self.log(f"작업 표시줄 상태 복구 실패: {exc}")
        self.schedule_taskbar_refresh()

    def set_taskbar_display_state(
        self,
        kind: str,
        completed: int = 0,
        total: int = 1,
        *,
        completion_basis: str = "none",
        run_id: str | None = None,
    ) -> None:
        def update() -> None:
            state = getattr(self, "print_progress_state", PrintProgressState())
            if run_id is not None and state.run_id != run_id:
                return
            state = transition_taskbar_state(state, kind, completed, total)
            if kind == "complete":
                state = replace(state, completion_basis=completion_basis)
            self.print_progress_state = state
            target = (state.taskbar_kind, state.completed, state.total)
            if getattr(self, "taskbar_display_state", ("idle", 0, 1)) == target:
                return
            self.taskbar_display_state = target
            self.taskbar_completion_pending = state.taskbar_kind == "complete"
            self.apply_taskbar_display_state()

        if threading.current_thread() is threading.main_thread():
            update()
        else:
            self.root.after(0, update)

    def apply_taskbar_display_state(self) -> None:
        indicator = getattr(self, "taskbar_indicator", None)
        if indicator is None:
            return
        kind, completed, total = self.taskbar_display_state
        try:
            if kind == "progress":
                indicator.clear_overlay()
                indicator.set_progress(completed, total)
            elif kind == "indeterminate":
                indicator.clear_overlay()
                indicator.set_indeterminate()
            elif kind == "paused":
                indicator.clear_overlay()
                indicator.set_paused()
            elif kind == "error":
                indicator.set_error()
            elif kind == "complete":
                indicator.set_completed()
            else:
                indicator.reset()
        except Exception as exc:
            self.log(f"작업 표시줄 상태 갱신 실패: {exc}")

    def update_taskbar_from_status(self, message: str) -> None:
        self.update_resource_completion_status(message)
        if message == "실제 인쇄 완료":
            self.set_taskbar_display_state("complete", completion_basis="device")
        elif message == "전송 완료 / 인쇄 완료":
            # An empty Windows spooler does not prove that paper reached the
            # output tray. Reserve the `1` badge for device-confirmed output.
            self.set_taskbar_display_state("idle")
        elif message in {"오류 발생", "전송 완료 / 대기열 확인 실패"}:
            self.set_taskbar_display_state("error")
        elif message in {
            "프린터 오류 / 인쇄 일시 중지",
            "전송 중지 요청됨",
            "전송 중지됨",
            "완료 확인 중지됨",
            "전송 완료 / 완료 확인 중지",
        }:
            self.set_taskbar_display_state("paused")
        elif message in {
            "인쇄 준비 중",
            "인쇄 전송 중",
            "실제 인쇄 확인 중",
            "전송 완료 / 인쇄 중",
        }:
            if getattr(self, "taskbar_display_state", ("idle", 0, 1))[0] != "progress":
                self.set_taskbar_display_state("indeterminate")
        elif message in {"대기 중", "미리보기 완료"}:
            self.set_taskbar_display_state("idle")

    def set_taskbar_print_progress(
        self,
        completed: int,
        total: int,
        *,
        run_id: str | None = None,
    ) -> None:
        self.set_taskbar_display_state(
            "progress",
            completed,
            total,
            run_id=run_id,
        )

    def begin_taskbar_print_run(self, total: int) -> None:
        gate = getattr(self, "notification_gate", None)
        if gate is not None:
            gate.clear("run-terminal")
        state = getattr(self, "print_progress_state", PrintProgressState()).begin(
            getattr(self, "history_run_id", None),
            total,
        )
        self.print_progress_state = state
        self.taskbar_display_state = (state.taskbar_kind, state.completed, state.total)
        self.taskbar_completion_pending = False
        self.apply_taskbar_display_state()

    def clear_taskbar_completion_notification(self) -> None:
        if not getattr(self, "taskbar_completion_pending", False):
            return
        self.set_taskbar_display_state("idle")

    def close_taskbar_indicator(self) -> None:
        indicator = getattr(self, "taskbar_indicator", None)
        self.taskbar_indicator = None
        after_id = getattr(self, "taskbar_refresh_after_id", None)
        self.taskbar_refresh_after_id = None
        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except tk.TclError:
                pass
        try:
            if indicator is not None:
                indicator.close()
        except Exception:
            pass
        self.close_windows_notifier()

    def initialize_device_monitor_from_config(self) -> None:
        if self.is_closing:
            return
        if not bool(self.device_monitor_config.get("enabled", False)):
            self.set_device_status_text("프린터 상태: 연결 설정 필요")
            return
        base_url = str(self.device_monitor_config.get("base_url", "")).strip()
        if not base_url:
            self.set_device_status_text("프린터 상태: 주소 설정 필요")
            return
        try:
            credential = load_device_credential(base_url)
        except CredentialStoreError as exc:
            self.set_device_status_text(f"프린터 상태: 자격 증명 오류 · {exc}")
            return
        if credential is None:
            self.set_device_status_text("프린터 상태: 부서 ID·PIN 연결 필요")
            return
        self.activate_device_monitor(
            CanonStatusClient(
                base_url,
                department_id=credential.department_id,
                pin=credential.pin,
            )
        )

    def activate_device_monitor(self, client: CanonStatusClient) -> None:
        if getattr(self, "is_printing", False):
            raise RuntimeError("인쇄 중에는 프린터 상태 연결을 변경할 수 없습니다. 전송을 중지한 뒤 변경해 주세요.")
        previous = self.device_monitor
        if previous is not None:
            previous.stop()
        idle_interval_seconds = max(
            DEVICE_IDLE_STATUS_INTERVAL_SECONDS,
            float(
                self.device_monitor_config.get(
                    "idle_interval_seconds", DEVICE_IDLE_STATUS_INTERVAL_SECONDS
                )
            ),
        )
        self.device_monitor_config["idle_interval_seconds"] = idle_interval_seconds
        self.device_monitor = PrinterDeviceMonitor(
            client,
            idle_interval_seconds=idle_interval_seconds,
            active_interval_seconds=float(
                self.device_monitor_config.get("active_interval_seconds", 1.0)
            ),
        )
        self.last_device_notification_health = None
        self.device_fault_notification_active = False
        self.set_device_status_text("프린터 상태: 연결 중", DeviceHealth.UNKNOWN)
        self.device_monitor.start()

    def notify_device_fault(self, snapshot: DeviceSnapshot) -> None:
        message = snapshot.errors[0] if snapshot.errors else snapshot.status_text
        title = (
            "프린터 연결 끊김"
            if snapshot.health is DeviceHealth.OFFLINE
            else "프린터 작업 중지"
        )
        shown = self.notify_operator(
            "printer-attention",
            f"{snapshot.health.value}:{message}",
            title,
            f"{message} 앱에서 상태를 확인하고 원인을 해결해 주세요.",
            NotificationLevel.ERROR,
        )
        if shown:
            self.device_fault_notification_active = True

    def handle_device_snapshot_notification(self, snapshot: DeviceSnapshot) -> None:
        previous = getattr(self, "last_device_notification_health", None)
        current = snapshot.health
        bad = current in {
            DeviceHealth.ERROR,
            DeviceHealth.HELD,
            DeviceHealth.OFFLINE,
        } or snapshot.stale
        previously_good = previous in {DeviceHealth.READY, DeviceHealth.BUSY}
        gate = getattr(self, "notification_gate", None)
        if bad:
            if gate is not None:
                gate.clear("printer-recovery")
            if self.is_printing or previously_good:
                self.notify_device_fault(snapshot)
        elif current in {DeviceHealth.READY, DeviceHealth.BUSY}:
            previously_bad = previous in {
                DeviceHealth.ERROR,
                DeviceHealth.HELD,
                DeviceHealth.OFFLINE,
            }
            if previously_bad and (
                self.is_printing
                or getattr(self, "device_fault_notification_active", False)
            ):
                action = (
                    "앱에서 [해결 후 이어서]를 눌러 남은 작업을 계속해 주세요."
                    if self.device_hold_active
                    else "앱이 남은 인쇄 상태 확인을 계속합니다."
                )
                self.notify_operator(
                    "printer-recovery",
                    f"{current.value}:{snapshot.status_text}",
                    "프린터 정상 복구",
                    action,
                )
            if gate is not None:
                gate.clear("printer-attention")
            self.device_fault_notification_active = False
        self.last_device_notification_health = current

    def poll_device_status(self) -> None:
        if self.is_closing:
            return
        self.refresh_resource_completion_clock()
        monitor = self.device_monitor
        if monitor is not None:
            snapshot = monitor.latest_snapshot
            is_initial_placeholder = (
                not snapshot.authenticated
                and snapshot.status_text == "프린터 상태 연결 전"
            )
            if (
                not is_initial_placeholder
                and getattr(self, "device_status_observed_at", None) != snapshot.observed_at
            ):
                self.device_status_observed_at = snapshot.observed_at
                view = build_device_status_view(snapshot)
                self.set_device_status_text(
                    f"{view.headline} · {view.a4_text} · 스테이플 {snapshot.consumables.staple_text}",
                    snapshot.health,
                )
                self.handle_device_snapshot_notification(snapshot)
                self.schedule_resource_estimate()
        self.root.after(1000, self.poll_device_status)

    def open_device_monitor_dialog(self) -> None:
        if self.is_printing:
            messagebox.showinfo(APP_TITLE, "인쇄 중에는 프린터 상태 연결을 변경할 수 없습니다. 전송을 중지한 뒤 변경해 주세요.")
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("프린터 상태 연결")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.configure(bg=self.colors["panel"])
        body = ttk.Frame(dialog, style="Surface.TFrame", padding=18)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)

        base_url_var = StringVar(value=str(self.device_monitor_config.get("base_url", "")))
        department_var = StringVar()
        pin_var = StringVar()
        # The operator asked for one-time setup.  Keep storage opt-out visible,
        # but default the first successful connection to Windows Credential
        # Manager so the next app launch can reconnect without another PIN.
        remember_var = BooleanVar(value=True)
        window_size_var = IntVar(
            value=max(1, min(10, int(self.device_monitor_config.get("dispatch_window_size", 6))))
        )
        stored_credential = None
        base_url = base_url_var.get().strip()
        if base_url:
            try:
                stored_credential = load_device_credential(base_url)
            except CredentialStoreError:
                stored_credential = None
        if stored_credential is not None:
            department_var.set(stored_credential.department_id)
            remember_var.set(True)

        ttk.Label(body, text="Canon Remote UI 주소", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=6
        )
        ttk.Entry(body, textvariable=base_url_var, width=42).grid(
            row=0, column=1, sticky="ew", pady=6
        )
        ttk.Label(body, text="부서 ID", style="Field.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=6
        )
        ttk.Entry(body, textvariable=department_var).grid(row=1, column=1, sticky="ew", pady=6)
        ttk.Label(body, text="PIN", style="Field.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 10), pady=6
        )
        ttk.Entry(body, textvariable=pin_var, show="●").grid(row=2, column=1, sticky="ew", pady=6)
        ttk.Checkbutton(
            body,
            text="Windows 자격 증명 관리자에 저장",
            variable=remember_var,
        ).grid(row=3, column=1, sticky="w", pady=(5, 10))
        ttk.Label(
            body,
            text="PIN은 앱 설정 파일과 인쇄 이력에 기록되지 않습니다.",
            style="SectionHint.TLabel",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 12))
        ttk.Label(body, text="선전송 창", style="Field.TLabel").grid(
            row=5, column=0, sticky="w", padx=(0, 10), pady=6
        )
        window_frame = ttk.Frame(body, style="Surface.TFrame")
        window_frame.grid(row=5, column=1, sticky="w", pady=6)
        ttk.Spinbox(window_frame, from_=1, to=10, width=4, textvariable=window_size_var).pack(
            side="left"
        )
        ttk.Label(
            window_frame,
            text="건 (권장 6 · 대기 약 5)",
            style="SectionHint.TLabel",
        ).pack(side="left", padx=(6, 0))

        actions = ttk.Frame(body, style="Surface.TFrame")
        actions.grid(row=6, column=0, columnspan=2, sticky="e", pady=(10, 0))

        def connect() -> None:
            if self.is_printing:
                messagebox.showwarning(APP_TITLE, "인쇄 중에는 프린터 상태 연결을 변경할 수 없습니다.", parent=dialog)
                return
            new_base_url = base_url_var.get().strip().rstrip("/")
            department_id = department_var.get().strip()
            pin = pin_var.get()
            try:
                existing = load_device_credential(new_base_url) if new_base_url else None
            except CredentialStoreError:
                existing = None
            if existing is not None:
                department_id = department_id or existing.department_id
                pin = pin or existing.pin
            if not new_base_url or not department_id or not pin:
                messagebox.showwarning(
                    APP_TITLE,
                    "Remote UI 주소, 부서 ID, PIN을 모두 입력해 주세요.",
                    parent=dialog,
                )
                return
            try:
                client = CanonStatusClient(
                    new_base_url,
                    department_id=department_id,
                    pin=pin,
                )
                # Validate the actual Remote UI login before persisting the
                # secret or closing the dialog.  A typo must not turn into a
                # silent background reconnect failure on every app launch.
                client.login()
                if remember_var.get():
                    save_device_credential(new_base_url, department_id, pin)
                else:
                    delete_device_credential(new_base_url)
            except (ValueError, CredentialStoreError, CanonStatusError) as exc:
                messagebox.showwarning(APP_TITLE, str(exc), parent=dialog)
                return
            self.device_monitor_config.update(
                {
                    "enabled": True,
                    "base_url": new_base_url,
                    "strict_ordering": True,
                    "idle_interval_seconds": DEVICE_IDLE_STATUS_INTERVAL_SECONDS,
                    "active_interval_seconds": 1.0,
                    "dispatch_window_size": max(1, min(10, int(window_size_var.get()))),
                    "dispatch_policy_version": DEVICE_DISPATCH_POLICY_VERSION,
                }
            )
            self.activate_device_monitor(client)
            self.save_current_config()
            dialog.destroy()

        ttk.Button(actions, text="취소", style="SoftAction.TButton", command=dialog.destroy).pack(
            side="left", padx=(0, 7)
        )
        ttk.Button(actions, text="연결", style="Primary.TButton", command=connect).pack(side="left")
        dialog.bind("<Return>", lambda _event: connect())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.grab_set()
        dialog.update_idletasks()
        dialog.geometry(
            f"+{self.root.winfo_rootx() + max(40, (self.root.winfo_width() - dialog.winfo_reqwidth()) // 2)}"
            f"+{self.root.winfo_rooty() + 100}"
        )

    def enter_device_hold(self, snapshot: DeviceSnapshot) -> None:
        message = snapshot.errors[0] if snapshot.errors else snapshot.status_text

        def update() -> None:
            self.device_hold_active = True
            self.device_hold_message = message
            self.set_status("프린터 오류 / 인쇄 일시 중지")
            self.set_current_work(f"현재 작업: {message} · 해결 후 이어서 눌러 주세요")
            self.refresh_print_action_buttons()
            self.notify_device_fault(snapshot)

        self.root.after(0, update)

    def resume_device_print(self) -> None:
        monitor = self.device_monitor
        if monitor is None:
            messagebox.showwarning(APP_TITLE, "먼저 프린터 상태 연결을 설정해 주세요.")
            return
        snapshot = monitor.latest_snapshot
        active_hold = any(job.state == DeviceJobState.HELD for job in snapshot.active_jobs)
        if snapshot.stale or snapshot.health in {
            DeviceHealth.ERROR,
            DeviceHealth.HELD,
            DeviceHealth.OFFLINE,
        } or active_hold:
            messagebox.showwarning(
                APP_TITLE,
                f"프린터 오류가 아직 해제되지 않았습니다.\n\n{snapshot.status_text}",
            )
            monitor.refresh_now()
            return
        self.device_hold_active = False
        self.device_hold_message = ""
        self.device_fault_notification_active = False
        gate = getattr(self, "notification_gate", None)
        if gate is not None:
            gate.clear("printer-recovery")
        self.device_resume_event.set()
        monitor.refresh_now()
        self.set_taskbar_display_state("running")
        self.set_status("실제 인쇄 확인 중")
        self.set_current_work("현재 작업: 멈춘 작업의 실제 완료를 기다리는 중")
        self.refresh_print_action_buttons()

    def refresh_print_action_buttons(self) -> None:
        preparing = getattr(self, "_print_preparation_request", None) is not None
        start_button = getattr(self, "start_print_button", None)
        if start_button is not None:
            active = self.is_printing or preparing
            flags = set(start_button.state())
            changes = []
            if ("alternate" in flags) != active:
                changes.append("alternate" if active else "!alternate")
            if ("disabled" in flags) != preparing:
                changes.append("disabled" if preparing else "!disabled")
            if changes:
                start_button.state(changes)
            label = "준비 중" if preparing else "인쇄 중" if self.is_printing else "인쇄 시작"
            if str(start_button.cget("text")) != label:
                start_button.configure(text=label)
        retry_button = getattr(self, "retry_button", None)
        if retry_button is not None:
            target_state = (
                "normal"
                if self.retry_batch is not None and not self.is_printing and not preparing
                else "disabled"
            )
            if str(retry_button.cget("state")) != target_state:
                retry_button.configure(state=target_state)
        cancel_button = getattr(self, "cancel_print_button", None)
        if cancel_button is not None:
            can_cancel = preparing or (self.is_printing and not self.print_cancel_event.is_set())
            target_state = "normal" if can_cancel else "disabled"
            if str(cancel_button.cget("state")) != target_state:
                cancel_button.configure(state=target_state)
        resume_button = getattr(self, "device_resume_button", None)
        if resume_button is not None:
            target_state = (
                "normal" if self.is_printing and self.device_hold_active else "disabled"
            )
            if str(resume_button.cget("state")) != target_state:
                resume_button.configure(state=target_state)

    def refresh_retry_button(self) -> None:
        self.refresh_print_action_buttons()

    def cancel_current_print(self) -> None:
        if self.cancel_pending_print_preparation() and not self.is_printing:
            self.queue_status_var.set("인쇄 준비를 취소했습니다.")
            return
        if not self.is_printing or self.print_cancel_event.is_set():
            return
        confirmed = messagebox.askyesno(
            APP_TITLE,
            "남은 작업 전송을 중지할까요?\n\n이미 프린터로 전송된 작업은 계속 인쇄됩니다.",
        )
        if not confirmed:
            return
        self.record_history("cancel_requested")
        self.print_cancel_event.set()
        self.set_status("전송 중지 요청됨")
        self.set_current_work("현재 작업: 안전하게 전송을 중지하는 중")
        self.log("사용자가 남은 작업 전송 중지를 요청했습니다.")
        self.refresh_print_action_buttons()

    def on_close(self) -> None:
        if self.is_closing:
            return
        if not self.is_printing:
            self.is_closing = True
            self.cancel_source_paths_open()
            self.close_persistent_operator_alert(reset_dismissal=True)
            if self.device_monitor is not None:
                self.device_monitor.stop()
            self.close_pdf_work_services()
            self.close_taskbar_indicator()
            self.save_current_config()
            self.schedule_queue_recovery_save("session_closed")
            self.record_history("session_closed", remaining_retry_count=len(self.retry_batch.jobs) if self.retry_batch else 0)
            self.close_queue_recovery()
            self.root.destroy()
            return
        confirmed = messagebox.askyesno(
            APP_TITLE,
            "인쇄 전송이 진행 중입니다. 남은 전송을 중지하고 종료할까요?\n\n"
            "이미 프린터로 전송된 작업은 계속 인쇄됩니다.",
        )
        if not confirmed:
            return
        self.is_closing = True
        self.cancel_source_paths_open()
        self.cancel_pending_block_preparations()
        self.print_cancel_event.set()
        self.set_status("종료 준비 중")
        self.set_current_work("현재 작업: 전송 중지 후 종료 준비 중")
        self.refresh_print_action_buttons()
        self.root.after(100, self.finish_close_when_idle)

    def finish_close_when_idle(self) -> None:
        if self.is_printing:
            self.root.after(100, self.finish_close_when_idle)
            return
        try:
            self.close_persistent_operator_alert(reset_dismissal=True)
            if self.device_monitor is not None:
                self.device_monitor.stop()
            self.close_pdf_work_services()
            self.close_taskbar_indicator()
            self.save_current_config()
        finally:
            self.schedule_queue_recovery_save("session_closed")
            self.record_history("session_closed", remaining_retry_count=len(self.retry_batch.jobs) if self.retry_batch else 0)
            self.close_queue_recovery()
            self.root.destroy()

    def preserve_retry_from_failure(self, job_index: int) -> None:
        with self.print_state_lock:
            pending_jobs = tuple(self.print_pending_jobs)
            include_separator_pages = getattr(self, "print_include_separator_pages", False)
            include_name_separator_pages = getattr(
                self,
                "print_include_name_separator_pages",
                False,
            )
            preceding_name_separator_label = (
                self.name_separator_label_before_job_index(pending_jobs, job_index)
                if include_name_separator_pages
                else ""
            )
        if job_index >= len(pending_jobs):
            return
        self.retry_batch = retry_from_failure(
            pending_jobs,
            job_index,
            include_separator_pages=include_separator_pages,
            include_name_separator_pages=include_name_separator_pages,
            preceding_name_separator_label=preceding_name_separator_label,
        )
        self.root.after(0, self.refresh_retry_button)

    def retry_remaining_jobs(self) -> None:
        if getattr(self, "_print_preparation_request", None) is not None:
            return
        if getattr(self, "group_build_pending", False):
            self.queue_status_var.set("묶음 준비가 끝난 뒤 인쇄를 시작해 주세요.")
            return
        if self.is_printing:
            messagebox.showinfo(APP_TITLE, "현재 인쇄 전송이 끝난 뒤에 재시도할 수 있습니다.")
            return
        retry_batch = self.retry_batch
        if retry_batch is not None:
            visible_uids = {id(block) for block in self.blocks}
            retained_jobs = tuple(job for job in retry_batch.jobs if job.block_uid in visible_uids)
            if len(retained_jobs) != len(retry_batch.jobs):
                retry_batch = replace(retry_batch, jobs=retained_jobs) if retained_jobs else None
                self.retry_batch = retry_batch
        if retry_batch is None:
            self.refresh_retry_button()
            messagebox.showinfo(APP_TITLE, "재시도할 남은 작업이 없습니다.")
            return
        retry_uids = {job.block_uid for job in retry_batch.jobs}
        blocks = [block for block in self.blocks if id(block) in retry_uids]
        self.begin_print_preparation("retry", blocks, retry_batch=retry_batch)

    def queue_block_status(self, block: PrintBlock) -> str:
        return self.block_status_by_id.get(id(block), "대기")

    def queue_block_status_label(self, block: PrintBlock) -> str:
        status = self.queue_block_status(block)
        if status != "완료":
            return status
        detail = self.block_detail_by_id.get(id(block), "")
        return "인쇄 완료" if "실제 인쇄 완료" in detail else "전송 완료"

    def sync_retry_queue(self) -> None:
        """Keep retry jobs attached to the exact blocks still in the UI."""
        if self.is_printing:
            return
        batch = getattr(self, "retry_batch", None)
        if batch is None:
            self.retry_batch = None
            self.refresh_retry_button()
            return
        include_names = batch.include_name_separator_pages or any(
            job.is_name_separator for job in batch.jobs
        )
        if not include_names:
            self.retry_batch = retain_visible_retry_jobs(batch, self.blocks)
            self.refresh_retry_button()
            return

        visible_ids = {id(block) for block in self.blocks}
        retry_ids = {job.block_uid for job in batch.jobs}
        ordered_blocks = [
            block for block in self.blocks if id(block) in retry_ids
        ]
        removed_ids = retry_ids - visible_ids
        prepared_paths = {
            label: materialize_name_separator_pdf(label, NAME_SEPARATOR_CACHE_DIR)
            for label in {
                normalize_name_separator_label(block.name_separator_label)
                for block in ordered_blocks
                if block_uses_name_separator(block)
            }
            if label
        }

        def make_name_job(block: PrintBlock, label: str, template: PrintJob) -> PrintJob:
            return PrintJob(
                block_index=template.block_index,
                block_total=template.block_total,
                block_uid=id(block),
                block_summary=block.summary,
                item=ExamPdf(0, f"이름 구분지 - {label}", prepared_paths[label]),
                copies=1,
                is_separator=True,
                is_name_separator=True,
                history_block=block_history_data(block),
            )

        repair = reconcile_pending_name_groups(
            batch.jobs,
            ordered_blocks,
            removed_ids,
            (),
            enabled=True,
            preceding_name_label=batch.preceding_name_separator_label,
            normalize_label=normalize_name_separator_label,
            block_uses_name_separator=block_uses_name_separator,
            block_name_label=lambda block: block.name_separator_label,
            job_name_label=lambda job: job.history_block.get("student_name", ""),
            make_name_separator_job=make_name_job,
        )
        self.retry_batch = (
            RetryBatch(
                repair.jobs,
                include_separator_pages=batch.include_separator_pages,
                include_name_separator_pages=True,
                preceding_name_separator_label=batch.preceding_name_separator_label,
            )
            if repair.jobs
            else None
        )
        self.refresh_retry_button()

    def queue_status_colors(self, status: str) -> tuple[str, str, str]:
        if status in {"진행 중", "전송 중"}:
            return self.colors["success_soft"], self.colors["success"], self.colors["success"]
        if status == "완료":
            return self.colors["complete_soft"], self.colors["complete"], self.colors["complete"]
        if status in {"대기", "추가됨", "예약"}:
            return self.colors["warning_soft"], self.colors["warning"], self.colors["warning"]
        if status in {"오류", "경고", "중지됨"}:
            return self.colors["danger_soft"], self.colors["danger"], self.colors["danger"]
        return self.colors["panel_soft"], self.colors["muted"], self.colors["border_soft"]

    def mark_block_status(self, block_uid: int, status: str, detail: str = "") -> None:
        def update() -> None:
            status_changed = self.block_status_by_id.get(block_uid) != status
            detail_changed = False
            if detail:
                detail_changed = self.block_detail_by_id.get(block_uid) != detail
            elif status in {"완료", "오류", "중지됨"}:
                detail_changed = block_uid in self.block_detail_by_id
            if not status_changed and not detail_changed:
                return
            self.block_status_by_id[block_uid] = status
            if detail:
                self.block_detail_by_id[block_uid] = detail
            elif status in {"완료", "오류", "중지됨"}:
                self.block_detail_by_id.pop(block_uid, None)
            self.draw_queue()

        if threading.current_thread() is threading.main_thread():
            update()
        else:
            self.root.after(0, update)

    def mark_blocks_status(self, blocks: list[PrintBlock], status: str) -> None:
        def update() -> None:
            for block in blocks:
                self.block_status_by_id[id(block)] = status
                self.block_detail_by_id.pop(id(block), None)
            self.draw_queue()

        if threading.current_thread() is threading.main_thread():
            update()
        else:
            self.root.after(0, update)

    def block_is_scheduled_for_print(self, block: PrintBlock) -> bool:
        with self.print_state_lock:
            return bool(self.is_printing and id(block) in self.print_scheduled_block_ids)

    def editable_queue_block_ids(self, blocks: list[PrintBlock]) -> set[int]:
        with self.print_state_lock:
            return self._editable_queue_block_ids_locked(blocks)

    def _editable_queue_block_ids_locked(self, blocks: list[PrintBlock]) -> set[int]:
        retry = getattr(self, "retry_batch", None)
        return editable_waiting_block_ids(
            blocks, self.block_status_by_id,
            scheduled_ids=getattr(self, "print_scheduled_block_ids", ()),
            started_ids=getattr(self, "print_started_block_ids", ()),
            pending_jobs=getattr(self, "print_pending_jobs", ()),
            retry_jobs=retry.jobs if retry is not None else (),
            active_block_uid=getattr(self, "active_print_block_uid", None),
        )

    @staticmethod
    def prepare_queue_edit_name_paths(blocks: list[PrintBlock], enabled: bool) -> dict[str, Path]:
        if not enabled:
            return {}
        labels = {
            normalize_name_separator_label(block.name_separator_label)
            for block in blocks if block_uses_name_separator(block)
        }
        return {label: materialize_name_separator_pdf(label, NAME_SEPARATOR_CACHE_DIR)
                for label in labels if label}

    def apply_waiting_block_replacements(
        self, replacements: dict[int, tuple[PrintBlock, ...]], *,
        job_replacements: dict[int, tuple[PrintJob, ...]],
        prepared_name_paths: dict[str, Path], undo_label: str,
    ) -> bool:
        """Commit UI blocks and unclaimed dispatch jobs in one ownership lock."""
        repair: PendingNameRepair | None = None
        replaced_job_ids: tuple[str, ...] = ()
        with self.print_state_lock:
            selected = [block for block in self.blocks if id(block) in replacements]
            if (len(selected) != len(replacements)
                    or set(replacements) - self._editable_queue_block_ids_locked(selected)):
                return False
            next_blocks = [item for block in self.blocks
                           for item in replacements.get(id(block), (block,))]
            scheduled = set(getattr(self, "print_scheduled_block_ids", ()))
            changed_scheduled = scheduled & replacements.keys()
            if changed_scheduled:
                if not self.is_printing or set(job_replacements) != changed_scheduled:
                    return False
                protected = set(getattr(self, "print_started_block_ids", ()))
                if self.active_print_block_uid is not None:
                    protected.add(self.active_print_block_uid)
                replaced_job_ids = tuple(job.job_id for job in self.print_pending_jobs
                                         if job.block_uid in changed_scheduled)
                jobs = replace_unstarted_block_jobs(
                    self.print_pending_jobs, job_replacements, protected,
                )
                scheduled = (scheduled - changed_scheduled) | {
                    job.block_uid for items in job_replacements.values() for job in items
                }
                blocks_by_uid = {id(block): block for block in next_blocks}
                dispatch_blocks = [blocks_by_uid[uid] for uid in dict.fromkeys(
                    job.block_uid for job in jobs
                )]
                repair = self.reconcile_active_name_jobs_locked(
                    jobs, dispatch_blocks,
                    (), protected, prepared_name_paths,
                )
            self.push_undo_state(undo_label)
            for old in selected:
                old_status = self.block_status_by_id.pop(id(old), "대기")
                old_detail = self.block_detail_by_id.pop(id(old), None)
                for new in replacements[id(old)]:
                    same_record = new.history_id == old.history_id
                    self.block_status_by_id[id(new)] = (
                        old_status if same_record else "예약" if id(new) in scheduled else "대기"
                    )
                    if same_record and old_detail is not None:
                        self.block_detail_by_id[id(new)] = old_detail
            self.blocks = next_blocks
            if repair is not None:
                self.print_pending_jobs = list(repair.jobs)
                self.print_scheduled_block_ids = scheduled
                self.print_last_name_separator_label = (
                    repair.trailing_name_label if self.print_include_name_separator_pages else ""
                )
                self.print_queue_revision = getattr(self, "print_queue_revision", 0) + 1
        if repair is not None:
            replacement_job_ids = {job.job_id for items in job_replacements.values() for job in items}
            self.record_history(
                "reserved_jobs_replaced", reason=undo_label,
                replaced_block_ids=[block.history_id for block in selected],
                removed_job_ids=list(dict.fromkeys(replaced_job_ids + repair.removed_job_ids)),
                replacement_job_ids=sorted(replacement_job_ids),
                added_name_separator_job_ids=[job.job_id for job in repair.added_jobs],
            )
        return True

    @staticmethod
    def trailing_name_separator_label(
        blocks: list[PrintBlock],
        preceding_label: object = "",
    ) -> str:
        previous = normalize_name_separator_label(preceding_label)
        for block in blocks:
            if not block_uses_name_separator(block):
                previous = ""
            else:
                previous = normalize_name_separator_label(block.name_separator_label)
        return previous

    def name_separator_label_before_job_index(
        self,
        jobs: tuple[PrintJob, ...] | list[PrintJob],
        job_index: int,
    ) -> str:
        """Return the name-group context established before an unsent suffix."""

        ordered_uids: list[int] = []
        seen: set[int] = set()
        for job in jobs[:max(0, job_index)]:
            if job.block_uid not in seen:
                ordered_uids.append(job.block_uid)
                seen.add(job.block_uid)
        block_by_uid = {id(block): block for block in self.blocks}
        blocks = [block_by_uid[uid] for uid in ordered_uids if uid in block_by_uid]
        return self.trailing_name_separator_label(blocks)

    def claim_active_print_job(self, job: PrintJob, expected_index: int | None = None) -> bool:
        """Claim a queued job before dispatch so deletion cannot race its send."""
        with self.print_state_lock:
            if expected_index is not None:
                if not 0 <= expected_index < len(self.print_pending_jobs):
                    return False
                if self.print_pending_jobs[expected_index].job_id != job.job_id:
                    return False
            elif not any(pending.job_id == job.job_id for pending in self.print_pending_jobs):
                return False
            started_ids = getattr(self, "print_started_block_ids", set())
            started_ids.add(job.block_uid)
            self.print_started_block_ids = started_ids
            self.active_print_block_uid = job.block_uid
            return True

    def repair_pending_name_separators_locked(
        self,
        removed_block_ids: set[int],
        prepared_paths: dict[str, Path],
    ) -> PendingNameRepair:
        """Repair only the unstarted suffix after a scheduled queue edit.

        The caller holds ``print_state_lock``.  Jobs belonging to any started
        block form an immutable prefix so submitted or uncertain work can
        never be recreated or moved.
        """

        scheduled_after = self.print_scheduled_block_ids - removed_block_ids
        ordered_blocks = [block for block in self.blocks if id(block) in scheduled_after]
        started_ids = set(getattr(self, "print_started_block_ids", ()))
        if self.active_print_block_uid is not None:
            started_ids.add(self.active_print_block_uid)

        repair = self.reconcile_active_name_jobs_locked(
            self.print_pending_jobs, ordered_blocks, removed_block_ids, started_ids,
            prepared_paths,
        )
        self.print_pending_jobs = list(repair.jobs)
        self.print_last_name_separator_label = (
            repair.trailing_name_label if self.print_include_name_separator_pages else ""
        )
        return repair

    def reconcile_active_name_jobs_locked(
        self, jobs: tuple[PrintJob, ...] | list[PrintJob], ordered_blocks: list[PrintBlock],
        removed_ids: set[int] | tuple[int, ...], protected_ids: set[int], prepared_paths: dict[str, Path],
    ) -> PendingNameRepair:
        started_blocks = [block for block in ordered_blocks if id(block) in protected_ids]
        preceding = self.trailing_name_separator_label(started_blocks)
        def make_name_job(block: PrintBlock, label: str, template: PrintJob) -> PrintJob:
            if label not in prepared_paths:
                raise ValueError("이름 구분지 준비 중 큐가 바뀌었습니다. 다시 선택해 주세요.")
            item = ExamPdf(
                0,
                f"이름 구분지 - {label}",
                prepared_paths[label],
            )
            return PrintJob(
                block_index=template.block_index,
                block_total=template.block_total,
                block_uid=id(block),
                block_summary=block.summary,
                item=item,
                copies=1,
                is_separator=True,
                is_name_separator=True,
                history_block=block_history_data(block),
            )

        return reconcile_pending_name_groups(
            jobs,
            ordered_blocks,
            removed_ids,
            protected_ids,
            enabled=self.print_include_name_separator_pages,
            preceding_name_label=preceding,
            normalize_label=normalize_name_separator_label,
            block_uses_name_separator=block_uses_name_separator,
            block_name_label=lambda block: block.name_separator_label,
            job_name_label=lambda job: job.history_block.get("student_name", ""),
            make_name_separator_job=make_name_job,
        )

    def remove_reserved_blocks_from_active_print(
        self,
        blocks: list[PrintBlock],
    ) -> tuple[bool, tuple[PrintJob, ...]]:
        """Remove only scheduled blocks whose first job has not started."""
        with self.print_state_lock:
            if not self.is_printing:
                return True, ()
            include_name_separators = self.print_include_name_separator_pages
        requested_ids = {id(block) for block in blocks}
        prepared_paths: dict[str, Path] = {}
        if include_name_separators:
            labels = {
                normalize_name_separator_label(block.name_separator_label)
                for block in self.blocks
                if id(block) not in requested_ids and block_uses_name_separator(block)
            }
            prepared_paths = {
                label: materialize_name_separator_pdf(label, NAME_SEPARATOR_CACHE_DIR)
                for label in labels
                if label
            }
        repair: PendingNameRepair | None = None
        with self.print_state_lock:
            if not self.is_printing:
                return True, ()
            scheduled_ids = requested_ids & self.print_scheduled_block_ids
            if not scheduled_ids:
                return True, ()
            started_ids = set(getattr(self, "print_started_block_ids", ()))
            if self.active_print_block_uid is not None:
                started_ids.add(self.active_print_block_uid)
            if scheduled_ids & started_ids:
                return False, ()
            removed_jobs = tuple(
                job for job in self.print_pending_jobs if job.block_uid in scheduled_ids
            )
            removable_ids = {job.block_uid for job in removed_jobs}
            # A scheduled block with no pending jobs has already crossed the
            # worker boundary, even if its Tk status repaint has not arrived.
            if removable_ids != scheduled_ids:
                return False, ()
            repair = self.repair_pending_name_separators_locked(
                scheduled_ids,
                prepared_paths,
            )
            self.print_scheduled_block_ids.difference_update(scheduled_ids)
            self.print_queue_revision = getattr(self, "print_queue_revision", 0) + 1
        self.record_history(
            "reserved_jobs_removed",
            removed_block_ids=[block.history_id for block in blocks if id(block) in scheduled_ids],
            removed_job_ids=list(repair.removed_job_ids) if repair is not None else [],
            added_name_separator_job_ids=(
                [job.job_id for job in repair.added_jobs]
                if repair is not None
                else []
            ),
            reason="operator_deleted_unstarted_blocks",
        )
        return True, removed_jobs

    def choose_root(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.root_dir_var.get() or str(DEFAULT_ROOT))
        if chosen:
            self.cancel_pending_block_preparations()
            self.root_dir_var.set(chosen)
            self.log("자료 폴더를 변경했습니다. 내장 목록의 상대 경로로 파일을 찾습니다.")
            self.update_preview()

    def choose_sumatra(self) -> None:
        chosen = filedialog.askopenfilename(
            title="SumatraPDF.exe 선택",
            filetypes=[("SumatraPDF", "SumatraPDF.exe"), ("실행 파일", "*.exe"), ("모든 파일", "*.*")],
        )
        if chosen:
            self.sumatra_var.set(chosen)

    def detect_sumatra(self) -> None:
        detected = find_sumatra_pdf(self.sumatra_var.get())
        if detected:
            self.sumatra_var.set(detected)
            self.log(f"SumatraPDF 감지: {detected}")
        else:
            self.log("SumatraPDF.exe를 찾지 못했습니다.")
            messagebox.showwarning(APP_TITLE, "SumatraPDF.exe를 찾지 못했습니다.")

    def open_printer_preferences(self) -> None:
        printer_name = self.printer_var.get().strip()
        if not printer_name:
            messagebox.showwarning(APP_TITLE, "프린터를 선택해 주세요.")
            return
        try:
            subprocess.Popen(["rundll32.exe", "printui.dll,PrintUIEntry", "/e", "/n", printer_name])
            self.log("프린터 드라이버 설정 창을 열었습니다.")
        except Exception as exc:
            self.log(f"드라이버 설정 열기 실패: {exc}")
            messagebox.showerror(APP_TITLE, f"드라이버 설정 열기 실패:\n{exc}")

    def setup_orientation_profiles(self) -> None:
        base_queue = self.printer_var.get().strip()
        try:
            profiles = derive_profile_names(base_queue)
        except ValueError as error:
            messagebox.showwarning(APP_TITLE, str(error))
            return
        runner = AppPowerShellRunner()
        discovery = inspect_base_queue(profiles.base_queue, runner)
        plan_or_block = plan_profile_setup(profiles, discovery, frozenset(load_printers()))
        if isinstance(plan_or_block, ProfileSetupBlocked):
            message = f"방향 프로필을 준비할 수 없습니다.\n{plan_or_block.detail}"
            self.profile_status_var.set("방향 프로필: 설정 불가")
            self.log(message)
            messagebox.showwarning(APP_TITLE, message)
            return
        if not plan_or_block.commands:
            self.profile_status_var.set("프린터 프로필: 모두 준비됨")
            self.log("방향 프로필이 이미 준비되어 있습니다.")
            messagebox.showinfo(APP_TITLE, "세로/가로/구분지/A:ble 전용 프린터 프로필이 이미 준비되어 있습니다.")
            return
        queue_lines = "\n".join(f"- {command.queue_name}" for command in plan_or_block.commands)
        confirmed = messagebox.askyesno(
            APP_TITLE,
            "현재 기본 프린터는 바꾸지 않고 아래 전용 큐만 만듭니다.\n\n"
            f"{queue_lines}\n\nA4, 흑백, 양면, 방향별 스테이플 기본값을 적용할까요?",
        )
        if not confirmed:
            return
        execution = execute_profile_setup(plan_or_block, runner)
        refreshed_printers = load_printers()
        with self.printer_inventory_lock:
            self.printer_inventory_cache = frozenset(refreshed_printers)
            self.printer_inventory_cached_at = time.monotonic()
        printer_combo = getattr(self, "printer_combo", None)
        if printer_combo is not None:
            printer_combo["values"] = refreshed_printers
        if execution.is_complete:
            self.profile_status_var.set("프린터 프로필: 모두 준비됨")
            self.log(f"방향 프로필 생성 완료: {', '.join(execution.created_queue_names)}")
            messagebox.showinfo(APP_TITLE, "방향 프로필 설정이 완료되었습니다.")
            return
        self.profile_status_var.set("방향 프로필: 일부 생성됨")
        self.log(f"방향 프로필 생성 중단: {execution.detail}")
        messagebox.showerror(
            APP_TITLE,
            "일부 방향 프로필만 생성되었습니다.\n"
            f"실패한 큐: {execution.failed_queue_name}\n{execution.detail}",
        )

    def ensure_orientation_profiles_ready(
        self,
        printer_name: str | None = None,
        available_printers: frozenset[str] | None = None,
        *,
        update_status: bool = True,
    ) -> None:
        profiles = derive_profile_names(printer_name or self.printer_var.get())
        available = available_printers if available_printers is not None else frozenset(load_printers())
        missing = tuple(queue_name for queue_name in profiles.profile_queues if queue_name not in available)
        if missing:
            missing_text = "\n".join(f"- {queue_name}" for queue_name in missing)
            if update_status:
                self.profile_status_var.set("방향 프로필: 설정 필요")
            raise RuntimeError(f"방향별 전용 프린터 프로필을 먼저 준비해 주세요.\n{missing_text}")
        if update_status:
            self.profile_status_var.set("프린터 프로필: 모두 사용")

    def cached_printer_inventory(self) -> frozenset[str]:
        now = time.monotonic()
        with self.printer_inventory_lock:
            cached = self.printer_inventory_cache
            cached_at = self.printer_inventory_cached_at
        if cached is not None and now - cached_at <= PRINTER_INVENTORY_TTL_SECONDS:
            return cached
        available = frozenset(load_printers())
        with self.printer_inventory_lock:
            self.printer_inventory_cache = available
            self.printer_inventory_cached_at = time.monotonic()
        return available

    def warm_printer_inventory_cache(self) -> None:
        with self.printer_inventory_lock:
            if self.printer_inventory_warming:
                return
            if (
                self.printer_inventory_cache is not None
                and time.monotonic() - self.printer_inventory_cached_at
                <= PRINTER_INVENTORY_TTL_SECONDS
            ):
                return
            self.printer_inventory_warming = True

        def load() -> None:
            available = frozenset(load_printers())
            with self.printer_inventory_lock:
                self.printer_inventory_cache = available
                self.printer_inventory_cached_at = time.monotonic()
                self.printer_inventory_warming = False

        threading.Thread(target=load, daemon=True).start()

    def ensure_block_printers_ready(
        self,
        blocks: list[PrintBlock],
        available_printers: frozenset[str] | None = None,
    ) -> None:
        required = sorted(
            {
                printer_name
                for block in blocks
                for printer_name in (
                    block.printer_override,
                    *(item.printer_override for item in block.files),
                )
                if printer_name
            }
        )
        self.ensure_named_printers_ready(required, available_printers)

    def ensure_job_printers_ready(
        self,
        jobs: list[PrintJob],
        available_printers: frozenset[str] | None = None,
    ) -> None:
        required = sorted({job.printer_override for job in jobs if job.printer_override})
        self.ensure_named_printers_ready(required, available_printers)

    def ensure_named_printers_ready(
        self,
        required: list[str],
        available_printers: frozenset[str] | None = None,
    ) -> None:
        if not required:
            return
        available = available_printers if available_printers is not None else frozenset(load_printers())
        missing = [printer_name for printer_name in required if printer_name not in available]
        if missing:
            missing_text = "\n".join(f"- {printer_name}" for printer_name in missing)
            raise RuntimeError(f"교재 전용 프린터 프로필을 찾을 수 없습니다.\n{missing_text}")

    def route_job_printer(self, job: PrintJob, printer_name: str | None = None) -> str:
        return route_job_to_profile(printer_name or self.printer_var.get(), job, self.pdf_preflight_by_path)

    def block_print_destinations(
        self,
        blocks: list[PrintBlock],
        printer_name: str,
        *,
        include_separator_pages: bool,
        include_name_separator_pages: bool,
    ) -> frozenset[str]:
        """Resolve real routes without regenerating separators or expanded jobs."""
        profiles = derive_profile_names(printer_name)
        targets = {printer_name}
        if include_separator_pages:
            targets.add(profiles.separator_queue)
        if include_name_separator_pages and any(block_uses_name_separator(block) for block in blocks):
            targets.add(profiles.portrait_queue)
        if any(block_requires_omr_sheet(block) for block in blocks):
            targets.add(profiles.landscape_queue)
        for block in blocks:
            for item in block.files:
                override, pages_per_sheet = resolve_exam_print_settings(block, item)
                job = PrintJob(block_uid=id(block), block_summary=block.summary,
                               block_index=1, block_total=1, item=item, copies=1,
                               printer_override=override, pages_per_sheet=pages_per_sheet)
                targets.add(route_job_to_profile(printer_name, job, self.pdf_preflight_by_path))
        return frozenset(targets)

    def ensure_device_printer_binding(
        self,
        run_options: PrintRunOptions,
        *,
        blocks: list[PrintBlock] | None = None,
        jobs: list[PrintJob] | None = None,
        include_separator_pages: bool = False,
        include_name_separator_pages: bool = False,
        monitor_base_url: str | None = None,
    ) -> PrintRunOptions:
        if run_options.dry_run or not run_options.device_completion_mode:
            return run_options
        if monitor_base_url is None:
            monitor = self.device_monitor
            if monitor is None:
                raise RuntimeError("프린터 상태 연결이 없습니다.")
            base_url = str(monitor.client.base_url).strip().rstrip("/")
        else:
            base_url = monitor_base_url
            if not base_url:
                raise RuntimeError("프린터 상태 연결이 없습니다.")
        if run_options.device_monitor_base_url and run_options.device_monitor_base_url != base_url:
            raise RuntimeError("인쇄 실행 중 프린터 상태 연결이 변경됐습니다. 기존 작업의 완료를 확인한 뒤 다시 시작해 주세요.")
        if jobs is not None:
            targets = frozenset({run_options.printer_name, *(self.route_job_printer(job, run_options.printer_name) for job in jobs)})
        else:
            targets = self.block_print_destinations(
                blocks or [], run_options.printer_name,
                include_separator_pages=include_separator_pages,
                include_name_separator_pages=include_name_separator_pages,
            )
        inventory = read_printer_binding_inventory()
        result = validate_printer_binding(inventory, sorted(targets), base_url)
        if result.unverifiable_names:
            resolved_hosts = resolve_printer_binding_hosts(inventory, sorted(targets), base_url)
            result = validate_printer_binding(inventory, sorted(targets), base_url, resolved_hosts=resolved_hosts)
        if not result.is_valid:
            raise RuntimeError(result.message)
        return replace(run_options, device_monitor_base_url=base_url,
                       device_verified_printers=run_options.device_verified_printers | targets)

    def assert_device_binding_for_dispatch(
        self, run_options: PrintRunOptions, monitor: PrinterDeviceMonitor, target_printer: str | None = None,
    ) -> None:
        """Read run authorization under the queue lock, including verified appends."""
        with self.print_state_lock:
            active_options = self.print_run_options
        if (
            not run_options.device_monitor_base_url
            or monitor is not self.device_monitor
            or str(monitor.client.base_url).strip().rstrip("/") != run_options.device_monitor_base_url
            or active_options is None
            or active_options.device_monitor_base_url != run_options.device_monitor_base_url
            or not active_options.device_verified_printers
        ):
            raise RuntimeError("프린터 상태 연결과 인쇄 경로의 검증이 유효하지 않습니다. 전송하지 않고 중지합니다.")
        if target_printer is not None and target_printer not in active_options.device_verified_printers:
            raise RuntimeError(f"장비 연결이 검증되지 않은 프린터입니다: {target_printer}. 전송하지 않고 중지합니다.")

    def refresh_all(self) -> None:
        subjects = catalog_subjects(self.catalog) if self.catalog else scan_subjects(self.root_path)
        self.subject_combo["values"] = subjects
        if subjects:
            current = self.subject_var.get()
            self.subject_var.set(current if current in subjects else subjects[0])
        if self.catalog:
            self.log(f"내장 목록 사용: 시험지 {count_catalog_files(self.catalog)}개")
        self.refresh_levels()

    def refresh_levels(self) -> None:
        if self.catalog:
            levels = catalog_levels(self.catalog, self.subject_var.get())
        else:
            levels = scan_levels(self.root_path, self.subject_var.get())
        self.level_combo["values"] = levels
        if levels:
            current = self.level_var.get()
            self.level_var.set(current if current in levels else levels[0])
        else:
            self.level_var.set("")
        self.refresh_rounds()

    def refresh_rounds(self) -> None:
        if self.catalog:
            rounds = catalog_rounds(self.catalog, self.subject_var.get(), self.level_var.get())
        else:
            rounds = scan_rounds(self.root_path, self.subject_var.get(), self.level_var.get())
        self.round_combo["values"] = rounds
        if rounds:
            current = self.round_var.get()
            self.round_var.set(current if current in rounds else rounds[0])
        else:
            self.round_var.set("")
        self.refresh_files()

    def refresh_files(self) -> None:
        if self.catalog:
            self.exam_files = catalog_exam_files(
                self.catalog,
                self.root_path,
                self.subject_var.get(),
                self.level_var.get(),
                self.round_var.get(),
            )
        else:
            self.exam_files = scan_exam_files(
                self.root_path,
                self.subject_var.get(),
                self.level_var.get(),
                self.round_var.get(),
            )
        values = [item.label for item in self.exam_files]
        self.start_combo["values"] = values
        self.end_combo["values"] = values
        self.unit_selected_numbers_var.set("")
        if values:
            current_start = self.start_var.get()
            current_end = self.end_var.get()
            self.start_var.set(current_start if current_start in values else values[0])
            self.end_var.set(current_end if current_end in values else values[min(4, len(values) - 1)])
        else:
            self.start_var.set("")
            self.end_var.set("")
        self.update_builder_status()

    def refresh_edit_subjects(self) -> None:
        subjects = catalog_subjects(self.catalog) if self.catalog else scan_subjects(self.root_path)
        self.edit_subject_combo["values"] = subjects
        if subjects and self.edit_subject_var.get() not in subjects:
            self.edit_subject_var.set(subjects[0])
        self.refresh_edit_levels()

    def refresh_edit_levels(self) -> None:
        if self.edit_subject_var.get() == "새 PDF":
            self.edit_level_combo["values"] = ["직접"]
            self.edit_round_combo["values"] = ["파일"]
            self.edit_level_var.set("직접")
            self.edit_round_var.set("파일")
            return
        if self.catalog:
            levels = catalog_levels(self.catalog, self.edit_subject_var.get())
        else:
            levels = scan_levels(self.root_path, self.edit_subject_var.get())
        current = self.edit_level_var.get()
        self.edit_level_combo["values"] = levels
        self.edit_level_var.set(current if current in levels else (levels[0] if levels else ""))
        self.refresh_edit_rounds()

    def refresh_edit_rounds(self) -> None:
        if self.edit_subject_var.get() == "새 PDF":
            self.edit_round_combo["values"] = ["파일"]
            self.edit_round_var.set("파일")
            return
        if self.catalog:
            rounds = catalog_rounds(self.catalog, self.edit_subject_var.get(), self.edit_level_var.get())
        else:
            rounds = scan_rounds(self.root_path, self.edit_subject_var.get(), self.edit_level_var.get())
        current = self.edit_round_var.get()
        self.edit_round_combo["values"] = rounds
        self.edit_round_var.set(current if current in rounds else (rounds[0] if rounds else ""))
        self.refresh_edit_files()

    def refresh_edit_files(self) -> None:
        if self.edit_subject_var.get() == "새 PDF":
            return
        if self.catalog:
            self.edit_exam_files = catalog_exam_files(
                self.catalog,
                self.root_path,
                self.edit_subject_var.get(),
                self.edit_level_var.get(),
                self.edit_round_var.get(),
            )
        else:
            self.edit_exam_files = scan_exam_files(
                self.root_path,
                self.edit_subject_var.get(),
                self.edit_level_var.get(),
                self.edit_round_var.get(),
            )
        values = [item.label for item in self.edit_exam_files]
        self.edit_start_combo["values"] = values
        self.edit_end_combo["values"] = values
        if values:
            self.edit_start_var.set(self.edit_start_var.get() if self.edit_start_var.get() in values else values[0])
            self.edit_end_var.set(
                self.edit_end_var.get() if self.edit_end_var.get() in values else values[min(4, len(values) - 1)]
            )
        else:
            self.edit_start_var.set("")
            self.edit_end_var.set("")
        self.edit_selected_numbers_var.set("")

    def label_for_number(self, number: int) -> str:
        for item in self.exam_files:
            if item.number == number:
                return item.label
        return f"{number:02d}"

    def edit_label_for_number(self, number: int) -> str:
        for item in self.edit_exam_files:
            if item.number == number:
                return item.label
        return f"{number:02d}"

    def selected_range(self) -> tuple[int, int]:
        start = parse_choice_number(self.start_var.get())
        end = parse_choice_number(self.end_var.get())
        if start > end:
            start, end = end, start
        return start, end

    def current_range_files(self) -> list[ExamPdf]:
        start, end = self.selected_range()
        return [item for item in self.exam_files if start <= item.number <= end]

    def selected_file_numbers(self) -> tuple[int, ...]:
        value = getattr(self, "unit_selected_numbers_var", None)
        return parse_file_number_list(value.get() if value is not None else "")

    def current_selected_files(self, numbers: tuple[int, ...]) -> list[ExamPdf]:
        files_by_number = {item.number: item for item in self.exam_files}
        missing = [number for number in numbers if number not in files_by_number]
        if missing:
            missing_label = ", ".join(str(number) for number in missing)
            raise ValueError(f"현재 회차에 없는 파일 번호입니다: {missing_label}")
        return [files_by_number[number] for number in numbers]

    def current_form_files(self) -> tuple[tuple[int, ...] | None, list[ExamPdf]]:
        numbers = self.selected_file_numbers()
        if not numbers:
            return None, self.current_range_files()
        return numbers, self.current_selected_files(numbers)

    def selected_edit_range(self) -> tuple[int, int]:
        start = parse_choice_number(self.edit_start_var.get())
        end = parse_choice_number(self.edit_end_var.get())
        if start > end:
            start, end = end, start
        return start, end

    def current_edit_range_files(self) -> list[ExamPdf]:
        start, end = self.selected_edit_range()
        return [item for item in self.edit_exam_files if start <= item.number <= end]

    def selected_edit_file_numbers(self) -> tuple[int, ...]:
        value = getattr(self, "edit_selected_numbers_var", None)
        return parse_file_number_list(value.get() if value is not None else "")

    def current_selected_edit_files(self, numbers: tuple[int, ...]) -> list[ExamPdf]:
        files_by_number = {item.number: item for item in self.edit_exam_files}
        missing = [number for number in numbers if number not in files_by_number]
        if missing:
            missing_label = ", ".join(str(number) for number in missing)
            raise ValueError(f"현재 회차에 없는 파일 번호입니다: {missing_label}")
        return [files_by_number[number] for number in numbers]

    def current_edit_files(self) -> tuple[tuple[int, ...] | None, list[ExamPdf]]:
        numbers = self.selected_edit_file_numbers()
        if not numbers:
            return None, self.current_edit_range_files()
        return numbers, self.current_selected_edit_files(numbers)

    def resolve_block_printer_route(self, route_value: object) -> tuple[str, str | None]:
        route = normalize_printer_route(route_value)
        base_printer = _ORDER_PRINTER_OVERRIDE.get()
        if base_printer is None:
            printer_var = getattr(self, "printer_var", None)
            base_printer = printer_var.get() if printer_var is not None else DEFAULT_PRINTER
        return route, resolve_custom_pdf_printer_profile(base_printer, route)

    def apply_new_block_name(self, block: PrintBlock) -> PrintBlock:
        enabled_variable = getattr(self, "name_separator_page_var", None)
        name_variable = getattr(self, "new_block_name_var", None)
        if enabled_variable is not None and enabled_variable.get() and name_variable is not None:
            block.name_separator_label = normalize_name_separator_label(name_variable.get())
        return block

    def clear_new_block_name(self) -> None:
        variable = getattr(self, "new_block_name_var", None)
        if variable is not None:
            variable.set("")

    def make_block_from_form(self) -> PrintBlock:
        selected_numbers, files = self.current_form_files()
        if selected_numbers:
            start, end = min(selected_numbers), max(selected_numbers)
        else:
            start, end = self.selected_range()
        copies = int(self.block_copies_var.get())
        if copies < 1:
            raise ValueError("부수는 1 이상이어야 합니다.")
        if not files:
            raise ValueError("선택된 범위에 시험지 파일이 없습니다.")
        printer_route, printer_override = self.resolve_block_printer_route("세로")
        return PrintBlock(
            subject=self.subject_var.get(),
            level=self.level_var.get(),
            round_name=self.round_var.get(),
            start=start,
            end=end,
            copies=copies,
            files=files,
            bundled=self.bundle_var.get(),
            printer_override=printer_override,
            printer_route=printer_route,
            selected_numbers=selected_numbers,
        )

    def make_able_block_from_editor(self, selected_block: PrintBlock, copies: int, bundled: bool) -> PrintBlock:
        subject = self.able_material_catalog.find(selected_block.subject)
        if subject is None:
            raise ValueError(f"{selected_block.subject} A:ble 자료를 찾을 수 없습니다.")
        document_kind = able_block_document_kind(selected_block)
        if selected_block.round_name == "풀 세트":
            return self.make_able_block_for_selection(
                subject,
                "풀 세트",
                copies,
                document_kind=document_kind,
                bundled=bundled,
            )

        selected_value = getattr(self, "edit_selected_numbers_var", None)
        raw_value = str(selected_value.get()).strip() if selected_value is not None else ""
        selected_numbers = parse_file_number_list(raw_value)
        available = set(subject.lesson_numbers)
        missing = [number for number in selected_numbers if number not in available]
        if missing:
            missing_label = ", ".join(str(number) for number in missing)
            raise ValueError(f"현재 A:ble 과목에 없는 차시입니다: {missing_label}")
        return self.make_able_block_for_selection(
            subject,
            selected_block.round_name,
            copies,
            document_kind=document_kind,
            selected_numbers=selected_numbers or None,
            start=selected_block.start,
            end=selected_block.end,
            bundled=bundled,
        )

    def make_apex_block_from_editor(
        self, selected_block: PrintBlock, copies: int, bundled: bool
    ) -> PrintBlock:
        document = self.apex_material_catalog.find(
            selected_block.level,
            selected_block.subject,
            selected_block.round_name,
        )
        if document is None:
            raise ValueError(
                f"{selected_block.level} / {selected_block.subject} / "
                f"{selected_block.round_name} APEX 자료를 찾을 수 없습니다."
            )
        selection_mode = selected_block.material_kind
        if selection_mode not in {"차시 별", "전체", "part.1", "part.2"}:
            label_parts = [part.strip() for part in selected_block.display_label.split("/")]
            selection_mode = next(
                (part for part in label_parts if part in {"차시 별", "전체", "part.1", "part.2"}),
                "차시 별",
            )
        available_spans = (
            document.lessons
            if selection_mode in {"차시 별", "전체"}
            else document.lessons_for_part(selection_mode)
        )
        spans_by_number = {span.number: span for span in available_spans}
        selected_numbers = parse_file_number_list(self.edit_selected_numbers_var.get().strip())
        if selected_numbers:
            missing = [number for number in selected_numbers if number not in spans_by_number]
            if missing:
                missing_label = ", ".join(str(number) for number in missing)
                raise ValueError(f"현재 APEX 선택 범위에 없는 차시입니다: {missing_label}")
            spans = tuple(spans_by_number[number] for number in selected_numbers)
        else:
            spans = tuple(
                span
                for span in available_spans
                if selected_block.start <= span.number <= selected_block.end
            )
            if not spans and selection_mode != "차시 별":
                spans = available_spans
        return self.make_apex_block_for_selection(
            document,
            selection_mode,
            spans,
            copies,
            selected_numbers or None,
            bundled,
        )

    def make_training_pack_block_from_editor(
        self, selected_block: PrintBlock, copies: int, bundled: bool
    ) -> PrintBlock:
        subject = self.training_pack_material_catalog.find(selected_block.subject)
        if subject is None:
            raise ValueError(f"{selected_block.subject} 트레이닝팩 자료를 찾을 수 없습니다.")
        if selected_block.round_name == "풀 세트":
            return self.make_training_pack_full_block(subject, copies, bundled)
        selected_numbers = parse_file_number_list(self.edit_selected_numbers_var.get().strip())
        if selected_numbers:
            lesson_numbers = selected_numbers
            selection_label = "(" + ",".join(str(number) for number in lesson_numbers) + ")"
        else:
            start, end = selected_block.start, selected_block.end
            lesson_numbers = tuple(number for number in subject.lesson_numbers if start <= number <= end)
            selection_label = f"{start:02d}~{end:02d}차시"
        return self.make_training_pack_block_for_selection(
            subject,
            lesson_numbers,
            copies,
            selected_numbers or None,
            selection_label,
            bundled,
        )

    def make_frequent_type_block_from_editor(
        self, selected_block: PrintBlock, copies: int, bundled: bool
    ) -> PrintBlock:
        subject = self.frequent_type_material_catalog.find(selected_block.subject)
        if subject is None:
            raise ValueError(f"{selected_block.subject} 빈출유형 자료를 찾을 수 없습니다.")
        if selected_block.round_name == "풀 세트":
            return self.make_lesson_material_full_block(
                "frequent_type", subject, copies, bundled
            )
        selected_numbers = parse_file_number_list(self.edit_selected_numbers_var.get().strip())
        if selected_numbers:
            lesson_numbers = selected_numbers
            selection_label = "(" + ",".join(str(number) for number in lesson_numbers) + ")"
        else:
            start, end = selected_block.start, selected_block.end
            lesson_numbers = tuple(
                number for number in subject.lesson_numbers if start <= number <= end
            )
            selection_label = f"{start:02d}~{end:02d}차시"
        return self.make_lesson_material_block_for_selection(
            "frequent_type",
            subject,
            lesson_numbers,
            copies,
            selected_numbers or None,
            selection_label,
            bundled,
        )

    def make_selected_exam_block_from_editor(
        self, selected_block: PrintBlock, copies: int, bundled: bool
    ) -> PrintBlock:
        subject = self.selected_exam_material_catalog.find_subject(selected_block.subject)
        if subject is None:
            raise ValueError(f"{selected_block.subject} 기출엄선 자료를 찾을 수 없습니다.")

        selected_numbers = parse_file_number_list(
            self.edit_selected_numbers_var.get().strip()
        )
        if selected_numbers:
            lesson_numbers = selected_numbers
        else:
            start, end = self.selected_edit_range()
            lesson_numbers = tuple(
                lesson.number
                for lesson in subject.lessons
                if start <= lesson.number <= end and lesson.is_available
            )

        lessons: list[SelectedExamLesson] = []
        missing_numbers: list[int] = []
        unavailable_numbers: list[int] = []
        for number in lesson_numbers:
            lesson = subject.find_lesson(number)
            if lesson is None:
                missing_numbers.append(number)
            elif not lesson.is_available:
                unavailable_numbers.append(number)
            else:
                lessons.append(lesson)
        if missing_numbers:
            label = ", ".join(str(number) for number in missing_numbers)
            raise ValueError(f"기출엄선 {subject.name}에 없는 차시입니다: {label}")
        if unavailable_numbers:
            label = ", ".join(str(number) for number in unavailable_numbers)
            raise ValueError(
                f"기출엄선 {subject.name}에 아직 PDF가 등록되지 않은 차시입니다: {label}"
            )

        return self.make_selected_exam_block_for_lessons(
            subject,
            tuple(lessons),
            copies,
            selected_numbers=selected_numbers or None,
            bundled=bundled,
        )

    def make_textbook_block_from_editor(
        self, selected_block: PrintBlock, copies: int, bundled: bool
    ) -> PrintBlock:
        edition = self.textbook_catalog.find_edition(
            selected_block.subject, selected_block.level
        )
        if edition is None or not edition.is_verified:
            raise ValueError(
                f"{selected_block.subject} / {selected_block.level} 교과서 판본을 찾을 수 없습니다."
            )
        document_kind = selected_block.round_name
        selected_numbers = parse_file_number_list(
            self.edit_selected_numbers_var.get().strip()
        )
        if selected_numbers:
            lesson_numbers = selected_numbers
        else:
            start, end = self.selected_edit_range()
            lesson_numbers = tuple(
                lesson.number
                for lesson in edition.lessons
                if start <= lesson.number <= end
            )
        lessons: list[TextbookLesson] = []
        for number in lesson_numbers:
            lesson = edition.lesson(number)
            if lesson is None:
                raise ValueError(
                    f"{edition.subject} / {edition.publisher}에 없는 파트입니다: {number}"
                )
            if not lesson.is_available(document_kind):
                raise ValueError(
                    f"{lesson.display_label} {document_kind} 자료 누락: "
                    f"{lesson.unavailable_reason(document_kind)}"
                )
            lessons.append(lesson)
        return self.make_textbook_block_for_selection(
            edition,
            document_kind,
            tuple(lessons),
            copies,
            selected_numbers or None,
            bundled,
        )

    def make_block_from_editor(self) -> PrintBlock:
        copies = int(self.edit_copies_var.get())
        if copies < 1:
            raise ValueError("부수는 1 이상이어야 합니다.")
        selected_index = self.selected_block_index()
        selected_block = self.blocks[selected_index] if selected_index is not None else None
        name_variable = getattr(self, "edit_name_separator_var", None)
        if name_variable is None:
            name_separator_label = selected_block.name_separator_label if selected_block is not None else ""
        else:
            name_separator_label = normalize_name_separator_label(name_variable.get())

        def finalize(block: PrintBlock) -> PrintBlock:
            block.name_separator_label = name_separator_label
            return block

        baseline = getattr(self, "editor_baseline", None)
        if (
            selected_block is not None
            and selected_block is getattr(self, "editor_block", None)
            and baseline is not None
            and self.queue_editor_values()[:6] == baseline[:6]
        ):
            # Metadata-only saves must not rebuild a selected textbook/APEX
            # range PDF or discard its existing routing/composition metadata.
            fixed_form = selected_block.source in FIXED_FORM_SOURCES
            route, override = selected_block.printer_route, selected_block.printer_override
            if selected_block.source == "custom" and selected_block.material_kind != "queue_bundle":
                route, override = self.resolve_block_printer_route(self.edit_printer_route_var.get())
            return replace(
                selected_block,
                files=list(selected_block.files),
                copies=copies,
                bundled=False if fixed_form else (
                    True if selected_block.material_kind == "queue_bundle" else self.edit_bundle_var.get()
                ),
                name_separator_label="" if fixed_form else name_separator_label,
                printer_route=route,
                printer_override=override,
                history_id=str(uuid.uuid4()),
                replaces_block_id="",
            )

        if selected_block is not None and selected_block.source in FIXED_FORM_SOURCES:
            fixed_route = "가로" if selected_block.source == "omr" else "세로"
            printer_route, printer_override = self.resolve_block_printer_route(fixed_route)
            return PrintBlock(
                subject=selected_block.subject,
                level=selected_block.level,
                round_name=selected_block.round_name,
                start=1,
                end=1,
                copies=copies,
                files=list(selected_block.files),
                bundled=False,
                source=selected_block.source,
                printer_override=printer_override,
                display_label=selected_block.display_label,
                printer_route=printer_route,
            )
        if selected_block is not None and selected_block.source in TEST_KIND_BY_SOURCE:
            selected_numbers, files = self.current_edit_files()
            if selected_numbers:
                start, end = min(selected_numbers), max(selected_numbers)
                selection_label = "(" + ",".join(str(number) for number in selected_numbers) + ")"
            else:
                start, end = self.selected_edit_range()
                selection_label = f"{start:02d}~{end:02d}"
            if not files:
                raise ValueError("선택된 범위에 시험지 파일이 없습니다.")
            kind = TEST_KIND_BY_SOURCE[selected_block.source]
            printer_route, printer_override = self.resolve_block_printer_route("세로")
            return finalize(PrintBlock(
                subject=self.edit_subject_var.get(),
                level=self.edit_level_var.get(),
                round_name=kind,
                start=start,
                end=end,
                copies=copies,
                files=files,
                bundled=self.edit_bundle_var.get(),
                source=selected_block.source,
                printer_override=printer_override,
                display_label=(
                    f"{kind} / {self.edit_subject_var.get()} / "
                    f"{self.edit_level_var.get()} / {selection_label}"
                ),
                printer_route=printer_route,
                selected_numbers=selected_numbers,
            ))
        if selected_block is not None and selected_block.source.startswith("material_"):
            if selected_block.source == "material_apex":
                return finalize(self.make_apex_block_from_editor(
                    selected_block,
                    copies,
                    self.edit_bundle_var.get(),
                ))
            if selected_block.source == "material_able":
                return finalize(self.make_able_block_from_editor(
                    selected_block,
                    copies,
                    self.edit_bundle_var.get(),
                ))
            if selected_block.source == "material_training_pack":
                return finalize(self.make_training_pack_block_from_editor(
                    selected_block,
                    copies,
                    self.edit_bundle_var.get(),
                ))
            if selected_block.source == "material_frequent_type":
                return finalize(self.make_frequent_type_block_from_editor(
                    selected_block,
                    copies,
                    self.edit_bundle_var.get(),
                ))
            if selected_block.source == "material_selected_exam":
                return finalize(self.make_selected_exam_block_from_editor(
                    selected_block,
                    copies,
                    self.edit_bundle_var.get(),
                ))
            if selected_block.source == "material_textbook":
                return finalize(self.make_textbook_block_from_editor(
                    selected_block,
                    copies,
                    self.edit_bundle_var.get(),
                ))
            if selected_block.source == "material_textbook_variant":
                edition = self.textbook_catalog.find_edition(
                    selected_block.subject, selected_block.level
                )
                if edition is None:
                    raise ValueError("핵심문제 변형 판본을 찾을 수 없습니다.")
                selected = parse_file_number_list(self.edit_selected_numbers_var.get().strip())
                if selected:
                    numbers = selected
                else:
                    start, end = self.selected_edit_range()
                    numbers = tuple(range(min(start, end), max(start, end) + 1))
                return finalize(self.make_textbook_variant_block_for_selection(
                    edition, numbers, copies, selected or None, self.edit_bundle_var.get()
                ))
            fixed_route = "A:ble" if selected_block.source == "material_able" else "세로"
            printer_route, printer_override = self.resolve_block_printer_route(fixed_route)
            return finalize(PrintBlock(
                subject=selected_block.subject,
                level=selected_block.level,
                round_name=selected_block.round_name,
                start=selected_block.start,
                end=selected_block.end,
                copies=copies,
                files=list(selected_block.files),
                bundled=self.edit_bundle_var.get(),
                source=selected_block.source,
                printer_override=printer_override,
                display_label=selected_block.display_label,
                pages_per_sheet=selected_block.pages_per_sheet,
                printer_route=printer_route,
                selected_numbers=selected_block.selected_numbers,
            ))
        if selected_block is not None and selected_block.material_kind == "queue_bundle":
            return finalize(PrintBlock(
                subject=selected_block.subject,
                level=selected_block.level,
                round_name=selected_block.round_name,
                start=1,
                end=len(selected_block.files),
                copies=copies,
                files=list(selected_block.files),
                bundled=True,
                source="custom",
                printer_override=selected_block.printer_override,
                display_label=selected_block.display_label,
                printer_route=selected_block.printer_route,
                material_kind="queue_bundle",
            ))
        selected_numbers, files = self.current_edit_files()
        if selected_numbers:
            start, end = min(selected_numbers), max(selected_numbers)
        else:
            start, end = self.selected_edit_range()
        if not files:
            raise ValueError("선택된 범위에 시험지 파일이 없습니다.")
        if selected_block is not None and selected_block.source == "custom":
            route_var = getattr(self, "edit_printer_route_var", None)
            route_value = route_var.get() if route_var is not None else "자동 감지"
            printer_route, printer_override = self.resolve_block_printer_route(route_value)
            custom_files = [
                ExamPdf(index, item.name, item.path)
                for index, item in enumerate(files, start=1)
            ]
            return finalize(PrintBlock(
                subject="새 PDF",
                level="",
                round_name="직접",
                start=1,
                end=len(custom_files),
                copies=copies,
                files=custom_files,
                bundled=self.edit_bundle_var.get(),
                source="custom",
                printer_override=printer_override,
                printer_route=printer_route,
            ))
        printer_route, printer_override = self.resolve_block_printer_route("세로")
        return finalize(PrintBlock(
            subject=self.edit_subject_var.get(),
            level=self.edit_level_var.get(),
            round_name=self.edit_round_var.get(),
            start=start,
            end=end,
            copies=copies,
            files=files,
            bundled=self.edit_bundle_var.get(),
            printer_override=printer_override,
            printer_route=printer_route,
            selected_numbers=selected_numbers,
        ))

    def update_builder_status(self) -> None:
        try:
            selected_numbers, files = self.current_form_files()
            copies = int(self.block_copies_var.get())
            selection_label = ""
            if selected_numbers:
                selection_label = " (" + ",".join(str(number) for number in selected_numbers) + ")"
            if self.bundle_var.get():
                self.builder_status_var.set(f"{len(files)}개{selection_label}, {copies}묶음")
            else:
                self.builder_status_var.set(f"{len(files)}개{selection_label}, 각 {copies}부")
        except ValueError as exc:
            self.builder_status_var.set(str(exc))
        except Exception:
            self.builder_status_var.set("범위 선택")

    def queue_snapshot(self) -> QueueSnapshot:
        return QueueSnapshot(
            tuple(self.blocks), dict(self.block_status_by_id), dict(self.block_detail_by_id),
            self.selected_block_index_value, getattr(self, "retry_batch", None),
            tuple(sorted(self.selected_block_indices)), getattr(self, "queue_selection_anchor", None),
        )

    def push_undo_state(self, label: str) -> None:
        if self.is_printing:
            return
        self.undo_stack.append((label, self.queue_snapshot()))
        self.redo_stack = []
        if len(self.undo_stack) > 50:
            self.undo_stack.pop(0)

    def undo_last_action(self) -> str:
        if self.is_printing:
            messagebox.showwarning(APP_TITLE, "인쇄 중에는 실행 큐 보호를 위해 되돌리기를 사용할 수 없습니다.")
            return "break"
        able_pending = getattr(self, "able_build_pending", False)
        order_pending = getattr(self, "order_build_pending", False)
        if able_pending or order_pending or getattr(self, "group_build_pending", False):
            self.cancel_pending_block_preparations()
            if able_pending:
                self.refresh_able_status()
            if order_pending and self.order_csv_path is not None:
                self.refresh_order_csv_list()
            self.queue_status_var.set("준비 중이던 블록 작업을 취소했습니다.")
        if not self.undo_stack:
            self.queue_status_var.set("되돌릴 작업이 없습니다.")
            return "break"
        label, snapshot = self.undo_stack.pop()
        if not hasattr(self, "redo_stack"):
            self.redo_stack = []
        self.redo_stack.append((label, self.queue_snapshot()))
        self.restore_queue_snapshot(snapshot)
        self.log(f"되돌리기: {label}")
        return "break"

    def redo_last_action(self) -> str:
        if self.is_printing:
            messagebox.showwarning(APP_TITLE, "인쇄 중에는 실행 큐 보호를 위해 다시 실행할 수 없습니다.")
            return "break"
        if (getattr(self, "able_build_pending", False)
            or getattr(self, "order_build_pending", False)
            or getattr(self, "group_build_pending", False)):
            self.queue_status_var.set("자료 준비가 끝난 뒤 다시 실행해 주세요.")
            return "break"
        if not getattr(self, "redo_stack", None):
            self.queue_status_var.set("다시 실행할 작업이 없습니다.")
            return "break"
        label, snapshot = self.redo_stack.pop()
        self.undo_stack.append((label, self.queue_snapshot()))
        self.restore_queue_snapshot(snapshot)
        self.log(f"다시 실행: {label}")
        return "break"

    def restore_queue_snapshot(self, snapshot: QueueSnapshot) -> None:
        self.blocks = list(snapshot.blocks)
        self.block_status_by_id = dict(snapshot.statuses)
        self.block_detail_by_id = dict(snapshot.details)
        self.retry_batch = snapshot.retry_batch
        self.sync_retry_queue()
        selected_index = snapshot.selected_index
        self.selected_block_index_value = selected_index if selected_index is not None and 0 <= selected_index < len(self.blocks) else None
        self.selected_block_indices = (
            {index for index in snapshot.selected_indices if 0 <= index < len(self.blocks)}
            if snapshot.selected_indices is not None else
            ({self.selected_block_index_value} if self.selected_block_index_value is not None else set())
        )
        self.queue_selection_anchor = snapshot.selection_anchor if snapshot.selection_anchor in range(len(self.blocks)) else self.selected_block_index_value
        self.update_queue_view()
        self.update_preview()

    def populate_queue_editor(self, index: int | None = None, *, discard_draft: bool = False) -> None:
        """Load a queue block without turning programmatic updates into edits."""
        if index is None:
            index = self.selected_block_index()
        block = self.blocks[index] if index is not None and 0 <= index < len(self.blocks) else None
        if (
            not discard_draft
            and block is not None
            and block is getattr(self, "editor_block", None)
            and self.keyboard_state.editor_dirty
        ):
            self.refresh_editor_status()
            return
        self.editor_syncing = True
        try:
            self._populate_queue_editor(index)
            self.editor_block = block
            self.editor_baseline = self.queue_editor_values()
        finally:
            self.editor_syncing = False
        self.keyboard_state.editor_dirty = False
        self.refresh_editor_status()

    def _populate_queue_editor(self, index: int | None = None) -> None:
        if not hasattr(self, "edit_subject_combo"):
            return
        if index is None:
            index = self.selected_block_index()
        if index is None or not 0 <= index < len(self.blocks):
            self.queue_edit_status_label.configure(text="선택 없음")
            self.edit_bundle_button.configure(state="normal")
            for combo in (
                self.edit_subject_combo, self.edit_level_combo, self.edit_round_combo,
                self.edit_start_combo, self.edit_end_combo,
            ):
                combo.configure(state="readonly")
            self.edit_level_label.configure(text="레벨")
            self.edit_round_label.configure(text="회차")
            self.edit_name_separator_var.set("")
            self.edit_printer_route_combo.pack_forget()
            self.edit_printer_route_label.pack_forget()
            self.refresh_name_separator_editor_state()
            return
        block = self.blocks[index]
        locked_bundle = block.material_kind == "queue_bundle"
        self.edit_bundle_button.configure(state="disabled" if locked_bundle else "normal")
        for combo in (
            self.edit_subject_combo, self.edit_level_combo, self.edit_round_combo,
            self.edit_start_combo, self.edit_end_combo,
        ):
            combo.configure(state="disabled" if locked_bundle else "readonly")
        self.edit_level_label.configure(
            text=(
                test_filter_label(TEST_KIND_BY_SOURCE[block.source])
                if block.source in TEST_KIND_BY_SOURCE
                else ("출판사" if block.source in {"material_textbook", "material_textbook_variant"} else "레벨")
            )
        )
        self.edit_round_label.configure(
            text="문서" if block.source in {"material_textbook", "material_textbook_variant"} else "회차"
        )
        self.queue_edit_status_label.configure(text=f"#{index + 1}")
        self.edit_name_separator_var.set(block.name_separator_label)
        self.refresh_name_separator_editor_state()
        is_lesson_material = block.source in {
            "material_apex",
            "material_able",
            "material_training_pack",
            "material_frequent_type",
            "material_selected_exam",
            "material_textbook",
            "material_textbook_variant",
        }
        self.edit_selected_numbers_label.configure(
            text=(
                "개별 파트"
                if block.source in {"material_textbook", "material_textbook_variant"}
                else ("개별 차시" if is_lesson_material else "개별 파일")
            )
        )
        self.edit_selected_numbers_hint_label.configure(
            text=(
                "예: 1-3, 6, 7-9 (공백 무관) · 지정 파트만 선택"
                if block.source in {"material_textbook", "material_textbook_variant"}
                else "예: 1-3, 6, 7-9 (공백 무관) · 지정 차시만 선택"
                if is_lesson_material
                else "예: 1-3, 6, 7-9 (공백 무관) · 지정 파일만 선택"
            )
        )
        if block.source == "custom" and not locked_bundle:
            printer_route = normalize_printer_route(block.printer_route)
            if printer_route == "자동 감지" and block.printer_override:
                printer_var = getattr(self, "printer_var", None)
                base_printer = printer_var.get() if printer_var is not None else DEFAULT_PRINTER
                printer_route = infer_printer_route(base_printer, block.printer_override)
            self.edit_printer_route_var.set(printer_route)
            self.edit_printer_route_label.pack(side="right", padx=(0, 6))
            self.edit_printer_route_combo.pack(side="right", padx=(0, 8))
        else:
            self.edit_printer_route_combo.pack_forget()
            self.edit_printer_route_label.pack_forget()
        if block.source in TEST_KIND_BY_SOURCE:
            kind = TEST_KIND_BY_SOURCE[block.source]
            self.edit_selected_numbers_var.set(
                ",".join(str(number) for number in (block.selected_numbers or ()))
            )
            self.edit_subject_combo["values"] = [block.subject]
            self.edit_level_combo["values"] = [block.level]
            self.edit_round_combo["values"] = [kind]
            self.edit_subject_var.set(block.subject)
            self.edit_level_var.set(block.level)
            self.edit_round_var.set(kind)
            full_files = self.test_catalog_exam_files(kind, block.subject, block.level)
            self.edit_exam_files = full_files or list(block.files)
            values = [item.label for item in self.edit_exam_files]
            labels_by_number = {item.number: item.label for item in self.edit_exam_files}
            self.edit_start_combo["values"] = values
            self.edit_end_combo["values"] = values
            self.edit_start_var.set(
                labels_by_number.get(block.start, values[0] if values else "")
            )
            self.edit_end_var.set(
                labels_by_number.get(block.end, values[-1] if values else "")
            )
            self.edit_copies_var.set(block.copies)
            self.edit_bundle_var.set(block.bundled)
            self.refresh_toggle_buttons()
            return
        if block.source in {"material_textbook", "material_textbook_variant"}:
            self.edit_selected_numbers_var.set(
                ",".join(str(number) for number in (block.selected_numbers or ()))
            )
            self.edit_subject_combo["values"] = [block.subject]
            self.edit_level_combo["values"] = [block.level]
            self.edit_round_combo["values"] = [block.round_name]
            self.edit_subject_var.set(block.subject)
            self.edit_level_var.set(block.level)
            self.edit_round_var.set(block.round_name)
            edition = self.textbook_catalog.find_edition(block.subject, block.level)
            if block.source == "material_textbook_variant":
                lessons = edition.available_lessons("핵심문제") if edition is not None else ()
                asset_path = edition.variant.path if edition is not None and edition.variant else block.files[0].path
                self.edit_exam_files = [
                    ExamPdf(lesson.number, lesson.display_label, asset_path)
                    for lesson in lessons
                ]
            else:
                lessons = (
                    edition.available_lessons(block.round_name)
                    if edition is not None and edition.is_verified else ()
                )
                asset_path = (
                    edition.asset(block.round_name).path if edition is not None else block.files[0].path
                )
                self.edit_exam_files = [
                    ExamPdf(lesson.number, lesson.display_label, asset_path)
                    for lesson in lessons
                ]
            values = [item.label for item in self.edit_exam_files]
            labels_by_number = {item.number: item.label for item in self.edit_exam_files}
            self.edit_start_combo["values"] = values
            self.edit_end_combo["values"] = values
            self.edit_start_var.set(
                labels_by_number.get(block.start, values[0] if values else "")
            )
            self.edit_end_var.set(
                labels_by_number.get(block.end, values[-1] if values else "")
            )
            self.edit_copies_var.set(block.copies)
            self.edit_bundle_var.set(block.bundled)
            self.refresh_toggle_buttons()
            return
        if block.source == "material_selected_exam":
            self.edit_selected_numbers_var.set(
                ",".join(str(number) for number in (block.selected_numbers or ()))
            )
            self.edit_subject_combo["values"] = [block.subject]
            self.edit_level_combo["values"] = [block.level]
            self.edit_round_combo["values"] = [block.round_name]
            self.edit_subject_var.set(block.subject)
            self.edit_level_var.set(block.level)
            self.edit_round_var.set(block.round_name)
            subject = self.selected_exam_material_catalog.find_subject(block.subject)
            lessons = (
                tuple(lesson for lesson in subject.lessons if lesson.is_available)
                if subject is not None
                else ()
            )
            self.edit_exam_files = [
                ExamPdf(
                    number=lesson.number,
                    name=lesson.label,
                    path=lesson.path,
                )
                for lesson in lessons
            ] or list(block.files)
            values = [item.label for item in self.edit_exam_files]
            labels_by_number = {item.number: item.label for item in self.edit_exam_files}
            self.edit_start_combo["values"] = values
            self.edit_end_combo["values"] = values
            self.edit_start_var.set(
                labels_by_number.get(block.start, values[0] if values else "")
            )
            self.edit_end_var.set(
                labels_by_number.get(block.end, values[-1] if values else "")
            )
            self.edit_copies_var.set(block.copies)
            self.edit_bundle_var.set(block.bundled)
            self.refresh_toggle_buttons()
            return
        if block.source != "catalog":
            self.edit_selected_numbers_var.set(
                ",".join(str(number) for number in (block.selected_numbers or ()))
                if is_lesson_material
                else ""
            )
            subject_label = block.subject if block.source.startswith("material_") else "새 PDF"
            level_label = block.level if block.source.startswith("material_") else "직접"
            round_label = block.round_name if block.source.startswith("material_") else "파일"
            self.edit_subject_combo["values"] = [subject_label]
            self.edit_level_combo["values"] = [level_label]
            self.edit_round_combo["values"] = [round_label]
            self.edit_subject_var.set(subject_label)
            self.edit_level_var.set(level_label)
            self.edit_round_var.set(round_label)
            self.edit_exam_files = list(block.files)
            values = [item.label for item in self.edit_exam_files]
            self.edit_start_combo["values"] = values
            self.edit_end_combo["values"] = values
            if values:
                self.edit_start_var.set(values[max(0, block.start - 1)] if block.start <= len(values) else values[0])
                self.edit_end_var.set(values[max(0, block.end - 1)] if block.end <= len(values) else values[-1])
            else:
                self.edit_start_var.set("")
                self.edit_end_var.set("")
            self.edit_copies_var.set(block.copies)
            self.edit_bundle_var.set(block.bundled)
            self.refresh_toggle_buttons()
            return
        self.edit_subject_var.set(block.subject)
        self.edit_level_combo["values"] = (
            catalog_levels(self.catalog, block.subject)
            if self.catalog else scan_levels(self.root_path, block.subject)
        )
        self.edit_level_var.set(block.level)
        self.edit_round_combo["values"] = (
            catalog_rounds(self.catalog, block.subject, block.level)
            if self.catalog else scan_rounds(self.root_path, block.subject, block.level)
        )
        self.edit_round_var.set(block.round_name)
        self.refresh_edit_files()
        self.edit_selected_numbers_label.configure(text="개별 파일")
        self.edit_selected_numbers_hint_label.configure(
            text="예: 1-3, 6, 7-9 (공백 무관) · 지정 파일만 선택"
        )
        self.edit_start_var.set(self.edit_label_for_number(block.start))
        self.edit_end_var.set(self.edit_label_for_number(block.end))
        self.edit_selected_numbers_var.set(",".join(str(number) for number in (block.selected_numbers or ())))
        self.edit_copies_var.set(block.copies)
        self.edit_bundle_var.set(block.bundled)
        self.refresh_toggle_buttons()

    def add_current_block(self) -> bool:
        captured_copies = self.block_copies_var.get()
        captured_numbers = self.unit_selected_numbers_var.get()
        captured_name = self.new_block_name_var.get()
        try:
            block = self.apply_new_block_name(self.make_block_from_form())
        except Exception as exc:
            messagebox.showwarning(APP_TITLE, str(exc))
            return False
        token = self.reserve_block_commit()

        def commit() -> None:
            self.push_undo_state("블록 추가")
            self.blocks.append(block)
            self.block_status_by_id[id(block)] = "대기"
            self.update_queue_view(select_index=len(self.blocks) - 1)
            self.update_preview()
            if self.block_copies_var.get() == captured_copies:
                self.block_copies_var.set(1)
            if self.unit_selected_numbers_var.get() == captured_numbers:
                self.unit_selected_numbers_var.set("")
            if self.new_block_name_var.get() == captured_name:
                self.clear_new_block_name()
            self.update_builder_status()
            self.log(f"블록 추가: {block.summary}")

        self.complete_block_commit(token, commit)
        return True

    def bind_queue_mousewheel(self, _event: tk.Event) -> None:
        self.queue_canvas.bind_all("<MouseWheel>", self.on_queue_mousewheel)

    def unbind_queue_mousewheel(self, _event: tk.Event) -> None:
        self.queue_canvas.unbind_all("<MouseWheel>")

    def on_queue_mousewheel(self, event: tk.Event) -> None:
        delta = -1 if event.delta > 0 else 1
        self.queue_canvas.yview_scroll(delta * 3, "units")
        return "break"

    def selected_block_index(self) -> int | None:
        index = self.selected_block_index_value
        if index is None:
            return None
        if not 0 <= index < len(self.blocks):
            return None
        return index

    def select_queue_index(self, index: int, *, load_form: bool = False) -> bool:
        if not 0 <= index < len(self.blocks):
            return False
        return self.select_queue_indices({index}, focus_index=index, anchor_index=index, load_form=load_form)

    def commit_selected_block_replacement(self, block: PrintBlock, source: str) -> bool:
        index = self.selected_block_index()
        if index is None:
            messagebox.showwarning(APP_TITLE, "수정할 블록을 작업 큐에서 선택해 주세요.")
            return False
        old_block = self.blocks[index]
        if self.block_is_scheduled_for_print(old_block):
            messagebox.showwarning(
                APP_TITLE,
                "이미 실행 큐에 등록된 블록은 인쇄 순서가 꼬일 수 있어 수정할 수 없습니다.\n"
                "수정본은 새 블록으로 추가한 뒤 [전체 큐 인쇄 시작 / 추가]를 눌러 주세요.",
            )
            return False
        if source == "왼쪽 폼":
            block.name_separator_label = old_block.name_separator_label
        retry = getattr(self, "retry_batch", None)
        remaining = sum(job.block_uid == id(old_block) for job in retry.jobs) if retry else 0
        if remaining and not messagebox.askyesno(
            APP_TITLE,
            f"이 블록의 남은 재시도 {remaining}건을 제거하고 수정한 블록 전체를 새 대기로 등록할까요?\n"
            "이미 전송한 부분도 수정본에 포함되면 다시 인쇄됩니다.",
        ):
            return False
        block.replaces_block_id = old_block.history_id
        block.order_id = old_block.order_id
        if [item.path for item in block.files] == [item.path for item in old_block.files]:
            block.material_id = old_block.material_id
        self.push_undo_state("블록 수정")
        self.block_status_by_id.pop(id(old_block), None)
        self.block_detail_by_id.pop(id(old_block), None)
        self.blocks[index] = block
        self.block_status_by_id[id(block)] = "대기"
        self.sync_retry_queue()
        self.update_queue_view(
            select_index=None if getattr(self, "editor_save_in_progress", False) else index
        )
        self.update_preview()
        self.log(f"블록 수정 저장({source}): {index + 1}. {block.summary}")
        return True

    def replace_selected_block(self) -> None:
        try:
            block = self.make_block_from_form()
        except Exception as exc:
            messagebox.showwarning(APP_TITLE, str(exc))
            return
        self.commit_selected_block_replacement(block, "왼쪽 폼")

    def replace_selected_block_from_editor(self) -> bool:
        try:
            block = self.make_block_from_editor()
        except Exception as exc:
            messagebox.showwarning(APP_TITLE, str(exc))
            return False
        return self.commit_selected_block_replacement(block, "오른쪽 편집")

    def load_selected_block_to_form(self) -> None:
        index = self.selected_block_index()
        if index is None:
            return
        block = self.blocks[index]
        if block.material_kind == "queue_bundle":
            self.populate_queue_editor(index)
            return
        if block.source in TEST_KIND_BY_SOURCE:
            kind = TEST_KIND_BY_SOURCE[block.source]
            variables = self.test_builder_vars[kind]
            variables["subject"].set(block.subject)
            variables["level"].set(block.level)
            self.refresh_test_catalog_files(kind)
            labels_by_number = {
                item.number: item.label for item in self.test_exam_files.get(kind, [])
            }
            variables["start"].set(labels_by_number.get(block.start, ""))
            variables["end"].set(labels_by_number.get(block.end, ""))
            variables["selected"].set(
                ",".join(str(number) for number in (block.selected_numbers or ()))
            )
            variables["copies"].set(block.copies)
            variables["bundle"].set(block.bundled)
            self.update_test_catalog_status(kind)
            self.refresh_toggle_buttons()
            self.populate_queue_editor(index)
            return
        self.subject_var.set(block.subject)
        self.level_combo["values"] = (
            catalog_levels(self.catalog, block.subject)
            if self.catalog else scan_levels(self.root_path, block.subject)
        )
        self.level_var.set(block.level)
        self.round_combo["values"] = (
            catalog_rounds(self.catalog, block.subject, block.level)
            if self.catalog else scan_rounds(self.root_path, block.subject, block.level)
        )
        self.round_var.set(block.round_name)
        self.refresh_files()
        self.start_var.set(self.label_for_number(block.start))
        self.end_var.set(self.label_for_number(block.end))
        self.unit_selected_numbers_var.set(",".join(str(number) for number in (block.selected_numbers or ())))
        self.block_copies_var.set(block.copies)
        self.bundle_var.set(block.bundled)
        self.update_builder_status()
        self.refresh_toggle_buttons()
        self.populate_queue_editor(index)

    def move_selected_block(self, delta: int) -> None:
        indices = sorted(self.selected_block_indices)
        if not indices:
            return
        if any(self.block_is_scheduled_for_print(self.blocks[index]) for index in indices):
            self.queue_status_var.set("실행 큐에 등록된 블록은 순서 변경 불가")
            return
        if delta < 0 and indices[0] == 0:
            return
        if delta > 0 and indices[-1] == len(self.blocks) - 1:
            return
        focused = self.selected_block_index()
        focused_block = self.blocks[focused] if focused is not None else None
        anchor = getattr(self, "queue_selection_anchor", None)
        anchor_block = self.blocks[anchor] if anchor is not None and 0 <= anchor < len(self.blocks) else None
        self.push_undo_state("블록 순서 변경")
        if delta < 0:
            for index in indices:
                self.blocks[index - 1], self.blocks[index] = self.blocks[index], self.blocks[index - 1]
            moved_indices = {index - 1 for index in indices}
        else:
            for index in reversed(indices):
                self.blocks[index + 1], self.blocks[index] = self.blocks[index], self.blocks[index + 1]
            moved_indices = {index + 1 for index in indices}
        focus = next((index for index, block in enumerate(self.blocks) if block is focused_block), min(moved_indices))
        self.selected_block_indices = moved_indices
        self.selected_block_index_value = focus
        self.queue_selection_anchor = next((index for index, block in enumerate(self.blocks) if block is anchor_block), focus)
        self.update_queue_view()
        self.update_preview()

    def sort_blocks_by_name(self) -> None:
        if self.is_printing or getattr(self, "retry_batch", None) is not None:
            messagebox.showwarning(APP_TITLE, "인쇄 중이거나 재시도할 작업이 있으면 순서를 변경할 수 없습니다.")
            return
        if len(self.blocks) < 2:
            return
        if not self.save_pending_queue_editor():
            return
        sorted_blocks = sorted(self.blocks, key=lambda block: name_separator_order_key(block.name_separator_label))
        if all(before is after for before, after in zip(self.blocks, sorted_blocks)):
            self.queue_status_var.set("이미 이름 가나다순입니다.")
            return
        selected_ids = {
            id(self.blocks[index]) for index in self.selected_block_indices
            if 0 <= index < len(self.blocks)
        }
        focused = self.selected_block_index()
        focused_id = id(self.blocks[focused]) if focused is not None else None
        self.push_undo_state("이름 가나다 정렬")
        self.blocks[:] = sorted_blocks
        self.selected_block_indices = {
            index for index, block in enumerate(self.blocks) if id(block) in selected_ids
        }
        self.selected_block_index_value = next(
            (index for index, block in enumerate(self.blocks) if id(block) == focused_id),
            min(self.selected_block_indices) if self.selected_block_indices else None,
        )
        self.queue_selection_anchor = self.selected_block_index_value
        self.update_queue_view()
        self.update_preview()
        self.log("작업 큐를 이름 가나다순으로 정렬했습니다.")

    def reorder_blocks(
        self,
        indices: set[int] | tuple[int, ...],
        drop_index: int,
        *,
        focus_index: int | None = None,
    ) -> QueueReorderResult:
        valid_indices = tuple(sorted(index for index in indices if 0 <= index < len(self.blocks)))
        # Printing may start while a mouse drag is held. Recheck at commit time.
        if any(self.block_is_scheduled_for_print(self.blocks[index]) for index in valid_indices):
            self.queue_status_var.set("실행 큐에 등록된 블록은 드래그 정렬 불가")
            return QueueReorderResult(
                tuple(self.blocks), valid_indices,
                focus_index if focus_index in range(len(self.blocks)) else (valid_indices[0] if valid_indices else None),
                valid_indices[0] if valid_indices else 0, False,
            )
        result = reorder_selected_items(
            self.blocks,
            indices,
            drop_index,
            focus_index=focus_index,
        )
        if result.changed:
            self.push_undo_state("블록 드래그 정렬")
            self.blocks[:] = result.items
            self.capture_queue_history()
        return result

    def reorder_block(self, start_index: int, drop_index: int) -> int:
        result = self.reorder_blocks({start_index}, drop_index, focus_index=start_index)
        return result.focus_index if result.focus_index is not None else start_index

    def delete_selected_block(self) -> None:
        indices = sorted(self.selected_block_indices)
        if not indices:
            return
        if len(indices) == 1:
            self.delete_block_at(indices[0])
            return
        selected_blocks = [self.blocks[index] for index in indices]
        can_delete, removed_jobs = self.remove_reserved_blocks_from_active_print(selected_blocks)
        if not can_delete:
            messagebox.showwarning(
                APP_TITLE,
                "진행 중이거나 이미 전송이 시작된 블록은 삭제할 수 없습니다.\n"
                "아직 예약 상태인 블록만 삭제할 수 있습니다.",
            )
            return
        self.push_undo_state("블록 여러 개 삭제")
        removed = selected_blocks
        for index in reversed(indices):
            block = self.blocks.pop(index)
            self.block_status_by_id.pop(id(block), None)
            self.block_detail_by_id.pop(id(block), None)
        self.sync_retry_queue()
        select_index = min(indices[0], len(self.blocks) - 1) if self.blocks else None
        self.update_queue_view(select_index=select_index)
        self.update_preview()
        active_note = f" · 미전송 {len(removed_jobs)}건 취소" if removed_jobs else ""
        self.log(f"블록 {len(removed)}개 삭제{active_note}")

    def delete_block_at(self, index: int) -> None:
        if not 0 <= index < len(self.blocks):
            return
        target = self.blocks[index]
        can_delete, removed_jobs = self.remove_reserved_blocks_from_active_print([target])
        if not can_delete:
            messagebox.showwarning(
                APP_TITLE,
                "진행 중이거나 이미 전송이 시작된 블록은 삭제할 수 없습니다.\n"
                "아직 예약 상태인 블록만 삭제할 수 있습니다.",
            )
            return
        self.push_undo_state("블록 삭제")
        removed = self.blocks.pop(index)
        self.block_status_by_id.pop(id(removed), None)
        self.block_detail_by_id.pop(id(removed), None)
        self.sync_retry_queue()
        select_index = min(index, len(self.blocks) - 1) if self.blocks else None
        self.update_queue_view(select_index=select_index)
        self.update_preview()
        active_note = f" · 미전송 {len(removed_jobs)}건 취소" if removed_jobs else ""
        self.log(f"블록 삭제: {removed.summary}{active_note}")

    def clear_blocks(self) -> None:
        able_pending = getattr(self, "able_build_pending", False)
        order_pending = getattr(self, "order_build_pending", False)
        pending_build = bool(
            able_pending or order_pending or getattr(self, "group_build_pending", False)
        )
        if not self.blocks and not pending_build:
            return
        prompt = (
            "작업 큐와 준비 중인 블록을 모두 취소할까요?"
            if pending_build
            else "작업 큐의 모든 블록을 삭제할까요?"
        )
        if not messagebox.askyesno(APP_TITLE, prompt):
            return
        if self.is_printing and any(self.block_is_scheduled_for_print(block) for block in self.blocks):
            messagebox.showwarning(APP_TITLE, "인쇄 중에는 실행 큐에 등록된 블록을 전체 삭제할 수 없습니다.")
            return
        if pending_build:
            self.cancel_pending_block_preparations()
            if able_pending:
                self.refresh_able_status()
            if order_pending and self.order_csv_path is not None:
                self.refresh_order_csv_list()
        self.push_undo_state("작업 큐 전체 삭제")
        self.blocks.clear()
        self.block_status_by_id.clear()
        self.block_detail_by_id.clear()
        self.sync_retry_queue()
        self.update_queue_view()
        self.update_preview()
        self.log("작업 큐를 비웠습니다.")

    def update_queue_view(self, select_index: int | None = None) -> None:
        """Refresh changed queue content, including history and aggregate totals."""
        self.capture_queue_history()
        total_files = total_sets = 0
        for block in self.blocks:
            file_count = block.file_count
            total_files += file_count
            total_sets += file_count * block.copies
        self.queue_status_var.set(f"블록 {len(self.blocks)}개 / PDF {total_files}개 / 전송 {total_sets}세트")
        self.refresh_queue_selection(select_index)
        self.schedule_queue_recovery_save()

    def queue_recovery_snapshot(self, reason: str) -> dict[str, object]:
        from unit_print_queue_recovery import snapshot_print_block

        now = datetime.now().astimezone().isoformat(timespec="milliseconds")
        entries = []
        for block in self.blocks:
            data = snapshot_print_block(block)
            state = self.block_status_by_id.get(id(block), "대기")
            risk = "unsent" if state in {"대기", "예약", "추가됨"} else "submitted" if state == "완료" else "uncertain"
            entries.append({"block": data, "state": state,
                            "detail": self.block_detail_by_id.get(id(block), ""), "risk": risk})
        return {
            "schema_version": 1, "snapshot_id": str(uuid.uuid4()), "created_at": now,
            "session_id": self.print_history.session_id,
            "session_started_at": self.history_session_started_at or now,
            "reason": reason, "queue": entries,
            "run": {"is_printing": self.is_printing, "run_id": self.history_run_id},
        }

    def schedule_queue_recovery_save(self, reason: str = "queue_changed") -> None:
        writer = getattr(self, "queue_recovery_writer", None)
        if writer is None or not self.blocks:
            return
        try:
            writer.submit_captured(self.queue_recovery_snapshot(reason))
        except Exception as error:
            self.report_queue_recovery_error(error)
            return
        if getattr(self, "recovery_poll_after_id", None) is None:
            self.recovery_poll_after_id = self.root.after(100, self.poll_queue_recovery_save)

    def poll_queue_recovery_save(self) -> None:
        self.recovery_poll_after_id = None
        writer = self.queue_recovery_writer
        error = writer.pop_error()
        if error is not None:
            self.report_queue_recovery_error(error)
        if writer.busy and not self.is_closing:
            self.recovery_poll_after_id = self.root.after(100, self.poll_queue_recovery_save)

    def report_queue_recovery_error(self, error: Exception) -> None:
        message = str(error)
        self.queue_status_var.set("큐 백업 실패 · 큐 도구에서 다시 저장해 주세요.")
        self.log(f"작업 큐 백업 실패: {message}")
        if message != getattr(self, "last_recovery_error", None) and not self.is_closing:
            self.last_recovery_error = message
            messagebox.showwarning(APP_TITLE, f"작업 큐 백업을 저장하지 못했습니다.\n\n{message}", parent=self.root)

    def save_queue_recovery(self) -> None:
        writer = getattr(self, "queue_recovery_writer", None)
        if writer is None or not self.blocks:
            self.queue_status_var.set("저장할 작업 큐가 없습니다. 이전 백업은 유지됩니다.")
            return
        try:
            writer.save_required(self.queue_recovery_snapshot("manual_save"))
        except Exception as error:
            self.report_queue_recovery_error(error)
            return
        self.last_recovery_error = None
        self.queue_status_var.set("작업 큐 저장 완료 · 큐 도구에서 수동 복구할 수 있습니다.")

    def close_queue_recovery(self) -> None:
        writer = getattr(self, "queue_recovery_writer", None)
        if writer is None:
            return
        try:
            writer.close()
        except Exception as error:
            self.report_queue_recovery_error(error)

    def show_queue_tools_menu(self) -> None:
        button = self.queue_tools_button
        menu = tk.Menu(button, tearoff=False)
        previous = getattr(self, "queue_tools_menu", None)
        if previous is not None:
            previous.destroy()
        self.queue_tools_menu = menu
        menu.add_command(label="작업 큐 저장", accelerator="Ctrl+S", command=self.save_queue_recovery)
        menu.add_command(label="작업 큐 복구…", accelerator="Ctrl+Shift+O", command=self.show_queue_recovery)
        try:
            menu.tk_popup(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())
        finally:
            menu.grab_release()

    def show_queue_recovery(self) -> None:
        if (self.is_printing or self.blocks or getattr(self, "retry_batch", None) is not None
            or any(getattr(self, name, False) for name in ("able_build_pending", "order_build_pending", "group_build_pending", "recovery_load_pending"))):
            messagebox.showinfo(APP_TITLE, "인쇄와 자료 준비를 끝내고 작업 큐를 비운 뒤 복구해 주세요.", parent=self.root)
            return
        store = self.queue_recovery_store
        try:
            candidates = store.list_candidates()
        except OSError as error:
            messagebox.showwarning(APP_TITLE, f"큐 백업 목록을 읽지 못했습니다.\n\n{error}", parent=self.root)
            return
        if not candidates:
            messagebox.showinfo(APP_TITLE, "저장된 작업 큐가 없습니다.", parent=self.root)
            return
        window = tk.Toplevel(self.root)
        window.title("작업 큐 복구")
        window.transient(self.root)
        window.geometry("900x510")
        window.minsize(680, 400)
        frame = ttk.Frame(window, style="Surface.TFrame", padding=18)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(3, weight=1)
        ttk.Label(frame, text="작업 큐 복구", style="SectionTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text="미전송으로 확인된 블록만 복구합니다. 전송 기록이 있는 블록은 확인용으로 남습니다.", style="SectionHint.TLabel").grid(row=1, column=0, sticky="w", pady=(6, 12))
        candidate_labels = []
        for path in candidates:
            try:
                saved_at = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            except OSError:
                saved_at = "파일 확인 필요"
            candidate_labels.append(f"{saved_at} · {path.stem.removeprefix('session-')[:8]}")
        chooser = ttk.Combobox(frame, state="readonly", values=candidate_labels)
        chooser.current(0)
        chooser.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        table_frame = ttk.Frame(frame, style="Surface.TFrame")
        table_frame.grid(row=3, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        table = ttk.Treeview(table_frame, columns=("name", "material", "copies", "state"), show="headings", selectmode="extended")
        for key, label, width in (("name", "이름", 90), ("material", "블록", 500), ("copies", "부수", 45), ("state", "복구 상태", 160)):
            table.heading(key, text=label)
            table.column(key, width=width, stretch=key == "material")
        table.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=table.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        table.configure(yscrollcommand=scroll.set)
        table.tag_configure("held", foreground=self.colors["muted_soft"])
        status = StringVar(value="백업 확인 중…")
        korean_label(frame, textvariable=status, style="SectionHint.TLabel", wraplength=820).grid(row=4, column=0, sticky="ew", pady=10)
        buttons = ttk.Frame(frame, style="Surface.TFrame")
        buttons.grid(row=5, column=0, sticky="e")
        ttk.Button(buttons, text="닫기", style="SoftAction.TButton", command=window.destroy).pack(side="left", padx=(0, 8))
        restore_button = ttk.Button(buttons, text="선택한 미전송 블록 복구", style="Primary.TButton", state="disabled")
        restore_button.pack(side="left")
        loaded: dict[str, object] = {}
        request_revision = 0

        def restore() -> None:
            snapshot = loaded.get("snapshot")
            review = loaded.get("review")
            if snapshot is None or review is None:
                return
            indices = [int(index) for index in table.selection()]
            safe = [index for index in indices if snapshot["queue"][index]["block"]["history_id"] in review.safe_block_ids]
            if not safe:
                status.set("복구할 미전송 블록을 선택해 주세요. 확인이 필요한 블록은 제외됩니다.")
                return
            if self.restore_recovery_blocks(snapshot, review, safe):
                window.destroy()
            else:
                load()

        def load(_event: tk.Event | None = None) -> None:
            nonlocal request_revision
            request_revision += 1
            revision = request_revision
            path = candidates[chooser.current()]
            loaded.clear()
            table.delete(*table.get_children())
            restore_button.configure(state="disabled")
            status.set("백업과 전송 이력 확인 중…")
            self.recovery_load_pending = True
            epoch = self.block_preparation_epoch

            def work() -> object:
                snapshot = store.load(path)
                if snapshot is None:
                    raise ValueError("선택한 백업 파일이 없습니다.")
                with self.queue_recovery_writer.restore_guard(snapshot["session_id"]):
                    consumed = store.consumed_block_ids(snapshot["session_id"])
                    return snapshot, classify_recovery_queue(snapshot, HISTORY_DIRECTORY, consumed_block_ids=consumed)

            def finish(future: object) -> None:
                if revision != request_revision:
                    return
                self.recovery_load_pending = False
                if not window.winfo_exists():
                    return
                try:
                    snapshot, review = future.result()
                except Exception as error:
                    status.set(f"복구할 수 없습니다: {error}")
                    return
                loaded.update(snapshot=snapshot, review=review)
                safe_rows = []
                for index, entry in enumerate(snapshot["queue"]):
                    data = entry["block"]
                    safe = data["history_id"] in review.safe_block_ids
                    label = "미전송 · 복구 가능" if safe else "확인 필요 · 복구 보류"
                    material = data["display_label"] or " / ".join((data["subject"], data["level"], data["round_name"]))
                    numbers = data["selected_numbers"]
                    material += " / " + (",".join(str(number) for number in numbers) if numbers else f"{data['start']}~{data['end']}")
                    table.insert("", "end", iid=str(index), values=(data["name_separator_label"], material, data["copies"], label), tags=() if safe else ("held",))
                    if safe:
                        safe_rows.append(str(index))
                table.selection_set(safe_rows)
                status.set(f"저장 시각 {snapshot['created_at']} · 복구 가능 {len(safe_rows)}개 · 확인 필요 {len(snapshot['queue']) - len(safe_rows)}개" + (f"\n{review.warning}" if review.warning else ""))
                restore_button.configure(state="normal" if safe_rows else "disabled")

            try:
                self.submit_background_block_build(work, finish, epoch=epoch)
            except Exception as error:
                self.recovery_load_pending = False
                status.set(f"복구할 수 없습니다: {error}")

        restore_button.configure(command=restore)
        chooser.bind("<<ComboboxSelected>>", load)
        window.bind("<Escape>", lambda _event: window.destroy())
        window.grab_set()
        load()

    def restore_recovery_blocks(self, snapshot: dict, review: object, indices: list[int]) -> bool:
        if (self.is_printing or self.blocks or getattr(self, "retry_batch", None) is not None
            or any(getattr(self, name, False) for name in ("able_build_pending", "order_build_pending", "group_build_pending", "recovery_load_pending"))):
            return False
        try:
            with self.queue_recovery_writer.restore_guard(snapshot["session_id"]):
                path = self.queue_recovery_store.directory / f"session-{snapshot['session_id']}.json"
                current = self.queue_recovery_store.load(path)
                if current != snapshot:
                    raise ValueError("확인 중 백업이 바뀌었습니다. 백업을 다시 선택해 주세요.")
                # The list is only a preview: dispatch evidence can change
                # while it is open. Never authorize recovery from that old review.
                consumed = self.queue_recovery_store.consumed_block_ids(snapshot["session_id"])
                review = classify_recovery_queue(snapshot, HISTORY_DIRECTORY, consumed_block_ids=consumed)
                if any(type(index) is not int or not 0 <= index < len(snapshot["queue"]) for index in indices):
                    raise ValueError("복구할 블록 선택이 올바르지 않습니다.")
                if not indices or any(snapshot["queue"][index]["block"]["history_id"] not in review.safe_block_ids for index in indices):
                    raise ValueError("선택한 블록의 전송·복구 상태가 바뀌었거나 복구할 블록이 없습니다. 목록을 다시 확인해 주세요.")
                return self._commit_recovery_blocks(snapshot, review, indices)
        except Exception as error:
            messagebox.showwarning(APP_TITLE, f"복구를 적용하지 않았습니다.\n\n{error}", parent=self.root)
            return False

    def _commit_recovery_blocks(self, snapshot: dict, review: object, indices: list[int]) -> bool:
        selected = [snapshot["queue"][index] for index in sorted(set(indices))]
        if not selected or any(entry["block"]["history_id"] not in review.safe_block_ids for entry in selected):
            return False
        restored = []
        try:
            for entry in selected:
                data = dict(entry["block"])
                data["files"] = [ExamPdf(**{**item, "path": Path(item["path"])}) for item in data["files"]]
                data["selected_numbers"] = tuple(data["selected_numbers"]) if data["selected_numbers"] is not None else None
                data["replaces_block_id"] = data["history_id"]
                data["history_id"] = str(uuid.uuid4())
                restored.append(PrintBlock(**data))
            self.preflight_pdf_paths([item.path for block in restored for item in block.files])
            self.record_history("queue_recovered", required=True, source_snapshot_id=snapshot["snapshot_id"],
                                source_session_id=snapshot["session_id"],
                                source_block_ids=[entry["block"]["history_id"] for entry in selected],
                                blocks=[block_history_data(block) for block in restored])
            self.queue_recovery_store.mark_recovered(snapshot["session_id"],
                                                    [entry["block"]["history_id"] for entry in selected])
        except Exception as error:
            messagebox.showwarning(APP_TITLE, f"복구를 적용하지 않았습니다.\n\n{error}", parent=self.root)
            return False
        self.push_undo_state("작업 큐 복구")
        self.blocks.extend(restored)
        self.block_status_by_id.update({id(block): "대기" for block in restored})
        self.update_queue_view(select_index=0)
        self.update_preview()
        self.log(f"작업 큐 복구: 미전송 블록 {len(restored)}개 · 인쇄는 시작하지 않았습니다.")
        return True

    def refresh_queue_selection(
        self, select_index: int | None = None, *, update_editor: bool = True,
    ) -> None:
        """Refresh cursor/selection without treating navigation as a queue edit."""
        if select_index is not None and 0 <= select_index < len(self.blocks):
            if (
                not getattr(self, "editor_save_in_progress", False)
                and select_index != self.selected_block_index_value
                and not self.save_queue_editor_before_selection(select_index, {select_index})
            ):
                self.draw_queue()
                return
            self.selected_block_index_value = select_index
            self.selected_block_indices = {select_index}
            self.queue_selection_anchor = select_index
        elif not self.blocks:
            self.selected_block_index_value = None
            self.selected_block_indices = set()
            self.queue_selection_anchor = None
        elif self.selected_block_index_value is None:
            self.selected_block_indices = {
                index for index in self.selected_block_indices if 0 <= index < len(self.blocks)
            }
            self.selected_block_index_value = min(self.selected_block_indices) if self.selected_block_indices else None
            self.queue_selection_anchor = self.selected_block_index_value
        elif self.selected_block_index_value >= len(self.blocks):
            self.selected_block_index_value = len(self.blocks) - 1
            self.selected_block_indices = {
                index for index in self.selected_block_indices if 0 <= index < len(self.blocks)
            }
            if not self.selected_block_indices:
                self.selected_block_indices = {self.selected_block_index_value}
            self.queue_selection_anchor = self.selected_block_index_value
        else:
            self.selected_block_indices = {
                index for index in self.selected_block_indices if 0 <= index < len(self.blocks)
            }

        if self.keyboard_state.zone in {KeyboardZone.QUEUE_LIST, KeyboardZone.QUEUE_EDITOR}:
            self.keyboard_state.queue_cursor = self.selected_block_index_value

        self.draw_queue()
        if update_editor:
            self.populate_queue_editor(self.selected_block_index_value)

    def on_queue_canvas_configure(self, _event: tk.Event) -> None:
        self.request_queue_redraw()

    def on_queue_canvas_scroll(self, first: str, last: str) -> None:
        """Follow all Canvas scroll operations, including keyboard and drag scroll."""
        self.queue_scrollbar.set(first, last)
        fractions = (float(first), float(last))
        if fractions != getattr(self, "queue_scroll_fractions", None):
            self.queue_scroll_fractions = fractions
            self.request_queue_redraw()

    def request_queue_redraw(self) -> None:
        """Coalesce resize/scroll notifications from the same Tk event turn."""
        if getattr(self, "is_closing", False):
            return
        if getattr(self, "queue_redraw_after_id", None) is None:
            self.queue_redraw_after_id = self.root.after_idle(self.draw_queue)

    def draw_round_rect(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        radius: float = 10,
        **kwargs,
    ) -> int:
        radius = min(radius, max(0, (x1 - x0) / 2), max(0, (y1 - y0) / 2))
        points = [
            x0 + radius,
            y0,
            x1 - radius,
            y0,
            x1,
            y0,
            x1,
            y0 + radius,
            x1,
            y1 - radius,
            x1,
            y1,
            x1 - radius,
            y1,
            x0 + radius,
            y1,
            x0,
            y1,
            x0,
            y1 - radius,
            x0,
            y0 + radius,
            x0,
            y0,
            x0 + radius,
            y0,
        ]
        return self.queue_canvas.create_polygon(points, smooth=True, splinesteps=12, **kwargs)

    def draw_queue(self) -> None:
        after_id = getattr(self, "queue_redraw_after_id", None)
        self.queue_redraw_after_id = None
        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except tk.TclError:
                pass
        colors = self.colors
        canvas = self.queue_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 420)
        visible_height = max(canvas.winfo_height(), 180)
        pad = self.queue_padding

        if not self.blocks:
            bottom = min(150, visible_height - pad)
            self.draw_round_rect(
                pad,
                pad,
                width - pad,
                max(bottom, pad + 120),
                radius=QUEUE_CARD_RADIUS,
                fill=colors["panel"],
                outline=colors["border_soft"],
                width=1,
            )
            canvas.create_text(
                pad + 18,
                pad + 34,
                text="작업 큐가 비어 있습니다.",
                anchor="w",
                fill=colors["text"],
                font=(self.ui_font_family, 14, "bold"),
            )
            canvas.create_text(
                pad + 18,
                pad + 64,
                text="왼쪽에서 범위를 고른 뒤 [추가]를 누르세요.",
                anchor="w",
                fill=colors["muted_soft"],
                font=(self.ui_font_family, 11),
            )
            canvas.configure(scrollregion=(0, 0, width, visible_height))
            return

        content_height = queue_content_height(
            len(self.blocks), card_height=self.queue_card_height,
            gap=self.queue_card_gap, padding=pad,
        )
        canvas.configure(scrollregion=(0, 0, width, max(content_height, visible_height)))
        viewport_top = float(canvas.canvasy(0))
        viewport_bottom = viewport_top + canvas.winfo_height()

        def visible(y0: float, *, lifted: bool = False) -> bool:
            return queue_card_intersects_viewport(
                y0, self.queue_card_height, viewport_top, viewport_bottom,
                lift_progress=self.drag_lift_progress, lifted=lifted,
            )

        if self.drag_active and self.drag_start_index is not None:
            drag_indices = self.current_drag_indices()
            drag_index_set = set(drag_indices)
            projection = self.current_drag_projection()
            projected_indices = {
                id(block): index for index, block in enumerate(projection.items)
            }
            for index, block in enumerate(self.blocks):
                if index in drag_index_set:
                    continue
                y0 = self.queue_card_top(projected_indices[id(block)])
                if not visible(y0):
                    continue
                self.draw_queue_card(
                    index,
                    block,
                    width,
                    y0=y0,
                )

            if self.drag_drop_index is not None:
                insertion_y = self.queue_card_top(projection.insertion_index) - self.queue_card_gap / 2
                if viewport_top - 6 <= insertion_y <= viewport_bottom + 6:
                    self.draw_queue_insert_line(projection.insertion_index, width)
            group_top = self.queue_drag_group_top()
            if group_top is not None:
                slot = self.queue_card_height + self.queue_card_gap
                for rank, index in enumerate(drag_indices):
                    y0 = group_top + rank * slot
                    if not visible(y0, lifted=True):
                        continue
                    self.draw_queue_card(
                        index,
                        self.blocks[index],
                        width,
                        y0=y0,
                        lifted=True,
                    )
            return

        indices = (
            range(len(self.blocks)) if self.drop_animation_positions
            else visible_queue_indices(
                len(self.blocks), viewport_top, viewport_bottom,
                card_height=self.queue_card_height, gap=self.queue_card_gap,
                padding=self.queue_padding,
            )
        )
        for index in indices:
            block = self.blocks[index]
            y0 = self.queue_animation_top(index, block)
            if visible(y0):
                self.draw_queue_card(index, block, width, y0=y0)



    def queue_file_line_label(self, item: ExamPdf | None) -> str:
        if item is None:
            return "선택된 파일 없음"
        label = item.label
        label = re.sub(r"\s*_?L[vV]\.?\s*\d+.*$", "", label).strip()
        label = re.sub(r"(?<=\D)\d+$", "", label).strip()
        return re.sub(r"\s+", " ", label)

    def queue_level_line_label(self, level: str) -> str:
        match = re.match(r"^\s*[lL][vV]\.?\s*(\d+)\s*$", level)
        if match:
            return f"Lv.{match.group(1)}"
        return level

    def queue_block_line(self, block: PrintBlock) -> str:
        prefix, suffix = self.queue_block_line_parts(block)
        return f"{prefix}{suffix}{self.queue_block_status(block)}"

    def queue_block_line_parts(self, block: PrintBlock) -> tuple[str, str]:
        name, title, details = self.queue_block_text_parts(block)
        return name + title + details, f"  //  {block.copies}부  //  "

    @staticmethod
    def queue_subtab_label(block: PrintBlock) -> str:
        """Resolve the operator's source tab from metadata, never a PDF name."""
        if block.source == "catalog":
            return "단원평가"
        if block.source in TEST_KIND_BY_SOURCE:
            return TEST_KIND_BY_SOURCE[block.source]
        if block.source == "material_apex":
            return "APEX Boost" if block.level == "APEX Boost" else "APEX"
        if block.source == "custom":
            return "묶음 블록" if block.material_kind == "queue_bundle" else "PDF파일"
        return {
            "material_able": "A:ble",
            "material_training_pack": "트레이닝팩",
            "material_frequent_type": "빈출유형",
            "material_gyopyeong": "교평 기출",
            "material_selected_exam": "기출엄선",
            "material_textbook": "교과서",
            "material_textbook_variant": "교과서 핵심문제 변형",
            "blank_review": "백지리뷰",
            "omr": "OMR",
        }.get(block.source, "자료")

    def queue_block_text_parts(self, block: PrintBlock) -> tuple[str, str, str]:
        """Return name, source/subject, and details without changing the block."""
        name = f"[{block.name_separator_label}] / " if block.name_separator_label else ""
        source = self.queue_subtab_label(block)
        title = source if block.subject == source else f"{source} / {block.subject}"
        details = ["[묶음]"] if block.bundled else []
        if block.source == "catalog":
            details.extend((self.queue_level_line_label(block.level), f"{block.round_name}회차"))
            if block.files:
                range_text = (f"{self.queue_file_line_label(block.files[0])} ~ "
                              f"{self.queue_file_line_label(block.files[-1])}")
            else:
                range_text = "선택된 파일 없음"
        else:
            # Registered labels contain useful publisher/document/range data.
            # Remove only their exact source and subject prefix, then retain
            # every detail and original filename shown by the previous UI.
            label_parts = [part.strip() for part in block.display_label.split("/") if part.strip()]
            source_prefixes = {source}
            if block.source == "material_textbook_variant":
                source_prefixes.add("교과서")
            elif block.source == "material_training_pack":
                source_prefixes.add("트팩")
            registered = block.source != "custom"
            if registered and label_parts and label_parts[0] in source_prefixes:
                label_parts.pop(0)
            if label_parts and label_parts[0] == block.subject:
                label_parts.pop(0)
            details.extend(label_parts)
            if not label_parts and block.source not in {"custom", "blank_review", "omr"}:
                details.extend(value for value in (block.level, block.round_name) if value and value != source)
            if block.files:
                start_label = block.files[0].path.stem
                end_label = block.files[-1].path.stem
                range_text = f"{start_label} ~ {end_label}" if start_label != end_label else start_label
            else:
                range_text = "선택된 파일 없음"
        details.append(range_text)
        return tuple(re.sub(r"[\r\n\t]+", " ", text) for text in (name, title, " / " + " / ".join(details)))

    def draw_queue_title_runs(self, block: PrintBlock, x: float, y: float, max_width: float) -> None:
        """Clip each styled run with the exact font used by Canvas."""
        name, title, details = self.queue_block_text_parts(block)
        runs = (
            (name, self.queue_line_bold_font, self.colors["complete"]),
            (title, self.queue_line_bold_font, self.colors["queue_title"]),
            (details, self.queue_line_font, self.colors["text"]),
        )
        right = x + max(0.0, max_width) - 2  # Canvas glyph overhang allowance.
        for text, font, color in runs:
            if not text:
                continue
            available = right - x
            if available < font.measure("…"):
                break
            clipped = compact_text_pixels(text, available, font)
            self.queue_canvas.create_text(x, y, text=clipped, anchor="w", fill=color, font=font)
            x += font.measure(clipped)
            if clipped != text:
                break

    def draw_queue_card(
        self,
        index: int,
        block: PrintBlock,
        width: int,
        *,
        y0: float | None = None,
        lifted: bool = False,
    ) -> None:
        canvas = self.queue_canvas
        colors = self.colors
        pad = self.queue_padding
        lift = self.drag_lift_progress if lifted else 0
        x0 = pad - 3 * lift
        x1 = width - pad + 3 * lift
        if y0 is None:
            y0 = self.queue_card_top(index)
        y0 = y0 - 2 * lift
        y1 = y0 + self.queue_card_height
        selected = index in self.selected_block_indices
        primary_cursor = (
            index == self.keyboard_state.queue_cursor
            and self.keyboard_state.zone in {KeyboardZone.QUEUE_LIST, KeyboardZone.QUEUE_EDITOR}
        )
        dragging = lifted or (self.drag_active and index in self.current_drag_indices())
        fill = colors["accent_soft"] if selected or dragging else colors["panel"]
        outline = colors["link"] if selected or dragging or primary_cursor else colors["border_soft"]
        outline_width = 2 if selected or dragging or primary_cursor else 1

        if lifted:
            shadow = 5 + 5 * lift
            self.draw_round_rect(
                x0 + shadow,
                y0 + shadow,
                x1 + shadow,
                y1 + shadow,
                radius=QUEUE_CARD_RADIUS,
                fill=colors.get("shadow", "#cbd5e1"),
                outline="",
                stipple="gray25",
            )

        self.draw_round_rect(x0, y0, x1, y1, radius=QUEUE_CARD_RADIUS, fill=fill, outline=outline, width=outline_width)
        if selected or dragging:
            marker_fill = colors["accent_hover"] if primary_cursor else colors["link"]
            self.draw_round_rect(x0 + 4, y0 + 9, x0 + 9, y1 - 9, radius=3, fill=marker_fill, outline="")

        row_mid = y0 + self.queue_card_height / 2
        canvas.create_text(
            x0 + 22,
            row_mid,
            text="⋮⋮",
            anchor="center",
            fill=colors["link"] if selected else colors["muted_soft"],
            font=(self.ui_font_family, 15, "bold"),
        )

        order_x = x1 - 34
        self.draw_round_rect(
            order_x - 18,
            y0 + 13,
            order_x + 18,
            y1 - 13,
            radius=QUEUE_CARD_RADIUS,
            fill=colors["accent_soft"] if selected or dragging else colors["panel_soft"],
            outline=colors["link"] if selected or dragging else colors["border_soft"],
            width=1,
        )
        canvas.create_text(
            order_x,
            row_mid,
            text=str(index + 1),
            anchor="center",
            fill=colors["link"] if selected else colors["text"],
            font=(self.ui_font_family, 13, "bold"),
        )

        text_left = x0 + 48
        text_right = order_x - 28
        status = self.queue_block_status(block)
        status_text = self.queue_block_status_label(block)
        status_fill, status_fg, status_outline = self.queue_status_colors(status)
        status_width = max(54, min(92, 24 + self.queue_status_font.measure(status_text)))
        status_x1 = text_right
        status_x0 = status_x1 - status_width
        copies_right = status_x0 - 8
        # Right-side metadata stays secondary; the material title remains one
        # line and the existing 62px queue geometry does not change.
        suffix_text = f"{block.copies}부"
        suffix_width = self.queue_meta_font.measure(suffix_text)
        prefix_width = max(0, copies_right - text_left - suffix_width - 8)
        self.draw_queue_title_runs(block, text_left, row_mid, prefix_width)
        canvas.create_text(
            copies_right,
            row_mid,
            text=suffix_text,
            anchor="e",
            fill=colors["muted"],
            font=self.queue_meta_font,
        )
        self.draw_round_rect(
            status_x0,
            row_mid - 14,
            status_x1,
            row_mid + 14,
            radius=QUEUE_CARD_RADIUS,
            fill=status_fill,
            outline=status_outline,
            width=1,
        )
        canvas.create_text(
            (status_x0 + status_x1) / 2,
            row_mid,
            text=status_text,
            anchor="center",
            fill=status_fg,
            font=self.queue_status_font,
        )




    def queue_animation_top(self, index: int, block: PrintBlock) -> float:
        if not self.drop_animation_positions:
            return self.queue_card_top(index)
        start, end = self.drop_animation_positions.get(
            id(block),
            (self.queue_card_top(index), self.queue_card_top(index)),
        )
        progress = ease_out_cubic(self.drop_animation_progress)
        return start + (end - start) * progress

    def current_drag_indices(self) -> tuple[int, ...]:
        indices = tuple(
            index
            for index in getattr(self, "drag_selected_indices", ())
            if 0 <= index < len(self.blocks)
        )
        if indices:
            return indices
        if self.drag_start_index is not None and 0 <= self.drag_start_index < len(self.blocks):
            return (self.drag_start_index,)
        return ()

    def current_drag_projection(self) -> QueueReorderResult:
        drop_index = self.drag_drop_index
        if drop_index is None:
            drop_index = self.drag_start_index if self.drag_start_index is not None else 0
        return reorder_selected_items(
            self.blocks,
            self.current_drag_indices(),
            drop_index,
            focus_index=self.drag_start_index,
        )

    def queue_drag_group_top(self) -> float | None:
        indices = self.current_drag_indices()
        if not indices or self.drag_start_index not in indices:
            return None
        slot = self.queue_card_height + self.queue_card_gap
        clicked_rank = indices.index(self.drag_start_index)
        clicked_top = self.queue_card_top(self.drag_start_index)
        if self.drag_pointer_y is not None:
            clicked_top = self.drag_pointer_y - self.drag_pointer_offset_y
        return clicked_top - clicked_rank * slot

    def cancel_lift_animation(self) -> None:
        if self.drag_lift_after_id is None:
            return
        try:
            self.root.after_cancel(self.drag_lift_after_id)
        except tk.TclError:
            pass
        self.drag_lift_after_id = None

    def start_lift_animation(self) -> None:
        self.cancel_lift_animation()
        self.drag_lift_started_at = time.perf_counter()
        self.drag_lift_progress = 0
        self.animate_lift()

    def animate_lift(self) -> None:
        if not self.drag_active:
            self.drag_lift_after_id = None
            return
        elapsed = time.perf_counter() - self.drag_lift_started_at
        self.drag_lift_progress = ease_out_cubic(elapsed / 0.12)
        self.draw_queue()
        if self.drag_lift_progress < 1:
            self.drag_lift_after_id = self.root.after(16, self.animate_lift)
        else:
            self.drag_lift_after_id = None

    def cancel_drop_animation(self) -> None:
        if self.drop_animation_after_id is not None:
            try:
                self.root.after_cancel(self.drop_animation_after_id)
            except tk.TclError:
                pass
        self.drop_animation_after_id = None
        self.drop_animation_positions = {}
        self.drop_animation_progress = 0

    def start_drop_animation(self, old_positions: dict[int, float]) -> None:
        self.cancel_drop_animation()
        self.drop_animation_positions = {}
        for index, block in enumerate(self.blocks):
            final_top = self.queue_card_top(index)
            self.drop_animation_positions[id(block)] = (old_positions.get(id(block), final_top), final_top)
        self.drop_animation_started_at = time.perf_counter()
        self.drop_animation_progress = 0
        self.animate_drop()

    def animate_drop(self) -> None:
        elapsed = time.perf_counter() - self.drop_animation_started_at
        self.drop_animation_progress = min(1.0, elapsed / self.drop_animation_duration)
        self.draw_queue()
        if self.drop_animation_progress < 1:
            self.drop_animation_after_id = self.root.after(16, self.animate_drop)
        else:
            self.drop_animation_after_id = None
            self.drop_animation_positions = {}
            self.drop_animation_progress = 0
            self.draw_queue()

    def current_queue_card_positions(self) -> dict[int, float]:
        positions: dict[int, float] = {}
        drag_indices = self.current_drag_indices() if self.drag_active else ()
        drag_index_set = set(drag_indices)
        group_top = self.queue_drag_group_top() if self.drag_active else None
        slot = self.queue_card_height + self.queue_card_gap
        projected_indices = (
            {
                id(block): index
                for index, block in enumerate(self.current_drag_projection().items)
            }
            if self.drag_active
            else {}
        )
        for index, block in enumerate(self.blocks):
            if self.drag_active and index in drag_index_set and group_top is not None:
                positions[id(block)] = group_top + drag_indices.index(index) * slot
            elif self.drag_active:
                positions[id(block)] = self.queue_card_top(projected_indices[id(block)])
            else:
                positions[id(block)] = self.queue_animation_top(index, block)
        return positions

    def draw_queue_insert_line(self, drop_index: int, width: int) -> None:
        colors = self.colors
        pad = self.queue_padding
        if drop_index <= 0:
            y = self.queue_card_top(0) - self.queue_card_gap / 2
        elif drop_index >= len(self.blocks):
            y = self.queue_card_top(len(self.blocks) - 1) + self.queue_card_height + self.queue_card_gap / 2
        else:
            previous_bottom = self.queue_card_top(drop_index - 1) + self.queue_card_height
            next_top = self.queue_card_top(drop_index)
            y = (previous_bottom + next_top) / 2

        self.queue_canvas.create_line(pad + 6, y, width - pad - 6, y, fill=colors["link"], width=3)
        self.queue_canvas.create_oval(pad, y - 5, pad + 10, y + 5, fill=colors["link"], outline="")

    def queue_card_top(self, index: int) -> int:
        return self.queue_padding + index * (self.queue_card_height + self.queue_card_gap)

    def queue_hit_index_from_y(self, canvas_y: float) -> int | None:
        return queue_hit_index(
            len(self.blocks), canvas_y, padding=self.queue_padding,
            card_height=self.queue_card_height, card_gap=self.queue_card_gap,
        )

    def on_queue_context_menu(self, event: tk.Event) -> str:
        index = self.queue_hit_index_from_y(self.queue_canvas.canvasy(event.y))
        if index is None:
            return "break"
        if not self.save_pending_queue_editor():
            return "break"
        if index not in self.selected_block_indices:
            if not self.select_queue_index(index):
                return "break"
        self.focus_queue_canvas()
        return self.post_queue_context_menu(event.x_root, event.y_root)

    def handle_queue_context_key(self, _event: tk.Event) -> str:
        index = self.selected_block_index()
        if index is None:
            return "break"
        if not self.save_pending_queue_editor():
            return "break"
        if not self.selected_block_indices:
            self.select_queue_index(index)
        self.focus_queue_canvas()
        return self.post_queue_context_menu(
            self.queue_canvas.winfo_rootx() + 28,
            int(self.queue_canvas.winfo_rooty() + self.queue_card_top(index) - self.queue_canvas.canvasy(0) + 20),
        )

    def post_queue_context_menu(self, x: int, y: int) -> str:
        previous_menu = getattr(self, "queue_context_menu", None)
        if previous_menu is not None:
            previous_menu.destroy()
        menu = tk.Menu(self.queue_canvas, tearoff=False)
        self.queue_context_menu = menu
        selected = [self.blocks[index] for index in sorted(self.selected_block_indices)
                    if 0 <= index < len(self.blocks)]
        editable_ids = self.editable_queue_block_ids(selected) if len(selected) >= 2 else set()
        can_group = (len(selected) >= 2 and not getattr(self, "group_build_pending", False)
                     and all(id(block) in editable_ids and not block.bundled for block in selected))
        can_fill = (len(selected) >= 2 and all(
            id(block) in editable_ids for block in selected
            if not normalize_name_separator_label(block.name_separator_label)
        ))
        menu.add_command(
            label="묶기",
            accelerator="Ctrl+G",
            state="normal" if can_group else "disabled",
            command=self.group_selected_blocks,
        )
        menu.add_command(
            label="이름 채우기",
            accelerator="Ctrl+Shift+N",
            state="normal" if can_fill else "disabled",
            command=self.fill_selected_block_names,
        )
        menu.add_separator()
        has_selection = bool(self.selected_block_indices)
        menu.add_command(
            label="원본 경로 열기",
            command=self.open_selected_block_source_folders,
            state="normal" if has_selection and not getattr(self, "source_paths_open_pending", False) else "disabled",
        )
        for label, accelerator, command, enabled in (
            ("복사", "Ctrl+C", self.copy_selected_blocks, has_selection),
            ("붙여넣기", "Ctrl+V", self.paste_copied_blocks, bool(getattr(self, "block_clipboard", ()))),
            ("복제", "Ctrl+D", self.duplicate_selected_blocks, has_selection),
            ("삭제", "Delete", self.delete_selected_block, has_selection),
        ):
            menu.add_command(label=label, accelerator=accelerator, command=command, state="normal" if enabled else "disabled")
        menu.add_separator()
        menu.add_command(
            label="가나다 정렬", command=self.sort_blocks_by_name,
            state="normal" if len(self.blocks) > 1 and not self.is_printing and getattr(self, "retry_batch", None) is None else "disabled",
        )
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()
        return "break"

    def open_selected_block_source_folders(self) -> str:
        """Open folders and select registered input PDFs without changing print state."""
        if getattr(self, "is_closing", False) or getattr(self, "source_paths_open_pending", False):
            return "break"
        indices = sorted(index for index in self.selected_block_indices if 0 <= index < len(self.blocks))
        paths = tuple(item.path for index in indices for item in self.blocks[index].files)
        if not paths:
            messagebox.showinfo(APP_TITLE, "등록된 PDF가 있는 블록을 선택해 주세요.", parent=self.root)
            return "break"
        folders = source_folder_targets(paths)
        key = tuple(sorted((os.path.normcase(str(folder)),
                            tuple(sorted(os.path.normcase(str(path)) for path in files)))
                           for folder, files in folders))
        now = time.monotonic()
        if (key == getattr(self, "source_paths_last_request_key", None)
                and now - getattr(self, "source_paths_last_request_at", 0.0) < 2.0):
            return "break"
        self.source_paths_last_request_key = key
        self.source_paths_last_request_at = now
        self.source_paths_open_pending = True
        cancel = threading.Event()
        request = (object(), cancel)
        self.source_paths_open_request = request
        results = getattr(self, "source_paths_open_results", None)
        if results is None:
            results = self.source_paths_open_results = queue.SimpleQueue()

        def work() -> None:
            try:
                result = open_source_folders(paths, cancelled=cancel.is_set)
            except Exception as error:
                result = error
            results.put((request, result))

        try:
            self.launch_source_paths_worker(work)
        except Exception:
            self.source_paths_open_pending = False
            self.source_paths_open_request = None
            messagebox.showwarning(APP_TITLE, "원본 폴더 열기를 시작하지 못했습니다.", parent=self.root)
            return "break"
        self.poll_source_paths_open_results()
        return "break"

    def launch_source_paths_worker(self, work: Callable[[], None]) -> None:
        threading.Thread(target=work, name="quick-print-source-paths", daemon=True).start()

    def poll_source_paths_open_results(self) -> None:
        self.source_paths_open_after_id = None
        if getattr(self, "is_closing", False):
            return
        request = getattr(self, "source_paths_open_request", None)
        if request is None:
            return
        result = None
        while True:
            try:
                completed_request, completed_result = self.source_paths_open_results.get_nowait()
            except queue.Empty:
                break
            if completed_request is request:
                result = completed_result
        if result is None:
            self.source_paths_open_after_id = self.root.after(50, self.poll_source_paths_open_results)
            return
        self.source_paths_open_pending = False
        self.source_paths_open_request = None
        if request[1].is_set():
            return
        if isinstance(result, Exception):
            messagebox.showwarning(APP_TITLE, "원본 폴더를 확인하지 못했습니다. 다시 실행해 주세요.", parent=self.root)
            return
        if result.cancelled:
            return
        notices: list[str] = []
        for paths, label in ((result.missing_files, "찾을 수 없는 PDF"),
                             (result.missing_folders, "찾을 수 없는 폴더"),
                             (result.failed_folders, "열지 못한 폴더")):
            if paths:
                notices.append(f"{label} {len(paths)}개:\n" + "\n".join(str(path) for path in paths[:3]))
        if result.skipped_folders:
            notices.append(f"폴더 {len(result.opened)}개를 열었습니다. 나머지 {result.skipped_folders}개는 선택 범위를 줄여 다시 열어 주세요.")
        if notices:
            messagebox.showwarning(APP_TITLE, "\n\n".join(notices), parent=self.root)

    def cancel_source_paths_open(self) -> None:
        request = getattr(self, "source_paths_open_request", None)
        if request is not None:
            request[1].set()
        self.source_paths_open_request = None
        self.source_paths_open_pending = False
        after_id = getattr(self, "source_paths_open_after_id", None)
        self.source_paths_open_after_id = None
        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except tk.TclError:
                pass

    def group_selected_blocks(self) -> bool:
        if getattr(self, "group_build_pending", False):
            self.queue_status_var.set("묶음 자료를 확인 중입니다. 잠시 기다려 주세요.")
            return False
        indices = sorted(
            index for index in self.selected_block_indices
            if 0 <= index < len(self.blocks)
        )
        if len(indices) < 2:
            messagebox.showwarning(APP_TITLE, "묶을 일반 블록을 두 개 이상 선택해 주세요.")
            return False
        selected = [self.blocks[index] for index in indices]
        with self.print_state_lock:
            editable = self._editable_queue_block_ids_locked(selected)
            scheduled_ids = {id(block) for block in selected} & set(
                getattr(self, "print_scheduled_block_ids", ())
            )
            run_key = (self.is_printing, getattr(self, "history_run_id", None))
            revision = getattr(self, "print_queue_revision", 0)
            template = next((job for job in getattr(self, "print_pending_jobs", ())
                             if job.block_uid in scheduled_ids), None)
            include_separator = bool(scheduled_ids and self.print_include_separator_pages)
            include_names = bool(scheduled_ids and self.print_include_name_separator_pages)
            name_context = [replace(block, files=list(block.files)) for block in self.blocks
                            if id(block) in getattr(self, "print_scheduled_block_ids", ())]
        if any(id(block) not in editable for block in selected):
            messagebox.showwarning(APP_TITLE, "전송이 시작됐거나 재시도 대상인 블록은 묶을 수 없습니다.")
            return False
        snapshots = [replace(block, files=list(block.files)) for block in selected]
        printer_var = getattr(self, "printer_var", None)
        base_printer = printer_var.get() if printer_var is not None else DEFAULT_PRINTER
        epoch = self.block_preparation_epoch
        token = self.reserve_block_commit()
        self.group_build_pending = True
        self.queue_status_var.set("묶음 자료 확인 중 · 작업 큐는 계속 사용할 수 있습니다.")

        def work() -> tuple[PrintBlock, tuple[PrintJob, ...], dict[str, Path]]:
            bundle = make_queue_bundle_block(snapshots, base_printer=base_printer)
            jobs = tuple(self.build_print_jobs(
                [bundle], include_separator_pages=include_separator,
                start_block_index=template.block_index, block_total=template.block_total,
                automatic_omr_item=None,
            )) if template is not None else ()
            paths = self.prepare_queue_edit_name_paths(name_context + [bundle], include_names)
            return bundle, jobs, paths

        def finish(future: object) -> None:
            try:
                bundle, jobs, name_paths = future.result()  # type: ignore[attr-defined]
            except Exception as error:
                message = str(error)

                def show_error() -> None:
                    self.group_build_pending = False
                    self.queue_status_var.set("묶음 준비 실패 · 원래 블록을 유지했습니다.")
                    messagebox.showwarning(APP_TITLE, message)

                self.complete_block_commit(token, show_error)
                return

            def commit() -> None:
                self.group_build_pending = False
                current_printer = printer_var.get() if printer_var is not None else DEFAULT_PRINTER
                if (getattr(self, "is_closing", False)
                    or (self.is_printing, getattr(self, "history_run_id", None)) != run_key
                    or (scheduled_ids and getattr(self, "print_queue_revision", 0) != revision)
                    or epoch != self.block_preparation_epoch or current_printer != base_printer
                    or any(
                        index >= len(self.blocks) or self.blocks[index] is not block
                        or block != snapshot
                        for index, block, snapshot in zip(indices, selected, snapshots)
                    )):
                    self.queue_status_var.set("준비 중 작업 큐가 바뀌어 묶기를 취소했습니다.")
                    messagebox.showwarning(APP_TITLE, "준비 중 작업 큐나 프린터가 바뀌었습니다. 다시 선택해 주세요.")
                    return
                error_message = "전송이 시작되거나 예약이 바뀌었습니다. 다시 선택해 주세요."
                try:
                    committed = self.commit_queue_bundle(
                        indices, selected, bundle, scheduled_ids=scheduled_ids,
                        prepared_jobs=jobs, prepared_name_paths=name_paths,
                    )
                except ValueError as error:
                    error_message = str(error)
                    committed = False
                if not committed:
                    self.queue_status_var.set("전송이 시작되거나 예약이 바뀌어 묶기를 취소했습니다.")
                    messagebox.showwarning(APP_TITLE, error_message)

            self.complete_block_commit(token, commit)

        try:
            self.submit_background_block_build(work, finish, epoch=epoch)
        except Exception as error:
            self.group_build_pending = False
            self.cancel_block_commit(token)
            self.queue_status_var.set("묶음 준비 실패 · 원래 블록을 유지했습니다.")
            messagebox.showwarning(APP_TITLE, str(error))
            return False
        return True

    def commit_queue_bundle(
        self, indices: list[int], selected: list[PrintBlock], bundle: PrintBlock,
        *, scheduled_ids: set[int] | None = None, prepared_jobs: tuple[PrintJob, ...] = (),
        prepared_name_paths: dict[str, Path] | None = None,
    ) -> bool:
        scheduled_ids = scheduled_ids or set()
        replacements = {id(block): () for block in selected}
        replacements[id(selected[0])] = (bundle,)
        job_replacements = {uid: () for uid in scheduled_ids}
        if scheduled_ids:
            anchor = next(id(block) for block in selected if id(block) in scheduled_ids)
            job_replacements[anchor] = prepared_jobs
        if not self.apply_waiting_block_replacements(
            replacements, job_replacements=job_replacements,
            prepared_name_paths=prepared_name_paths or {}, undo_label="블록 묶기",
        ):
            return False
        self.sync_retry_queue()
        self.update_queue_view(select_index=indices[0])
        self.update_preview()
        self.record_history(
            "blocks_grouped", group_block_id=bundle.history_id,
            source_block_ids=[block.history_id for block in selected],
            group_mode="document_sequence",
        )
        self.log(f"블록 {len(selected)}개 묶기: {bundle.summary}")
        return True


    def fill_selected_block_names(self) -> bool:
        indices = sorted(index for index in self.selected_block_indices if 0 <= index < len(self.blocks))
        if len(indices) < 2:
            messagebox.showwarning(APP_TITLE, "이름이 있는 블록과 이름을 채울 블록을 함께 선택해 주세요.")
            return False
        selected = [self.blocks[index] for index in indices]
        try:
            name, blank_positions = name_separator_fill_plan(
                block.name_separator_label for block in selected
            )
        except ValueError as error:
            messagebox.showwarning(APP_TITLE, str(error))
            return False
        targets = [(indices[position], selected[position]) for position in blank_positions]
        if any(not block_uses_name_separator(block) for _index, block in targets):
            messagebox.showwarning(APP_TITLE, "고정 백지리뷰·OMR 블록은 이름 구분지 대상이 아닙니다.")
            return False
        with self.print_state_lock:
            editable = self._editable_queue_block_ids_locked([block for _, block in targets])
            scheduled_ids = set(getattr(self, "print_scheduled_block_ids", ()))
            pending_jobs = tuple(getattr(self, "print_pending_jobs", ()))
            include_names = bool(self.is_printing and getattr(self, "print_include_name_separator_pages", False))
        if any(id(block) not in editable for _, block in targets):
            messagebox.showwarning(
                APP_TITLE,
                "전송이 시작됐거나 재시도 대상인 블록의 이름은 일괄 변경할 수 없습니다.",
            )
            return False

        replacements = {id(old): (replace(old, name_separator_label=name),) for _, old in targets}
        job_replacements: dict[int, tuple[PrintJob, ...]] = {}
        for old_uid, (new,) in replacements.items():
            if old_uid in scheduled_ids:
                history = block_history_data(new)
                job_replacements[old_uid] = tuple(
                    replace(job, block_uid=id(new), block_summary=new.summary,
                            history_block=history, job_id=str(uuid.uuid4()))
                    for job in pending_jobs if job.block_uid == old_uid and not job.is_name_separator
                )
        try:
            name_context = [replacements.get(id(block), (block,))[0] for block in self.blocks
                            if id(block) in scheduled_ids]
            paths = self.prepare_queue_edit_name_paths(name_context, include_names and bool(job_replacements))
            if not self.apply_waiting_block_replacements(
                replacements, job_replacements=job_replacements,
                prepared_name_paths=paths, undo_label="이름 채우기",
            ):
                messagebox.showwarning(APP_TITLE, "전송이 시작되거나 예약이 바뀌었습니다. 다시 선택해 주세요.")
                return False
        except (OSError, ValueError) as error:
            messagebox.showwarning(APP_TITLE, str(error))
            return False
        self.selected_block_indices = set(indices)
        self.update_queue_view()
        self.update_preview()
        self.queue_status_var.set(f"{name} · 이름 없는 블록 {len(targets)}개에 적용")
        self.log(f"이름 채우기: {name} · 블록 {len(targets)}개")
        return True

    def queue_drop_index_from_canvas_y(self, canvas_y: float) -> int:
        return queue_drop_index(
            len(self.blocks), canvas_y, padding=self.queue_padding,
            card_height=self.queue_card_height, card_gap=self.queue_card_gap,
        )

    def on_queue_drag_start(self, event: tk.Event) -> str:
        self.cancel_drop_animation()
        canvas_y = self.queue_canvas.canvasy(event.y)
        index = self.queue_hit_index_from_y(canvas_y)
        if index is None:
            self.drag_start_index = None
            self.drag_selected_indices = ()
            self.drag_drop_index = None
            self.drag_pointer_y = None
            self.drag_pointer_offset_y = 0
            self.drag_active = False
            if not event.state & 0x0005:
                if self.select_queue_indices(set()):
                    self.focus_queue_canvas()
            else:
                self.queue_canvas.focus_set()
                self.draw_queue()
            return "break"
        if event.state & 0x0005:
            selected = self.select_queue_with_modifiers(index, event.state)
            self.drag_start_index = None
            self.drag_selected_indices = ()
            self.drag_drop_index = None
            self.drag_pointer_y = None
            self.drag_pointer_offset_y = 0
            self.drag_active = False
            if selected:
                self.focus_queue_canvas()
            return "break"
        selected = set(self.selected_block_indices)
        drag_indices = tuple(sorted(selected)) if index in selected else (index,)
        if not self.save_queue_editor_before_selection(index, set(drag_indices)):
            return "break"
        self.queue_canvas.focus_set()
        if any(self.block_is_scheduled_for_print(self.blocks[item]) for item in drag_indices):
            self.queue_status_var.set("실행 큐에 등록된 블록은 드래그 정렬 불가")
            self.drag_start_index = None
            self.drag_selected_indices = ()
            self.drag_drop_index = None
            self.drag_pointer_y = None
            self.drag_pointer_offset_y = 0
            self.drag_active = False
            if index not in selected:
                self.select_queue_index(index, load_form=True)
            return "break"
        self.drag_start_index = index
        self.drag_selected_indices = drag_indices
        self.drag_drop_index = index
        self.drag_origin_y = canvas_y
        self.drag_pointer_y = canvas_y
        self.drag_pointer_offset_y = canvas_y - self.queue_card_top(index)
        self.drag_lift_progress = 0
        self.drag_active = False
        self.selected_block_index_value = index
        self.selected_block_indices = set(drag_indices)
        if len(drag_indices) == 1:
            self.queue_selection_anchor = index
        self.draw_queue()
        return "break"

    def on_queue_drag_motion(self, event: tk.Event) -> str:
        if self.drag_start_index is None:
            return "break"
        if event.y < 28:
            self.queue_canvas.yview_scroll(-1, "units")
        elif event.y > self.queue_canvas.winfo_height() - 28:
            self.queue_canvas.yview_scroll(1, "units")

        canvas_y = self.queue_canvas.canvasy(event.y)
        self.drag_pointer_y = canvas_y
        if not self.drag_active and abs(canvas_y - self.drag_origin_y) < 8:
            return "break"
        if not self.drag_active:
            self.drag_active = True
            self.start_lift_animation()
        self.drag_active = True
        drop_index = self.queue_drop_index_from_canvas_y(canvas_y)
        self.drag_drop_index = drop_index
        self.request_queue_redraw()
        drag_indices = self.current_drag_indices()
        if drop_index == len(self.blocks):
            destination = "맨 아래"
        else:
            # Only the selected indices are needed for this label. The complete
            # projection is built once by the deferred frame or final commit.
            selected = tuple(sorted(set(drag_indices)))
            bounded_drop = max(0, min(drop_index, len(self.blocks)))
            insertion_index = bounded_drop - sum(index < bounded_drop for index in selected)
            insertion_index = max(0, min(insertion_index, len(self.blocks) - len(selected)))
            if len(selected) > 1:
                destination = (
                    f"{insertion_index + 1}~"
                    f"{insertion_index + len(selected)}번 위치"
                )
            else:
                destination = f"{insertion_index + 1}번 위치"
        count = len(drag_indices)
        prefix = f"블록 {count}개 · " if count > 1 else ""
        status = f"{prefix}{destination}로 이동"
        if self.queue_status_var.get() != status:
            self.queue_status_var.set(status)
        return "break"

    def on_queue_drag_release(self, event: tk.Event) -> str:
        if self.drag_start_index is None:
            return "break"
        start_index = self.drag_start_index
        drag_indices = self.current_drag_indices()
        if not self.drag_active:
            self.cancel_lift_animation()
            self.drag_start_index = None
            self.drag_selected_indices = ()
            self.drag_drop_index = None
            self.drag_pointer_y = None
            self.drag_pointer_offset_y = 0
            self.drag_lift_progress = 0
            self.drag_active = False
            selected = self.select_queue_indices(
                set(drag_indices), focus_index=start_index,
                anchor_index=self.queue_selection_anchor, load_form=True,
            )
            if selected:
                self.focus_queue_canvas()
            return "break"

        canvas_y = self.queue_canvas.canvasy(event.y)
        self.drag_pointer_y = canvas_y
        drop_index = self.drag_drop_index
        if drop_index is None:
            drop_index = self.queue_drop_index_from_canvas_y(canvas_y)
        old_positions = self.current_queue_card_positions()
        result = self.reorder_blocks(drag_indices, drop_index, focus_index=start_index)
        self.cancel_lift_animation()
        self.drag_start_index = None
        self.drag_selected_indices = ()
        self.drag_drop_index = None
        self.drag_pointer_y = None
        self.drag_pointer_offset_y = 0
        self.drag_lift_progress = 0
        self.drag_active = False
        self.selected_block_index_value = result.focus_index
        self.selected_block_indices = set(result.selected_indices)
        self.queue_selection_anchor = result.focus_index
        if (
            result.focus_index is not None
            and self.keyboard_state.zone in {KeyboardZone.QUEUE_LIST, KeyboardZone.QUEUE_EDITOR}
        ):
            self.keyboard_state.queue_cursor = result.focus_index
        self.start_drop_animation(old_positions)
        total_files = sum(block.file_count for block in self.blocks)
        total_sets = sum(block.file_count * block.copies for block in self.blocks)
        if any(self.block_is_scheduled_for_print(self.blocks[index]) for index in result.selected_indices):
            self.queue_status_var.set("실행 큐에 등록된 블록은 드래그 정렬 불가")
        else:
            self.queue_status_var.set(f"블록 {len(self.blocks)}개 / PDF {total_files}개 / 전송 {total_sets}세트")
        self.update_preview()
        if result.changed:
            if len(result.selected_indices) > 1:
                destination = f"{result.selected_indices[0] + 1}~{result.selected_indices[-1] + 1}번"
                self.log(f"블록 {len(result.selected_indices)}개 순서 변경: {destination} 위치")
            elif result.focus_index is not None:
                self.log(f"블록 순서 변경: {result.focus_index + 1}번 위치")
        return "break"

    def on_queue_double_click(self, event: tk.Event) -> str:
        canvas_y = self.queue_canvas.canvasy(event.y)
        index = self.queue_hit_index_from_y(canvas_y)
        if index is not None:
            if self.select_queue_index(index, load_form=True):
                self.focus_queue_editor()
        return "break"

    @staticmethod
    def pdf_preflight_signature(path: Path) -> tuple[int, int] | None:
        try:
            stat = path.stat()
            return stat.st_size, stat.st_mtime_ns
        except OSError:
            return None

    def cached_preflight_result(self, path: Path) -> PdfPreflightResult | None:
        result = self.pdf_preflight_by_path.get(path)
        if result is None:
            return None
        signatures = getattr(self, "pdf_preflight_signature_by_path", {})
        if signatures.get(path) != self.pdf_preflight_signature(path):
            self.pdf_preflight_by_path.pop(path, None)
            signatures.pop(path, None)
            return None
        return result

    def remember_preflight_results(
        self,
        results: tuple[PdfPreflightResult, ...] | list[PdfPreflightResult],
        signatures: dict[Path, tuple[int, int] | None] | None = None,
    ) -> None:
        signature_cache = getattr(self, "pdf_preflight_signature_by_path", None)
        if signature_cache is None:
            signature_cache = {}
            self.pdf_preflight_signature_by_path = signature_cache
        for result in results:
            self.pdf_preflight_by_path[result.path] = result
            signature_cache[result.path] = (
                signatures[result.path]
                if signatures is not None and result.path in signatures
                else self.pdf_preflight_signature(result.path)
            )

    def schedule_preview_preflight(self, paths: set[Path]) -> None:
        """Resolve automatic PDF routes without blocking the Tk event loop."""
        if not paths or self.is_closing:
            return
        with self.preview_preflight_lock:
            self.preview_preflight_pending_paths.update(paths)
        self.start_preview_preflight_batch()

    def start_preview_preflight_batch(self) -> None:
        with self.preview_preflight_lock:
            if self.preview_preflight_active or not self.preview_preflight_pending_paths:
                return
            paths = tuple(self.preview_preflight_pending_paths)
            self.preview_preflight_pending_paths.clear()
            self.preview_preflight_active = True
        signatures: dict[Path, tuple[int, int] | None] = {}

        def inspect() -> None:
            try:
                signatures.update((path, self.pdf_preflight_signature(path)) for path in paths)
                result: PdfPreflightReport | Exception = preflight_pdfs(paths)
            except Exception as error:
                result = error
            with self.preview_preflight_lock:
                self.preview_preflight_completed = (result, signatures)

        try:
            threading.Thread(target=inspect, daemon=True).start()
        except Exception as error:
            with self.preview_preflight_lock:
                self.preview_preflight_completed = (error, signatures)
        if self.preview_preflight_poll_after_id is None:
            self.preview_preflight_poll_after_id = self.root.after(
                15,
                self.poll_preview_preflight_result,
            )

    def poll_preview_preflight_result(self) -> None:
        self.preview_preflight_poll_after_id = None
        with self.preview_preflight_lock:
            completed = self.preview_preflight_completed
            if completed is not None:
                self.preview_preflight_completed = None
                self.preview_preflight_active = False
            active = self.preview_preflight_active
        if self.is_closing:
            return
        if completed is None:
            if active:
                self.preview_preflight_poll_after_id = self.root.after(
                    15,
                    self.poll_preview_preflight_result,
                )
            return
        report, signatures = completed
        if isinstance(report, Exception):
            self.log(f"미리보기 PDF 검사 실패: {type(report).__name__}: {report}")
            # A failed batch is retryable on the next explicit preview request.
            # Redrawing here would immediately schedule the same unresolved PDFs
            # again and create a tight error/retry loop. Print preflight remains
            # mandatory and is independent of this optional preview cache.
            self.start_preview_preflight_batch()
            return
        self.remember_preflight_results(report.results, signatures)
        self.update_preview()
        self.start_preview_preflight_batch()

    def update_preview(self) -> None:
        """Coalesce edits; file checks and formatting never run in this callback."""
        self.cancel_preview_render()
        if getattr(self, "is_closing", False):
            return
        self.preview_update_after_id = self.root.after(1, self.start_preview_render)

    def cancel_preview_render(self) -> None:
        self.preview_render_revision = getattr(self, "preview_render_revision", 0) + 1
        for name in ("preview_update_after_id", "preview_render_after_id"):
            after_id = getattr(self, name, None)
            if after_id is not None:
                try:
                    self.root.after_cancel(after_id)
                except tk.TclError:
                    pass
            setattr(self, name, None)
        worker = getattr(self, "preview_render_worker", None)
        if worker is not None:
            worker.cancel(self.preview_render_revision)
        self.preview_render_chunks = None

    def start_preview_render(self) -> None:
        self.preview_update_after_id = None
        if getattr(self, "is_closing", False):
            return
        self.preview_text.delete("1.0", END)
        if not self.blocks:
            self.preview_text.insert(END, "작업 큐가 비어 있습니다. 범위를 선택하고 [블록 추가]를 누르세요.\n")
            self.schedule_resource_estimate()
            return
        self.preview_text.insert(END, "미리보기 갱신 중…\n")
        try:
            # Snapshot values on Tk; the worker cannot observe later in-place
            # edits or read StringVar/BooleanVar. Frozen ExamPdf values are shared.
            blocks = tuple(replace(block, files=list(block.files)) for block in self.blocks)
            request = PreviewRequest(
                blocks=blocks,
                printer=self.printer_var.get(),
                name_separator=bool(self.name_separator_page_var.get()),
                separator=bool(self.separator_page_var.get()),
                automatic_omr=(self.make_automatic_omr_item() if any(block_requires_omr_sheet(block) for block in blocks) else None),
                cached_results=dict(self.pdf_preflight_by_path),
                cached_signatures=dict(self.pdf_preflight_signature_by_path),
            )
            # Capture the forecast's Tk variables before the CPU-heavy preview
            # worker starts. One read per option avoids repeated GIL handoffs.
            self.schedule_resource_estimate()
            worker = getattr(self, "preview_render_worker", None)
            if worker is None:
                worker = self.preview_render_worker = PreviewWorker()
            worker.submit(self.preview_render_revision, request)
            self.preview_render_after_id = self.root.after(10, self.poll_preview_render)
        except Exception as error:
            self.show_preview_render_error(error)

    def poll_preview_render(self) -> None:
        self.preview_render_after_id = None
        if getattr(self, "is_closing", False):
            return
        worker = self.preview_render_worker
        completed = None
        while True:
            try:
                revision, result = worker.results.get_nowait()
            except queue.Empty:
                break
            if revision == self.preview_render_revision:
                completed = result
        if completed is None:
            self.preview_render_after_id = self.root.after(10, self.poll_preview_render)
            return
        if isinstance(completed, Exception):
            self.show_preview_render_error(completed)
            return
        self.preview_render_chunks = iter(completed.chunks)
        self.preview_render_unresolved = completed.unresolved_paths
        self.preview_text.delete("1.0", END)
        self.insert_preview_chunk()

    def insert_preview_chunk(self) -> None:
        self.preview_render_after_id = None
        if getattr(self, "is_closing", False) or self.preview_render_chunks is None:
            return
        chunk = next(self.preview_render_chunks, None)
        if chunk is None:
            self.preview_render_chunks = None
            self.schedule_preview_preflight(set(self.preview_render_unresolved))
            return
        self.preview_text.insert(END, chunk)
        self.preview_render_after_id = self.root.after(1, self.insert_preview_chunk)

    def show_preview_render_error(self, error: Exception) -> None:
        self.preview_render_after_id = None
        self.preview_render_chunks = None
        self.preview_text.delete("1.0", END)
        self.preview_text.insert(END, "미리보기를 갱신하지 못했습니다. 다시 편집하면 재시도합니다. 실제 인쇄 전 PDF 검사는 유지됩니다.\n")
        self.log(f"미리보기 생성 실패: {type(error).__name__}")

    def preview_route_text(
        self,
        item: ExamPdf,
        printer_override: str | None = None,
        unresolved_paths: set[Path] | None = None,
    ) -> str:
        result = self.cached_preflight_result(item.path)
        if result is None and unresolved_paths is not None:
            unresolved_paths.add(item.path)
        if printer_override:
            return f"  [전용 -> {printer_override}]"
        if result is None:
            return "  [자동 감지 확인 중]"
        if not result.is_printable or result.orientation is None:
            finding = result.findings[0] if result.findings else None
            return f"  [인쇄 차단: {finding.code.value if finding is not None else '검사 실패'}]"
        profiles = derive_profile_names(self.printer_var.get())
        kind = PrintRouteKind.PORTRAIT if result.orientation is PdfOrientation.PORTRAIT else PrintRouteKind.LANDSCAPE
        route_name = route_print_job(profiles, kind).queue_name
        orientation_label = "세로" if kind is PrintRouteKind.PORTRAIT else "가로"
        return f"  [{orientation_label} -> {route_name}]"

    def preview_separator_route_text(self) -> str:
        profiles = derive_profile_names(self.printer_var.get())
        return f"  [구분지 -> {route_print_job(profiles, PrintRouteKind.SEPARATOR).queue_name}]"

    def preview_name_separator_route_text(self) -> str:
        profiles = derive_profile_names(self.printer_var.get())
        return f"  [이름 구분지·세로 -> {route_print_job(profiles, PrintRouteKind.PORTRAIT).queue_name}]"

    def block_to_config(self, block: PrintBlock) -> dict[str, object]:
        data: dict[str, object] = {
            "source": block.source,
            "subject": block.subject,
            "level": block.level,
            "round": block.round_name,
            "start": block.start,
            "end": block.end,
            "copies": block.copies,
            "bundled": block.bundled,
            "printer_override": block.printer_override,
            "display_label": block.display_label,
            "pages_per_sheet": block.pages_per_sheet,
            "printer_route": block.printer_route,
            "name_separator_label": block.name_separator_label,
            "material_kind": block.material_kind,
        }
        if block.selected_numbers:
            data["selected_numbers"] = list(block.selected_numbers)
        if block.source == "custom" and block.material_kind != "queue_bundle":
            data["files"] = [str(item.path) for item in block.files]
        elif (
            (block.source == "custom" and block.material_kind == "queue_bundle")
            or block.source.startswith("material_")
            or block.source.startswith("test_")
            or block.source in FIXED_FORM_SOURCES
        ):
            data["files"] = [
                {
                    "number": item.number,
                    "name": item.name,
                    "path": str(item.path),
                    "printer_override": item.printer_override,
                    "pages_per_sheet": item.pages_per_sheet,
                }
                for item in block.files
            ]
        return data

    def block_from_config(self, data: dict) -> PrintBlock | None:
        try:
            source = str(data.get("source", "catalog"))
            subject = str(data["subject"])
            level = str(data["level"])
            round_name = str(data["round"])
            start = int(data["start"])
            end = int(data["end"])
            copies = int(data["copies"])
            bundled = bool(data.get("bundled", False))
            printer_override = data.get("printer_override")
            printer_route = normalize_printer_route(data.get("printer_route", "자동 감지"))
            display_label = str(data.get("display_label", ""))
            name_separator_label = normalize_name_separator_label(
                data.get("name_separator_label", "")
            )
            raw_material_kind = data.get("material_kind", "")
            material_kind = (
                "queue_bundle" if source == "custom" and raw_material_kind == "queue_bundle"
                else normalize_able_document_kind(raw_material_kind)
            )
            pages_per_sheet = int(data.get("pages_per_sheet", 1))
            if pages_per_sheet not in {1, 4}:
                return None
            raw_selected_numbers = data.get("selected_numbers")
            selected_numbers: tuple[int, ...] | None = None
            if raw_selected_numbers is not None:
                if not isinstance(raw_selected_numbers, list):
                    return None
                selected_numbers = tuple(int(value) for value in raw_selected_numbers)
                if (
                    not selected_numbers
                    or any(number < 1 for number in selected_numbers)
                    or len(set(selected_numbers)) != len(selected_numbers)
                ):
                    return None
        except Exception:
            return None
        if printer_override:
            printer_override = str(printer_override)
        if printer_route == "자동 감지" and printer_override:
            printer_var = getattr(self, "printer_var", None)
            base_printer = printer_var.get() if printer_var is not None else DEFAULT_PRINTER
            printer_route = infer_printer_route(base_printer, printer_override)
        if source == "material_able" and not material_kind:
            raw_files = data.get("files", [])
            material_kind = classify_able_document_kind(
                [
                    display_label,
                    *(
                        str(item.get("name", ""))
                        for item in raw_files
                        if isinstance(item, dict)
                    ),
                ]
            )
        if source in {
            "catalog",
            "material_apex",
            "material_able",
            "material_training_pack",
            "material_frequent_type",
            "material_selected_exam",
            "material_gyopyeong",
            "material_textbook",
            "material_textbook_variant",
            "blank_review",
            "omr",
            *TEST_KIND_BY_SOURCE,
        }:
            if source == "omr":
                fixed_route = "가로"
            elif source == "material_able":
                fixed_route = (
                    "세로" if material_kind in {"백지리뷰", "개념정리"} else "A:ble"
                )
            else:
                fixed_route = "세로"
            printer_route, printer_override = self.resolve_block_printer_route(fixed_route)
        if source == "custom":
            raw_files = data.get("files", [])
            files = []
            try:
                for index, item in enumerate(raw_files, start=1):
                    if material_kind == "queue_bundle" and isinstance(item, dict):
                        number = int(item["number"])
                        item_pages = item.get("pages_per_sheet")
                        if number < 1 or item_pages not in {None, 1}:
                            return None
                        item_printer = item.get("printer_override")
                        files.append(ExamPdf(
                            number, str(item["name"]), Path(str(item["path"])),
                            printer_override=str(item_printer) if item_printer else None,
                            pages_per_sheet=item_pages,
                        ))
                    else:
                        files.append(ExamPdf(index, Path(item).stem, Path(item)))
            except (KeyError, TypeError, ValueError):
                return None
            if not files:
                return None
            if material_kind == "queue_bundle":
                if not bundled or pages_per_sheet != 1:
                    return None
                start, end = 1, len(files)
                selected = files
                selected_numbers = None
            else:
                start = max(1, min(start, len(files)))
                end = max(start, min(end, len(files)))
                selected = [item for item in files if start <= item.number <= end]
            return PrintBlock(
                subject if material_kind == "queue_bundle" else "새 PDF",
                level if material_kind == "queue_bundle" else "",
                round_name if material_kind == "queue_bundle" else "직접",
                start,
                end,
                copies,
                selected,
                bundled,
                source="custom",
                printer_override=printer_override,
                display_label=display_label,
                pages_per_sheet=pages_per_sheet,
                printer_route=printer_route,
                name_separator_label=name_separator_label,
                material_kind=material_kind,
            )
        if source.startswith("material_") or source.startswith("test_") or source in FIXED_FORM_SOURCES:
            raw_files = data.get("files", [])
            files: list[ExamPdf] = []
            for item in raw_files:
                if not isinstance(item, dict):
                    continue
                try:
                    number = int(item["number"])
                    name = str(item["name"])
                    path = Path(str(item["path"]))
                    if source == "material_able":
                        files.append(make_able_exam_pdf(number, name, path))
                        continue
                    item_printer = item.get("printer_override")
                    raw_item_pages = item.get("pages_per_sheet")
                    item_pages = int(raw_item_pages) if raw_item_pages is not None else None
                    if item_pages not in {None, 1, 4}:
                        return None
                    files.append(
                        ExamPdf(
                            number,
                            name,
                            path,
                            printer_override=str(item_printer) if item_printer else None,
                            pages_per_sheet=item_pages,
                        )
                    )
                except Exception:
                    continue
            if not files:
                return None
            if source == "material_able":
                pages_per_sheet = 1
            return PrintBlock(
                subject,
                level,
                round_name,
                start,
                end,
                copies,
                files,
                bundled,
                source=source,
                printer_override=str(printer_override) if printer_override else None,
                display_label=display_label,
                pages_per_sheet=pages_per_sheet,
                printer_route=printer_route,
                selected_numbers=selected_numbers,
                name_separator_label=name_separator_label,
                material_kind=material_kind,
            )
        files = catalog_exam_files(self.catalog, self.root_path, subject, level, round_name) if self.catalog else scan_exam_files(self.root_path, subject, level, round_name)
        if selected_numbers:
            files_by_number = {item.number: item for item in files}
            if any(number not in files_by_number for number in selected_numbers):
                return None
            selected = [files_by_number[number] for number in selected_numbers]
            start, end = min(selected_numbers), max(selected_numbers)
        else:
            selected = [item for item in files if start <= item.number <= end]
        if not selected:
            return None
        return PrintBlock(
            subject,
            level,
            round_name,
            start,
            end,
            copies,
            selected,
            bundled,
            source="catalog",
            printer_override=printer_override,
            printer_route=printer_route,
            selected_numbers=selected_numbers,
            name_separator_label=name_separator_label,
        )

    def restore_blocks(self) -> None:
        for data in self.config.get("blocks", []):
            block = self.block_from_config(data)
            if block is not None:
                self.blocks.append(block)

    def save_current_config(self) -> None:
        eta_samples = self.eta_print_rate_samples()
        timing_samples = self.eta_job_timing_samples()
        config = {
            "root_dir": self.root_dir_var.get(),
            "printer": self.printer_var.get(),
            "sumatra_path": self.sumatra_var.get(),
            "copies": 1,
            "custom_copies": 1,
            "custom_bundle": self.custom_bundle_var.get(),
            "custom_printer_route": self.custom_printer_route_var.get(),
            "queue_batch_size": self.queue_batch_size_var.get(),
            "queue_refill_threshold": self.queue_refill_threshold_var.get(),
            "separator_page": self.separator_page_var.get(),
            "name_separator_page": self.name_separator_page_var.get(),
            "duplex": True,
            "engine": self.engine_var.get(),
            "device_monitor": {
                "enabled": bool(self.device_monitor_config.get("enabled", False)),
                "base_url": str(self.device_monitor_config.get("base_url", "")),
                "strict_ordering": bool(self.device_monitor_config.get("strict_ordering", True)),
                "idle_interval_seconds": float(
                    self.device_monitor_config.get("idle_interval_seconds", 12.0)
                ),
                "active_interval_seconds": float(
                    self.device_monitor_config.get("active_interval_seconds", 1.0)
                ),
                "dispatch_window_size": max(
                    1,
                    min(10, int(self.device_monitor_config.get("dispatch_window_size", 6))),
                ),
                "dispatch_policy_version": DEVICE_DISPATCH_POLICY_VERSION,
                "completion_timeout_seconds": int(
                    self.device_monitor_config.get(
                        "completion_timeout_seconds", DEVICE_COMPLETION_TIMEOUT_SECONDS
                    )
                ),
                ETA_PRINT_RATE_CONFIG_KEY: list(eta_samples),
                ETA_TIMING_DEVICE_KEY: timing_device_key(str(self.device_monitor_config.get("base_url", ""))),
                ETA_TIMING_CONFIG_KEY: [
                    {"sheets": sample.sheets, "copies": sample.copies, "seconds": sample.seconds, "duplex": sample.duplex}
                    for sample in timing_samples
                ],
            },
            "blocks": [],
        }
        save_config(config)
        self.log("설정을 저장했습니다.")

    def resolve_engine(self) -> tuple[str, str]:
        engine = self.engine_var.get()
        sumatra = find_sumatra_pdf(self.sumatra_var.get())
        if engine == "SumatraPDF":
            if not sumatra:
                raise RuntimeError(
                    "SumatraPDF.exe 경로를 찾을 수 없습니다. "
                    "SumatraPDF를 설치한 뒤 '자동 찾기'를 누르거나 직접 경로를 지정해 주세요."
                )
            self.sumatra_var.set(sumatra)
            return "sumatra", sumatra
        if engine == "Windows 기본 인쇄":
            return "shell", ""
        raise RuntimeError(f"알 수 없는 인쇄 방식입니다: {engine}")

    def current_print_run_options(self) -> PrintRunOptions:
        engine, sumatra_path = self.resolve_engine()
        return PrintRunOptions(
            printer_name=self.printer_var.get().strip(),
            engine=engine,
            sumatra_path=sumatra_path,
            duplex=bool(self.duplex_var.get()),
            dry_run=bool(self.dry_run_var.get()),
            queue_batch_size=max(1, int(self.queue_batch_size_var.get())),
            queue_refill_threshold=max(0, int(self.queue_refill_threshold_var.get())),
            device_completion_mode=bool(
                not self.dry_run_var.get()
                and self.device_monitor_config.get("enabled", False)
                and self.device_monitor_config.get("strict_ordering", True)
            ),
            device_completion_timeout_seconds=max(
                60,
                int(
                    self.device_monitor_config.get(
                        "completion_timeout_seconds", DEVICE_COMPLETION_TIMEOUT_SECONDS
                    )
                ),
            ),
            device_window_size=max(
                1,
                min(10, int(self.device_monitor_config.get("dispatch_window_size", 6))),
            ),
        )

    def ensure_device_monitor_ready_for_print(self, run_options: PrintRunOptions) -> None:
        if run_options.dry_run or not run_options.device_completion_mode:
            return
        monitor = self.device_monitor
        if monitor is None:
            raise RuntimeError(
                "프린터 물리 완료 확인이 켜져 있지만 상태 연결이 없습니다. "
                "아래 [프린터 상태]에서 부서 ID·PIN을 연결해 주세요."
            )
        snapshot = monitor.latest_snapshot
        if not snapshot.authenticated or snapshot.stale:
            raise RuntimeError(
                "Canon 프린터 상태 연결을 아직 확인하지 못했습니다. "
                "아래 상태 표시가 연결됨으로 바뀐 뒤 다시 시작해 주세요."
            )
        if not snapshot.can_dispatch:
            raise RuntimeError(
                f"Canon 프린터가 전송 가능한 상태인지 확인하지 못했습니다: "
                f"{snapshot.status_text}. [프린터 상태]에서 다시 연결한 뒤 시작해 주세요."
            )

    def validate_print_blocks(
        self,
        blocks: list[PrintBlock],
        *,
        require_name_separator_labels: bool | None = None,
        queue_position_by_id: dict[int, int] | None = None,
    ) -> list[PrintBlock]:
        if not blocks:
            raise ValueError("작업 큐가 비어 있습니다. 먼저 블록을 추가해 주세요.")
        for block in blocks:
            if block.copies < 1:
                raise ValueError(f"부수가 잘못된 블록이 있습니다: {block.summary}")
        if require_name_separator_labels is None:
            variable = getattr(self, "name_separator_page_var", None)
            require_name_separator_labels = bool(variable is not None and variable.get())
        name_separator_paths: list[Path] = []
        if require_name_separator_labels:
            if queue_position_by_id is None:
                queue_blocks = getattr(self, "blocks", blocks)
                queue_position_by_id = {
                    id(queue_block): index
                    for index, queue_block in enumerate(queue_blocks, start=1)
                }
            missing = [
                str(queue_position_by_id.get(id(block), index))
                for index, block in enumerate(blocks, start=1)
                if block_uses_name_separator(block)
                and not normalize_name_separator_label(block.name_separator_label)
            ]
            if missing:
                raise ValueError(
                    "이름 구분지가 켜져 있지만 이름이 없는 블록이 있습니다: "
                    + ", ".join(missing)
                )
            name_separator_paths = [
                materialize_name_separator_pdf(block.name_separator_label, NAME_SEPARATOR_CACHE_DIR)
                for block in blocks
                if block_uses_name_separator(block)
            ]
        automatic_omr_paths = (
            [find_omr_pdf()]
            if any(block_requires_omr_sheet(block) for block in blocks)
            else []
        )
        self.preflight_pdf_paths(
            [item.path for block in blocks for item in block.files]
            + name_separator_paths
            + automatic_omr_paths
        )
        return list(blocks)

    def validate_print_jobs(self, jobs: list[PrintJob]) -> None:
        if not jobs:
            raise ValueError("재시도할 작업이 없습니다.")
        self.preflight_pdf_paths([job.item.path for job in jobs])

    def preflight_pdf_paths(self, pdf_paths: list[Path]) -> None:
        unique_paths = list(dict.fromkeys(pdf_paths))
        results_by_path: dict[Path, PdfPreflightResult] = {}
        uncached_paths: list[Path] = []
        for path in unique_paths:
            result = self.cached_preflight_result(path)
            if result is None:
                uncached_paths.append(path)
            else:
                results_by_path[path] = result
        if uncached_paths:
            fresh_report = preflight_pdfs(uncached_paths)
            self.remember_preflight_results(fresh_report.results)
            results_by_path.update({result.path: result for result in fresh_report.results})
        report = PdfPreflightReport(tuple(results_by_path[path] for path in unique_paths))
        if not report.is_printable:
            raise ValueError(format_preflight_report(report))

    def blocks_waiting_for_print(self) -> list[PrintBlock]:
        """Return only blocks that have not received a print command yet."""
        return waiting_blocks(
            self.blocks, self.block_status_by_id, getattr(self, "print_scheduled_block_ids", ()),
        )

    def validate_blocks(self, blocks: list[PrintBlock] | None = None) -> list[PrintBlock]:
        target_blocks = list(self.blocks if blocks is None else blocks)
        if int(self.queue_batch_size_var.get()) < 1:
            raise ValueError("호출 단위는 1 이상이어야 합니다.")
        if int(self.queue_refill_threshold_var.get()) < 0:
            raise ValueError("큐 기준은 0 이상이어야 합니다.")
        if self.separator_page_var.get():
            ensure_separator_pdf()
        self.validate_print_blocks(
            target_blocks,
            require_name_separator_labels=self.name_separator_page_var.get(),
        )
        return target_blocks















def run_application(*, error_handler=None, startup_report=None) -> None:
    set_windows_app_user_model_id()
    set_windows_dpi_awareness()
    root = TkinterDnD.Tk() if TkinterDnD is not None else Tk()
    root.startup_report = startup_report
    if error_handler is not None:
        root.report_callback_exception = error_handler
    try:
        if startup_report is not None:
            startup_report("catalog_and_ui_start")
        PrintManagerApp(root)
        if startup_report is not None:
            startup_report("ui_constructed")
            root.after_idle(lambda: startup_report("gui_ready", mapped=bool(root.winfo_viewable())))
            root.after(2000, lambda: startup_report("gui_stable", mapped=bool(root.winfo_viewable())))
        root.mainloop()
    finally:
        try:
            if root.winfo_exists():
                root.destroy()
        except tk.TclError:
            pass


def main() -> int:
    from unit_print_bootstrap import main as bootstrap_main
    return bootstrap_main()
