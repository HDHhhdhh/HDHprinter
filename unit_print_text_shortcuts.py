"""Local editing shortcuts for the application's single-line text fields.

The extra bindtag runs before Tk's entry class so clipboard and navigation
remain native.  Entry widgets do not have Tk Text's undo stack, so a small
snapshot history supplies undo and redo without tracing application variables.
"""

from __future__ import annotations

from dataclasses import dataclass
import tkinter as tk


@dataclass(frozen=True)
class EntrySnapshot:
    text: str
    cursor: int = 0
    selection: tuple[int, int] | None = None


class EditHistory:
    """Bounded text edits, with the cursor and selection for each value."""

    def __init__(self, initial: EntrySnapshot, *, limit: int = 100) -> None:
        if limit < 2:
            raise ValueError("The snapshot limit must be at least two.")
        self.limit = limit
        self.reset(initial)

    @property
    def current(self) -> EntrySnapshot:
        return self._snapshots[self._index]

    @property
    def can_undo(self) -> bool:
        return self._index > 0

    @property
    def can_redo(self) -> bool:
        return self._index + 1 < len(self._snapshots)

    def reset(self, snapshot: EntrySnapshot) -> None:
        self._snapshots = [snapshot]
        self._index = 0

    def synchronize(self, snapshot: EntrySnapshot) -> bool:
        """Discard stale edits if an application changed the text externally."""
        if snapshot.text != self.current.text:
            self.reset(snapshot)
            return True
        self._snapshots[self._index] = snapshot
        return False

    def record(self, snapshot: EntrySnapshot) -> bool:
        if snapshot.text == self.current.text:
            self._snapshots[self._index] = snapshot
            return False
        del self._snapshots[self._index + 1 :]
        self._snapshots.append(snapshot)
        if len(self._snapshots) > self.limit:
            del self._snapshots[: len(self._snapshots) - self.limit]
        self._index = len(self._snapshots) - 1
        return True

    def undo(self) -> EntrySnapshot | None:
        if not self.can_undo:
            return None
        self._index -= 1
        return self.current

    def redo(self) -> EntrySnapshot | None:
        if not self.can_redo:
            return None
        self._index += 1
        return self.current


@dataclass
class _FieldState:
    history: EditHistory
    pending: str | None = None


