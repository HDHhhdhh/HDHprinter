from __future__ import annotations

from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from unit_print_text_shortcuts import EditHistory, EntrySnapshot, TextShortcutBindings


class EditHistoryTests(unittest.TestCase):
    def test_undo_redo_preserve_cursor_and_selection(self) -> None:
        selected = EntrySnapshot("before", 3, (1, 4))
        history = EditHistory(selected)
        edited = EntrySnapshot("after", 5)
        history.record(edited)

        self.assertEqual(selected, history.undo())
        self.assertFalse(history.can_undo)
        self.assertEqual(edited, history.redo())
        self.assertFalse(history.can_redo)
        self.assertIsNone(history.redo())

    def test_new_edit_after_undo_discards_redo_branch(self) -> None:
        history = EditHistory(EntrySnapshot(""))
        history.record(EntrySnapshot("a", 1))
        history.record(EntrySnapshot("ab", 2))
        history.undo()
        history.record(EntrySnapshot("ac", 2))

        self.assertFalse(history.can_redo)
        self.assertEqual("a", history.undo().text)
        self.assertEqual("ac", history.redo().text)

    def test_bound_retains_most_recent_edits(self) -> None:
        history = EditHistory(EntrySnapshot("0"), limit=3)
        for number in range(1, 6):
            history.record(EntrySnapshot(str(number), 1))

        self.assertEqual("4", history.undo().text)
        self.assertEqual("3", history.undo().text)
        self.assertIsNone(history.undo())
        self.assertEqual("4", history.redo().text)
        self.assertEqual("5", history.redo().text)

    def test_cursor_navigation_does_not_add_an_edit_or_discard_redo(self) -> None:
        history = EditHistory(EntrySnapshot("a", 1))
        history.record(EntrySnapshot("ab", 2))
        history.undo()

        self.assertFalse(history.record(EntrySnapshot("a", 0, (0, 1))))
        self.assertTrue(history.can_redo)
        self.assertFalse(history.can_undo)
        self.assertEqual(EntrySnapshot("a", 0, (0, 1)), history.current)

    def test_external_population_resets_both_undo_and_redo(self) -> None:
        history = EditHistory(EntrySnapshot("old", 3))
        history.record(EntrySnapshot("old edit", 8))
        history.undo()

        self.assertTrue(history.synchronize(EntrySnapshot("new block", 9)))
        self.assertIsNone(history.undo())
        self.assertIsNone(history.redo())
        history.record(EntrySnapshot("new block!", 10))
        self.assertEqual("new block", history.undo().text)

    def test_invalid_history_limit_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            EditHistory(EntrySnapshot(""), limit=1)


class FakeRoot:
    def __init__(self) -> None:
        self.tk = object()
        self.bindings: dict[tuple[str, str], object] = {}
        self.pending: dict[str, object] = {}
        self.focus = None
        self._next_id = 0

    def bind_class(self, tag, sequence, callback):
        self.bindings[tag, sequence] = callback

    def after_idle(self, callback):
        self._next_id += 1
        identifier = f"idle{self._next_id}"
        self.pending[identifier] = callback
        return identifier

    def after_cancel(self, identifier):
        self.pending.pop(identifier, None)

    def run_idle(self):
        while self.pending:
            identifier, callback = next(iter(self.pending.items()))
            self.pending.pop(identifier)
            callback()


