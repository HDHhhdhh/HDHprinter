import unittest
from dataclasses import FrozenInstanceError

from unit_print_keyboard import (
    QueueSelection,
    queue_click_selection,
    queue_navigation_selection,
    queue_navigation_target,
)


class QueueSelectionTests(unittest.TestCase):
    def test_selection_state_is_immutable(self) -> None:
        state = QueueSelection(frozenset({1}), 1, 1)
        with self.assertRaises(FrozenInstanceError):
            state.focus_index = 2

    def test_plain_click_selects_only_clicked_row_and_starts_new_anchor(self) -> None:
        state = queue_click_selection(8, {1, 2, 5}, 5, 1, 3)
        self.assertEqual(state, QueueSelection(frozenset({3}), 3, 3))

    def test_control_click_adds_and_removes_without_moving_other_selection(self) -> None:
        state = queue_click_selection(8, {1, 5}, 5, 1, 3, control=True)
        self.assertEqual(state, QueueSelection(frozenset({1, 3, 5}), 3, 3))

        state = queue_click_selection(8, state.selected_indices, state.focus_index, state.anchor_index, 3, control=True)
        self.assertEqual(state, QueueSelection(frozenset({1, 5}), 3, 3))

    def test_control_click_can_clear_last_selection_and_retain_cursor(self) -> None:
        state = queue_click_selection(4, {2}, 2, 2, 2, control=True)
        self.assertEqual(state, QueueSelection(frozenset(), 2, 2))

    def test_shift_click_extends_shrinks_and_crosses_stable_anchor(self) -> None:
        state = QueueSelection(frozenset({2}), 2, 2)
        for target, expected in ((6, {2, 3, 4, 5, 6}), (4, {2, 3, 4}), (0, {0, 1, 2})):
            with self.subTest(target=target):
                state = queue_click_selection(8, state.selected_indices, state.focus_index, state.anchor_index, target, shift=True)
                self.assertEqual(state, QueueSelection(frozenset(expected), target, 2))

    def test_control_shift_click_unions_range_with_disjoint_selection(self) -> None:
        state = queue_click_selection(8, {0, 7}, 3, 3, 5, control=True, shift=True)
        self.assertEqual(state, QueueSelection(frozenset({0, 3, 4, 5, 7}), 5, 3))

    def test_shift_without_anchor_uses_cursor_or_clicked_row(self) -> None:
        self.assertEqual(
            queue_click_selection(6, {3}, 3, None, 1, shift=True),
            QueueSelection(frozenset({1, 2, 3}), 1, 3),
        )
        self.assertEqual(
            queue_click_selection(6, set(), None, None, 4, shift=True),
            QueueSelection(frozenset({4}), 4, 4),
        )

    def test_plain_navigation_replaces_selection_and_anchor(self) -> None:
        state = queue_navigation_selection(8, {1, 3, 5}, 3, 1, 4)
        self.assertEqual(state, QueueSelection(frozenset({4}), 4, 4))

    def test_control_navigation_preserves_selection_and_range_anchor(self) -> None:
        state = queue_navigation_selection(8, {1, 5}, 1, 1, 3, control=True)
        self.assertEqual(state, QueueSelection(frozenset({1, 5}), 3, 1))

        state = queue_navigation_selection(8, state.selected_indices, state.focus_index, state.anchor_index, 4, shift=True)
        self.assertEqual(state, QueueSelection(frozenset({1, 2, 3, 4}), 4, 1))

    def test_control_navigation_can_move_empty_selection_without_creating_anchor(self) -> None:
        state = queue_navigation_selection(6, set(), 1, None, 2, control=True)
        self.assertEqual(state, QueueSelection(frozenset(), 2, None))

    def test_shift_navigation_keeps_anchor_when_direction_changes(self) -> None:
        state = queue_navigation_selection(8, {3, 4}, 4, 3, 1, shift=True)
        self.assertEqual(state, QueueSelection(frozenset({1, 2, 3}), 1, 3))

    def test_control_shift_navigation_unions_range(self) -> None:
        state = queue_navigation_selection(8, {0, 7}, 3, 3, 5, control=True, shift=True)
        self.assertEqual(state, QueueSelection(frozenset({0, 3, 4, 5, 7}), 5, 3))

    def test_invalid_saved_indices_are_filtered_before_action(self) -> None:
        for operation in (queue_click_selection, queue_navigation_selection):
            with self.subTest(operation=operation.__name__):
                state = operation(5, {-1, 0, 2, 5}, 7, -1, 3, control=True)
                expected_anchor = 3 if operation is queue_click_selection else None
                self.assertEqual(state, QueueSelection(frozenset({0, 2, 3}) if operation is queue_click_selection else frozenset({0, 2}), 3, expected_anchor))

    def test_invalid_targets_leave_cleaned_state(self) -> None:
        for operation in (queue_click_selection, queue_navigation_selection):
            for target in (-1, 5):
                with self.subTest(operation=operation.__name__, target=target):
                    state = operation(5, {-1, 0, 2, 5}, 7, -1, target, control=True, shift=True)
                    self.assertEqual(state, QueueSelection(frozenset({0, 2}), None, None))

    def test_empty_queue_has_no_selection_cursor_or_anchor(self) -> None:
        for operation in (queue_click_selection, queue_navigation_selection):
            for count in (-1, 0):
                with self.subTest(operation=operation.__name__, count=count):
                    state = operation(count, {0, 2}, 2, 0, 0, control=True, shift=True)
                    self.assertEqual(state, QueueSelection())


