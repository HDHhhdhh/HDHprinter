"""Unsent edits exercise the real queue commit/claim boundary without printing."""
from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pypdf import PdfWriter

from test_unit_print_queue_lifecycle import Variable, make_app
from unit_print_manager import ExamPdf, PrintBlock
from unit_print_queue_state import replace_unstarted_block_jobs, waiting_blocks
from unit_print_retry import RetryBatch


class ActiveQueueEditTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.addCleanup(patch.stopall)
        self.root = Path(temporary.name)
        self.blocks = []
        for index in range(4):
            path = self.root / f"{index}.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=595, height=842)
            with path.open("wb") as stream:
                writer.write(stream)
            self.blocks.append(PrintBlock(
                "자료", "", "직접", 1, 1, 2, [ExamPdf(1, str(index), path)],
                source="custom", printer_route="세로",
            ))
        self.app = make_app(self.blocks)
        self.app.is_closing = False
        self.app.block_preparation_epoch = 0
        self.app.group_build_pending = False
        self.app.printer_var = Variable("FAKE")
        self.app.queue_canvas = Mock()
        self.app.record_history = Mock()
        self.app.refresh_retry_button = Mock()
        self.app.submit_background_block_build = Mock()
        for name in ("showinfo", "showwarning", "showerror"):
            patch("unit_print_manager.messagebox." + name).start()
        for name in ("sumatra_enqueue_pdfs", "save_config",
                     "get_print_queue_count", "run_powershell"):
            patch("unit_print_manager." + name, side_effect=AssertionError(name)).start()
        self.make_name = patch("unit_print_manager.materialize_name_separator_pdf",
                               return_value=self.blocks[0].files[0].path).start()

    def start(self, registered=None, *, names=False):
        jobs = self.app.initialize_active_print_queue(
            self.blocks if registered is None else registered, False, names,
        )
        self.app.is_printing = True
        self.assertTrue(self.app.claim_active_print_job(jobs[0], 0))
        return tuple(jobs)

    def deliver_group(self):
        work, done = self.app.submit_background_block_build.call_args.args
        future = Future()
        future.set_result(work())
        done(future)

    def select(self, *indices):
        self.app.selected_block_indices = set(indices)
        self.app.selected_block_index_value = indices[-1]

    def test_menu_enables_both_actions_for_reserved_blocks_during_another_print(self):
        self.start()
        self.select(1, 2)
        with patch("unit_print_manager.tk.Menu") as menu:
            self.app.post_queue_context_menu(0, 0)
        commands = {call.kwargs.get("label"): call.kwargs
                    for call in menu.return_value.add_command.call_args_list}
        self.assertEqual("normal", commands["묶기"]["state"])
        self.assertEqual("normal", commands["이름 채우기"]["state"])

    def test_menu_and_commands_protect_claimed_blocks_before_badges_update(self):
        jobs = self.start()
        self.select(1, 2)
        self.blocks[2].name_separator_label = "학생A"
        self.assertTrue(self.app.claim_active_print_job(jobs[1], 1))
        self.assertEqual("예약", self.app.block_status_by_id[id(self.blocks[1])])
        with patch("unit_print_manager.tk.Menu") as menu:
            self.app.post_queue_context_menu(0, 0)
        commands = {call.kwargs.get("label"): call.kwargs
                    for call in menu.return_value.add_command.call_args_list}
        self.assertEqual("disabled", commands["묶기"]["state"])
        self.assertEqual("disabled", commands["이름 채우기"]["state"])
        self.assertFalse(self.app.group_selected_blocks())
        self.assertFalse(self.app.fill_selected_block_names())
        self.assertEqual(self.blocks, self.app.blocks)
        self.assertEqual(jobs, tuple(self.app.print_pending_jobs))

    def test_group_rewrites_only_reserved_jobs_and_preserves_the_started_prefix(self):
        jobs = self.start()
        self.select(1, 2)
        self.assertTrue(self.app.group_selected_blocks())
        self.deliver_group()
        bundle = self.app.blocks[1]
        self.assertEqual([self.blocks[0], bundle, self.blocks[3]], self.app.blocks)
        pending = self.app.print_pending_jobs
        self.assertIs(jobs[0], pending[0])
        self.assertIs(jobs[3], pending[-1])
        paths = [block.files[0].path for block in self.blocks[1:3]]
        self.assertEqual(paths * 2, [job.item.path for job in pending[1:-1]])
        self.assertTrue(all(job.copies == 1 for job in pending[1:-1]))
        self.assertEqual("예약", self.app.block_status_by_id[id(bundle)])
        self.assertIn(id(bundle), self.app.print_scheduled_block_ids)
        self.assertNotIn(id(self.blocks[1]), self.app.print_scheduled_block_ids)
        self.assertFalse(self.app.claim_active_print_job(jobs[1], 1))

    def test_new_waiting_group_does_not_enter_the_active_run(self):
        jobs = self.start([self.blocks[0]])
        self.select(1, 2)
        self.assertTrue(self.app.group_selected_blocks())
        self.deliver_group()
        bundle = self.app.blocks[1]
        self.assertEqual(jobs, tuple(self.app.print_pending_jobs))
        self.assertNotIn(id(bundle), self.app.print_scheduled_block_ids)
        self.assertEqual("대기", self.app.block_status_by_id[id(bundle)])
        self.assertEqual([bundle, self.blocks[3]], waiting_blocks(
            self.app.blocks, self.app.block_status_by_id, self.app.print_scheduled_block_ids,
        ))

    def test_mixed_reserved_and_new_waiting_group_stays_in_the_reserved_position(self):
        jobs = self.start(self.blocks[:2])
        self.select(1, 2)
        self.assertTrue(self.app.group_selected_blocks())
        self.deliver_group()
        bundle = self.app.blocks[1]
        self.assertIs(jobs[0], self.app.print_pending_jobs[0])
        self.assertEqual([self.blocks[1].files[0].path, self.blocks[2].files[0].path] * 2,
                         [job.item.path for job in self.app.print_pending_jobs[1:]])
        self.assertIn(id(bundle), self.app.print_scheduled_block_ids)
        self.assertEqual([self.blocks[3]], waiting_blocks(
            self.app.blocks, self.app.block_status_by_id, self.app.print_scheduled_block_ids,
        ))

    def test_group_drops_result_if_a_selected_job_started_during_preparation(self):
        jobs = self.start()
        self.select(1, 2)
        self.assertTrue(self.app.group_selected_blocks())
        self.assertTrue(self.app.claim_active_print_job(jobs[1], 1))
        self.deliver_group()
        self.assertEqual(self.blocks, self.app.blocks)
        self.assertEqual(jobs, tuple(self.app.print_pending_jobs))
        self.assertFalse(self.app.group_build_pending)

    def test_mixed_group_keeps_dispatch_position_even_if_new_block_precedes_the_active_block(self):
        for block, name in zip(self.blocks, ("학생A", "학생C", "학생B", "학생A")):
            block.name_separator_label = name
        jobs = self.start(self.blocks[1:], names=True)
        self.select(0, 3)
        self.assertTrue(self.app.group_selected_blocks())
        self.deliver_group()
        bundle = self.app.blocks[0]
        pending = self.app.print_pending_jobs
        self.assertEqual(jobs[:4], tuple(pending[:4]))
        self.assertEqual(["학생C", "학생B", "학생A"],
                         [job.history_block["student_name"] for job in pending if job.is_name_separator])
        self.assertEqual([self.blocks[0].files[0].path, self.blocks[3].files[0].path] * 2,
                         [job.item.path for job in pending if job.block_uid == id(bundle)
                          and not job.is_name_separator])

    def test_name_fill_retargets_jobs_and_moves_one_name_separator_to_the_group_start(self):
        self.blocks[0].name_separator_label = "학생B"
        self.blocks[2].name_separator_label = "학생A"
        jobs = self.start(names=True)
        self.select(1, 2, 3)
        stale = next(job for job in jobs if job.block_uid == id(self.blocks[1]))
        stale_index = jobs.index(stale)
        donor_body = next(job for job in jobs if job.block_uid == id(self.blocks[2])
                          and not job.is_name_separator)
        self.assertTrue(self.app.fill_selected_block_names())
        pending = self.app.print_pending_jobs
        self.assertIs(jobs[0], pending[0])
        self.assertIs(jobs[1], pending[1])
        names = [job for job in pending[2:] if job.is_name_separator]
        self.assertEqual(1, len(names))
        self.assertEqual(id(self.app.blocks[1]), names[0].block_uid)
        self.assertEqual("학생A", names[0].history_block["student_name"])
        self.assertIn(donor_body, pending)
        self.assertTrue(any(job is donor_body for job in pending))
        self.assertEqual(["학생B", "학생A", "학생A", "학생A"],
                         [block.name_separator_label for block in self.app.blocks])
        self.assertFalse(self.app.claim_active_print_job(stale, stale_index))
        self.assertNotIn(id(self.blocks[1]), self.app.print_scheduled_block_ids)
        self.assertEqual("학생A", self.app.print_last_name_separator_label)
        self.assertIn(stale.job_id, self.app.record_history.call_args.kwargs["removed_job_ids"])

    def test_name_fill_works_for_new_waiting_blocks_using_an_active_donor(self):
        self.blocks[0].name_separator_label = "학생A"
        jobs = self.start([self.blocks[0]])
        self.select(0, 1, 2)
        self.assertTrue(self.app.fill_selected_block_names())
        self.assertEqual(jobs, tuple(self.app.print_pending_jobs))
        self.assertIs(self.blocks[0], self.app.blocks[0])
        self.assertEqual(["학생A", "학생A", "학생A"],
                         [block.name_separator_label for block in self.app.blocks[:3]])
        self.assertNotIn(id(self.app.blocks[1]), self.app.print_scheduled_block_ids)

    def test_name_fill_rechecks_claim_after_preparing_the_name_pdf(self):
        self.blocks[0].name_separator_label = "학생B"
        self.blocks[2].name_separator_label = "학생A"
        jobs = self.start(names=True)
        self.select(1, 2)
        target = next(job for job in jobs if job.block_uid == id(self.blocks[1]))
        def claim_then_return(_label, _cache):
            self.assertTrue(self.app.claim_active_print_job(target, jobs.index(target)))
            return self.blocks[0].files[0].path
        self.make_name.side_effect = claim_then_return
        self.assertFalse(self.app.fill_selected_block_names())
        self.assertEqual(self.blocks, self.app.blocks)
        self.assertEqual(jobs, tuple(self.app.print_pending_jobs))

    def test_retry_targets_cannot_be_grouped_even_with_a_waiting_badge(self):
        jobs = self.app.build_print_jobs(self.blocks)
        self.app.retry_batch = RetryBatch((jobs[1],))
        self.select(1, 2)
        self.assertFalse(self.app.group_selected_blocks())
        self.assertEqual(self.blocks, self.app.blocks)


class UnstartedJobSpliceTest(unittest.TestCase):
    def job(self, uid, name):
        return SimpleNamespace(block_uid=uid, job_id=name)

    def test_splice_keeps_unselected_jobs_and_replaces_noncontiguous_members_once(self):
        original = tuple(self.job(uid, name) for uid, name in
                         ((1,"prefix"),(2,"old-a"),(2,"old-b"),(4,"keep"),(3,"old-c")))
        replacement = tuple(self.job(8, name) for name in ("new-a", "new-b"))
        result = replace_unstarted_block_jobs(original, {2: replacement, 3: ()}, {1})
        self.assertEqual((original[0], *replacement, original[3]), result)
        self.assertIs(original[0], result[0])
        self.assertIs(original[3], result[-1])

    def test_claimed_prefix_and_missing_reservation_fail_before_any_splice(self):
        original = (self.job(2, "before"), self.job(1, "claimed"), self.job(3, "after"))
        for uid in (1, 2, 99):
            with self.subTest(uid=uid), self.assertRaises(ValueError):
                replace_unstarted_block_jobs(original, {uid: ()}, {1})


if __name__ == "__main__":
    unittest.main()
