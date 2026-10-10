from __future__ import annotations

import itertools
import unittest

from unit_print_theme import QUEUE_CARD_RADIUS, WORKBENCH_COLORS, apply_workbench_styles


class StyleRecorder:
    """Resolve ttk's first-match maps without creating a Tk window."""

    def __init__(self) -> None:
        self.options: dict[str, dict] = {}
        self.maps: dict[str, dict] = {}

    def configure(self, name: str, **options) -> None:
        self.options.setdefault(name, {}).update(options)

    def map(self, name: str, **options) -> None:
        self.maps.setdefault(name, {}).update(options)

    def resolve(self, name: str, option: str, states=()) -> str:
        active = set(states)
        for *conditions, value in self.maps.get(name, {}).get(option, ()):
            if all((condition[1:] not in active) if condition.startswith("!") else (condition in active) for condition in conditions):
                return value
        return self.options[name][option]


def contrast(first: str, second: str) -> float:
    def luminance(color: str) -> float:
        values = [int(color[offset:offset + 2], 16) / 255 for offset in (1, 3, 5)]
        linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in values]
        return sum(value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


class WorkbenchThemeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.style = StyleRecorder()
        apply_workbench_styles(self.style, "Malgun Gothic")

    def test_disabled_buttons_override_hover_press_and_focus(self) -> None:
        colors = WORKBENCH_COLORS
        for name in self.style.options:
            if not name.endswith("TButton"):
                continue
            with self.subTest(style=name):
                states = ("disabled", "pressed", "active", "focus")
                self.assertEqual(colors["disabled_bg"], self.style.resolve(name, "background", states))
                self.assertEqual(colors["disabled_fg"], self.style.resolve(name, "foreground", states))
                self.assertEqual(colors["border"], self.style.resolve(name, "bordercolor", states))

    def test_pressed_buttons_override_active_and_focus_stays_visible(self) -> None:
        for name in self.style.options:
            if not name.endswith("TButton"):
                continue
            with self.subTest(style=name):
                self.assertEqual(
                    self.style.resolve(name, "background", ("pressed",)),
                    self.style.resolve(name, "background", ("pressed", "active")),
                )
                self.assertEqual(2, self.style.options[name]["focusthickness"])
                self.assertEqual(WORKBENCH_COLORS["focus"], self.style.resolve(name, "bordercolor", ("focus", "active")))
                self.assertGreaterEqual(
                    contrast(self.style.resolve(name, "focuscolor", ("focus",)), self.style.resolve(name, "background", ("focus",))),
                    3.0,
                )

    def test_processing_button_remains_distinct_when_disabled(self) -> None:
        for name in ("Primary.TButton", "Accent.TButton"):
            with self.subTest(style=name):
                states = ("disabled", "alternate", "pressed", "active")
                self.assertEqual(WORKBENCH_COLORS["success_soft"], self.style.resolve(name, "background", states))
                self.assertEqual(WORKBENCH_COLORS["success"], self.style.resolve(name, "foreground", states))
                self.assertNotEqual(self.style.resolve(name, "background", ("disabled",)), self.style.resolve(name, "background", states))

    def test_readonly_inputs_stay_readable_and_disabled_wins(self) -> None:
        for name in ("TEntry", "TCombobox", "TSpinbox", "StudentNameOn.TEntry"):
            with self.subTest(style=name):
                self.assertEqual((8, 6), self.style.options[name]["padding"])
                self.assertEqual(("Malgun Gothic", 11), self.style.options[name]["font"])
                self.assertEqual(WORKBENCH_COLORS["text"], self.style.resolve(name, "foreground", ("readonly", "focus")))
                self.assertEqual(WORKBENCH_COLORS["focus"], self.style.resolve(name, "bordercolor", ("readonly", "focus")))
                states = ("disabled", "readonly", "focus")
                self.assertEqual(WORKBENCH_COLORS["disabled_fg"], self.style.resolve(name, "foreground", states))
                self.assertEqual(WORKBENCH_COLORS["disabled_bg"], self.style.resolve(name, "fieldbackground", states))
                self.assertEqual(WORKBENCH_COLORS["border"], self.style.resolve(name, "bordercolor", states))

    def test_required_name_uses_brand_and_inactive_name_uses_disabled_colors(self) -> None:
        self.assertEqual(WORKBENCH_COLORS["accent"], self.style.options["StudentNameOn.TEntry"]["bordercolor"])
        self.assertEqual(2, self.style.options["StudentNameOn.TEntry"]["borderwidth"])
        self.assertEqual(WORKBENCH_COLORS["disabled_fg"], self.style.resolve("StudentNameOff.TEntry", "foreground", ("disabled",)))
        self.assertEqual(WORKBENCH_COLORS["disabled_bg"], self.style.options["StudentNameOff.TEntry"]["fieldbackground"])

    def test_hidden_combobox_cannot_reveal_text_arrow_or_focus(self) -> None:
        name = "Blank.TCombobox"
        for option in ("foreground", "arrowcolor", "fieldbackground", "bordercolor", "focuscolor"):
            for states in ((), ("disabled",), ("readonly", "focus"), ("disabled", "readonly", "focus", "active")):
                with self.subTest(option=option, states=states):
                    self.assertEqual(WORKBENCH_COLORS["panel"], self.style.resolve(name, option, states))

    def test_selected_toggle_keeps_disabled_and_pressed_priority(self) -> None:
        for name in ("Toolbar.Toggle.TCheckbutton", "Panel.Toggle.TCheckbutton"):
            with self.subTest(style=name):
                states = ("disabled", "selected", "active", "pressed", "focus")
                self.assertEqual(WORKBENCH_COLORS["accent_soft"], self.style.resolve(name, "background", states))
                self.assertEqual(WORKBENCH_COLORS["accent"], self.style.resolve(name, "foreground", states))
                self.assertEqual(WORKBENCH_COLORS["accent_pressed"], self.style.resolve(name, "background", ("selected", "active", "pressed")))

    def test_notebook_dimensions_are_not_overridden(self) -> None:
        for name, options in self.style.options.items():
            if "TNotebook" in name:
                with self.subTest(style=name):
                    self.assertFalse(set(options) & {"padding", "width", "font", "tabmargins", "borderwidth"})

    def test_workspace_focus_does_not_change_border_geometry(self) -> None:
        for workspace in ("BuilderWorkspace", "QueueWorkspace"):
            self.assertEqual(
                self.style.options[f"{workspace}.TFrame"]["borderwidth"],
                self.style.options[f"Focus{workspace}.TFrame"]["borderwidth"],
            )

    def test_all_visible_style_text_states_meet_normal_text_contrast(self) -> None:
        # Include all simultaneous states to catch a readable color being
        # shadowed by a different background map entry.
        states = ("disabled", "alternate", "pressed", "active", "focus", "selected", "readonly")
        for name, options in self.style.options.items():
            if "foreground" not in options or name == "Blank.TCombobox":
                continue
            background_option = "fieldbackground" if "fieldbackground" in options else "background"
            for count in range(len(states) + 1):
                for active in itertools.combinations(states, count):
                    with self.subTest(style=name, states=active):
                        self.assertGreaterEqual(contrast(self.style.resolve(name, "foreground", active), self.style.resolve(name, background_option, active)), 4.5)

    def test_canvas_status_and_log_tokens_meet_normal_text_contrast(self) -> None:
        for foreground, background in (
            ("muted_soft", "panel"), ("muted_soft", "app_bg"),
            ("muted_soft", "panel_soft"), ("muted", "app_bg"),
            ("accent", "accent_soft"), ("success", "success_soft"),
            ("warning", "warning_soft"), ("danger", "danger_soft"),
            ("complete", "complete_soft"),
            ("disabled_fg", "disabled_bg"), ("log_text", "log_bg"),
        ):
            with self.subTest(foreground=foreground, background=background):
                self.assertGreaterEqual(contrast(WORKBENCH_COLORS[foreground], WORKBENCH_COLORS[background]), 4.5)
        self.assertEqual(6, QUEUE_CARD_RADIUS)

    def test_completion_uses_its_blue_semantic_role(self) -> None:
        c = WORKBENCH_COLORS
        self.assertEqual(c["complete"], self.style.options["Completion.TButton"]["background"])


if __name__ == "__main__":
    unittest.main()