class QueueNavigationTargetTests(unittest.TestCase):
    def test_navigation_keys_and_keypad_equivalents(self) -> None:
        expected = {"Up": 3, "Down": 5, "Home": 0, "End": 9, "Prior": 1, "Next": 7}
        for key, target in expected.items():
            for keysym in (key, f"KP_{key}"):
                with self.subTest(keysym=keysym):
                    self.assertEqual(queue_navigation_target(10, 4, keysym, page_size=3), target)

    def test_page_aliases(self) -> None:
        for key in ("PageUp", "KP_Page_Up"):
            self.assertEqual(queue_navigation_target(10, 4, key, 3), 1)
        for key in ("PageDown", "KP_Page_Down"):
            self.assertEqual(queue_navigation_target(10, 4, key, 3), 7)

    def test_arrows_and_page_movement_clamp_at_both_ends(self) -> None:
        for key in ("Up", "Prior"):
            self.assertEqual(queue_navigation_target(5, 0, key, 20), 0)
        for key in ("Down", "Next"):
            self.assertEqual(queue_navigation_target(5, 4, key, 20), 4)
        self.assertEqual(queue_navigation_target(5, 2, "Prior", 20), 0)
        self.assertEqual(queue_navigation_target(5, 2, "Next", 20), 4)

    def test_invalid_cursor_starts_at_first_row_except_end(self) -> None:
        for current in (None, -1, 5):
            for key in ("Up", "Down", "Home", "Prior", "Next"):
                with self.subTest(current=current, key=key):
                    self.assertEqual(queue_navigation_target(5, current, key, 3), 0)
            self.assertEqual(queue_navigation_target(5, current, "End"), 4)

    def test_page_size_is_at_least_one_and_defaults_to_one(self) -> None:
        self.assertEqual(queue_navigation_target(5, 2, "Prior"), 1)
        for page_size in (0, -3):
            self.assertEqual(queue_navigation_target(5, 2, "Next", page_size), 3)

    def test_empty_queue_and_unhandled_key_have_no_target(self) -> None:
        self.assertIsNone(queue_navigation_target(0, 0, "Down"))
        self.assertIsNone(queue_navigation_target(-1, 0, "End"))
        self.assertIsNone(queue_navigation_target(5, 2, "Return"))


if __name__ == "__main__":
    unittest.main()
