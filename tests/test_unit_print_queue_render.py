from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from unit_print_manager import PrintManagerApp
from unit_print_keyboard import KeyboardState
from unit_print_queue_render import (
    queue_card_intersects_viewport,
    queue_content_height,
    visible_queue_indices,
)


class Canvas:
    def __init__(self, *, top=0.0, height=280, width=900):
        self.top = top
        self.height = height
        self.width = width
        self.scrollregion = None
        self.scroll_requests = []

    def delete(self, _tag):
        pass

    def winfo_width(self):
        return self.width

    def winfo_height(self):
        return self.height

    def canvasy(self, y):
        return self.top + y

    def configure(self, *, scrollregion):
        self.scrollregion = scrollregion

    def focus_set(self):
        pass

    def update_idletasks(self):
        pass

    def bbox(self, _tag):
        raise AssertionError("Virtualized item bounds cannot represent the full queue")

    def yview_moveto(self, fraction):
        self.scroll_requests.append(fraction)
        full_height = self.scrollregion[3]
        self.top = min(max(0, full_height - self.height), max(0, full_height * fraction))


class IdleRoot:
    def __init__(self):
        self.callbacks = {}
        self.next_id = 0

    def after_idle(self, callback):
        self.next_id += 1
        task_id = str(self.next_id)
        self.callbacks[task_id] = callback
        return task_id

    def after_cancel(self, task_id):
        self.callbacks.pop(task_id, None)

    def flush(self):
        callbacks = tuple(self.callbacks.values())
        self.callbacks.clear()
        for callback in callbacks:
            callback()


def make_app(count=500, *, top=0, height=280):
    app = PrintManagerApp.__new__(PrintManagerApp)
    app.root = IdleRoot()
    app.queue_canvas = Canvas(top=top, height=height)
    app.queue_scrollbar = Mock()
    app.blocks = [SimpleNamespace(number=index) for index in range(count)]
    app.colors = {}
    app.is_closing = False
    app.queue_card_height = 62
    app.queue_card_gap = 8
    app.queue_padding = 8
    app.drag_active = False
    app.drag_start_index = None
    app.drag_selected_indices = ()
    app.drag_drop_index = None
    app.drag_lift_progress = 0.0
    app.drop_animation_positions = {}
    app.drop_animation_progress = 0.0
    app.keyboard_state = KeyboardState()
    app.selected_block_index_value = 0 if count else None
    app.set_active_workspace = Mock()
    app.draw_queue_card = Mock()
    app.draw_queue_insert_line = Mock()
    return app


def rendered_indices(app):
    return [call.args[0] for call in app.draw_queue_card.call_args_list]


class QueueGeometryTests(unittest.TestCase):
    def test_full_model_extent_is_independent_of_viewport(self):
        self.assertEqual(35008, queue_content_height(500, card_height=62, gap=8, padding=8))
        self.assertEqual(78, queue_content_height(1, card_height=62, gap=8, padding=8))
        self.assertEqual(16, queue_content_height(0, card_height=62, gap=8, padding=8))

    def test_ordinary_visible_range_matches_actual_bounds(self):
        for count in (0, 1, 5, 1000):
            for top in (-80, 0, 70, 71, 73, 735, 69980, 70080):
                for height in (0, 30, 280, 1000):
                    with self.subTest(count=count, top=top, height=height):
                        expected = tuple(
                            index for index in range(count)
                            if queue_card_intersects_viewport(8 + 70 * index, 62, top, top + height)
                        )
                        actual = tuple(visible_queue_indices(
                            count, top, top + height,
                            card_height=62, gap=8, padding=8,
                        ))
                        self.assertEqual(expected, actual)

    def test_gap_only_viewport_does_not_render_neighbors(self):
        self.assertEqual((), tuple(visible_queue_indices(
            10, 73, 75, card_height=62, gap=8, padding=8,
        )))

    def test_lifted_shadow_can_be_visible_without_card_body(self):
        self.assertFalse(queue_card_intersects_viewport(-68, 62, 0, 280))
        self.assertTrue(queue_card_intersects_viewport(
            -68, 62, 0, 280, lifted=True, lift_progress=1,
        ))

    def test_invalid_geometry_is_empty(self):
        for count, top, bottom, height, gap in (
            (0, 0, 280, 62, 8), (1, 280, 0, 62, 8),
            (1, 0, 280, 0, 8), (1, 0, 280, 62, -62),
        ):
            self.assertEqual((), tuple(visible_queue_indices(
                count, top, bottom, card_height=height, gap=gap, padding=8,
            )))


