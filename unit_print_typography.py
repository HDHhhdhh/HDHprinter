"""Korean operator-label wrapping. Source variables and print data stay intact."""
from __future__ import annotations

import re
import tkinter as tk
from tkinter import font as tkfont, ttk
from collections.abc import Callable
import unicodedata


def _text_units(text: str) -> list[str]:
    """Keep combining marks and joined emoji with their base on overflow."""
    units: list[str] = []
    for char in text:
        if units and (
            unicodedata.category(char).startswith("M")
            or char == "\u200d" or units[-1].endswith("\u200d")
            or "\U0001f3fb" <= char <= "\U0001f3ff"
            or len(unicodedata.normalize("NFC", units[-1] + char))
            < len(unicodedata.normalize("NFC", units[-1])) + 1
        ):
            units[-1] += char
        else:
            units.append(char)
    return units


def wrap_korean_text(
    text: str, max_width: int, measure: Callable[[str], int], *, mode: str = "pretty",
) -> str:
    """Keep words intact, preserve explicit lines, and split only overwide words.

    This is a small Tk policy inspired by CSS keep-all/break-word/pre-line.
    ``balance`` narrows short headings without adding lines; ``pretty`` moves
    one word to a lonely last word only when both resulting lines fit.
    Logs, input fields, buttons, and clipped queue titles do not use this helper.
    """
    if mode not in {"normal", "pretty", "balance", "nowrap"}:
        raise ValueError(f"Unknown wrapping mode: {mode}")
    if mode == "nowrap" or max_width <= 0:
        return text
    widths: dict[str, int] = {}

    def width(value: str) -> int:
        if value not in widths:
            widths[value] = measure(value)
        return widths[value]

    def greedy(words: list[str], limit: int) -> list[list[str]]:
        lines: list[list[str]] = []
        current: list[str] = []
        for word in words:
            if width(word) > limit:
                if current:
                    lines.append(current)
                    current = []
                piece = ""
                for unit in _text_units(word):
                    if piece and width(piece + unit) > limit:
                        lines.append([piece])
                        piece = ""
                    piece += unit
                current = [piece]
            elif current and width(" ".join([*current, word])) > limit:
                lines.append(current)
                current = [word]
            else:
                current.append(word)
        if current:
            lines.append(current)
        return lines

    paragraphs: list[str] = []
    for paragraph in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        words = re.findall(r"[^ \t\f\v]+", paragraph)
        if not words:
            paragraphs.append("")
            continue
        lines = greedy(words, max_width)
        if mode == "balance" and 1 < len(lines) <= 6 and len(words) <= 80:
            low, high = max(width(word) for word in words), max_width
            if low <= high:
                target_count = len(lines)
                while low < high:
                    middle = (low + high) // 2
                    if len(greedy(words, middle)) <= target_count:
                        high = middle
                    else:
                        low = middle + 1
                lines = greedy(words, low)
        elif mode == "pretty" and len(lines) >= 2 and len(lines[-1]) == 1 and len(lines[-2]) >= 3:
            last_line = [lines[-2][-1], *lines[-1]]
            if width(" ".join(last_line)) <= max_width:
                lines[-2] = lines[-2][:-1]
                lines[-1] = last_line
        paragraphs.append("\n".join(" ".join(line) for line in lines))
    return "\n".join(paragraphs)


def _horizontal_inset(widget: tk.Misc) -> int:
    options = widget.keys()
    inset = 0
    if "padding" in options:
        values = widget.tk.splitlist(widget.cget("padding"))
        if values:
            left = widget.winfo_pixels(values[0])
            right = widget.winfo_pixels(values[2]) if len(values) >= 3 else left
            inset += left + right
    for option in ("padx", "borderwidth", "highlightthickness"):
        if option in options:
            value = widget.cget(option)
            if str(value):
                inset += 2 * widget.winfo_pixels(value)
    return inset


class _LabelWrap:
    def __init__(self, label: tk.Label | ttk.Label, source: tk.Variable | str, display: tk.StringVar, mode: str, responsive: bool):
        self.label, self.source, self.display, self.mode = label, source, display, mode
        self.responsive = responsive
        self.pending: str | None = None
        self.closed = False
        self.signature: tuple | None = None
        self.font_spec: tuple | None = None
        self.font: tkfont.Font | None = None
        self.source_trace = source.trace_add("write", self.schedule) if isinstance(source, tk.Variable) else None
        label.bind("<Configure>", self.schedule, add="+")
        label.bind("<Destroy>", self.close, add="+")
        self.parent_binding = label.master.bind("<Configure>", self.schedule, add="+")
        self.schedule()

    def schedule(self, *_args: object) -> None:
        if not self.closed and self.pending is None:
            self.pending = self.label.after_idle(self.render)

    def render(self) -> None:
        self.pending = None
        if self.closed:
            return
        label = self.label
        text = str(self.source.get()) if isinstance(self.source, tk.Variable) else self.source
        limit = label.winfo_pixels(label.cget("wraplength"))
        # Use the containing frame, not the label's natural text width. The
        # latter shrinks after balancing and would cause repeated rewrapping.
        parent_width = label.master.winfo_width()
        if self.responsive and parent_width > 1 and limit > 0:
            limit = min(limit, max(1, parent_width - _horizontal_inset(label.master) - _horizontal_inset(label)))
        spec = label.cget("font")
        if not spec:
            spec = ttk.Style(label).lookup(label.cget("style") or "TLabel", "font")
        scaling = str(label.tk.call("tk", "scaling"))
        actual = tuple(label.tk.splitlist(label.tk.call("font", "actual", spec)))
        font_spec = (str(spec), actual, scaling)
        signature = (text, limit, font_spec)
        if signature == self.signature:
            return
        if self.font is None or font_spec != self.font_spec:
            self.font = tkfont.Font(root=label, font=spec)
            self.font_spec = font_spec
        self.signature = signature
        rendered = wrap_korean_text(text, limit, self.font.measure, mode=self.mode)
        if rendered != self.display.get():
            self.display.set(rendered)

    def close(self, event: tk.Event) -> None:
        if event.widget is not self.label or self.closed:
            return
        self.closed = True
        if self.source_trace is not None:
            self.source.trace_remove("write", self.source_trace)
        try:
            if self.pending is not None:
                self.label.after_cancel(self.pending)
            self.label.master.unbind("<Configure>", self.parent_binding)
        except tk.TclError:
            # The containing frame can already be destroyed during app close.
            pass
        self.pending = None


def korean_label(parent: tk.Misc, *, native: bool = False, mode: str = "pretty", responsive: bool = True, **options) -> tk.Label | ttk.Label:
    """Create an ordinary Tk/ttk label with a private display variable."""
    if mode not in {"normal", "pretty", "balance", "nowrap"}:
        raise ValueError(f"Unknown wrapping mode: {mode}")
    if mode == "nowrap":
        options["wraplength"] = 0
    source = options.pop("textvariable", None)
    text = options.pop("text", "")
    if source is None:
        source = str(text)
    elif not isinstance(source, tk.Variable):
        raise TypeError("Korean labels require a Tk Variable object")
    display = tk.StringVar(master=parent, value=source.get() if isinstance(source, tk.Variable) else source)
    factory = tk.Label if native else ttk.Label
    label = factory(parent, textvariable=display, **options)
    label._korean_wrap = _LabelWrap(label, source, display, mode, responsive)
    return label
