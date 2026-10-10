from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from unit_print_eta import (
    ActivePrintTime, CompletionTimeProjection, ETA_TIMING_CONFIG_KEY,
    ETA_TIMING_DEVICE_KEY, EtaTimingSample, EtaWorkload, PrintEtaObservation,
    completed_job_timing, estimate_workloads_seconds, initialize_timing_samples,
    timing_device_key, validated_timing_samples,
)
from unit_print_manager import PrintManagerApp
from unit_print_device_state import DeviceJob, DeviceJobState


LOCAL = timezone(timedelta(hours=9))


class WorkloadEtaTests(unittest.TestCase):
    def setUp(self):
        self.samples = (EtaTimingSample(300, 300, 458.312), EtaTimingSample(5, 1, 12.488))

    def test_bulk_job_replay_finishes_near_observed_1830_instead_of_1842(self):
        estimate = estimate_workloads_seconds((EtaWorkload(300, 300), EtaWorkload(5)), self.samples)
        self.assertLess(abs(estimate.seconds_expected - 473.930), 10)
        start = datetime(2026, 10, 7, 18, 23, 1, tzinfo=LOCAL)
        projection = CompletionTimeProjection(estimate, start)
        self.assertEqual("예상 완료 시간 오후 6:31", projection.display_text(start))
        self.assertGreater(estimate.seconds_high, estimate.seconds_expected)
        self.assertLess(estimate.seconds_low, estimate.seconds_expected)

    def test_many_small_slow_jobs_cannot_outvote_a_large_repeat_job(self):
        slow_short = (EtaTimingSample(4, 1, 40),) * 80
        estimate = estimate_workloads_seconds((EtaWorkload(300, 300),), slow_short + self.samples)
        self.assertEqual(458, estimate.seconds_expected)

    def test_actual_dispatch_count_distinguishes_one_copy_batch_from_300_jobs(self):
        one = estimate_workloads_seconds((EtaWorkload(300, 300),))
        separate = estimate_workloads_seconds((EtaWorkload(300, 300, 300),))
        self.assertGreater(separate.seconds_expected - one.seconds_expected, 2000)

    def test_short_job_learning_removes_prior_bulk_queue_wait(self):
        start = datetime(2026, 10, 7, 18, 24, tzinfo=LOCAL)
        previous_end = datetime(2026, 10, 7, 18, 30, 42, tzinfo=LOCAL)
        end = previous_end + timedelta(seconds=13)
        sample = completed_job_timing(start, end, 5, 1, previous_completed_at=previous_end)
        self.assertIsNotNone(sample)
        self.assertEqual(13, sample.seconds)
        self.assertEqual(13, estimate_workloads_seconds((EtaWorkload(5),), (sample,)).seconds_expected)

    def test_future_completion_and_zero_duration_are_not_learning_evidence(self):
        now = datetime.now(LOCAL)
        self.assertIsNone(completed_job_timing(now, now, 5, 1))
        self.assertIsNone(completed_job_timing(now, now + timedelta(seconds=10), 5, 1,
                                               previous_completed_at=now + timedelta(seconds=20)))

    def test_running_work_is_deducted_but_waiting_jobs_keep_full_duration(self):
        work = (EtaWorkload(300, 300), EtaWorkload(5))
        before = estimate_workloads_seconds(work, self.samples)
        after = estimate_workloads_seconds(work, self.samples, elapsed_seconds=(180, 0))
        self.assertEqual(before.seconds_expected - 180, after.seconds_expected)

    def test_append_adds_new_work_without_restarting_the_active_document(self):
        work = (EtaWorkload(300, 300),)
        before = estimate_workloads_seconds(work, self.samples)
        after = estimate_workloads_seconds(work + (EtaWorkload(5),), self.samples,
                                           elapsed_seconds=(180, 0))
        self.assertAlmostEqual(before.seconds_expected - 180 + 12.488, after.seconds_expected, delta=1)

    def test_an_unmatched_duplex_mode_uses_initial_bounds(self):
        estimate = estimate_workloads_seconds((EtaWorkload(300, 300, duplex=False),), self.samples)
        self.assertEqual("초기 추정", estimate.confidence)
        self.assertLess(estimate.seconds_low, estimate.seconds_high)

    def test_empty_work_and_overrun_never_create_negative_durations(self):
        self.assertEqual(0, estimate_workloads_seconds(()).seconds_expected)
        estimate = estimate_workloads_seconds((EtaWorkload(5),), self.samples, elapsed_seconds=(100,))
        self.assertEqual((0, 0, 0), (estimate.seconds_low, estimate.seconds_high, estimate.seconds_expected))

    def test_progress_only_observations_keep_a_stable_completion_anchor(self):
        now = datetime.now(LOCAL)
        first_eta = estimate_workloads_seconds((EtaWorkload(300, 300),), self.samples)
        later_eta = estimate_workloads_seconds((EtaWorkload(300, 300),), self.samples, elapsed_seconds=(60,))
        key = ("run", "job", self.samples, False, True, "job")
        first = PrintEtaObservation("run", first_eta, now, ("job",), (), 300, projection_key=key)
        later = PrintEtaObservation("run", later_eta, now + timedelta(seconds=60), ("job",), (), 300, projection_key=key)
        self.assertEqual(first.anchor_key, later.anchor_key)
        self.assertEqual(CompletionTimeProjection(first_eta, now).completion_at,
                         CompletionTimeProjection(later_eta, later.observed_at).completion_at)


