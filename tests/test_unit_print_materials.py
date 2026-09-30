from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter
import unit_print_materials

from unit_print_materials import (
    ABLE_PROFILE_NAME,
    APEX_PROFILE_NAME,
    ApexDocument,
    PageSpan,
    TrainingPackSubject,
    apex_manual_lesson_page_spans,
    apex_manual_page_numbers,
    apply_apex_page_map,
    apex_boost_answer_page,
    apex_boost_lesson_page_spans,
    apex_boost_page_numbers,
    able_lesson_page_spans,
    able_document_print_settings,
    combined_span,
    load_apex_catalog,
    load_frequent_type_page_map,
    materialize_pdf_documents,
    materialize_pdf_pages,
    materialize_pdf_span,
    parse_able_lesson_filename,
    parse_apex_filename,
    frequent_type_page_numbers,
    load_training_pack_page_map,
    training_pack_page_numbers,
)


class MaterialFilenameTest(unittest.TestCase):
    def test_classifies_apex_and_apex_boost_documents(self) -> None:
        self.assertEqual(
            ("APEX", "공통수학1", "교재"),
            parse_apex_filename(Path("APEX 공통수학1.pdf")),
        )
        self.assertEqual(
            ("APEX Boost", "미적분1", "해설"),
            parse_apex_filename(Path("APEX Boost 미적분1 해설.pdf")),
        )

    def test_classifies_able_lesson_documents_and_normalizes_subject(self) -> None:
        self.assertEqual(
            ("공통수학2", 13),
            parse_able_lesson_filename(Path("공수2 13차시.pdf")),
        )
        self.assertEqual(
            ("미적분1", 4),
            parse_able_lesson_filename(Path("미적분1 4차시.ppt.pdf")),
        )

    def test_routes_only_able_combined_and_lesson_notes_to_four_up(self) -> None:
        self.assertEqual(
            (ABLE_PROFILE_NAME, 4),
            able_document_print_settings(Path("공수1 Able 합본.pdf")),
        )
        self.assertEqual(
            (ABLE_PROFILE_NAME, 4),
            able_document_print_settings(Path("공수1 3차시.pdf")),
        )
        self.assertEqual(
            (ABLE_PROFILE_NAME, 4),
            able_document_print_settings(Path("Able_확률과 통계_07_렉쳐노트.pdf")),
        )
        self.assertEqual(
            (APEX_PROFILE_NAME, 1),
            able_document_print_settings(Path("개념정리노트_공통수학1.pdf")),
        )
        self.assertEqual(
            (APEX_PROFILE_NAME, 1),
            able_document_print_settings(Path("백지 리뷰 - 공통수학1.pdf")),
        )

    def test_merges_complete_lesson_pdfs_into_one_cached_document(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "1차시.pdf"
            second = root / "2차시.pdf"
            for path, page_count in ((first, 2), (second, 3)):
                writer = PdfWriter()
                for _index in range(page_count):
                    _ = writer.add_blank_page(width=595, height=842)
                with path.open("wb") as output_file:
                    _ = writer.write(output_file)

            merged = materialize_pdf_documents(
                (first, second), root / "cache", "A:ble 1,2차시 묶음"
            )
            cached = materialize_pdf_documents(
                (first, second), root / "cache", "A:ble 1,2차시 묶음"
            )

            self.assertEqual(5, len(PdfReader(str(merged)).pages))
            self.assertEqual(merged, cached)


class AbleLessonPageMapTest(unittest.TestCase):
    def test_supplied_page_ranges_cover_each_combined_document(self) -> None:
        expected = {
            "공통수학1": (15, 412),
            "공통수학2": (13, 384),
            "기하": (14, 248),
            "대수": (12, 328),
            "미적분1": (16, 272),
            "확률과 통계": (14, 232),
        }

        for subject, (lesson_count, page_count) in expected.items():
            spans = able_lesson_page_spans(subject)
            self.assertEqual(lesson_count, len(spans), subject)
            self.assertEqual(1, spans[0].start_page, subject)
            self.assertEqual(page_count, spans[-1].end_page, subject)
            for previous, current in zip(spans, spans[1:]):
                self.assertEqual(previous.end_page + 1, current.start_page, subject)


class TrainingPackPageMapTest(unittest.TestCase):
    def test_markdown_map_contains_problem_ranges_without_covers_or_solutions(self) -> None:
        page_map = load_training_pack_page_map(Path("트레이닝팩_차시별_인쇄페이지.md"))

        self.assertEqual(14, len(page_map["공통수학1"]))
        self.assertEqual((2, 9), (page_map["공통수학1"][0].start_page, page_map["공통수학1"][0].end_page))
        self.assertEqual((91, 100), (page_map["공통수학1"][-1].start_page, page_map["공통수학1"][-1].end_page))
        self.assertEqual(16, len(page_map["미적분2"]))
        self.assertNotIn("통계", page_map)

    def test_selected_training_pack_lessons_expand_in_input_order(self) -> None:
        subject = TrainingPackSubject(
            "공통수학1",
            Path("공수1 트팩.pdf"),
            100,
            (
                PageSpan(1, "다항식", 2, 3),
                PageSpan(2, "항등식", 4, 5),
                PageSpan(3, "인수분해", 6, 8),
            ),
        )

        self.assertEqual((6, 7, 8, 2, 3), training_pack_page_numbers(subject, (3, 1)))


class FrequentTypePageMapTest(unittest.TestCase):
    def test_markdown_map_matches_all_seven_combined_pdfs(self) -> None:
        page_map = load_frequent_type_page_map(Path("빈출유형_차시별_인쇄페이지.md"))
        expected = {
            "공통수학1": (24, 95),
            "공통수학2": (29, 117),
            "기하": (28, 114),
            "대수": (21, 87),
            "미적분1": (20, 99),
            "미적분2": (32, 130),
            "확률과 통계": (28, 117),
        }

        self.assertEqual(set(expected), set(page_map))
        for subject, (lesson_count, page_count) in expected.items():
            spans = page_map[subject]
            self.assertEqual(lesson_count, len(spans), subject)
            self.assertEqual(1, spans[0].start_page, subject)
            self.assertEqual(page_count, spans[-1].end_page, subject)
            for previous, current in zip(spans, spans[1:]):
                self.assertEqual(previous.end_page + 1, current.start_page, subject)

    def test_selected_frequent_type_lessons_keep_input_order(self) -> None:
        subject = TrainingPackSubject(
            "대수",
            Path("대수 빈출유형.pdf"),
            12,
            (
                PageSpan(1, "지수", 1, 4),
                PageSpan(2, "로그", 5, 7),
                PageSpan(3, "수열", 8, 12),
            ),
        )

        self.assertEqual((8, 9, 10, 11, 12, 1, 2, 3, 4), frequent_type_page_numbers(subject, (3, 1)))


class ApexBoostLessonPageMapTest(unittest.TestCase):
    def test_supplied_boost_maps_expose_all_documented_lessons(self) -> None:
        expected = {
            "공통수학2": (10, 2, 5, 98, 107),
            "대수": (10, 1, 5, 91, 100),
            "미적분1": (9, 1, 10, 89, 97),
        }

        for subject, (lesson_count, first_start, first_end, first_answer, last_answer) in expected.items():
            spans = apex_boost_lesson_page_spans(subject)
            self.assertEqual(lesson_count, len(spans), subject)
            self.assertEqual((first_start, first_end), (spans[0].start_page, spans[0].end_page), subject)
            self.assertEqual(first_answer, apex_boost_answer_page(subject, 1), subject)
            self.assertEqual(last_answer, apex_boost_answer_page(subject, lesson_count), subject)

    def test_boost_page_numbers_keep_each_answer_after_its_body(self) -> None:
        expected = tuple(range(6, 13)) + (92,) + tuple(range(13, 23)) + (93,)

        self.assertEqual(expected, apex_boost_page_numbers("대수", (2, 3)))

    def test_catalog_applies_boost_map_before_and_after_cache_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "APEX Boost 대수.pdf"
            writer = PdfWriter()
            for _index in range(100):
                _ = writer.add_blank_page(width=595, height=842)
            with source.open("wb") as output_file:
                _ = writer.write(output_file)
            cache_path = root / "catalog.json"

            first = load_apex_catalog(root, cache_path).documents[0]
            second = load_apex_catalog(root, cache_path).documents[0]

            self.assertEqual(10, len(first.lessons))
            self.assertEqual("지수", first.lessons[0].label)
            self.assertEqual(first.lessons, second.lessons)


class ApexManualLessonPageMapTest(unittest.TestCase):
    def test_common_math1_manual_map_exposes_split_ranges_and_answers(self) -> None:
        spans = apex_manual_lesson_page_spans("공통수학1")

        self.assertEqual(51, len(spans))
        self.assertEqual("다항식의 연산", spans[0].label)
        self.assertEqual("곱셈 공식의 활용", spans[1].label)
        self.assertEqual((3, 14, "part.1"), (spans[0].start_page, spans[0].end_page, spans[0].part))
        self.assertEqual("51-57,60-62", spans[8].page_range_label)
        self.assertEqual((141, 150, "part.2"), (spans[24].start_page, spans[24].end_page, spans[24].part))
        self.assertEqual((245, 262, "part.2"), (spans[-1].start_page, spans[-1].end_page, spans[-1].part))

    def test_common_math1_merges_body_pages_and_appends_unique_answers(self) -> None:
        expected = tuple(range(3, 24)) + (263, 264)

        self.assertEqual(expected, apex_manual_page_numbers("공통수학1", (1, 2)))

    def test_common_math1_deduplicates_overlapping_lesson_pages(self) -> None:
        expected = tuple(range(35, 51)) + (264, 265)

        self.assertEqual(expected, apex_manual_page_numbers("공통수학1", (5, 6, 7, 8)))

    def test_common_math1_preserves_a_split_lesson_page_gap(self) -> None:
        expected = tuple(range(51, 58)) + tuple(range(60, 63)) + (265, 266)

        self.assertEqual(expected, apex_manual_page_numbers("공통수학1", (9,)))

    def test_common_math1_keeps_part_answer_pages_after_merged_body(self) -> None:
        expected = tuple(range(99, 140)) + tuple(range(141, 151)) + (268, 269, 270, 271)

        self.assertEqual(expected, apex_manual_page_numbers("공통수학1", (24, 25)))

    def test_catalog_map_replaces_auto_detected_common_math1_lessons(self) -> None:
        source = Path("APEX 공통수학1.pdf")
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
            lessons=(PageSpan(1, "자동 감지", 1, 262),),
        )

        mapped = apply_apex_page_map(document)

        self.assertEqual(51, len(mapped.lessons))
        self.assertEqual("인수분해", mapped.lessons[8].label)
        self.assertEqual("part.2", mapped.lessons[24].part)

    def test_common_math2_manual_map_exposes_reset_question_numbers_and_answers(self) -> None:
        spans = apex_manual_lesson_page_spans("공통수학2")

        self.assertEqual(50, len(spans))
        self.assertEqual((3, 6, "part.1"), (spans[0].start_page, spans[0].end_page, spans[0].part))
        self.assertEqual((79, 81), (spans[24].start_page, spans[24].end_page))
        self.assertEqual((83, 94, "part.2"), (spans[25].start_page, spans[25].end_page, spans[25].part))
        self.assertEqual((168, 171), (spans[-1].start_page, spans[-1].end_page))

    def test_common_math2_merges_overlapping_body_pages_and_appends_part_answers(self) -> None:
        self.assertEqual(
            tuple(range(3, 8)) + (172,),
            apex_manual_page_numbers("공통수학2", (1, 2)),
        )
        self.assertEqual(
            tuple(range(83, 100)) + (178, 179),
            apex_manual_page_numbers("공통수학2", (26, 27, 28, 29, 30)),
        )

    def test_common_math2_keeps_part_gap_and_answer_order(self) -> None:
        expected = tuple(range(79, 82)) + tuple(range(83, 95)) + (176, 177, 178)

        self.assertEqual(expected, apex_manual_page_numbers("공통수학2", (25, 26)))

    def test_catalog_map_replaces_auto_detected_common_math2_lessons(self) -> None:
        source = Path("APEX 공통수학2.pdf")
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
            lessons=(PageSpan(1, "자동 감지", 1, 171),),
        )

        mapped = apply_apex_page_map(document)

        self.assertEqual(50, len(mapped.lessons))
        self.assertEqual("집합의 연산", mapped.lessons[25].label)
        self.assertEqual("part.2", mapped.lessons[25].part)

    def test_algebra_manual_map_exposes_two_parts_and_all_lessons(self) -> None:
        spans = apex_manual_lesson_page_spans("대수")

        self.assertEqual(47, len(spans))
        self.assertEqual((3, 6, "part.1"), (spans[0].start_page, spans[0].end_page, spans[0].part))
        self.assertEqual((64, 70), (spans[22].start_page, spans[22].end_page))
        self.assertEqual((72, 77, "part.2"), (spans[23].start_page, spans[23].end_page, spans[23].part))
        self.assertEqual((150, 153), (spans[-1].start_page, spans[-1].end_page))

    def test_algebra_merges_overlapping_body_pages_and_deduplicates_answers(self) -> None:
        self.assertEqual(
            tuple(range(3, 13)) + (154,),
            apex_manual_page_numbers("대수", (1, 2)),
        )
        self.assertEqual(
            tuple(range(64, 71)) + tuple(range(72, 78)) + (157, 158, 159),
            apex_manual_page_numbers("대수", (21, 22, 23, 24, 25)),
        )
        self.assertEqual(
            tuple(range(133, 141)) + (162, 163),
            apex_manual_page_numbers("대수", (42, 43)),
        )

    def test_catalog_map_replaces_auto_detected_algebra_lessons(self) -> None:
        source = Path("APEX 대수.pdf")
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
            lessons=(PageSpan(1, "자동 감지", 1, 153),),
        )

        mapped = apply_apex_page_map(document)

        self.assertEqual(47, len(mapped.lessons))
        self.assertEqual("삼각함수의 그래프", mapped.lessons[23].label)
        self.assertEqual("part.2", mapped.lessons[23].part)


