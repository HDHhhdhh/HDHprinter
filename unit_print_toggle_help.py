from __future__ import annotations

import tkinter as tk
from unit_print_typography import korean_label


class ToggleHoverCard:
    """Owns the mutable lifecycle for one small Tkinter hover card."""

    def __init__(
        self,
        widget: tk.Widget,
        title: str,
        body: str,
        *,
        background: str,
        border: str,
        title_color: str,
        body_color: str,
    ) -> None:
        self.widget = widget
        self.title = title
        self.body = body
        self.background = background
        self.border = border
        self.title_color = title_color
        self.body_color = body_color
        self.window: tk.Toplevel | None = None

        widget.bind("<Enter>", self.show, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def show(self, event: tk.Event) -> None:
        self.hide()
        window = tk.Toplevel(self.widget)
        window.wm_overrideredirect(True)
        window.configure(bg=self.border)
        window.attributes("-topmost", True)

        frame = tk.Frame(window, bg=self.background, padx=12, pady=9)
        frame.pack(padx=1, pady=1)
        korean_label(
            frame,
            native=True,
            responsive=False,
            mode="balance",
            text=self.title,
            bg=self.background,
            fg=self.title_color,
            font=("Malgun Gothic", 9, "bold"),
            anchor="w",
            justify="left",
            wraplength=260,
        ).pack(fill="x")
        korean_label(
            frame,
            native=True,
            responsive=False,
            text=self.body,
            bg=self.background,
            fg=self.body_color,
            font=("Malgun Gothic", 9),
            justify="left",
            wraplength=260,
            anchor="w",
        ).pack(fill="x", pady=(4, 0))

        x = event.x_root + 14
        y = event.y_root + 12
        window.wm_geometry(f"+{x}+{y}")
        self.window = window

    def hide(self, _event: tk.Event | None = None) -> None:
        if self.window is None:
            return
        try:
            self.window.destroy()
        except tk.TclError:
            self.window = None
            return
        self.window = None
