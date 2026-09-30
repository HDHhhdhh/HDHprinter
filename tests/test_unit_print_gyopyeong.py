from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from unit_print_gyopyeong import (
    GyopyeongCatalog,
    GyopyeongLesson,
    GyopyeongPart,
    GyopyeongSubject,
    parse_gyopyeong_filename,
    scan_gyopyeong_catalog,
)
from unit_print_manager import PrintManagerApp


class FakeVariable:
    def __init__(self, value: object = "") -> None:
        self.value = value

    def get(self) -> object:
        return self.value

    def set(self, value: object) -> None:
        self.value = value


class GyopyeongCatalogTests(unittest.TestCase):
    def test_filename_parser_normalizes_subject_and_marks_solutions(self) -> None:
        self.assertEqual(
            ("공통수학1", "중간", "최근 기출 킬러", False),
            parse_gyopyeong_filename(Path("공수1 중간 최근 기출 킬러.pdf")),
        )
        self.assertEqual(
            ("확률과 통계", "기말", "최근 고3 기출 준킬러까지", True),
            parse_gyopyeong_filename(
                Path("확통 기말 최근 고3 기출 준킬러까지_해설.pdf")
            ),
        )
        self.assertIsNone(parse_gyopyeong_filename(Path("메모.txt")))

    def test_scanner_excludes_solutions_and_orders_lesson_choices(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            filenames = (
                "공수1 중간 최근 기출 킬러.pdf",
                "공수1 중간 최근 기출 킬러_해설.pdf",
                "기하 전범위 최근 고3 기출 준킬러까지.pdf",
                "대수 중간 최근 고3 기출 킬러.pdf",
                "대수 중간 최근 고3 기출 준킬러까지.pdf",
            )
            for filename in filenames:
                (root / filename).write_bytes(b"pdf")

            catalog = scan_gyopyeong_catalog(root)

            self.assertEqual(("공통수학1", "기하", "대수"), catalog.subject_names)
            common_math = catalog.find_subject("공통수학1")
            geometry = catalog.find_subject("기하")
            algebra = catalog.find_subject("대수")
            assert common_math is not None and geometry is not None and algebra is not None
            self.assertEqual(("중간",), common_math.part_names)
            self.assertTrue(geometry.has_full_range_only)
            algebra_middle = algebra.find_part("중간")
            assert algebra_middle is not None
            self.assertEqual(
                ("최근 고3 기출 준킬러까지", "최근 고3 기출 킬러"),
                tuple(lesson.label for lesson in algebra_middle.lessons),
            )
            self.assertEqual((1, 2), tuple(lesson.number for lesson in algebra_middle.lessons))

    def test_missing_folder_returns_an_empty_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "없음"
            self.assertEqual((), scan_gyopyeong_catalog(missing).subjects)


class GyopyeongBlockTests(unittest.TestCase):
    def test_selected_lesson_builds_a_portrait_problem_block_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "대수 중간 최근 고3 기출 준킬러까지.pdf"
            source.write_bytes(b"pdf")
            lesson = GyopyeongLesson(1, "최근 고3 기출 준킬러까지", source)
            catalog = GyopyeongCatalog(
                (GyopyeongSubject("대수", (GyopyeongPart("중간", (lesson,)),)),)
            )
            app = PrintManagerApp.__new__(PrintManagerApp)
            test_app = cast(Any, app)
            test_app.gyopyeong_material_catalog = catalog
            test_app.gyopyeong_subject_var = FakeVariable("대수")
            test_app.gyopyeong_part_var = FakeVariable("중간")
            test_app.gyopyeong_lesson_var = FakeVariable(lesson.choice_label)
            test_app.gyopyeong_copies_var = FakeVariable(2)
            test_app.printer_var = FakeVariable("iR-ADV 8585 III")

            block = app.make_gyopyeong_material_block()

            self.assertEqual("material_gyopyeong", block.source)
            self.assertEqual("대수", block.subject)
            self.assertEqual("중간", block.level)
            self.assertEqual("최근 고3 기출 준킬러까지", block.round_name)
            self.assertEqual(2, block.copies)
            self.assertEqual("세로", block.printer_route)
            self.assertEqual([source], [item.path for item in block.files])
            self.assertIn("01. 최근 고3 기출 준킬러까지", block.display_label)

            restored = app.block_from_config(app.block_to_config(block))
            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual("material_gyopyeong", restored.source)
            self.assertEqual("세로", restored.printer_route)
            self.assertEqual([source], [item.path for item in restored.files])


if __name__ == "__main__":
    unittest.main()