class MaterialPageRangeTest(unittest.TestCase):
    def test_rebuilds_a_matching_but_malformed_apex_cache_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "APEX algebra.pdf"
            writer = PdfWriter()
            _ = writer.add_blank_page(width=595, height=842)
            with source.open("wb") as output_file:
                _ = writer.write(output_file)
            stat = source.stat()
            cache_path = root / "catalog.json"
            malformed: dict[str, object] = {
                str(source.resolve()): {
                    "mtime_ns": stat.st_mtime_ns,
                    "size": stat.st_size,
                    "page_count": "broken",
                    "parts": [],
                    "lessons": [],
                }
            }
            _ = cache_path.write_text(json.dumps(malformed), encoding="utf-8")

            catalog = load_apex_catalog(root, cache_path)

            self.assertEqual(1, len(catalog.documents))
            self.assertEqual(1, catalog.documents[0].page_count)

    def test_portable_apex_cache_is_rebound_to_the_extracted_file_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "APEX algebra.pdf"
            writer = PdfWriter()
            _ = writer.add_blank_page(width=595, height=842)
            with source.open("wb") as output_file:
                _ = writer.write(output_file)
            stat = source.stat()
            cache_path = root / "catalog.json"
            portable = {
                "APEX algebra.pdf": {
                    "portable": True,
                    "mtime_ns": 0,
                    "size": stat.st_size,
                    "page_count": 1,
                    "parts": [],
                    "lessons": [],
                }
            }
            _ = cache_path.write_text(json.dumps(portable), encoding="utf-8")

            catalog = load_apex_catalog(root, cache_path)
            rebound = json.loads(cache_path.read_text(encoding="utf-8"))["APEX algebra.pdf"]

            self.assertEqual(1, len(catalog.documents))
            self.assertEqual(stat.st_mtime_ns, rebound["mtime_ns"])
            self.assertNotIn("portable", rebound)

    def test_combines_selected_lessons_into_one_contiguous_span(self) -> None:
        spans = (
            PageSpan(2, "02차시", 6, 10, "part.1"),
            PageSpan(3, "03차시", 11, 15, "part.1"),
        )

        result = combined_span(spans, "02~03차시", "part.1")

        self.assertEqual((6, 15, "part.1"), (result.start_page, result.end_page, result.part))

    def test_materializes_only_the_requested_pdf_pages_and_reuses_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            writer = PdfWriter()
            for _index in range(6):
                _ = writer.add_blank_page(width=595, height=842)
            with source.open("wb") as output_file:
                _ = writer.write(output_file)
            span = PageSpan(2, "02차시", 2, 4)

            first = materialize_pdf_span(source, span, root / "cache", "교재 02차시")
            with patch("unit_print_materials.PdfReader", side_effect=AssertionError("cache miss")):
                second = materialize_pdf_span(source, span, root / "cache", "교재 02차시")

            self.assertEqual(first, second)
            self.assertEqual(3, len(PdfReader(first).pages))

    def test_materializes_an_ordered_non_contiguous_page_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            writer = PdfWriter()
            for _index in range(6):
                _ = writer.add_blank_page(width=595, height=842)
            with source.open("wb") as output_file:
                _ = writer.write(output_file)

            output = materialize_pdf_pages(source, (2, 5, 1), root / "cache", "Boost 02~03차시")
            with patch("unit_print_materials.PdfReader", side_effect=AssertionError("cache miss")):
                cached = materialize_pdf_pages(
                    source,
                    (2, 5, 1),
                    root / "cache",
                    "Boost 02~03차시",
                )

            self.assertEqual(3, len(PdfReader(output).pages))
            self.assertEqual(output, cached)

    def test_same_cache_target_can_be_built_concurrently_without_partial_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            writer = PdfWriter()
            for _index in range(8):
                writer.add_blank_page(width=595, height=842)
            with source.open("wb") as output_file:
                writer.write(output_file)
            original_write = unit_print_materials._write_pdf_atomically
            barrier = threading.Barrier(2)

            def synchronized_write(pdf_writer, output_path):
                barrier.wait(2)
                original_write(pdf_writer, output_path)

            with patch(
                "unit_print_materials._write_pdf_atomically",
                side_effect=synchronized_write,
            ), ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(
                        materialize_pdf_pages,
                        source,
                        (1, 3, 5, 7),
                        root / "cache",
                        "동시 준비",
                    )
                    for _index in range(2)
                ]
                outputs = [future.result(3) for future in futures]

            self.assertEqual(outputs[0], outputs[1])
            self.assertEqual(4, len(PdfReader(str(outputs[0])).pages))
            self.assertEqual([], list((root / "cache").glob("*.tmp.pdf")))

    def test_cache_key_changes_when_size_changes_even_with_same_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"

            def write_pages(count: int) -> None:
                writer = PdfWriter()
                for _index in range(count):
                    writer.add_blank_page(width=595, height=842)
                with source.open("wb") as output_file:
                    writer.write(output_file)

            write_pages(2)
            original_mtime = source.stat().st_mtime_ns
            first = materialize_pdf_pages(source, (1,), root / "cache", "크기 키")
            write_pages(3)
            os.utime(source, ns=(original_mtime, original_mtime))
            second = materialize_pdf_pages(source, (1,), root / "cache", "크기 키")

            self.assertNotEqual(first, second)


if __name__ == "__main__":
    _ = unittest.main()
