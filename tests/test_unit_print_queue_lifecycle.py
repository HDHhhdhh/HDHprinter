from __future__ import annotations

import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from unit_print_keyboard import KeyboardState
from unit_print_manager import ExamPdf, PrintBlock, PrintManagerApp, PrintRunOptions
from unit_print_retry import RetryBatch


class Variable:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def block(name):
    return PrintBlock(name, "", "", 1, 1, 1, [ExamPdf(1, name, Path(name + ".pdf"))])


def named_block(name, student):
    item = block(name)
    item.name_separator_label = student
    return item


def make_app(blocks):
    app = PrintManagerApp.__new__(PrintManagerApp)
    app.blocks = blocks
    app.root = Mock()
    # Existing domain fixtures run the same preparation/result/commit path
    # immediately. Async UI behavior is covered by the event-loop fixture.
    app._launch_print_preparation = lambda work: work()
    app.is_printing = False
    app.retry_batch = None
    app.block_status_by_id = {}
    app.block_detail_by_id = {}
    app.print_state_lock = threading.Lock()
    app.print_cancel_event = threading.Event()
    app.print_scheduled_block_ids = set()
    app.print_started_block_ids = set()
    app.active_print_block_uid = None
    app.print_include_name_separator_pages = False
    app.print_include_separator_pages = False
    app.print_last_name_separator_label = ""
    app.print_next_block_index = 1
    app.print_pending_jobs = []
    app.print_accepting_appends = True
    app.print_run_options = PrintRunOptions("FAKE", "sumatra", "FAKE.exe", True, False, 3, 1)
    app.current_print_run_options = Mock(return_value=app.print_run_options)
    app.selected_block_index_value = 0 if blocks else None
    app.selected_block_indices = {0} if blocks else set()
    app.undo_stack = []
    for name in ("app_status_var", "current_work_var", "queue_status_var"):
        setattr(app, name, Variable("idle"))
    for name in ("separator_page_var", "name_separator_page_var"):
        setattr(app, name, Variable(False))
    for name in ("save_current_config", "set_status", "set_current_work", "log",
                 "update_queue_view", "update_preview", "draw_queue", "refresh_print_action_buttons",
                 "validate_print_jobs", "validate_print_blocks", "ensure_orientation_profiles_ready",
                 "ensure_block_printers_ready", "ensure_job_printers_ready"):
        setattr(app, name, Mock())
    app.cached_printer_inventory = Mock(return_value=frozenset())
    return app


class QueueLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.dialog = patch("unit_print_manager.messagebox.askyesno", return_value=True).start()
        self.addCleanup(patch.stopall)
        for name in ("showinfo", "showwarning", "showerror"):
            patch("unit_print_manager.messagebox." + name).start()
        # Tripwires: these tests can never reach a real printer or config write.
        for name in ("sumatra_enqueue_pdfs", "shell_print_pdf", "save_config", "load_printers",
                     "get_print_queue_count", "run_powershell"):
            patch("unit_print_manager." + name, side_effect=AssertionError(name)).start()
        self.worker = patch("unit_print_manager.threading.Thread").start()

    def test_name_sort_is_stable_keeps_selection_and_can_be_undone(self):
        late, first, unnamed, same_name = (
            named_block("late", "홍길동"), named_block("first", "김하늘"),
            block("unnamed"), named_block("same", "김하늘"),
        )
        app = make_app([late, first, unnamed, same_name])
        app.selected_block_indices = {0, 3}
        app.selected_block_index_value = 3

        app.sort_blocks_by_name()

        self.assertEqual([first, same_name, late, unnamed], app.blocks)
        self.assertEqual({1, 2}, app.selected_block_indices)
        self.assertEqual(1, app.selected_block_index_value)
        self.assertEqual(1, len(app.undo_stack))
        app.undo_last_action()
        self.assertEqual([late, first, unnamed, same_name], app.blocks)

    def test_name_sort_does_not_change_active_or_retry_order(self):
        late, first = named_block("late", "홍길동"), named_block("first", "김하늘")
        app = make_app([late, first])
        app.is_printing = True
        app.sort_blocks_by_name()
        self.assertEqual([late, first], app.blocks)
        self.assertFalse(app.undo_stack)

        app.is_printing = False
        app.retry_batch = RetryBatch(tuple(app.build_print_jobs(app.blocks)))
        app.sort_blocks_by_name()
        self.assertEqual([late, first], app.blocks)
        self.assertFalse(app.undo_stack)

    def test_append_excludes_previous_completed_and_failed_blocks(self):
        completed, active, new, failed = [block(name) for name in ("done", "active", "new", "failed")]
        app = make_app([completed, active, new, failed])
        app.block_status_by_id = {id(completed): "완료", id(active): "예약", id(failed): "오류"}
        app.is_printing = True
        app.print_scheduled_block_ids = {id(active)}
        app.print_pending_jobs = app.build_print_jobs([active])
        app.print_next_block_index = 2
        app.append_new_blocks_to_active_print()
        self.assertEqual(["active.pdf", "new.pdf"], [j.item.path.name for j in app.print_pending_jobs])
        app.append_new_blocks_to_active_print()
        self.assertEqual(2, len(app.print_pending_jobs))

    def test_delete_filters_only_removed_block_from_partial_retry(self):
        removed, retained = block("removed"), block("retained")
        app = make_app([removed, retained])
        jobs = app.build_print_jobs([removed, retained])
        app.retry_batch = RetryBatch(tuple(jobs))
        app.delete_block_at(0)
        self.assertEqual((jobs[1],), app.retry_batch.jobs)
        app.retry_remaining_jobs()
        self.assertEqual([jobs[1]], app.print_pending_jobs)
        self.assertEqual([retained], self.worker.call_args.kwargs["args"][0])

    def test_deleted_last_retry_never_starts_worker(self):
        item = block("removed")
        app = make_app([item])
        app.retry_batch = RetryBatch(tuple(app.build_print_jobs([item])))
        app.delete_block_at(0)
        app.retry_remaining_jobs()
        self.assertIsNone(app.retry_batch)
        self.worker.assert_not_called()

    def test_reserved_block_can_be_deleted_during_print(self):
        active, reserved, retained = [block(name) for name in ("active", "reserved", "retained")]
        app = make_app([active, reserved, retained])
        jobs = app.build_print_jobs(app.blocks)
        app.is_printing = True
        app.print_pending_jobs = list(jobs)
        app.print_scheduled_block_ids = {id(active), id(reserved), id(retained)}
        app.print_started_block_ids = {id(active)}
        app.active_print_block_uid = id(active)
        app.block_status_by_id = {
            id(active): "진행 중",
            id(reserved): "예약",
            id(retained): "예약",
        }

        app.delete_block_at(1)

        self.assertEqual([active, retained], app.blocks)
        self.assertEqual(
            ["active.pdf", "retained.pdf"],
            [job.item.path.name for job in app.print_pending_jobs],
        )
        self.assertNotIn(id(reserved), app.print_scheduled_block_ids)
        self.assertIn(id(retained), app.print_scheduled_block_ids)

    def test_started_block_stays_protected_from_delete(self):
        active, reserved = block("active"), block("reserved")
        app = make_app([active, reserved])
        app.is_printing = True
        app.print_pending_jobs = app.build_print_jobs(app.blocks)
        app.print_scheduled_block_ids = {id(active), id(reserved)}
        app.print_started_block_ids = {id(active)}
        app.active_print_block_uid = id(active)

        app.delete_block_at(0)

        self.assertEqual([active, reserved], app.blocks)
        self.assertEqual(2, len(app.print_pending_jobs))

    def test_deleted_reserved_job_cannot_be_claimed_by_worker(self):
        reserved = block("reserved")
        app = make_app([reserved])
        job = app.build_print_jobs(app.blocks)[0]
        app.is_printing = True
        app.print_pending_jobs = [job]
        app.print_scheduled_block_ids = {id(reserved)}

        app.delete_block_at(0)

        self.assertFalse(app.claim_active_print_job(job))
        self.assertEqual([], app.print_pending_jobs)

    def test_deleting_first_reserved_block_keeps_name_separator_for_same_student(self):
        active = named_block("active-a", "학생A")
        removed = named_block("reserved-b1", "학생B")
        retained = named_block("reserved-b2", "학생B")
        app = make_app([active, removed, retained])
        app.name_separator_page_var.set(True)
        app.print_include_name_separator_pages = True
        app.print_pending_jobs = app.build_print_jobs(
            app.blocks, include_name_separator_pages=True,
        )
        app.is_printing = True
        app.print_scheduled_block_ids = {id(active), id(removed), id(retained)}
        app.print_started_block_ids = {id(active)}
        app.active_print_block_uid = id(active)

        app.delete_block_at(1)

        retained_jobs = [job for job in app.print_pending_jobs if job.block_uid == id(retained)]
        self.assertTrue(retained_jobs[0].is_name_separator)
        self.assertEqual("이름 구분지 - 학생B", retained_jobs[0].item.name)
        self.assertEqual("reserved-b2.pdf", retained_jobs[1].item.path.name)

    def test_deleting_tail_updates_name_context_before_runtime_append(self):
        active = named_block("active-a", "학생A")
        removed = named_block("reserved-b", "학생B")
        app = make_app([active, removed])
        app.name_separator_page_var.set(True)
        app.print_include_name_separator_pages = True
        app.print_pending_jobs = app.build_print_jobs(
            app.blocks, include_name_separator_pages=True,
        )
        app.print_last_name_separator_label = "학생B"
        app.is_printing = True
        app.print_scheduled_block_ids = {id(active), id(removed)}
        app.print_started_block_ids = {id(active)}
        app.active_print_block_uid = id(active)

        app.delete_block_at(1)
        appended = named_block("appended-b", "학생B")
        app.blocks.append(appended)
        app.append_new_blocks_to_active_print()

        appended_jobs = [job for job in app.print_pending_jobs if job.block_uid == id(appended)]
        self.assertTrue(appended_jobs[0].is_name_separator)
        self.assertEqual("학생B", app.print_last_name_separator_label)

    def test_stale_worker_candidate_cannot_skip_inserted_name_separator(self):
        active = named_block("active-a", "학생A")
        removed = named_block("reserved-b1", "학생B")
        retained = named_block("reserved-b2", "학생B")
        app = make_app([active, removed, retained])
        app.print_include_name_separator_pages = True
        app.print_pending_jobs = app.build_print_jobs(
            app.blocks, include_name_separator_pages=True,
        )
        app.is_printing = True
        app.print_scheduled_block_ids = {id(active), id(removed), id(retained)}
        app.print_started_block_ids = {id(active)}
        app.active_print_block_uid = id(active)
        stale_body = next(
            job for job in app.print_pending_jobs
            if job.block_uid == id(retained) and not job.is_name_separator
        )
        expected_index = next(
            index for index, job in enumerate(app.print_pending_jobs)
            if job.block_uid == id(removed)
        )

        app.delete_block_at(1)

        self.assertFalse(app.claim_active_print_job(stale_body, expected_index))
        self.assertTrue(app.print_pending_jobs[expected_index].is_name_separator)

    def test_deleting_middle_name_group_removes_newly_redundant_separator(self):
        active = named_block("active-a1", "학생A")
        removed = named_block("reserved-b", "학생B")
        retained = named_block("reserved-a2", "학생A")
        app = make_app([active, removed, retained])
        app.print_include_name_separator_pages = True
        app.print_pending_jobs = app.build_print_jobs(
            app.blocks,
            include_name_separator_pages=True,
        )
        app.is_printing = True
        app.print_scheduled_block_ids = {id(active), id(removed), id(retained)}
        app.print_started_block_ids = {id(active)}
        app.active_print_block_uid = id(active)

        app.delete_block_at(1)

        retained_jobs = [job for job in app.print_pending_jobs if job.block_uid == id(retained)]
        self.assertEqual(1, len(retained_jobs))
        self.assertFalse(retained_jobs[0].is_name_separator)

    def test_multi_delete_rejects_whole_selection_when_one_block_started(self):
        active = block("active")
        reserved = block("reserved")
        app = make_app([active, reserved])
        app.is_printing = True
        app.print_pending_jobs = app.build_print_jobs(app.blocks)
        app.print_scheduled_block_ids = {id(active), id(reserved)}
        app.print_started_block_ids = {id(active)}
        app.active_print_block_uid = id(active)
        app.selected_block_indices = {0, 1}

        app.delete_selected_block()

        self.assertEqual([active, reserved], app.blocks)
        self.assertEqual(2, len(app.print_pending_jobs))

    def test_retry_keeps_name_mode_after_separator_was_already_sent(self):
        first = named_block("first-a", "학생A")
        second = named_block("second-a", "학생A")
        app = make_app([first, second])
        app.print_include_name_separator_pages = True
        app.print_pending_jobs = app.build_print_jobs(
            app.blocks,
            include_name_separator_pages=True,
        )

        app.preserve_retry_from_failure(1)

        self.assertIsNotNone(app.retry_batch)
        assert app.retry_batch is not None
        self.assertTrue(app.retry_batch.include_name_separator_pages)
        self.assertEqual("학생A", app.retry_batch.preceding_name_separator_label)
        self.assertFalse(any(job.is_name_separator for job in app.retry_batch.jobs))

    def test_deleting_first_retry_block_rehomes_shared_name_separator(self):
        removed = named_block("retry-b1", "학생B")
        retained = named_block("retry-b2", "학생B")
        app = make_app([removed, retained])
        jobs = app.build_print_jobs(
            app.blocks,
            include_name_separator_pages=True,
        )
        self.assertTrue(jobs[0].is_name_separator)
        self.assertFalse(any(job.is_name_separator for job in jobs if job.block_uid == id(retained)))
        app.retry_batch = RetryBatch(
            tuple(jobs),
            include_name_separator_pages=True,
        )

        app.delete_block_at(0)

        self.assertIsNotNone(app.retry_batch)
        assert app.retry_batch is not None
        self.assertEqual({id(retained)}, {job.block_uid for job in app.retry_batch.jobs})
        self.assertEqual(2, len(app.retry_batch.jobs))
        self.assertTrue(app.retry_batch.jobs[0].is_name_separator)
        self.assertEqual("이름 구분지 - 학생B", app.retry_batch.jobs[0].item.name)
        self.assertEqual("retry-b2.pdf", app.retry_batch.jobs[1].item.path.name)

    def test_replace_requires_confirmation_and_removes_old_retry(self):
        original, replacement, other = block("original"), block("replacement"), block("other")
        app = make_app([original, other])
        jobs = app.build_print_jobs(app.blocks)
        app.retry_batch = RetryBatch(tuple(jobs))
        self.dialog.return_value = False
        self.assertFalse(app.commit_selected_block_replacement(replacement, "수정창"))
        self.assertIs(app.blocks[0], original)
        self.assertEqual(tuple(jobs), app.retry_batch.jobs)
        self.dialog.return_value = True
        self.assertTrue(app.commit_selected_block_replacement(replacement, "수정창"))
        self.assertEqual((jobs[1],), app.retry_batch.jobs)
        self.assertEqual([replacement, other], app.blocks)
        self.assertEqual("대기", app.queue_block_status(replacement))

    def test_multi_delete_and_clear_remove_matching_retry(self):
        a, b, c = block("a"), block("b"), block("c")
        app = make_app([a, b, c])
        jobs = app.build_print_jobs(app.blocks)
        app.retry_batch = RetryBatch(tuple(jobs))
        app.selected_block_indices = {0, 2}
        app.delete_selected_block()
        self.assertEqual((jobs[1],), app.retry_batch.jobs)
        app.clear_blocks()
        self.assertIsNone(app.retry_batch)
        self.assertEqual([], app.blocks)

    def test_order_edit_keeps_lineage_without_mislabeling_changed_material(self):
        original = block("original")
        original.order_id = "ORDER-001"
        original.material_id = "MATERIAL-001"
        app = make_app([original])
        replacement = block("original")
        replacement.copies = 5
        self.assertTrue(app.commit_selected_block_replacement(replacement, "수정창"))
        self.assertEqual("ORDER-001", replacement.order_id)
        self.assertEqual("MATERIAL-001", replacement.material_id)
        self.assertEqual(original.history_id, replacement.replaces_block_id)
        changed = block("different_material")
        self.assertTrue(app.commit_selected_block_replacement(changed, "수정창"))
        self.assertEqual("ORDER-001", changed.order_id)
        self.assertEqual("", changed.material_id)
        self.assertEqual(replacement.history_id, changed.replaces_block_id)

    def test_undo_preserves_completed_state_and_restores_visible_retry_identity(self):
        completed, failed = block("done"), block("failed")
        app = make_app([completed, failed])
        app.block_status_by_id = {id(completed): "완료", id(failed): "중지됨"}
        jobs = app.build_print_jobs([failed])
        app.retry_batch = RetryBatch(tuple(jobs))
        app.delete_block_at(1)
        app.undo_last_action()
        self.assertIs(app.blocks[0], completed)
        self.assertIs(app.blocks[1], failed)
        self.assertEqual("완료", app.queue_block_status(completed))
        self.assertEqual([], app.blocks_waiting_for_print())
        self.assertEqual(tuple(jobs), app.retry_batch.jobs)

    def test_completed_badge_distinguishes_submission_from_physical_output(self):
        submitted, printed = block("submitted"), block("printed")
        app = make_app([submitted, printed])
        app.block_status_by_id = {id(submitted): "완료", id(printed): "완료"}
        app.block_detail_by_id = {id(printed): "실제 인쇄 완료"}

        self.assertEqual("전송 완료", app.queue_block_status_label(submitted))
        self.assertEqual("인쇄 완료", app.queue_block_status_label(printed))

    def test_copy_paste_clones_selected_blocks_as_fresh_waiting_work(self):
        first, skipped, third = block("first"), block("skipped"), block("third")
        first.order_id = "ORDER-001"
        first.material_id = "MATERIAL-001"
        third.replaces_block_id = "OLD-BLOCK"
        app = make_app([first, skipped, third])
        app.selected_block_indices = {0, 2}
        app.selected_block_index_value = 2

        app.copy_selected_blocks()
        copied_files = [template.files for template in app.block_clipboard]
        app.paste_copied_blocks()

        pasted = app.blocks[3:]
        self.assertEqual(["first", "third"], [item.subject for item in pasted])
        self.assertEqual({3, 4}, app.selected_block_indices)
        self.assertEqual(3, app.selected_block_index_value)
        self.assertTrue(all(app.queue_block_status(item) == "대기" for item in pasted))
        self.assertTrue(all(item.history_id not in {first.history_id, third.history_id} for item in pasted))
        self.assertEqual("ORDER-001", pasted[0].order_id)
        self.assertEqual("MATERIAL-001", pasted[0].material_id)
        self.assertEqual(["", ""], [item.replaces_block_id for item in pasted])
        self.assertIsNot(copied_files[0], first.files)
        self.assertIsNot(pasted[0].files, copied_files[0])

        first_paste_ids = {item.history_id for item in pasted}
        app.paste_copied_blocks()
        second_paste = app.blocks[5:]
        self.assertTrue(first_paste_ids.isdisjoint(item.history_id for item in second_paste))

    def test_paste_inserts_immediately_below_selected_middle_block(self):
        first, selected, last = [block(name) for name in ("first", "selected", "last")]
        app = make_app([first, selected, last])
        app.block_clipboard = (block("copied"),)
        app.selected_block_indices = {1}
        app.selected_block_index_value = 1

        self.assertEqual("break", app.paste_copied_blocks())

        self.assertEqual(["first", "selected", "copied", "last"], [item.subject for item in app.blocks])
        self.assertEqual({2}, app.selected_block_indices)
        self.assertEqual(2, app.selected_block_index_value)
        self.assertEqual("대기", app.block_status_by_id[id(app.blocks[2])])
        self.assertNotEqual(app.block_clipboard[0].history_id, app.blocks[2].history_id)

    def test_paste_uses_last_selected_row_and_keeps_copied_order(self):
        app = make_app([block(name) for name in ("a", "b", "c", "d")])
        app.block_clipboard = (block("copy-1"), block("copy-2"))
        app.selected_block_indices = {0, 2}
        app.selected_block_index_value = 2

        app.paste_copied_blocks()

        self.assertEqual(
            ["a", "b", "c", "copy-1", "copy-2", "d"],
            [item.subject for item in app.blocks],
        )
        self.assertEqual({3, 4}, app.selected_block_indices)

    def test_deferred_paste_tracks_selected_block_identity_after_queue_shift(self):
        app = make_app([block(name) for name in ("a", "anchor", "c")])
        app.block_clipboard = (block("copied"),)
        app.selected_block_indices = {1}
        app.selected_block_index_value = 1
        callbacks = []
        app.reserve_block_commit = Mock(return_value=object())
        app.complete_block_commit = Mock(side_effect=lambda _token, callback: callbacks.append(callback))

        app.paste_copied_blocks()
        app.blocks.insert(0, block("new-first"))
        callbacks.pop()()

        self.assertEqual(
            ["new-first", "a", "anchor", "copied", "c"],
            [item.subject for item in app.blocks],
        )
        self.assertEqual({3}, app.selected_block_indices)

    def test_active_print_paste_stays_at_tail_to_preserve_scheduled_order(self):
        app = make_app([block(name) for name in ("scheduled", "waiting")])
        app.block_clipboard = (block("copied"),)
        app.selected_block_indices = {0}
        app.selected_block_index_value = 0
        app.is_printing = True
        app.print_scheduled_block_ids = {id(app.blocks[0])}

        app.paste_copied_blocks()

        self.assertEqual(["scheduled", "waiting", "copied"], [item.subject for item in app.blocks])
        self.assertIn("순서 보호", app.queue_status_var.get())

    def test_paste_without_selection_or_with_retry_work_uses_tail(self):
        app = make_app([block(name) for name in ("first", "last")])
        app.block_clipboard = (block("copy-a"),)
        app.selected_block_indices = set()
        app.selected_block_index_value = None
        app.paste_copied_blocks()
        self.assertEqual(["first", "last", "copy-a"], [item.subject for item in app.blocks])

        app.block_clipboard = (block("copy-b"),)
        app.selected_block_indices = {0}
        app.selected_block_index_value = 0
        app.retry_batch = SimpleNamespace(jobs=(SimpleNamespace(block_uid=id(app.blocks[1])),))
        app.paste_copied_blocks()
        self.assertEqual(["first", "last", "copy-a", "copy-b"], [item.subject for item in app.blocks])

    def test_drag_start_keeps_existing_multi_selection_when_grabbing_selected_block(self):
        items = [block(name) for name in ("first", "second", "third", "fourth")]
        app = make_app(items)
        app.selected_block_indices = {1, 2}
        app.selected_block_index_value = 2
        app.queue_selection_anchor = 1
        app.keyboard_state = KeyboardState()
        app.queue_card_height = 62
        app.queue_card_gap = 8
        app.queue_padding = 8
        app.drag_start_index = None
        app.drag_selected_indices = ()
        app.drag_drop_index = None
        app.drag_pointer_y = None
        app.drag_pointer_offset_y = 0
        app.drag_active = False
        app.drag_lift_progress = 0
        app.cancel_drop_animation = Mock()
        app.queue_canvas = Mock()
        app.queue_canvas.canvasy.side_effect = lambda value: value
        event = SimpleNamespace(y=app.queue_card_top(2) + 10, state=0)

        self.assertEqual("break", app.on_queue_drag_start(event))

        self.assertEqual((1, 2), app.drag_selected_indices)
        self.assertEqual({1, 2}, app.selected_block_indices)
        self.assertEqual(2, app.drag_start_index)
        self.assertEqual(2, app.selected_block_index_value)

    def test_multi_drag_is_blocked_when_any_selected_block_is_scheduled(self):
        items = [block(name) for name in ("first", "second", "third")]
        app = make_app(items)
        app.is_printing = True
        app.print_scheduled_block_ids = {id(items[1])}
        app.selected_block_indices = {1, 2}
        app.selected_block_index_value = 2
        app.queue_selection_anchor = 1
        app.queue_card_height = 62
        app.queue_card_gap = 8
        app.queue_padding = 8
        app.drag_start_index = None
        app.drag_selected_indices = ()
        app.drag_drop_index = None
        app.drag_pointer_y = None
        app.drag_pointer_offset_y = 0
        app.drag_active = False
        app.cancel_drop_animation = Mock()
        app.queue_canvas = Mock()
        app.queue_canvas.canvasy.side_effect = lambda value: value
        event = SimpleNamespace(y=app.queue_card_top(2) + 10, state=0)

        self.assertEqual("break", app.on_queue_drag_start(event))

        self.assertIsNone(app.drag_start_index)
        self.assertEqual((), app.drag_selected_indices)
        self.assertEqual({1, 2}, app.selected_block_indices)
        self.assertEqual("실행 큐에 등록된 블록은 드래그 정렬 불가", app.queue_status_var.get())

    def test_multi_drag_reorders_selected_blocks_as_one_group(self):
        items = [block(name) for name in ("first", "second", "third", "fourth", "fifth")]
        app = make_app(items)
        app.push_undo_state = Mock()
        app.capture_queue_history = Mock()

        result = app.reorder_blocks({1, 2}, len(items), focus_index=2)

        self.assertEqual(
            ["first", "fourth", "fifth", "second", "third"],
            [item.subject for item in app.blocks],
        )
        self.assertEqual((3, 4), result.selected_indices)
        self.assertEqual(4, result.focus_index)
        self.assertTrue(result.changed)
        app.push_undo_state.assert_called_once_with("블록 드래그 정렬")
        app.capture_queue_history.assert_called_once_with()

    def test_multi_drag_inside_selected_range_is_noop(self):
        items = [block(name) for name in ("first", "second", "third", "fourth")]
        app = make_app(items)
        app.push_undo_state = Mock()
        app.capture_queue_history = Mock()

        result = app.reorder_blocks({1, 2}, 3, focus_index=1)

        self.assertEqual(items, app.blocks)
        self.assertEqual((1, 2), result.selected_indices)
        self.assertFalse(result.changed)
        app.push_undo_state.assert_not_called()
        app.capture_queue_history.assert_not_called()

    def test_block_clipboard_shortcuts_leave_editor_copy_paste_untouched(self):
        app = make_app([block("first")])
        app.keyboard_state = KeyboardState()
        app.keyboard_state.enter_editor(cursor=0)
        event = SimpleNamespace(widget=object())
        app.widget_is_in_queue = Mock(return_value=True)
        app.widget_is_in_queue_editor = Mock(return_value=True)
        app.shortcut_focus_is_editor = Mock(return_value=True)

        self.assertIsNone(app.handle_ctrl_c_shortcut(event))
        self.assertIsNone(app.handle_ctrl_v_shortcut(event))
        self.assertFalse(hasattr(app, "block_clipboard"))

    def test_print_start_resumes_exact_retry_before_new_waiting_blocks(self):
        stopped = PrintBlock(
            "stopped",
            "",
            "",
            1,
            2,
            1,
            [
                ExamPdf(1, "sent", Path("sent.pdf")),
                ExamPdf(2, "remaining", Path("remaining.pdf")),
            ],
        )
        appended, new = block("appended"), block("new")
        app = make_app([stopped, appended, new])
        app.block_status_by_id = {
            id(stopped): "중지됨",
            id(appended): "추가됨",
            id(new): "대기",
        }
        original_jobs = app.build_print_jobs([stopped, appended])
        retry_jobs = tuple(original_jobs[1:])
        app.retry_batch = RetryBatch(retry_jobs)

        app.start_print_thread()

        self.assertEqual(list(retry_jobs), app.print_pending_jobs)
        self.assertEqual(
            ["remaining.pdf", "appended.pdf"],
            [job.item.path.name for job in app.print_pending_jobs],
        )
        self.assertEqual("예약", app.queue_block_status(stopped))
        self.assertEqual("예약", app.queue_block_status(appended))
        self.assertEqual("대기", app.queue_block_status(new))
        self.worker.assert_called_once()
        self.assertEqual([stopped, appended], self.worker.call_args.kwargs["args"][0])

    def test_retry_defensively_drops_orphaned_jobs(self):
        orphan = block("orphan")
        app = make_app([])
        app.retry_batch = RetryBatch(tuple(app.build_print_jobs([orphan])))
        app.retry_remaining_jobs()
        self.assertIsNone(app.retry_batch)
        self.worker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
