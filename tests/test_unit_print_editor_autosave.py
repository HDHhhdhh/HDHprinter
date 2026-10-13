"""Editor drafts and real queue commits; Tcl variables, no printer or visible UI."""
from __future__ import annotations

import tkinter as tk
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from test_unit_print_queue_lifecycle import block, make_app
from unit_print_keyboard import KeyboardState
from unit_print_manager import PrintManagerApp, QUEUE_EDITOR_VARIABLE_NAMES
from unit_print_queue_recovery import snapshot_print_block
from unit_print_retry import RetryBatch


class EditorAutosaveTests(unittest.TestCase):
    def setUp(self):
        self.tcl = tk.Tcl()
        self.addCleanup(patch.stopall)
        self.warning = patch("unit_print_manager.messagebox.showwarning").start()
        self.confirm = patch("unit_print_manager.messagebox.askyesno", return_value=True).start()
        for name in ("sumatra_enqueue_pdfs", "save_config", "load_printers",
                     "get_print_queue_count", "run_powershell", "take_windows_ime_composition"):
            patch("unit_print_manager." + name, side_effect=AssertionError(name)).start()

    def app(self, source="custom"):
        first, second = block("first"), block("second")
        first.source = second.source = source
        first.files.append(replace(block("another").files[0], number=2))
        first.end = 2
        app = make_app([first, second])
        app.keyboard_state = KeyboardState()
        app.queue_selection_anchor = 0
        app.editor_syncing = False
        app.editor_block = None
        app.editor_baseline = None
        app.editor_save_in_progress = False
        for name in QUEUE_EDITOR_VARIABLE_NAMES:
            factory = tk.IntVar if name == "edit_copies_var" else (
                tk.BooleanVar if name == "edit_bundle_var" else tk.StringVar
            )
            variable = factory(master=self.tcl)
            setattr(app, name, variable)
            variable.trace_add("write", app.handle_editor_variable_change)
        for name in ("edit_subject_combo", "edit_level_combo", "edit_round_combo",
                     "edit_start_combo", "edit_end_combo", "edit_bundle_button",
                     "edit_level_label", "edit_round_label", "edit_printer_route_combo",
                     "edit_printer_route_label", "edit_selected_numbers_label",
                     "edit_selected_numbers_hint_label", "queue_edit_status_label"):
            setattr(app, name, MagicMock())
        app.refresh_shortcut_help = Mock()
        app.refresh_toggle_buttons = Mock()
        app.refresh_name_separator_editor_state = Mock()
        app.resolve_block_printer_route = Mock(side_effect=lambda route: (route, None))
        app.textbook_catalog = Mock()
        app.textbook_catalog.find_edition.return_value = None
        app.focus_queue_editor = Mock(return_value="break")
        app.focus_queue_canvas = Mock(return_value="break")
        app.focus_builder_area = Mock(return_value="break")
        app.widget_is_in_queue_editor = Mock(return_value=False)
        app.root.focus_get.return_value = None
        app.take_name_entry_ime = Mock(return_value=False)
        app.load_selected_block_to_form = Mock(
            side_effect=lambda: app.populate_queue_editor(app.selected_block_index())
        )
        app.captured = []
        app.capture_queue_history = Mock(
            side_effect=lambda: app.captured.append([snapshot_print_block(item) for item in app.blocks])
        )
        app.schedule_queue_recovery_save = Mock()
        app.update_queue_view = PrintManagerApp.update_queue_view.__get__(app)
        app.queue_canvas = MagicMock()
        app.queue_canvas.canvasy.side_effect = lambda value: value
        app.queue_canvas.winfo_height.return_value = 220
        app.queue_padding, app.queue_card_height, app.queue_card_gap = 5, 40, 10
        app.cancel_drop_animation = Mock()
        app.cancel_lift_animation = Mock()
        app.drag_start_index = None
        app.material_dropdown_widget = None
        app.populate_queue_editor(0)
        self.assertFalse(app.keyboard_state.editor_dirty)
        return app

    def test_selecting_next_block_saves_all_metadata_with_history_recovery_and_undo(self):
        app = self.app()
        old, target = app.blocks
        old.order_id, old.material_id = "order-test", "material-test"
        app.edit_name_separator_var.set("김연후")
        app.edit_copies_var.set(3)
        app.edit_bundle_var.set(True)
        app.edit_printer_route_var.set("가로")

        self.assertTrue(app.select_queue_index(1))

        saved = app.blocks[0]
        self.assertEqual(("김연후", 3, True, "가로"), (
            saved.name_separator_label, saved.copies, saved.bundled, saved.printer_route,
        ))
        self.assertEqual(old.files, saved.files)
        self.assertIsNot(old.files, saved.files)
        self.assertNotEqual(old.history_id, saved.history_id)
        self.assertEqual(old.history_id, saved.replaces_block_id)
        self.assertEqual(("order-test", "material-test"), (saved.order_id, saved.material_id))
        self.assertIs(target, app.blocks[1])
        self.assertEqual({1}, app.selected_block_indices)
        self.assertEqual(1, app.selected_block_index())
        self.assertIs(target, app.editor_block)
        self.assertEqual("", app.edit_name_separator_var.get())
        self.assertFalse(app.keyboard_state.editor_dirty)
        self.assertEqual(1, len(app.undo_stack))
        app.capture_queue_history.assert_called_once_with()
        app.schedule_queue_recovery_save.assert_called_once_with()
        self.assertEqual("김연후", app.captured[0][0]["name_separator_label"])

    def test_blank_queue_click_saves_then_clears_selection(self):
        app = self.app()
        app.edit_copies_var.set(4)
        app.on_queue_drag_start(SimpleNamespace(y=180, state=0))
        self.assertEqual(4, app.blocks[0].copies)
        self.assertEqual(set(), app.selected_block_indices)
        self.assertIsNone(app.selected_block_index())
        self.assertIsNone(app.editor_block)
        self.assertFalse(app.keyboard_state.editor_dirty)

    def test_ctrl_deselect_saves_even_when_cursor_stays_on_same_block(self):
        app = self.app()
        app.edit_name_separator_var.set("이규민")
        self.assertTrue(app.select_queue_with_modifiers(0, 4))
        self.assertEqual(set(), app.selected_block_indices)
        self.assertEqual(0, app.selected_block_index())
        self.assertEqual("이규민", app.blocks[0].name_separator_label)

    def test_multiselection_drag_saves_original_cursor_and_keeps_group(self):
        app = self.app()
        app.selected_block_indices = {0, 1}
        app.edit_name_separator_var.set("김연후")
        click = SimpleNamespace(y=60, state=0)
        app.on_queue_drag_start(click)
        self.assertEqual("김연후", app.blocks[0].name_separator_label)
        self.assertEqual("", app.blocks[1].name_separator_label)
        self.assertEqual({0, 1}, app.selected_block_indices)
        self.assertEqual((0, 1), app.drag_selected_indices)
        app.on_queue_drag_release(click)
        self.assertEqual({0, 1}, app.selected_block_indices)
        self.assertIs(app.blocks[1], app.editor_block)

    def test_invalid_copies_keep_draft_cursor_and_selection(self):
        for value in ("", "abc", "0"):
            with self.subTest(value=value):
                app = self.app()
                old = app.blocks[0]
                app.edit_copies_var.set(value)
                self.assertTrue(app.keyboard_state.editor_dirty)
                self.assertFalse(app.select_queue_index(1))
                self.assertIs(old, app.blocks[0])
                self.assertEqual({0}, app.selected_block_indices)
                self.assertEqual(0, app.selected_block_index())
                self.assertEqual(value, str(tk.Variable.get(app.edit_copies_var)))
                self.assertTrue(app.keyboard_state.editor_dirty)
                self.assertFalse(app.undo_stack)
                app.capture_queue_history.assert_not_called()
                app.focus_queue_editor.assert_called_once_with()

    def test_invalid_individual_numbers_block_click_and_context_menu(self):
        app = self.app()
        app.edit_selected_numbers_var.set("oops")
        app.post_queue_context_menu = Mock()
        app.on_queue_context_menu(SimpleNamespace(y=60, x_root=0, y_root=0))
        self.assertEqual(0, app.selected_block_index())
        self.assertEqual("oops", app.edit_selected_numbers_var.get())
        app.post_queue_context_menu.assert_not_called()
        app.capture_queue_history.assert_not_called()

    def test_scheduled_block_is_never_replaced_by_autosave(self):
        app = self.app()
        old = app.blocks[0]
        app.is_printing = True
        app.print_scheduled_block_ids = {id(old)}
        app.edit_copies_var.set(2)
        self.assertFalse(app.select_queue_index(1))
        self.assertIs(old, app.blocks[0])
        self.assertTrue(app.keyboard_state.editor_dirty)
        app.capture_queue_history.assert_not_called()
        self.assertIn("실행 큐", self.warning.call_args.args[1])

    def test_retry_replacement_still_requires_existing_confirmation(self):
        app = self.app()
        old = app.blocks[0]
        retry = app.retry_batch = RetryBatch(tuple(app.build_print_jobs([old])))
        self.confirm.return_value = False
        app.edit_copies_var.set(2)
        self.assertFalse(app.select_queue_index(1))
        self.assertIs(old, app.blocks[0])
        self.assertIs(retry, app.retry_batch)
        self.assertEqual(0, app.selected_block_index())
        self.assertTrue(app.keyboard_state.editor_dirty)
        self.confirm.assert_called_once()
        app.capture_queue_history.assert_not_called()

    def test_same_block_refresh_and_select_all_do_not_discard_draft_or_write_history(self):
        app = self.app()
        original = app.blocks[0]
        app.edit_name_separator_var.set("김연후")
        app.select_queue_index(0, load_form=True)
        app.select_all_queue()
        app.refresh_queue_selection()
        self.assertIs(original, app.blocks[0])
        self.assertEqual("김연후", app.edit_name_separator_var.get())
        self.assertTrue(app.keyboard_state.editor_dirty)
        app.capture_queue_history.assert_not_called()
        app.schedule_queue_recovery_save.assert_not_called()

    def test_mouse_toggle_is_dirty_even_outside_editor_keyboard_zone_and_reverting_is_clean(self):
        app = self.app()
        app.keyboard_state.enter_queue(cursor=0)
        app.edit_bundle_var.set(True)
        self.assertTrue(app.keyboard_state.editor_dirty)
        app.edit_bundle_var.set(False)
        self.assertFalse(app.keyboard_state.editor_dirty)
        self.assertTrue(app.select_queue_index(1))
        app.capture_queue_history.assert_not_called()

    def test_metadata_only_material_changes_reuse_pdf_and_keep_composition(self):
        for source in ("material_apex", "material_textbook", "material_textbook_variant", "material_able"):
            with self.subTest(source=source):
                app = self.app(source)
                old = app.blocks[0]
                old.material_kind, old.pages_per_sheet = "composition-test", 4
                old.printer_route, old.printer_override = "A:ble", "TEST A:ble"
                app.make_apex_block_from_editor = Mock(side_effect=AssertionError("PDF rebuilt"))
                app.make_able_block_from_editor = Mock(side_effect=AssertionError("PDF rebuilt"))
                app.make_textbook_block_from_editor = Mock(side_effect=AssertionError("PDF rebuilt"))
                app.make_textbook_variant_block_for_selection = Mock(side_effect=AssertionError("PDF rebuilt"))
                app.edit_name_separator_var.set("김연후")
                app.edit_copies_var.set(2)
                self.assertTrue(app.save_pending_queue_editor())
                saved = app.blocks[0]
                self.assertEqual(old.files, saved.files)
                self.assertEqual(("composition-test", 4, "A:ble", "TEST A:ble"), (
                    saved.material_kind, saved.pages_per_sheet, saved.printer_route, saved.printer_override,
                ))

    def test_changed_range_uses_existing_file_selection_and_validation(self):
        app = self.app()
        expected = app.blocks[0].files[1].path
        app.edit_selected_numbers_var.set("2")
        self.assertTrue(app.select_queue_index(1))
        self.assertEqual([expected], [item.path for item in app.blocks[0].files])

    def test_paste_saves_draft_before_switching_selection_to_inserted_block(self):
        app = self.app()
        app.block_clipboard = (replace(app.blocks[1]),)
        app.edit_name_separator_var.set("김연후")
        app.paste_copied_blocks()
        self.assertEqual(3, len(app.blocks))
        self.assertEqual("김연후", app.blocks[0].name_separator_label)
        self.assertEqual(1, app.selected_block_index())
        self.assertIs(app.blocks[1], app.editor_block)
        self.assertFalse(app.keyboard_state.editor_dirty)
        self.assertEqual(2, len(app.undo_stack))

    def test_block_copy_uses_latest_editor_values(self):
        app = self.app()
        app.edit_copies_var.set(3)
        app.copy_selected_blocks()
        self.assertEqual(3, app.block_clipboard[0].copies)
        self.assertFalse(app.keyboard_state.editor_dirty)

    def test_name_sort_uses_edited_name_and_keeps_focused_block(self):
        app = self.app()
        app.blocks[1].name_separator_label = "강하늘"
        app.edit_name_separator_var.set("홍길동")
        app.sort_blocks_by_name()
        self.assertEqual(["강하늘", "홍길동"], [item.name_separator_label for item in app.blocks])
        self.assertEqual(1, app.selected_block_index())
        self.assertIs(app.blocks[1], app.editor_block)
        self.assertEqual(2, len(app.undo_stack))

    def test_manual_save_refreshes_baseline_without_recursive_autosave(self):
        app = self.app()
        app.edit_copies_var.set(2)
        app.save_editor_from_keyboard()
        self.assertEqual(2, app.blocks[0].copies)
        self.assertIs(app.blocks[0], app.editor_block)
        self.assertFalse(app.keyboard_state.editor_dirty)
        self.assertEqual(1, len(app.undo_stack))
        self.warning.assert_not_called()
        app.capture_queue_history.assert_called_once_with()

    def test_autosave_can_be_undone_and_redone(self):
        app = self.app()
        app.edit_name_separator_var.set("김연후")
        app.select_queue_index(1)
        app.undo_last_action()
        self.assertEqual("", app.blocks[0].name_separator_label)
        self.assertEqual("", app.edit_name_separator_var.get())
        app.redo_last_action()
        self.assertEqual("김연후", app.blocks[0].name_separator_label)

    def test_escape_discards_draft_without_autosaving(self):
        app = self.app()
        app.keyboard_state.enter_editor(cursor=0)
        app.edit_name_separator_var.set("김연후")
        app.handle_escape_shortcut(SimpleNamespace(widget=Mock()))
        self.assertEqual("", app.edit_name_separator_var.get())
        self.assertEqual("", app.blocks[0].name_separator_label)
        self.assertFalse(app.keyboard_state.editor_dirty)
        app.capture_queue_history.assert_not_called()

    def test_f6_saves_before_leaving_editor_without_collapsing_multiselection(self):
        app = self.app()
        app.selected_block_indices = {0, 1}
        app.keyboard_state.enter_editor(cursor=0)
        app.widget_is_in_queue_editor.return_value = True
        app.edit_copies_var.set(2)
        app.handle_workspace_cycle(SimpleNamespace(widget=Mock(), state=0))
        self.assertEqual(2, app.blocks[0].copies)
        self.assertEqual({0, 1}, app.selected_block_indices)
        app.focus_builder_area.assert_called_once_with()

    def test_ime_only_draft_is_committed_before_mouse_focus_changes(self):
        app = self.app()
        entry = Mock(spec=tk.Entry)
        app.root.focus_get.return_value = entry
        app.widget_is_in_queue_editor.return_value = True
        calls = []

        def finish_ime(widget):
            self.assertIs(entry, widget)
            calls.append(app.selected_block_index())
            if len(calls) == 1:
                app.edit_name_separator_var.set("김연후")
                return True
            return False

        app.take_name_entry_ime.side_effect = finish_ime
        self.assertFalse(app.keyboard_state.editor_dirty)
        self.assertTrue(app.select_queue_index(1))
        self.assertEqual("김연후", app.blocks[0].name_separator_label)
        self.assertTrue(calls)
        self.assertEqual({0}, set(calls))


if __name__ == "__main__":
    unittest.main()
