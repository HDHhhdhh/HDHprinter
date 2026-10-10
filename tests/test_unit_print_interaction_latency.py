"""Exercise selection and pointer work without a native UI or print dispatch."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from unit_print_keyboard import KeyboardState
from unit_print_manager import ExamPdf, PrintBlock, PrintManagerApp
from unit_print_queue_state import reorder_selected_items
from test_unit_print_queue_lifecycle import make_app as make_lifecycle_app


class Variable:
    def __init__(self, value=""):
        self.value = value
        self.writes = 0

    def get(self):
        return self.value

    def set(self, value):
        self.value = value
        self.writes += 1


class Widget:
    def __init__(self):
        self.options = {}

    def __setitem__(self, key, value):
        self.options[key] = value

    def configure(self, **options):
        self.options.update(options)

    def pack(self, **_options):
        pass

    def pack_forget(self):
        pass


class IdleRoot:
    def __init__(self):
        self.callbacks = {}
        self.counter = 0

    def after_idle(self, callback):
        self.counter += 1
        token = str(self.counter)
        self.callbacks[token] = callback
        return token

    def after_cancel(self, token):
        self.callbacks.pop(token, None)

    def flush(self):
        callbacks = tuple(self.callbacks.values())
        self.callbacks.clear()
        for callback in callbacks:
            callback()


def catalog_app():
    app = PrintManagerApp.__new__(PrintManagerApp)
    item = PrintBlock(
        "subject", "Lv.2", "2", 2, 3, 4,
        [ExamPdf(2, "second", Path("second.pdf")), ExamPdf(3, "third", Path("third.pdf"))],
        selected_numbers=(2, 3), name_separator_label="synthetic name", bundled=True,
    )
    app.blocks = [item]
    app.catalog = {"synthetic": True}
    app.root_dir_var = Variable("synthetic root")
    app.selected_block_indices = set()
    app.selected_block_index_value = None
    app.queue_selection_anchor = None
    app.keyboard_state = KeyboardState()
    app.keyboard_state.enter_editor(cursor=0)
    app.keyboard_state.editor_dirty = True
    app.editor_syncing = False
    app.draw_queue = Mock()
    app.refresh_editor_status = Mock()
    app.refresh_name_separator_editor_state = Mock()
    app.refresh_toggle_buttons = Mock()
    app.update_builder_status = Mock()
    for name in (
        "subject_var", "level_var", "round_var", "start_var", "end_var",
        "unit_selected_numbers_var", "block_copies_var", "bundle_var",
        "edit_subject_var", "edit_level_var", "edit_round_var", "edit_start_var",
        "edit_end_var", "edit_selected_numbers_var", "edit_copies_var", "edit_bundle_var",
        "edit_name_separator_var", "edit_printer_route_var",
    ):
        setattr(app, name, Variable())
    for name in (
        "level_combo", "round_combo", "start_combo", "end_combo", "edit_subject_combo",
        "edit_level_combo", "edit_round_combo", "edit_start_combo", "edit_end_combo",
        "edit_bundle_button", "edit_printer_route_combo", "edit_printer_route_label",
        "edit_level_label", "edit_round_label", "queue_edit_status_label",
        "edit_selected_numbers_label", "edit_selected_numbers_hint_label",
    ):
        setattr(app, name, Widget())
    return app


class InteractionLatencyTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        for name in ("sumatra_enqueue_pdfs", "shell_print_pdf", "save_config", "load_printers", "run_powershell"):
            patch("unit_print_manager." + name, side_effect=AssertionError(name)).start()
        self.files = [ExamPdf(number, f"file {number}", Path(f"synthetic-{number}.pdf")) for number in (1, 2, 3)]
        self.levels = patch("unit_print_manager.catalog_levels", return_value=("Lv.1", "Lv.2")).start()
        self.rounds = patch("unit_print_manager.catalog_rounds", return_value=("1", "2")).start()
        self.file_lookup = patch("unit_print_manager.catalog_exam_files", return_value=self.files).start()

    def test_click_loads_builder_and_editor_once_with_final_values(self):
        app = catalog_app()
        original = app.blocks[0]
        with patch.object(app, "populate_queue_editor", wraps=app.populate_queue_editor) as populate:
            app.select_queue_indices({0}, focus_index=0, anchor_index=0, load_form=True)
        populate.assert_called_once_with(0)
        # One file-list load for the builder, one for the editor; no three-pass cascades.
        self.assertEqual(2, self.file_lookup.call_count)
        self.assertEqual(2, self.levels.call_count)
        self.assertEqual(2, self.rounds.call_count)
        self.assertIs(original, app.blocks[0])
        self.assertEqual({0}, app.selected_block_indices)
        self.assertEqual(0, app.keyboard_state.queue_cursor)
        self.assertFalse(app.keyboard_state.editor_dirty)
        self.assertFalse(app.editor_syncing)
        for prefix in ("", "edit_"):
            self.assertEqual(original.subject, getattr(app, prefix + "subject_var").get())
            self.assertEqual(original.level, getattr(app, prefix + "level_var").get())
            self.assertEqual(original.round_name, getattr(app, prefix + "round_var").get())
            self.assertEqual(self.files[1].label, getattr(app, prefix + "start_var").get())
            self.assertEqual(self.files[2].label, getattr(app, prefix + "end_var").get())
        self.assertEqual("2,3", app.unit_selected_numbers_var.get())
        self.assertEqual("2,3", app.edit_selected_numbers_var.get())
        self.assertEqual(4, app.block_copies_var.get())
        self.assertEqual(4, app.edit_copies_var.get())
        self.assertEqual(original.name_separator_label, app.edit_name_separator_var.get())

    def test_keyboard_selection_only_populates_editor(self):
        app = catalog_app()
        with patch.object(app, "load_selected_block_to_form", wraps=app.load_selected_block_to_form) as load:
            app.select_queue_indices({0}, focus_index=0)
        load.assert_not_called()
        self.file_lookup.assert_called_once()
        self.assertEqual("", app.subject_var.get())
        self.assertEqual("subject", app.edit_subject_var.get())
        self.assertFalse(app.keyboard_state.editor_dirty)

    def test_empty_explicit_load_still_clears_editor_once(self):
        app = catalog_app()
        app.blocks.clear()
        with patch.object(app, "populate_queue_editor", wraps=app.populate_queue_editor) as populate:
            app.select_queue_indices(set(), load_form=True)
        populate.assert_called_once_with(None)
        self.file_lookup.assert_not_called()
        self.assertEqual("선택 없음", app.queue_edit_status_label.options["text"])
        self.assertFalse(app.keyboard_state.editor_dirty)

    def test_editor_sync_guard_cleans_up_on_population_failure(self):
        app = catalog_app()
        with patch.object(app, "_populate_queue_editor", side_effect=ValueError("synthetic failure")):
            with self.assertRaises(ValueError):
                app.populate_queue_editor(0)
        self.assertFalse(app.editor_syncing)
        self.assertTrue(app.keyboard_state.editor_dirty)

    def drag_app(self, count=10):
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.blocks = [object() for _ in range(count)]
        app.root = IdleRoot()
        app.is_closing = False
        app.queue_card_height, app.queue_card_gap, app.queue_padding = 62, 8, 8
        app.queue_canvas = Mock()
        app.queue_canvas.winfo_height.return_value = 1000
        app.queue_canvas.canvasy.side_effect = lambda value: value
        app.drag_start_index = 2
        app.drag_selected_indices = (2, 5)
        app.drag_active = True
        app.drag_origin_y = 0
        app.drag_pointer_y = 0
        app.drag_drop_index = None
        app.queue_status_var = Variable()
        app.current_drag_projection = Mock(side_effect=AssertionError("pointer event projected the full queue"))
        frames = []

        def draw():
            app.queue_redraw_after_id = None
            frames.append((app.drag_pointer_y, app.drag_drop_index))

        app.draw_queue = Mock(side_effect=draw)
        app.frames = frames
        return app

    def test_pointer_burst_coalesces_draw_and_keeps_latest_coordinates(self):
        app = self.drag_app()
        for y in (100, 200, 300):
            self.assertEqual("break", app.on_queue_drag_motion(SimpleNamespace(y=y)))
        app.draw_queue.assert_not_called()
        self.assertEqual(1, len(app.root.callbacks))
        self.assertEqual(300, app.drag_pointer_y)
        self.assertEqual(app.queue_drop_index_from_canvas_y(300), app.drag_drop_index)
        app.root.flush()
        self.assertEqual([(300, app.drag_drop_index)], app.frames)
        app.current_drag_projection.assert_not_called()

    def test_drag_labels_match_commit_projection_without_full_queue_work(self):
        app = self.drag_app()
        for drop in range(len(app.blocks) + 1):
            app.queue_drop_index_from_canvas_y = Mock(return_value=drop)
            app.on_queue_drag_motion(SimpleNamespace(y=300))
            projected = reorder_selected_items(app.blocks, app.drag_selected_indices, drop, focus_index=2)
            destination = "맨 아래" if drop == len(app.blocks) else f"{projected.selected_indices[0] + 1}~{projected.selected_indices[-1] + 1}번 위치"
            self.assertEqual(f"블록 2개 · {destination}로 이동", app.queue_status_var.get())
        writes = app.queue_status_var.writes
        app.on_queue_drag_motion(SimpleNamespace(y=300))
        self.assertEqual(writes, app.queue_status_var.writes)
        app.current_drag_projection.assert_not_called()

    def test_drag_release_rechecks_print_protection_before_commit(self):
        blocks = [PrintBlock(str(index), "", "", 1, 1, 1, [self.files[0]]) for index in range(4)]
        app = make_lifecycle_app(blocks)
        app.keyboard_state = KeyboardState()
        app.queue_card_height, app.queue_card_gap, app.queue_padding = 62, 8, 8
        app.queue_canvas = Mock()
        app.queue_canvas.canvasy.side_effect = lambda value: value
        app.drag_start_index = 1
        app.drag_selected_indices = (1, 2)
        app.drag_drop_index = len(blocks)
        app.drag_pointer_y = 300
        app.drag_pointer_offset_y = 0
        app.drag_active = True
        app.drop_animation_positions = {}
        app.drop_animation_progress = 0
        app.queue_selection_anchor = 1
        app.cancel_lift_animation = Mock()
        app.start_drop_animation = Mock()
        app.push_undo_state = Mock()
        app.capture_queue_history = Mock()
        # The run starts after the press, before the release.
        app.is_printing = True
        app.print_scheduled_block_ids = {id(blocks[2])}
        app.on_queue_drag_release(SimpleNamespace(y=300))
        self.assertEqual(blocks, app.blocks)
        app.push_undo_state.assert_not_called()
        app.capture_queue_history.assert_not_called()
        self.assertEqual("실행 큐에 등록된 블록은 드래그 정렬 불가", app.queue_status_var.get())
        self.assertFalse(app.drag_active)


if __name__ == "__main__":
    unittest.main()
