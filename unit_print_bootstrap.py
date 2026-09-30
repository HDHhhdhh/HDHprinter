"""Stdlib-only launcher: validate files, hold one instance, diagnose hidden failures."""

from __future__ import annotations

import ctypes
import importlib
import importlib.util
import os
import sys
import tempfile
import threading
import traceback
import uuid
from datetime import datetime
from pathlib import Path


APP_TITLE = "빠른 인쇄기"
APP_ROOT = Path(__file__).resolve().parent
_ENTRY_LOCK = threading.Lock()
# File existence only. PDF hashing and catalog validation belong to preflight/release checks.
STARTUP_REQUIRED_FILES = (
    "unit_print_bootstrap.py", "unit_print_instance.py", "unit_print_manager.py",
    "unit_print_domain.py", "unit_print_execution.py",
    "unit_print_paths.py", "unit_print_keyboard.py", "unit_print_text_shortcuts.py",
    "unit_print_gyopyeong.py", "unit_print_selected_exams.py", "unit_print_orders.py",
    "unit_print_server_catalog.py", "unit_print_materials.py", "unit_print_name_separator.py",
    "unit_print_pdf_printing.py", "unit_print_pdf_metadata.py", "unit_print_device_state.py",
    "unit_print_canon_status.py", "unit_print_device_credentials.py", "unit_print_device_monitor.py",
    "unit_print_dispatch_state.py", "unit_print_device_scheduler.py", "unit_print_device_ui.py",
    "unit_print_eta.py", "unit_print_resource_estimate.py", "unit_print_notifications.py",
    "unit_print_taskbar.py", "unit_print_printer_domain.py", "unit_print_printer_binding.py",
    "unit_print_printer_profiles.py", "unit_print_printer_setup.py", "unit_print_retry.py",
    "unit_print_progress.py", "unit_print_preparation.py", "unit_print_queue_state.py",
    "unit_print_queue_render.py", "unit_print_queue_recovery.py", "unit_print_recovery_history.py",
    "unit_print_history.py", "unit_print_history_events.py", "unit_print_toggle_help.py",
    "unit_print_test_catalog.py", "unit_print_textbooks.py", "unit_print_textbook_catalog.json",
    "unit_print_manager_requirements.txt", "unit_print_separator_a4.pdf",
)
STARTUP_REQUIRED_PACKAGES = ("tkinter", "ttkbootstrap", "tkinterdnd2", "pypdf", "reportlab", "comtypes")


class StartupValidationError(RuntimeError):
    def __init__(self, *, files=(), packages=(), python_unsupported=False) -> None:
        self.files = tuple(files)
        self.packages = tuple(packages)
        self.python_unsupported = bool(python_unsupported)
        super().__init__("빠른 인쇄기 실행 파일 또는 Python 환경이 불완전합니다.")


def validate_startup(app_root: Path, *, find_spec=None) -> None:
    find_spec = find_spec or importlib.util.find_spec
    files = tuple(name for name in STARTUP_REQUIRED_FILES if not (app_root / name).is_file())
    missing_packages = []
    for package in STARTUP_REQUIRED_PACKAGES:
        try:
            if find_spec(package) is None:
                missing_packages.append(package)
        except (ImportError, ValueError):
            missing_packages.append(package)
    python_unsupported = sys.version_info[:2] != (3, 12)
    if files or missing_packages or python_unsupported:
        raise StartupValidationError(
            files=files, packages=missing_packages, python_unsupported=python_unsupported
        )


def show_notice(message: str, *, error: bool = False) -> None:
    """Use a native app-owned message box even when tkinter itself cannot import."""
    if os.name == "nt":
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        from ctypes import wintypes
        user32.MessageBoxW.argtypes = (wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT)
        user32.MessageBoxW.restype = ctypes.c_int
        user32.MessageBoxW(None, message, APP_TITLE, 0x10 if error else 0x40)
    elif sys.stderr is not None:
        print(message, file=sys.stderr)


def safe_traceback(error: BaseException) -> str:
    """Stack locations and exception types only: never locals, config, or raw messages."""
    output = ["Traceback (most recent call last):"]
    visited = set()
    current = error
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        for frame in traceback.extract_tb(current.__traceback__):
            output.append(f'  File "{frame.filename}", line {frame.lineno}, in {frame.name}')
        if isinstance(current, SyntaxError):
            output.append(f'  Syntax location: {current.filename}:{current.lineno}:{current.offset}')
        output.append(type(current).__name__ + ": exception details withheld (may contain private data)")
        current = current.__cause__ if current.__cause__ is not None else (
            None if current.__suppress_context__ else current.__context__
        )
        if current is not None:
            output.append("Caused by:")
    if isinstance(error, StartupValidationError):
        output.append("Missing app files: " + ", ".join(error.files))
        output.append("Missing packages: " + ", ".join(error.packages))
    return "\n".join(output) + "\n"


