from __future__ import annotations

import hashlib
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Callable, cast
from unittest.mock import Mock, patch

from pypdf import PdfReader, PdfWriter

from unit_print_manager import (
    ExamPdf,
    PrintBlock,
    PrintCancelled,
    PrintJob,
    PrintManagerApp,
    PrintRunOptions,
    able_print_composition_settings,
    parse_file_number_list,
    resolve_custom_pdf_printer_profile,
    resolve_exam_print_settings,
)
from unit_print_materials import (
    ABLE_PROFILE_NAME,
    APEX_PROFILE_NAME,
    AbleCatalog,
    AbleSubject,
    ApexCatalog,
    ApexDocument,
    PageSpan,
    TrainingPackCatalog,
    TrainingPackSubject,
    apex_boost_lesson_page_spans,
    apex_manual_lesson_page_spans,
)
from unit_print_pdf_printing import preflight_pdfs
from unit_print_textbooks import (
    TextbookCoreSection,
    TextbookEdition,
    TextbookLesson,
    TextbookPdfAsset,
)


class FakeVariable:
    def __init__(self, value: object) -> None:
        self.value = value

    def get(self) -> object:
        return self.value


class ImmediateRoot:
    def after(self, _delay: int, callback: Callable[[], None]) -> None:
        callback()


def write_pdf(path: Path, page_count: int = 1) -> None:
    writer = PdfWriter()
    for _page_number in range(page_count):
        writer.add_blank_page(width=595, height=842)
    with path.open("wb") as pdf_file:
        writer.write(pdf_file)


def make_job(path: Path, number: int) -> PrintJob:
    return PrintJob(
        block_index=1,
        block_total=1,
        block_uid=101,
        block_summary="테스트 블록",
        item=ExamPdf(number, path.stem, path),
        copies=1,
    )


