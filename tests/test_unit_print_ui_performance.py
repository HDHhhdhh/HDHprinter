from __future__ import annotations

import unittest
import queue
from concurrent.futures import Future
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from unit_print_device_state import DeviceHealth, DeviceSnapshot
from unit_print_manager import ExamPdf, PrintBlock, PrintManagerApp
from unit_print_preparation import OrderedCommitQueue


class Variable:
    def __init__(self, value: object) -> None:
        self.value = value
        self.set_calls = 0

    def get(self) -> object:
        return self.value

    def set(self, value: object) -> None:
        self.value = value
        self.set_calls += 1


class UiPerformanceTests(unittest.TestCase):
    def test_processing_button_refresh_changes_only_presentation_once_per_transition(self):
        class Button:
            def __init__(self):
                self.flags = set()
                self.text = "인쇄 시작"
                self.writes = []

            def state(self, flags=None):
                if flags is not None:
                    self.writes.append(tuple(flags))
                    for flag in flags:
                        self.flags.discard(flag[1:]) if flag.startswith("!") else self.flags.add(flag)
                return tuple(self.flags)

            def cget(self, option):
                return self.text

            def configure(self, **options):
                self.writes.append(options)
                self.text = options["text"]

        app = PrintManagerApp.__new__(PrintManagerApp)
        app.start_print_button = Button()
        app.is_printing = False
        app.refresh_print_action_buttons()
        self.assertEqual([], app.start_print_button.writes)
        app.is_printing = True
        app.refresh_print_action_buttons()
        writes = list(app.start_print_button.writes)
        app.refresh_print_action_buttons()
        self.assertEqual(writes, app.start_print_button.writes)
        self.assertEqual("인쇄 중", app.start_print_button.text)
        self.assertEqual({"alternate"}, app.start_print_button.flags)
        app.is_printing = False
        app.refresh_print_action_buttons()
        self.assertEqual("인쇄 시작", app.start_print_button.text)
        self.assertEqual(set(), app.start_print_button.flags)

    def test_slow_material_request_keeps_later_pdf_block_in_user_order(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.block_commit_queue = OrderedCommitQueue()
        app.blocks = []
        app.block_status_by_id = {}
        app.push_undo_state = Mock()
        app.update_queue_view = Mock()
        app.update_preview = Mock()
        app.clear_custom_pdfs = Mock()
        app.clear_new_block_name = Mock()
        app.log = Mock()
        app.custom_pdf_files = [Path("fast.pdf")]
        app.custom_copies_var = Variable(1)
        app.new_block_name_var = Variable("")
        app.name_separator_page_var = Variable(False)
        slow = PrintBlock("slow", "", "", 1, 1, 1, [ExamPdf(1, "slow", Path("slow.pdf"))])
        fast = PrintBlock("fast", "", "", 1, 1, 1, [ExamPdf(1, "fast", Path("fast.pdf"))])
        app.make_custom_pdf_block = Mock(return_value=fast)
        first_token = app.reserve_block_commit()

        self.assertTrue(app.add_custom_pdf_block())
        self.assertEqual([], app.blocks)

        app.complete_block_commit(first_token, lambda: app.blocks.append(slow))

        self.assertEqual([slow, fast], app.blocks)

    def test_able_add_returns_before_pdf_work_and_preserves_newer_input(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.block_commit_queue = OrderedCommitQueue()
        app.block_preparation_epoch = 0
        app.background_build_results = queue.SimpleQueue()
        app.background_build_poll_after_id = None
        app.able_build_pending = False
        app.order_build_pending = False
        app.group_build_pending = False
        app.recovery_load_pending = False
        app.is_closing = False
        app.root = Mock()
        app.root.after.return_value = "poll-1"
        captured: dict[str, object] = {}

        def submit(work, deliver):
            captured["work"] = work
            captured["deliver"] = deliver
            return Future()

        app.background_build_service = SimpleNamespace(submit=submit)
        subject = SimpleNamespace(subject="공통수학1")
        app.current_able_subject = Mock(return_value=subject)
        app.able_copies_var = Variable(1)
        app.able_document_kind_var = Variable("교재")
        app.able_selected_numbers_var = Variable("1")
        app.able_start_var = Variable("01. 1차시")
        app.able_end_var = Variable("01. 1차시")
        app.able_status_var = Variable("")
        app.name_separator_page_var = Variable(False)
        app.new_block_name_var = Variable("old")
        app.resolve_block_printer_route = Mock(return_value=("A:ble", "printer"))
        app.able_available_lesson_numbers = Mock(return_value=(1, 2))
        block = PrintBlock("공통수학1", "A:ble", "차시별", 1, 1, 1, [ExamPdf(1, "교재", Path("book.pdf"))])
        app.make_able_block_for_selection = Mock(return_value=block)
        committed: list[PrintBlock] = []
        app._commit_material_block = Mock(side_effect=committed.append)
        app.clear_new_block_name = Mock()
        app.refresh_able_status = Mock()

        self.assertTrue(app.add_able_material_block())
        self.assertIn("work", captured)
        self.assertEqual([], committed)

        app.able_selected_numbers_var.set("2")
        app.able_copies_var.set(3)
        app.new_block_name_var.set("new")
        future: Future[object] = Future()
        future.set_result(captured["work"]())  # type: ignore[operator]
        captured["deliver"](future)  # type: ignore[operator]
        app.poll_background_build_results()

        self.assertEqual([block], committed)
        self.assertEqual("2", app.able_selected_numbers_var.get())
        self.assertEqual(3, app.able_copies_var.get())
        self.assertEqual("new", app.new_block_name_var.get())

    def test_repeated_current_work_text_skips_tk_variable_write(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.current_work_var = Variable("현재 작업: 대기")

        app.set_current_work("현재 작업: 대기")

        self.assertEqual(0, app.current_work_var.set_calls)

    def test_repeated_block_progress_skips_full_queue_redraw(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.block_status_by_id = {}
        app.block_detail_by_id = {}
        app.draw_queue = Mock()

        app.mark_block_status(101, "진행 중", "실제 완료 0/20")
        app.mark_block_status(101, "진행 중", "실제 완료 0/20")

        app.draw_queue.assert_called_once_with()

    def test_repeated_device_snapshot_skips_badge_reconfigure(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.device_status_var = Variable("프린터 인쇄 가능")
        app.device_status_health = DeviceHealth.READY
        app.refresh_device_status_badge = Mock()

        app.set_device_status_text("프린터 인쇄 가능", DeviceHealth.READY)

        self.assertEqual(0, app.device_status_var.set_calls)
        app.refresh_device_status_badge.assert_not_called()

    def test_resource_forecast_uses_clickable_summary_on_narrow_windows(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        full = "예상 A4 842장 · 스테이플 37회 · 약 28~34분 · 용지 충분"
        app.resource_estimate_var = Variable(full)
        app.resource_estimate_full_text = full
        app.latest_resource_estimate = None
        app.resource_estimate_bar = SimpleNamespace(winfo_width=lambda: 1400)

        app.refresh_resource_estimate_display()

        self.assertEqual("예상 자원 보기", app.resource_estimate_var.get())
        app.resource_estimate_bar = SimpleNamespace(winfo_width=lambda: 2000)
        app.refresh_resource_estimate_display()
        self.assertEqual(full, app.resource_estimate_var.get())

    def test_status_poll_only_schedules_ui_work_when_snapshot_changes(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.is_closing = False
        app.root = Mock()
        app.set_device_status_text = Mock()
        app.refresh_print_action_buttons = Mock()
        app.device_monitor = SimpleNamespace(
            latest_snapshot=DeviceSnapshot(
                observed_at=datetime.now().astimezone(),
                health=DeviceHealth.READY,
                status_text="인쇄 가능",
                authenticated=True,
            )
        )

        app.poll_device_status()

        app.set_device_status_text.assert_called_once()
        app.refresh_print_action_buttons.assert_not_called()
        app.root.after.assert_called_once_with(1000, app.poll_device_status)

    def test_initial_monitor_placeholder_does_not_replace_connecting_message(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.is_closing = False
        app.root = Mock()
        app.set_device_status_text = Mock()
        app.device_monitor = SimpleNamespace(
            latest_snapshot=DeviceSnapshot.unavailable("프린터 상태 연결 전")
        )

        app.poll_device_status()

        app.set_device_status_text.assert_not_called()
        app.root.after.assert_called_once_with(1000, app.poll_device_status)

    def test_idle_monitor_interval_is_clamped_to_reduce_background_load(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.device_monitor = None
        app.device_monitor_config = {
            "idle_interval_seconds": 12.0,
            "active_interval_seconds": 1.0,
        }
        app.set_device_status_text = Mock()
        client = Mock()

        with patch("unit_print_manager.PrinterDeviceMonitor") as monitor_type:
            app.activate_device_monitor(client)

        self.assertEqual(
            30.0,
            monitor_type.call_args.kwargs["idle_interval_seconds"],
        )
        self.assertEqual(30.0, app.device_monitor_config["idle_interval_seconds"])
        monitor_type.return_value.start.assert_called_once_with()

    def test_completed_taskbar_badge_stays_pending_until_operator_acknowledges(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.taskbar_indicator = Mock()
        app.taskbar_display_state = ("idle", 0, 1)
        app.taskbar_completion_pending = False
        app.log = Mock()

        app.update_taskbar_from_status("실제 인쇄 완료")

        self.assertTrue(app.taskbar_completion_pending)
        app.taskbar_indicator.set_completed.assert_called_once_with()

        app.clear_taskbar_completion_notification()

        self.assertFalse(app.taskbar_completion_pending)
        app.taskbar_indicator.reset.assert_called_once_with()

    def test_windows_spooler_completion_does_not_set_completion_badge(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.taskbar_indicator = Mock()
        app.taskbar_display_state = ("progress", 3, 4)
        app.taskbar_completion_pending = False
        app.log = Mock()

        app.update_taskbar_from_status("전송 완료 / 인쇄 완료")

        self.assertFalse(app.taskbar_completion_pending)
        app.taskbar_indicator.reset.assert_called_once_with()
        app.taskbar_indicator.set_completed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
