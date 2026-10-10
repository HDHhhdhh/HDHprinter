from __future__ import annotations

import threading
import unittest
import queue
from concurrent.futures import Future
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from unit_print_eta import CompletionTimeProjection, EtaEstimate, PrintEtaObservation
from unit_print_manager import PrintManagerApp, PrintRunOptions
from unit_print_progress import PrintProgressState, RunPhase
from unit_print_resource_estimate import PaperReadiness, PrintResourceEstimate, ResourceJob


LOCAL = timezone(timedelta(hours=9))


class Variable:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        if threading.current_thread() is not threading.main_thread():
            raise AssertionError("Worker changed a UI variable")
        self.value = value


class QueuedRoot:
    def __init__(self):
        self.callbacks = []

    def after(self, _delay, callback):
        self.callbacks.append(callback)

    def pump(self):
        callbacks, self.callbacks = self.callbacks, []
        for callback in callbacks:
            callback()


class CompletionProjectionTests(unittest.TestCase):
    def test_midpoint_uses_minute_ceiling_and_unambiguous_twelve_hour_clock(self):
        start = datetime(2026, 10, 7, 13, 55, tzinfo=LOCAL)
        projection = CompletionTimeProjection(EtaEstimate(16 * 60, 21 * 60, "초기 추정"), start)
        self.assertEqual(datetime(2026, 10, 7, 14, 14, tzinfo=LOCAL), projection.completion_at)
        self.assertEqual("예상 완료 시간 오후 2:14", projection.display_text(start))

    def test_midnight_noon_tomorrow_and_later_dates(self):
        cases = (
            (datetime(2026, 10, 7, 11, 59, tzinfo=LOCAL), 60, "오후 12:00"),
            (datetime(2026, 10, 7, 23, 59, tzinfo=LOCAL), 60, "내일 오전 12:00"),
            (datetime(2026, 10, 7, 23, 59, tzinfo=LOCAL), 25 * 3600, "10/9 오전 12:59"),
            (datetime(2026, 12, 30, 23, 59, tzinfo=LOCAL), 25 * 3600, "2027/1/1 오전 12:59"),
        )
        for now, seconds, expected in cases:
            with self.subTest(expected=expected):
                projection = CompletionTimeProjection(EtaEstimate(seconds, seconds, "test"), now)
                self.assertEqual("예상 완료 시간 " + expected, projection.display_text(now))

    def test_expired_forecast_keeps_its_time_and_does_not_claim_completion(self):
        now = datetime(2026, 10, 7, 14, 0, tzinfo=LOCAL)
        projection = CompletionTimeProjection(EtaEstimate(600, 600, "test"), now)
        self.assertEqual("예상 완료 시간 오후 2:10 · 완료 확인 중", projection.display_text(now + timedelta(minutes=15)))
        self.assertEqual(now + timedelta(minutes=10), projection.completion_at)


class CompletionUiTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 7, 14, 0, tzinfo=LOCAL)
        self.app = PrintManagerApp.__new__(PrintManagerApp)
        self.app.root = QueuedRoot()
        self.app.resource_estimate_var = Variable()
        self.app.resource_estimate_bar = SimpleNamespace(winfo_width=lambda: 2000)
        self.app.resource_completion_now = lambda: self.now
        self.app.is_printing = False
        self.app.is_closing = False
        self.app.print_cancel_event = threading.Event()
        self.app.retry_batch = None
        self.app.history_run_id = "run-a"
        self.app.print_progress_state = PrintProgressState().begin("run-a", 3)
        self.estimate = PrintResourceEstimate(
            total_sheets=100, staple_sets=3, output_sets=3, unknown_documents=0,
            oversize_staple_sets=0, eta=EtaEstimate(600, 1200, "test"),
            paper_capacity=None, paper_readiness=PaperReadiness.UNKNOWN,
        )
        self.options = PrintRunOptions("FAKE", "sumatra", "FAKE.exe", True, False, 3, 1)

    def observation(self, ids=("a", "b", "c"), **changes):
        value = PrintEtaObservation("run-a", self.estimate.eta, self.now, ids, (), 100)
        return replace(value, **changes)

    def active(self):
        self.app.is_printing = True
        self.app.begin_resource_estimate_run(replace(self.options, device_completion_mode=True))

    def test_duplicate_result_resize_and_clock_refresh_preserve_idle_anchor(self):
        self.app.resource_estimate_duration_key = ("jobs", (), True)
        self.app.adopt_resource_estimate(self.estimate, ("future", 1))
        target = self.app.resource_completion_projection.completion_at
        self.now += timedelta(minutes=3)
        self.app.adopt_resource_estimate(self.estimate, ("future", 2))
        self.app.resource_estimate_bar = SimpleNamespace(winfo_width=lambda: 1650)
        self.app.refresh_resource_estimate_display()
        self.app.refresh_resource_completion_clock()
        self.assertEqual(target, self.app.resource_completion_projection.completion_at)
        self.assertIn("오후 2:15", self.app.resource_estimate_var.get())
        self.assertNotIn("약", self.app.resource_estimate_var.get())
        self.assertIn("약 10~20분", self.app.resource_estimate_detail_text())

    def test_narrow_badge_keeps_completion_clock_visible(self):
        self.app.adopt_resource_estimate(self.estimate, "same")
        self.app.resource_estimate_bar = SimpleNamespace(winfo_width=lambda: 1400)
        self.app.refresh_resource_estimate_display()
        self.assertEqual("예상 완료 오후 2:15", self.app.resource_estimate_var.get())

    def test_pending_new_forecast_does_not_resurrect_previous_completion_clock(self):
        self.app.adopt_resource_estimate(self.estimate, "previous")
        self.app.latest_resource_estimate = None
        self.app.set_resource_estimate_text("예상 자원  계산 중")
        self.app.refresh_resource_completion_clock()
        self.assertEqual("예상 자원  계산 중", self.app.resource_estimate_var.get())

    def test_new_idle_forecast_invalidates_previous_terminal_override_and_active_work(self):
        self.active()
        self.app.update_print_resource_eta(self.observation())
        self.app.update_resource_completion_status("실제 인쇄 완료")
        self.app.is_printing = False
        future = Future()
        self.app.resource_estimate_scheduler = SimpleNamespace(submit=lambda *_args, **_kwargs: future)
        self.app.resource_estimate_results = queue.SimpleQueue()
        self.app.resource_estimate_request_key = "previous"
        self.app.resource_estimate_poll_after_id = None
        self.app.blocks_waiting_for_print = lambda: [object()]
        self.app.resource_estimate_jobs = lambda _blocks: (ResourceJob(None, known_page_count=10),)
        self.app.resource_estimate_consumables = lambda: None
        self.app.eta_print_rate_samples = lambda: ()
        self.app.duplex_var = Variable(True)
        self.app.schedule_resource_estimate()
        self.now += timedelta(seconds=2)
        self.app.refresh_resource_completion_clock()
        self.assertEqual("예상 자원  계산 중", self.app.resource_estimate_var.get())
        self.assertIsNone(self.app.active_resource_eta)
        self.assertIsNone(self.app.resource_completion_override)

    def test_expired_idle_forecast_requests_recalculation_and_active_forecast_checks_completion(self):
        self.app.adopt_resource_estimate(self.estimate, "waiting")
        self.now += timedelta(minutes=20)
        self.app.refresh_resource_completion_clock()
        self.assertIn("시작 시 재계산", self.app.resource_estimate_var.get())
        self.assertNotIn("완료 확인 중", self.app.resource_estimate_var.get())
        self.active()
        self.app.update_print_resource_eta(self.observation())
        self.now += timedelta(minutes=20)
        self.app.refresh_resource_completion_clock()
        self.assertIn("완료 확인 중", self.app.resource_estimate_var.get())

    def test_duplicate_preparation_forecast_cannot_overwrite_terminal_run_result(self):
        self.app.adopt_resource_estimate(self.estimate, "ready")
        self.app.is_printing = True
        self.app.begin_resource_estimate_run(self.options)
        self.app.update_resource_completion_status("실제 인쇄 완료")
        self.app.is_printing = False
        self.now += timedelta(minutes=1)
        self.app.adopt_resource_estimate(self.estimate, "ready")
        self.assertEqual("실제 인쇄 완료", self.app.resource_estimate_var.get())
        self.assertEqual("info", self.app.resource_estimate_tone)

    def test_pending_forecast_late_success_cannot_replace_terminal_completion(self):
        self.app.resource_estimate_revision = 3
        self.app.resource_estimate_request_key = "pending"
        self.app.is_printing = True
        self.app.begin_resource_estimate_run(replace(self.options, device_completion_mode=True))
        self.app.update_resource_completion_status("실제 인쇄 완료")
        self.app.is_printing = False
        self.app.adopt_resource_estimate(self.estimate, "pending")
        self.assertEqual("실제 인쇄 완료", self.app.resource_estimate_var.get())
        self.assertEqual("info", self.app.resource_estimate_tone)

    def test_pending_forecast_late_failure_keeps_active_or_terminal_clock_state(self):
        self.app.resource_estimate_revision = 3
        self.app.resource_estimate_request_key = "pending"
        self.app.resource_estimate_results = queue.SimpleQueue()
        self.app.log = Mock()
        self.active()
        self.app.update_print_resource_eta(self.observation())
        for terminal in (False, True):
            if terminal:
                self.app.update_resource_completion_status("실제 인쇄 완료")
                self.app.is_printing = False
            previous = self.app.resource_estimate_var.get()
            tone = self.app.resource_estimate_tone
            self.app.resource_estimate_request_key = "pending"
            future = Future()
            future.set_exception(OSError("Synthetic forecast failure"))
            self.app.resource_estimate_future = future
            self.app.resource_estimate_results.put((3, future))
            self.app.poll_resource_estimate_results()
            self.assertEqual(previous, self.app.resource_estimate_var.get())
            self.assertEqual(tone, self.app.resource_estimate_tone)

    def test_repeated_progress_and_submission_preserve_same_logical_work_anchor(self):
        self.active()
        first = self.observation()
        self.app.update_print_resource_eta(first)
        self.now += timedelta(minutes=4)
        # The same IDs represent work moving from pending to Canon outstanding.
        self.app.update_print_resource_eta(replace(first, observed_at=self.now))
        self.app.refresh_resource_completion_clock()
        self.assertEqual(first.observed_at + timedelta(minutes=15), self.app.resource_completion_projection.completion_at)
        self.assertIn("오후 2:15", self.app.resource_estimate_var.get())

    def test_completion_append_rate_changes_and_resume_reanchor(self):
        self.active()
        self.app.update_print_resource_eta(self.observation())
        first = self.app.resource_completion_projection
        for changes in (
            {"remaining_job_ids": ("b", "c"), "eta": EtaEstimate(300, 600, "test")},
            {"remaining_job_ids": ("b", "c", "d")},
            {"rate_samples": (0.6,)},
            {"held": True},
            {"held": False},
        ):
            self.now += timedelta(minutes=1)
            observation = replace(self.app.active_resource_eta, observed_at=self.now, **changes)
            self.app.update_print_resource_eta(observation)
            self.assertIsNot(first, self.app.resource_completion_projection)
            self.assertEqual(self.now, self.app.resource_completion_projection.observed_at)
            if observation.held:
                self.assertIn("인쇄 중지", self.app.resource_estimate_var.get())
                self.assertNotIn("오후", self.app.resource_estimate_var.get())
            first = self.app.resource_completion_projection

    def test_partial_forecast_and_unknown_active_pages_hide_completion_clock(self):
        partial = replace(self.estimate, unknown_documents=1)
        self.app.adopt_resource_estimate(partial, "partial")
        self.assertIsNone(self.app.resource_completion_projection)
        self.assertNotIn("오후", self.app.resource_estimate_var.get())
        self.active()
        self.app.update_print_resource_eta(self.observation(complete=False))
        self.assertIn("일부 계산 불가", self.app.resource_estimate_var.get())
        self.assertNotIn("오후", self.app.resource_estimate_var.get())

    def test_no_remaining_jobs_means_confirmation_not_physical_completion(self):
        self.active()
        self.app.update_print_resource_eta(self.observation(ids=(), remaining_sheets=0,
                                                           eta=EtaEstimate(0, 0, "test")))
        self.assertIn("완료 확인 중", self.app.resource_estimate_var.get())
        self.assertNotIn("실제 인쇄 완료", self.app.resource_estimate_var.get())
        self.assertNotEqual(RunPhase.COMPLETE, self.app.print_progress_state.phase)

    def test_remaining_eta_does_not_hide_existing_paper_shortage_warning(self):
        self.app.latest_resource_estimate = replace(self.estimate, paper_readiness=PaperReadiness.INSUFFICIENT)
        self.active()
        self.app.update_print_resource_eta(self.observation())
        self.assertIn("용지 부족 예상", self.app.resource_estimate_var.get())
        self.assertEqual("danger", self.app.resource_estimate_tone)

    def test_start_anchors_at_actual_start_and_legacy_submission_counts_do_not_shrink_eta(self):
        self.app.adopt_resource_estimate(self.estimate, "waiting")
        self.now += timedelta(minutes=5)
        self.app.is_printing = True
        self.app.begin_resource_estimate_run(self.options)
        self.app.set_taskbar_print_progress(2, 3, run_id="run-a")
        self.assertIn("오후 2:20", self.app.resource_estimate_var.get())
        self.app.update_resource_completion_status("전송 완료 / 인쇄 중")
        self.assertEqual("완료 확인 중", self.app.resource_estimate_var.get())
        self.app.update_resource_completion_status("전송 완료 / 인쇄 완료")
        self.assertIn("실제 완료 시각 확인 불가", self.app.resource_estimate_var.get())

    def test_dry_run_never_presents_a_physical_completion_clock(self):
        self.app.adopt_resource_estimate(self.estimate, "waiting")
        self.app.begin_resource_estimate_run(replace(self.options, dry_run=True))
        self.assertIn("미리보기", self.app.resource_estimate_var.get())
        self.assertNotIn("오후", self.app.resource_estimate_var.get())

    def test_legacy_append_invalidates_old_clock_until_remaining_work_can_be_known(self):
        self.app.adopt_resource_estimate(self.estimate, "waiting")
        self.app.begin_resource_estimate_run(self.options)
        self.app.print_run_options = self.options
        self.app.invalidate_resource_completion_for_append()
        self.assertEqual("예상 완료 시간 재계산 필요", self.app.resource_estimate_var.get())
        self.assertNotIn("오후", self.app.resource_estimate_var.get())

    def test_badge_colors_follow_warning_waiting_running_completed_roles(self):
        self.app.colors = {
            "success_soft": "green-soft", "success": "green",
            "danger_soft": "red-soft", "danger": "red",
            "warning_soft": "orange-soft", "warning": "orange",
            "panel_soft": "gray-soft", "muted": "gray",
            "accent_soft": "violet-soft", "accent": "violet",
            "complete_soft": "blue-soft", "complete": "blue",
        }
        self.app.resource_estimate_badge = SimpleNamespace(configure=Mock())
        for tone, color in (("warning", "red"), ("waiting", "orange"), ("success", "green"), ("info", "blue")):
            self.app.set_resource_estimate_text("test", tone)
            self.assertEqual(color, self.app.resource_estimate_badge.configure.call_args.kwargs["fg"])

    def test_worker_posts_ui_update_and_stale_run_terminal_or_close_are_ignored(self):
        self.active()
        observation = self.observation()
        old = self.app.resource_estimate_var.get()
        thread = threading.Thread(target=self.app.update_print_resource_eta, args=(observation,))
        thread.start()
        thread.join()
        self.assertEqual(old, self.app.resource_estimate_var.get())
        self.app.root.pump()
        self.assertIn("오후 2:15", self.app.resource_estimate_var.get())
        for phase, closing, run_id in (
            (RunPhase.RUNNING, False, "run-old"),
            (RunPhase.COMPLETE, False, "run-a"),
            (RunPhase.RUNNING, True, "run-a"),
        ):
            self.app.print_progress_state = replace(self.app.print_progress_state, phase=phase)
            self.app.is_closing = closing
            first = self.app.active_resource_eta
            self.app.update_print_resource_eta(replace(observation, run_id=run_id, eta=EtaEstimate(2, 2, "other")))
            self.assertIs(first, self.app.active_resource_eta)

    def test_queued_observation_cannot_reopen_clock_after_stop_request_uses_held_phase(self):
        self.active()
        first = self.observation()
        self.app.update_print_resource_eta(first)
        queued = replace(first, eta=EtaEstimate(30, 60, "queued"))
        thread = threading.Thread(target=self.app.update_print_resource_eta, args=(queued,))
        thread.start()
        thread.join()
        self.app.print_cancel_event.set()
        self.app.update_taskbar_from_status("전송 중지 요청됨")
        self.assertEqual(RunPhase.HELD, self.app.print_progress_state.phase)
        self.app.root.pump()
        self.assertIs(first, self.app.active_resource_eta)
        self.assertIn("인쇄 중지", self.app.resource_estimate_var.get())
        self.assertNotIn("오후", self.app.resource_estimate_var.get())


if __name__ == "__main__":
    unittest.main()
