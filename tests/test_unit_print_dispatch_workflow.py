from __future__ import annotations

import re
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pypdf import PdfWriter

from test_unit_print_queue_lifecycle import make_app
from unit_print_device_monitor import DeviceWaitResult
from unit_print_device_state import DeviceHealth, DeviceJob, DeviceJobState, DeviceSnapshot
from unit_print_history import HistoryWriteError, PrintHistory, read_history
from unit_print_manager import ExamPdf, PrintBlock, PrintRunOptions


class ImmediateRoot:
    def after(self, _delay, callback):
        callback()


class ScriptedMonitor:
    """Exercise the real rolling worker without network, sleep, or printing."""

    def __init__(self, app, *, acceptance_timeout=False, fresh_timeout=False, hold=False):
        self.app = app
        self.client = SimpleNamespace(base_url="http://192.168.0.248:8000")
        self.acceptance_timeout = acceptance_timeout
        self.fresh_timeout = fresh_timeout
        self.hold_steps = ["held", "resumed"] if hold else []
        self.events = []
        self.accepted_tokens = []
        self.sent_tokens = []
        self.pending_tokens = []
        self.completed_tokens = []
        self.completion_batch_size = None
        self.pause_count = 0
        self._snapshot = DeviceSnapshot(
            observed_at=datetime.now().astimezone(),
            health=DeviceHealth.READY,
            authenticated=True,
        )

    @property
    def latest_snapshot(self):
        return self._snapshot

    def refresh_now(self):
        pass

    def pause_full_snapshots(self):
        self.pause_count += 1

    def resume_full_snapshots(self):
        self.pause_count -= 1

    @staticmethod
    def job(token, state):
        return DeviceJob(f"QP_{token}_exam.pdf", state=state, dispatch_token=token)

    def send(self, _executable, paths, _printer, **_options):
        # Canon must acknowledge every preceding app job before another send.
        if self.sent_tokens != self.accepted_tokens:
            raise AssertionError("A later job crossed the Canon acknowledgement barrier")
        token = re.search(r"QP_([A-Z0-9]+)_", paths[0].name).group(1)
        self.events.append(("send", token))
        self.sent_tokens.append(token)
        self.pending_tokens.append(token)
        return 1

    def wait_for_job_acceptance(self, token, *, cancel_event, **_options):
        if cancel_event.is_set():
            return DeviceWaitResult(self._snapshot, cancelled=True)
        if self.acceptance_timeout:
            return DeviceWaitResult(self._snapshot, timed_out=True)
        self.events.append(("acknowledge", token))
        self.accepted_tokens.append(token)
        return DeviceWaitResult(self._snapshot, job=self.job(token, DeviceJobState.QUEUED))

    def wait_for_change(self, _observed_at, _timeout):
        if self.fresh_timeout:
            return self._snapshot
        health = DeviceHealth.READY
        errors = ()
        state = DeviceJobState.QUEUED
        if self.pending_tokens and self.hold_steps:
            step = self.hold_steps.pop(0)
            self.events.append((step, tuple(self.pending_tokens)))
            if step == "held":
                health = DeviceHealth.ERROR
                errors = ("스테이플을 보급해 주세요",)
                state = DeviceJobState.HELD
            else:
                self.app.device_resume_event.set()
        else:
            count = len(self.pending_tokens) if self.completion_batch_size is None else self.completion_batch_size
            self.completed_tokens.extend(self.pending_tokens[:count])
            del self.pending_tokens[:count]
        self._snapshot = DeviceSnapshot(
            observed_at=self._snapshot.observed_at + timedelta(seconds=1),
            health=health,
            errors=errors,
            active_jobs=tuple(self.job(token, state) for token in self.pending_tokens),
            recent_jobs=tuple(self.job(token, DeviceJobState.COMPLETED) for token in self.completed_tokens),
            authenticated=True,
        )
        return self._snapshot


class RollingDispatchWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.pdf = self.directory / "시험지.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        with self.pdf.open("wb") as stream:
            writer.write(stream)
        self.blocks = [
            PrintBlock("대수", "상", "1", 1, 1, 1, [ExamPdf(1, f"시험지 {index}", self.pdf)])
            for index in range(4)
        ]
        self.app = make_app(self.blocks)
        self.app.root = ImmediateRoot()
        self.app.is_closing = False
        self.app.print_history = PrintHistory(self.directory / "history")
        self.app.history_run_id = None
        self.app.device_resume_event = threading.Event()
        self.app.device_hold_active = False
        self.app.device_hold_message = ""
        self.app.device_monitor_config = {}
        self.app.enter_device_hold = Mock()
        self.app.route_job_printer = Mock(return_value="FAKE 세로")
        self.app.set_taskbar_print_progress = Mock()
        self.options = PrintRunOptions(
            "FAKE", "sumatra", "FAKE.exe", True, False, 3, 1,
            device_completion_mode=True,
            device_window_size=2,
            device_monitor_base_url="http://192.168.0.248:8000",
            device_verified_printers=frozenset({"FAKE", "FAKE 세로"}),
        )
        self.app.begin_history_run(self.options)
        self.jobs = tuple(self.app.initialize_active_print_queue(self.blocks, False))
        self.app.print_run_options = self.options
        self.app.is_printing = True
        self.monitor = ScriptedMonitor(self.app)
        self.app.device_monitor = self.monitor
        self.enqueue = patch("unit_print_manager.sumatra_enqueue_pdfs", side_effect=self.monitor.send).start()
        self.addCleanup(patch.stopall)
        patch("unit_print_manager.DEVICE_DISPATCH_SPOOL_DIR", self.directory / "spool").start()
        for name in ("shell_print_pdf", "save_config", "load_printers", "get_print_queue_count", "run_powershell"):
            patch("unit_print_manager." + name, side_effect=AssertionError(name)).start()
        patch("unit_print_manager.messagebox.showerror").start()

    def run_worker(self):
        # print_blocks is the actual public worker entry; it must choose rolling.
        self.app.print_blocks(self.blocks, self.options)
        self.assertFalse(self.app.is_printing)
        self.assertFalse(self.app.print_accepting_appends)
        self.assertEqual([], self.app.print_pending_jobs)
        self.assertEqual(0, self.monitor.pause_count)
        return read_history(self.directory / "history")

    def test_acknowledged_window_finishes_only_after_exact_physical_completions(self):
        records = self.run_worker()
        self.assertEqual(4, self.enqueue.call_count)
        self.assertEqual(self.monitor.sent_tokens, self.monitor.accepted_tokens)
        self.assertEqual(["send", "acknowledge"] * 4, [event[0] for event in self.monitor.events])
        completed = [record for record in records if record["event"] == "job_physically_completed"]
        self.assertEqual([job.job_id for job in self.jobs], [record["job_id"] for record in completed])
        self.assertEqual("device_jobs_completed", records[-1]["outcome"])
        self.assertTrue(records[-1]["physical_output_verified"])
        self.assertEqual(4, records[-1]["completed_jobs"])
        self.assertTrue(all(self.app.queue_block_status(block) == "완료" for block in self.blocks))
        self.assertIsNone(self.app.retry_batch)

    def test_acceptance_timeout_excludes_sent_job_and_keeps_only_unsent_suffix(self):
        self.monitor.acceptance_timeout = True
        records = self.run_worker()
        self.assertEqual(1, self.enqueue.call_count)
        self.assertEqual([job.job_id for job in self.jobs[1:]], [job.job_id for job in self.app.retry_batch.jobs])
        self.assertEqual("run_failed", records[-1]["event"])
        self.assertEqual(1, records[-1]["submitted_jobs"])
        self.assertEqual(0, records[-1]["completed_jobs"])
        self.assertNotIn("job_physically_completed", [record["event"] for record in records])
        self.assertEqual(1, len(records[-1]["outstanding_tokens"]))

    def test_block_completion_requires_every_job_while_other_blocks_are_pending(self):
        self.blocks[0].files.append(ExamPdf(2, "첫 블록의 두 번째 파일", self.pdf))
        self.jobs = tuple(self.app.initialize_active_print_queue(self.blocks, False))
        self.monitor.completion_batch_size = 1
        observations = []
        real_mark = self.app.mark_block_status

        def observe_completion(uid, status, detail=""):
            if status == "완료":
                observations.append((uid, frozenset(self.monitor.completed_tokens)))
            return real_mark(uid, status, detail)

        self.app.mark_block_status = observe_completion
        records = self.run_worker()
        token_by_job_id = {
            record["job_id"]: record["dispatch_token"]
            for record in records
            if record["event"] == "job_submitted"
        }
        for uid, observed_tokens in observations:
            expected_tokens = {
                token_by_job_id[job.job_id]
                for job in self.jobs
                if job.block_uid == uid
            }
            self.assertTrue(expected_tokens)
            self.assertLessEqual(expected_tokens, observed_tokens)
        first_completion = next(tokens for uid, tokens in observations if uid == id(self.blocks[0]))
        self.assertEqual(2, len(first_completion))
        self.assertLess(len(first_completion), len(self.jobs))
        self.assertEqual(5, records[-1]["completed_jobs"])

    def test_unknown_send_excludes_attempt_without_claiming_physical_completion(self):
        self.enqueue.side_effect = RuntimeError("sender timeout")
        records = self.run_worker()
        self.assertEqual(1, self.enqueue.call_count)
        self.assertEqual([job.job_id for job in self.jobs[1:]], [job.job_id for job in self.app.retry_batch.jobs])
        unconfirmed = next(record for record in records if record["event"] == "job_unconfirmed")
        self.assertFalse(unconfirmed["automatic_retry_allowed"])
        self.assertNotIn("job_submitted", [record["event"] for record in records])
        self.assertNotIn("job_physically_completed", [record["event"] for record in records])
        self.assertEqual(0, records[-1]["submitted_jobs"])
        self.assertEqual(1, records[-1]["uncertain_jobs"])
        self.assertEqual([self.jobs[0].job_id], records[-1]["uncertain_job_ids"])
        self.assertEqual("dispatch_uncertain", records[-1]["phase"])

    def test_required_history_failure_before_attempt_keeps_every_job_retryable(self):
        real_record = self.app.print_history.record

        def record(event, **data):
            if event == "job_attempt":
                raise HistoryWriteError(self.directory, OSError("disk full"))
            return real_record(event, **data)

        with patch.object(self.app.print_history, "record", side_effect=record):
            self.run_worker()
        self.enqueue.assert_not_called()
        self.assertEqual([job.job_id for job in self.jobs], [job.job_id for job in self.app.retry_batch.jobs])

    def test_required_history_failure_after_send_never_retries_sent_job(self):
        real_record = self.app.print_history.record

        def record(event, **data):
            if event == "job_submitted":
                raise HistoryWriteError(self.directory, OSError("disk full"))
            return real_record(event, **data)

        with patch.object(self.app.print_history, "record", side_effect=record):
            records = self.run_worker()
        self.enqueue.assert_called_once()
        self.assertEqual([job.job_id for job in self.jobs[1:]], [job.job_id for job in self.app.retry_batch.jobs])
        self.assertEqual("submission_history", records[-1]["phase"])

    def test_hold_requires_recovery_before_refilling_and_does_not_resend(self):
        self.monitor.hold_steps = ["held", "resumed"]
        records = self.run_worker()
        self.assertEqual(1, self.app.enter_device_hold.call_count)
        events = [event[0] for event in self.monitor.events]
        self.assertEqual(["send", "acknowledge"] * 2, events[:4])
        self.assertEqual(["held", "resumed"], events[4:6])
        self.assertEqual(["send", "acknowledge"] * 2, events[6:])
        self.assertEqual(4, len(set(self.monitor.sent_tokens)))
        names = [record["event"] for record in records]
        self.assertLess(names.index("device_dispatch_held"), names.index("device_dispatch_resumed"))
        self.assertTrue(records[-1]["physical_output_verified"])

    def test_first_dispatch_requires_a_fresh_device_observation(self):
        self.monitor.fresh_timeout = True
        records = self.run_worker()
        self.enqueue.assert_not_called()
        self.assertEqual([job.job_id for job in self.jobs], [job.job_id for job in self.app.retry_batch.jobs])
        self.assertEqual("run_failed", records[-1]["event"])
        self.assertEqual(0, records[-1]["submitted_jobs"])

    def test_unverified_run_stops_before_any_send_and_keeps_jobs_retryable(self):
        from dataclasses import replace
        self.options = replace(self.options, device_verified_printers=frozenset())
        self.app.print_run_options = self.options
        records = self.run_worker()
        self.enqueue.assert_not_called()
        self.assertEqual([job.job_id for job in self.jobs], [job.job_id for job in self.app.retry_batch.jobs])
        self.assertEqual("run_failed", records[-1]["event"])
        self.assertEqual(0, records[-1]["submitted_jobs"])

    def test_changed_monitor_address_stops_before_send(self):
        self.monitor.client.base_url = "http://192.168.0.20:8000"
        records = self.run_worker()
        self.enqueue.assert_not_called()
        self.assertEqual("run_failed", records[-1]["event"])
        self.assertEqual(0, records[-1]["submitted_jobs"])

    def test_unapproved_destination_stops_without_dispatch(self):
        self.app.route_job_printer = Mock(return_value="Other printer")
        records = self.run_worker()
        self.enqueue.assert_not_called()
        self.assertEqual([job.job_id for job in self.jobs], [job.job_id for job in self.app.retry_batch.jobs])
        self.assertEqual("run_failed", records[-1]["event"])

    def test_cancel_after_acknowledgement_keeps_sent_job_out_of_retry(self):
        real_accept = self.monitor.wait_for_job_acceptance

        def acknowledge_then_cancel(*args, **options):
            result = real_accept(*args, **options)
            self.app.print_cancel_event.set()
            return result

        self.monitor.wait_for_job_acceptance = acknowledge_then_cancel
        records = self.run_worker()
        self.enqueue.assert_called_once()
        self.assertEqual([job.job_id for job in self.jobs[1:]], [job.job_id for job in self.app.retry_batch.jobs])
        self.assertEqual("run_cancelled", records[-1]["event"])
        self.assertEqual(1, records[-1]["submitted_jobs"])
        self.assertEqual(0, records[-1]["completed_jobs"])
        self.assertEqual(0, records[-1]["uncertain_jobs"])
        self.assertEqual(1, len(records[-1]["outstanding_tokens"]))

    def test_preparation_cleanup_failure_still_resets_active_worker_state(self):
        service = Mock()
        service.resolve_job.side_effect = lambda job: job.item.path
        service.close.side_effect = RuntimeError("cleanup failed")
        with patch("unit_print_manager.PrintPreparationService", return_value=service):
            with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                self.app.print_blocks(self.blocks, self.options)
        self.assertFalse(self.app.is_printing)
        self.assertFalse(self.app.print_accepting_appends)
        self.assertEqual([], self.app.print_pending_jobs)
        self.assertEqual(set(), self.app.print_scheduled_block_ids)
        self.assertEqual(set(), self.app.print_started_block_ids)
        self.assertIsNone(self.app.print_run_options)
        self.assertIsNone(self.app.active_print_block_uid)
        self.assertTrue(self.app.device_resume_event.is_set())
        self.assertEqual(0, self.monitor.pause_count)


if __name__ == "__main__":
    unittest.main()
