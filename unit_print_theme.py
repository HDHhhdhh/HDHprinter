"""Visual tokens and ttk styles for the quick-printer workbench.

Apply after the manager's existing style setup.  This module does not create
widgets or change their states, layouts, commands, or print behavior.
"""

from __future__ import annotations

from typing import Any, Protocol


WORKBENCH_COLORS = {
    "app_bg": "#f5f6f8",
    "panel": "#ffffff",
    "panel_soft": "#f1f3f5",
    "border": "#d0d5dc",
    "border_soft": "#e1e5ea",
    "text": "#1f2937",
    "queue_title": "#000000",
    "muted": "#526071",
    "muted_soft": "#626e7f",
    "accent": "#245ec7",
    "accent_hover": "#1d4fa8",
    "accent_pressed": "#183f87",
    "accent_soft": "#eaf1fc",
    "link": "#245ec7",
    "danger": "#b42318",
    "danger_soft": "#fff1ef",
    "danger_hover": "#ffe4df",
    "danger_pressed": "#ffd6cf",
    "success": "#166534",
    "success_soft": "#edf7ee",
    "warning": "#9a4e13",
    "warning_soft": "#fff1e5",
    # Completion has its own semantic role; it remains blue when branding changes.
    "complete": "#245ec7",
    "complete_soft": "#eaf1fc",
    "complete_hover": "#1d4fa8",
    "complete_pressed": "#183f87",
    "log_bg": "#1f2937",
    "log_text": "#f3f6fa",
    "disabled_bg": "#e8ebef",
    "disabled_fg": "#586474",
    "hover": "#e8edf5",
    "pressed": "#dce5f2",
    "focus": "#245ec7",
    "on_accent": "#ffffff",
    "shadow": "#cbd5e1",
}

QUEUE_CARD_RADIUS = 6


class _Style(Protocol):
    def configure(self, style: str, **options: Any) -> Any: ...

    def map(self, style: str, **options: Any) -> Any: ...


