from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from pypdf import PdfReader

from unit_print_manager import (
    IME_SHORTCUT_DELAY_MS,
    NAME_IME_IDLE_COMMIT_MS,
    ExamPdf,
    PrintBlock,
    PrintManagerApp,
    name_separator_group_count,
    name_separator_group_labels,
)
from unit_print_name_separator import (
    MAX_FONT_SIZE,
    MAX_NAME_SEPARATOR_LABEL_LENGTH,
    materialize_name_separator_pdf,
    normalize_name_separator_label,
)
from unit_print_test_catalog import TEST_SOURCE_BY_KIND


class FakeVariable:
    def __init__(self, value: object) -> None:
        self.value = value

    def get(self) -> object:
        return self.value

    def set(self, value: object) -> None:
        self.value = value


class FakeEntry(FakeVariable):
    def __init__(self, value: object, *, window_handle: int = 2468) -> None:
        super().__init__(value)
        self.focus_calls = 0
        self.window_handle = window_handle
        self.cursor = len(str(value))

    def focus_set(self) -> None:
        self.focus_calls += 1

    def winfo_toplevel(self) -> "FakeEntry":
        return self

    def winfo_id(self) -> int:
        return self.window_handle

    def index(self, index: object) -> int:
        if index == "insert":
            return self.cursor
        raise ValueError(index)

    def selection_present(self) -> bool:
        return False

    def insert(self, index: int, text: str) -> None:
        current = str(self.value)
        self.value = current[:index] + text + current[index:]
        self.cursor = index + len(text)

    def icursor(self, index: int) -> None:
        self.cursor = index

    def __str__(self) -> str:
        return ".student_name"


class RecordingRoot:
    def __init__(self, focused: object | None = None) -> None:
        self.callbacks: list[tuple[int, object]] = []
        self.focused = focused
        self.focus_calls = 0
        self.cancelled: list[str] = []

    def after(self, delay: int, callback: object) -> str:
        self.callbacks.append((delay, callback))
        return f"after-{len(self.callbacks)}"

    def after_cancel(self, after_id: str) -> None:
        self.cancelled.append(after_id)

    def focus_get(self) -> object | None:
        return self.focused

    def focus_set(self) -> None:
        self.focus_calls += 1

    def run_next(self, delay: int) -> None:
        index = next(
            index
            for index, (queued_delay, _callback) in enumerate(self.callbacks)
            if queued_delay == delay
        )
        _queued_delay, callback = self.callbacks.pop(index)
        callback()


def make_block(name: str, number: int = 1) -> PrintBlock:
    return PrintBlock(
        subject=f"블록 {number}",
        level="Lv.1",
        round_name="1",
        start=1,
        end=1,
        copies=1,
        files=[ExamPdf(1, f"본문 {number}", Path(f"본문 {number}.pdf"))],
        name_separator_label=name,
    )


class NameSeparatorPdfTest(unittest.TestCase):
    def test_label_normalization_produces_one_safe_line(self) -> None:
        self.assertEqual("양연준 학생", normalize_name_separator_label("  양연준\n 학생  "))
        self.assertEqual("", normalize_name_separator_label(" \t "))
        with self.assertRaisesRegex(ValueError, "제어 문자"):
            normalize_name_separator_label("양연준\x00")
        with self.assertRaisesRegex(ValueError, "이내"):
            normalize_name_separator_label("가" * (MAX_NAME_SEPARATOR_LABEL_LENGTH + 1))

    def test_materialized_pdf_is_cached_portrait_a4_with_top_centered_korean_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            first = materialize_name_separator_pdf("양연준", output_dir)
            first_mtime = first.stat().st_mtime_ns
            second = materialize_name_separator_pdf(" 양연준 ", output_dir)
            other = materialize_name_separator_pdf("한동훈", output_dir)

            self.assertEqual(first, second)
            self.assertEqual(first_mtime, second.stat().st_mtime_ns)
            self.assertNotEqual(first, other)
            reader = PdfReader(str(first))
            self.assertEqual(1, len(reader.pages))
            page = reader.pages[0]
            page_width = float(page.mediabox.width)
            page_height = float(page.mediabox.height)
            self.assertAlmostEqual(595.2756, page_width, places=2)
            self.assertAlmostEqual(841.8898, page_height, places=2)
            self.assertEqual("양연준", (page.extract_text() or "").strip())

            text_runs: list[tuple[str, list[float], float]] = []

            def collect_text(
                text: str,
                _current_matrix: list[float],
                text_matrix: list[float],
                _font_dictionary: object,
                font_size: float,
            ) -> None:
                if text.strip():
                    text_runs.append((text.strip(), text_matrix, font_size))

            page.extract_text(visitor_text=collect_text)
            name_text, text_matrix, font_size = next(
                run for run in text_runs if run[0] == "양연준"
            )
            self.assertEqual("양연준", name_text)
            self.assertAlmostEqual(MAX_FONT_SIZE, font_size, places=1)
            self.assertGreater(text_matrix[4], page_width * 0.30)
            self.assertLess(text_matrix[4], page_width * 0.50)
            self.assertGreater(text_matrix[5], page_height * 0.90)

    def test_blank_name_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "이름을 입력"):
                materialize_name_separator_pdf("", Path(temp_dir))


