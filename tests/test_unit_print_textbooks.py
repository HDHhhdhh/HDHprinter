from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from unit_print_textbooks import (
    TEXTBOOK_CATALOG_PATH,
    TextbookCatalog,
    load_textbook_catalog,
    selected_textbook_page_numbers,
    selected_textbook_variant_page_numbers,
    selected_textbook_variant_problem_numbers,
    verify_textbook_asset,
)


def write_pdf(path: Path, page_count: int = 5) -> None:
    writer = PdfWriter()
    for _index in range(page_count):
        writer.add_blank_page(width=595, height=842)
    with path.open("wb") as stream:
        writer.write(stream)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_catalog(
    path: Path,
    root: Path,
    *,
    status: str = "verified",
    lessons: list[dict[str, object]] | None = None,
    problem_path: str = "공수1/공수1 동아출판 문제.pdf",
    core_sections: list[dict[str, object]] | None = None,
    core_excluded_pages: list[int] | None = None,
    core_cover_pages: int = 0,
    variant_ranges: list[list[int]] | None = None,
    variant_counts: list[int] | None = None,
) -> None:
    problem = root / problem_path
    solution = root / "공수1" / "공수1 동아출판 해설.pdf"
    core = root / "공수1" / "공수1 동아출판 핵심문제.pdf"
    payload = {
        "schema_version": 1,
        "subjects": [
            {
                "id": "공수1",
                "label": "공수1",
                "editions": [
                    {
                        "publisher": "동아출판",
                        "mapping_status": status,
                        "problem": {
                            "relative_path": problem_path,
                            "page_count": 5,
                            "size_bytes": problem.stat().st_size if problem.is_file() else 1,
                            "sha256": sha256(problem) if problem.is_file() else "0" * 64,
                        },
                        "solution": {
                            "relative_path": "공수1/공수1 동아출판 해설.pdf",
                            "page_count": 4,
                            "size_bytes": solution.stat().st_size,
                            "sha256": sha256(solution),
                        },
                        "lessons": lessons if lessons is not None else [],
                    }
                ],
            }
        ],
    }
    if core_sections is not None:
        edition = payload["subjects"][0]["editions"][0]
        edition["core"] = {
            "relative_path": "공수1/공수1 동아출판 핵심문제.pdf",
            "page_count": len(PdfReader(str(core)).pages),
            "size_bytes": core.stat().st_size,
            "sha256": sha256(core),
        }
        edition["core_sections"] = core_sections
        if core_cover_pages:
            edition["core_cover_pages"] = core_cover_pages
        if core_excluded_pages:
            edition["core_excluded_pages"] = core_excluded_pages
    if variant_ranges is not None:
        variant = root / "공수1" / "공수1 동아출판 핵심문제 변형.pdf"
        edition = payload["subjects"][0]["editions"][0]
        edition["variant"] = {
            "relative_path": "공수1/공수1 동아출판 핵심문제 변형.pdf",
            "page_count": 5,
            "size_bytes": variant.stat().st_size,
            "sha256": sha256(variant),
        }
        edition["variant_problem_page_ranges"] = variant_ranges
        edition["variant_section_problem_counts"] = variant_counts or [len(variant_ranges)]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class TextbookCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "교과서"
        subject = self.root / "공수1"
        subject.mkdir(parents=True)
        write_pdf(subject / "공수1 동아출판 문제.pdf", 5)
        write_pdf(subject / "공수1 동아출판 해설.pdf", 4)
        write_pdf(subject / "공수1 동아출판 핵심문제.pdf", 4)
        write_pdf(subject / "공수1 동아출판 핵심문제 변형.pdf", 5)
        self.catalog_path = Path(temporary.name) / "catalog.json"

    def load(self) -> TextbookCatalog:
        return load_textbook_catalog(self.catalog_path, self.root)

    def test_verified_ranges_are_1_based_and_deduplicate_shared_pages(self) -> None:
        write_catalog(
            self.catalog_path,
            self.root,
            lessons=[
                {
                    "number": 1,
                    "label": "첫 소단원",
                    "problem_page_ranges": [[1, 2]],
                    "solution_page_ranges": [[1, 1]],
                },
                {
                    "number": 2,
                    "label": "둘째 소단원",
                    "problem_page_ranges": [[2, 3], [5, 5]],
                    "solution_page_ranges": [[2, 4]],
                },
            ],
        )
        catalog = self.load()

        self.assertEqual((), catalog.issues)
        edition = catalog.find_edition("공수1", "동아출판")
        self.assertIsNotNone(edition)
        assert edition is not None
        self.assertTrue(edition.is_verified)
        self.assertEqual((1, 2, 3, 5), selected_textbook_page_numbers(edition, "문제", (1, 2)))
        self.assertEqual((2, 3, 5, 1), selected_textbook_page_numbers(edition, "문제", (2, 1)))
        self.assertEqual("01. 첫 소단원", edition.lessons[0].display_label)

    def test_core_selection_includes_enclosing_major_assessment_once(self) -> None:
        lessons = [
            {
                "number": number,
                "label": f"파트 {number}",
                "problem_page_ranges": [[number, number]],
                "solution_page_ranges": [[number, number]],
            }
            for number in range(1, 5)
        ]
        write_catalog(
            self.catalog_path,
            self.root,
            lessons=lessons,
            core_sections=[
                {"scope": "중단원", "lesson_start": 1, "lesson_end": 2, "page_start": 1, "page_end": 1},
                {"scope": "중단원", "lesson_start": 3, "lesson_end": 4, "page_start": 2, "page_end": 2},
                {"scope": "대단원", "lesson_start": 1, "lesson_end": 4, "page_start": 3, "page_end": 4},
            ],
        )
        catalog = self.load()
        self.assertEqual((), catalog.issues)
        edition = catalog.find_edition("공수1", "동아출판")
        assert edition is not None
        self.assertEqual((1, 3, 4), selected_textbook_page_numbers(edition, "핵심문제", (1,)))
        self.assertEqual((2, 3, 4), selected_textbook_page_numbers(edition, "핵심문제", (3,)))
        self.assertEqual((1, 2, 3, 4), selected_textbook_page_numbers(edition, "핵심문제", (4, 1)))
        self.assertEqual(4, len(edition.available_lessons("핵심문제")))

    def test_core_selection_prepends_covers_once_without_shifting_other_documents(self) -> None:
        write_pdf(self.root / "공수1" / "공수1 동아출판 핵심문제.pdf", 6)
        write_catalog(
            self.catalog_path, self.root,
            lessons=[
                {"number": 1, "label": "첫 단원", "problem_page_ranges": [[1, 2]],
                 "solution_page_ranges": [[1, 2]]},
                {"number": 2, "label": "둘째 단원", "problem_page_ranges": [[3, 5]],
                 "solution_page_ranges": [[3, 4]]},
            ],
            core_sections=[
                {"scope": "중단원", "lesson_start": 1, "lesson_end": 1,
                 "page_start": 3, "page_end": 3},
                {"scope": "중단원", "lesson_start": 2, "lesson_end": 2,
                 "page_start": 4, "page_end": 4},
                {"scope": "대단원", "lesson_start": 1, "lesson_end": 2,
                 "page_start": 5, "page_end": 6},
            ],
            core_cover_pages=2,
        )
        catalog = self.load()
        self.assertEqual((), catalog.issues)
        edition = catalog.find_edition("공수1", "동아출판")
        assert edition is not None
        self.assertEqual((1, 2, 3, 5, 6), selected_textbook_page_numbers(edition, "핵심문제", (1,)))
        self.assertEqual((1, 2, 4, 5, 6), selected_textbook_page_numbers(edition, "핵심문제", (2,)))
        self.assertEqual((1, 2), selected_textbook_page_numbers(edition, "문제", (1,)))

    def test_variant_selection_includes_two_covers_and_deduplicates_shared_pages(self) -> None:
        write_catalog(
            self.catalog_path, self.root,
            lessons=[{"number": 1, "label": "첫 파트", "problem_page_ranges": [[1, 5]],
                      "solution_page_ranges": [[1, 4]]}],
            core_sections=[{"scope": "대단원", "lesson_start": 1, "lesson_end": 1,
                            "page_start": 1, "page_end": 4}],
            variant_ranges=[[3, 3], [3, 4], [5, 5]],
        )
        catalog = self.load()
        self.assertEqual((), catalog.issues)
        edition = catalog.find_edition("공수1", "동아출판")
        assert edition is not None
        self.assertEqual((1, 2, 3), selected_textbook_variant_page_numbers(edition, (1,)))
        self.assertEqual((1, 2, 3, 4, 5), selected_textbook_variant_page_numbers(edition, (3, 2)))
        self.assertEqual((1, 2, 3), selected_textbook_variant_problem_numbers(edition, (1,)))
        with self.assertRaisesRegex(ValueError, "자료 범위"):
            selected_textbook_variant_page_numbers(edition, (4,))

    def test_variant_catalog_rejects_missing_body_page(self) -> None:
        write_catalog(
            self.catalog_path, self.root,
            lessons=[{"number": 1, "label": "첫 파트", "problem_page_ranges": [[1, 5]],
                      "solution_page_ranges": [[1, 4]]}],
            core_sections=[{"scope": "대단원", "lesson_start": 1, "lesson_end": 1,
                            "page_start": 1, "page_end": 4}],
            variant_ranges=[[3, 3], [5, 5]],
        )
        catalog = self.load()
        self.assertIsNone(catalog.find_edition("공수1", "동아출판"))
        self.assertIn("누락", catalog.issues[0])

    def test_variant_named_lesson_includes_its_medium_and_major_sections(self) -> None:
        write_catalog(
            self.catalog_path, self.root,
            lessons=[
                {"number": 1, "label": "첫 단원", "problem_page_ranges": [[1, 2]],
                 "solution_page_ranges": [[1, 2]]},
                {"number": 2, "label": "둘째 단원", "problem_page_ranges": [[3, 5]],
                 "solution_page_ranges": [[3, 4]]},
            ],
            core_sections=[
                {"scope": "중단원", "lesson_start": 1, "lesson_end": 1,
                 "page_start": 1, "page_end": 1},
                {"scope": "중단원", "lesson_start": 2, "lesson_end": 2,
                 "page_start": 2, "page_end": 2},
                {"scope": "대단원", "lesson_start": 1, "lesson_end": 2,
                 "page_start": 3, "page_end": 4},
            ],
            variant_ranges=[[3, 3], [3, 3], [4, 4], [5, 5]],
            variant_counts=[1, 1, 2],
        )
        catalog = self.load()
        self.assertEqual((), catalog.issues)
        edition = catalog.find_edition("공수1", "동아출판")
        assert edition is not None
        self.assertEqual(
            (1, 3, 4), selected_textbook_variant_problem_numbers(edition, (1,))
        )
        self.assertEqual(
            (1, 2, 3, 4, 5),
            selected_textbook_variant_page_numbers(edition, (1, 3, 4)),
        )

    def test_core_catalog_rejects_missing_pdf_section(self) -> None:
        write_catalog(
            self.catalog_path,
            self.root,
            lessons=[
                {
                    "number": 1,
                    "label": "파트 1",
                    "problem_page_ranges": [[1, 1]],
                    "solution_page_ranges": [[1, 1]],
                }
            ],
            core_sections=[
                {"scope": "대단원", "lesson_start": 1, "lesson_end": 1, "page_start": 1, "page_end": 2},
                {"scope": "대단원", "lesson_start": 1, "lesson_end": 1, "page_start": 4, "page_end": 4},
            ],
        )
        catalog = self.load()
        self.assertIsNone(catalog.find_edition("공수1", "동아출판"))
        self.assertIn("빈틈없이", catalog.issues[0])

    def test_core_catalog_explicitly_skips_non_problem_page(self) -> None:
        write_catalog(
            self.catalog_path,
            self.root,
            lessons=[
                {
                    "number": 1,
                    "label": "첫 파트",
                    "problem_page_ranges": [[1, 1]],
                    "solution_page_ranges": [[1, 1]],
                },
                {
                    "number": 2,
                    "label": "둘째 파트",
                    "problem_page_ranges": [[2, 2]],
                    "solution_page_ranges": [[2, 2]],
                },
            ],
            core_sections=[
                {"scope": "중단원", "lesson_start": 1, "lesson_end": 1, "page_start": 1, "page_end": 1},
                {"scope": "중단원", "lesson_start": 2, "lesson_end": 2, "page_start": 3, "page_end": 3},
                {"scope": "대단원", "lesson_start": 1, "lesson_end": 2, "page_start": 4, "page_end": 4},
            ],
            core_excluded_pages=[2],
        )
        catalog = self.load()
        self.assertEqual((), catalog.issues)
        edition = catalog.find_edition("공수1", "동아출판")
        assert edition is not None
        self.assertEqual((1, 4), selected_textbook_page_numbers(edition, "핵심문제", (1,)))
        self.assertEqual((1, 3, 4), selected_textbook_page_numbers(edition, "핵심문제", (1, 2)))

    def test_pending_stub_is_loaded_but_never_selectable(self) -> None:
        write_catalog(self.catalog_path, self.root, status="pending")
        catalog = self.load()
        edition = catalog.find_edition("공수1", "동아출판")

        self.assertEqual((), catalog.issues)
        self.assertEqual((), catalog.verified_editions)
        self.assertIsNotNone(edition)
        assert edition is not None
        with self.assertRaisesRegex(ValueError, "검수 완료되지 않은"):
            selected_textbook_page_numbers(edition, "문제", (1,))

    def test_incomplete_verified_edition_must_be_deferred_as_a_whole(self) -> None:
        write_catalog(
            self.catalog_path,
            self.root,
            lessons=[
                {
                    "number": 1,
                    "label": "여러 가지 순열",
                    "problem_page_ranges": [[1, 2]],
                    "solution_page_ranges": [],
                    "solution_unavailable_reason": "원본 해설 PDF에 해당 페이지가 없음",
                },
                {
                    "number": 2,
                    "label": "다음 소단원",
                    "problem_page_ranges": [[3, 4]],
                    "solution_page_ranges": [[1, 2]],
                },
            ],
        )
        catalog = self.load()

        self.assertIsNone(catalog.find_edition("공수1", "동아출판"))
        self.assertIn("판본 전체를 pending으로 보류", catalog.issues[0])

    def test_pending_edition_rejects_leftover_candidate_lessons(self) -> None:
        write_catalog(
            self.catalog_path,
            self.root,
            status="pending",
            lessons=[
                {
                    "number": 1,
                    "label": "추정 범위",
                    "problem_page_ranges": [[1, 2]],
                    "solution_page_ranges": [[1, 1]],
                }
            ],
        )
        catalog = self.load()

        self.assertIsNone(catalog.find_edition("공수1", "동아출판"))
        self.assertIn("lessons를 비워야", catalog.issues[0])

    def test_empty_document_range_requires_an_explicit_reason(self) -> None:
        write_catalog(
            self.catalog_path,
            self.root,
            lessons=[
                {
                    "number": 1,
                    "label": "누락 사유 없음",
                    "problem_page_ranges": [[1, 2]],
                    "solution_page_ranges": [],
                }
            ],
        )

        catalog = self.load()
        self.assertIsNone(catalog.find_edition("공수1", "동아출판"))
        self.assertIn("페이지 범위 또는 누락 사유", catalog.issues[0])

    def test_invalid_verified_edition_is_reported_and_excluded(self) -> None:
        write_catalog(
            self.catalog_path,
            self.root,
            lessons=[
                {
                    "number": 1,
                    "label": "범위 오류",
                    "problem_page_ranges": [[1, 6]],
                    "solution_page_ranges": [[1, 1]],
                }
            ],
        )
        catalog = self.load()

        self.assertIsNone(catalog.find_edition("공수1", "동아출판"))
        self.assertEqual(1, len(catalog.issues))
        self.assertIn("PDF 범위를 벗어났습니다", catalog.issues[0])

    def test_catalog_rejects_path_escape(self) -> None:
        write_catalog(
            self.catalog_path,
            self.root,
            status="pending",
            problem_path="../outside.pdf",
        )
        catalog = self.load()

        self.assertIsNone(catalog.find_edition("공수1", "동아출판"))
        self.assertIn("안전하지 않은", catalog.issues[0])

    def test_asset_verification_checks_size_pages_and_hash(self) -> None:
        write_catalog(
            self.catalog_path,
            self.root,
            lessons=[
                {
                    "number": 1,
                    "label": "전체",
                    "problem_page_ranges": [[1, 5]],
                    "solution_page_ranges": [[1, 4]],
                }
            ],
        )
        edition = self.load().find_edition("공수1", "동아출판")
        assert edition is not None
        verify_textbook_asset(edition.problem, verify_sha256=True)

        edition.problem.path.write_bytes(edition.problem.path.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "크기가 카탈로그와 다릅니다"):
            verify_textbook_asset(edition.problem)

    def test_checked_in_inventory_has_42_verified_triplets(self) -> None:
        catalog = load_textbook_catalog(TEXTBOOK_CATALOG_PATH, self.root)
        editions = [edition for subject in catalog.subjects for edition in subject.editions]
        paths = {
            asset.relative_path.as_posix()
            for edition in editions
            for asset in (edition.core, edition.problem, edition.solution)
            if asset is not None
        }

        self.assertEqual((), catalog.issues)
        self.assertEqual(42, len(editions))
        self.assertEqual(126, len(paths))
        self.assertEqual(1_895_168_445, sum(
            edition.problem.size_bytes + edition.solution.size_bytes for edition in editions
        ))
        self.assertEqual(42, len(catalog.verified_editions))
        variants = [edition for edition in editions if edition.variant is not None]
        self.assertEqual(15, len(variants))
        self.assertTrue(all(
            len(edition.variant_section_problem_counts) == len(edition.core_sections)
            and sum(edition.variant_section_problem_counts) == len(edition.variant_problem_page_ranges)
            for edition in variants
        ))
        hong = catalog.find_edition("공수2", "천재(홍진곤)")
        assert hong is not None
        self.assertEqual(
            (10, 10, 10, 16, 10, 10, 15, 10, 10, 16),
            hong.variant_section_problem_counts,
        )
        self.assertEqual(1_046, sum(len(edition.lessons) for edition in editions))
        self.assertEqual(887, sum(edition.core.page_count for edition in editions if edition.core))
        self.assertTrue(all(edition.core_cover_pages == 2 for edition in editions))
        self.assertTrue(all(edition.core and edition.core_sections for edition in editions))
        self.assertTrue(all(edition.is_verified and edition.lessons for edition in editions))
        algebra = catalog.find_edition("대수", "미래엔")
        assert algebra is not None
        self.assertEqual((6, 16, 26, 30), algebra.core_excluded_pages)

    def test_repaired_cheonjae_editions_match_original_assets(self) -> None:
        catalog = load_textbook_catalog(TEXTBOOK_CATALOG_PATH, self.root)
        expected = {
            ("기하", "천재(전인태)"): {
                "lesson_count": 31,
                "problem": (120, 41_923_170, "bfc650995c5e64efbdb58f0009866200d4cdb2438188e0d9487a2a9861b8c0a8"),
                "solution": (21, 1_302_377, "93446a3fdc5ae40e662ae27bf985120b1f9cc6a3ae17bcaa46cdc670f9a72322"),
            },
            ("확통", "천재(전인태)"): {
                "lesson_count": 23,
                "problem": (95, 41_214_251, "d434146ec5258b4691ada7621bef3f32dae6c932441749b00db67d6d3626d463"),
                "solution": (18, 1_095_931, "86703a70a25c4461a112871b7819568ff84d0c5c22606fd174f73f2e7e31e124"),
            },
        }

        for (subject, publisher), values in expected.items():
            edition = catalog.find_edition(subject, publisher)
            self.assertIsNotNone(edition)
            assert edition is not None
            self.assertTrue(edition.is_verified)
            self.assertEqual(values["lesson_count"], len(edition.lessons))
            for document in ("problem", "solution"):
                asset = getattr(edition, document)
                self.assertEqual(values[document], (asset.page_count, asset.size_bytes, asset.sha256))


if __name__ == "__main__":
    unittest.main()
