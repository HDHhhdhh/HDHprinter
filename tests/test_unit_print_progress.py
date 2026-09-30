from __future__ import annotations

import unittest

from unit_print_progress import (
    PrintProgressState,
    RunPhase,
    persistent_alert_for_status,
    transition_taskbar_state,
)


class PrintProgressStateTests(unittest.TestCase):
    def test_progress_does_not_clear_held_state(self) -> None:
        state = transition_taskbar_state(PrintProgressState(), "indeterminate")
        state = transition_taskbar_state(state, "paused")

        state = transition_taskbar_state(state, "progress", 2, 10)

        self.assertEqual(RunPhase.HELD, state.phase)
        self.assertEqual("paused", state.taskbar_kind)
        self.assertEqual((2, 10), (state.completed, state.total))

    def test_explicit_resume_restores_progress_after_hold(self) -> None:
        state = PrintProgressState(phase=RunPhase.HELD, completed=2, total=10)

        state = transition_taskbar_state(state, "running")

        self.assertEqual(RunPhase.RUNNING, state.phase)
        self.assertEqual("progress", state.taskbar_kind)

    def test_generic_running_status_does_not_resume_operator_hold(self) -> None:
        state = PrintProgressState(
            phase=RunPhase.HELD,
            completed=2,
            total=10,
            hold_reason="스테이플 없음",
        )

        state = transition_taskbar_state(state, "indeterminate")

        self.assertEqual(RunPhase.HELD, state.phase)
        self.assertEqual("paused", state.taskbar_kind)

    def test_new_run_clears_previous_completion_notification(self) -> None:
        state = transition_taskbar_state(PrintProgressState(), "complete")

        state = state.begin("run-2", total=7)

        self.assertEqual(RunPhase.PREPARING, state.phase)
        self.assertFalse(state.completion_pending)
        self.assertEqual("run-2", state.run_id)

    def test_late_progress_does_not_clear_error_or_completion(self) -> None:
        for terminal in ("error", "complete"):
            with self.subTest(terminal=terminal):
                state = transition_taskbar_state(PrintProgressState(), terminal)
                state = transition_taskbar_state(state, "progress", 1, 4)
                self.assertEqual(terminal, state.taskbar_kind)

    def test_printer_hold_alert_keeps_device_reason_and_resume_action(self) -> None:
        alert = persistent_alert_for_status(
            "프린터 오류 / 인쇄 일시 중지",
            device_detail="스테이플 없음",
        )

        self.assertIsNotNone(alert)
        self.assertEqual("printer-held", alert.key)
        self.assertIn("스테이플 없음", alert.message)
        self.assertTrue(alert.allow_resume)

    def test_transfer_stop_statuses_share_one_acknowledgement_key(self) -> None:
        requested = persistent_alert_for_status("전송 중지 요청됨")
        stopped = persistent_alert_for_status("전송 중지됨")

        self.assertEqual("transfer-stopped", requested.key)
        self.assertEqual(requested.key, stopped.key)
        self.assertFalse(stopped.allow_resume)

    def test_completion_alert_requires_physical_completion(self) -> None:
        physical = persistent_alert_for_status("실제 인쇄 완료")
        spooler = persistent_alert_for_status("전송 완료 / 인쇄 완료")

        self.assertEqual("print-complete", physical.key)
        self.assertEqual("실제 인쇄 완료", physical.title)
        self.assertIsNone(spooler)

    def test_ordinary_running_status_has_no_persistent_alert(self) -> None:
        self.assertIsNone(persistent_alert_for_status("인쇄 전송 중"))


if __name__ == "__main__":
    unittest.main()