class QueueRenderTests(unittest.TestCase):
    def test_500_row_queue_renders_only_visible_rows(self):
        app = make_app()
        app.draw_queue()
        self.assertEqual([0, 1, 2, 3], rendered_indices(app))
        self.assertEqual((0, 0, 900, 35008), app.queue_canvas.scrollregion)
        self.assertEqual(500, len(app.blocks))

    def test_scroll_callback_repaints_new_rows_and_updates_scrollbar(self):
        app = make_app()
        app.draw_queue()
        app.draw_queue_card.reset_mock()
        app.queue_canvas.top = 735
        app.on_queue_canvas_scroll("0.02099", "0.02899")
        app.root.flush()
        self.assertEqual([10, 11, 12, 13, 14], rendered_indices(app))
        app.queue_scrollbar.set.assert_called_once_with("0.02099", "0.02899")

    def test_keyboard_focus_scroll_uses_entire_queue_height(self):
        for index in (4, 8, 499):
            with self.subTest(index=index):
                app = make_app()
                app.draw_queue()
                app.selected_block_index_value = index
                self.assertEqual("break", app.focus_queue_canvas())
                expected_top = app.queue_card_top(index) + app.queue_card_height - 280
                self.assertAlmostEqual(expected_top / 35008, app.queue_canvas.scroll_requests[-1])
                self.assertAlmostEqual(expected_top, app.queue_canvas.top)
                self.assertEqual(index, app.keyboard_state.queue_cursor)
                self.assertLessEqual(app.queue_card_top(index) + 62, app.queue_canvas.top + 280)

    def test_repeated_keyboard_down_scrolls_to_each_row_without_jumping_to_end(self):
        app = make_app()
        app.draw_queue()
        for index in range(4, 26):
            app.selected_block_index_value = index
            app.focus_queue_canvas()
            app.draw_queue()
            self.assertAlmostEqual(app.queue_card_top(index) + 62 - 280, app.queue_canvas.top)
        self.assertEqual(1540, app.queue_canvas.top)
        self.assertLess(app.queue_canvas.top, 35008 - 280)

    def test_keyboard_home_can_scroll_back_after_virtualized_end(self):
        app = make_app(top=34720)
        app.draw_queue()
        app.selected_block_index_value = 0
        app.focus_queue_canvas()
        self.assertAlmostEqual(8 / 35008, app.queue_canvas.scroll_requests[-1])
        self.assertAlmostEqual(8, app.queue_canvas.top)

    def test_resize_and_scroll_notifications_coalesce(self):
        app = make_app()
        app.on_queue_canvas_configure(None)
        app.on_queue_canvas_configure(None)
        app.on_queue_canvas_scroll("0", "0.008")
        app.on_queue_canvas_scroll("0", "0.008")
        self.assertEqual(1, len(app.root.callbacks))
        app.root.flush()
        self.assertEqual([0, 1, 2, 3], rendered_indices(app))
        app.on_queue_canvas_scroll("0", "0.008")
        self.assertFalse(app.root.callbacks)

    def test_explicit_redraw_cancels_pending_resize_redraw(self):
        app = make_app()
        app.request_queue_redraw()
        app.draw_queue()
        self.assertFalse(app.root.callbacks)
        app.root.flush()
        self.assertEqual(4, app.draw_queue_card.call_count)

    def test_animation_uses_actual_coordinates_instead_of_final_row(self):
        app = make_app()
        app.drop_animation_positions = {
            id(app.blocks[0]): (7008, 7008),
            id(app.blocks[100]): (20, 20),
        }
        app.draw_queue()
        self.assertEqual([1, 2, 3, 100], rendered_indices(app))

    def test_drag_preserves_visible_shadow_and_culls_offscreen_group_rows(self):
        app = make_app()
        app.drag_active = True
        app.drag_start_index = 0
        app.drag_selected_indices = (0, 499)
        app.drag_lift_progress = 1.0
        app.drag_drop_index = 500
        app.queue_drag_group_top = Mock(return_value=-68)
        app.draw_queue()
        lifted = [call for call in app.draw_queue_card.call_args_list if call.kwargs.get("lifted")]
        self.assertEqual([0, 499], [call.args[0] for call in lifted])
        self.assertEqual([-68, 2], [call.kwargs["y0"] for call in lifted])
        self.assertLessEqual(app.draw_queue_card.call_count, 7)
        app.draw_queue_insert_line.assert_not_called()

    def test_drag_culls_lifted_rows_below_viewport(self):
        app = make_app()
        app.drag_active = True
        app.drag_start_index = 0
        app.drag_selected_indices = (0, 1, 2, 3, 4)
        app.drag_drop_index = 500
        app.queue_drag_group_top = Mock(return_value=200)
        app.draw_queue()
        lifted = [call.args[0] for call in app.draw_queue_card.call_args_list if call.kwargs.get("lifted")]
        self.assertEqual([0, 1], lifted)

    def test_closing_does_not_schedule_redraw(self):
        app = make_app()
        app.is_closing = True
        app.request_queue_redraw()
        self.assertFalse(app.root.callbacks)


if __name__ == "__main__":
    unittest.main()
