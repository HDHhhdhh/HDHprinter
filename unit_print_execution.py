"""Print-job expansion, Windows pacing, and physical completion orchestration.

Side-effect dependencies enter through one explicit service bundle. The GUI owns
its widgets/state and supplies services; this module never imports the manager.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from tkinter import messagebox
from typing import Callable

from unit_print_domain import (
    ExamPdf, PrintBlock, PrintCancelled, PrintJob, PrintRunOptions,
    block_requires_omr_sheet, block_uses_name_separator,
    name_separator_group_count, name_separator_group_labels,
    resolve_exam_print_settings,
)
from unit_print_device_state import DeviceHealth, DeviceJobState
from unit_print_dispatch_state import RollingDispatchWindow
from unit_print_eta import estimate_remaining_seconds, estimated_sheets
from unit_print_history import HistoryWriteError
from unit_print_history_events import block_history_data, job_history_data
from unit_print_name_separator import normalize_name_separator_label
from unit_print_preparation import PrintPreparationService
from unit_print_queue_state import waiting_blocks

QUEUE_REFILL_TIMEOUT_SECONDS = 15 * 60
QUEUE_EMPTY_TIMEOUT_SECONDS = 2 * 60 * 60


@dataclass(frozen=True)
class PrintExecutionServices:
    app_title: str
    layout_cache_dir: Path
    name_separator_cache_dir: Path
    dispatch_spool_dir: Path
    device_acceptance_timeout_seconds: float
    preparation_factory: Callable[..., PrintPreparationService]
    materialize_four_up_pdf: Callable[..., Path]
    materialize_name_separator_pdf: Callable[..., Path]
    ensure_separator_pdf: Callable[[], Path]
    get_print_queue_count: Callable[[str], int]
    fingerprint_pdf: Callable[[Path], dict[str, object]]
    sumatra_enqueue_pdfs: Callable[..., int]
    get_pdf_page_count: Callable[[Path], int]
    new_dispatch_token: Callable[[], str]
    prepare_dispatch_copy: Callable[[Path, Path, str], Path]
    remove_dispatch_copy: Callable[..., bool]


@dataclass
class DeviceOutstandingJob:
    job: PrintJob
    target_printer: str
    print_path: Path
    dispatch_path: Path
    history_data: dict[str, object]
    submitted_at_monotonic: float
    estimated_sheet_count: int


class PrintExecutionMixin:
    def print_execution_services(self) -> PrintExecutionServices:
        """Implemented by the application boundary; no hidden global lookup."""
        raise NotImplementedError

    def raise_if_print_cancelled(self) -> None:
        if self.print_cancel_event.is_set():
            raise PrintCancelled("사용자가 남은 작업 전송을 중지했습니다.")

    def wait_for_queue_capacity(
        self,
        printer_name: str,
        queue_limit: int,
        timeout_seconds: float = QUEUE_REFILL_TIMEOUT_SECONDS,
    ) -> None:
        services = self.print_execution_services()
        queue_limit = max(1, queue_limit)
        last_logged_count: int | None = None
        started_at = time.monotonic()
        while True:
            self.raise_if_print_cancelled()
            count = services.get_print_queue_count(printer_name)
            if count < queue_limit:
                if last_logged_count is not None:
                    self.log(f"대기열 여유 확보: 현재 {count}개 / 제한 {queue_limit}개")
                return
            if count != last_logged_count:
                self.log(f"대기열 대기: 현재 {count}개 / 제한 {queue_limit}개")
                last_logged_count = count
            if time.monotonic() - started_at >= timeout_seconds:
                raise TimeoutError(
                    f"{printer_name} 대기열이 {timeout_seconds / 60:.0f}분 동안 비워지지 않았습니다."
                )
            if self.print_cancel_event.wait(2.0):
                self.raise_if_print_cancelled()

    def wait_for_queue_refill_window(
        self,
        printer_name: str,
        batch_size: int,
        refill_threshold: int,
        remaining_jobs: int,
        timeout_seconds: float = QUEUE_REFILL_TIMEOUT_SECONDS,
    ) -> int:
        services = self.print_execution_services()
        batch_size = max(1, batch_size)
        refill_threshold = max(0, refill_threshold)
        jobs_to_send = min(batch_size, remaining_jobs)
        last_logged_count: int | None = None
        started_at = time.monotonic()
        while True:
            self.raise_if_print_cancelled()
            count = services.get_print_queue_count(printer_name)
            if count <= refill_threshold:
                if last_logged_count is not None:
                    self.log(f"큐 채움 준비: 현재 {count}개, {jobs_to_send}개 전송")
                return jobs_to_send
            if count != last_logged_count:
                self.log(f"큐 대기: 현재 {count}개 / 기준 {refill_threshold}개 이하")
                last_logged_count = count
            if time.monotonic() - started_at >= timeout_seconds:
                raise TimeoutError(
                    f"{printer_name} 대기열이 {timeout_seconds / 60:.0f}분 동안 "
                    f"{refill_threshold}개 이하로 줄지 않았습니다."
                )
            if self.print_cancel_event.wait(1.0):
                self.raise_if_print_cancelled()

    def wait_for_print_queue_empty(
        self,
        printer_name: str,
        stable_seconds: float = 4.0,
        timeout_seconds: float = QUEUE_EMPTY_TIMEOUT_SECONDS,
    ) -> bool:
        services = self.print_execution_services()
        zero_since: float | None = None
        last_logged_count: int | None = None
        started_at = time.monotonic()
        while True:
            self.raise_if_print_cancelled()
            try:
                count = services.get_print_queue_count(printer_name)
            except Exception as exc:
                self.log(f"프린터 대기열 최종 확인 실패: {exc}")
                return False
            if count == 0:
                if zero_since is None:
                    zero_since = time.perf_counter()
                    self.log("프린터 대기열 0개 감지. 안정 확인 중...")
                elapsed = time.perf_counter() - zero_since
                self.set_current_work(f"현재 작업: 전송 완료 / 인쇄 완료 확인 중 · {elapsed:.0f}/{stable_seconds:.0f}초")
                if elapsed >= stable_seconds:
                    self.log("프린터 대기열 비움 확인 완료.")
                    return True
            else:
                zero_since = None
                if count != last_logged_count:
                    self.log(f"전송 완료 / 인쇄 중: 프린터 대기열 {count}개")
                    last_logged_count = count
                self.set_current_work(f"현재 작업: 전송 완료 / 인쇄 중 · 대기열 {count}개")
            if time.monotonic() - started_at >= timeout_seconds:
                raise TimeoutError(
                    f"{printer_name} 인쇄 완료를 {timeout_seconds / 60:.0f}분 동안 확인하지 못했습니다."
                )
            if self.print_cancel_event.wait(2.0):
                self.raise_if_print_cancelled()

    def build_print_jobs(
        self,
        blocks: list[PrintBlock],
        include_separator_pages: bool = False,
        include_name_separator_pages: bool = False,
        *,
        start_block_index: int = 1,
        block_total: int | None = None,
        preceding_name_separator_label: object = "",
    ) -> list[PrintJob]:
        services = self.print_execution_services()
        jobs: list[PrintJob] = []
        if block_total is None:
            block_total = start_block_index + len(blocks) - 1
        separator_item: ExamPdf | None = None
        if include_separator_pages:
            separator_item = ExamPdf(0, "구분지 고정 표지", services.ensure_separator_pdf())
        automatic_omr_item = (
            self.make_automatic_omr_item()
            if any(block_requires_omr_sheet(block) for block in blocks)
            else None
        )
        grouped_name_labels = (
            name_separator_group_labels(blocks, preceding_name_separator_label)
            if include_name_separator_pages
            else (None,) * len(blocks)
        )
        for offset, block in enumerate(blocks):
            block_index = start_block_index + offset
            name_label = grouped_name_labels[offset]
            if include_name_separator_pages and name_label is not None:
                name_separator_item = ExamPdf(
                    0,
                    f"이름 구분지 - {name_label}",
                    services.materialize_name_separator_pdf(name_label, services.name_separator_cache_dir),
                )
                jobs.append(
                    PrintJob(
                        block_index=block_index,
                        block_total=block_total,
                        block_uid=id(block),
                        block_summary=block.summary,
                        item=name_separator_item,
                        copies=1,
                        is_separator=True,
                        is_name_separator=True,
                    )
                )
            if automatic_omr_item is not None and block_requires_omr_sheet(block):
                jobs.append(
                    PrintJob(
                        block_index=block_index,
                        block_total=block_total,
                        block_uid=id(block),
                        block_summary=block.summary,
                        item=automatic_omr_item,
                        copies=1,
                        printer_override=automatic_omr_item.printer_override,
                        pages_per_sheet=1,
                        is_omr=True,
                    )
                )
            if block.bundled:
                for bundle_no in range(1, block.copies + 1):
                    for item in block.files:
                        printer_override, pages_per_sheet = resolve_exam_print_settings(block, item)
                        jobs.append(
                            PrintJob(
                                block_index=block_index,
                                block_total=block_total,
                                block_uid=id(block),
                                block_summary=block.summary,
                                item=item,
                                copies=1,
                                bundle_no=bundle_no,
                                bundle_total=block.copies,
                                printer_override=printer_override,
                                pages_per_sheet=pages_per_sheet,
                            )
                        )
            else:
                for item in block.files:
                    printer_override, pages_per_sheet = resolve_exam_print_settings(block, item)
                    jobs.append(
                        PrintJob(
                            block_index=block_index,
                            block_total=block_total,
                            block_uid=id(block),
                            block_summary=block.summary,
                            item=item,
                            copies=block.copies,
                            printer_override=printer_override,
                            pages_per_sheet=pages_per_sheet,
                        )
                    )
            if separator_item is not None:
                jobs.append(
                    PrintJob(
                        block_index=block_index,
                        block_total=block_total,
                        block_uid=id(block),
                        block_summary=block.summary,
                        item=separator_item,
                        copies=1,
                        is_separator=True,
                    )
                )
        snapshots = {id(block): block_history_data(block) for block in blocks}
        return [replace(job, history_block=snapshots[job.block_uid]) for job in jobs]

    def initialize_active_print_queue(
        self,
        blocks: list[PrintBlock],
        include_separator_pages: bool,
        include_name_separator_pages: bool = False,
    ) -> list[PrintJob]:
        jobs = self.build_print_jobs(
            blocks,
            include_separator_pages=include_separator_pages,
            include_name_separator_pages=include_name_separator_pages,
            start_block_index=1,
            block_total=len(blocks),
        )
        with self.print_state_lock:
            self.print_pending_jobs = list(jobs)
            self.print_scheduled_block_ids = {id(block) for block in blocks}
            self.print_started_block_ids = set()
            self.print_next_block_index = len(blocks) + 1
            self.print_include_separator_pages = include_separator_pages
            self.print_include_name_separator_pages = include_name_separator_pages
            self.print_last_name_separator_label = (
                normalize_name_separator_label(blocks[-1].name_separator_label)
                if include_name_separator_pages
                and blocks
                and block_uses_name_separator(blocks[-1])
                else ""
            )
            self.print_accepting_appends = True
            self.active_print_block_uid = None
            self.print_queue_revision = getattr(self, "print_queue_revision", 0) + 1
        self.mark_blocks_status(blocks, "예약")
        self.set_current_work("현재 작업: 전송 준비 중")
        return jobs

    def active_print_total_jobs(self) -> int:
        with self.print_state_lock:
            return len(self.print_pending_jobs)

    def active_print_batch(self, job_index: int, batch_count: int) -> tuple[list[PrintJob], int]:
        with self.print_state_lock:
            total_jobs = len(self.print_pending_jobs)
            if job_index >= total_jobs:
                return [], total_jobs
            end_index = min(total_jobs, job_index + batch_count)
            return list(self.print_pending_jobs[job_index:end_index]), total_jobs

    def append_new_blocks_to_active_print(self) -> None:
        services = self.print_execution_services()
        with self.print_state_lock:
            accepting = self.print_accepting_appends
            scheduled_ids = set(self.print_scheduled_block_ids)
            include_name_separator_pages = self.print_include_name_separator_pages
            include_separator_pages = self.print_include_separator_pages
            preceding_name_separator_label = self.print_last_name_separator_label
        if not accepting:
            messagebox.showinfo(services.app_title, "현재 전송이 마무리 중입니다. 잠시 후 다시 눌러 새 큐로 시작해 주세요.")
            return

        new_blocks = waiting_blocks(self.blocks, self.block_status_by_id, scheduled_ids)
        if not new_blocks:
            messagebox.showinfo(services.app_title, "현재 인쇄 작업에 추가할 새 블록이 없습니다.")
            return

        try:
            self.validate_print_blocks(
                new_blocks,
                require_name_separator_labels=include_name_separator_pages,
            )
            run_options = self.print_run_options
            if run_options is None:
                raise RuntimeError("현재 인쇄 실행 설정을 찾을 수 없습니다.")
            if not run_options.dry_run:
                available_printers = self.cached_printer_inventory()
                self.ensure_orientation_profiles_ready(
                    run_options.printer_name,
                    available_printers,
                )
                self.ensure_block_printers_ready(new_blocks, available_printers)
                verified_run_options = self.ensure_device_printer_binding(
                    run_options, blocks=new_blocks,
                    include_separator_pages=include_separator_pages,
                    include_name_separator_pages=include_name_separator_pages,
                )
            else:
                verified_run_options = run_options
        except Exception as exc:
            messagebox.showwarning(services.app_title, str(exc))
            return

        total_files = sum(block.file_count for block in new_blocks)
        total_sets = sum(block.file_count * block.copies for block in new_blocks)
        name_separator_count = (
            name_separator_group_count(new_blocks, preceding_name_separator_label)
            if include_name_separator_pages
            else 0
        )
        name_separator_text = (
            f"\n이름 구분지 {name_separator_count}장을 이름 묶음 앞에 추가합니다."
            if name_separator_count
            else ""
        )
        automatic_omr_count = sum(block_requires_omr_sheet(block) for block in new_blocks)
        automatic_omr_text = (
            f"\nOMR 내신 {automatic_omr_count}장을 대상 블록 앞에 자동 추가합니다."
            if automatic_omr_count
            else ""
        )
        ok = messagebox.askyesno(
            services.app_title,
            f"새 블록 {len(new_blocks)}개, PDF {total_files}개, 총 {total_sets}세트를 "
            f"현재 인쇄 큐 뒤에 이어서 추가할까요?{name_separator_text}{automatic_omr_text}",
        )
        if not ok:
            return

        start_block_index = 0
        include_separator_pages = False
        with self.print_state_lock:
            if not self.print_accepting_appends:
                still_accepting = False
            else:
                still_accepting = True
                start_block_index = self.print_next_block_index
                include_separator_pages = self.print_include_separator_pages
                include_name_separator_pages = self.print_include_name_separator_pages
                preceding_name_separator_label = self.print_last_name_separator_label
        if not still_accepting:
            messagebox.showinfo(services.app_title, "현재 전송이 마무리 중입니다. 잠시 후 다시 눌러 새 큐로 시작해 주세요.")
            return
        jobs = self.build_print_jobs(
            new_blocks,
            include_separator_pages=include_separator_pages,
            include_name_separator_pages=include_name_separator_pages,
            start_block_index=start_block_index,
            block_total=start_block_index + len(new_blocks) - 1,
            preceding_name_separator_label=preceding_name_separator_label,
        )
        try:
            self.record_history(
                "append_requested", required=True,
                blocks=[block_history_data(block) for block in new_blocks],
                job_ids=[job.job_id for job in jobs],
            )
        except HistoryWriteError as error:
            messagebox.showwarning(services.app_title, str(error))
            return
        total_jobs = 0
        with self.print_state_lock:
            if not self.print_accepting_appends or self.print_run_options is not run_options:
                still_accepting = False
            else:
                still_accepting = True
                self.print_run_options = verified_run_options
                self.print_pending_jobs.extend(jobs)
                self.print_scheduled_block_ids.update(id(block) for block in new_blocks)
                self.print_next_block_index += len(new_blocks)
                if include_name_separator_pages and new_blocks:
                    self.print_last_name_separator_label = (
                        normalize_name_separator_label(new_blocks[-1].name_separator_label)
                        if block_uses_name_separator(new_blocks[-1])
                        else ""
                    )
                total_jobs = len(self.print_pending_jobs)
                self.print_queue_revision = getattr(self, "print_queue_revision", 0) + 1
        if not still_accepting:
            messagebox.showinfo(services.app_title, "현재 전송이 마무리 중입니다. 잠시 후 다시 눌러 새 큐로 시작해 주세요.")
            return

        self.mark_blocks_status(new_blocks, "추가됨")
        self.record_history("jobs_added", job_ids=[job.job_id for job in jobs], total_jobs=total_jobs)
        self.save_current_config()
        self.log(f"실행 중 큐 추가: 블록 {len(new_blocks)}개, 전송 {len(jobs)}건, 누적 {total_jobs}건")
        self.set_status("인쇄 전송 중")
        self.set_current_work(f"현재 작업: 새 블록 {len(new_blocks)}개가 실행 큐 뒤에 추가됨")
        self.queue_status_var.set(f"실행 중 추가됨 / 누적 전송 {total_jobs}건")

    def start_print_thread(self) -> None:
        services = self.print_execution_services()
        if getattr(self, "group_build_pending", False):
            self.queue_status_var.set("묶음 준비가 끝난 뒤 인쇄를 시작해 주세요.")
            return
        if self.is_printing:
            self.append_new_blocks_to_active_print()
            return
        self.sync_retry_queue()
        if self.retry_batch is not None:
            self.retry_remaining_jobs()
            return
        waiting_blocks = self.blocks_waiting_for_print()
        if not waiting_blocks:
            messagebox.showinfo(
                services.app_title,
                "새로 인쇄할 대기 블록이 없습니다.\n"
                "이미 인쇄 명령이 들어간 블록은 자동으로 제외됩니다.",
            )
            return
        previous_status = self.app_status_var.get()
        previous_work = self.current_work_var.get()
        previous_queue_status = self.queue_status_var.get()
        self.set_status("인쇄 준비 중")
        self.set_current_work("현재 작업: PDF와 프린터 확인 중")
        self.queue_status_var.set("인쇄 준비 중 · PDF 및 프린터 확인")
        self.root.update_idletasks()
        try:
            blocks = self.validate_blocks(waiting_blocks)
            run_options = self.current_print_run_options()
            self.ensure_device_monitor_ready_for_print(run_options)
            if not run_options.dry_run:
                available_printers = self.cached_printer_inventory()
                self.ensure_orientation_profiles_ready(
                    run_options.printer_name,
                    available_printers,
                )
                self.ensure_block_printers_ready(blocks, available_printers)
                if run_options.engine != "sumatra":
                    raise RuntimeError("방향별 스테이플 인쇄는 SumatraPDF 방식에서만 지원합니다.")
                run_options = self.ensure_device_printer_binding(
                    run_options, blocks=blocks,
                    include_separator_pages=bool(self.separator_page_var.get()),
                    include_name_separator_pages=bool(self.name_separator_page_var.get()),
                )
        except Exception as error:
            self.set_status(previous_status)
            self.set_current_work(previous_work)
            self.queue_status_var.set(previous_queue_status)
            messagebox.showwarning(services.app_title, str(error))
            return

        self.save_current_config()
        if not run_options.dry_run:
            self.complete_resource_estimate_for_confirmation()
            total_files = sum(block.file_count for block in blocks)
            total_sets = sum(block.file_count * block.copies for block in blocks)
            separator_count = len(blocks) if self.separator_page_var.get() else 0
            separator_text = f"\n구분지 {separator_count}장을 블록 뒤에 추가합니다." if separator_count else ""
            name_separator_count = (
                name_separator_group_count(blocks)
                if self.name_separator_page_var.get()
                else 0
            )
            name_separator_text = (
                f"\n이름 구분지 {name_separator_count}장을 이름 묶음 앞에 추가합니다."
                if name_separator_count
                else ""
            )
            automatic_omr_count = sum(block_requires_omr_sheet(block) for block in blocks)
            automatic_omr_text = (
                f"\nOMR 내신 {automatic_omr_count}장을 대상 블록 앞에 자동 추가합니다."
                if automatic_omr_count
                else ""
            )
            resource_text = self.resource_estimate_confirmation_text()
            ok = messagebox.askyesno(
                services.app_title,
                f"블록 {len(blocks)}개, PDF {total_files}개, 총 {total_sets}세트를 순서대로 인쇄할까요?"
                f"{name_separator_text}{automatic_omr_text}{separator_text}{resource_text}",
            )
            if not ok:
                self.set_status(previous_status)
                self.set_current_work(previous_work)
                self.queue_status_var.set(previous_queue_status)
                return

        include_separator_pages = self.separator_page_var.get()
        include_name_separator_pages = self.name_separator_page_var.get()
        try:
            self.begin_history_run(run_options)
        except HistoryWriteError as error:
            self.set_status(previous_status)
            self.set_current_work(previous_work)
            self.queue_status_var.set(previous_queue_status)
            messagebox.showwarning(services.app_title, str(error))
            return
        self.initialize_active_print_queue(
            blocks,
            include_separator_pages=include_separator_pages,
            include_name_separator_pages=include_name_separator_pages,
        )
        self.retry_batch = None
        self.undo_stack = []
        self.redo_stack = []
        self.begin_taskbar_print_run(self.active_print_total_jobs())
        with self.print_state_lock:
            self.print_run_options = run_options
        self.print_cancel_event.clear()
        self.is_printing = True
        self.refresh_print_action_buttons()
        self.print_thread = threading.Thread(
            target=self.print_blocks,
            args=(blocks, run_options),
            daemon=True,
        )
        self.print_thread.start()

    def finish_active_print_run(self) -> None:
        """Reset shared worker state after completion, stop, or failure."""

        with self.print_state_lock:
            self.print_accepting_appends = False
            self.print_pending_jobs = []
            self.print_scheduled_block_ids = set()
            self.print_started_block_ids = set()
            self.print_next_block_index = 1
            self.print_last_name_separator_label = ""
            self.active_print_block_uid = None
            self.print_run_options = None
        self.device_hold_active = False
        self.device_hold_message = ""
        resume_event = getattr(self, "device_resume_event", None)
        if resume_event is not None:
            resume_event.set()
        self.is_printing = False
        self.print_thread = None
        self.history_run_id = None
        self.root.after(0, self.refresh_print_action_buttons)

    def print_blocks_device_rolling(
        self,
        blocks: list[PrintBlock],
        run_options: PrintRunOptions,
    ) -> None:
        """Keep a bounded FIFO of tokenized jobs inside the physical printer."""
        services = self.print_execution_services()

        monitor = self.device_monitor
        window = RollingDispatchWindow(run_options.device_window_size)
        outstanding: dict[str, DeviceOutstandingJob] = {}
        acknowledged_tokens: set[str] = set()
        completed_job_ids: set[str] = set()
        sheet_estimates: dict[str, int] = {}
        display_run_id = getattr(self, "history_run_id", None)
        preparation: PrintPreparationService | None = None
        fingerprints: dict[tuple[Path, int, int], dict[str, object]] = {}
        used_printers: set[str] = set()
        job_index = 0
        submitted_count = 0
        uncertain_job_ids: list[str] = []
        completed_count = 0
        phase = "rolling_preparation"
        paused_for_device = False
        full_snapshot_pause_active = False
        active_block_uid: int | None = None

        def sheet_count(job: PrintJob) -> int:
            cached = sheet_estimates.get(job.job_id)
            if cached is not None:
                return cached
            try:
                pages = services.get_pdf_page_count(job.item.path)
                if job.pages_per_sheet == 4:
                    pages = (pages + 3) // 4
                value = estimated_sheets(pages, job.copies, run_options.duplex)
            except (OSError, ValueError):
                value = max(1, job.copies)
            sheet_estimates[job.job_id] = value
            return value

        def update_progress(total_jobs: int) -> None:
            with self.print_state_lock:
                pending = list(self.print_pending_jobs[job_index:])
            remaining_sheets = sum(meta.estimated_sheet_count for meta in outstanding.values())
            remaining_sheets += sum(sheet_count(job) for job in pending)
            eta = estimate_remaining_seconds(
                remaining_sheets,
                self.eta_print_rate_samples(),
                remaining_jobs=len(outstanding) + len(pending),
            )
            unsent = max(0, total_jobs - job_index)
            status = (
                f"실제 완료 {completed_count}/{total_jobs} · "
                f"프린터 내부 {window.outstanding_count}건 · 미전송 {unsent}건 · "
                f"예상 {eta.display_text}"
            )
            if paused_for_device:
                reason = self.device_hold_message or "프린터 오류가 아직 해제되지 않았습니다"
                self.set_current_work(
                    f"현재 작업: {reason} · 해결 후 이어서 눌러 주세요 · {status}"
                )
            else:
                self.set_current_work(f"현재 작업: {status}")
            self.set_taskbar_print_progress(
                completed_count,
                total_jobs,
                run_id=display_run_id,
            )
            if active_block_uid is not None:
                self.mark_block_status(active_block_uid, "진행 중", status)

        def mark_completed_blocks() -> None:
            with self.print_state_lock:
                current_jobs = tuple(self.print_pending_jobs)
            job_ids_by_block_uid: dict[int, set[str]] = {}
            for job in current_jobs:
                job_ids_by_block_uid.setdefault(job.block_uid, set()).add(job.job_id)
            for block in self.blocks:
                block_job_ids = job_ids_by_block_uid.get(id(block))
                if block_job_ids and block_job_ids <= completed_job_ids:
                    self.mark_block_status(id(block), "완료", "실제 인쇄 완료")

        try:
            if monitor is None:
                raise RuntimeError("프린터 상태 모니터가 연결되지 않았습니다.")
            preparation = services.preparation_factory(
                services.layout_cache_dir,
                four_up=services.materialize_four_up_pdf,
                scheduler=getattr(self, "pdf_work_scheduler", None),
            )
            self.device_resume_event.set()
            total_jobs = self.active_print_total_jobs()
            planned_jobs, _ = self.active_print_batch(0, total_jobs)
            self.assert_device_binding_for_dispatch(run_options, monitor)
            self.record_history(
                "run_prepared",
                required=True,
                dry_run=False,
                physical_completion_tracking=True,
                dispatch_policy="rolling_device_window",
                dispatch_window_size=window.max_outstanding,
                blocks=[block_history_data(block) for block in blocks],
                jobs=[
                    job_history_data(job, self.route_job_printer(job, run_options.printer_name))
                    for job in planned_jobs
                ],
            )
            self.set_status("실제 인쇄 확인 중")
            self.log(
                f"롤링 선전송 창 {window.max_outstanding}건: 완료 1건 확인마다 "
                "다음 대기 작업 1건을 보충합니다."
            )
            # Do not start from an idle-poll snapshot that may already be up
            # to several seconds old.  The first dispatch requires a fresh
            # device observation made after this run started.
            initial_observed_at = monitor.latest_snapshot.observed_at
            monitor.refresh_now()
            initial_snapshot = monitor.wait_for_change(initial_observed_at, 30.0)
            if initial_snapshot.observed_at == initial_observed_at:
                raise TimeoutError(
                    "인쇄 시작 전 Canon 프린터의 최신 상태를 확인하지 못했습니다."
                )
            last_observed_at = initial_snapshot.observed_at

            while True:
                self.raise_if_print_cancelled()
                self.assert_device_binding_for_dispatch(run_options, monitor)
                snapshot = monitor.latest_snapshot
                total_jobs = self.active_print_total_jobs()
                lookahead, _ = self.active_print_batch(job_index, 3)
                preparation.prefetch_jobs(lookahead, limit=2)

                phase = "device_reconciliation"
                reconciled = window.reconcile(snapshot)
                for entry in reconciled.completed:
                    meta = outstanding.pop(entry.token, None)
                    if meta is None:
                        continue
                    device_job = snapshot.find_job(entry.token)
                    if device_job is None:
                        continue
                    completed_count += 1
                    completed_job_ids.add(meta.job.job_id)
                    elapsed_seconds = max(0.0, time.monotonic() - meta.submitted_at_monotonic)
                    self.learn_completed_device_job_rate(
                        device_job,
                        meta.estimated_sheet_count,
                    )
                    self.record_history(
                        "job_physically_completed",
                        required=True,
                        receipt_no=device_job.receipt_no,
                        device_result=device_job.result,
                        device_end_code=device_job.end_code,
                        device_source_pages=device_job.source_pages,
                        device_output_pages=device_job.output_pages,
                        elapsed_seconds=round(elapsed_seconds, 3),
                        **meta.history_data,
                    )
                    services.remove_dispatch_copy(
                        meta.dispatch_path, spool_dir=services.dispatch_spool_dir,
                    )
                    self.log(
                        f"  실제 완료 {completed_count}/{total_jobs}: {meta.job.item.path.name} "
                        f"(접수번호 {device_job.receipt_no or '확인됨'})"
                    )
                if reconciled.completed:
                    mark_completed_blocks()

                if reconciled.failed:
                    failed_entry = reconciled.failed[0]
                    failed_meta = outstanding.get(failed_entry.token)
                    failed_job = snapshot.find_job(failed_entry.token)
                    label = failed_meta.job.item.path.name if failed_meta is not None else failed_entry.job_id
                    result = failed_job.result if failed_job is not None else "알 수 없음"
                    raise RuntimeError(f"{label}의 프린터 작업 결과가 실패입니다: {result}")

                device_fault = not snapshot.can_dispatch or bool(reconciled.held)
                if device_fault and not paused_for_device:
                    paused_for_device = True
                    self.device_resume_event.clear()
                    self.enter_device_hold(snapshot)
                    self.record_history(
                        "device_dispatch_held",
                        outstanding_tokens=[entry.token for entry in window.entries],
                        reason=snapshot.status_text,
                    )

                if paused_for_device:
                    healthy = snapshot.can_dispatch and not reconciled.held
                    if healthy and self.device_resume_event.is_set():
                        # Reconciliation above always happens before refilling.
                        paused_for_device = False
                        self.device_hold_active = False
                        self.device_hold_message = ""
                        self.log(
                            f"장비 상태 재조정 완료: 내부 {window.outstanding_count}건을 유지한 채 보충 재개"
                        )
                        self.record_history(
                            "device_dispatch_resumed",
                            outstanding_tokens=[entry.token for entry in window.entries],
                        )
                    else:
                        update_progress(total_jobs)
                        monitor.refresh_now()
                        last_observed_at = monitor.wait_for_change(last_observed_at, 0.5).observed_at
                        continue

                for token, meta in tuple(outstanding.items()):
                    if (
                        time.monotonic() - meta.submitted_at_monotonic
                        > run_options.device_completion_timeout_seconds
                    ):
                        raise TimeoutError(
                            f"{meta.job.item.path.name}의 실제 완료를 확인하지 못했습니다. "
                            "자동 재전송하지 않습니다."
                        )

                phase = "rolling_refill"
                known_tokens = {entry.token.casefold() for entry in window.entries}
                external_active_count = sum(
                    1
                    for active_job in snapshot.active_jobs
                    if not active_job.dispatch_token
                    or active_job.dispatch_token.casefold() not in known_tokens
                )
                # Windows queues that share one physical printer do not share
                # a reliable FIFO boundary.  Every registered entry reaches
                # this set only after Canon itself exposes its exact token.
                device_acknowledged_prior_jobs = all(
                    entry.token.casefold() in acknowledged_tokens
                    for entry in window.entries
                )
                monitor.pause_full_snapshots()
                full_snapshot_pause_active = True
                while (
                    device_acknowledged_prior_jobs
                    and window.slots_available > external_active_count
                ):
                    self.raise_if_print_cancelled()
                    total_jobs = self.active_print_total_jobs()
                    if job_index >= total_jobs:
                        break
                    latest = monitor.latest_snapshot
                    if not latest.can_dispatch:
                        break
                    jobs, total_jobs = self.active_print_batch(job_index, 1)
                    if not jobs:
                        break
                    job = jobs[0]
                    target_printer = self.route_job_printer(job, run_options.printer_name)
                    self.assert_device_binding_for_dispatch(run_options, monitor, target_printer)
                    if not self.claim_active_print_job(job, job_index):
                        continue
                    if job.block_uid != active_block_uid:
                        active_block_uid = job.block_uid
                        self.mark_block_status(job.block_uid, "진행 중", "선전송 준비 중")

                    print_path = preparation.resolve_job(job)

                    dispatch_token = services.new_dispatch_token()
                    dispatch_path = services.prepare_dispatch_copy(
                        print_path,
                        services.dispatch_spool_dir,
                        dispatch_token,
                    )
                    # Audit the immutable tokenized copy passed to Sumatra,
                    # rather than a source that can change after copying.
                    job_data = job_history_data(job, target_printer, dispatch_path)
                    job_data["prepared_pdf_path"] = str(print_path)
                    if getattr(self, "print_history", None) is not None:
                        for prefix, path in (("pdf", job.item.path), ("printed_pdf", dispatch_path)):
                            stat = path.stat()
                            key = (path, stat.st_size, stat.st_mtime_ns)
                            if key not in fingerprints:
                                fingerprints[key] = services.fingerprint_pdf(path)
                            job_data.update(
                                {
                                    f"{prefix}_{name}": value
                                    for name, value in fingerprints[key].items()
                                }
                            )
                    job_data.update(
                        {
                            "attempt_id": str(uuid.uuid4()),
                            "dispatch_token": dispatch_token,
                            "dispatch_window_size": window.max_outstanding,
                            "estimated_sheets": sheet_count(job),
                        }
                    )
                    self.record_history("job_attempt", required=True, **job_data)
                    self.assert_device_binding_for_dispatch(run_options, monitor, target_printer)
                    submitted_at = time.monotonic()
                    phase = "dispatch"
                    try:
                        call_count = services.sumatra_enqueue_pdfs(
                            run_options.sumatra_path,
                            [dispatch_path],
                            target_printer,
                            copies=job.copies,
                            duplex=run_options.duplex,
                        )
                        if call_count != 1:
                            raise RuntimeError(
                                f"SumatraPDF가 {job.item.path.name} 전송을 확인하지 못했습니다."
                            )
                    except Exception as error:
                        job_index += 1
                        uncertain_job_ids.append(job.job_id)
                        phase = "dispatch_uncertain"
                        self.record_history(
                            "job_unconfirmed",
                            error=str(error),
                            automatic_retry_allowed=False,
                            **job_data,
                        )
                        raise RuntimeError(
                            f"{job.item.path.name} 전송 결과를 확정할 수 없습니다. "
                            "자동 재전송하지 않습니다. Canon 작업 이력을 확인해 주세요."
                        ) from error

                    # From this point the job is submitted and can never return
                    # to automatic retry.  Register it before any further send.
                    job_index += 1
                    submitted_count += 1
                    window.register(job.job_id, dispatch_token)
                    outstanding[dispatch_token] = DeviceOutstandingJob(
                        job=job,
                        target_printer=target_printer,
                        print_path=print_path,
                        dispatch_path=dispatch_path,
                        history_data=job_data,
                        submitted_at_monotonic=submitted_at,
                        estimated_sheet_count=sheet_count(job),
                    )
                    used_printers.add(target_printer)
                    phase = "submission_history"
                    self.record_history("job_submitted", required=True, **job_data)
                    self.log(
                        f"  선전송 {job_index}/{total_jobs} · 내부 {window.outstanding_count}/"
                        f"{window.max_outstanding}: {job.item.path.name} / {dispatch_token}"
                    )
                    # Use Canon's lightweight active-job endpoint for the
                    # ordering barrier.  A full portal/supplies/history read
                    # takes several seconds and used to halve throughput when
                    # it was repeated after every submitted document.
                    phase = "device_acceptance"
                    acceptance = monitor.wait_for_job_acceptance(
                        dispatch_token,
                        cancel_event=self.print_cancel_event,
                        timeout_seconds=services.device_acceptance_timeout_seconds,
                    )
                    if acceptance.cancelled:
                        self.raise_if_print_cancelled()
                        raise PrintCancelled("프린터 상태 확인이 중지되었습니다.")
                    if acceptance.timed_out or acceptance.job is None:
                        raise TimeoutError(
                            f"{job.item.path.name} 전송 후 Canon 장비 접수를 확인하지 못했습니다. "
                            "이미 전송했을 수 있어 자동 재전송하지 않습니다."
                        )

                    acknowledged_tokens.add(dispatch_token.casefold())
                    device_job = acceptance.job
                    if device_job.state == DeviceJobState.FAILED:
                        raise RuntimeError(
                            f"{job.item.path.name}의 Canon 접수 결과가 실패입니다: "
                            f"{device_job.result or device_job.end_code or '알 수 없음'}"
                        )
                    if device_job.state == DeviceJobState.HELD:
                        held_message = (
                            f"{job.item.path.name} 작업이 Canon 장비에서 보류되었습니다."
                        )
                        held_snapshot = replace(
                            monitor.latest_snapshot,
                            health=DeviceHealth.HELD,
                            status_text=held_message,
                            errors=(held_message,),
                        )
                        paused_for_device = True
                        self.device_resume_event.clear()
                        self.enter_device_hold(held_snapshot)
                        self.record_history(
                            "device_dispatch_held",
                            outstanding_tokens=[entry.token for entry in window.entries],
                            reason=held_message,
                        )
                        monitor.refresh_now()
                        break
                    phase = "rolling_refill"

                monitor.resume_full_snapshots()
                full_snapshot_pause_active = False

                total_jobs = self.active_print_total_jobs()
                update_progress(total_jobs)
                if job_index >= total_jobs and window.outstanding_count == 0:
                    with self.print_state_lock:
                        if len(self.print_pending_jobs) <= job_index:
                            self.print_accepting_appends = False
                            break
                monitor.refresh_now()
                last_observed_at = monitor.wait_for_change(last_observed_at, 0.5).observed_at

            mark_completed_blocks()
            self.set_status("실제 인쇄 완료")
            self.set_current_work(
                f"현재 작업: 실제 완료 {completed_count}/{completed_count} · "
                "프린터 내부 0건 · 미전송 0건"
            )
            self.record_history(
                "run_finished",
                submitted_jobs=submitted_count,
                completed_jobs=completed_count,
                outcome="device_jobs_completed",
                physical_output_verified=True,
                dispatch_window_size=window.max_outstanding,
                printers=sorted(used_printers),
            )
        except PrintCancelled as exc:
            total_jobs = self.active_print_total_jobs()
            if job_index < total_jobs:
                self.preserve_retry_from_failure(job_index)
            if active_block_uid is not None and job_index < total_jobs:
                self.mark_block_status(active_block_uid, "중지됨")
            self.log(str(exc))
            self.set_status("전송 중지됨" if job_index < total_jobs else "완료 확인 중지됨")
            self.set_current_work(
                f"현재 작업: 내부 {window.outstanding_count}건은 재전송하지 않음 · "
                f"미전송 {max(0, total_jobs - job_index)}건"
            )
            self.record_history(
                "run_cancelled",
                phase=phase,
                submitted_jobs=submitted_count,
                uncertain_jobs=len(uncertain_job_ids),
                uncertain_job_ids=uncertain_job_ids,
                completed_jobs=completed_count,
                outstanding_tokens=[entry.token for entry in window.entries],
                remaining_jobs=max(0, total_jobs - job_index),
                error=str(exc),
            )
        except Exception as exc:
            total_jobs = self.active_print_total_jobs()
            if job_index < total_jobs:
                self.preserve_retry_from_failure(job_index)
            if active_block_uid is not None:
                self.mark_block_status(active_block_uid, "오류")
            error_message = str(exc)
            self.log(f"오류: {error_message}")
            self.set_status("오류 발생")
            self.set_current_work(
                f"현재 작업: {error_message} · 내부 {window.outstanding_count}건 자동 재전송 금지"
            )
            if not self.is_closing:
                self.root.after(
                    0,
                    lambda message=error_message: messagebox.showerror(services.app_title, message),
                )
            self.record_history(
                "run_failed",
                phase=phase,
                submitted_jobs=submitted_count,
                uncertain_jobs=len(uncertain_job_ids),
                uncertain_job_ids=uncertain_job_ids,
                completed_jobs=completed_count,
                outstanding_tokens=[entry.token for entry in window.entries],
                remaining_jobs=max(0, total_jobs - job_index),
                error=error_message,
            )
        finally:
            try:
                if preparation is not None:
                    preparation.close()
            finally:
                try:
                    if full_snapshot_pause_active:
                        monitor.resume_full_snapshots()
                finally:
                    self.finish_active_print_run()

    def print_blocks(self, blocks: list[PrintBlock], run_options: PrintRunOptions) -> None:
        services = self.print_execution_services()
        if getattr(run_options, "device_completion_mode", False) and not run_options.dry_run:
            self.print_blocks_device_rolling(blocks, run_options)
            return
        job_index = 0
        sent = 0
        uncertain_job_ids: list[str] = []
        dry_run = run_options.dry_run
        phase = "preparation"
        display_run_id = getattr(self, "history_run_id", None)
        preparation: PrintPreparationService | None = None
        try:
            preparation = services.preparation_factory(
                services.layout_cache_dir,
                four_up=services.materialize_four_up_pdf,
                scheduler=getattr(self, "pdf_work_scheduler", None),
            )
            self.set_status("인쇄 전송 중")
            with self.print_state_lock:
                include_separator_pages = self.print_include_separator_pages
                include_name_separator_pages = bool(
                    getattr(self, "print_include_name_separator_pages", False)
                )
            total_jobs = self.active_print_total_jobs()
            planned_jobs, _ = self.active_print_batch(0, total_jobs)
            self.record_history(
                "run_prepared", required=True, dry_run=dry_run,
                blocks=[block_history_data(block) for block in blocks],
                jobs=[job_history_data(job, self.route_job_printer(job, run_options.printer_name))
                      for job in planned_jobs],
            )

            self.log(
                f"작업 준비: 블록 {len(blocks)}개, 전송 {total_jobs}건, "
                f"방향 프로필 자동 선택, 방식={run_options.engine}, "
                f"구분지={'사용' if include_separator_pages else '없음'}, "
                f"이름 구분지={'사용' if include_name_separator_pages else '없음'}, "
                f"호출 단위={run_options.queue_batch_size}개, "
                f"큐 기준={run_options.queue_refill_threshold}개 이하"
            )
            if run_options.engine == "sumatra":
                self.log(f"SumatraPDF: {run_options.sumatra_path}")

            if dry_run:
                jobs, _total = self.active_print_batch(0, total_jobs)
                for index, job in enumerate(jobs, start=1):
                    target_printer = self.route_job_printer(job, run_options.printer_name)
                    layout = " / A4 가로 2x2" if job.pages_per_sheet == 4 else ""
                    self.log(
                        f"미리보기 {index}/{total_jobs}: {job.item.path.name}{layout} -> {target_printer}"
                    )
                self.log("미리보기만 선택되어 실제 인쇄는 보내지 않습니다.")
                self.mark_blocks_status(blocks, "대기")
                self.set_status("미리보기 완료")
                self.set_current_work("현재 작업: 미리보기 완료")
                self.record_history("run_finished", dry_run=True, outcome="preview_only", submitted_jobs=0)
                return
            if run_options.engine != "sumatra":
                raise RuntimeError("방향별 스테이플 인쇄는 SumatraPDF 방식에서만 지원합니다.")

            active_block_uid: int | None = None
            active_bundle_key: tuple[int, int] | None = None
            used_printers: set[str] = set()
            fingerprints: dict[tuple[Path, int, int], dict[str, object]] = {}

            while True:
                self.raise_if_print_cancelled()
                total_jobs = self.active_print_total_jobs()
                if job_index >= total_jobs:
                    with self.print_state_lock:
                        if len(self.print_pending_jobs) <= job_index:
                            self.print_accepting_appends = False
                            break
                    if self.print_cancel_event.wait(0.2):
                        self.raise_if_print_cancelled()
                    continue
                candidates, total_jobs = self.active_print_batch(
                    job_index,
                    run_options.queue_batch_size,
                )
                if not candidates:
                    if self.print_cancel_event.wait(0.2):
                        self.raise_if_print_cancelled()
                    continue
                lookahead, _ = self.active_print_batch(job_index, 3)
                preparation.prefetch_jobs(lookahead, limit=2)
                target_printer = self.route_job_printer(candidates[0], run_options.printer_name)
                routed_batch: list[tuple[PrintJob, str]] = []
                for candidate in candidates:
                    candidate_printer = self.route_job_printer(candidate, run_options.printer_name)
                    if candidate_printer != target_printer:
                        break
                    routed_batch.append((candidate, candidate_printer))
                send_count = self.wait_for_queue_refill_window(
                    target_printer,
                    run_options.queue_batch_size,
                    run_options.queue_refill_threshold,
                    len(routed_batch),
                )
                routed_batch = routed_batch[:send_count]
                self.log(f"호출 묶음 준비: {target_printer} / {len(routed_batch)}건")

                for job, target_printer in routed_batch:
                    phase = "preparation"
                    self.raise_if_print_cancelled()
                    if not self.claim_active_print_job(job, job_index):
                        continue
                    if job.block_uid != active_block_uid:
                        if active_block_uid is not None:
                            self.mark_block_status(active_block_uid, "완료")
                        active_block_uid = job.block_uid
                        active_bundle_key = None
                        self.mark_block_status(job.block_uid, "진행 중", job.block_summary)
                        self.set_current_work(
                            f"현재 작업: 블록 {job.block_index}/{job.block_total} · {job.block_summary}"
                        )
                        self.log(f"블록 {job.block_index}/{job.block_total} 시작: {job.block_summary}")
                    if job.bundle_no is not None and job.bundle_total is not None:
                        bundle_key = (job.block_index, job.bundle_no)
                        if bundle_key != active_bundle_key:
                            active_bundle_key = bundle_key
                            self.log(f"  묶음 {job.bundle_no}/{job.bundle_total} 시작")

                    next_sent = sent + 1
                    current_total = self.active_print_total_jobs()
                    if job.is_name_separator:
                        item_label = job.item.name
                    elif job.is_separator:
                        item_label = "구분지"
                    else:
                        item_label = self.queue_file_line_label(job.item)
                    if job.bundle_no is not None and job.bundle_total is not None:
                        job_detail = (
                            f"묶음 {job.bundle_no}/{job.bundle_total} · "
                            f"{item_label} · {next_sent}/{current_total}"
                        )
                    elif job.is_name_separator:
                        job_detail = f"{item_label} 전송 중 · {next_sent}/{current_total}"
                    elif job.is_separator:
                        job_detail = f"구분지 전송 중 · {next_sent}/{current_total}"
                    else:
                        job_detail = f"{item_label} x {job.copies}부 · {next_sent}/{current_total}"
                    self.mark_block_status(job.block_uid, "진행 중", job_detail)
                    self.set_current_work(
                        f"현재 작업: 블록 {job.block_index}/{job.block_total} · {job_detail}"
                    )

                    if job.pages_per_sheet == 4:
                        self.log(f"  A4 가로 2x2 조판 준비: {job.item.path.name}")
                    print_path = preparation.resolve_job(job)
                    layout_label = " / 2x2" if job.pages_per_sheet == 4 else ""

                    self.log(
                        f"인쇄 전송 {next_sent}/{current_total}: "
                        f"{job.item.path.name}{layout_label} -> {target_printer}"
                    )
                    job_data = job_history_data(job, target_printer, print_path)
                    if getattr(self, "print_history", None) is not None:
                        for prefix, path in (("pdf", job.item.path), ("printed_pdf", print_path)):
                            stat = path.stat()
                            key = (path, stat.st_size, stat.st_mtime_ns)
                            if key not in fingerprints:
                                fingerprints[key] = services.fingerprint_pdf(path)
                            job_data.update({f"{prefix}_{name}": value for name, value in fingerprints[key].items()})
                    job_data["attempt_id"] = str(uuid.uuid4())
                    self.record_history("job_attempt", required=True, **job_data)
                    phase = "dispatch"
                    try:
                        call_count = services.sumatra_enqueue_pdfs(
                            run_options.sumatra_path,
                            [print_path],
                            target_printer,
                            copies=job.copies,
                            duplex=run_options.duplex,
                        )
                        if call_count != 1:
                            raise RuntimeError(f"SumatraPDF가 {job.item.path.name} 전송을 확인하지 못했습니다.")
                    except Exception as error:
                        # Crossing the process boundary cannot prove that the
                        # printer did not receive the document. Exclude this
                        # attempt before any history write can fail.
                        job_index += 1
                        uncertain_job_ids.append(job.job_id)
                        phase = "dispatch_uncertain"
                        self.record_history(
                            "job_unconfirmed",
                            error=str(error),
                            automatic_retry_allowed=False,
                            **job_data,
                        )
                        raise RuntimeError(
                            f"{job.item.path.name} 전송 결과를 확정할 수 없습니다. "
                            "자동 재전송하지 않습니다. 프린터 작업 이력을 확인해 주세요."
                        ) from error
                    sent = next_sent
                    job_index += 1
                    self.set_taskbar_print_progress(
                        job_index,
                        current_total,
                        run_id=display_run_id,
                    )
                    used_printers.add(target_printer)
                    # Advance first: a disk error after successful submission
                    # must never put this PDF back in the retry batch.
                    phase = "submission_history"
                    self.record_history("job_submitted", required=True, **job_data)
                    self.log(f"  Sumatra 호출 확인: {job.item.path.name} x {job.copies}부{layout_label}")

            if active_block_uid is not None:
                self.mark_block_status(active_block_uid, "완료")
            self.log("전체 큐 인쇄 전송 완료. 프린터 대기열 완료 여부를 확인합니다.")
            self.set_status("전송 완료 / 인쇄 중")
            self.set_current_work("현재 작업: 전송 완료 / 프린터 대기열 인쇄 중")
            phase = "queue_observation"
            self.record_history("run_submitted", submitted_jobs=job_index, printers=sorted(used_printers))
            queue_results = [self.wait_for_print_queue_empty(printer_name) for printer_name in sorted(used_printers)]
            queues_are_empty = all(queue_results)
            if queues_are_empty:
                self.set_status("전송 완료 / 인쇄 완료")
                self.set_current_work("현재 작업: 전송 완료 / 인쇄 완료")
            else:
                self.set_status("전송 완료 / 대기열 확인 실패")
                self.set_current_work("현재 작업: 전송 완료 / 프린터 대기열 확인 실패")
            self.record_history(
                "run_finished", submitted_jobs=job_index,
                outcome="windows_queues_empty" if queues_are_empty else "queue_check_failed",
                physical_output_verified=False,
            )
        except PrintCancelled as exc:
            total_jobs = self.active_print_total_jobs()
            has_unsent_jobs = not dry_run and job_index < total_jobs
            if has_unsent_jobs:
                self.preserve_retry_from_failure(job_index)
            with self.print_state_lock:
                stopped_uid = self.active_print_block_uid
            if stopped_uid is not None and has_unsent_jobs:
                self.mark_block_status(stopped_uid, "중지됨")
            self.log(str(exc))
            if has_unsent_jobs:
                retry_suffix = " · 남은 작업 재시도 가능" if self.retry_batch is not None else ""
                self.set_status("전송 중지됨")
                self.set_current_work(f"현재 작업: 남은 전송 중지됨{retry_suffix}")
            else:
                self.set_status("전송 완료 / 완료 확인 중지")
                self.set_current_work("현재 작업: 전송은 완료됨 · 대기열 확인만 중지됨")
            self.record_history(
                "run_cancelled", phase=phase, submitted_jobs=sent,
                uncertain_jobs=len(uncertain_job_ids),
                uncertain_job_ids=uncertain_job_ids,
                remaining_jobs=max(0, total_jobs - job_index), error=str(exc),
            )
        except Exception as exc:
            error_message = str(exc)
            if not dry_run:
                self.preserve_retry_from_failure(job_index)
            with self.print_state_lock:
                failed_uid = self.active_print_block_uid
            if failed_uid is not None:
                self.mark_block_status(failed_uid, "오류")
            self.log(f"오류: {error_message}")
            self.set_status("오류 발생")
            retry_suffix = " · 미전송 작업 재시도 가능" if self.retry_batch is not None else ""
            self.set_current_work(f"현재 작업: 오류 발생 · {error_message}{retry_suffix}")
            if not self.is_closing:
                self.root.after(0, lambda message=error_message: messagebox.showerror(services.app_title, message))
            self.record_history(
                "run_failed", phase=phase, submitted_jobs=sent,
                uncertain_jobs=len(uncertain_job_ids),
                uncertain_job_ids=uncertain_job_ids,
                remaining_jobs=max(0, self.active_print_total_jobs() - job_index), error=error_message,
            )
        finally:
            try:
                if preparation is not None:
                    preparation.close()
            finally:
                self.finish_active_print_run()