class PrintManagerWorkflowTest(unittest.TestCase):
    def test_textbook_variant_block_adds_cover_once_and_selected_problem_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = directory / "핵심문제 변형.pdf"
            write_pdf(source, page_count=6)
            asset = TextbookPdfAsset(
                Path(source.name), source, 6, source.stat().st_size,
                hashlib.sha256(source.read_bytes()).hexdigest(),
            )
            edition = TextbookEdition(
                subject="공수2", publisher="YBM", mapping_status="verified",
                problem=asset, solution=asset,
                lessons=(
                    TextbookLesson(1, "첫 파트", ((1, 1),), ((1, 1),)),
                    TextbookLesson(2, "둘째 파트", ((2, 2),), ((2, 2),)),
                ),
                core=asset,
                core_sections=(
                    TextbookCoreSection(1, 1, 1, 2, "중단원"),
                    TextbookCoreSection(2, 2, 3, 4, "중단원"),
                ),
                variant=asset,
                variant_problem_page_ranges=((3, 3), (3, 4), (5, 6)),
                variant_section_problem_counts=(2, 1),
            )
            app = PrintManagerApp.__new__(PrintManagerApp)
            test_app = cast(Any, app)
            test_app.printer_var = FakeVariable("iR-ADV 8585 III")
            with patch("unit_print_manager.MATERIAL_RANGE_CACHE_DIR", directory / "ranges"):
                block = app.make_textbook_variant_block_for_selection(
                    edition, (1, 2), copies=2, selected_numbers=(1, 2)
                )
            self.assertEqual("material_textbook_variant", block.source)
            self.assertEqual((1, 2), block.selected_numbers)
            self.assertEqual("세로", block.printer_route)
            self.assertEqual(6, len(PdfReader(block.files[0].path).pages))

    def test_textbook_core_block_materializes_medium_and_major_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            core = directory / "공수1 동아출판 핵심문제.pdf"
            problem = directory / "공수1 동아출판 문제.pdf"
            solution = directory / "공수1 동아출판 해설.pdf"
            for path, pages in ((core, 4), (problem, 2), (solution, 2)):
                write_pdf(path, page_count=pages)
            edition = TextbookEdition(
                subject="공수1",
                publisher="동아출판",
                mapping_status="verified",
                problem=TextbookPdfAsset(Path(problem.name), problem, 2, problem.stat().st_size, "0" * 64),
                solution=TextbookPdfAsset(Path(solution.name), solution, 2, solution.stat().st_size, "0" * 64),
                lessons=(
                    TextbookLesson(1, "첫 파트", ((1, 1),), ((1, 1),)),
                    TextbookLesson(2, "둘째 파트", ((2, 2),), ((2, 2),)),
                ),
                core=TextbookPdfAsset(Path(core.name), core, 4, core.stat().st_size, "0" * 64),
                core_sections=(
                    TextbookCoreSection(1, 1, 1, 1, "중단원"),
                    TextbookCoreSection(2, 2, 2, 2, "중단원"),
                    TextbookCoreSection(1, 2, 3, 4, "대단원"),
                ),
            )
            app = PrintManagerApp.__new__(PrintManagerApp)
            test_app = cast(Any, app)
            test_app.printer_var = FakeVariable("iR-ADV 8585 III")

            with patch("unit_print_manager.MATERIAL_RANGE_CACHE_DIR", directory / "ranges"):
                block = app.make_textbook_block_for_selection(
                    edition, "핵심문제", (edition.lessons[0],), copies=1
                )

            self.assertEqual("핵심문제", block.round_name)
            self.assertEqual("material_textbook", block.source)
            self.assertEqual(3, len(PdfReader(block.files[0].path).pages))

    def test_textbook_block_preserves_individual_part_order_and_portrait_route(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            problem = directory / "공수1 동아출판 문제.pdf"
            solution = directory / "공수1 동아출판 해설.pdf"
            write_pdf(problem, page_count=8)
            write_pdf(solution, page_count=4)
            edition = TextbookEdition(
                subject="공수1",
                publisher="동아출판",
                mapping_status="verified",
                problem=TextbookPdfAsset(
                    Path(problem.name), problem, 8, problem.stat().st_size, "0" * 64
                ),
                solution=TextbookPdfAsset(
                    Path(solution.name), solution, 4, solution.stat().st_size, "0" * 64
                ),
                lessons=(
                    TextbookLesson(1, "첫 파트", ((1, 2),), ((1, 1),)),
                    TextbookLesson(2, "둘째 파트", ((3, 4),), ((2, 2),)),
                    TextbookLesson(3, "셋째 파트", ((5, 6),), (), solution_unavailable_reason="원본 누락"),
                ),
            )
            app = PrintManagerApp.__new__(PrintManagerApp)
            test_app = cast(Any, app)
            test_app.printer_var = FakeVariable("iR-ADV 8585 III")

            with patch("unit_print_manager.MATERIAL_RANGE_CACHE_DIR", directory / "ranges"):
                block = app.make_textbook_block_for_selection(
                    edition,
                    "문제",
                    (edition.lessons[2], edition.lessons[0]),
                    copies=2,
                    selected_numbers=(3, 1),
                )

            self.assertEqual("material_textbook", block.source)
            self.assertEqual("동아출판", block.level)
            self.assertEqual("문제", block.round_name)
            self.assertEqual((3, 1), block.selected_numbers)
            self.assertEqual("세로", block.printer_route)
            self.assertEqual(4, len(PdfReader(block.files[0].path).pages))
            self.assertIn("(3,1)", block.display_label)

            restored = app.block_from_config(app.block_to_config(block))
            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual("material_textbook", restored.source)
            self.assertEqual((3, 1), restored.selected_numbers)

    def test_textbook_selection_never_silently_skips_incomplete_in_memory_data(self) -> None:
        edition = TextbookEdition(
            subject="공수1",
            publisher="테스트판",
            mapping_status="verified",
            problem=cast(Any, object()),
            solution=cast(Any, object()),
            lessons=(
                TextbookLesson(
                    1,
                    "테스트 파트",
                    ((1, 2),),
                    (),
                    solution_unavailable_reason="테스트 원본 해설 누락",
                ),
            ),
        )
        app = PrintManagerApp.__new__(PrintManagerApp)
        test_app = cast(Any, app)
        test_app.textbook_selected_numbers_var = FakeVariable("1")
        test_app.textbook_start_var = FakeVariable("01. 테스트 파트")
        test_app.textbook_end_var = FakeVariable("01. 테스트 파트")

        with self.assertRaisesRegex(ValueError, "테스트 원본 해설 누락"):
            app.selected_textbook_lessons(edition, "해설")

    def test_textbook_pending_status_names_deferred_editions(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        test_app = cast(Any, app)
        test_app.textbook_catalog = type(
            "Catalog",
            (),
            {
                "subjects": (
                    type("Subject", (), {"editions": (
                        type("Edition", (), {
                            "is_verified": False,
                            "subject": "기하",
                            "publisher": "천재(전인태)",
                        })(),
                    )})(),
                    type("Subject", (), {"editions": (
                        type("Edition", (), {
                            "is_verified": False,
                            "subject": "확통",
                            "publisher": "천재(전인태)",
                        })(),
                    )})(),
                )
            },
        )()

        self.assertEqual(
            " · 원본 대기 2판본: 기하/천재(전인태), 확통/천재(전인태)",
            app.textbook_pending_status_note(),
        )

    def test_pdf_preflight_cache_skips_unchanged_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "cached.pdf"
            write_pdf(source)
            app = PrintManagerApp.__new__(PrintManagerApp)
            app.pdf_preflight_by_path = {}
            app.pdf_preflight_signature_by_path = {}

            with patch("unit_print_manager.preflight_pdfs", wraps=preflight_pdfs) as inspect:
                app.preflight_pdf_paths([source, source])
                app.preflight_pdf_paths([source])

            inspect.assert_called_once_with([source])

    def test_warmed_printer_inventory_avoids_a_powershell_query(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.printer_inventory_lock = threading.Lock()
        app.printer_inventory_cache = frozenset({"세로", "가로"})
        app.printer_inventory_cached_at = time.monotonic()

        with patch("unit_print_manager.load_printers") as load:
            available = app.cached_printer_inventory()

        self.assertEqual(frozenset({"세로", "가로"}), available)
        load.assert_not_called()

    def test_able_print_composition_dropdown_maps_all_five_choices(self) -> None:
        self.assertEqual(
            [
                ("풀 세트", "전체 묶음"),
                ("차시별", "전체 묶음"),
                ("차시별", "교재"),
                ("차시별", "백지리뷰"),
                ("풀 세트", "개념정리"),
            ],
            [
                able_print_composition_settings(choice)
                for choice in ("풀세트", "교재 + 백지리뷰", "교재", "백지리뷰", "개념정리")
            ],
        )

    def test_file_number_list_parser_accepts_parenthesized_non_contiguous_input(self) -> None:
        self.assertEqual((1, 3, 4, 5), parse_file_number_list("(1, 3, 4, 5)"))
        self.assertEqual((2, 7), parse_file_number_list("２，７"))
        self.assertEqual((5, 3, 1), parse_file_number_list("5; 3; 1"))
        self.assertEqual((), parse_file_number_list(""))

    def test_file_number_list_parser_expands_mixed_ranges_with_optional_spaces(self) -> None:
        expected = (1, 2, 3, 6, 7, 8, 9)

        self.assertEqual(expected, parse_file_number_list("1-3, 6, 7-9"))
        self.assertEqual(expected, parse_file_number_list("1-3,6,7-9"))
        self.assertEqual((1, 2, 3, 5), parse_file_number_list("(１－３，５)"))

    def test_file_number_list_parser_rejects_ambiguous_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "괄호"):
            parse_file_number_list("(1,3,4,5")
        with self.assertRaisesRegex(ValueError, "1-5"):
            parse_file_number_list("1--3")
        with self.assertRaisesRegex(ValueError, "작은 번호"):
            parse_file_number_list("5-3")
        with self.assertRaisesRegex(ValueError, "중복"):
            parse_file_number_list("1-3,3-5")

    def test_non_contiguous_catalog_selection_preserves_file_order_and_config(self) -> None:
        files = [ExamPdf(number, f"파일 {number}", Path(f"파일 {number}.pdf")) for number in range(1, 6)]
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.exam_files = files
        app.unit_selected_numbers_var = FakeVariable("1-3,5")
        app.start_var = FakeVariable("01. 파일 1")
        app.end_var = FakeVariable("05. 파일 5")
        app.block_copies_var = FakeVariable(1)
        app.bundle_var = FakeVariable(False)
        app.subject_var = FakeVariable("공통수학1")
        app.level_var = FakeVariable("Lv.3")
        app.round_var = FakeVariable("1")

        block = app.make_block_from_form()

        self.assertEqual((1, 2, 3, 5), block.selected_numbers)
        self.assertEqual([1, 2, 3, 5], [item.number for item in block.files])
        self.assertEqual("(1,2,3,5)", block.range_label)

        app.catalog = object()
        app.root_dir_var = FakeVariable(".")
        with patch("unit_print_manager.catalog_exam_files", return_value=files):
            restored = app.block_from_config(app.block_to_config(block))

        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual((1, 2, 3, 5), restored.selected_numbers)
        self.assertEqual([1, 2, 3, 5], [item.number for item in restored.files])

    def test_queue_editor_preserves_and_updates_non_contiguous_catalog_selection(self) -> None:
        files = [ExamPdf(number, f"파일 {number}", Path(f"파일 {number}.pdf")) for number in range(1, 10)]
        original = PrintBlock(
            "공통수학1",
            "Lv.3",
            "1",
            3,
            9,
            1,
            [files[index - 1] for index in (3, 4, 7, 9)],
            selected_numbers=(3, 4, 7, 9),
        )
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.blocks = [original]
        app.selected_block_index_value = 0
        app.edit_exam_files = files
        app.edit_subject_var = FakeVariable("공통수학1")
        app.edit_level_var = FakeVariable("Lv.3")
        app.edit_round_var = FakeVariable("1")
        app.edit_start_var = FakeVariable("03. 파일 3")
        app.edit_end_var = FakeVariable("09. 파일 9")
        app.edit_selected_numbers_var = FakeVariable("(3,4,7,9)")
        app.edit_copies_var = FakeVariable(1)
        app.edit_bundle_var = FakeVariable(False)

        restored = app.make_block_from_editor()

        self.assertEqual((3, 4, 7, 9), restored.selected_numbers)
        self.assertEqual([3, 4, 7, 9], [item.number for item in restored.files])

        app.edit_selected_numbers_var = FakeVariable("")
        ranged = app.make_block_from_editor()
        self.assertIsNone(ranged.selected_numbers)
        self.assertEqual(list(range(3, 10)), [item.number for item in ranged.files])

    def test_queue_editor_updates_non_contiguous_apex_selection_and_label(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = directory / "APEX 공통수학1.pdf"
            write_pdf(source, page_count=6)
            lessons = tuple(
                PageSpan(number, f"{number}차시", number, number)
                for number in range(1, 7)
            )
            document = ApexDocument(
                variant="APEX",
                subject="공통수학1",
                document_kind="교재",
                path=source,
                page_count=6,
                parts=(),
                lessons=lessons,
            )
            original = PrintBlock(
                subject="공통수학1",
                level="APEX",
                round_name="교재",
                start=1,
                end=6,
                copies=1,
                files=[ExamPdf(1, "기존", source)],
                source="material_apex",
                display_label="APEX / 공통수학1 / 교재 / 차시 별 / (1,3,4,6)",
                selected_numbers=(1, 3, 4, 6),
                material_kind="차시 별",
            )
            app = PrintManagerApp.__new__(PrintManagerApp)
            test_app = cast(Any, app)
            test_app.apex_material_catalog = ApexCatalog((document,))
            test_app.blocks = [original]
            test_app.selected_block_index_value = 0
            test_app.edit_selected_numbers_var = FakeVariable("1-2, 5")
            test_app.edit_copies_var = FakeVariable(1)
            test_app.edit_bundle_var = FakeVariable(False)

            with (
                patch("unit_print_manager.MATERIAL_RANGE_CACHE_DIR", directory / "ranges"),
                patch("unit_print_manager.apex_manual_page_numbers", side_effect=ValueError),
            ):
                block = app.make_block_from_editor()

            self.assertEqual((1, 2, 5), block.selected_numbers)
            self.assertEqual((1, 5), (block.start, block.end))
            self.assertIn("(1,2,5)", block.display_label)
            self.assertEqual("차시 별", block.material_kind)

    def test_new_print_filter_excludes_blocks_with_existing_print_commands(self) -> None:
        item = ExamPdf(1, "테스트", Path("테스트.pdf"))
        pending = PrintBlock("대기", "", "", 1, 1, 1, [item])
        completed = PrintBlock("완료", "", "", 1, 1, 1, [item])
        active = PrintBlock("진행", "", "", 1, 1, 1, [item])
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.blocks = [pending, completed, active]
        app.block_status_by_id = {
            id(pending): "대기",
            id(completed): "완료",
            id(active): "진행 중",
        }

        self.assertEqual([pending], app.blocks_waiting_for_print())

    def test_custom_pdf_route_selection_resolves_to_profile_queues(self) -> None:
        base = "iR-ADV 8585 III"

        self.assertIsNone(resolve_custom_pdf_printer_profile(base, "자동 감지"))
        self.assertEqual("iR-ADV 8585 III - 가로", resolve_custom_pdf_printer_profile(base, "가로"))
        self.assertEqual("iR-ADV 8585 III - 세로", resolve_custom_pdf_printer_profile(base, "세로"))
        self.assertEqual("iR-ADV 8585 III -A:ble", resolve_custom_pdf_printer_profile(base, "A:ble"))
        self.assertEqual(
            "iR-ADV 8585 III - 가로",
            resolve_custom_pdf_printer_profile("iR-ADV 8585 III - 세로", "가로"),
        )

    def test_apex_boost_lesson_block_interleaves_body_and_quick_answers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = directory / "APEX Boost 대수.pdf"
            write_pdf(source, page_count=100)
            document = ApexDocument(
                variant="APEX Boost",
                subject="대수",
                document_kind="교재",
                path=source,
                page_count=100,
                parts=(PageSpan(1, "전체", 1, 90),),
                lessons=apex_boost_lesson_page_spans("대수"),
            )
            app = PrintManagerApp.__new__(PrintManagerApp)
            test_app = cast(Any, app)
            test_app.apex_material_catalog = ApexCatalog((document,))
            test_app.apex_variant_var = FakeVariable("APEX Boost")
            test_app.apex_subject_var = FakeVariable("대수")
            test_app.apex_document_kind_var = FakeVariable("교재")
            test_app.apex_part_var = FakeVariable("전체")
            test_app.apex_start_var = FakeVariable("02. 로그")
            test_app.apex_end_var = FakeVariable("03. 지수함수")
            test_app.apex_copies_var = FakeVariable(1)

            with patch("unit_print_manager.MATERIAL_RANGE_CACHE_DIR", directory / "ranges"):
                block = app.make_apex_material_block()

            self.assertIn("본문+빠른정답", block.display_label)
            self.assertEqual(19, len(PdfReader(block.files[0].path).pages))
            self.assertEqual("세로", block.printer_route)

    def test_training_pack_lesson_block_materializes_problem_pages_portrait(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = directory / "공수1 트팩.pdf"
            write_pdf(source, page_count=100)
            subject = TrainingPackSubject(
                "공통수학1",
                source,
                100,
                (
                    PageSpan(1, "다항식의 연산", 2, 9),
                    PageSpan(2, "항등식과 나머지정리", 10, 16),
                    PageSpan(3, "인수분해", 17, 22),
                ),
            )
            app = PrintManagerApp.__new__(PrintManagerApp)
            test_app = cast(Any, app)
            test_app.training_pack_material_catalog = TrainingPackCatalog((subject,))
            test_app.training_pack_subject_var = FakeVariable("공통수학1")
            test_app.training_pack_mode_var = FakeVariable("차시별")
            test_app.training_pack_start_var = FakeVariable("02. 항등식과 나머지정리")
            test_app.training_pack_end_var = FakeVariable("03. 인수분해")
            test_app.training_pack_selected_numbers_var = FakeVariable("2-3")
            test_app.training_pack_copies_var = FakeVariable(1)
            test_app.printer_var = FakeVariable("iR-ADV 8585 III")

            with patch("unit_print_manager.MATERIAL_RANGE_CACHE_DIR", directory / "ranges"):
                block = app.make_training_pack_material_block()

            self.assertEqual("material_training_pack", block.source)
            self.assertEqual((2, 3), block.selected_numbers)
            self.assertEqual("세로", block.printer_route)
            self.assertEqual(13, len(PdfReader(block.files[0].path).pages))
            self.assertIn("(2,3)", block.display_label)

            restored = app.block_from_config(app.block_to_config(block))
            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual("material_training_pack", restored.source)
            self.assertEqual("세로", restored.printer_route)

    def test_training_pack_full_set_uses_the_original_pdf_from_page_one(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = directory / "공수1 트팩.pdf"
            write_pdf(source, page_count=100)
            subject = TrainingPackSubject(
                "공통수학1",
                source,
                100,
                (PageSpan(1, "다항식의 연산", 2, 9),),
            )
            app = PrintManagerApp.__new__(PrintManagerApp)
            test_app = cast(Any, app)
            test_app.training_pack_material_catalog = TrainingPackCatalog((subject,))
            test_app.training_pack_subject_var = FakeVariable("공통수학1")
            test_app.training_pack_mode_var = FakeVariable("풀 세트")
            test_app.training_pack_copies_var = FakeVariable(1)
            test_app.printer_var = FakeVariable("iR-ADV 8585 III")

            block = app.make_training_pack_material_block()

            self.assertEqual(source, block.files[0].path)
            self.assertEqual(100, len(PdfReader(block.files[0].path).pages))
            self.assertEqual("풀 세트", block.round_name)
            self.assertEqual((1, 100), (block.start, block.end))
            self.assertEqual("세로", block.printer_route)

    def test_frequent_type_selected_lessons_materialize_as_a_portrait_block(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = directory / "대수 빈출유형.pdf"
            write_pdf(source, page_count=12)
            subject = TrainingPackSubject(
                "대수",
                source,
                12,
                (
                    PageSpan(1, "지수", 1, 4),
                    PageSpan(2, "로그", 5, 7),
                    PageSpan(3, "수열", 8, 12),
                ),
            )
            app = PrintManagerApp.__new__(PrintManagerApp)
            test_app = cast(Any, app)
            test_app.frequent_type_material_catalog = TrainingPackCatalog((subject,))
            test_app.frequent_type_subject_var = FakeVariable("대수")
            test_app.frequent_type_mode_var = FakeVariable("차시별")
            test_app.frequent_type_start_var = FakeVariable("01. 지수")
            test_app.frequent_type_end_var = FakeVariable("03. 수열")
            test_app.frequent_type_selected_numbers_var = FakeVariable("3,1")
            test_app.frequent_type_copies_var = FakeVariable(1)
            test_app.printer_var = FakeVariable("iR-ADV 8585 III")

            with patch("unit_print_manager.MATERIAL_RANGE_CACHE_DIR", directory / "ranges"):
                block = app.make_frequent_type_material_block()

            self.assertEqual("material_frequent_type", block.source)
            self.assertEqual("빈출유형", block.level)
            self.assertEqual((3, 1), block.selected_numbers)
            self.assertEqual("세로", block.printer_route)
            self.assertEqual(9, len(PdfReader(block.files[0].path).pages))
            self.assertIn("(3,1)", block.display_label)

            restored = app.block_from_config(app.block_to_config(block))
            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual("material_frequent_type", restored.source)
            self.assertEqual((3, 1), restored.selected_numbers)

    def test_apex_common_math1_lesson_block_merges_body_and_appends_quick_answers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = directory / "APEX 공통수학1.pdf"
            write_pdf(source, page_count=280)
            document = ApexDocument(
                variant="APEX",
                subject="공통수학1",
                document_kind="교재",
                path=source,
                page_count=280,
                parts=(
                    PageSpan(1, "part.1", 1, 140, "part.1"),
                    PageSpan(2, "part.2", 141, 262, "part.2"),
                ),
                lessons=apex_manual_lesson_page_spans("공통수학1"),
            )
            app = PrintManagerApp.__new__(PrintManagerApp)
            test_app = cast(Any, app)
            test_app.apex_material_catalog = ApexCatalog((document,))
            test_app.apex_variant_var = FakeVariable("APEX")
            test_app.apex_subject_var = FakeVariable("공통수학1")
            test_app.apex_document_kind_var = FakeVariable("교재")
            test_app.apex_part_var = FakeVariable("part.1")
            test_app.apex_start_var = FakeVariable("01. 01차시")
            test_app.apex_end_var = FakeVariable("02. 02차시")
            test_app.apex_copies_var = FakeVariable(1)

            with patch("unit_print_manager.MATERIAL_RANGE_CACHE_DIR", directory / "ranges"):
                block = app.make_apex_material_block()

            self.assertIn("본문+빠른정답", block.display_label)
            self.assertEqual(23, len(PdfReader(block.files[0].path).pages))
            self.assertEqual("세로", block.printer_route)

    def test_apex_part_selection_uses_whole_part_when_lesson_range_is_blank(self) -> None:
        document = ApexDocument(
            variant="APEX",
            subject="공통수학1",
            document_kind="교재",
            path=Path("APEX 공통수학1.pdf"),
            page_count=280,
            parts=(
                PageSpan(1, "part.1", 1, 140, "part.1"),
                PageSpan(2, "part.2", 141, 262, "part.2"),
            ),
            lessons=apex_manual_lesson_page_spans("공통수학1"),
        )
        app = PrintManagerApp.__new__(PrintManagerApp)
        test_app = cast(Any, app)
        test_app.apex_part_var = FakeVariable("part.1")
        test_app.apex_start_var = FakeVariable("")
        test_app.apex_end_var = FakeVariable("")

        spans = app.selected_apex_spans(document)

        self.assertEqual(24, len(spans))
        self.assertEqual("part.1", spans[0].part)
        self.assertEqual("part.1", spans[-1].part)

    def test_apex_lesson_mode_uses_start_and_end_for_all_lessons(self) -> None:
        document = ApexDocument(
            variant="APEX",
            subject="공통수학1",
            document_kind="교재",
            path=Path("APEX 공통수학1.pdf"),
            page_count=280,
            parts=(
                PageSpan(1, "part.1", 1, 140, "part.1"),
                PageSpan(2, "part.2", 141, 262, "part.2"),
            ),
            lessons=apex_manual_lesson_page_spans("공통수학1"),
        )
        app = PrintManagerApp.__new__(PrintManagerApp)
        test_app = cast(Any, app)
        test_app.apex_part_var = FakeVariable("차시 별")
        test_app.apex_start_var = FakeVariable("01. 다항식의 연산")
        test_app.apex_end_var = FakeVariable("02. 곱셈 공식의 활용")

        spans = app.selected_apex_spans(document)

        self.assertEqual((1, 2), tuple(span.number for span in spans))

    def test_apex_common_math1_block_accepts_non_contiguous_lesson_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = directory / "APEX 공통수학1.pdf"
            write_pdf(source, page_count=280)
            document = ApexDocument(
                variant="APEX",
                subject="공통수학1",
                document_kind="교재",
                path=source,
                page_count=280,
                parts=(
                    PageSpan(1, "part.1", 1, 140, "part.1"),
                    PageSpan(2, "part.2", 141, 262, "part.2"),
                ),
                lessons=apex_manual_lesson_page_spans("공통수학1"),
            )
            app = PrintManagerApp.__new__(PrintManagerApp)
            test_app = cast(Any, app)
            test_app.apex_material_catalog = ApexCatalog((document,))
            test_app.apex_variant_var = FakeVariable("APEX")
            test_app.apex_subject_var = FakeVariable("공통수학1")
            test_app.apex_document_kind_var = FakeVariable("교재")
            test_app.apex_part_var = FakeVariable("part.1")
            test_app.apex_start_var = FakeVariable("01. 01차시")
            test_app.apex_end_var = FakeVariable("05. 05차시")
            test_app.apex_selected_numbers_var = FakeVariable("1-2, 5")
            test_app.apex_copies_var = FakeVariable(1)

            with patch("unit_print_manager.MATERIAL_RANGE_CACHE_DIR", directory / "ranges"):
                block = app.make_apex_material_block()

            self.assertEqual((1, 2, 5), block.selected_numbers)
            self.assertIn("(1,2,5)", block.display_label)
            self.assertIn("본문+빠른정답", block.display_label)
            self.assertEqual(40, len(PdfReader(block.files[0].path).pages))
            self.assertEqual("세로", block.printer_route)

    def test_apex_common_math2_lesson_block_uses_part_specific_quick_answers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = directory / "APEX 공통수학2.pdf"
            write_pdf(source, page_count=183)
            document = ApexDocument(
                variant="APEX",
                subject="공통수학2",
                document_kind="교재",
                path=source,
                page_count=183,
                parts=(
                    PageSpan(1, "part.1", 1, 82, "part.1"),
                    PageSpan(2, "part.2", 83, 171, "part.2"),
                ),
                lessons=apex_manual_lesson_page_spans("공통수학2"),
            )
            app = PrintManagerApp.__new__(PrintManagerApp)
            test_app = cast(Any, app)
            test_app.apex_material_catalog = ApexCatalog((document,))
            test_app.apex_variant_var = FakeVariable("APEX")
            test_app.apex_subject_var = FakeVariable("공통수학2")
            test_app.apex_document_kind_var = FakeVariable("교재")
            test_app.apex_part_var = FakeVariable("part.1")
            test_app.apex_start_var = FakeVariable("01. 01차시")
            test_app.apex_end_var = FakeVariable("02. 02차시")
            test_app.apex_copies_var = FakeVariable(1)

            with patch("unit_print_manager.MATERIAL_RANGE_CACHE_DIR", directory / "ranges"):
                block = app.make_apex_material_block()

            self.assertIn("본문+빠른정답", block.display_label)
            self.assertEqual(6, len(PdfReader(block.files[0].path).pages))
            self.assertEqual("세로", block.printer_route)

    def test_apex_algebra_lesson_block_uses_algebra_quick_answers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = directory / "APEX 대수.pdf"
            write_pdf(source, page_count=163)
            document = ApexDocument(
                variant="APEX",
                subject="대수",
                document_kind="교재",
                path=source,
                page_count=163,
                parts=(
                    PageSpan(1, "part.1", 1, 71, "part.1"),
                    PageSpan(2, "part.2", 72, 153, "part.2"),
                ),
                lessons=apex_manual_lesson_page_spans("대수"),
            )
            app = PrintManagerApp.__new__(PrintManagerApp)
            test_app = cast(Any, app)
            test_app.apex_material_catalog = ApexCatalog((document,))
            test_app.apex_variant_var = FakeVariable("APEX")
            test_app.apex_subject_var = FakeVariable("대수")
            test_app.apex_document_kind_var = FakeVariable("교재")
            test_app.apex_part_var = FakeVariable("part.1")
            test_app.apex_start_var = FakeVariable("01. 01차시")
            test_app.apex_end_var = FakeVariable("02. 02차시")
            test_app.apex_copies_var = FakeVariable(1)

            with patch("unit_print_manager.MATERIAL_RANGE_CACHE_DIR", directory / "ranges"):
                block = app.make_apex_material_block()

            self.assertIn("본문+빠른정답", block.display_label)
            self.assertEqual(11, len(PdfReader(block.files[0].path).pages))
            self.assertEqual("세로", block.printer_route)

    def test_explicit_block_route_overrides_file_route(self) -> None:
        item = ExamPdf(
            1,
            "교재",
            Path("교재.pdf"),
            printer_override=ABLE_PROFILE_NAME,
            pages_per_sheet=4,
        )
        block = PrintBlock(
            "교재",
            "",
            "",
            1,
            1,
            1,
            [item],
            printer_override="iR-ADV 8585 III - 가로",
            printer_route="가로",
        )

        self.assertEqual(
            ("iR-ADV 8585 III - 가로", 4),
            resolve_exam_print_settings(block, item),
        )

    def test_auto_block_route_preserves_file_route(self) -> None:
        item = ExamPdf(
            1,
            "교재",
            Path("교재.pdf"),
            printer_override=ABLE_PROFILE_NAME,
            pages_per_sheet=4,
        )
        block = PrintBlock("교재", "", "", 1, 1, 1, [item])

        self.assertEqual(
            (ABLE_PROFILE_NAME, 4),
            resolve_exam_print_settings(block, item),
        )

    def test_block_printer_route_round_trips_through_config(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        original = PrintBlock(
            "직접",
            "",
            "직접",
            1,
            1,
            1,
            [ExamPdf(1, "교재", Path("교재.pdf"))],
            source="custom",
            printer_override="iR-ADV 8585 III -A:ble",
            printer_route="A:ble",
        )

        restored = app.block_from_config(app.block_to_config(original))

        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual("A:ble", restored.printer_route)
        self.assertEqual("iR-ADV 8585 III -A:ble", restored.printer_override)

    def test_able_textbook_lesson_block_uses_four_up_without_reviews(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            lesson_one = directory / "대수 1차시.pdf"
            lesson_two = directory / "대수 2차시.pdf"
            review = directory / "백지 리뷰 - 대수.pdf"
            write_pdf(lesson_one)
            write_pdf(lesson_two)
            write_pdf(review, page_count=3)
            subject = AbleSubject(
                subject="대수",
                full_documents=(),
                lesson_documents=((1, lesson_one), (2, lesson_two)),
                review_document=review,
            )
            app = PrintManagerApp.__new__(PrintManagerApp)
            test_app = cast(Any, app)
            test_app.able_material_catalog = AbleCatalog((subject,))
            test_app.able_subject_var = FakeVariable("대수")
            test_app.able_document_kind_var = FakeVariable("교재")
            test_app.able_mode_var = FakeVariable("차시별")
            test_app.able_start_var = FakeVariable("01. 1차시")
            test_app.able_end_var = FakeVariable("02. 2차시")
            test_app.able_copies_var = FakeVariable(1)

            with patch("unit_print_manager.MATERIAL_RANGE_CACHE_DIR", directory / "ranges"):
                block = app.make_able_material_block()

            self.assertEqual(1, block.file_count)
            self.assertEqual(1, block.pages_per_sheet)
            self.assertEqual(ABLE_PROFILE_NAME, block.printer_override)
            self.assertEqual("A:ble", block.printer_route)
            self.assertEqual("교재", block.material_kind)
            self.assertEqual(
                [(ABLE_PROFILE_NAME, 4)],
                [(item.printer_override, item.pages_per_sheet) for item in block.files],
            )
            self.assertEqual(
                ["대수 1,2차시 교재"],
                [item.name for item in block.files],
            )
            self.assertEqual(2, len(PdfReader(str(block.files[0].path)).pages))

            jobs = app.build_print_jobs([block])

            self.assertEqual(
                [(ABLE_PROFILE_NAME, 4)],
                [(job.printer_override, job.pages_per_sheet) for job in jobs],
            )
            self.assertEqual(1, len(jobs))

    def test_able_review_block_accepts_non_contiguous_lesson_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            lesson_paths = []
            for number in (1, 2, 3, 4):
                path = directory / f"대수 {number}차시.pdf"
                write_pdf(path)
                lesson_paths.append((number, path))
            review = directory / "백지 리뷰 - 대수.pdf"
            write_pdf(review, page_count=4)
            subject = AbleSubject(
                subject="대수",
                full_documents=(),
                lesson_documents=tuple(lesson_paths),
                review_document=review,
            )
            app = PrintManagerApp.__new__(PrintManagerApp)
            test_app = cast(Any, app)
            test_app.able_material_catalog = AbleCatalog((subject,))
            test_app.able_subject_var = FakeVariable("대수")
            test_app.able_document_kind_var = FakeVariable("백지리뷰")
            test_app.able_mode_var = FakeVariable("차시별")
            test_app.able_start_var = FakeVariable("01. 1차시")
            test_app.able_end_var = FakeVariable("04. 4차시")
            test_app.able_selected_numbers_var = FakeVariable("1, 3-4")
            test_app.able_copies_var = FakeVariable(1)

            with patch("unit_print_manager.MATERIAL_RANGE_CACHE_DIR", directory / "ranges"):
                block = app.make_able_material_block()

            self.assertEqual((1, 3, 4), block.selected_numbers)
            self.assertEqual("(1,3,4)", block.range_label)
            self.assertEqual("백지리뷰", block.material_kind)
            self.assertEqual("세로", block.printer_route)
            self.assertEqual(
                ["대수 1,3,4차시 백지리뷰"],
                [item.name for item in block.files],
            )
            self.assertEqual(
                [(APEX_PROFILE_NAME, 1)],
                [(item.printer_override, item.pages_per_sheet) for item in block.files],
            )
            self.assertEqual(3, len(PdfReader(str(block.files[0].path)).pages))

            restored = app.block_from_config(app.block_to_config(block))
            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual((1, 3, 4), restored.selected_numbers)
            self.assertEqual("백지리뷰", restored.material_kind)
            self.assertEqual("세로", restored.printer_route)

    def test_able_textbook_block_materializes_supplied_combined_page_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            combined = directory / "대수 Able 합본.pdf"
            review = directory / "백지 리뷰 - 대수.pdf"
            write_pdf(combined, page_count=8)
            write_pdf(review, page_count=2)
            subject = AbleSubject(
                subject="대수",
                full_documents=(combined,),
                lesson_documents=(),
                review_document=review,
                lesson_page_spans=(
                    PageSpan(1, "01차시", 1, 3),
                    PageSpan(2, "02차시", 4, 8),
                ),
            )
            app = PrintManagerApp.__new__(PrintManagerApp)
            test_app = cast(Any, app)
            test_app.able_material_catalog = AbleCatalog((subject,))
            test_app.able_subject_var = FakeVariable("대수")
            test_app.able_document_kind_var = FakeVariable("교재")
            test_app.able_mode_var = FakeVariable("차시별")
            test_app.able_start_var = FakeVariable("01. 1차시 (p.1~3)")
            test_app.able_end_var = FakeVariable("02. 2차시 (p.4~8)")
            test_app.able_copies_var = FakeVariable(1)

            with patch("unit_print_manager.MATERIAL_RANGE_CACHE_DIR", directory / "ranges"):
                block = app.make_able_material_block()

            self.assertEqual([8], [len(PdfReader(str(item.path)).pages) for item in block.files])
            self.assertEqual(
                [(ABLE_PROFILE_NAME, 4)],
                [(item.printer_override, item.pages_per_sheet) for item in block.files],
            )
            self.assertEqual(combined, block.files[0].path)

    def test_able_geometry_review_selects_only_lessons_three_four_and_five(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            review = directory / "백지 리뷰 - 기하.pdf"
            write_pdf(review, page_count=5)
            subject = AbleSubject(
                subject="기하",
                full_documents=(),
                lesson_documents=(),
                review_document=review,
                lesson_page_spans=tuple(
                    PageSpan(number, f"{number:02d}차시", number, number)
                    for number in range(1, 6)
                ),
            )
            app = PrintManagerApp.__new__(PrintManagerApp)
            test_app = cast(Any, app)
            test_app.able_material_catalog = AbleCatalog((subject,))
            test_app.able_subject_var = FakeVariable("기하")
            test_app.able_document_kind_var = FakeVariable("백지리뷰")
            test_app.able_mode_var = FakeVariable("차시별")
            test_app.able_start_var = FakeVariable("01. 1차시")
            test_app.able_end_var = FakeVariable("05. 5차시")
            test_app.able_selected_numbers_var = FakeVariable("(3,4,5)")
            test_app.able_copies_var = FakeVariable(1)

            with patch("unit_print_manager.MATERIAL_RANGE_CACHE_DIR", directory / "ranges"):
                block = app.make_able_material_block()

            self.assertEqual((3, 4, 5), block.selected_numbers)
            self.assertEqual("세로", block.printer_route)
            self.assertEqual(
                ["기하 3,4,5차시 백지리뷰"],
                [item.name for item in block.files],
            )
            self.assertEqual(3, len(PdfReader(str(block.files[0].path)).pages))

    def test_able_probability_textbook_selects_only_lesson_seven(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            combined = directory / "확통 Able 합본.pdf"
            write_pdf(combined, page_count=7)
            subject = AbleSubject(
                subject="확률과 통계",
                full_documents=(combined,),
                lesson_documents=(),
                review_document=None,
                lesson_page_spans=tuple(
                    PageSpan(number, f"{number:02d}차시", number, number)
                    for number in range(1, 8)
                ),
            )
            app = PrintManagerApp.__new__(PrintManagerApp)
            test_app = cast(Any, app)
            test_app.able_material_catalog = AbleCatalog((subject,))
            test_app.able_subject_var = FakeVariable("확률과 통계")
            test_app.able_document_kind_var = FakeVariable("교재")
            test_app.able_mode_var = FakeVariable("차시별")
            test_app.able_start_var = FakeVariable("01. 1차시")
            test_app.able_end_var = FakeVariable("07. 7차시")
            test_app.able_selected_numbers_var = FakeVariable("(7)")
            test_app.able_copies_var = FakeVariable(1)

            with patch("unit_print_manager.MATERIAL_RANGE_CACHE_DIR", directory / "ranges"):
                block = app.make_able_material_block()

            self.assertEqual((7,), block.selected_numbers)
            self.assertEqual("A:ble", block.printer_route)
            self.assertEqual(["확률과 통계 7차시 교재"], [item.name for item in block.files])
            self.assertEqual(
                [(ABLE_PROFILE_NAME, 4)],
                [(item.printer_override, item.pages_per_sheet) for item in block.files],
            )

    def test_able_editor_rebuilds_block_and_label_for_new_lesson_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            lesson_paths = []
            for number in range(1, 7):
                path = directory / f"공통수학2 {number}차시.pdf"
                write_pdf(path)
                lesson_paths.append((number, path))
            review = directory / "백지 리뷰 - 공통수학2.pdf"
            write_pdf(review, page_count=6)
            subject = AbleSubject(
                subject="공통수학2",
                full_documents=(),
                lesson_documents=tuple(lesson_paths),
                review_document=review,
            )
            original = PrintBlock(
                subject="공통수학2",
                level="A:ble",
                round_name="차시별",
                start=1,
                end=6,
                copies=1,
                files=[],
                bundled=True,
                source="material_able",
                selected_numbers=(1, 3, 4, 6),
                display_label="A:ble / 공통수학2 / 백지리뷰 / (1,3,4,6)",
                material_kind="백지리뷰",
            )
            app = PrintManagerApp.__new__(PrintManagerApp)
            test_app = cast(Any, app)
            test_app.able_material_catalog = AbleCatalog((subject,))
            test_app.blocks = [original]
            test_app.selected_block_index_value = 0
            test_app.edit_selected_numbers_var = FakeVariable("1-2, 5")
            test_app.edit_copies_var = FakeVariable(1)
            test_app.edit_bundle_var = FakeVariable(True)

            with patch("unit_print_manager.MATERIAL_RANGE_CACHE_DIR", directory / "ranges"):
                block = app.make_block_from_editor()

            self.assertEqual((1, 2, 5), block.selected_numbers)
            self.assertIn("(1,2,5)", block.display_label)
            self.assertEqual("백지리뷰", block.material_kind)
            self.assertEqual(
                ["공통수학2 1,2,5차시 백지리뷰"],
                [item.name for item in block.files],
            )

    def test_able_full_set_composition_prints_all_three_routed_documents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            combined = directory / "대수 Able 합본.pdf"
            concept = directory / "개념정리노트-대수.pdf"
            review = directory / "백지 리뷰 - 대수.pdf"
            for path in (combined, concept, review):
                write_pdf(path)
            subject = AbleSubject(
                subject="대수",
                full_documents=(combined, concept),
                lesson_documents=(),
                review_document=review,
            )
            app = PrintManagerApp.__new__(PrintManagerApp)
            test_app = cast(Any, app)
            test_app.able_material_catalog = AbleCatalog((subject,))
            test_app.able_subject_var = FakeVariable("대수")
            test_app.able_document_kind_var = FakeVariable("풀세트")
            test_app.able_mode_var = FakeVariable("차시별")
            test_app.able_copies_var = FakeVariable(1)

            block = app.make_able_material_block()

            self.assertEqual("전체 묶음", block.material_kind)
            self.assertEqual("풀 세트", block.round_name)
            self.assertEqual([concept.stem, combined.stem, review.stem], [item.name for item in block.files])
            self.assertEqual(
                [(APEX_PROFILE_NAME, 1), (ABLE_PROFILE_NAME, 4), (APEX_PROFILE_NAME, 1)],
                [(item.printer_override, item.pages_per_sheet) for item in block.files],
            )

    def test_able_textbook_review_composition_merges_each_stapled_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            combined = directory / "기하 Able 합본.pdf"
            review = directory / "백지 리뷰 - 기하.pdf"
            write_pdf(combined, page_count=3)
            write_pdf(review, page_count=3)
            subject = AbleSubject(
                subject="기하",
                full_documents=(combined,),
                lesson_documents=(),
                review_document=review,
                lesson_page_spans=tuple(
                    PageSpan(number, f"{number:02d}차시", number, number)
                    for number in range(1, 4)
                ),
            )
            app = PrintManagerApp.__new__(PrintManagerApp)
            test_app = cast(Any, app)
            test_app.able_material_catalog = AbleCatalog((subject,))
            test_app.able_subject_var = FakeVariable("기하")
            test_app.able_document_kind_var = FakeVariable("교재 + 백지리뷰")
            test_app.able_mode_var = FakeVariable("풀 세트")
            test_app.able_start_var = FakeVariable("01. 1차시")
            test_app.able_end_var = FakeVariable("03. 3차시")
            test_app.able_selected_numbers_var = FakeVariable("(1,3)")
            test_app.able_copies_var = FakeVariable(1)

            with patch("unit_print_manager.MATERIAL_RANGE_CACHE_DIR", directory / "ranges"):
                block = app.make_able_material_block()

            self.assertEqual((1, 3), block.selected_numbers)
            self.assertEqual("전체 묶음", block.material_kind)
            self.assertEqual("차시별", block.round_name)
            self.assertEqual(
                [
                    "기하 1,3차시 교재",
                    "기하 1,3차시 백지리뷰",
                ],
                [item.name for item in block.files],
            )
            self.assertEqual(
                [
                    (ABLE_PROFILE_NAME, 4),
                    (APEX_PROFILE_NAME, 1),
                ],
                [(item.printer_override, item.pages_per_sheet) for item in block.files],
            )
            self.assertEqual(
                [2, 2],
                [len(PdfReader(str(item.path)).pages) for item in block.files],
            )

            jobs = app.build_print_jobs([block])

            self.assertEqual(
                [
                    "기하 1,3차시 교재",
                    "기하 1,3차시 백지리뷰",
                ],
                [job.item.name for job in jobs],
            )
            self.assertEqual(2, len(jobs))

    def test_legacy_able_config_is_reclassified_by_document_type(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        data = {
            "source": "material_able",
            "subject": "대수",
            "level": "A:ble",
            "round": "풀 세트",
            "start": 1,
            "end": 3,
            "copies": 1,
            "bundled": True,
            "printer_override": ABLE_PROFILE_NAME,
            "pages_per_sheet": 4,
            "files": [
                {"number": 1, "name": "대수 Able 합본", "path": "대수 Able 합본.pdf"},
                {"number": 2, "name": "개념정리노트-대수", "path": "개념정리노트-대수.pdf"},
                {"number": 3, "name": "백지 리뷰 - 대수", "path": "백지 리뷰 - 대수.pdf"},
            ],
        }

        block = app.block_from_config(data)

        self.assertIsNotNone(block)
        assert block is not None
        self.assertEqual(ABLE_PROFILE_NAME, block.printer_override)
        self.assertEqual("A:ble", block.printer_route)
        self.assertEqual(1, block.pages_per_sheet)
        self.assertEqual(
            [(ABLE_PROFILE_NAME, 4), (APEX_PROFILE_NAME, 1), (APEX_PROFILE_NAME, 1)],
            [(item.printer_override, item.pages_per_sheet) for item in block.files],
        )

    def test_block_profile_validation_includes_item_overrides(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        block = PrintBlock(
            subject="대수",
            level="A:ble",
            round_name="풀 세트",
            start=1,
            end=1,
            copies=1,
            files=[
                ExamPdf(
                    1,
                    "대수 Able 합본",
                    Path("대수 Able 합본.pdf"),
                    printer_override=ABLE_PROFILE_NAME,
                    pages_per_sheet=4,
                )
            ],
        )

        with patch("unit_print_manager.load_printers", return_value=[APEX_PROFILE_NAME]):
            with self.assertRaisesRegex(RuntimeError, ABLE_PROFILE_NAME):
                app.ensure_block_printers_ready([block])

    def test_missing_job_override_profile_is_rejected(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        job = PrintJob(
            block_index=1,
            block_total=1,
            block_uid=1,
            block_summary="A:ble",
            item=ExamPdf(1, "A:ble", Path("able.pdf")),
            copies=1,
            printer_override=ABLE_PROFILE_NAME,
        )

        with patch("unit_print_manager.load_printers", return_value=["iR-ADV 8585 III - 세로"]):
            with self.assertRaisesRegex(RuntimeError, ABLE_PROFILE_NAME):
                app.ensure_job_printers_ready([job])

    def test_queue_refill_wait_honors_requested_batch_size(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.print_cancel_event = threading.Event()
        app.log = Mock()

        with patch("unit_print_manager.get_print_queue_count", return_value=2):
            count = app.wait_for_queue_refill_window(
                "Test Printer",
                batch_size=7,
                refill_threshold=2,
                remaining_jobs=11,
            )

        self.assertEqual(7, count)

    def test_queue_wait_can_be_cancelled_before_polling(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.print_cancel_event = threading.Event()
        app.print_cancel_event.set()
        app.log = Mock()

        with patch("unit_print_manager.get_print_queue_count") as queue_count:
            with self.assertRaises(PrintCancelled):
                app.wait_for_queue_refill_window("Test Printer", 3, 1, 3)

        queue_count.assert_not_called()

    def test_queue_wait_times_out_instead_of_waiting_forever(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.print_cancel_event = threading.Event()
        app.log = Mock()

        with (
            patch("unit_print_manager.get_print_queue_count", return_value=9),
            patch("unit_print_manager.time.monotonic", side_effect=(0.0, 1.0)),
        ):
            with self.assertRaisesRegex(TimeoutError, "대기열"):
                app.wait_for_queue_refill_window(
                    "Test Printer",
                    batch_size=3,
                    refill_threshold=1,
                    remaining_jobs=3,
                    timeout_seconds=0.5,
                )

    def test_dry_run_finishes_with_completed_status_without_printing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "preview.pdf"
            write_pdf(pdf_path)
            app = PrintManagerApp.__new__(PrintManagerApp)
            test_app = cast(Any, app)
            test_app.root = ImmediateRoot()
            test_app.print_state_lock = threading.Lock()
            test_app.print_cancel_event = threading.Event()
            test_app.print_pending_jobs = [make_job(pdf_path, 1)]
            test_app.print_include_separator_pages = False
            test_app.print_accepting_appends = True
            test_app.print_scheduled_block_ids = {101}
            test_app.print_next_block_index = 2
            test_app.active_print_block_uid = None
            test_app.print_run_options = None
            test_app.print_thread = None
            test_app.retry_batch = None
            test_app.is_printing = True
            test_app.is_closing = False
            test_app.log = Mock()
            test_app.set_status = Mock()
            test_app.set_current_work = Mock()
            test_app.mark_blocks_status = Mock()
            test_app.route_job_printer = lambda job, printer_name=None: "Test Printer"
            options = PrintRunOptions(
                printer_name="Base Printer",
                engine="sumatra",
                sumatra_path="SumatraPDF.exe",
                duplex=True,
                dry_run=True,
                queue_batch_size=7,
                queue_refill_threshold=3,
            )

            with patch("unit_print_manager.sumatra_enqueue_pdfs") as enqueue:
                app.print_blocks([], options)

            enqueue.assert_not_called()
            test_app.set_status.assert_any_call("미리보기 완료")
            test_app.set_current_work.assert_any_call("현재 작업: 미리보기 완료")

    def test_print_worker_uses_configured_call_batch_size(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            first_path = directory / "first.pdf"
            second_path = directory / "second.pdf"
            write_pdf(first_path)
            write_pdf(second_path)
            jobs = [make_job(first_path, 1), make_job(second_path, 2)]
            app = PrintManagerApp.__new__(PrintManagerApp)
            test_app = cast(Any, app)
            test_app.root = ImmediateRoot()
            test_app.print_state_lock = threading.Lock()
            test_app.print_cancel_event = threading.Event()
            test_app.print_pending_jobs = jobs
            test_app.print_include_separator_pages = False
            test_app.print_accepting_appends = True
            test_app.print_scheduled_block_ids = {101}
            test_app.print_next_block_index = 2
            test_app.active_print_block_uid = None
            test_app.print_run_options = None
            test_app.print_thread = None
            test_app.retry_batch = None
            test_app.is_printing = True
            test_app.is_closing = False
            test_app.log = Mock()
            test_app.set_status = Mock()
            test_app.set_current_work = Mock()
            test_app.mark_block_status = Mock()
            test_app.mark_blocks_status = Mock()
            test_app.queue_file_line_label = lambda item: item.name
            test_app.route_job_printer = lambda job, printer_name=None: "Test Printer"
            wait_for_refill = Mock(return_value=2)
            test_app.wait_for_queue_refill_window = wait_for_refill
            test_app.wait_for_print_queue_empty = Mock(return_value=True)
            options = PrintRunOptions(
                printer_name="Base Printer",
                engine="sumatra",
                sumatra_path="SumatraPDF.exe",
                duplex=True,
                dry_run=False,
                queue_batch_size=7,
                queue_refill_threshold=3,
            )

            with patch("unit_print_manager.sumatra_enqueue_pdfs", return_value=1) as enqueue:
                app.print_blocks([], options)

            wait_for_refill.assert_called_once_with(
                "Test Printer",
                7,
                3,
                2,
            )
            self.assertEqual(2, enqueue.call_count)

    def test_cancel_after_first_send_preserves_only_unsent_jobs_for_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            first_path = directory / "first.pdf"
            second_path = directory / "second.pdf"
            write_pdf(first_path)
            write_pdf(second_path)
            jobs = [make_job(first_path, 1), make_job(second_path, 2)]
            app = PrintManagerApp.__new__(PrintManagerApp)
            test_app = cast(Any, app)
            test_app.root = ImmediateRoot()
            test_app.print_state_lock = threading.Lock()
            test_app.print_cancel_event = threading.Event()
            test_app.print_pending_jobs = jobs
            test_app.print_include_separator_pages = False
            test_app.print_accepting_appends = True
            test_app.print_scheduled_block_ids = {101}
            test_app.print_next_block_index = 2
            test_app.active_print_block_uid = None
            test_app.print_run_options = None
            test_app.print_thread = None
            test_app.retry_batch = None
            test_app.is_printing = True
            test_app.is_closing = False
            test_app.log = Mock()
            test_app.set_status = Mock()
            test_app.set_current_work = Mock()
            test_app.mark_block_status = Mock()
            test_app.queue_file_line_label = lambda item: item.name
            test_app.route_job_printer = lambda job, printer_name=None: "Test Printer"
            test_app.wait_for_queue_refill_window = Mock(return_value=2)
            test_app.wait_for_print_queue_empty = Mock(return_value=True)
            options = PrintRunOptions(
                printer_name="Base Printer",
                engine="sumatra",
                sumatra_path="SumatraPDF.exe",
                duplex=True,
                dry_run=False,
                queue_batch_size=2,
                queue_refill_threshold=3,
            )

            def enqueue_then_cancel(*_args: object, **_kwargs: object) -> int:
                app.print_cancel_event.set()
                return 1

            with patch("unit_print_manager.sumatra_enqueue_pdfs", side_effect=enqueue_then_cancel) as enqueue:
                app.print_blocks([], options)

            self.assertEqual(1, enqueue.call_count)
            self.assertIsNotNone(app.retry_batch)
            assert app.retry_batch is not None
            self.assertEqual((second_path,), tuple(job.item.path for job in app.retry_batch.jobs))
            test_app.set_status.assert_any_call("전송 중지됨")


if __name__ == "__main__":
    unittest.main()
