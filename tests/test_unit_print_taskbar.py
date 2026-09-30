from __future__ import annotations

import unittest
from unittest.mock import patch

from unit_print_manager import PrintManagerApp
from unit_print_progress import PrintProgressState

from unit_print_taskbar import (
    TBPF_NOPROGRESS,
    TBPF_INDETERMINATE,
    TBPF_NORMAL,
    TBPF_PAUSED,
    TaskbarIndicator,
    four_stage_progress,
)


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def set_progress_state(self, hwnd: int, state: int) -> None:
        self.calls.append(("state", hwnd, state))

    def set_progress_value(self, hwnd: int, completed: int, total: int) -> None:
        self.calls.append(("value", hwnd, completed, total))

    def set_overlay_icon(self, hwnd: int, icon_handle: int, description: str) -> None:
        self.calls.append(("overlay", hwnd, icon_handle, description))

    def close(self) -> None:
        self.calls.append(("close",))


class TaskbarIndicatorTests(unittest.TestCase):
    def test_progress_is_quantized_into_four_stable_stages(self) -> None:
        self.assertEqual(0, four_stage_progress(0, 100))
        self.assertEqual(0, four_stage_progress(24, 100))
        self.assertEqual(1, four_stage_progress(25, 100))
        self.assertEqual(2, four_stage_progress(50, 100))
        self.assertEqual(3, four_stage_progress(75, 100))
        self.assertEqual(4, four_stage_progress(100, 100))

    def test_same_stage_does_not_repeat_taskbar_com_calls(self) -> None:
        backend = FakeBackend()
        with patch("unit_print_taskbar.taskbar_window_handle", return_value=77):
            indicator = TaskbarIndicator(11, backend)

        indicator.set_progress(25, 100)
        indicator.set_progress(49, 100)

        self.assertEqual(
            [("state", 77, TBPF_NORMAL), ("value", 77, 1, 4)],
            backend.calls,
        )

    def test_before_first_quarter_keeps_visible_running_indicator(self) -> None:
        backend = FakeBackend()
        with patch("unit_print_taskbar.taskbar_window_handle", return_value=77):
            indicator = TaskbarIndicator(11, backend)

        indicator.set_progress(10, 100)

        self.assertEqual([("state", 77, TBPF_INDETERMINATE)], backend.calls)

    def test_paused_state_keeps_the_last_progress_stage(self) -> None:
        backend = FakeBackend()
        with patch("unit_print_taskbar.taskbar_window_handle", return_value=77):
            indicator = TaskbarIndicator(11, backend)
        indicator.set_progress(50, 100)
        backend.calls.clear()

        with (
            patch("unit_print_taskbar.create_overlay_icon", return_value=123),
            patch("unit_print_taskbar.ctypes.windll.user32.DestroyIcon"),
        ):
            indicator.set_paused()

        self.assertEqual(
            [
                ("value", 77, 2, 4),
                ("state", 77, TBPF_PAUSED),
                ("overlay", 77, 123, "빠른 인쇄기 작업 중지"),
            ],
            backend.calls,
        )

    def test_completion_clears_progress_and_sets_one_overlay(self) -> None:
        backend = FakeBackend()
        with patch("unit_print_taskbar.taskbar_window_handle", return_value=77):
            indicator = TaskbarIndicator(11, backend)
        indicator.set_progress(75, 100)
        backend.calls.clear()

        with (
            patch("unit_print_taskbar.create_overlay_icon", return_value=123),
            patch("unit_print_taskbar.ctypes.windll.user32.DestroyIcon"),
        ):
            indicator.set_completed()

        self.assertEqual(("state", 77, TBPF_NOPROGRESS), backend.calls[0])
        self.assertEqual(("overlay", 77, 123, "빠른 인쇄 완료 1건"), backend.calls[1])

    def test_app_progress_update_keeps_paused_taskbar_state(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.root = type("ImmediateRoot", (), {"after": lambda _self, _delay, callback: callback()})()
        app.taskbar_indicator = None
        app.taskbar_display_state = ("idle", 0, 1)
        app.taskbar_completion_pending = False

        app.set_taskbar_display_state("paused")
        app.set_taskbar_print_progress(0, 8)

        self.assertEqual(("paused", 0, 8), app.taskbar_display_state)

    def test_late_progress_from_previous_run_is_ignored(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.root = type("ImmediateRoot", (), {"after": lambda _self, _delay, callback: callback()})()
        app.taskbar_indicator = None
        app.print_progress_state = PrintProgressState().begin("new-run", total=9)
        app.taskbar_display_state = ("indeterminate", 0, 9)
        app.taskbar_completion_pending = False

        app.set_taskbar_print_progress(8, 8, run_id="old-run")

        self.assertEqual(("indeterminate", 0, 9), app.taskbar_display_state)

    def test_safe_timer_refresh_reapplies_visible_state(self) -> None:
        scheduled: list[tuple[int, object]] = []
        invalidations: list[bool] = []
        applications: list[bool] = []

        class TimerRoot:
            def after(self, delay, callback):
                scheduled.append((delay, callback))
                return "refresh-id"

        class Indicator:
            def invalidate_cache(self):
                invalidations.append(True)

        app = PrintManagerApp.__new__(PrintManagerApp)
        app.root = TimerRoot()
        app.is_closing = False
        app.taskbar_indicator = Indicator()
        app.taskbar_refresh_after_id = "expired-id"
        app.taskbar_display_state = ("complete", 1, 1)
        app.apply_taskbar_display_state = lambda: applications.append(True)

        app.refresh_taskbar_indicator()

        self.assertEqual([True], invalidations)
        self.assertEqual([True], applications)
        self.assertEqual("refresh-id", app.taskbar_refresh_after_id)
        self.assertEqual(1, len(scheduled))

    def test_invalidating_indicator_reapplies_same_completed_overlay(self) -> None:
        backend = FakeBackend()
        with patch("unit_print_taskbar.taskbar_window_handle", return_value=77):
            indicator = TaskbarIndicator(11, backend)
        with (
            patch("unit_print_taskbar.create_overlay_icon", return_value=123),
            patch("unit_print_taskbar.ctypes.windll.user32.DestroyIcon"),
        ):
            indicator.set_completed()
            first_count = len([call for call in backend.calls if call[0] == "overlay"])
            indicator.invalidate_cache()
            indicator.set_completed()

        self.assertEqual(1, first_count)
        self.assertEqual(2, len([call for call in backend.calls if call[0] == "overlay"]))


if __name__ == "__main__":
    unittest.main()
