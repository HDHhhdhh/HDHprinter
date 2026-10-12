from __future__ import annotations

import unittest
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from unit_print_manager import ExamPdf, PrintBlock, PrintManagerApp
from unit_print_name_separator import name_separator_fill_plan


def block(name: str = "", *, source: str = "custom") -> PrintBlock:
    return PrintBlock(
        subject="교과서", level="", round_name="직접", start=1, end=1,
        copies=1, files=[ExamPdf(1, "문서", Path("document.pdf"))],
        source=source, name_separator_label=name,
    )


def app_with_blocks(blocks: list[PrintBlock], indices: set[int]) -> PrintManagerApp:
    app = object.__new__(PrintManagerApp)
    app.blocks = list(blocks)
    app.selected_block_indices = set(indices)
    app.selected_block_index_value = min(indices)
    app.queue_selection_anchor = min(indices)
    app.block_status_by_id = {id(item): "대기" for item in blocks}
    app.block_detail_by_id = {}
    app.retry_batch = None
    app.undo_stack = []
    app.is_printing = False
    app.print_state_lock = threading.Lock()
    app.update_queue_view = Mock()
    app.update_preview = Mock()
    app.sync_retry_queue = Mock()
    app.queue_status_var = Mock()
    app.log = Mock()
    return app


class NameSeparatorFillPlanTest(unittest.TestCase):
    def test_uses_one_normalized_name_and_only_blank_positions(self) -> None:
        self.assertEqual(("이규민", (1, 2)), name_separator_fill_plan([" 이규민 ", "", "  ", "이규민"]))

    def test_rejects_missing_or_conflicting_source_names(self) -> None:
        with self.assertRaisesRegex(ValueError, "입력된 블록이 없습니다"):
            name_separator_fill_plan(["", ""])
        with self.assertRaisesRegex(ValueError, "서로 다른 이름"):
            name_separator_fill_plan(["이규민", "박철수", ""])


class QueueNameFillTest(unittest.TestCase):
    def test_fills_blank_selected_blocks_and_undo_restores_originals(self) -> None:
        blocks = [block("이규민"), block(), block(), block("다른 학생")]
        app = app_with_blocks(blocks, {0, 1, 2})

        self.assertTrue(app.fill_selected_block_names())

        self.assertIs(app.blocks[0], blocks[0])
        self.assertIs(app.blocks[3], blocks[3])
        self.assertEqual(["이규민", "이규민", "이규민"], [item.name_separator_label for item in app.blocks[:3]])
        self.assertIsNot(app.blocks[1], blocks[1])
        self.assertEqual(blocks[1].history_id, app.blocks[1].history_id)
        self.assertEqual({0, 1, 2}, app.selected_block_indices)
        self.assertEqual("대기", app.block_status_by_id[id(app.blocks[1])])
        self.assertNotIn(id(blocks[1]), app.block_status_by_id)
        app.update_queue_view.assert_called_once_with()

        self.assertEqual("break", app.undo_last_action())
        self.assertTrue(all(before is after for before, after in zip(blocks, app.blocks)))
        self.assertEqual("", app.blocks[1].name_separator_label)

    def test_conflicting_names_leave_queue_unchanged(self) -> None:
        blocks = [block("이규민"), block("박철수"), block()]
        app = app_with_blocks(blocks, {0, 1, 2})

        with patch("unit_print_manager.messagebox.showwarning") as warning:
            self.assertFalse(app.fill_selected_block_names())

        self.assertTrue(all(before is after for before, after in zip(blocks, app.blocks)))
        self.assertFalse(app.undo_stack)
        warning.assert_called_once()

    def test_does_not_replace_a_submitted_or_retry_target(self) -> None:
        blocks = [block("이규민"), block()]
        app = app_with_blocks(blocks, {0, 1})
        app.block_status_by_id[id(blocks[1])] = "완료"
        with patch("unit_print_manager.messagebox.showwarning"):
            self.assertFalse(app.fill_selected_block_names())
        self.assertIs(app.blocks[1], blocks[1])
        self.assertFalse(app.undo_stack)

        app.block_status_by_id[id(blocks[1])] = "대기"
        app.retry_batch = SimpleNamespace(jobs=(SimpleNamespace(block_uid=id(blocks[1])),))
        with patch("unit_print_manager.messagebox.showwarning"):
            self.assertFalse(app.fill_selected_block_names())
        self.assertIs(app.blocks[1], blocks[1])
        self.assertFalse(app.undo_stack)


if __name__ == "__main__":
    unittest.main()