class NameSeparatorWorkflowTest(unittest.TestCase):
    def test_ctrl_enter_commits_ime_then_defers_variable_read(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.keyboard_latches = set()
        composed_value = FakeEntry("한동")
        app.root = RecordingRoot(composed_value)
        captured: list[object] = []
        command = Mock(side_effect=lambda: captured.append(composed_value.get()))

        with (
            patch("unit_print_manager.take_windows_ime_composition", return_value=""),
            patch("unit_print_manager.complete_windows_ime_composition", return_value=True) as commit_ime,
        ):
            result = app.run_keyboard_after_ime("save-editor", command)
            duplicate = app.run_keyboard_after_ime("save-editor", command)
            self.assertEqual("break", result)
            self.assertEqual("break", duplicate)
            commit_ime.assert_called_once_with()
            self.assertEqual(0, app.root.focus_calls)
            command.assert_not_called()

            # Several polls still see the stale value. The command must wait
            # until the IME publishes the final syllable and it remains stable.
            for _ in range(6):
                app.root.run_next(IME_SHORTCUT_DELAY_MS)
                command.assert_not_called()
            composed_value.set("한동훈")
            for _ in range(2):
                app.root.run_next(IME_SHORTCUT_DELAY_MS)

        command.assert_called_once_with()
        self.assertEqual(["한동훈"], captured)

    def test_ctrl_enter_runs_on_next_ui_tick_when_no_ime_is_active(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.keyboard_latches = set()
        entry = FakeEntry("한동훈")
        app.root = RecordingRoot(entry)
        command = Mock()

        with (
            patch("unit_print_manager.take_windows_ime_composition", return_value=""),
            patch("unit_print_manager.complete_windows_ime_composition", return_value=False),
        ):
            app.run_keyboard_after_ime("save-editor", command)
            command.assert_not_called()
            app.root.run_next(0)

        command.assert_called_once_with()

    def test_ctrl_enter_synchronously_takes_final_ime_syllable(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.keyboard_latches = set()
        app.name_ime_after_ids = {}
        entry = FakeEntry("한동")
        app.root = RecordingRoot(entry)
        captured: list[object] = []
        command = Mock(side_effect=lambda: captured.append(entry.get()))

        with (
            patch("unit_print_manager.take_windows_ime_composition", return_value="훈") as take_ime,
            patch("unit_print_manager.complete_windows_ime_composition") as complete_ime,
        ):
            result = app.run_keyboard_after_ime(
                "save-editor",
                command,
                source_widget=entry,
            )
            self.assertEqual("break", result)
            self.assertEqual("한동훈", entry.get())
            take_ime.assert_called_once_with(entry.window_handle)
            complete_ime.assert_not_called()

            app.root.run_next(0)

        command.assert_called_once_with()
        self.assertEqual(["한동훈"], captured)

    def test_ctrl_press_synchronously_finishes_student_name(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.root = RecordingRoot()
        app.name_ime_after_ids = {}
        entry = FakeEntry("한동")
        event = Mock(widget=entry)

        with patch("unit_print_manager.take_windows_ime_composition", return_value="훈"):
            app.handle_name_ime_modifier_press(event)

        self.assertEqual("한동훈", entry.get())

    def test_student_name_ime_commits_on_idle_and_focus_exit(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.root = RecordingRoot()
        app.name_ime_after_ids = {}
        entry = FakeEntry("한동")

        with patch("unit_print_manager.complete_windows_ime_composition", return_value=True) as commit:
            app.schedule_name_ime_commit(entry)
            delayed = [
                callback
                for delay, callback in app.root.callbacks
                if delay == NAME_IME_IDLE_COMMIT_MS
            ]
            self.assertEqual(1, len(delayed))
            delayed[0]()
            commit.assert_called_once_with(entry.window_handle)

            app.name_ime_after_ids[str(entry)] = "pending-focusout"
            self.assertTrue(app.commit_name_entry_ime(entry))

        self.assertIn("pending-focusout", app.root.cancelled)
        self.assertEqual(
            [
                call(entry.window_handle),
                call(entry.window_handle),
            ],
            commit.call_args_list,
        )

    def test_student_name_focus_applies_matching_ime_font(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.ui_font_family = "Pretendard"
        entry = FakeEntry("")
        event = Mock(widget=entry)

        with (
            patch("unit_print_manager.activate_windows_korean_input") as activate_korean,
            patch("unit_print_manager.set_windows_ime_composition_font") as set_font,
        ):
            app.handle_name_ime_focus_in(event)

        activate_korean.assert_called_once_with(entry.window_handle)
        set_font.assert_called_once_with(entry.window_handle, "Pretendard", 11)

    def test_new_block_name_is_normalized_and_cleared_after_use(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.name_separator_page_var = FakeVariable(True)
        app.new_block_name_var = FakeVariable("  한동훈  ")
        block = make_block("")

        self.assertIs(block, app.apply_new_block_name(block))
        self.assertEqual("한동훈", block.name_separator_label)
        app.clear_new_block_name()
        self.assertEqual("", app.new_block_name_var.get())

        app.name_separator_page_var = FakeVariable(False)
        app.new_block_name_var = FakeVariable("장규리")
        disabled_block = make_block("")
        app.apply_new_block_name(disabled_block)
        self.assertEqual("", disabled_block.name_separator_label)

    def test_student_name_entry_style_tracks_name_separator_toggle(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.name_separator_page_var = FakeVariable(False)
        app.new_block_name_entries = [Mock()]
        app.builder_notebook = Mock()
        app.builder_notebook.index.return_value = 0
        app.selected_block_index = Mock(return_value=None)
        app.blocks = []

        app.refresh_name_separator_editor_state()

        app.new_block_name_entries[0].configure.assert_called_with(
            state="disabled",
            style="StudentNameOff.TEntry",
        )

        app.name_separator_page_var.set(True)
        app.refresh_name_separator_editor_state()

        app.new_block_name_entries[0].configure.assert_called_with(
            state="normal",
            style="StudentNameOn.TEntry",
        )

    def test_fixed_blank_review_and_omr_are_exempt_from_student_name_separators(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        first = make_block("한동훈", 1)
        blank_review = make_block("", 2)
        blank_review.source = "blank_review"
        omr = make_block("", 3)
        omr.source = "omr"
        last = make_block("한동훈", 4)
        blocks = [first, blank_review, omr, last]

        with patch(
            "unit_print_manager.materialize_name_separator_pdf",
            side_effect=lambda label, _directory: Path(f"이름-{label}.pdf"),
        ):
            jobs = app.build_print_jobs(blocks, include_name_separator_pages=True)

        self.assertEqual(
            ("한동훈", None, None, "한동훈"),
            name_separator_group_labels(blocks),
        )
        self.assertEqual(2, name_separator_group_count(blocks))
        self.assertEqual(
            ["이름 구분지 - 한동훈", "이름 구분지 - 한동훈"],
            [job.item.name for job in jobs if job.is_name_separator],
        )

        app.preflight_pdf_paths = Mock()
        with patch("unit_print_manager.materialize_name_separator_pdf", return_value=Path("이름.pdf")):
            app.validate_print_blocks(blocks, require_name_separator_labels=True)
        app.preflight_pdf_paths.assert_called_once()

    def test_fixed_blank_review_add_does_not_consume_shared_student_name(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        block = make_block("")
        block.source = "blank_review"
        app.blank_review_copies_var = FakeVariable(2)
        app.new_block_name_var = FakeVariable("다음 학생")
        app.blocks = []
        app.block_status_by_id = {}
        app.make_blank_review_block = Mock(return_value=block)
        app.push_undo_state = Mock()
        app.update_queue_view = Mock()
        app.update_preview = Mock()
        app.log = Mock()

        self.assertTrue(app.add_blank_review_block())

        self.assertEqual("", block.name_separator_label)
        self.assertEqual("다음 학생", app.new_block_name_var.get())
        self.assertEqual(1, app.blank_review_copies_var.get())

    def test_name_separator_precedes_each_consecutive_name_group(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        blocks = [make_block("한동훈", 1), make_block(" 한동훈 ", 2), make_block("장규리", 3)]

        with (
            patch(
                "unit_print_manager.materialize_name_separator_pdf",
                side_effect=lambda label, _directory: Path(f"이름-{label}.pdf"),
            ),
            patch("unit_print_manager.ensure_separator_pdf", return_value=Path("고정-구분지.pdf")),
        ):
            jobs = app.build_print_jobs(
                blocks,
                include_separator_pages=True,
                include_name_separator_pages=True,
            )

        self.assertEqual(
            [
                "이름 구분지 - 한동훈",
                "본문 1",
                "구분지 고정 표지",
                "본문 2",
                "구분지 고정 표지",
                "이름 구분지 - 장규리",
                "본문 3",
                "구분지 고정 표지",
            ],
            [job.item.name for job in jobs],
        )
        self.assertEqual([0, 5], [index for index, job in enumerate(jobs) if job.is_name_separator])
        self.assertTrue(all(job.is_separator for job in jobs if job.is_name_separator))
        self.assertTrue(all(job.copies == 1 for job in jobs if job.is_name_separator))

    def test_same_name_across_apex_and_unit_blocks_prints_one_name_separator(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        apex = make_block("한동훈", 1)
        apex.source = "material_apex"
        apex.display_label = "APEX 블록"
        unit = make_block(" 한동훈 ", 2)
        unit.source = "catalog"
        unit.display_label = "단원평가 블록"

        with patch(
            "unit_print_manager.materialize_name_separator_pdf",
            return_value=Path("이름-한동훈.pdf"),
        ):
            jobs = app.build_print_jobs(
                [apex, unit],
                include_name_separator_pages=True,
            )

        self.assertEqual(("한동훈", None), name_separator_group_labels([apex, unit]))
        self.assertEqual(1, name_separator_group_count([apex, unit]))
        self.assertEqual(
            ["이름 구분지 - 한동훈", "본문 1", "본문 2"],
            [job.item.name for job in jobs],
        )
        self.assertEqual(1, sum(job.is_name_separator for job in jobs))

    def test_school_and_ilsan_blocks_insert_one_landscape_omr_after_group_name(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.printer_var = FakeVariable("iR-ADV 8585 III")
        school_first = make_block("성이름", 1)
        school_first.source = TEST_SOURCE_BY_KIND["학군지기출"]
        school_first.copies = 3
        school_second = make_block("성이름", 2)
        school_second.source = TEST_SOURCE_BY_KIND["학군지기출"]
        ilsan = make_block("성이름", 3)
        ilsan.source = TEST_SOURCE_BY_KIND["일산기출"]
        blocks = [school_first, school_second, ilsan]

        with (
            patch(
                "unit_print_manager.materialize_name_separator_pdf",
                return_value=Path("이름-성이름.pdf"),
            ),
            patch("unit_print_manager.find_omr_pdf", return_value=Path("OMR 내신.pdf")),
        ):
            jobs = app.build_print_jobs(blocks, include_name_separator_pages=True)

        self.assertEqual(
            [
                "이름 구분지 - 성이름",
                "OMR 내신",
                "본문 1",
                "OMR 내신",
                "본문 2",
                "OMR 내신",
                "본문 3",
            ],
            [job.item.name for job in jobs],
        )
        omr_jobs = [job for job in jobs if job.is_omr]
        self.assertEqual(3, len(omr_jobs))
        self.assertTrue(all(job.copies == 1 for job in omr_jobs))
        self.assertTrue(all(job.pages_per_sheet == 1 for job in omr_jobs))
        self.assertTrue(
            all(job.printer_override == "iR-ADV 8585 III - 가로" for job in omr_jobs)
        )
        self.assertEqual(1, sum(job.is_name_separator for job in jobs))

    def test_other_test_blocks_do_not_receive_automatic_omr(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.printer_var = FakeVariable("iR-ADV 8585 III")
        unit = make_block("학생", 1)
        unit.source = "catalog"
        review = make_block("학생", 2)
        review.source = TEST_SOURCE_BY_KIND["복습테스트"]

        jobs = app.build_print_jobs([unit, review])

        self.assertFalse(any(job.is_omr for job in jobs))
        self.assertEqual(["본문 1", "본문 2"], [job.item.name for job in jobs])

    def test_automatic_omr_is_included_in_print_preflight(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.preflight_pdf_paths = Mock()
        block = make_block("학생")
        block.source = TEST_SOURCE_BY_KIND["학군지기출"]
        omr_path = Path("OMR 내신.pdf")

        with patch("unit_print_manager.find_omr_pdf", return_value=omr_path):
            app.validate_print_blocks([block], require_name_separator_labels=False)

        checked_paths = app.preflight_pdf_paths.call_args.args[0]
        self.assertEqual([Path("본문 1.pdf"), omr_path], checked_paths)

    def test_same_name_after_another_student_starts_a_new_group(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        blocks = [
            make_block("한동훈", 1),
            make_block("한동훈", 2),
            make_block("장규리", 3),
            make_block("한동훈", 4),
        ]

        with patch(
            "unit_print_manager.materialize_name_separator_pdf",
            side_effect=lambda label, _directory: Path(f"이름-{label}.pdf"),
        ):
            jobs = app.build_print_jobs(blocks, include_name_separator_pages=True)

        self.assertEqual(
            ["이름 구분지 - 한동훈", "이름 구분지 - 장규리", "이름 구분지 - 한동훈"],
            [job.item.name for job in jobs if job.is_name_separator],
        )
        self.assertEqual(("한동훈", None, "장규리", "한동훈"), name_separator_group_labels(blocks))
        self.assertEqual(3, name_separator_group_count(blocks))

    def test_active_queue_append_reuses_the_preceding_name_group(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        blocks = [make_block("한동훈", 2), make_block("한동훈", 3), make_block("장규리", 4)]

        with patch(
            "unit_print_manager.materialize_name_separator_pdf",
            side_effect=lambda label, _directory: Path(f"이름-{label}.pdf"),
        ):
            jobs = app.build_print_jobs(
                blocks,
                include_name_separator_pages=True,
                start_block_index=2,
                preceding_name_separator_label="한동훈",
            )

        self.assertEqual(
            ["본문 2", "본문 3", "이름 구분지 - 장규리", "본문 4"],
            [job.item.name for job in jobs],
        )
        self.assertEqual(1, name_separator_group_count(blocks, "한동훈"))

    def test_disabled_name_separator_does_not_generate_name_pdfs(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        with patch("unit_print_manager.materialize_name_separator_pdf") as materialize:
            jobs = app.build_print_jobs([make_block("양연준")])
        materialize.assert_not_called()
        self.assertEqual(["본문 1"], [job.item.name for job in jobs])

    def test_validation_reports_blocks_without_names(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.preflight_pdf_paths = Mock()
        blocks = [make_block("양연준", 1), make_block("", 2), make_block("", 3)]

        with self.assertRaisesRegex(ValueError, "2, 3"):
            app.validate_print_blocks(blocks, require_name_separator_labels=True)
        app.preflight_pdf_paths.assert_not_called()

    def test_config_round_trip_preserves_name(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.printer_var = FakeVariable("iR-ADV 8585 III")
        original = PrintBlock(
            subject="새 PDF",
            level="",
            round_name="직접",
            start=1,
            end=1,
            copies=1,
            files=[ExamPdf(1, "본문", Path("본문.pdf"))],
            source="custom",
            name_separator_label="양연준",
        )

        restored = app.block_from_config(app.block_to_config(original))

        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual("양연준", restored.name_separator_label)


if __name__ == "__main__":
    unittest.main()
