"""Queue navigation work and metadata regressions without Tk or live printing."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from unit_print_history_events import block_history_data
from unit_print_keyboard import KeyboardState
from unit_print_manager import ExamPdf, PrintBlock, PrintManagerApp
from unit_print_queue_state import queue_drop_index, queue_hit_index


class Variable:
    def __init__(self, value="ongoing print"):
        self.value = value
        self.calls = 0

    def set(self, value):
        self.value = value
        self.calls += 1


class CountedBlock:
    def __init__(self, copies=1, files=1):
        self.copies = copies
        self.count = files
        self.file_count_reads = 0

    @property
    def file_count(self):
        self.file_count_reads += 1
        return self.count


class LengthOnlyQueue:
    """Coordinate queries must never fetch or iterate its rows."""
    def __len__(self):
        return 1_000_000

    def __getitem__(self, _index):
        raise AssertionError("coordinate hit test accessed a block")

    def __iter__(self):
        raise AssertionError("coordinate hit test scanned the queue")


def make_app(blocks):
    app = PrintManagerApp.__new__(PrintManagerApp)
    app.blocks = blocks
    app.selected_block_indices = {0} if blocks else set()
    app.selected_block_index_value = 0 if blocks else None
    app.queue_selection_anchor = 0 if blocks else None
    app.keyboard_state = KeyboardState()
    app.keyboard_state.enter_queue(cursor=app.selected_block_index_value)
    app.block_status_by_id = {}
    app.block_detail_by_id = {}
    app.capture_queue_history = Mock()
    app.draw_queue = Mock()
    app.populate_queue_editor = Mock()
    app.load_selected_block_to_form = Mock()
    app.queue_status_var = Variable()
    app.queue_padding = 5
    app.queue_card_height = 40
    app.queue_card_gap = 10
    return app


class QueueRefreshTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        for name in ("sumatra_enqueue_pdfs", "shell_print_pdf", "save_config",
                     "load_printers", "get_print_queue_count", "run_powershell"):
            patch("unit_print_manager." + name, side_effect=AssertionError(name)).start()

    def test_repeated_selection_never_captures_history_or_recomputes_file_totals(self):
        blocks = [CountedBlock(copies=3, files=8) for _ in range(1000)]
        app = make_app(blocks)
        for index in range(100):
            app.select_queue_indices({index}, focus_index=index, anchor_index=index)

        app.capture_queue_history.assert_not_called()
        self.assertEqual(0, sum(block.file_count_reads for block in blocks))
        self.assertEqual(0, app.queue_status_var.calls)
        self.assertEqual("ongoing print", app.queue_status_var.value)
        self.assertEqual(100, app.draw_queue.call_count)
        self.assertEqual(100, app.populate_queue_editor.call_count)
        self.assertEqual(99, app.keyboard_state.queue_cursor)

    def test_model_refresh_captures_history_and_counts_each_block_once(self):
        blocks = [CountedBlock(copies=3, files=2), CountedBlock(copies=2, files=4)]
        app = make_app(blocks)
        app.update_queue_view(select_index=1)

        app.capture_queue_history.assert_called_once_with()
        self.assertEqual([1, 1], [block.file_count_reads for block in blocks])
        self.assertEqual("블록 2개 / PDF 6개 / 전송 14세트", app.queue_status_var.value)
        self.assertEqual({1}, app.selected_block_indices)
        self.assertEqual(1, app.queue_selection_anchor)
        app.populate_queue_editor.assert_called_once_with(1)

    def test_cursor_without_selection_and_completed_details_survive_navigation(self):
        first, second = CountedBlock(), CountedBlock()
        app = make_app([first, second])
        app.block_status_by_id = {id(first): "완료", id(second): "예약"}
        app.block_detail_by_id = {id(first): "실제 인쇄 완료"}
        app.select_queue_indices(set(), focus_index=1, anchor_index=0)

        self.assertEqual(set(), app.selected_block_indices)
        self.assertEqual(1, app.selected_block_index_value)
        self.assertEqual(1, app.keyboard_state.queue_cursor)
        self.assertEqual(0, app.queue_selection_anchor)
        self.assertEqual({id(first): "완료", id(second): "예약"}, app.block_status_by_id)
        self.assertEqual({id(first): "실제 인쇄 완료"}, app.block_detail_by_id)
        app.load_selected_block_to_form.assert_not_called()

    def test_selection_can_still_explicitly_load_builder_form(self):
        app = make_app([CountedBlock(), CountedBlock()])
        app.select_queue_indices({0, 1}, focus_index=1, anchor_index=0, load_form=True)
        self.assertEqual({0, 1}, app.selected_block_indices)
        app.load_selected_block_to_form.assert_called_once_with()

    def test_actual_arrow_handler_uses_selection_only_refresh(self):
        blocks = [CountedBlock() for _ in range(20)]
        app = make_app(blocks)
        app.widget_is_in_queue = Mock(return_value=True)
        app.widget_is_in_queue_editor = Mock(return_value=False)
        app.queue_canvas = SimpleNamespace(winfo_height=lambda: 220)
        app.focus_queue_canvas = Mock()
        event = SimpleNamespace(widget=app.queue_canvas, keysym="Next", state=0)
        self.assertEqual("break", app.handle_queue_arrow(event))
        self.assertEqual({4}, app.selected_block_indices)
        app.capture_queue_history.assert_not_called()
        self.assertEqual(0, sum(block.file_count_reads for block in blocks))
        app.focus_queue_canvas.assert_called_once_with()

    def test_empty_model_refresh_clears_all_selection_state(self):
        app = make_app([])
        app.selected_block_indices = {3}
        app.selected_block_index_value = 3
        app.queue_selection_anchor = 3
        app.update_queue_view()
        self.assertEqual(set(), app.selected_block_indices)
        self.assertIsNone(app.selected_block_index_value)
        self.assertIsNone(app.queue_selection_anchor)
        self.assertIsNone(app.keyboard_state.queue_cursor)
        self.assertEqual("블록 0개 / PDF 0개 / 전송 0세트", app.queue_status_var.value)


class QueueCoordinateTests(unittest.TestCase):
    def test_coordinates_match_old_scan_at_edges_gaps_and_midpoints(self):
        for count in (0, 1, 2, 20):
            for padding, height, gap in ((5, 40, 10), (0, 1, 0), (2.5, 9.5, 0.5)):
                kwargs = dict(padding=padding, card_height=height, card_gap=gap)
                stride = height + gap
                ys = {-100.0, 0.0, padding - 0.1, padding, padding + count * stride + 100}
                for index in range(count + 1):
                    top = padding + index * stride
                    for position in (top, top + height, top + height / 2, top + stride):
                        ys.update((position - 0.01, position, position + 0.01))
                for y in ys:
                    expected_hit = next((index for index in range(count)
                                         if padding + index * stride <= y <= padding + index * stride + height), None)
                    expected_drop = next((index for index in range(count)
                                          if y < padding + index * stride + height / 2), count)
                    with self.subTest(count=count, y=y, **kwargs):
                        self.assertEqual(expected_hit, queue_hit_index(count, y, **kwargs))
                        self.assertEqual(expected_drop, queue_drop_index(count, y, **kwargs))

    def test_manager_coordinate_queries_need_only_queue_length(self):
        app = make_app([])
        app.blocks = LengthOnlyQueue()
        app.queue_card_top = Mock(side_effect=AssertionError("old row scan"))
        row = 900_000
        top = app.queue_padding + row * (app.queue_card_height + app.queue_card_gap)
        self.assertEqual(row, app.queue_hit_index_from_y(top + 2))
        self.assertIsNone(app.queue_hit_index_from_y(top + 45))
        self.assertEqual(row, app.queue_drop_index_from_canvas_y(top + 19))
        self.assertEqual(row + 1, app.queue_drop_index_from_canvas_y(top + 20))
        app.queue_card_top.assert_not_called()

    def test_invalid_nonempty_geometry_is_rejected(self):
        for height, gap in ((0, 1), (-1, 2), (1, -1)):
            for operation in (queue_hit_index, queue_drop_index):
                with self.subTest(operation=operation.__name__, height=height, gap=gap):
                    with self.assertRaises(ValueError):
                        operation(3, 20, padding=0, card_height=height, card_gap=gap)


class QueueHistoryMetadataTests(unittest.TestCase):
    def block(self):
        return PrintBlock(
            "공통수학2", "YBM", "핵심문제", 3, 9, 2,
            [ExamPdf(3, "문제", Path("not-opened.pdf"))],
            printer_override="explicit printer", printer_route="세로",
            pages_per_sheet=4, material_kind="queue_bundle", display_label="선택 묶음",
            selected_numbers=(3, 5, 9), name_separator_label="김학생",
        )

    def test_history_snapshot_contains_behavior_fields_without_file_access(self):
        block = self.block()
        with patch.object(Path, "stat", side_effect=AssertionError("history must not stat")), \
             patch.object(Path, "open", side_effect=AssertionError("history must not open")):
            snapshot = block_history_data(block)
        self.assertEqual((3, 9), (snapshot["start"], snapshot["end"]))
        self.assertEqual(4, snapshot["pages_per_sheet"])
        self.assertEqual("explicit printer", snapshot["printer_override"])
        self.assertEqual("queue_bundle", snapshot["material_kind"])
        self.assertEqual("선택 묶음", snapshot["display_label"])
        self.assertEqual([3, 5, 9], snapshot["selected_numbers"])

    def test_capture_detects_same_id_layout_change_as_block_update(self):
        block = self.block()
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.blocks = [block]
        app.print_history = SimpleNamespace(record=Mock(return_value={"saved": True}))
        app._history_queue_snapshot = {block.history_id: block_history_data(block)}
        app.blocks = [replace(block, pages_per_sheet=1, printer_override="second printer")]
        app.log = Mock()
        app.capture_queue_history()

        app.print_history.record.assert_called_once()
        args, kwargs = app.print_history.record.call_args
        self.assertEqual(("block_updated",), args)
        self.assertEqual(block.history_id, kwargs["block"]["block_id"])
        self.assertEqual(1, kwargs["block"]["pages_per_sheet"])
        self.assertEqual("second printer", kwargs["block"]["printer_override"])


if __name__ == "__main__":
    unittest.main()