class TextShortcutBindings:
    """Install Ctrl+A/Z/Y/Shift+Z on editable Entry and Spinbox widgets.

    Call ``install`` for fields in the app and any subsequently created dialog.
    Readonly/disabled fields, comboboxes, and other widgets are left untouched.
    Reinstalling an existing field is harmless.
    """

    _CLASSES = frozenset(("Entry", "TEntry", "Spinbox", "TSpinbox"))
    _CONTROL_MASK = 0x0004
    _SHIFT_MASK = 0x0001
    _ALT_MASK = 0x0008 | 0x20000  # X11 Mod1 and Windows Alt/AltGr.

    def __init__(self, root: tk.Misc, *, history_limit: int = 100) -> None:
        if history_limit < 2:
            raise ValueError("The snapshot limit must be at least two.")
        self.root = root
        self.history_limit = history_limit
        self.bindtag = f"UnitPrintTextShortcuts_{id(self)}"
        self._fields: dict[tk.Misc, _FieldState] = {}
        root.bind_class(self.bindtag, "<KeyPress>", self._on_keypress)
        root.bind_class(self.bindtag, "<FocusIn>", self._on_focus)
        root.bind_class(self.bindtag, "<Destroy>", self._on_destroy)
        for event in ("<<Paste>>", "<<Cut>>", "<<Clear>>"):
            root.bind_class(self.bindtag, event, self._on_edit)
        root.bind_class(self.bindtag, "<<Undo>>", self._on_undo)
        root.bind_class(self.bindtag, "<<Redo>>", self._on_redo)

    @classmethod
    def _editable(cls, widget: tk.Misc) -> bool:
        try:
            return (
                bool(widget.winfo_exists())
                and widget.winfo_class() in cls._CLASSES
                and str(widget.cget("state")) not in ("disabled", "readonly")
                and not (
                    hasattr(widget, "instate")
                    and (widget.instate(("disabled",)) or widget.instate(("readonly",)))
                )
            )
        except (tk.TclError, AttributeError):
            return False

    @staticmethod
    def _snapshot(widget: tk.Misc) -> EntrySnapshot | None:
        try:
            selection = None
            if widget.selection_present():
                selection = (int(widget.index("sel.first")), int(widget.index("sel.last")))
            return EntrySnapshot(str(widget.get()), int(widget.index("insert")), selection)
        except (tk.TclError, AttributeError):
            return None

    def install(self, widget: tk.Misc) -> bool:
        """Return whether this field has the bindings; unsupported fields no-op."""
        if not self._editable(widget):
            return False
        if widget in self._fields:
            return True
        snapshot = self._snapshot(widget)
        if snapshot is None or widget.tk is not self.root.tk:
            return False
        tags = tuple(tag for tag in widget.bindtags() if tag != self.bindtag)
        widget.bindtags((tags[0], self.bindtag, *tags[1:]))
        self._fields[widget] = _FieldState(EditHistory(snapshot, limit=self.history_limit))
        return True

    def _cancel_pending(self, state: _FieldState) -> None:
        if state.pending is not None:
            try:
                self.root.after_cancel(state.pending)
            except tk.TclError:
                pass
            state.pending = None

    def _capture(self, widget: tk.Misc) -> None:
        state = self._fields.get(widget)
        if state is None:
            return
        self._cancel_pending(state)
        snapshot = self._snapshot(widget)
        if snapshot is not None:
            state.history.record(snapshot)

    def _prepare(self, widget: tk.Misc) -> _FieldState | None:
        state = self._fields.get(widget)
        if state is None or not self._editable(widget):
            return None
        # A subsequent key can arrive before the previous after_idle callback.
        if state.pending is not None:
            self._capture(widget)
        snapshot = self._snapshot(widget)
        if snapshot is None:
            return None
        state.history.synchronize(snapshot)
        return state

    def _on_focus(self, event: tk.Event) -> None:
        self._prepare(event.widget)

    def _on_destroy(self, event: tk.Event) -> None:
        state = self._fields.pop(event.widget, None)
        if state is not None:
            self._cancel_pending(state)

    def _on_edit(self, event: tk.Event) -> None:
        state = self._prepare(event.widget)
        if state is not None:
            state.pending = self.root.after_idle(lambda: self._capture(event.widget))

    @staticmethod
    def _has_focus(widget: tk.Misc) -> bool:
        try:
            return widget.focus_get() is widget
        except tk.TclError:
            return False

    def _on_keypress(self, event: tk.Event) -> str | None:
        widget = event.widget
        if not self._has_focus(widget):
            return None
        state = self._prepare(widget)
        if state is None:
            return None
        key = str(event.keysym).lower()
        modifiers = int(event.state)
        control = bool(modifiers & self._CONTROL_MASK) and not modifiers & self._ALT_MASK
        if control and key == "a":
            widget.selection_range(0, "end")
            widget.icursor("end")
            return "break"
        if control and key == "z":
            return self._change_history(widget, redo=bool(modifiers & self._SHIFT_MASK))
        if control and key == "y":
            return self._change_history(widget, redo=True)
        # Track text-producing keys and Tk's native deletion/clipboard keys.
        # Enter/Tab/navigation can run application callbacks that populate
        # another block's fields; those values must not enter this undo stack.
        if (
            (bool(event.char) and event.char.isprintable() and not control)
            or key in ("backspace", "delete")
            or (control and key in ("v", "x", "h", "d", "k", "t"))
        ):
            state.pending = self.root.after_idle(lambda: self._capture(widget))
        return None

    def _on_undo(self, event: tk.Event) -> str | None:
        return self._change_history(event.widget, redo=False)

    def _on_redo(self, event: tk.Event) -> str | None:
        return self._change_history(event.widget, redo=True)

    def _change_history(self, widget: tk.Misc, *, redo: bool) -> str | None:
        if not self._has_focus(widget):
            return None
        state = self._prepare(widget)
        if state is None:
            return None
        target = state.history.redo() if redo else state.history.undo()
        if target is None:
            return "break"
        try:
            widget.delete(0, "end")
            widget.insert(0, target.text)
            widget.icursor(target.cursor)
            widget.selection_clear()
            if target.selection is not None:
                widget.selection_range(*target.selection)
        except tk.TclError:
            pass
        actual = self._snapshot(widget)
        if actual is not None:
            # Validators and application callbacks may reject/replace a value.
            state.history.synchronize(actual)
        return "break"
