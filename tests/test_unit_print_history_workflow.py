from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pypdf import PdfWriter

from test_unit_print_queue_lifecycle import make_app
from unit_print_history import HistoryWriteError, PrintHistory, read_history
from unit_print_manager import ExamPdf, PrintBlock, PrintRunOptions


class ImmediateRoot:
    def after(self, _delay, callback):
        callback()


class PrintHistoryWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.pdf = self.directory / "한글 시험지.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        with self.pdf.open("wb") as stream:
            writer.write(stream)
        self.block = PrintBlock("대수", "상", "1", 1, 1, 2,
                                [ExamPdf(1, "시험지", self.pdf)], name_separator_label="김학생",
                                order_id="ORDER-001", material_id="MATERIAL-001")
        self.app = make_app([self.block])
        self.app.root = ImmediateRoot()
        self.app.is_closing = False
        self.app.print_history = PrintHistory(self.directory / "history")
        self.app.history_run_id = None
        self.app._history_queue_snapshot = {}
        self.app.route_job_printer = Mock(return_value="FAKE 세로")
        self.app.queue_file_line_label = lambda item: item.name
        self.app.wait_for_queue_refill_window = Mock(return_value=2)
        self.app.wait_for_print_queue_empty = Mock(return_value=True)
        self.options = self.app.print_run_options
        self.enqueue = patch("unit_print_manager.sumatra_enqueue_pdfs", return_value=1).start()
        self.addCleanup(patch.stopall)
        for name in ("shell_print_pdf", "save_config", "load_printers", "get_print_queue_count", "run_powershell"):
            patch("unit_print_manager." + name, side_effect=AssertionError(name)).start()
        for name in ("showwarning", "showerror", "showinfo"):
            patch("unit_print_manager.messagebox." + name).start()

    def prepare(self, *, dry_run=False):
        options = PrintRunOptions("FAKE", "sumatra", "FAKE.exe", True, dry_run, 3, 1)
        self.app.begin_history_run(options)
        self.app.initialize_active_print_queue(self.app.blocks, False)
        self.app.print_run_options = options
        self.app.is_printing = True
        return options

    def records(self):
        # Read using a separate object/path after the worker finishes.
        return read_history(self.directory / "history")

    def test_success_records_identity_copies_file_hash_and_queue_observation(self):
        self.app.capture_queue_history()
        options = self.prepare()
        thread = threading.Thread(target=self.app.print_blocks, args=(self.app.blocks, options))
        thread.start()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        records = self.records()
        names = [r["event"] for r in records]
        self.assertIn("block_added", names)
        self.assertLess(names.index("job_attempt"), names.index("job_submitted"))
        sent = next(r for r in records if r["event"] == "job_submitted")
        self.assertEqual("김학생", sent["student_name"])
        self.assertEqual(2, sent["copies"])
        self.assertEqual("ORDER-001", sent["order_id"])
        self.assertEqual("MATERIAL-001", sent["material_id"])
        self.assertEqual(str(self.pdf), sent["pdf_path"])
        self.assertEqual(64, len(sent["pdf_sha256"]))
        attempt = next(r for r in records if r["event"] == "job_attempt")
        self.assertEqual(attempt["attempt_id"], sent["attempt_id"])
        self.assertEqual(attempt["job_id"], sent["job_id"])
        self.assertEqual("windows_queues_empty", records[-1]["outcome"])
        self.assertFalse(records[-1]["physical_output_verified"])
        self.enqueue.assert_called_once()

    def test_history_failure_before_attempt_prevents_dispatch_and_keeps_retry(self):
        options = self.prepare()
        real_record = self.app.print_history.record

        def record(event, **data):
            if event == "job_attempt":
                raise HistoryWriteError(self.directory, OSError("disk full"))
            return real_record(event, **data)

        with patch.object(self.app.print_history, "record", side_effect=record):
            self.app.print_blocks(self.app.blocks, options)
        self.enqueue.assert_not_called()
        self.assertEqual(1, len(self.app.retry_batch.jobs))
        self.assertEqual("run_failed", self.records()[-1]["event"])
        self.assertEqual(0, self.records()[-1]["submitted_jobs"])

    def test_history_failure_after_submission_never_retries_confirmed_job(self):
        second = PrintBlock("기하", "상", "1", 1, 1, 1, [ExamPdf(2, "두번째", self.pdf)])
        self.app.blocks.append(second)
        options = self.prepare()
        real_record = self.app.print_history.record

        def record(event, **data):
            if event == "job_submitted":
                raise HistoryWriteError(self.directory, OSError("disk full"))
            return real_record(event, **data)

        with patch.object(self.app.print_history, "record", side_effect=record):
            self.app.print_blocks(self.app.blocks, options)
        self.enqueue.assert_called_once()
        self.assertEqual([id(second)], [j.block_uid for j in self.app.retry_batch.jobs])
        self.assertEqual(1, self.records()[-1]["submitted_jobs"])
        self.assertEqual("submission_history", self.records()[-1]["phase"])

    def test_dispatch_error_records_unconfirmed_not_success(self):
        options = self.prepare()
        self.enqueue.side_effect = RuntimeError("sender timeout")
        self.app.print_blocks(self.app.blocks, options)
        records = self.records()
        names = [r["event"] for r in records]
        self.assertIn("job_unconfirmed", names)
        self.assertNotIn("job_submitted", names)
        self.assertIsNone(self.app.retry_batch)
        unconfirmed = next(record for record in records if record["event"] == "job_unconfirmed")
        self.assertFalse(unconfirmed["automatic_retry_allowed"])
        self.assertEqual(0, records[-1]["submitted_jobs"])
        self.assertEqual(1, records[-1]["uncertain_jobs"])
        self.assertEqual([unconfirmed["job_id"]], records[-1]["uncertain_job_ids"])
        self.assertEqual(0, records[-1]["remaining_jobs"])

    def test_uncertain_attempt_keeps_only_never_attempted_suffix_retryable(self):
        second = PrintBlock("기하", "상", "1", 1, 1, 1, [ExamPdf(2, "두번째", self.pdf)])
        third = PrintBlock("대수", "상", "1", 1, 1, 1, [ExamPdf(3, "세번째", self.pdf)])
        self.app.blocks.extend((second, third))
        options = self.prepare()
        jobs = tuple(self.app.print_pending_jobs)
        self.enqueue.side_effect = (1, RuntimeError("sender timeout"))
        self.app.print_blocks(self.app.blocks, options)
        self.assertEqual(2, self.enqueue.call_count)
        self.assertEqual([jobs[2].job_id], [job.job_id for job in self.app.retry_batch.jobs])
        records = self.records()
        self.assertEqual(1, records[-1]["submitted_jobs"])
        self.assertEqual(1, records[-1]["uncertain_jobs"])
        self.assertEqual([jobs[1].job_id], records[-1]["uncertain_job_ids"])
        self.assertEqual(1, records[-1]["remaining_jobs"])
        self.assertEqual("dispatch_uncertain", records[-1]["phase"])

    def test_unconfirmed_history_failure_cannot_put_attempt_back_into_retry(self):
        second = PrintBlock("기하", "상", "1", 1, 1, 1, [ExamPdf(2, "두번째", self.pdf)])
        self.app.blocks.append(second)
        options = self.prepare()
        real_record = self.app.print_history.record

        def record(event, **data):
            if event == "job_unconfirmed":
                raise HistoryWriteError(self.directory, OSError("disk full"))
            return real_record(event, **data)

        self.enqueue.side_effect = RuntimeError("sender timeout")
        with patch.object(self.app.print_history, "record", side_effect=record):
            self.app.print_blocks(self.app.blocks, options)
        self.assertEqual([id(second)], [job.block_uid for job in self.app.retry_batch.jobs])
        self.assertEqual(0, self.records()[-1]["submitted_jobs"])
        self.assertEqual(1, self.records()[-1]["uncertain_jobs"])

    def test_cancel_after_first_send_records_only_remaining_jobs_and_retry_links_job_id(self):
        second = PrintBlock("기하", "상", "1", 1, 1, 1, [ExamPdf(2, "두번째", self.pdf)])
        self.app.blocks.append(second)
        options = self.prepare()
        original_jobs = list(self.app.print_pending_jobs)

        def send(*args, **kwargs):
            self.app.print_cancel_event.set()
            return 1

        self.enqueue.side_effect = send
        self.app.print_blocks(self.app.blocks, options)
        self.assertEqual(1, self.records()[-1]["remaining_jobs"])
        self.assertEqual(original_jobs[1].job_id, self.app.retry_batch.jobs[0].job_id)
        self.assertEqual("run_cancelled", self.records()[-1]["event"])

    def test_preview_records_no_submissions_and_never_sends(self):
        options = self.prepare(dry_run=True)
        self.app.print_blocks(self.app.blocks, options)
        self.enqueue.assert_not_called()
        self.assertEqual("preview_only", self.records()[-1]["outcome"])
        self.assertNotIn("job_submitted", [r["event"] for r in self.records()])

    def test_appended_job_and_retry_each_leave_linked_persistent_events(self):
        options = self.prepare()
        extra = PrintBlock("기하", "상", "1", 1, 1, 1, [ExamPdf(2, "추가", self.pdf)])
        self.app.blocks.append(extra)
        with patch("unit_print_manager.messagebox.askyesno", return_value=True):
            self.app.append_new_blocks_to_active_print()
        jobs = list(self.app.print_pending_jobs)
        self.app.print_cancel_event.set()
        self.app.print_blocks(self.app.blocks, options)
        self.assertEqual([j.job_id for j in jobs], [j.job_id for j in self.app.retry_batch.jobs])
        self.assertIn("jobs_added", [r["event"] for r in self.records()])
        first_run_id = next(r["run_id"] for r in self.records() if r["event"] == "run_started")
        with patch("unit_print_manager.messagebox.askyesno", return_value=True), patch("unit_print_manager.threading.Thread"):
            self.app.retry_remaining_jobs()
        self.app.print_blocks(self.app.blocks, options)
        retry_run = [r for r in self.records() if r["event"] == "run_started"][-1]
        self.assertEqual("retry", retry_run["run_kind"])
        self.assertNotEqual(first_run_id, retry_run["run_id"])
        submitted = [r for r in self.records() if r["event"] == "job_submitted"]
        self.assertEqual([j.job_id for j in jobs], [r["job_id"] for r in submitted])

    def test_generated_separator_omr_and_four_up_jobs_are_distinguished(self):
        from unittest.mock import patch
        self.block.source = "test_school_district"
        self.block.pages_per_sheet = 4
        generated = self.directory / "layout.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=842, height=595)
        with generated.open("wb") as stream:
            writer.write(stream)
        self.app.begin_history_run(self.options)
        with patch("unit_print_manager.materialize_name_separator_pdf", return_value=self.pdf), \
             patch("unit_print_manager.ensure_separator_pdf", return_value=self.pdf), \
             patch.object(self.app, "make_automatic_omr_item", return_value=ExamPdf(0, "OMR", self.pdf)), \
             patch("unit_print_manager.block_requires_omr_sheet", return_value=True):
            self.app.initialize_active_print_queue(self.app.blocks, True, True)
        self.app.is_printing = True
        with patch("unit_print_manager.materialize_four_up_pdf", return_value=generated):
            self.app.print_blocks(self.app.blocks, self.options)
        sent = [r for r in self.records() if r["event"] == "job_submitted"]
        self.assertEqual(["name_separator", "omr", "pdf", "separator"], [r["kind"] for r in sent])
        pdf_record = sent[2]
        self.assertEqual(str(generated), pdf_record["printed_pdf_path"])
        self.assertNotEqual(pdf_record["pdf_sha256"], pdf_record["printed_pdf_sha256"])


if __name__ == "__main__":
    unittest.main()