class FakeEntry:
    def __init__(self, root, text="", class_name="TEntry", state="normal"):
        self.root = root
        self.tk = root.tk
        self.text = text
        self.cursor = len(text)
        self.selection = None
        self.class_name = class_name
        self.state = state
        self.exists = True
        self.tags = (".entry", class_name, ".", "all")
        self.changes = []

    def winfo_exists(self):
        return self.exists

    def winfo_class(self):
        return self.class_name

    def cget(self, option):
        return self.state

    def instate(self, states):
        return self.state in states

    def bindtags(self, tags=None):
        if tags is not None:
            self.tags = tags
        return self.tags

    def get(self):
        return self.text

    def index(self, index):
        if index == "insert":
            return self.cursor
        if index == "end":
            return len(self.text)
        if index in ("sel.first", "sel.last"):
            return self.selection[0 if index == "sel.first" else 1]
        return int(index)

    def selection_present(self):
        return self.selection is not None

    def selection_range(self, start, end):
        self.selection = (self.index(start), self.index(end))

    def selection_clear(self):
        self.selection = None

    def icursor(self, index):
        self.cursor = self.index(index)

    def focus_get(self):
        return self.root.focus

    def delete(self, start, end):
        start, end = self.index(start), self.index(end)
        self.text = self.text[:start] + self.text[end:]
        self.cursor = start
        self.selection = None
        self.changes.append(self.text)

    def insert(self, index, text):
        index = self.index(index)
        self.text = self.text[:index] + text + self.text[index:]
        self.cursor = index + len(text)
        self.changes.append(self.text)


class TextShortcutBindingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = FakeRoot()
        self.bindings = TextShortcutBindings(self.root)
        self.entry = FakeEntry(self.root, "start")
        self.root.focus = self.entry
        self.bindings.install(self.entry)

    def event(self, key="", *, state=0, char="", widget=None):
        return SimpleNamespace(widget=widget or self.entry, keysym=key, state=state, char=char)

    def type_text(self, text, *, run_idle=True):
        for char in text:
            self.assertIsNone(self.bindings._on_keypress(self.event(char, char=char)))
            self.entry.insert("insert", char)  # The native entry class action.
            if run_idle:
                self.root.run_idle()

    def shortcut(self, key, *, state=4):
        return self.bindings._on_keypress(self.event(key, state=state))

    def test_bindtag_precedes_native_class_and_install_is_idempotent(self) -> None:
        tags = self.entry.bindtags()
        self.assertEqual((".entry", self.bindings.bindtag, "TEntry", ".", "all"), tags)
        self.assertTrue(self.bindings.install(self.entry))
        self.assertEqual(tags, self.entry.bindtags())
        self.assertNotIn((self.bindings.bindtag, "<<Copy>>"), self.root.bindings)

    def test_non_text_readonly_and_disabled_widgets_are_untouched(self) -> None:
        for class_name, state in (
            ("TCombobox", "normal"), ("Text", "normal"),
            ("TEntry", "readonly"), ("Entry", "disabled"),
        ):
            with self.subTest(class_name=class_name, state=state):
                widget = FakeEntry(self.root, class_name=class_name, state=state)
                tags = widget.bindtags()
                self.assertFalse(self.bindings.install(widget))
                self.assertEqual(tags, widget.bindtags())
        for class_name in ("Entry", "Spinbox", "TSpinbox"):
            self.assertTrue(self.bindings.install(FakeEntry(self.root, class_name=class_name)))

    def test_select_all_and_standard_local_undo_redo(self) -> None:
        self.type_text("!")
        self.assertEqual("break", self.shortcut("a"))
        self.assertEqual((0, 6), self.entry.selection)
        self.assertEqual("break", self.shortcut("z"))
        self.assertEqual("start", self.entry.get())
        self.assertEqual("break", self.shortcut("y"))
        self.assertEqual("start!", self.entry.get())
        self.shortcut("z")
        self.shortcut("Z", state=5)
        self.assertEqual("start!", self.entry.get())
        self.assertEqual(["", "start!"], self.entry.changes[-2:])

    def test_consecutive_keys_before_idle_keep_separate_undo_steps(self) -> None:
        self.type_text("ab", run_idle=False)
        self.shortcut("z")
        self.assertEqual("starta", self.entry.get())
        self.shortcut("z")
        self.assertEqual("start", self.entry.get())
        self.root.run_idle()
        self.assertEqual("start", self.entry.get())

    def test_external_value_is_baseline_before_next_edit_or_focus(self) -> None:
        self.type_text("!")
        self.entry.text, self.entry.cursor = "another block", 13
        self.shortcut("z")
        self.assertEqual("another block", self.entry.get())
        self.type_text("?")
        self.shortcut("z")
        self.assertEqual("another block", self.entry.get())
        self.entry.text, self.entry.cursor = "third block", 11
        self.bindings._on_focus(self.event())
        self.shortcut("y")
        self.assertEqual("third block", self.entry.get())

    def test_native_navigation_copy_cut_and_paste_are_not_consumed(self) -> None:
        self.assertIsNone(self.shortcut("Left"))
        self.assertIsNone(self.shortcut("c"))
        self.assertIsNone(self.shortcut("v"))
        self.entry.insert("end", " paste")
        self.root.run_idle()
        self.shortcut("z")
        self.assertEqual("start", self.entry.get())
        self.assertIsNone(self.bindings._on_edit(self.event()))
        self.entry.delete(0, "end")
        self.root.run_idle()
        self.shortcut("z")
        self.assertEqual("start", self.entry.get())

    def test_altgr_key_does_not_trigger_control_shortcut(self) -> None:
        self.assertIsNone(self.shortcut("a", state=4 | 0x20000))
        self.assertIsNone(self.entry.selection)

    def test_focus_and_current_state_guard_undo(self) -> None:
        self.type_text("!")
        self.root.focus = None
        self.assertIsNone(self.shortcut("z"))
        self.root.focus = self.entry
        self.entry.state = "readonly"
        self.assertIsNone(self.shortcut("z"))
        self.assertEqual("start!", self.entry.get())

    def test_destroy_cancels_pending_and_late_capture_is_safe(self) -> None:
        self.type_text("!", run_idle=False)
        self.entry.exists = False
        self.bindings._on_destroy(self.event())
        self.assertFalse(self.root.pending)
        self.bindings._capture(self.entry)
        self.assertNotIn(self.entry, self.bindings._fields)


class TkTextShortcutEventTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.root = tk.Tk()
            cls.root.withdraw()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"Tk is unavailable: {exc}") from exc

    @classmethod
    def tearDownClass(cls) -> None:
        cls.root.destroy()

    def test_native_clear_event_has_local_undo_redo(self) -> None:
        for entry_type in (tk.Entry, ttk.Entry, tk.Spinbox, ttk.Spinbox):
            with self.subTest(entry_type=entry_type.__name__):
                widget = entry_type(self.root)
                widget.insert(0, "native edit")
                bindings = TextShortcutBindings(self.root)
                self.assertTrue(bindings.install(widget))
                widget.selection_range(0, "end")
                widget.event_generate("<<Clear>>")
                self.root.update_idletasks()
                self.assertEqual("", widget.get())
                # Virtual events can be sent to an unmapped test widget. Keep
                # the production focus guard while avoiding a visible window.
                with patch.object(bindings, "_has_focus", return_value=True):
                    widget.event_generate("<<Undo>>")
                    self.assertEqual("native edit", widget.get())
                    widget.event_generate("<<Redo>>")
                    self.assertEqual("", widget.get())
                widget.destroy()
                self.root.update_idletasks()

    def test_string_variable_population_resets_history(self) -> None:
        value = tk.StringVar(self.root, value="first")
        widget = ttk.Entry(self.root, textvariable=value)
        bindings = TextShortcutBindings(self.root)
        bindings.install(widget)
        widget.selection_range(0, "end")
        widget.event_generate("<<Clear>>")
        self.root.update_idletasks()
        value.set("different block")
        widget.event_generate("<FocusIn>")
        with patch.object(bindings, "_has_focus", return_value=True):
            widget.event_generate("<<Undo>>")
        self.assertEqual("different block", value.get())
        widget.destroy()


if __name__ == "__main__":
    unittest.main()
