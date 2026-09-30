from __future__ import annotations

import queue
import threading
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import Mock, patch

from unit_print_manager import ExamPdf, PrintBlock, PrintManagerApp
from unit_print_preparation import OrderedCommitQueue


def _block(number: int) -> PrintBlock:
    return PrintBlock("자료", "", "직접", 1, 1, 1,
                      [ExamPdf(1, f"{number}", Path(f"{number}.pdf"))], source="custom")


class GroupPreparationTest(unittest.TestCase):
    def app(self) -> PrintManagerApp:
        app = object.__new__(PrintManagerApp)
        app.blocks = [_block(1), _block(2), _block(3)]
        app.selected_block_indices = {0, 2}
        app.block_status_by_id = {}
        app.block_detail_by_id = {}
        app.block_preparation_epoch = 0
        app.block_commit_queue = OrderedCommitQueue()
        app.background_build_results = queue.SimpleQueue()
        app.background_build_service = Mock()
        app.background_build_poll_after_id = None
        app.root = Mock()
        app.is_printing = False
        app.is_closing = False
        app.group_build_pending = False
        app.able_build_pending = False
        app.order_build_pending = False
        app.recovery_load_pending = False
        app.print_state_lock = threading.RLock()
        app.printer_var = Mock()
        app.printer_var.get.return_value = "Canon"
        app.queue_status_var = Mock()
        app.push_undo_state = Mock()
        app.sync_retry_queue = Mock()
        app.update_queue_view = Mock()
        app.update_preview = Mock()
        app.record_history = Mock()
        app.log = Mock()
        return app

    def deliver(self, app: PrintManagerApp, *, error: Exception | None = None) -> PrintBlock:
        work, done = app.background_build_service.submit.call_args.args
        bundle = _block(4)
        bundle.bundled = True
        bundle.material_kind = "queue_bundle"
        future = Future()
        if error is None:
            with patch("unit_print_manager.make_queue_bundle_block", return_value=bundle):
                future.set_result(work())
        else:
            future.set_exception(error)
        done(future)
        app.poll_background_build_results()
        return bundle

    def test_group_prepares_off_ui_and_commits_at_original_position(self) -> None:
        app = self.app()
        original = list(app.blocks)
        with patch("unit_print_manager.make_queue_bundle_block") as build:
            self.assertTrue(app.group_selected_blocks())
            build.assert_not_called()
        self.assertEqual(original, app.blocks)
        self.assertTrue(app.group_build_pending)
        work, _ = app.background_build_service.submit.call_args.args
        with patch("unit_print_manager.make_queue_bundle_block", return_value=_block(4)) as build:
            work()
            captured = build.call_args.args[0]
            self.assertIsNot(original[0], captured[0])
            self.assertIsNot(original[0].files, captured[0].files)
            self.assertEqual("Canon", build.call_args.kwargs["base_printer"])
        bundle = self.deliver(app)
        self.assertEqual([bundle, original[1]], app.blocks)
        self.assertFalse(app.group_build_pending)
        app.push_undo_state.assert_called_once_with("블록 묶기")
        app.update_queue_view.assert_called_once_with(select_index=0)

    def test_stale_result_never_overwrites_edited_deleted_or_printing_queue(self) -> None:
        mutations = {
            "edit": lambda a: setattr(a.blocks[0], "copies", 2),
            "delete": lambda a: a.blocks.pop(0),
            "reorder": lambda a: a.blocks.reverse(),
            "printing": lambda a: setattr(a, "is_printing", True),
            "printer": lambda a: setattr(a.printer_var.get, "return_value", "Other"),
            "submitted": lambda a: a.block_status_by_id.update({id(a.blocks[0]): "완료"}),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                app = self.app()
                app.group_selected_blocks()
                mutate(app)
                after_change = list(app.blocks)
                with patch("unit_print_manager.messagebox.showwarning") as warning:
                    self.deliver(app)
                    warning.assert_called_once()
                self.assertEqual(after_change, app.blocks)
                self.assertFalse(app.group_build_pending)
                app.push_undo_state.assert_not_called()
                app.record_history.assert_not_called()

    def test_cancelled_late_completion_is_ignored(self) -> None:
        app = self.app()
        original = list(app.blocks)
        app.group_selected_blocks()
        app.cancel_pending_block_preparations()
        self.deliver(app)
        self.assertEqual(original, app.blocks)
        self.assertFalse(app.group_build_pending)
        app.push_undo_state.assert_not_called()
        app.background_build_service.cancel_pending.assert_called_once()

    def test_error_preserves_queue_and_allows_next_attempt(self) -> None:
        app = self.app()
        original = list(app.blocks)
        app.group_selected_blocks()
        with patch("unit_print_manager.messagebox.showwarning") as warning:
            self.deliver(app, error=ValueError("병합 실패"))
            warning.assert_called_once()
        self.assertEqual(original, app.blocks)
        self.assertFalse(app.group_build_pending)
        self.assertEqual(0, app.block_commit_queue.pending_count)
        self.assertTrue(app.group_selected_blocks())

    def test_ready_group_waits_for_earlier_requested_material(self) -> None:
        app = self.app()
        earlier = app.block_commit_queue.reserve()
        original = list(app.blocks)
        app.group_selected_blocks()
        bundle = self.deliver(app)
        self.assertEqual(original, app.blocks)
        self.assertTrue(app.group_build_pending)
        self.assertFalse(app.group_selected_blocks())
        app.block_commit_queue.cancel(earlier)
        self.assertEqual([bundle, original[1]], app.blocks)
        self.assertFalse(app.group_build_pending)

    def test_service_failure_releases_commit_slot(self) -> None:
        app = self.app()
        app.background_build_service.submit.side_effect = RuntimeError("종료됨")
        with patch("unit_print_manager.messagebox.showwarning"):
            self.assertFalse(app.group_selected_blocks())
        self.assertFalse(app.group_build_pending)
        self.assertEqual(0, app.block_commit_queue.pending_count)

    def test_start_and_retry_cannot_capture_queue_before_group_finishes(self) -> None:
        app = self.app()
        app.group_selected_blocks()
        app.blocks_waiting_for_print = Mock(side_effect=AssertionError("stale queue captured"))
        app.sync_retry_queue.reset_mock()
        with patch("unit_print_manager.messagebox.askyesno",
                   side_effect=AssertionError("nested dialog opened")):
            app.start_print_thread()
            app.retry_remaining_jobs()
        app.sync_retry_queue.assert_not_called()
        app.blocks_waiting_for_print.assert_not_called()
        self.assertFalse(app.is_printing)
        self.assertEqual(
            ["묶음 준비가 끝난 뒤 인쇄를 시작해 주세요."] * 2,
            [call.args[0] for call in app.queue_status_var.set.call_args_list[-2:]],
        )
        self.deliver(app)
        self.assertFalse(app.group_build_pending)


if __name__ == "__main__":
    unittest.main()