def apply_workbench_styles(style: _Style, family: str) -> None:
    """Apply the palette without replacing ttk elements or widget layouts."""
    c = WORKBENCH_COLORS
    style.configure(".", font=(family, 11), background=c["app_bg"], foreground=c["text"])

    for name in ("TFrame", "Shell.TFrame"):
        style.configure(name, background=c["app_bg"])
    for name, background, border in (
        ("Panel.TFrame", "panel", "border"),
        ("Card.TFrame", "panel", "border"),
        ("Footer.TFrame", "panel", "border"),
        ("Surface.TFrame", "panel", "border"),
        ("Sidebar.TFrame", "panel", "border"),
        ("BuilderWorkspace.TFrame", "panel", "border"),
        ("QueueWorkspace.TFrame", "panel", "border"),
        ("Soft.TFrame", "panel_soft", "border_soft"),
        ("Inset.TFrame", "panel_soft", "border_soft"),
    ):
        style.configure(name, background=c[background], bordercolor=c[border], borderwidth=1, relief="solid")
    # Focus changes only the color, never the inner workspace geometry.
    for name in ("BuilderWorkspace.TFrame", "QueueWorkspace.TFrame"):
        style.configure(name, borderwidth=2)
    for name in ("FocusBuilderWorkspace.TFrame", "FocusQueueWorkspace.TFrame"):
        style.configure(name, background=c["panel"], bordercolor=c["focus"], borderwidth=2, relief="solid")
    style.configure("Toolbar.TFrame", background=c["panel_soft"])
    style.configure("StatusBar.TFrame", background=c["panel"])
    # Sizes follow one body, one section, and one page-title hierarchy.
    for name, background, foreground, size, weight in (
        ("TLabel", "panel", "text", 11, "normal"),
        ("Header.TLabel", "panel", "text", 18, "bold"),
        ("PageTitle.TLabel", "panel", "text", 18, "bold"),
        ("PanelTitle.TLabel", "panel", "text", 13, "bold"),
        ("CardTitle.TLabel", "panel", "text", 13, "bold"),
        ("SectionTitle.TLabel", "panel", "text", 13, "bold"),
        ("Kicker.TLabel", "panel", "accent", 10, "bold"),
        ("Subtle.TLabel", "panel", "muted", 11, "normal"),
        ("ShellSubtle.TLabel", "app_bg", "muted", 11, "normal"),
        ("CardText.TLabel", "panel", "muted", 11, "normal"),
        ("ToolbarText.TLabel", "panel_soft", "muted", 10, "normal"),
        ("StatusChip.TLabel", "complete_soft", "complete", 11, "bold"),
        ("WarningChip.TLabel", "danger_soft", "danger", 10, "bold"),
        ("StatusBar.TLabel", "panel", "muted", 11, "normal"),
        ("Status.TLabel", "panel", "muted", 11, "normal"),
        ("Field.TLabel", "panel", "text", 11, "bold"),
        ("FieldSoft.TLabel", "panel_soft", "text", 11, "bold"),
        ("SectionHint.TLabel", "panel", "muted", 10, "normal"),
        ("InsetTitle.TLabel", "panel_soft", "text", 11, "bold"),
        ("InsetText.TLabel", "panel_soft", "muted", 10, "normal"),
        ("ShortcutHelpTitle.TLabel", "panel_soft", "text", 11, "bold"),
        ("ShortcutHelpContext.TLabel", "panel_soft", "accent", 10, "bold"),
        ("ShortcutHelpText.TLabel", "panel_soft", "muted", 11, "normal"),
        ("QueueMeta.TLabel", "panel", "muted", 10, "normal"),
        ("FooterText.TLabel", "app_bg", "muted", 10, "normal"),
    ):
        style.configure(name, background=c[background], foreground=c[foreground], font=(family, size, weight))
        style.map(name, foreground=[("disabled", c["disabled_fg"])])

    for name, background, foreground, hover, pressed, padding, size, weight in (
        ("TButton", "panel_soft", "text", "hover", "pressed", (10, 6), 11, "normal"),
        ("Compact.TButton", "panel", "text", "hover", "pressed", (6, 3), 10, "bold"),
        ("Ghost.TButton", "panel", "text", "hover", "pressed", (8, 4), 11, "normal"),
        ("Primary.TButton", "accent", "on_accent", "accent_hover", "accent_pressed", (14, 7), 11, "bold"),
        ("Accent.TButton", "accent", "on_accent", "accent_hover", "accent_pressed", (14, 7), 11, "bold"),
        ("Completion.TButton", "complete", "on_accent", "complete_hover", "complete_pressed", (14, 7), 11, "bold"),
        ("SoftAction.TButton", "panel_soft", "text", "hover", "pressed", (9, 5), 10, "bold"),
        ("DangerAction.TButton", "danger_soft", "danger", "danger_hover", "danger_pressed", (9, 5), 10, "bold"),
        ("Danger.TButton", "danger_soft", "danger", "danger_hover", "danger_pressed", (7, 3), 11, "bold"),
        ("CompactDanger.TButton", "danger_soft", "danger", "danger_hover", "danger_pressed", (6, 3), 10, "bold"),
    ):
        filled = background in {"accent", "complete"}
        border = c[background] if filled else c["border"]
        focus_color = c["on_accent"] if filled else c["focus"]
        style.configure(
            name, background=c[background], foreground=c[foreground], padding=padding,
            font=(family, size, weight), borderwidth=1, bordercolor=border,
            relief="flat", focusthickness=2, focuscolor=focus_color,
        )
        # ttk uses the first matching map entry.  Keep disabled first, and
        # pressed before active.  Alternate is a visual processing state only.
        style.map(
            name,
            background=[
                ("disabled", "alternate", c["success_soft"]),
                ("disabled", c["disabled_bg"]),
                ("alternate", c["success_soft"]),
                ("pressed", c[pressed]),
                ("active", c[hover]),
            ],
            foreground=[
                ("disabled", "alternate", c["success"]),
                ("disabled", c["disabled_fg"]),
                ("alternate", c["success"]),
            ],
            bordercolor=[
                ("disabled", "alternate", c["success"]),
                ("disabled", c["border"]),
                ("focus", c["focus"]),
                ("alternate", c["success"]),
                ("pressed", border),
                ("active", border),
            ],
            focuscolor=[
                ("disabled", c["disabled_fg"]),
                ("alternate", c["focus"]),
                ("focus", focus_color),
            ],
        )

    for name in ("TEntry", "TCombobox", "TSpinbox", "StudentNameOn.TEntry", "StudentNameOff.TEntry"):
        required = name == "StudentNameOn.TEntry"
        inactive = name == "StudentNameOff.TEntry"
        background = c["disabled_bg"] if inactive else c["panel"]
        foreground = c["disabled_fg"] if inactive else c["text"]
        border = c["accent"] if required else c["border"]
        style.configure(
            name, padding=(8, 6), font=(family, 11), background=background,
            fieldbackground=background, foreground=foreground, insertcolor=c["text"],
            selectbackground=c["accent"], selectforeground=c["on_accent"],
            bordercolor=border, lightcolor=border, darkcolor=border,
            borderwidth=2 if required else 1, focusthickness=2,
            focuscolor=c["focus"], arrowcolor=foreground,
        )
        field_map = [("disabled", c["disabled_bg"]), ("readonly", background), ("focus", background)]
        text_map = [("disabled", c["disabled_fg"]), ("readonly", foreground), ("focus", foreground)]
        border_map = [("disabled", c["border"]), ("focus", c["border"] if inactive else c["focus"]), ("readonly", border)]
        style.map(
            name, background=field_map, fieldbackground=field_map, foreground=text_map,
            arrowcolor=text_map, bordercolor=border_map, lightcolor=border_map, darkcolor=border_map,
        )

    # This disabled combobox is an intentional invisible placeholder.  Replace
    # inherited state maps too, so readonly/focus cannot expose its arrow/text.
    hidden_options = {
        key: c["panel"] for key in (
            "background", "fieldbackground", "foreground", "bordercolor",
            "lightcolor", "darkcolor", "arrowcolor", "selectbackground",
            "selectforeground", "focuscolor",
        )
    }
    style.configure("Blank.TCombobox", **hidden_options, font=(family, 11), padding=(8, 6), focusthickness=0)
    hidden_map = [(state, c["panel"]) for state in ("disabled", "readonly", "focus", "active")]
    style.map("Blank.TCombobox", **{key: hidden_map for key in hidden_options})

    for name, background in (
        ("TCheckbutton", "panel"), ("TRadiobutton", "panel"),
        ("Toolbar.TCheckbutton", "panel_soft"),
    ):
        style.configure(name, background=c[background], foreground=c["text"], font=(family, 11), focuscolor=c["focus"], focusthickness=2)
        style.map(
            name, foreground=[("disabled", c["disabled_fg"])],
            indicatorcolor=[("disabled", c["disabled_fg"]), ("selected", c["accent"])],
            bordercolor=[("disabled", c["border"]), ("focus", c["focus"])],
        )
    for name, background in (("Toolbar.Toggle.TCheckbutton", "panel_soft"), ("Panel.Toggle.TCheckbutton", "panel")):
        style.configure(
            name, background=c[background], foreground=c["muted"], padding=(10, 5),
            font=(family, 11, "bold"), borderwidth=1, bordercolor=c["border"],
            relief="flat", focuscolor=c["focus"], focusthickness=2,
        )
        style.map(
            name,
            background=[
                ("disabled", "selected", c["accent_soft"]), ("disabled", c["disabled_bg"]),
                ("pressed", "selected", c["accent_pressed"]), ("pressed", c["pressed"]),
                ("selected", c["accent"]), ("active", c["hover"]),
            ],
            foreground=[
                ("disabled", "selected", c["accent"]), ("disabled", c["disabled_fg"]),
                ("selected", c["on_accent"]),
            ],
            bordercolor=[
                ("disabled", c["border"]), ("focus", c["focus"]), ("selected", c["accent"]),
            ],
        )

    # Notebook metrics belong to the existing UI.  Only colors change here.
    for name in ("TNotebook", "Builder.TNotebook", "Material.TNotebook", "LessonMaterial.TNotebook"):
        style.configure(name, background=c["panel"] if name == "LessonMaterial.TNotebook" else c["app_bg"])
        tab_name = f"{name}.Tab"
        style.configure(tab_name, background=c["panel_soft"], foreground=c["muted"], bordercolor=c["border"], focuscolor=c["focus"])
        style.map(
            tab_name,
            background=[("disabled", c["disabled_bg"]), ("selected", c["accent_soft"]), ("active", c["hover"])],
            foreground=[("disabled", c["disabled_fg"]), ("selected", c["accent"])],
            bordercolor=[("disabled", c["border"]), ("focus", c["focus"]), ("selected", c["accent"])],
            focuscolor=[("disabled", c["border"]), ("focus", c["focus"])],
        )
