from __future__ import annotations

import tempfile
import threading
import unittest
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pypdf import PdfWriter

from unit_print_manager import (
    ExamPdf,
    PrintBlock,
    PrintManagerApp,
    make_queue_bundle_block,
)
from unit_print_retry import retry_from_failure


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
    def setUp(self) -> None:
        self.addCleanup(patch.stopall)
        # Grouping must never materialize a merged PDF or reach live printing.
        for name in (
            "materialize_pdf_documents", "sumatra_enqueue_pdfs", "shell_print_pdf",
            "get_print_queue_count", "run_powershell", "save_config",
        ):
            patch("unit_print_manager." + name, side_effect=AssertionError(name)).start()

    def test_five_selected_blocks_repeat_original_documents_for_each_of_five_copies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / f"{index}.pdf" for index in range(5)]
            for index, path in enumerate(paths):
                _pdf(path, 590 + index)
            blocks = [_block(path, copies=5) for path in paths]

            bundle = make_queue_bundle_block(blocks, root / "cache")
            app = object.__new__(PrintManagerApp)
            jobs = app.build_print_jobs([bundle])

            self.assertEqual(paths, [item.path for item in bundle.files])
            self.assertEqual((1, 5), (bundle.start, bundle.end))
            self.assertEqual(5, bundle.copies)
            self.assertTrue(bundle.bundled)
            self.assertEqual("queue_bundle", bundle.material_kind)
            self.assertEqual(25, len(jobs))
            self.assertEqual(paths * 5, [job.item.path for job in jobs])
            self.assertEqual([copy for copy in range(1, 6) for _ in paths], [job.bundle_no for job in jobs])
            self.assertTrue(all(job.bundle_total == 5 for job in jobs))
            self.assertTrue(all(job.copies == 1 for job in jobs))
            self.assertFalse((root / "cache").exists())
            self.assertEqual(set(paths), set(root.glob("*.pdf")))

    def test_multifile_blocks_preserve_duplicate_paths_and_source_metadata_in_queue_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, shared, last = [root / name for name in ("first.pdf", "shared.pdf", "last.pdf")]
            for path in (first, shared, last):
                _pdf(path, 595)
            left, right = _block(first, copies=2), _block(last, copies=2)
            left.files = [ExamPdf(7, "첫 범위", first), ExamPdf(9, "공유 첫 등장", shared)]
            right.files = [ExamPdf(3, "공유 재등장", shared), ExamPdf(4, "끝 범위", last)]
            bundle = make_queue_bundle_block([left, right], root / "cache")
            self.assertEqual([first, shared, shared, last], [item.path for item in bundle.files])
            self.assertEqual([7, 9, 3, 4], [item.number for item in bundle.files])
            self.assertEqual(["첫 범위", "공유 첫 등장", "공유 재등장", "끝 범위"], [item.name for item in bundle.files])
            self.assertEqual((1, 4), (bundle.start, bundle.end))
            jobs = object.__new__(PrintManagerApp).build_print_jobs([bundle])
            self.assertEqual([first, shared, shared, last] * 2, [job.item.path for job in jobs])
            self.assertEqual([7, 9, 3, 4] * 2, [job.item.number for job in jobs])
            self.assertFalse((root / "cache").exists())

    def test_rejects_mismatched_copies_without_creating_a_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / "first.pdf", root / "second.pdf"
            _pdf(first, 590)
            _pdf(second, 591)
            with self.assertRaisesRegex(ValueError, "부수"):
                make_queue_bundle_block([_block(first), _block(second, copies=2)], root / "cache")
            self.assertFalse((root / "cache").exists())

    def test_rejects_mismatched_names_and_page_layout_without_creating_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / f"{index}.pdf" for index in range(2)]
            for path in paths:
                _pdf(path, 595)
            blocks = [_block(path) for path in paths]
            blocks[0].name_separator_label = "김학생"
            with self.assertRaisesRegex(ValueError, "이름 구분지"):
                make_queue_bundle_block(blocks, root / "cache")
            blocks[1].name_separator_label = "김학생"
            blocks[1].files[0] = replace(blocks[1].files[0], pages_per_sheet=4)
            with self.assertRaisesRegex(ValueError, "페이지 배치"):
                make_queue_bundle_block(blocks, root / "cache")
            self.assertFalse((root / "cache").exists())

    def test_group_separators_print_once_around_all_repeated_source_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / f"{index}.pdf" for index in range(5)]
            for path in paths:
                _pdf(path, 595)
            blocks = [_block(path, copies=5) for path in paths]
            for block in blocks:
                block.name_separator_label = "김학생"
            bundle = make_queue_bundle_block(blocks, root / "cache")
            app = object.__new__(PrintManagerApp)
            name_path, separator_path = root / "name.pdf", root / "separator.pdf"
            with (
                patch("unit_print_manager.materialize_name_separator_pdf", return_value=name_path) as make_name,
                patch("unit_print_manager.ensure_separator_pdf", return_value=separator_path),
            ):
                jobs = app.build_print_jobs([bundle], include_separator_pages=True, include_name_separator_pages=True)
            self.assertEqual(27, len(jobs))
            self.assertTrue(jobs[0].is_name_separator)
            self.assertEqual(name_path, jobs[0].item.path)
            self.assertTrue(jobs[-1].is_separator)
            self.assertFalse(jobs[-1].is_name_separator)
            self.assertEqual(separator_path, jobs[-1].item.path)
            self.assertEqual(1, sum(job.is_name_separator for job in jobs))
            self.assertEqual(1, sum(job.is_separator and not job.is_name_separator for job in jobs))
            self.assertEqual(paths * 5, [job.item.path for job in jobs[1:-1]])
            self.assertTrue(all(job.copies == 1 for job in jobs))
            self.assertEqual(1, make_name.call_count)
            self.assertFalse((root / "cache").exists())

    def test_retry_keeps_exact_unsent_suffix_inside_repeated_group(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / f"{index}.pdf" for index in range(5)]
            for path in paths:
                _pdf(path, 595)
            bundle = make_queue_bundle_block([_block(path, copies=5) for path in paths], root / "cache")
            app = object.__new__(PrintManagerApp)
            jobs = app.build_print_jobs([bundle])
            # Seven jobs were submitted: one full copy and two documents of copy 2.
            app.retry_batch = retry_from_failure(jobs, 7)
            app.blocks = [bundle]
            app.is_printing = False
            app.refresh_retry_button = Mock()
            app.sync_retry_queue()
            remaining = app.retry_batch.jobs
            self.assertEqual(paths[2:] + paths * 3, [job.item.path for job in remaining])
            self.assertEqual(18, len(remaining))
            self.assertEqual([2] * 3 + [copy for copy in (3, 4, 5) for _ in paths], [job.bundle_no for job in remaining])
            for before, after in zip(jobs[7:], remaining):
                self.assertIs(before, after)
            self.assertTrue(all(job.copies == 1 for job in remaining))

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
            bundle = app.blocks[0]
            self.assertEqual([paths[0], paths[2], paths[3]], [item.path for item in bundle.files])
            self.assertEqual((1, 3), (bundle.start, bundle.end))
            jobs = app.build_print_jobs([bundle])
            self.assertEqual([paths[0], paths[2], paths[3]] * 3, [job.item.path for job in jobs])
            self.assertEqual(9, len(jobs))
            self.assertFalse((root / "cache").exists())
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
            self.assertEqual(paths, [item.path for item in bundle.files])
            self.assertEqual(2, len(bundle.files))
            self.assertFalse((root / "cache").exists())

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

    def test_bundle_freezes_effective_file_settings_without_mutating_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / f"{index}.pdf" for index in range(2)]
            for path in paths:
                _pdf(path, 595)
            blocks = [_block(path, copies=2) for path in paths]
            for block in blocks:
                block.printer_override = "Canon - 세로"
            # The explicit block route is authoritative over this item setting.
            blocks[0].files[0] = replace(blocks[0].files[0], printer_override="Old unused queue")
            source_items = [block.files[0] for block in blocks]
            bundle = make_queue_bundle_block(blocks, root / "cache", base_printer="Canon")
            self.assertEqual(["Canon - 세로"] * 2, [item.printer_override for item in bundle.files])
            self.assertEqual([1, 1], [item.pages_per_sheet for item in bundle.files])
            self.assertEqual("Old unused queue", source_items[0].printer_override)
            self.assertIsNone(source_items[0].pages_per_sheet)
            self.assertIs(source_items[0], blocks[0].files[0])
            jobs = object.__new__(PrintManagerApp).build_print_jobs([bundle])
            self.assertEqual(["Canon - 세로"] * 4, [job.printer_override for job in jobs])
            self.assertEqual(paths * 2, [job.item.path for job in jobs])

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
        for field, value in (("bundled", False), ("pages_per_sheet", 4)):
            self.assertIsNone(app.block_from_config({**serialized, field: value}))

    def test_queue_bundle_roundtrip_preserves_every_file_metadata_and_duplicate(self) -> None:
        app = object.__new__(PrintManagerApp)
        bundle = _block(Path("first.pdf"), copies=5)
        bundle.files = [
            ExamPdf(7, "교과서 핵심문제", Path("first.pdf"), "Canon - 세로", 1),
            ExamPdf(3, "변형문제", Path("second.pdf"), "Canon - 세로", 1),
            ExamPdf(7, "다시 출력할 교과서", Path("first.pdf"), "Canon - 세로", 1),
        ]
        bundle.end = len(bundle.files)
        bundle.bundled = True
        bundle.material_kind = "queue_bundle"
        bundle.display_label = "선택 묶음 (3개 블록)"
        bundle.name_separator_label = "김학생"
        serialized = app.block_to_config(bundle)
        self.assertEqual(
            [
                {"number": item.number, "name": item.name, "path": str(item.path),
                 "printer_override": item.printer_override, "pages_per_sheet": item.pages_per_sheet}
                for item in bundle.files
            ],
            serialized["files"],
        )
        restored = app.block_from_config(serialized)
        self.assertIsNotNone(restored)
        self.assertEqual(bundle.files, restored.files)
        self.assertEqual((1, 3), (restored.start, restored.end))
        self.assertEqual(bundle.name_separator_label, restored.name_separator_label)
        self.assertNotEqual(bundle.history_id, restored.history_id)
        jobs = app.build_print_jobs([restored])
        self.assertEqual([item.path for item in bundle.files] * 5, [job.item.path for job in jobs])
        self.assertTrue(all(job.copies == 1 for job in jobs))

    def test_queue_bundle_config_restores_full_file_list_despite_old_single_range(self) -> None:
        app = object.__new__(PrintManagerApp)
        bundle = _block(Path("first.pdf"))
        bundle.bundled = True
        bundle.material_kind = "queue_bundle"
        serialized = app.block_to_config(bundle)
        serialized["files"] = [
            {"number": 5, "name": "five", "path": "five.pdf", "printer_override": None, "pages_per_sheet": 1},
            {"number": 9, "name": "nine", "path": "nine.pdf", "printer_override": None, "pages_per_sheet": 1},
            {"number": 5, "name": "repeat", "path": "five.pdf", "printer_override": None, "pages_per_sheet": 1},
        ]
        restored = app.block_from_config(serialized)
        self.assertIsNotNone(restored)
        self.assertEqual([Path("five.pdf"), Path("nine.pdf"), Path("five.pdf")], [item.path for item in restored.files])
        self.assertEqual((1, 3), (restored.start, restored.end))

    def test_queue_bundle_config_rejects_hidden_four_up_item_setting(self) -> None:
        app = object.__new__(PrintManagerApp)
        bundle = _block(Path("source.pdf"))
        bundle.bundled = True
        bundle.material_kind = "queue_bundle"
        serialized = app.block_to_config(bundle)
        serialized["files"] = [
            {"number": 1, "name": "layout", "path": "source.pdf", "printer_override": None, "pages_per_sheet": 4},
        ]
        self.assertIsNone(app.block_from_config(serialized))

    def test_legacy_single_merged_path_queue_bundle_still_loads(self) -> None:
        app = object.__new__(PrintManagerApp)
        bundle = _block(Path("legacy_merged.pdf"))
        bundle.bundled = True
        bundle.material_kind = "queue_bundle"
        serialized = app.block_to_config(bundle)
        serialized["files"] = ["legacy_merged.pdf"]
        restored = app.block_from_config(serialized)
        self.assertIsNotNone(restored)
        self.assertEqual([Path("legacy_merged.pdf")], [item.path for item in restored.files])
        self.assertTrue(restored.bundled)
        self.assertEqual("queue_bundle", restored.material_kind)
        self.assertEqual((1, 1), (restored.start, restored.end))

    def test_edit_group_copies_and_name_preserves_full_range_and_job_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / f"{index}.pdf" for index in range(5)]
            for path in paths:
                _pdf(path, 595)
            bundle = make_queue_bundle_block([_block(path, copies=5) for path in paths], root / "cache")
            app = object.__new__(PrintManagerApp)
            app.blocks = [bundle]
            app.selected_block_index_value = 0
            app.edit_copies_var = Mock()
            app.edit_copies_var.get.return_value = 2
            app.edit_name_separator_var = Mock()
            app.edit_name_separator_var.get.return_value = " 이규민 "
            edited = app.make_block_from_editor()
            self.assertEqual((1, 5), (edited.start, edited.end))
            self.assertEqual(2, edited.copies)
            self.assertEqual("이규민", edited.name_separator_label)
            self.assertTrue(edited.bundled)
            self.assertEqual("queue_bundle", edited.material_kind)
            self.assertEqual(bundle.files, edited.files)
            restored = app.block_from_config(app.block_to_config(edited))
            self.assertIsNotNone(restored)
            self.assertEqual((1, 5), (restored.start, restored.end))
            jobs = app.build_print_jobs([restored])
            self.assertEqual(paths * 2, [job.item.path for job in jobs])
            self.assertTrue(all(job.copies == 1 for job in jobs))

    def test_copy_paste_group_preserves_every_original_file_with_fresh_block_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / f"{index}.pdf" for index in range(3)]
            for path in paths:
                _pdf(path, 595)
            bundle = make_queue_bundle_block([_block(path, copies=2) for path in paths], root / "cache")
            app = object.__new__(PrintManagerApp)
            app.blocks = [bundle]
            app.selected_block_indices = {0}
            app.selected_block_index_value = 0
            app.is_printing = False
            app.retry_batch = None
            app.block_status_by_id = {id(bundle): "완료"}
            app.block_detail_by_id = {id(bundle): "실제 인쇄 완료"}
            app.queue_status_var = Mock()
            for name in ("push_undo_state", "update_queue_view", "update_preview", "log"):
                setattr(app, name, Mock())
            app.copy_selected_blocks()
            app.paste_copied_blocks()
            self.assertEqual(2, len(app.blocks))
            copied = app.blocks[1]
            self.assertIsNot(bundle, copied)
            self.assertNotEqual(bundle.history_id, copied.history_id)
            self.assertEqual(bundle.files, copied.files)
            self.assertIsNot(bundle.files, copied.files)
            self.assertEqual("대기", app.block_status_by_id[id(copied)])
            self.assertEqual((1, 3), (copied.start, copied.end))
            restored = app.block_from_config(app.block_to_config(copied))
            self.assertIsNotNone(restored)
            self.assertEqual(copied.files, restored.files)
            jobs = app.build_print_jobs([restored])
            self.assertEqual(paths * 2, [job.item.path for job in jobs])

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
            source_selections = []
            app.open_selected_block_source_folders = Mock(
                side_effect=lambda: source_selections.append(tuple(sorted(app.selected_block_indices)))
            )
            event = SimpleNamespace(y=100, x_root=300, y_root=400)

            with patch("unit_print_manager.tk.Menu") as menu_class:
                result = app.on_queue_context_menu(event)

            self.assertEqual("break", result)
            self.assertEqual({0, 1, 2}, app.selected_block_indices)
            app.select_queue_index.assert_not_called()
            commands = menu_class.return_value.add_command.call_args_list
            self.assertEqual(["묶기", "이름 채우기"], [call.kwargs["label"] for call in commands[:2]])
            self.assertEqual(["normal", "normal"], [call.kwargs["state"] for call in commands[:2]])
            self.assertEqual(["원본 경로 열기", "복사", "붙여넣기", "복제", "삭제", "가나다 정렬"],
                             [call.kwargs["label"] for call in commands[2:]])
            source_command = commands[2].kwargs
            self.assertEqual("normal", source_command["state"])
            self.assertIs(app.open_selected_block_source_folders, source_command["command"])
            source_command["command"]()
            self.assertEqual([(0, 1, 2)], source_selections)
            app.select_queue_index.assert_not_called()
            menu_class.return_value.destroy.assert_not_called()

            # An in-flight folder request disables only that menu action and
            # keeps the selected group intact when the menu is rebuilt.
            app.source_paths_open_pending = True
            with patch("unit_print_manager.tk.Menu") as pending_menu:
                app.post_queue_context_menu(event.x_root, event.y_root)
            pending_commands = pending_menu.return_value.add_command.call_args_list
            pending_source = next(call.kwargs for call in pending_commands
                                  if call.kwargs["label"] == "원본 경로 열기")
            self.assertEqual("disabled", pending_source["state"])
            self.assertEqual({0, 1, 2}, app.selected_block_indices)


if __name__ == "__main__":
    unittest.main()