def write_diagnostic(app_root: Path, error: BaseException, phase: str) -> Path | None:
    filename = datetime.now().strftime("%Y%m%d_%H%M%S_%f") + f"_{os.getpid()}_{uuid.uuid4().hex[:8]}.log"
    content = (
        "빠른 인쇄기 실행 진단\n"
        + f"Time: {datetime.now().astimezone().isoformat(timespec='seconds')}\n"
        + f"Phase: {phase}\nPython: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}\n"
        + safe_traceback(error)
    )
    candidates = [app_root / "output" / "unit_print_manager" / "startup_logs"]
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "QuickPrinter" / "startup_logs")
    candidates.append(Path(tempfile.gettempdir()) / "QuickPrinter" / "startup_logs")
    for directory in candidates:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / filename
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
            return path
        except OSError:
            continue
    return None


class ErrorReporter:
    def __init__(self, app_root: Path, notify=show_notice) -> None:
        self.app_root = app_root
        self.notify = notify

    def report(self, error: BaseException, phase: str) -> None:
        log_path = write_diagnostic(self.app_root, error, phase)
        message = "빠른 인쇄기를 시작하지 못했습니다." if phase != "callback" and phase != "thread" else "빠른 인쇄기에서 처리 오류가 발생했습니다."
        if isinstance(error, StartupValidationError):
            if error.files:
                message += f"\n\n실행에 필요한 파일 {len(error.files)}개가 없습니다:\n"
                message += "\n".join(error.files[:8])
                if len(error.files) > 8:
                    message += f"\n나머지 {len(error.files) - 8}개는 아래 진단 로그에서 확인할 수 있습니다."
                message += "\n검증된 백업 또는 배포본에서 해당 파일을 복원해 주세요."
            if error.packages:
                message += "\n\n필요한 Python 패키지: " + ", ".join(error.packages)
                message += "\n앱 폴더의 install_quick_printer.cmd를 실행해 환경을 복구해 주세요."
            if error.python_unsupported:
                message += "\nPython 3.12를 설치하거나 앱 설치 프로그램을 다시 실행해 주세요."
        elif phase == "instance" or phase == "instance_release":
            message += "\nWindows 중복 실행 잠금을 확인하지 못해 실행을 중단했습니다."
            message += "\n기존 창을 확인한 뒤 같은 Windows 사용자 권한으로 다시 실행해 주세요."
            message += f"\n오류 종류: {type(error).__name__}"
        else:
            message += f"\n오류 종류: {type(error).__name__}\n검증된 파일과 Python 환경을 복원한 뒤 다시 실행해 주세요."
        message += "\n\n진단 로그: " + (str(log_path) if log_path else "저장 실패 — 폴더 쓰기 권한을 확인해 주세요.")
        try:
            self.notify(message, error=True)
        except Exception:
            if sys.stderr is not None:
                print(message, file=sys.stderr)

    def callback_exception(self, error_type, error, tb) -> None:
        self.report(error.with_traceback(tb), "callback")


def main(*, app_root: Path | None = None, instance_factory=None, importer=None, validator=None, notify=None) -> int:
    app_root = Path(app_root) if app_root is not None else APP_ROOT
    notify = notify or show_notice
    if not _ENTRY_LOCK.acquire(blocking=False):
        notify("빠른 인쇄기가 이미 실행 중입니다.\n작업 표시줄에서 기존 빠른 인쇄기 창을 확인해 주세요.", error=False)
        return 0
    reporter = ErrorReporter(app_root, notify)
    lease = None
    previous_thread_hook = threading.excepthook
    phase = "instance"
    try:
        if instance_factory is None:
            if not (app_root / "unit_print_instance.py").is_file():
                raise StartupValidationError(files=("unit_print_instance.py",))
            from unit_print_instance import SingleInstance
            instance_factory = SingleInstance
        lease = instance_factory()
        if not lease.acquire():
            notify("빠른 인쇄기가 이미 실행 중입니다.\n작업 표시줄에서 기존 빠른 인쇄기 창을 확인해 주세요.", error=False)
            return 0
        phase = "validation"
        (validator or validate_startup)(app_root)
        phase = "import"
        manager = (importer or importlib.import_module)("unit_print_manager")
        threading.excepthook = lambda args: reporter.report(args.exc_value.with_traceback(args.exc_traceback), "thread")
        phase = "application"
        manager.run_application(error_handler=reporter.callback_exception)
        return 0
    except Exception as error:
        reporter.report(error, phase)
        return 1
    finally:
        threading.excepthook = previous_thread_hook
        try:
            if lease is not None:
                try:
                    lease.close()
                except Exception as error:
                    reporter.report(error, "instance_release")
        finally:
            _ENTRY_LOCK.release()


if __name__ == "__main__":
    raise SystemExit(main())