class ActivePrintTimeTests(unittest.TestCase):
    def test_first_observation_recovers_printing_during_next_pdf_preparation(self):
        clock = ActivePrintTime()
        clock.observe("a", 90, started_at=0)
        self.assertEqual(90, clock.elapsed("a"))
        clock.observe("a", 100, started_at=0)
        self.assertEqual(100, clock.elapsed("a"))
        clock.observe(None, 100)
        clock.observe("a", 200, started_at=0)
        self.assertEqual(100, clock.elapsed("a"))

    def test_queued_hold_resume_and_job_switch_do_not_spend_other_jobs_time(self):
        clock = ActivePrintTime()
        clock.observe(None, 0)
        clock.observe(None, 30)
        self.assertEqual(0, clock.elapsed("a"))
        clock.observe("a", 30)
        clock.observe("a", 40)
        clock.observe(None, 40)
        clock.observe(None, 100)
        clock.observe("a", 100)
        clock.observe("a", 110)
        self.assertEqual(20, clock.elapsed("a"))
        clock.observe("b", 110)
        clock.observe("b", 120)
        self.assertEqual(20, clock.elapsed("a"))
        self.assertEqual(10, clock.elapsed("b"))


class TimingProfileTests(unittest.TestCase):
    def test_bootstrap_is_device_scoped_and_preserves_newer_config_learning(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            samples = [{"sheets": 300, "copies": 300, "seconds": 458.312, "duplex": True}]
            key = timing_device_key("http://printer-a")
            path.write_text(json.dumps({"device_key": key, "samples": samples}), encoding="utf-8")
            config = {"base_url": "http://printer-a"}
            initialize_timing_samples(config, path)
            self.assertEqual(samples, config[ETA_TIMING_CONFIG_KEY])
            config[ETA_TIMING_CONFIG_KEY] = [dict(samples[0], seconds=440)]
            initialize_timing_samples(config, path)
            self.assertEqual(440, config[ETA_TIMING_CONFIG_KEY][0]["seconds"])
            other = {"base_url": "http://printer-b"}
            initialize_timing_samples(other, path)
            self.assertNotIn(ETA_TIMING_CONFIG_KEY, other)

    def test_malformed_nonfinite_and_oversized_calibration_is_disposable(self):
        valid = {"sheets": 10, "copies": 1, "seconds": 20.0, "duplex": True}
        values = [valid, dict(valid, seconds=float("nan")), dict(valid, copies=0), dict(valid, sheets=True), "bad"]
        self.assertEqual((EtaTimingSample(10, 1, 20),), validated_timing_samples(values))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            config = {"base_url": "http://printer-a"}
            path.write_text("{" * (64 * 1024 + 1), encoding="utf-8")
            initialize_timing_samples(config, path)
            self.assertEqual({"base_url": "http://printer-a"}, config)
            path.write_text("not json", encoding="utf-8")
            initialize_timing_samples(config, path)
            self.assertEqual({"base_url": "http://printer-a"}, config)

    def test_manager_keeps_short_copy_counts_and_rejects_cold_first_short_sample(self):
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.device_monitor_config = {"base_url": "http://printer-a"}
        start = datetime.now(LOCAL)
        job = DeviceJob("redacted", state=DeviceJobState.COMPLETED, started_at=start, ended_at=start + timedelta(seconds=40))
        self.assertIsNone(app.learn_completed_device_job_rate(job, 5))
        bulk = DeviceJob("redacted", state=DeviceJobState.COMPLETED, started_at=start, ended_at=start + timedelta(seconds=450))
        sample = app.learn_completed_device_job_rate(bulk, 300, copies=300)
        self.assertEqual(300, sample.copies)
        self.assertEqual((sample,), app.eta_job_timing_samples())
        app.device_monitor_config["base_url"] = "http://printer-b"
        self.assertEqual((), app.eta_job_timing_samples())


if __name__ == "__main__":
    unittest.main()
