from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from unit_print_manager import PrintManagerApp
from unit_print_selected_exams import (
    SELECTED_EXAM_LESSON_NUMBERS,
    SELECTED_EXAM_SUBJECT_ORDER,
    scan_selected_exam_catalog,
)


class FakeVariable:
    def __init__(self, value: object = "") -> None:
        self.value = value

    def get(self) -> object:
        return self.value

    def set(self, value: object) -> None:
        self.value = value


class FakeNotebook:
    def __init__(self, current: int, count: int) -> None:
        self.current = current
        self.count = count

    def index(self, _value: object) -> int:
        return self.current

    def tabs(self) -> tuple[int, ...]:
        return tuple(range(self.count))


class SelectedExamCatalogTests(unittest.TestCase):
    def test_catalog_always_contains_five_subjects_and_ten_lessons(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            catalog = scan_selected_exam_catalog(Path(temp_dir) / "missing")

            self.assertEqual(SELECTED_EXAM_SUBJECT_ORDER, catalog.subject_names)
            for subject in catalog.subjects:
                self.assertEqual(
                    SELECTED_EXAM_LESSON_NUMBERS,
                    tuple(lesson.number for lesson in subject.lessons),
                )
                self.assertFalse(any(lesson.is_available for lesson in subject.lessons))

    def test_only_exact_subject_lesson_paths_become_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            expected = root / "기하" / "기출엄선 기하 3회차.pdf"
            expected.parent.mkdir(parents=True)
            expected.write_bytes(b"pdf")
            nested = root / "기하" / "PPT" / "기출엄선 기하 4회차.pdf"
            nested.parent.mkdir(parents=True)
            nested.write_bytes(b"pdf")

            catalog = scan_selected_exam_catalog(root)
            geometry = catalog.find_subject("기하")
            assert geometry is not None

            self.assertTrue(geometry.find_lesson(3).is_available)
            self.assertFalse(geometry.find_lesson(4).is_available)


class SelectedExamBlockTests(unittest.TestCase):
    def make_app(self, catalog: object, lesson_label: str, copies: int = 2) -> PrintManagerApp:
        app = PrintManagerApp.__new__(PrintManagerApp)
        test_app = cast(Any, app)
        test_app.selected_exam_material_catalog = catalog
        test_app.selected_exam_subject_var = FakeVariable("기하")
        test_app.selected_exam_lesson_var = FakeVariable(lesson_label)
        test_app.selected_exam_selected_numbers_var = FakeVariable("")
        test_app.selected_exam_copies_var = FakeVariable(copies)
        test_app.printer_var = FakeVariable("iR-ADV 8585 III")
        return app

    def test_available_lesson_builds_portrait_block_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "기하" / "기출엄선 기하 3회차.pdf"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"pdf")
            catalog = scan_selected_exam_catalog(root)
            geometry = catalog.find_subject("기하")
            assert geometry is not None
            lesson = geometry.find_lesson(3)
            assert lesson is not None
            app = self.make_app(catalog, lesson.choice_label)

            block = app.make_selected_exam_material_block()

            self.assertEqual("material_selected_exam", block.source)
            self.assertEqual("기하", block.subject)
            self.assertEqual("기출엄선", block.level)
            self.assertEqual("3회차", block.round_name)
            self.assertEqual(2, block.copies)
            self.assertEqual("세로", block.printer_route)
            self.assertEqual([source], [item.path for item in block.files])

            restored = app.block_from_config(app.block_to_config(block))
            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual("material_selected_exam", restored.source)
            self.assertEqual("세로", restored.printer_route)
            self.assertEqual([source], [item.path for item in restored.files])

    def test_missing_lesson_cannot_build_a_block(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            catalog = scan_selected_exam_catalog(Path(temp_dir))
            geometry = catalog.find_subject("기하")
            assert geometry is not None
            lesson = geometry.find_lesson(1)
            assert lesson is not None
            app = self.make_app(catalog, lesson.choice_label)

            with self.assertRaisesRegex(ValueError, "아직 등록되지 않았습니다"):
                app.make_selected_exam_material_block()

    def test_individual_lessons_accept_ranges_and_preserve_input_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for number in (1, 2, 3, 5):
                source = root / "기하" / f"기출엄선 기하 {number}회차.pdf"
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(b"pdf")
            catalog = scan_selected_exam_catalog(root)
            geometry = catalog.find_subject("기하")
            assert geometry is not None
            lesson = geometry.find_lesson(1)
            assert lesson is not None
            app = self.make_app(catalog, lesson.choice_label)
            cast(Any, app).selected_exam_selected_numbers_var.set("1-3, 5")

            block = app.make_selected_exam_material_block()

            self.assertEqual((1, 2, 3, 5), block.selected_numbers)
            self.assertEqual([1, 2, 3, 5], [item.number for item in block.files])
            self.assertEqual("(1,2,3,5)차시", block.round_name)
            self.assertIn("(1,2,3,5)차시", block.display_label)

    def test_individual_lessons_reject_an_unregistered_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "기하" / "기출엄선 기하 1회차.pdf"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"pdf")
            catalog = scan_selected_exam_catalog(root)
            geometry = catalog.find_subject("기하")
            assert geometry is not None
            lesson = geometry.find_lesson(1)
            assert lesson is not None
            app = self.make_app(catalog, lesson.choice_label)
            cast(Any, app).selected_exam_selected_numbers_var.set("1,2")

            with self.assertRaisesRegex(ValueError, "등록되지 않은 차시.*2"):
                app.make_selected_exam_material_block()

    def test_editor_can_replace_individual_lesson_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for number in (1, 2, 5):
                source = root / "기하" / f"기출엄선 기하 {number}회차.pdf"
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(b"pdf")
            catalog = scan_selected_exam_catalog(root)
            geometry = catalog.find_subject("기하")
            assert geometry is not None
            lesson = geometry.find_lesson(1)
            assert lesson is not None
            app = self.make_app(catalog, lesson.choice_label)
            cast(Any, app).selected_exam_selected_numbers_var.set("1,2")
            original = app.make_selected_exam_material_block()
            cast(Any, app).edit_selected_numbers_var = FakeVariable("2,5")

            edited = app.make_selected_exam_block_from_editor(original, 3, False)

            self.assertEqual((2, 5), edited.selected_numbers)
            self.assertEqual([2, 5], [item.number for item in edited.files])
            self.assertEqual(3, edited.copies)
            self.assertIn("(2,5)차시", edited.display_label)

    def test_reordered_tab_routes_ctrl_enter_to_selected_exam(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        test_app = cast(Any, app)
        test_app.builder_notebook = FakeNotebook(current=1, count=5)
        test_app.material_tabs = FakeNotebook(current=2, count=7)
        test_app.widget_is_in_builder = lambda _widget: True
        calls: list[str] = []
        test_app.add_selected_exam_material_block = lambda: calls.append("selected")

        result = app.handle_add_shortcut(SimpleNamespace(widget=object()))

        self.assertEqual("break", result)
        self.assertEqual(["selected"], calls)

    def test_apex_boost_is_resolved_from_the_last_material_tab(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        test_app = cast(Any, app)
        test_app.builder_notebook = FakeNotebook(current=1, count=5)
        test_app.material_tabs = FakeNotebook(current=5, count=7)
        test_app.apex_variant_var = FakeVariable("APEX")

        self.assertEqual("APEX Boost", app.active_apex_variant())

    def test_appended_textbook_tab_routes_ctrl_enter_to_textbook(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        test_app = cast(Any, app)
        test_app.builder_notebook = FakeNotebook(current=1, count=5)
        test_app.material_tabs = FakeNotebook(current=6, count=7)
        test_app.widget_is_in_builder = lambda _widget: True
        calls: list[str] = []
        test_app.add_textbook_material_block = lambda: calls.append("textbook")

        result = app.handle_add_shortcut(SimpleNamespace(widget=object()))

        self.assertEqual("break", result)
        self.assertEqual(["textbook"], calls)


if __name__ == "__main__":
    unittest.main()
