from __future__ import annotations

import tempfile
import threading
import unittest
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pypdf import PdfReader, PdfWriter

from unit_print_manager import (
    ExamPdf,
    PrintBlock,
    PrintManagerApp,
    make_queue_bundle_block,
)


def _pdf(path: Path, width: int) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=width, height=842)
    with path.open("wb") as stream:
        writer.write(stream)


def _block(path: Path, *, copies: int = 3, route: str = "세로") -> PrintBlock:
    return PrintBlock(
        subject="자료", level="", round_name="직접", start=1, end=1,
        copies=copies, files=[ExamPdf(1, path.stem, path)],
        source="custom", printer_route=route,
    )


class QueueBundleTest(unittest.TestCase):
    def test_three_selected_blocks_become_one_stapled_pdf_per_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / f"{index}.pdf" for index in range(3)]
            for index, path in enumerate(paths):
                _pdf(path, 590 + index)
            blocks = [_block(path) for path in paths]

            bundle = make_queue_bundle_block(blocks, root / "cache")
            widths = [int(page.mediabox.width) for page in PdfReader(str(bundle.files[0].path)).pages]
            app = object.__new__(PrintManagerApp)
            jobs = app.build_print_jobs([bundle])

            self.assertEqual([590, 591, 592], widths)
            self.assertEqual(3, bundle.copies)
            self.assertTrue(bundle.bundled)
            self.assertEqual("queue_bundle", bundle.material_kind)
            self.assertEqual(3, len(jobs))
            self.assertEqual([1, 2, 3], [job.bundle_no for job in jobs])
            self.assertTrue(all(job.item.path == bundle.files[0].path for job in jobs))
            self.assertTrue(all(job.copies == 1 for job in jobs))

    def test_rejects_mismatched_copies_without_creating_a_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / "first.pdf", root / "second.pdf"
            _pdf(first, 590)
            _pdf(second, 591)
            with self.assertRaisesRegex(ValueError, "부수"):
                make_queue_bundle_block([_block(first), _block(second, copies=2)], root / "cache")
            self.assertFalse((root / "cache").exists())

    def test_rejects_cross_orientation_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / "portrait.pdf", root / "landscape.pdf"
            _pdf(first, 590)
            writer = PdfWriter()
            writer.add_blank_page(width=842, height=595)
            with second.open("wb") as stream:
                writer.write(stream)
            with self.assertRaisesRegex(ValueError, "가로와 세로"):
                make_queue_bundle_block(
                    [_block(first, route="자동 감지"), _block(second, route="자동 감지")],
                    root / "cache",
                )

    def test_group_action_replaces_selected_rows_at_first_position(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / f"{index}.pdf" for index in range(4)]
            for index, path in enumerate(paths):
                _pdf(path, 590 + index)
            blocks = [_block(path) for path in paths]
            app = object.__new__(PrintManagerApp)
            app.blocks = list(blocks)
            app.selected_block_indices = {0, 2, 3}
            app.block_status_by_id = {id(block): "대기" for block in blocks}
            app.block_detail_by_id = {}
            app.is_printing = False
            app.print_state_lock = threading.RLock()
            app.push_undo_state = Mock()
            app.sync_retry_queue = Mock()
            app.update_queue_view = Mock()
            app.update_preview = Mock()
            app.record_history = Mock()
            app.log = Mock()
            app.queue_status_var = Mock()
            app.block_preparation_epoch = 0
            app.submit_background_block_build = Mock()

            with patch("unit_print_manager.MATERIAL_RANGE_CACHE_DIR", root / "cache"):
                result = app.group_selected_blocks()
                self.assertEqual(blocks, app.blocks)
                work, finish = app.submit_background_block_build.call_args.args
                future = Future()
                future.set_result(work())
                finish(future)

            self.assertTrue(result)
            self.assertEqual(2, len(app.blocks))
            self.assertIs(blocks[1], app.blocks[1])
            widths = [int(page.mediabox.width) for page in PdfReader(str(app.blocks[0].files[0].path)).pages]
            self.assertEqual([590, 592, 593], widths)
            app.update_queue_view.assert_called_once_with(select_index=0)
            app.push_undo_state.assert_called_once_with("블록 묶기")

    def test_mixed_explicit_and_auto_routes_compare_real_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / f"{index}.pdf" for index in range(2)]
            for path in paths:
                _pdf(path, 595)
            blocks = [_block(path, route="자동 감지") for path in paths]
            blocks[0].printer_override = "다른 프린터 - 세로"
            with self.assertRaisesRegex(ValueError, "서로 다른 프린터"):
                make_queue_bundle_block(blocks, root / "cache", base_printer="Canon")
            self.assertFalse((root / "cache").exists())
            with self.assertRaisesRegex(ValueError, "기본 프린터"):
                make_queue_bundle_block(blocks, root / "cache")

            blocks[0].printer_override = "Canon - 세로"
            bundle = make_queue_bundle_block(blocks, root / "cache", base_printer="Canon")
            self.assertEqual("Canon - 세로", bundle.printer_override)
            self.assertEqual(2, len(PdfReader(bundle.files[0].path).pages))

    def test_item_printer_override_cannot_be_hidden_by_auto_block(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / f"{index}.pdf" for index in range(2)]
            for path in paths:
                _pdf(path, 595)
            blocks = [_block(path, route="자동 감지") for path in paths]
            blocks[0].files[0] = replace(blocks[0].files[0], printer_override="다른 프린터")
            with self.assertRaisesRegex(ValueError, "서로 다른 프린터"):
                make_queue_bundle_block(blocks, root / "cache", base_printer="Canon")
            self.assertFalse((root / "cache").exists())

    def test_queue_bundle_roundtrip_preserves_locked_bundle_kind(self) -> None:
        app = object.__new__(PrintManagerApp)
        block = _block(Path("merged.pdf"))
        block.bundled = True
        block.material_kind = "queue_bundle"
        block.name_separator_label = "학생"
        block.display_label = "선택 묶음 (3개 블록)"
        block.printer_override = "Canon - 세로"
        serialized = app.block_to_config(block)
        restored = app.block_from_config(serialized)
        self.assertIsNotNone(restored)
        self.assertEqual("queue_bundle", restored.material_kind)
        self.assertTrue(restored.bundled)
        self.assertEqual(block.copies, restored.copies)
        self.assertEqual(block.printer_override, restored.printer_override)
        self.assertEqual(block.display_label, restored.display_label)
        self.assertEqual(block.name_separator_label, restored.name_separator_label)
        for field, value in (("bundled", False), ("pages_per_sheet", 4),
                             ("files", ["a.pdf", "b.pdf"])):
            self.assertIsNone(app.block_from_config({**serialized, field: value}))

    def test_right_click_on_selected_row_keeps_multi_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            blocks = [_block(root / f"{index}.pdf") for index in range(3)]
            app = object.__new__(PrintManagerApp)
            app.blocks = blocks
            app.selected_block_indices = {0, 1, 2}
            app.block_status_by_id = {id(block): "대기" for block in blocks}
            app.is_printing = False
            app.queue_canvas = Mock()
            app.queue_canvas.canvasy.return_value = 100
            app.queue_hit_index_from_y = Mock(return_value=1)
            app.select_queue_index = Mock()
            app.focus_queue_canvas = Mock()
            event = SimpleNamespace(y=100, x_root=300, y_root=400)

            with patch("unit_print_manager.tk.Menu") as menu_class:
                result = app.on_queue_context_menu(event)

            self.assertEqual("break", result)
            self.assertEqual({0, 1, 2}, app.selected_block_indices)
            app.select_queue_index.assert_not_called()
            commands = menu_class.return_value.add_command.call_args_list
            self.assertEqual(["묶기", "이름 채우기"], [call.kwargs["label"] for call in commands[:2]])
            self.assertEqual(["normal", "normal"], [call.kwargs["state"] for call in commands[:2]])
            self.assertEqual(["복사", "붙여넣기", "복제", "삭제", "가나다 정렬"], [call.kwargs["label"] for call in commands[2:]])
            menu_class.return_value.destroy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
