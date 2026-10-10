"""Manual recovery integration with real local snapshots, journals, and PDF checks.

No GUI is launched and Windows/printer calls are tripwires. Fixtures use closed
source writers and genuine session/dispatch events, rather than forged safety
reviews, so these cases exercise the published recovery boundary.
"""
from __future__ import annotations

import copy
import tempfile
import threading
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pypdf import PdfWriter

from unit_print_history import HistoryWriteError, PrintHistory, read_history
from unit_print_keyboard import KeyboardState
from unit_print_manager import ExamPdf, PrintBlock, PrintManagerApp
from unit_print_queue_recovery import RecoveryStore, RecoveryWriteError, RecoveryWriter, snapshot_print_block
from unit_print_recovery_history import RecoveryHistoryReview, classify_recovery_queue


class RecoveryWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.history_directory = self.directory / "history"
        self.store = RecoveryStore(self.directory / "recovery")
        self.writers: list[RecoveryWriter] = []
        self.addCleanup(self.close_writers)
        self.pdf = self.directory / "교과서.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        with self.pdf.open("wb") as stream:
            writer.write(stream)
        self.addCleanup(patch.stopall)
        patch("unit_print_manager.HISTORY_DIRECTORY", self.history_directory).start()
        self.warning = patch("unit_print_manager.messagebox.showwarning").start()
        self.info = patch("unit_print_manager.messagebox.showinfo").start()
        patch("unit_print_manager.messagebox.showerror").start()
        for name in ("sumatra_enqueue_pdfs", "shell_print_pdf", "load_printers",
                     "get_print_queue_count", "run_powershell", "save_config"):
            patch("unit_print_manager." + name, side_effect=AssertionError(name)).start()

    def close_writers(self) -> None:
        for writer in self.writers:
            try:
                writer.close(timeout=2)
            except Exception:
                # Failure cases deliberately exercise write failure; preserve
                # the original assertion while releasing its worker lifetime.
                pass

    def app(self, blocks: list[PrintBlock] | None = None) -> PrintManagerApp:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.root = Mock()
        app.blocks = list(blocks or ())
        app.block_status_by_id = {}
        app.block_detail_by_id = {}
        app.is_printing = False
        app.is_closing = False
        app.retry_batch = None
        app.able_build_pending = False
        app.order_build_pending = False
        app.group_build_pending = False
        app.recovery_load_pending = False
        app.recovery_poll_after_id = None
        app.block_preparation_epoch = 0
        app.print_state_lock = threading.RLock()
        app.print_history = PrintHistory(self.history_directory)
        app.history_run_id = None
        app._history_queue_snapshot = {}
        app.history_session_started_at = app.record_history("session_started")["timestamp"]
        app.queue_recovery_store = self.store
        app.queue_recovery_writer = RecoveryWriter(self.store)
        self.writers.append(app.queue_recovery_writer)
        app.queue_status_var = Mock()
        app.log = Mock()
        app.draw_queue = Mock()
        app.populate_queue_editor = Mock()
        app.update_preview = Mock()
        app.push_undo_state = Mock()
        app.keyboard_state = KeyboardState()
        app.selected_block_indices = set()
        app.selected_block_index_value = None
        app.queue_selection_anchor = None
        app.cached_preflight_result = Mock(return_value=None)
        app.remember_preflight_results = Mock()
        app.start_print_thread = Mock(side_effect=AssertionError("recovery started printing"))
        app.resolve_block_printer_route = Mock(side_effect=AssertionError("recovery re-inferred route"))
        app.block_from_config = Mock(side_effect=AssertionError("recovery used lossy config codec"))
        return app

    def blocks(self) -> list[PrintBlock]:
        return [
            PrintBlock(
                "공수2", "교과서", "차시", 7, 9, 3,
                [ExamPdf(7, "백지리뷰", self.pdf, "Canon - 세로", 4),
                 ExamPdf(9, "교재", self.pdf, "Canon - A:ble", 1)],
                bundled=True, source="material_able", printer_override="Canon - A:ble",
                display_label="교재 묶음", pages_per_sheet=1, printer_route="A:ble",
                selected_numbers=(7, 9), name_separator_label="김학생", material_kind="교재",
                order_id="ORDER-7", material_id="ABLE-7", replaces_block_id="prior-7",
            ),
            PrintBlock(
                "교과서", "", "직접", 1, 1, 2,
                [ExamPdf(1, "직접 PDF", self.pdf, "Custom printer", 4)],
                source="custom", printer_override="Custom printer", display_label="직접 PDF",
                pages_per_sheet=4, printer_route="자동 감지", name_separator_label="김학생",
                order_id="ORDER-8", material_id="CUSTOM-8",
            ),
            PrintBlock(
                "기하", "Lv.3", "2", 5, 5, 1, [ExamPdf(5, "카탈로그 원본", self.pdf)],
                source="catalog", name_separator_label="박학생", order_id="ORDER-9",
                material_id="CATALOG-9",
            ),
        ]

    def source(self, *, attempted: set[int] | None = None, close: bool = True):
        app = self.app(self.blocks())
        snapshot = app.queue_recovery_snapshot("queue_changed")
        app.queue_recovery_writer.save_required(snapshot, timeout=2)
        for index in attempted or ():
            app.print_history.record("job_attempt", run_id="original-run", job_id=f"job-{index}",
                                     block_id=app.blocks[index].history_id)
        if close:
            app.record_history("session_closed")
            app.queue_recovery_writer.close(timeout=2)
        review = self.review(snapshot, writer=app.queue_recovery_writer)
        return app, snapshot, review

    def review(self, snapshot: dict, *, writer: RecoveryWriter | None = None):
        guard = writer.restore_guard if writer is not None else self.store.restore_guard
        with guard(snapshot["session_id"]):
            consumed = self.store.consumed_block_ids(snapshot["session_id"])
            return classify_recovery_queue(snapshot, self.history_directory, consumed_block_ids=consumed)

    def target(self) -> PrintManagerApp:
        return self.app()

    def assert_no_queue_commit(self, app: PrintManagerApp) -> None:
        self.assertEqual([], app.blocks)
        self.assertEqual({}, app.block_status_by_id)
        self.assertEqual({}, app.block_detail_by_id)
        app.push_undo_state.assert_not_called()
        app.start_print_thread.assert_not_called()

    def test_safe_recovery_preserves_exact_metadata_order_and_file_settings(self) -> None:
        source, snapshot, review = self.source()
        app = self.target()
        self.assertEqual({block.history_id for block in source.blocks}, set(review.safe_block_ids), review.warning)
        self.assertTrue(app.restore_recovery_blocks(snapshot, review, [2, 0, 1]))
        self.assertEqual(3, len(app.blocks))
        for original, restored in zip(source.blocks, app.blocks):
            expected = asdict(original)
            actual = asdict(restored)
            self.assertNotEqual(original.history_id, restored.history_id)
            self.assertEqual(original.history_id, restored.replaces_block_id)
            for key in ("history_id", "replaces_block_id"):
                expected.pop(key)
                actual.pop(key)
            self.assertEqual(expected, actual)
        self.assertEqual({"대기"}, set(app.block_status_by_id.values()))
        self.assertEqual({}, app.block_detail_by_id)
        app.start_print_thread.assert_not_called()
        app.block_from_config.assert_not_called()
        app.resolve_block_printer_route.assert_not_called()
        recovered = next(record for record in read_history(self.history_directory)
                         if record["event"] == "queue_recovered")
        self.assertEqual([block.history_id for block in source.blocks], recovered["source_block_ids"])

    def test_partial_block_attempt_is_held_as_whole_and_never_restored(self) -> None:
        source, snapshot, review = self.source(attempted={0})
        app = self.target()
        self.assertNotIn(source.blocks[0].history_id, review.safe_block_ids)
        self.assertFalse(app.restore_recovery_blocks(snapshot, review, [0, 1, 2]))
        self.assert_no_queue_commit(app)
        self.assertTrue(app.restore_recovery_blocks(snapshot, review, [1, 2]))
        self.assertEqual([block.history_id for block in source.blocks[1:]],
                         [block.replaces_block_id for block in app.blocks])
        app.start_print_thread.assert_not_called()

    def test_saved_progress_or_completed_blocks_remain_held_without_dispatch_event(self) -> None:
        source = self.app(self.blocks())
        source.block_status_by_id = {id(source.blocks[0]): "진행 중", id(source.blocks[1]): "완료"}
        snapshot = source.queue_recovery_snapshot("queue_changed")
        source.queue_recovery_writer.save_required(snapshot, timeout=2)
        source.record_history("session_closed")
        source.queue_recovery_writer.close(timeout=2)
        review = self.review(snapshot)
        self.assertEqual({source.blocks[2].history_id}, set(review.safe_block_ids), review.warning)
        app = self.target()
        self.assertTrue(app.restore_recovery_blocks(snapshot, review, [2]))
        self.assertEqual(1, len(app.blocks))
        self.assertEqual(source.blocks[2].history_id, app.blocks[0].replaces_block_id)

    def test_missing_or_corrupt_history_prevents_every_block_commit(self) -> None:
        _source, snapshot, _review = self.source()
        path = next(self.history_directory.glob("*.jsonl"))
        original = path.read_bytes()
        for content in (None, original + b'{"interrupted'):
            with self.subTest(content=content is None):
                if content is None:
                    path.unlink()
                else:
                    path.write_bytes(content)
                review = self.review(snapshot)
                self.assertFalse(review.safe_block_ids)
                app = self.target()
                self.assertFalse(app.restore_recovery_blocks(snapshot, review, [0]))
                self.assert_no_queue_commit(app)
                path.write_bytes(original)

    def test_live_source_lease_refuses_recovery_even_with_an_unsent_review(self) -> None:
        source, snapshot, review = self.source(close=False)
        app = self.target()
        self.assertTrue(review.safe_block_ids, review.warning)
        self.assertFalse(app.restore_recovery_blocks(snapshot, review, [0]))
        self.assert_no_queue_commit(app)
        self.warning.assert_called()
        self.assertTrue(self.store.is_session_active(source.print_history.session_id))

    def test_changed_source_snapshot_refuses_stale_dialog_selection(self) -> None:
        _source, snapshot, review = self.source()
        changed = copy.deepcopy(snapshot)
        changed["snapshot_id"] = "changed-after-review"
        changed["queue"][0]["block"]["copies"] = 9
        self.store.save(changed)
        app = self.target()
        self.assertFalse(app.restore_recovery_blocks(snapshot, review, [0]))
        self.assert_no_queue_commit(app)
        self.warning.assert_called()

    def test_new_dispatch_marker_after_review_refuses_stale_safe_review(self) -> None:
        source, snapshot, review = self.source()
        source.print_history.record("job_attempt", run_id="unexpected-run", job_id="late-job",
                                    block_id=source.blocks[0].history_id)
        app = self.target()
        self.assertFalse(app.restore_recovery_blocks(snapshot, review, [0]))
        self.assert_no_queue_commit(app)

    def test_forged_safe_review_cannot_bypass_saved_risk_or_dispatch_history(self) -> None:
        source, snapshot, _review = self.source(attempted={0})
        forged = RecoveryHistoryReview(frozenset(block.history_id for block in source.blocks), frozenset(), frozenset())
        app = self.target()
        self.assertFalse(app.restore_recovery_blocks(snapshot, forged, [0]))
        self.assert_no_queue_commit(app)

    def test_original_source_is_consumed_after_restore_and_cannot_be_restored_twice(self) -> None:
        source, snapshot, review = self.source()
        first = self.target()
        self.assertTrue(first.restore_recovery_blocks(snapshot, review, [0]))
        fresh_id = first.blocks[0].history_id
        first.print_history.record("job_attempt", run_id="new-run", job_id="new-job", block_id=fresh_id)
        second = self.target()
        repeated_review = self.review(snapshot)
        self.assertNotIn(source.blocks[0].history_id, repeated_review.safe_block_ids)
        self.assertIn(source.blocks[1].history_id, repeated_review.safe_block_ids)
        self.assertFalse(second.restore_recovery_blocks(snapshot, review, [0]))
        self.assert_no_queue_commit(second)
        self.assertTrue(second.restore_recovery_blocks(snapshot, repeated_review, [1]))

    def test_restore_during_printing_retry_or_pending_build_never_changes_queue(self) -> None:
        _source, snapshot, review = self.source()
        flags = {
            "is_printing": True,
            "retry_batch": SimpleNamespace(jobs=()),
            "able_build_pending": True,
            "order_build_pending": True,
            "group_build_pending": True,
            "recovery_load_pending": True,
        }
        for field, value in flags.items():
            with self.subTest(field=field):
                app = self.target()
                setattr(app, field, value)
                self.assertFalse(app.restore_recovery_blocks(snapshot, review, [0]))
                self.assert_no_queue_commit(app)

    def test_missing_or_corrupt_consumption_receipt_refuses_old_safe_review(self) -> None:
        _source, snapshot, review = self.source()
        receipt = self.store.directory / f"session-{snapshot['session_id']}.consumed.json"
        original = receipt.read_bytes()
        for content in (None, b"broken receipt"):
            with self.subTest(content=content):
                if content is None:
                    receipt.unlink()
                else:
                    receipt.write_bytes(content)
                app = self.target()
                self.assertFalse(app.restore_recovery_blocks(snapshot, review, [0]))
                self.assert_no_queue_commit(app)
                app.remember_preflight_results.assert_not_called()
                receipt.write_bytes(original)

    def test_required_consumption_mark_failure_refuses_commit_and_leaves_receipt_unchanged(self) -> None:
        _source, snapshot, review = self.source()
        app = self.target()
        with patch.object(self.store, "mark_recovered", side_effect=RecoveryWriteError("disk full")) as mark:
            self.assertFalse(app.restore_recovery_blocks(snapshot, review, [0]))
        mark.assert_called_once()
        self.assert_no_queue_commit(app)
        self.assertEqual(frozenset(), self.store.consumed_block_ids(snapshot["session_id"]))
        self.assertTrue(any(record["event"] == "queue_recovered" for record in read_history(self.history_directory)))

    def test_two_restore_windows_cannot_commit_the_same_source_blocks(self) -> None:
        _source, snapshot, review = self.source()
        first = self.target()
        second = self.target()
        first_preflight = first.preflight_pdf_paths
        entered = threading.Event()
        release = threading.Event()
        result = []

        def blocking_preflight(paths):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test gate")
            first_preflight(paths)

        first.preflight_pdf_paths = blocking_preflight
        thread = threading.Thread(target=lambda: result.append(first.restore_recovery_blocks(snapshot, review, [0])))
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertFalse(second.restore_recovery_blocks(snapshot, review, [0]))
            self.assert_no_queue_commit(second)
        finally:
            release.set()
            thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual([True], result)
        self.assertEqual(1, len(first.blocks))
        self.assertFalse(second.restore_recovery_blocks(snapshot, review, [0]))
        self.assert_no_queue_commit(second)

    def test_invalid_indices_are_rejected_without_silently_selecting_last_row(self) -> None:
        _source, snapshot, review = self.source()
        for indices in ([-1], [len(snapshot["queue"])], [True], ["0"], [0, -1]):
            with self.subTest(indices=indices):
                app = self.target()
                self.assertFalse(app.restore_recovery_blocks(snapshot, review, indices))
                self.assert_no_queue_commit(app)

    def test_preflight_failure_leaves_queue_and_required_recovery_history_untouched(self) -> None:
        _source, snapshot, review = self.source()
        self.pdf.unlink()
        app = self.target()
        before = list(read_history(self.history_directory))
        self.assertFalse(app.restore_recovery_blocks(snapshot, review, [0, 1]))
        self.assert_no_queue_commit(app)
        self.assertEqual(before, read_history(self.history_directory))
        self.warning.assert_called()

    def test_required_history_failure_leaves_queue_and_undo_untouched(self) -> None:
        _source, snapshot, review = self.source()
        app = self.target()
        genuine_record = app.print_history.record

        def record(event, **data):
            if event == "queue_recovered":
                raise HistoryWriteError(self.history_directory, OSError("disk full"))
            return genuine_record(event, **data)

        with patch.object(app.print_history, "record", side_effect=record):
            self.assertFalse(app.restore_recovery_blocks(snapshot, review, [0, 1]))
        self.assert_no_queue_commit(app)
        self.warning.assert_called()

    def test_bad_snapshot_field_prevents_partial_restore(self) -> None:
        _source, snapshot, review = self.source()
        corrupt = copy.deepcopy(snapshot)
        corrupt["queue"][1]["block"]["copies"] = "2"
        path = self.store.directory / f"session-{snapshot['session_id']}.json"
        import json

        path.write_text(json.dumps(corrupt), encoding="utf-8")
        app = self.target()
        self.assertFalse(app.restore_recovery_blocks(corrupt, review, [0, 1]))
        self.assert_no_queue_commit(app)

    def test_empty_launch_and_manual_empty_save_do_not_replace_crashed_candidate(self) -> None:
        _source, snapshot, _review = self.source()
        candidates = self.store.list_candidates()
        before = {path: path.read_bytes() for path in candidates}
        app = self.target()
        app.schedule_queue_recovery_save()
        app.save_queue_recovery()
        app.queue_recovery_writer.close(timeout=2)
        self.assertEqual(before, {path: path.read_bytes() for path in self.store.list_candidates()})
        app.queue_status_var.set.assert_called_with("저장할 작업 큐가 없습니다. 이전 백업은 유지됩니다.")
        self.assertEqual(snapshot, self.store.load(candidates[0]))

    def test_model_edit_saves_snapshot_but_selection_refresh_does_not(self) -> None:
        app = self.app(self.blocks())
        app.keyboard_state.enter_queue(cursor=0)
        app.queue_recovery_writer = Mock()
        app.update_queue_view(select_index=0)
        app.queue_recovery_writer.submit_captured.assert_called_once()
        app.queue_recovery_writer.submit.assert_not_called()
        first = app.queue_recovery_writer.submit_captured.call_args.args[0]
        self.assertEqual("ORDER-7", first["queue"][0]["block"]["order_id"])
        app.queue_recovery_writer.reset_mock()
        app.refresh_queue_selection(select_index=1)
        app.queue_recovery_writer.submit_captured.assert_not_called()

    def test_production_block_capture_matches_legacy_format_and_is_detached(self) -> None:
        blocks = self.blocks()
        app = self.app(blocks)
        for index, block in enumerate(blocks):
            app.block_status_by_id[id(block)] = ("대기", "예약", "진행 중")[index]
            app.block_detail_by_id[id(block)] = f"synthetic detail {index}"
        captured = app.queue_recovery_snapshot("queue_changed")
        for original, entry in zip(blocks, captured["queue"]):
            legacy = asdict(original)
            for item in legacy["files"]:
                item["path"] = str(item["path"])
            if legacy["selected_numbers"] is not None:
                legacy["selected_numbers"] = list(legacy["selected_numbers"])
            self.assertEqual(legacy, entry["block"])
            self.assertEqual(list(legacy), list(entry["block"]), "serialized field order changed")
            self.assertEqual(snapshot_print_block(original), entry["block"])
        before = copy.deepcopy(captured)
        blocks[0].copies = 99
        blocks[0].selected_numbers = (1, 2, 3)
        blocks[0].files.append(ExamPdf(10, "later", self.pdf))
        blocks[1].history_id = "changed-id"
        blocks[1].name_separator_label = "changed-name"
        app.block_status_by_id[id(blocks[2])] = "완료"
        self.assertEqual(before, captured)
        self.assertEqual(["unsent", "unsent", "uncertain"], [entry["risk"] for entry in captured["queue"]])

    def test_automatic_production_capture_uses_fast_path_and_remains_exact(self) -> None:
        app = self.app(self.blocks())
        expected_blocks = [snapshot_print_block(block) for block in app.blocks]
        with patch.object(app.queue_recovery_writer, "submit", side_effect=AssertionError("automatic save used synchronous validation")):
            app.schedule_queue_recovery_save()
            app.queue_recovery_writer.flush(timeout=2)
        saved = self.store.load()
        self.assertEqual(expected_blocks, [entry["block"] for entry in saved["queue"]])
        self.assertEqual("queue_changed", saved["reason"])
        self.assertEqual(app.print_history.session_id, saved["session_id"])

    def test_close_stores_final_queue_before_session_close_evidence_and_destroy(self) -> None:
        app = self.app(self.blocks())
        app.blocks[0].copies = 11
        app.device_monitor = None
        for name in ("close_persistent_operator_alert", "close_pdf_work_services",
                     "close_taskbar_indicator", "save_current_config"):
            setattr(app, name, Mock())
        app.on_close()
        self.assertTrue(app.is_closing)
        app.root.destroy.assert_called_once()
        path = self.store.directory / f"session-{app.print_history.session_id}.json"
        saved = self.store.load(path)
        self.assertEqual("session_closed", saved["reason"])
        self.assertEqual(11, saved["queue"][0]["block"]["copies"])
        review = self.review(saved)
        self.assertEqual({block.history_id for block in app.blocks}, set(review.safe_block_ids), review.warning)
        closed = next(record for record in read_history(self.history_directory)
                      if record["session_id"] == app.print_history.session_id and record["event"] == "session_closed")
        self.assertLessEqual(saved["created_at"], closed["timestamp"])
        self.assertFalse(self.store.is_session_active(app.print_history.session_id))

    def test_ctrl_s_keeps_queue_editor_and_builder_scopes_separate(self) -> None:
        app = self.target()
        app.save_queue_recovery = Mock()
        app.save_current_config = Mock()
        app.save_editor_from_keyboard = Mock()
        app.run_keyboard_after_ime = Mock(return_value="break")
        app.widget_is_in_queue = Mock(return_value=True)
        app.widget_is_in_queue_editor = Mock(return_value=False)
        app.widget_is_in_builder = Mock(return_value=False)
        widget = object()
        event = SimpleNamespace(widget=widget, keysym="s", state=4)
        app.keyboard_state.enter_queue()
        self.assertEqual("break", app.handle_save_shortcut(event))
        app.save_queue_recovery.assert_called_once()
        app.save_current_config.assert_not_called()
        app.run_keyboard_after_ime.assert_not_called()
        app.save_queue_recovery.reset_mock()
        app.keyboard_state.enter_editor()
        app.widget_is_in_queue_editor.return_value = True
        self.assertEqual("break", app.handle_save_shortcut(event))
        app.run_keyboard_after_ime.assert_called_once_with("save-editor", app.save_editor_from_keyboard, source_widget=widget)
        app.save_queue_recovery.assert_not_called()
        app.widget_is_in_queue_editor.return_value = False
        app.keyboard_state.enter_builder()
        app.widget_is_in_builder.return_value = True
        self.assertEqual("break", app.handle_save_shortcut(event))
        app.save_current_config.assert_called_once()
        app.save_queue_recovery.assert_not_called()


if __name__ == "__main__":
    unittest.main()
