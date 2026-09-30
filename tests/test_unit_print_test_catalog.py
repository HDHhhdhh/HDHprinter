from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from unit_print_manager import ExamPdf, PrintBlock, PrintManagerApp
from unit_print_test_catalog import (
    TEST_SOURCE_BY_KIND,
    scan_test_catalog,
    test_filter_choices as catalog_filter_choices,
    test_filter_label as catalog_filter_label,
)


class FakeVariable:
    def __init__(self, value: object) -> None:
        self.value = value

    def get(self) -> object:
        return self.value

    def set(self, value: object) -> None:
        self.value = value


def touch_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4\n%%EOF\n")


class TestCatalogScanTests(unittest.TestCase):
    def test_review_scan_maps_middle_to_middle_high_and_excludes_answers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            touch_pdf(root / "공수1" / "복습테스트_중" / "2회차 문제.pdf")
            touch_pdf(root / "공수1" / "복습테스트_중" / "1회차 문제.pdf")
            touch_pdf(root / "공수1" / "복습테스트_중" / "1회차 해설.pdf")
            touch_pdf(root / "공수1" / "복습테스트_상" / "1회차 문제.pdf")
            touch_pdf(root / "공수1" / "_backup_exam" / "복습테스트_상" / "3회차 문제.pdf")

            catalog = scan_test_catalog(root, "복습테스트")

            self.assertEqual(("공통수학1",), catalog.subject_names)
            subject = catalog.find_subject("공통수학1")
            assert subject is not None
            self.assertEqual([1, 2], [item.embedded_number for item in subject.middle_high])
            self.assertEqual(1, len(subject.high))
            self.assertEqual(3, catalog.file_count)
            self.assertEqual(1, catalog.excluded_solution_count)
            self.assertEqual(1, catalog.excluded_directory_count)

    def test_school_scan_combines_levels_by_term_and_starts_high_at_eleven(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            touch_pdf(root / "대수" / "중간" / "중상" / "1회차 압구정고.pdf")
            touch_pdf(root / "대수" / "중간" / "상" / "1회차 휘문고.pdf")
            touch_pdf(root / "대수" / "중간" / "레거시 문제.pdf")
            (root / "미적1").mkdir()

            catalog = scan_test_catalog(root, "학군지기출")

            self.assertEqual(("대수", "미적분1"), catalog.subject_names)
            self.assertEqual(2, catalog.file_count)
            self.assertEqual(1, catalog.unclassified_level_count)
            self.assertEqual((), catalog.documents("미적분1", "중상"))
            middle_term = catalog.documents("대수", "중간")
            self.assertEqual([1, 11], [item.number for item in middle_term])
            self.assertEqual(
                ["중상 1회차 압구정고", "상 1회차 휘문고"],
                [item.label for item in middle_term],
            )

    def test_ilsan_term_view_keeps_high_slot_at_eleven_after_seven_middle_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for number in range(1, 8):
                touch_pdf(root / "대수" / "중상" / "중간" / f"{number}회차 중상.pdf")
            touch_pdf(root / "대수" / "상" / "중간" / "1회차 휘문고.pdf")
            touch_pdf(root / "대수" / "상" / "중간" / "2회차 상산고.pdf")
            touch_pdf(root / "미적1" / "기말" / "1회차.pdf")

            catalog = scan_test_catalog(root, "일산기출")

            middle_term = catalog.documents("대수", "중간")
            self.assertEqual([1, 2, 3, 4, 5, 6, 7, 11, 12], [item.number for item in middle_term])
            self.assertEqual("상 1회차 휘문고", middle_term[7].label)
            self.assertEqual("상 2회차 상산고", middle_term[8].label)
            self.assertEqual("중상 1회차", catalog.documents("미적분1", "기말")[0].label)

    def test_school_and_ilsan_use_term_filter_while_review_keeps_level_filter(self) -> None:
        self.assertEqual(("중상", "상"), catalog_filter_choices("복습테스트"))
        self.assertEqual("레벨", catalog_filter_label("복습테스트"))
        for kind in ("학군지기출", "일산기출"):
            self.assertEqual(("중간", "기말"), catalog_filter_choices(kind))
            self.assertEqual("시험 구분", catalog_filter_label(kind))

    def test_ilsan_flat_numbered_baseline_remains_fallback_until_level_files_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            legacy = root / "미적1" / "중간" / "1회차.pdf"
            touch_pdf(legacy)
            original_bytes = legacy.read_bytes()

            before = scan_test_catalog(root, "일산기출")
            self.assertEqual([legacy], [item.path for item in before.documents("미적분1", "중간")])
            self.assertEqual(0, before.excluded_legacy_fallback_count)

            renewed = root / "미적1" / "중간" / "중상" / "1회차 25 저동고.pdf"
            touch_pdf(renewed)
            after = scan_test_catalog(root, "일산기출")
            self.assertEqual([renewed], [item.path for item in after.documents("미적분1", "중간")])
            self.assertEqual(1, after.excluded_legacy_fallback_count)
            self.assertEqual(0, after.excluded_directory_count)
            self.assertEqual(0, after.unclassified_level_count)
            self.assertEqual(original_bytes, legacy.read_bytes())

    def test_ilsan_high_only_replacement_starts_at_eleven(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            touch_pdf(root / "미적1" / "중간" / "01회차.pdf")
            renewed = root / "미적1" / "중간" / "상" / "1회차 25 저현고.pdf"
            touch_pdf(renewed)

            catalog = scan_test_catalog(root, "일산기출")

            self.assertEqual([renewed], [item.path for item in catalog.documents("미적분1", "중간")])
            self.assertEqual([11], [item.number for item in catalog.documents("미적분1", "중간")])
            self.assertEqual(1, catalog.excluded_legacy_fallback_count)

    def test_ilsan_legacy_preference_is_scoped_to_subject_and_term(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            same_term_legacy = root / "미적1" / "중간" / "1회차.pdf"
            other_term_legacy = root / "미적1" / "기말" / "1회차.pdf"
            other_subject_legacy = root / "공수1" / "중간" / "1회차.pdf"
            renewed = root / "미적1" / "중간" / "중상" / "1회차 25 저동고.pdf"
            for path in (same_term_legacy, other_term_legacy, other_subject_legacy, renewed):
                touch_pdf(path)

            catalog = scan_test_catalog(root, "일산기출")

            self.assertEqual([renewed], [item.path for item in catalog.documents("미적분1", "중간")])
            self.assertEqual([other_term_legacy], [item.path for item in catalog.documents("미적분1", "기말")])
            self.assertEqual([other_subject_legacy], [item.path for item in catalog.documents("공통수학1", "중간")])
            self.assertEqual(1, catalog.excluded_legacy_fallback_count)

    def test_ilsan_answers_and_excluded_directories_do_not_suppress_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            legacy = root / "미적1" / "중간" / "1회차.pdf"
            touch_pdf(legacy)
            touch_pdf(root / "미적1" / "중간" / "중상" / "1회차 해설.pdf")
            touch_pdf(root / "미적1" / "중간" / "상" / "1회차 정답.pdf")
            touch_pdf(root / "미적1" / "중간" / "_backup" / "중상" / "1회차 문제.pdf")

            catalog = scan_test_catalog(root, "일산기출")

            self.assertEqual([legacy], [item.path for item in catalog.documents("미적분1", "중간")])
            self.assertEqual(0, catalog.excluded_legacy_fallback_count)
            self.assertEqual(2, catalog.excluded_solution_count)
            self.assertEqual(1, catalog.excluded_directory_count)

    def test_ilsan_named_and_nested_level_less_pdfs_remain_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            legacy = root / "미적1" / "중간" / "1회차.pdf"
            named = root / "미적1" / "중간" / "1회차 보충.pdf"
            nested = root / "미적1" / "중간" / "보충" / "2회차.pdf"
            renewed = root / "미적1" / "중간" / "중상" / "1회차 25 저동고.pdf"
            for path in (legacy, named, nested, renewed):
                touch_pdf(path)

            catalog = scan_test_catalog(root, "일산기출")

            self.assertEqual({named, nested, renewed}, {item.path for item in catalog.documents("미적분1", "중간")})
            self.assertEqual(1, catalog.excluded_legacy_fallback_count)

    def test_other_catalog_kinds_keep_existing_level_classification(self) -> None:
        for kind in ("복습테스트", "학군지기출"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                touch_pdf(root / "미적1" / "중간" / "1회차.pdf")
                renewed = root / "미적1" / "중간" / "중상" / "1회차 문제.pdf"
                touch_pdf(renewed)

                catalog = scan_test_catalog(root, kind)

                self.assertEqual([renewed], [item.path for item in catalog.documents("미적분1", "중상")])
                self.assertEqual(1, catalog.unclassified_level_count)
                self.assertEqual(0, catalog.excluded_legacy_fallback_count)

    def test_missing_root_returns_an_empty_catalog(self) -> None:
        catalog = scan_test_catalog(Path("Z:/definitely/missing/test-root"), "일산기출")

        self.assertEqual((), catalog.subjects)
        self.assertEqual(0, catalog.file_count)


class TestCatalogBlockTests(unittest.TestCase):
    def make_app(self) -> PrintManagerApp:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.printer_var = FakeVariable("iR-ADV 8585 III")
        app.test_builder_vars = {
            "복습테스트": {
                "subject": FakeVariable("공통수학1"),
                "level": FakeVariable("중상"),
                "start": FakeVariable("01. 1회차"),
                "end": FakeVariable("05. 5회차"),
                "selected": FakeVariable("1-3, 5"),
                "copies": FakeVariable(2),
                "bundle": FakeVariable(True),
            }
        }
        app.test_exam_files = {
            "복습테스트": [
                ExamPdf(number, f"{number}회차", Path(f"복습 {number}.pdf"))
                for number in range(1, 6)
            ]
        }
        return app

    def test_non_contiguous_test_block_uses_portrait_route_and_distinct_source(self) -> None:
        app = self.make_app()

        block = app.make_test_catalog_block("복습테스트")

        self.assertEqual(TEST_SOURCE_BY_KIND["복습테스트"], block.source)
        self.assertEqual((1, 2, 3, 5), block.selected_numbers)
        self.assertEqual([1, 2, 3, 5], [item.number for item in block.files])
        self.assertEqual("세로", block.printer_route)
        self.assertIn("(1,2,3,5)", block.display_label)
        self.assertTrue(block.bundled)
        self.assertEqual(2, block.copies)

    def test_test_block_config_round_trip_keeps_files_and_selection(self) -> None:
        app = self.make_app()
        block = app.make_test_catalog_block("복습테스트")

        restored = app.block_from_config(app.block_to_config(block))

        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(block.source, restored.source)
        self.assertEqual(block.selected_numbers, restored.selected_numbers)
        self.assertEqual([1, 2, 3, 5], [item.number for item in restored.files])
        self.assertEqual("세로", restored.printer_route)

    def test_queue_editor_rebuilds_test_block_label_after_selection_change(self) -> None:
        app = self.make_app()
        original = app.make_test_catalog_block("복습테스트")
        app.blocks = [original]
        app.selected_block_index_value = 0
        app.edit_exam_files = app.test_exam_files["복습테스트"]
        app.edit_subject_var = FakeVariable("공통수학1")
        app.edit_level_var = FakeVariable("중상")
        app.edit_round_var = FakeVariable("복습테스트")
        app.edit_start_var = FakeVariable("01. 1회차")
        app.edit_end_var = FakeVariable("05. 5회차")
        app.edit_selected_numbers_var = FakeVariable("2-4")
        app.edit_name_separator_var = FakeVariable("학생")
        app.edit_copies_var = FakeVariable(1)
        app.edit_bundle_var = FakeVariable(False)

        edited = app.make_block_from_editor()

        self.assertEqual((2, 3, 4), edited.selected_numbers)
        self.assertEqual([2, 3, 4], [item.number for item in edited.files])
        self.assertIn("(2,3,4)", edited.display_label)
        self.assertEqual("학생", edited.name_separator_label)

    def test_school_individual_number_eleven_selects_first_high_document(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.printer_var = FakeVariable("iR-ADV 8585 III")
        app.test_builder_vars = {
            "학군지기출": {
                "subject": FakeVariable("대수"),
                "level": FakeVariable("중간"),
                "start": FakeVariable("01. 중상 1회차 압구정고"),
                "end": FakeVariable("11. 상 1회차 휘문고"),
                "selected": FakeVariable("11"),
                "copies": FakeVariable(1),
                "bundle": FakeVariable(False),
            }
        }
        app.test_exam_files = {
            "학군지기출": [
                *[
                    ExamPdf(number, f"중상 {number}회차 학교{number}", Path(f"중상 {number}.pdf"))
                    for number in range(1, 8)
                ],
                ExamPdf(11, "상 1회차 휘문고", Path("상 1.pdf")),
            ]
        }

        block = app.make_test_catalog_block("학군지기출")

        self.assertEqual((11,), block.selected_numbers)
        self.assertEqual([Path("상 1.pdf")], [item.path for item in block.files])
        self.assertIn("중간 / (11)", block.display_label)


if __name__ == "__main__":
    unittest.main()
