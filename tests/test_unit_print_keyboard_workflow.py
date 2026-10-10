"""Application keyboard workflows without a window, config writes, or printing.

These scenarios exercise the manager's actual handlers and queue transitions.
Widget geometry and focus are fake; the print engine and Windows calls are
tripwires so accidental dispatch cannot reach the operator's printer.
"""
from __future__ import annotations

import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from test_unit_print_queue_lifecycle import block, make_app
from unit_print_keyboard import KeyboardInteraction, KeyboardState, KeyboardZone
from unit_print_retry import RetryBatch


def event(widget, keysym="Return", state=0, **extra):
    return SimpleNamespace(widget=widget, keysym=keysym, state=state, **extra)


class KeyboardWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        for name in ("showinfo", "showwarning", "showerror"):
            patch("unit_print_manager.messagebox." + name).start()
        patch("unit_print_manager.messagebox.askyesno", return_value=True).start()
        for name in (
            "sumatra_enqueue_pdfs", "shell_print_pdf", "save_config",
            "load_printers", "get_print_queue_count", "run_powershell",
        ):
            patch("unit_print_manager." + name, side_effect=AssertionError(name)).start()
        patch("unit_print_manager.complete_windows_ime_composition", return_value=False).start()

    def app(self, count=4):
        app = make_app([block(f"item-{i}") for i in range(count)])
        app.keyboard_state = KeyboardState()
        app.keyboard_latches = set()
        app.redo_stack = []
        app.queue_selection_anchor = 0 if count else None
        app.block_clipboard = ()
        app.material_dropdown_widget = None
        app.custom_drop_list = Mock(spec=tk.Listbox)
        app.queue_canvas = Mock(spec=tk.Canvas)
        app.queue_canvas.winfo_height.return_value = 220
        app.queue_canvas.canvasy.side_effect = lambda y: y
        app.queue_canvas.winfo_rootx.return_value = 300
        app.queue_canvas.winfo_rooty.return_value = 200
        app.queue_card_height = 40
        app.queue_card_gap = 10
        app.queue_padding = 5
        for name in (
            "load_selected_block_to_form", "focus_queue_canvas", "focus_queue_editor",
            "focus_builder_area", "set_active_workspace", "refresh_editor_status",
            "refresh_retry_button", "capture_queue_history", "cancel_drop_animation",
            "populate_queue_editor",
            "cancel_lift_animation", "start_drop_animation", "close_keyboard_popup",
        ):
            setattr(app, name, Mock(return_value="break"))
        app.widget_is_in_queue = Mock(return_value=True)
        app.widget_is_in_queue_editor = Mock(return_value=False)
        app.widget_is_in_builder = Mock(return_value=False)
        app.is_managed_combo = Mock(return_value=False)
        return app

    def editor(self, app):
        entry = Mock(spec=tk.Entry)
        entry.winfo_class.return_value = "Entry"
        app.keyboard_state.enter_editor(cursor=0)
        app.keyboard_state.editor_dirty = True
        app.widget_is_in_queue_editor.return_value = True
        app.take_name_entry_ime = Mock(return_value=False)
        app.keyboard_widget_text = Mock(return_value="김연후")
        app.replace_selected_block_from_editor = Mock(return_value=True)
        return entry

    def run_first_pending_callback(self, app):
        delay, callback = app.root.after.call_args_list[0].args[:2]
        self.assertEqual(0, delay)
        callback()

    def test_enter_saves_editor_after_ime_commit_without_duplicate_key_repeat(self):
        app = self.app()
        entry = self.editor(app)
        for key in ("Return", "KP_Enter"):
            self.assertEqual("break", app.handle_return_shortcut(event(entry, key)))
        app.replace_selected_block_from_editor.assert_not_called()
        self.assertEqual(1, app.take_name_entry_ime.call_count)
        self.run_first_pending_callback(app)
        app.replace_selected_block_from_editor.assert_called_once_with()
        app.focus_queue_canvas.assert_called_once_with()
        self.assertFalse(app.keyboard_state.editor_dirty)
        app.refresh_editor_status.assert_called_once_with()

    def test_keypad_enter_alone_saves_editor(self):
        app = self.app()
        entry = self.editor(app)
        app.handle_return_shortcut(event(entry, "KP_Enter"))
        self.run_first_pending_callback(app)
        app.replace_selected_block_from_editor.assert_called_once_with()

    def test_invalid_editor_save_keeps_editor_dirty_and_focus(self):
        app = self.app()
        entry = self.editor(app)
        app.replace_selected_block_from_editor.return_value = False
        app.handle_return_shortcut(event(entry))
        self.run_first_pending_callback(app)
        self.assertTrue(app.keyboard_state.editor_dirty)
        app.focus_queue_canvas.assert_not_called()
        app.refresh_editor_status.assert_not_called()

    def test_ctrl_s_uses_same_ime_aware_editor_save(self):
        app = self.app()
        entry = self.editor(app)
        self.assertEqual("break", app.handle_save_shortcut(event(entry, "s", 4)))
        app.replace_selected_block_from_editor.assert_not_called()
        self.run_first_pending_callback(app)
        app.replace_selected_block_from_editor.assert_called_once_with()
        app.save_current_config.assert_not_called()

    def test_open_combo_return_and_ctrl_s_never_save_editor(self):
        app = self.app()
        entry = self.editor(app)
        app.keyboard_state.open_combo(".editor.combo")
        self.assertIsNone(app.handle_return_shortcut(event(entry)))
        self.assertEqual("break", app.handle_save_shortcut(event(entry, "s", 4)))
        app.replace_selected_block_from_editor.assert_not_called()
        app.root.after.assert_not_called()

    def test_closed_combo_enter_opens_choices_instead_of_saving(self):
        from unit_print_manager import ttk

        app = self.app()
        combo = Mock(spec=ttk.Combobox)
        self.editor(app)
        app.is_managed_combo.return_value = True
        app.open_material_dropdown = Mock(return_value="break")
        self.assertEqual("break", app.handle_return_shortcut(event(combo)))
        app.open_material_dropdown.assert_called_once_with(combo)
        app.replace_selected_block_from_editor.assert_not_called()

    def test_ctrl_shift_enter_does_not_print_dirty_editor(self):
        app = self.app()
        entry = self.editor(app)
        app.start_print_thread = Mock(side_effect=AssertionError("live print"))
        self.assertEqual("break", app.handle_ctrl_shift_enter_shortcut(event(entry, state=5)))
        app.start_print_thread.assert_not_called()
        self.assertIn("Enter", app.queue_status_var.get())

    def test_tab_and_shift_tab_traverse_editor_fields_then_wrap(self):
        app = self.app()
        controls = [Mock() for _ in range(3)]
        app.widget_is_in_queue_editor.return_value = True
        app.editor_focus_widgets = Mock(return_value=controls)
        self.assertEqual("break", app.handle_workspace_tab(event(controls[0], "Tab")))
        controls[1].focus_set.assert_called_once_with()
        app.handle_workspace_tab(event(controls[1], "ISO_Left_Tab", 1))
        controls[0].focus_set.assert_called_once_with()
        app.handle_workspace_tab(event(controls[0], "ISO_Left_Tab", 1))
        controls[2].focus_set.assert_called_once_with()
        app.focus_builder_area.assert_not_called()
        app.focus_queue_canvas.assert_not_called()

    def test_builder_tab_preserves_standard_field_order(self):
        app = self.app()
        controls = [Mock() for _ in range(2)]
        app.widget_is_in_queue.return_value = False
        app.widget_is_in_builder.return_value = True
        app.builder_focus_widgets = Mock(return_value=controls)
        app.handle_workspace_tab(event(controls[0], "Tab"))
        controls[1].focus_set.assert_called_once_with()
        app.handle_workspace_tab(event(controls[0], "ISO_Left_Tab", 1))
        self.assertEqual(2, controls[1].focus_set.call_count)

    def test_f6_saves_unsaved_editor_before_moving(self):
        app = self.app()
        entry = self.editor(app)
        app.handle_workspace_cycle(event(entry, "F6"))
        app.replace_selected_block_from_editor.assert_called_once_with()
        app.focus_builder_area.assert_called_once_with()
        app.focus_queue_canvas.assert_not_called()
        self.assertFalse(app.keyboard_state.editor_dirty)

    def test_f6_stays_in_editor_when_save_fails(self):
        app = self.app()
        entry = self.editor(app)
        app.replace_selected_block_from_editor.return_value = False
        app.handle_workspace_cycle(event(entry, "F6"))
        app.focus_builder_area.assert_not_called()
        app.focus_queue_canvas.assert_not_called()
        app.focus_queue_editor.assert_called_once_with()
        self.assertTrue(app.keyboard_state.editor_dirty)
        self.assertIn("Esc", app.queue_status_var.get())

    def test_f6_and_shift_f6_cycle_queue_and_editor(self):
        app = self.app()
        app.handle_workspace_cycle(event(app.queue_canvas, "F6"))
        app.focus_queue_editor.assert_called_once_with()
        app.handle_workspace_cycle(event(app.queue_canvas, "F6", 1))
        app.focus_builder_area.assert_called_once_with()
        app.widget_is_in_queue_editor.return_value = True
        app.handle_workspace_cycle(event(Mock(), "F6", 1))
        app.focus_queue_canvas.assert_called_once_with()

    def test_listbox_arrow_keys_are_left_to_native_extended_selection(self):
        app = self.app()
        app.focus_control_direction = Mock(side_effect=AssertionError("list intercepted"))
        for key, modifiers in (("Up", 0), ("Down", 1), ("Home", 4)):
            self.assertIsNone(app.handle_control_arrow(event(app.custom_drop_list, key, modifiers)))
        app.focus_control_direction.assert_not_called()

    def test_ctrl_and_shift_caret_keys_are_not_panel_navigation(self):
        app = self.app()
        app.focus_control_direction = Mock(side_effect=AssertionError("caret intercepted"))
        for modifier in (1, 4, 5):
            self.assertIsNone(app.handle_control_arrow(event(Mock(), "Down", modifier)))

    def test_spinbox_up_down_keep_native_number_increment_and_decrement(self):
        from unit_print_manager import ttk

        app = self.app()
        app.focus_control_direction = Mock(side_effect=AssertionError("spinbox intercepted"))
        for widget_type in (tk.Spinbox, ttk.Spinbox):
            spinbox = Mock(spec=widget_type)
            for key in ("Up", "Down", "KP_Up", "KP_Down"):
                self.assertIsNone(app.handle_control_arrow(event(spinbox, key)))
        app.focus_control_direction.assert_not_called()

    def test_text_ctrl_a_and_undo_do_not_select_or_undo_queue(self):
        app = self.app()
        entry = Mock(spec=tk.Entry)
        entry.winfo_class.return_value = "Entry"
        app.keyboard_state.enter_queue(cursor=0)
        app.undo_last_action = Mock(side_effect=AssertionError("queue undo"))
        self.assertIsNone(app.handle_ctrl_a_shortcut(event(entry, "a", 4)))
        self.assertIsNone(app.handle_undo_shortcut(event(entry, "z", 4)))
        self.assertEqual({0}, app.selected_block_indices)

    def test_queue_ctrl_a_selects_all_rows_without_changing_current_cursor(self):
        app = self.app(5)
        app.keyboard_state.enter_queue(cursor=3)
        app.selected_block_index_value = 3
        app.queue_canvas.winfo_class.return_value = "Canvas"
        self.assertEqual("break", app.handle_ctrl_a_shortcut(event(app.queue_canvas, "a", 4)))
        self.assertEqual(set(range(5)), app.selected_block_indices)
        self.assertEqual(3, app.selected_block_index())

    def test_csv_list_select_all_and_copy_use_native_rows_without_queue_changes(self):
        app = self.app(2)
        preview = Mock(spec=tk.Listbox)
        preview.size.return_value = 3
        preview.curselection.return_value = (0, 2)
        preview.get.side_effect = lambda index: ("김연후 주문", "박학생 주문", "홍학생 주문")[index]
        app.handle_native_list_select_all(event(preview, "a", 4))
        preview.selection_set.assert_called_once_with(0, "end")
        app.handle_native_list_copy(event(preview, "c", 4))
        app.root.clipboard_append.assert_called_once_with("김연후 주문\n홍학생 주문")
        self.assertEqual({0}, app.selected_block_indices)

    def test_ctrl_navigation_retains_selection_and_shift_extends_stable_anchor(self):
        app = self.app(8)
        app.selected_block_indices = {0, 6}
        app.handle_queue_arrow(event(app.queue_canvas, "Down", 4))
        self.assertEqual({0, 6}, app.selected_block_indices)
        self.assertEqual(1, app.selected_block_index())
        self.assertEqual(0, app.queue_selection_anchor)
        app.handle_queue_arrow(event(app.queue_canvas, "Down", 5))
        self.assertEqual({0, 1, 2, 6}, app.selected_block_indices)
        self.assertEqual(2, app.selected_block_index())
        app.handle_queue_arrow(event(app.queue_canvas, "Up", 1))
        self.assertEqual({0, 1}, app.selected_block_indices)
        self.assertEqual(0, app.queue_selection_anchor)

    def test_home_end_and_page_navigation_reach_visible_pages_and_boundaries(self):
        app = self.app(12)
        app.handle_queue_arrow(event(app.queue_canvas, "Next"))
        self.assertEqual(4, app.selected_block_index())
        self.assertEqual({4}, app.selected_block_indices)
        app.handle_queue_arrow(event(app.queue_canvas, "End", 1))
        self.assertEqual(set(range(4, 12)), app.selected_block_indices)
        app.handle_queue_arrow(event(app.queue_canvas, "Home", 4))
        self.assertEqual(0, app.selected_block_index())
        self.assertEqual(set(range(4, 12)), app.selected_block_indices)
        app.handle_queue_arrow(event(app.queue_canvas, "Prior"))
        self.assertEqual({0}, app.selected_block_indices)

    def test_ctrl_shift_click_unions_range_without_losing_disjoint_selection(self):
        app = self.app(6)
        app.selected_block_indices = {0, 5}
        app.selected_block_index_value = 0
        app.queue_selection_anchor = 0
        app.select_queue_with_modifiers(2, 5)
        self.assertEqual({0, 1, 2, 5}, app.selected_block_indices)
        self.assertEqual(2, app.selected_block_index())
        self.assertEqual(0, app.queue_selection_anchor)

    def test_deselect_last_row_keeps_cursor_but_copy_delete_move_do_nothing(self):
        app = self.app(2)
        originals = tuple(app.blocks)
        previous_clipboard = (block("clipboard"),)
        app.block_clipboard = previous_clipboard
        app.select_queue_with_modifiers(0, 4)
        self.assertEqual(set(), app.selected_block_indices)
        self.assertEqual(0, app.selected_block_index())
        app.copy_selected_blocks()
        app.delete_selected_block()
        app.move_selected_block(1)
        self.assertEqual(originals, tuple(app.blocks))
        self.assertIs(previous_clipboard, app.block_clipboard)
        self.assertEqual([], app.undo_stack)

    def test_real_queue_refresh_does_not_reselect_deselected_cursor(self):
        app = self.app(2)
        del app.update_queue_view
        app.populate_queue_editor = Mock()
        app.selected_block_indices = set()
        app.selected_block_index_value = 1
        app.keyboard_state.enter_queue(cursor=1)
        app.update_queue_view()
        self.assertEqual(set(), app.selected_block_indices)
        self.assertEqual(1, app.selected_block_index())
        self.assertEqual(1, app.keyboard_state.queue_cursor)
        app.populate_queue_editor.assert_called_once_with(1)

    def test_undo_restores_explicit_empty_selection_independently_of_cursor(self):
        app = self.app(2)
        app.selected_block_indices = set()
        app.selected_block_index_value = 1
        app.queue_selection_anchor = 0
        app.push_undo_state("empty selection")
        app.selected_block_indices = {0, 1}
        app.selected_block_index_value = 0
        app.undo_last_action()
        self.assertEqual(set(), app.selected_block_indices)
        self.assertEqual(1, app.selected_block_index())
        self.assertEqual(0, app.queue_selection_anchor)

    def test_duplicate_inserts_group_below_selection_with_fresh_ids_and_keeps_clipboard(self):
        app = self.app(4)
        app.selected_block_indices = {0, 2}
        originals = tuple(app.blocks)
        app.block_status_by_id[id(originals[0])] = "완료"
        previous_clipboard = (block("old clipboard"),)
        app.block_clipboard = previous_clipboard
        app.duplicate_selected_blocks()
        self.assertEqual([originals[0], originals[1], originals[2]], app.blocks[:3])
        clones = app.blocks[3:5]
        self.assertEqual([originals[0].subject, originals[2].subject], [b.subject for b in clones])
        self.assertIs(originals[3], app.blocks[5])
        for original, clone in zip((originals[0], originals[2]), clones):
            self.assertIsNot(original, clone)
            self.assertNotEqual(original.history_id, clone.history_id)
            self.assertIsNot(original.files, clone.files)
            self.assertEqual("대기", app.block_status_by_id[id(clone)])
        self.assertIs(previous_clipboard, app.block_clipboard)
        self.assertEqual({3, 4}, app.selected_block_indices)

    def test_duplicate_deferred_commit_keeps_its_payload_after_restoring_clipboard(self):
        app = self.app(2)
        app.block_clipboard = (block("old clipboard"),)
        clipboard = app.block_clipboard
        pending = []
        app.complete_block_commit = Mock(side_effect=lambda _token, callback: pending.append(callback))
        app.duplicate_selected_blocks()
        self.assertEqual(2, len(app.blocks))
        self.assertIs(clipboard, app.block_clipboard)
        pending[0]()
        self.assertEqual("item-0", app.blocks[1].subject)
        self.assertIs(clipboard, app.block_clipboard)

    def test_paste_without_selection_appends_even_with_unselected_cursor(self):
        app = self.app(3)
        app.selected_block_indices = set()
        app.selected_block_index_value = 0
        original_tail = app.blocks[-1]
        app.block_clipboard = (block("paste"),)
        app.paste_copied_blocks()
        self.assertIs(original_tail, app.blocks[-2])
        self.assertEqual("paste", app.blocks[-1].subject)

    def test_undo_redo_restores_multi_selection_status_details_and_same_job_identities(self):
        app = self.app(4)
        originals = tuple(app.blocks)
        jobs = tuple(app.build_print_jobs(app.blocks))
        app.retry_batch = RetryBatch(jobs, include_separator_pages=True)
        app.block_status_by_id = {id(originals[0]): "완료", id(originals[2]): "인쇄 중지"}
        app.block_detail_by_id = {id(originals[0]): "actual finished", id(originals[2]): "retained"}
        app.selected_block_indices = {0, 2}
        app.selected_block_index_value = 2
        app.queue_selection_anchor = 0
        app.push_undo_state("test transition")
        app.blocks = [originals[1], originals[3]]
        app.block_status_by_id = {id(originals[1]): "추가됨"}
        app.block_detail_by_id = {id(originals[1]): "after"}
        app.retry_batch = RetryBatch((jobs[1], jobs[3]))
        app.selected_block_indices = {1}
        app.selected_block_index_value = 0
        app.queue_selection_anchor = 1
        app.undo_last_action()
        self.assertEqual(originals, tuple(app.blocks))
        self.assertEqual({0, 2}, app.selected_block_indices)
        self.assertEqual(2, app.selected_block_index())
        self.assertEqual(0, app.queue_selection_anchor)
        self.assertEqual("완료", app.block_status_by_id[id(originals[0])])
        self.assertEqual("retained", app.block_detail_by_id[id(originals[2])])
        for before, restored in zip(jobs, app.retry_batch.jobs):
            self.assertIs(before, restored)
        self.assertTrue(app.retry_batch.include_separator_pages)
        app.redo_last_action()
        self.assertEqual([originals[1], originals[3]], app.blocks)
        self.assertEqual({1}, app.selected_block_indices)
        self.assertEqual(0, app.selected_block_index())
        self.assertEqual(1, app.queue_selection_anchor)
        self.assertEqual({id(originals[1]): "추가됨"}, app.block_status_by_id)
        self.assertIs(jobs[1], app.retry_batch.jobs[0])
        self.assertIs(jobs[3], app.retry_batch.jobs[1])

    def test_new_queue_mutation_after_undo_discards_redo_branch(self):
        app = self.app(3)
        app.push_undo_state("before first")
        app.blocks.append(block("first"))
        app.undo_last_action()
        self.assertEqual(1, len(app.redo_stack))
        app.push_undo_state("new branch")
        app.blocks.append(block("new"))
        self.assertEqual([], app.redo_stack)
        app.redo_last_action()
        self.assertEqual("new", app.blocks[-1].subject)

    def test_undo_and_redo_are_protected_during_printing(self):
        app = self.app(2)
        app.push_undo_state("test")
        app.blocks.append(block("after"))
        app.undo_last_action()
        undo_before, redo_before = list(app.undo_stack), list(app.redo_stack)
        blocks_before = tuple(app.blocks)
        app.is_printing = True
        app.undo_last_action()
        app.redo_last_action()
        self.assertEqual(blocks_before, tuple(app.blocks))
        self.assertEqual(undo_before, app.undo_stack)
        self.assertEqual(redo_before, app.redo_stack)

    def test_click_release_on_selected_group_keeps_whole_group(self):
        app = self.app(4)
        app.selected_block_indices = {0, 2, 3}
        app.queue_selection_anchor = 0
        click = event(app.queue_canvas, y=110)
        app.on_queue_drag_start(click)
        self.assertEqual((0, 2, 3), app.drag_selected_indices)
        self.assertFalse(app.drag_active)
        app.on_queue_drag_release(click)
        self.assertEqual({0, 2, 3}, app.selected_block_indices)
        self.assertEqual(2, app.selected_block_index())
        self.assertEqual(0, app.queue_selection_anchor)
        self.assertEqual([], app.undo_stack)

    def test_print_start_between_drag_press_and_release_cannot_reorder(self):
        app = self.app(4)
        before = tuple(app.blocks)
        app.on_queue_drag_start(event(app.queue_canvas, y=10))
        app.drag_active = True
        app.drag_drop_index = 4
        app.current_queue_card_positions = Mock(return_value={})
        app.is_printing = True
        app.print_scheduled_block_ids = {id(before[0])}
        app.on_queue_drag_release(event(app.queue_canvas, y=220))
        self.assertEqual(before, tuple(app.blocks))
        self.assertEqual([], app.undo_stack)
        app.capture_queue_history.assert_not_called()
        self.assertIn("불가", app.queue_status_var.get())

    def test_alt_move_preserves_unselected_cursor_and_anchor_by_block_identity(self):
        app = self.app(5)
        before = tuple(app.blocks)
        app.selected_block_indices = {2, 4}
        app.selected_block_index_value = 1
        app.queue_selection_anchor = 3
        app.move_selected_block(-1)
        self.assertEqual([before[0], before[2], before[1], before[4], before[3]], app.blocks)
        self.assertEqual({1, 3}, app.selected_block_indices)
        self.assertIs(before[1], app.blocks[app.selected_block_index()])
        self.assertIs(before[3], app.blocks[app.queue_selection_anchor])

    def test_keyboard_context_menu_stays_alive_and_can_invoke_selected_action(self):
        app = self.app(3)
        app.selected_block_indices = {0, 2}
        app.selected_block_index_value = 2
        menu = Mock()
        with patch("unit_print_manager.tk.Menu", return_value=menu):
            self.assertEqual("break", app.handle_queue_context_key(event(app.queue_canvas, "F10", 1)))
        menu.tk_popup.assert_called_once_with(328, 325)
        menu.grab_release.assert_called_once_with()
        menu.destroy.assert_not_called()
        self.assertIs(menu, app.queue_context_menu)
        commands = {call.kwargs["label"]: call.kwargs for call in menu.add_command.call_args_list}
        self.assertEqual("normal", commands["복사"]["state"])
        commands["복사"]["command"]()
        self.assertEqual(["item-0", "item-2"], [b.subject for b in app.block_clipboard])
        self.assertEqual({0, 2}, app.selected_block_indices)

    def test_context_menu_replaces_previous_menu_without_destroying_new_menu(self):
        app = self.app(2)
        old, new = Mock(), Mock()
        app.queue_context_menu = old
        with patch("unit_print_manager.tk.Menu", return_value=new):
            app.post_queue_context_menu(10, 20)
        old.destroy.assert_called_once_with()
        new.destroy.assert_not_called()
        self.assertIs(new, app.queue_context_menu)


if __name__ == "__main__":
    unittest.main()
