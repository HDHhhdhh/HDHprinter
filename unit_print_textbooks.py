from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from unit_print_paths import DATA_ROOT
from unit_print_pdf_metadata import get_pdf_page_count


MODULE_DIR: Final = Path(__file__).resolve().parent
TEXTBOOK_CATALOG_PATH: Final = MODULE_DIR / "unit_print_textbook_catalog.json"
BUNDLED_TEXTBOOK_ROOT: Final = DATA_ROOT / "교과서"
OPERATOR_TEXTBOOK_ROOT: Final = Path.home() / "Desktop" / "교과서"
DEFAULT_TEXTBOOK_ROOT: Final = (
    BUNDLED_TEXTBOOK_ROOT if BUNDLED_TEXTBOOK_ROOT.is_dir() else OPERATOR_TEXTBOOK_ROOT
)
TEXTBOOK_SUBJECT_ORDER: Final = (
    "공수1",
    "공수2",
    "기하",
    "대수",
    "미적1",
    "미적2",
    "확통",
)
TEXTBOOK_DOCUMENT_KINDS: Final = ("핵심문제", "문제", "해설")
TEXTBOOK_MAPPING_STATUSES: Final = frozenset({"pending", "verified"})
_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")


def _flatten_ranges(page_ranges: tuple[tuple[int, int], ...]) -> tuple[int, ...]:
    return tuple(
        page
        for start_page, end_page in page_ranges
        for page in range(start_page, end_page + 1)
    )


def _parse_page_ranges(
    value: object,
    *,
    field_name: str,
    allow_empty: bool = False,
) -> tuple[tuple[int, int], ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError(f"{field_name}가 비어 있습니다.")
    ranges: list[tuple[int, int]] = []
    seen_pages: set[int] = set()
    for raw_range in value:
        if not isinstance(raw_range, list) or len(raw_range) != 2:
            raise ValueError(f"{field_name}는 [시작, 끝] 목록이어야 합니다.")
        start_page, end_page = (int(raw_range[0]), int(raw_range[1]))
        if start_page < 1 or end_page < start_page:
            raise ValueError(f"{field_name} 페이지 범위가 잘못되었습니다: {raw_range}")
        pages = set(range(start_page, end_page + 1))
        if seen_pages.intersection(pages):
            raise ValueError(f"{field_name} 안에 중복 페이지가 있습니다: {raw_range}")
        seen_pages.update(pages)
        ranges.append((start_page, end_page))
    return tuple(ranges)


def _resolve_relative_pdf(root: Path, value: object) -> tuple[Path, Path]:
    relative_path = Path(str(value))
    if (
        relative_path.is_absolute()
        or relative_path.suffix.casefold() != ".pdf"
        or ".." in relative_path.parts
    ):
        raise ValueError(f"안전하지 않은 교과서 PDF 상대 경로입니다: {value}")
    resolved_root = root.resolve()
    path = (root / relative_path).resolve()
    if not path.is_relative_to(resolved_root):
        raise ValueError(f"교과서 자료 루트 밖의 경로입니다: {value}")
    return relative_path, path


@dataclass(frozen=True, slots=True)
class TextbookPdfAsset:
    relative_path: Path
    path: Path
    page_count: int
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        if self.page_count < 1:
            raise ValueError(f"교과서 PDF 페이지 수가 잘못되었습니다: {self.relative_path}")
        if self.size_bytes < 1:
            raise ValueError(f"교과서 PDF 크기가 잘못되었습니다: {self.relative_path}")
        if _SHA256_PATTERN.fullmatch(self.sha256) is None:
            raise ValueError(f"교과서 PDF SHA-256이 잘못되었습니다: {self.relative_path}")


@dataclass(frozen=True, slots=True)
class TextbookLesson:
    number: int
    label: str
    problem_page_ranges: tuple[tuple[int, int], ...]
    solution_page_ranges: tuple[tuple[int, int], ...]
    problem_unavailable_reason: str = ""
    solution_unavailable_reason: str = ""

    def __post_init__(self) -> None:
        if self.number < 1 or not self.label.strip():
            raise ValueError(f"교과서 소단원 정보가 잘못되었습니다: {self!r}")
        for document_kind, page_ranges, reason in (
            ("문제", self.problem_page_ranges, self.problem_unavailable_reason),
            ("해설", self.solution_page_ranges, self.solution_unavailable_reason),
        ):
            if not page_ranges and not reason.strip():
                raise ValueError(
                    f"교과서 {document_kind} 페이지 범위 또는 누락 사유가 필요합니다: {self!r}"
                )
            if page_ranges and reason.strip():
                raise ValueError(
                    f"교과서 {document_kind} 페이지와 누락 사유를 함께 지정할 수 없습니다: {self!r}"
                )

    @property
    def display_label(self) -> str:
        return f"{self.number:02d}. {self.label}"

    def page_ranges(self, document_kind: str) -> tuple[tuple[int, int], ...]:
        if document_kind == "문제":
            return self.problem_page_ranges
        if document_kind == "해설":
            return self.solution_page_ranges
        raise ValueError(f"알 수 없는 교과서 문서 종류입니다: {document_kind}")

    def unavailable_reason(self, document_kind: str) -> str:
        if document_kind == "문제":
            return self.problem_unavailable_reason
        if document_kind == "해설":
            return self.solution_unavailable_reason
        raise ValueError(f"알 수 없는 교과서 문서 종류입니다: {document_kind}")

    def is_available(self, document_kind: str) -> bool:
        return bool(self.page_ranges(document_kind))


@dataclass(frozen=True, slots=True)
class TextbookCoreSection:
    """A complete source-PDF section linked to the existing problem part numbers."""

    lesson_start: int
    lesson_end: int
    page_start: int
    page_end: int
    scope: str

    def __post_init__(self) -> None:
        if self.lesson_start < 1 or self.lesson_end < self.lesson_start:
            raise ValueError(f"핵심문제 파트 범위가 잘못되었습니다: {self!r}")
        if self.page_start < 1 or self.page_end < self.page_start:
            raise ValueError(f"핵심문제 페이지 범위가 잘못되었습니다: {self!r}")
        if self.scope not in {"중단원", "대단원"}:
            raise ValueError(f"핵심문제 범위 종류가 잘못되었습니다: {self!r}")

    def includes_lesson(self, number: int) -> bool:
        return self.lesson_start <= number <= self.lesson_end


@dataclass(frozen=True, slots=True)
class TextbookEdition:
    subject: str
    publisher: str
    mapping_status: str
    problem: TextbookPdfAsset
    solution: TextbookPdfAsset
    lessons: tuple[TextbookLesson, ...]
    core: TextbookPdfAsset | None = None
    core_sections: tuple[TextbookCoreSection, ...] = ()
    core_excluded_pages: tuple[int, ...] = ()
    core_cover_pages: int = 0
    variant: TextbookPdfAsset | None = None
    variant_problem_page_ranges: tuple[tuple[int, int], ...] = ()
    variant_section_problem_counts: tuple[int, ...] = ()

    @property
    def is_verified(self) -> bool:
        return self.mapping_status == "verified"

    @property
    def is_available(self) -> bool:
        return self.is_verified and self.problem.path.is_file() and self.solution.path.is_file()

    def asset(self, document_kind: str) -> TextbookPdfAsset:
        if document_kind == "핵심문제":
            if self.core is None:
                raise ValueError(f"핵심문제 PDF가 등록되지 않았습니다: {self.subject} / {self.publisher}")
            return self.core
        if document_kind == "문제":
            return self.problem
        if document_kind == "해설":
            return self.solution
        raise ValueError(f"알 수 없는 교과서 문서 종류입니다: {document_kind}")

    def lesson(self, number: int) -> TextbookLesson | None:
        return next((lesson for lesson in self.lessons if lesson.number == number), None)

    def available_lessons(self, document_kind: str) -> tuple[TextbookLesson, ...]:
        return tuple(
            lesson for lesson in self.lessons if self.lesson_available(lesson, document_kind)
        )

    def unavailable_lessons(self, document_kind: str) -> tuple[TextbookLesson, ...]:
        return tuple(
            lesson for lesson in self.lessons if not self.lesson_available(lesson, document_kind)
        )

    def lesson_available(self, lesson: TextbookLesson, document_kind: str) -> bool:
        if document_kind == "핵심문제":
            return (
                self.core is not None
                and self.core.path.is_file()
                and any(section.includes_lesson(lesson.number) for section in self.core_sections)
            )
        return lesson.is_available(document_kind)

    def lesson_unavailable_reason(self, lesson: TextbookLesson, document_kind: str) -> str:
        if document_kind == "핵심문제":
            if self.core is None:
                return "핵심문제 PDF 미등록"
            if not self.core.path.is_file():
                return f"핵심문제 PDF 없음: {self.core.relative_path}"
            return "검수된 핵심문제 파트 범위 없음"
        return lesson.unavailable_reason(document_kind)


@dataclass(frozen=True, slots=True)
class TextbookSubject:
    id: str
    label: str
    editions: tuple[TextbookEdition, ...]

    @property
    def verified_editions(self) -> tuple[TextbookEdition, ...]:
        return tuple(edition for edition in self.editions if edition.is_verified)

    def find_edition(self, publisher: str) -> TextbookEdition | None:
        return next((edition for edition in self.editions if edition.publisher == publisher), None)


@dataclass(frozen=True, slots=True)
class TextbookCatalog:
    subjects: tuple[TextbookSubject, ...]
    issues: tuple[str, ...] = ()

    @property
    def subject_labels(self) -> tuple[str, ...]:
        return tuple(subject.label for subject in self.subjects)

    @property
    def verified_editions(self) -> tuple[TextbookEdition, ...]:
        return tuple(
            edition
            for subject in self.subjects
            for edition in subject.verified_editions
        )

    def find_subject(self, subject_id_or_label: str) -> TextbookSubject | None:
        return next(
            (
                subject
                for subject in self.subjects
                if subject.id == subject_id_or_label or subject.label == subject_id_or_label
            ),
            None,
        )

    def find_edition(self, subject_id_or_label: str, publisher: str) -> TextbookEdition | None:
        subject = self.find_subject(subject_id_or_label)
        return subject.find_edition(publisher) if subject is not None else None


def _parse_pdf_asset(root: Path, value: object) -> TextbookPdfAsset:
    if not isinstance(value, dict):
        raise ValueError("교과서 PDF 정보가 객체가 아닙니다.")
    relative_path, path = _resolve_relative_pdf(root, value.get("relative_path", ""))
    return TextbookPdfAsset(
        relative_path=relative_path,
        path=path,
        page_count=int(value.get("page_count", 0)),
        size_bytes=int(value.get("size_bytes", 0)),
        sha256=str(value.get("sha256", "")).casefold(),
    )


def _parse_lessons(
    value: object,
    *,
    problem_page_count: int,
    solution_page_count: int,
    required: bool,
) -> tuple[TextbookLesson, ...]:
    if not isinstance(value, list):
        raise ValueError("교과서 소단원 목록이 배열이 아닙니다.")
    if required and not value:
        raise ValueError("검수 완료 판본의 소단원 목록이 비어 있습니다.")
    lessons: list[TextbookLesson] = []
    for raw_lesson in value:
        if not isinstance(raw_lesson, dict):
            raise ValueError("교과서 소단원 정보가 객체가 아닙니다.")
        lesson = TextbookLesson(
            number=int(raw_lesson.get("number", 0)),
            label=str(raw_lesson.get("label", "")).strip(),
            problem_page_ranges=_parse_page_ranges(
                raw_lesson.get("problem_page_ranges"),
                field_name="problem_page_ranges",
                allow_empty=True,
            ),
            solution_page_ranges=_parse_page_ranges(
                raw_lesson.get("solution_page_ranges"),
                field_name="solution_page_ranges",
                allow_empty=True,
            ),
            problem_unavailable_reason=str(
                raw_lesson.get("problem_unavailable_reason", "")
            ).strip(),
            solution_unavailable_reason=str(
                raw_lesson.get("solution_unavailable_reason", "")
            ).strip(),
        )
        problem_pages = _flatten_ranges(lesson.problem_page_ranges)
        solution_pages = _flatten_ranges(lesson.solution_page_ranges)
        if problem_pages and max(problem_pages) > problem_page_count:
            raise ValueError(f"{lesson.display_label} 문제 페이지가 PDF 범위를 벗어났습니다.")
        if solution_pages and max(solution_pages) > solution_page_count:
            raise ValueError(f"{lesson.display_label} 해설 페이지가 PDF 범위를 벗어났습니다.")
        lessons.append(lesson)
    expected_numbers = tuple(range(1, len(lessons) + 1))
    actual_numbers = tuple(lesson.number for lesson in lessons)
    if actual_numbers != expected_numbers:
        raise ValueError(
            "교과서 소단원 번호는 1부터 입력 순서대로 연속되어야 합니다: "
            f"{actual_numbers}"
        )
    return tuple(lessons)


def _parse_core_sections(
    value: object,
    *,
    page_count: int,
    lesson_count: int,
    excluded_pages: tuple[int, ...],
    cover_pages: int = 0,
) -> tuple[TextbookCoreSection, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("핵심문제 PDF의 검수된 파트 대응표가 비어 있습니다.")
    sections: list[TextbookCoreSection] = []
    mapped_pages: set[int] = set()
    covered_lessons: set[int] = set()
    for raw_section in value:
        if not isinstance(raw_section, dict):
            raise ValueError("핵심문제 섹션 정보가 객체가 아닙니다.")
        section = TextbookCoreSection(
            lesson_start=int(raw_section.get("lesson_start", 0)),
            lesson_end=int(raw_section.get("lesson_end", 0)),
            page_start=int(raw_section.get("page_start", 0)),
            page_end=int(raw_section.get("page_end", 0)),
            scope=str(raw_section.get("scope", "")),
        )
        if section.page_end > page_count or section.lesson_end > lesson_count:
            raise ValueError("핵심문제 페이지 또는 파트 범위가 PDF·교과서 범위를 벗어났습니다.")
        pages = set(range(section.page_start, section.page_end + 1))
        if mapped_pages.intersection(pages) or (
            sections and section.page_start <= sections[-1].page_end
        ):
            raise ValueError("핵심문제 페이지가 순서대로 한 번씩 등록되지 않았습니다.")
        mapped_pages.update(pages)
        covered_lessons.update(range(section.lesson_start, section.lesson_end + 1))
        sections.append(section)
    if mapped_pages.union(excluded_pages) != set(range(cover_pages + 1, page_count + 1)):
        raise ValueError("핵심문제 페이지가 순서대로 빈틈없이 등록되지 않았습니다.")
    if mapped_pages.intersection(excluded_pages):
        raise ValueError("인쇄 제외 페이지가 핵심문제 섹션과 겹칩니다.")
    if covered_lessons != set(range(1, lesson_count + 1)):
        raise ValueError("핵심문제 대응표에서 누락된 교과서 파트가 있습니다.")
    return tuple(sections)


def _parse_variant_problem_page_ranges(
    value: object, *, page_count: int
) -> tuple[tuple[int, int], ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("핵심문제 변형의 문제 번호별 페이지 대응표가 비어 있습니다.")
    ranges: list[tuple[int, int]] = []
    covered_pages: set[int] = set()
    for number, raw_range in enumerate(value, start=1):
        if not isinstance(raw_range, list) or len(raw_range) != 2:
            raise ValueError(f"핵심문제 변형 {number}번의 페이지 범위가 잘못되었습니다.")
        start, end = map(int, raw_range)
        if start < 3 or end < start or end > page_count:
            raise ValueError(f"핵심문제 변형 {number}번이 본문 페이지를 벗어났습니다.")
        if ranges and (start < ranges[-1][0] or end < ranges[-1][1]):
            raise ValueError("핵심문제 변형 문제 번호의 페이지 순서가 잘못되었습니다.")
        ranges.append((start, end))
        covered_pages.update(range(start, end + 1))
    if covered_pages != set(range(3, page_count + 1)):
        raise ValueError("핵심문제 변형 본문 페이지에 누락이 있습니다.")
    return tuple(ranges)


def load_textbook_catalog(
    catalog_path: Path = TEXTBOOK_CATALOG_PATH,
    root: Path = DEFAULT_TEXTBOOK_ROOT,
) -> TextbookCatalog:
    if not catalog_path.is_file():
        return TextbookCatalog((), (f"교과서 카탈로그를 찾을 수 없습니다: {catalog_path}",))
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return TextbookCatalog((), (f"교과서 카탈로그를 읽을 수 없습니다: {error}",))
    if not isinstance(payload, dict) or int(payload.get("schema_version", 0)) != 1:
        return TextbookCatalog((), ("지원하지 않는 교과서 카탈로그 스키마입니다.",))
    raw_subjects = payload.get("subjects")
    if not isinstance(raw_subjects, list):
        return TextbookCatalog((), ("교과서 카탈로그 subjects가 배열이 아닙니다.",))

    subjects: list[TextbookSubject] = []
    issues: list[str] = []
    seen_subjects: set[str] = set()
    for raw_subject in raw_subjects:
        if not isinstance(raw_subject, dict):
            issues.append("교과서 과목 정보가 객체가 아닙니다.")
            continue
        subject_id = str(raw_subject.get("id", "")).strip()
        subject_label = str(raw_subject.get("label", subject_id)).strip()
        if subject_id not in TEXTBOOK_SUBJECT_ORDER or not subject_label:
            issues.append(f"알 수 없는 교과서 과목입니다: {subject_id or raw_subject!r}")
            continue
        if subject_id in seen_subjects:
            issues.append(f"중복 교과서 과목입니다: {subject_id}")
            continue
        seen_subjects.add(subject_id)
        raw_editions = raw_subject.get("editions")
        if not isinstance(raw_editions, list):
            issues.append(f"{subject_id} 출판사 목록이 배열이 아닙니다.")
            raw_editions = []
        editions: list[TextbookEdition] = []
        seen_publishers: set[str] = set()
        for raw_edition in raw_editions:
            publisher = ""
            try:
                if not isinstance(raw_edition, dict):
                    raise ValueError("판본 정보가 객체가 아닙니다.")
                publisher = str(raw_edition.get("publisher", "")).strip()
                if not publisher or publisher in seen_publishers:
                    raise ValueError(f"출판사명이 비었거나 중복되었습니다: {publisher!r}")
                status = str(raw_edition.get("mapping_status", "pending")).strip()
                if status not in TEXTBOOK_MAPPING_STATUSES:
                    raise ValueError(f"알 수 없는 mapping_status입니다: {status}")
                problem = _parse_pdf_asset(root, raw_edition.get("problem"))
                solution = _parse_pdf_asset(root, raw_edition.get("solution"))
                lessons = _parse_lessons(
                    raw_edition.get("lessons", []),
                    problem_page_count=problem.page_count,
                    solution_page_count=solution.page_count,
                    required=status == "verified",
                )
                core_raw = raw_edition.get("core")
                core = _parse_pdf_asset(root, core_raw) if core_raw is not None else None
                cover_pages = int(raw_edition.get("core_cover_pages", 0))
                if cover_pages not in (0, 2) or (core is None and cover_pages):
                    raise ValueError("핵심문제 표지 페이지 수가 잘못되었습니다.")
                excluded_raw = raw_edition.get("core_excluded_pages", [])
                if not isinstance(excluded_raw, list):
                    raise ValueError("핵심문제 인쇄 제외 페이지 목록이 배열이 아닙니다.")
                core_excluded_pages = tuple(int(page) for page in excluded_raw)
                if (
                    len(set(core_excluded_pages)) != len(core_excluded_pages)
                    or any(page < 1 or (core is not None and page > core.page_count)
                           for page in core_excluded_pages)
                ):
                    raise ValueError("핵심문제 인쇄 제외 페이지가 중복되거나 PDF 범위를 벗어났습니다.")
                if core is None:
                    if raw_edition.get("core_sections") or core_excluded_pages:
                        raise ValueError("핵심문제 PDF 없이 파트 대응표만 등록할 수 없습니다.")
                    core_sections: tuple[TextbookCoreSection, ...] = ()
                elif status == "verified":
                    core_sections = _parse_core_sections(
                        raw_edition.get("core_sections"),
                        page_count=core.page_count,
                        lesson_count=len(lessons),
                        excluded_pages=core_excluded_pages,
                        cover_pages=cover_pages,
                    )
                else:
                    raise ValueError("보류 판본에 핵심문제 PDF를 등록할 수 없습니다.")
                variant_raw = raw_edition.get("variant")
                variant = _parse_pdf_asset(root, variant_raw) if variant_raw is not None else None
                if variant is None:
                    if (raw_edition.get("variant_problem_page_ranges")
                            or raw_edition.get("variant_section_problem_counts")):
                        raise ValueError("핵심문제 변형 PDF 없이 페이지 대응표만 등록할 수 없습니다.")
                    variant_ranges: tuple[tuple[int, int], ...] = ()
                    variant_counts: tuple[int, ...] = ()
                elif status == "verified":
                    variant_ranges = _parse_variant_problem_page_ranges(
                        raw_edition.get("variant_problem_page_ranges"),
                        page_count=variant.page_count,
                    )
                    counts_raw = raw_edition.get("variant_section_problem_counts")
                    if not isinstance(counts_raw, list) or len(counts_raw) != len(core_sections):
                        raise ValueError("핵심문제 변형의 단원별 문항 수가 핵심문제 섹션과 맞지 않습니다.")
                    variant_counts = tuple(int(count) for count in counts_raw)
                    if any(count < 1 for count in variant_counts) or sum(variant_counts) != len(variant_ranges):
                        raise ValueError("핵심문제 변형의 단원별 문항 수 합계가 PDF 문제 번호와 맞지 않습니다.")
                else:
                    raise ValueError("보류 판본에 핵심문제 변형 PDF를 등록할 수 없습니다.")
                if status == "pending" and lessons:
                    raise ValueError(
                        "원본 대기 판본은 추정 범위를 남기지 않고 lessons를 비워야 합니다."
                    )
                if status == "verified":
                    incomplete = tuple(
                        (lesson, document_kind)
                        for lesson in lessons
                        for document_kind in ("문제", "해설")
                        if not lesson.is_available(document_kind)
                    )
                    if incomplete:
                        lesson, document_kind = incomplete[0]
                        raise ValueError(
                            "문제·해설이 불완전한 판본은 일부만 등록하지 않고 판본 전체를 "
                            f"pending으로 보류해야 합니다: {lesson.display_label} / {document_kind}"
                        )
                editions.append(
                    TextbookEdition(
                        subject=subject_id,
                        publisher=publisher,
                        mapping_status=status,
                        problem=problem,
                        solution=solution,
                        lessons=lessons,
                        core=core,
                        core_sections=core_sections,
                        core_excluded_pages=core_excluded_pages,
                        core_cover_pages=cover_pages,
                        variant=variant,
                        variant_problem_page_ranges=variant_ranges,
                        variant_section_problem_counts=variant_counts,
                    )
                )
                seen_publishers.add(publisher)
            except (TypeError, ValueError) as error:
                prefix = f"{subject_id}/{publisher}" if publisher else subject_id
                issues.append(f"{prefix}: {error}")
        subjects.append(TextbookSubject(subject_id, subject_label, tuple(editions)))

    subject_order = {name: index for index, name in enumerate(TEXTBOOK_SUBJECT_ORDER)}
    subjects.sort(key=lambda subject: subject_order[subject.id])
    return TextbookCatalog(tuple(subjects), tuple(issues))


def selected_textbook_page_numbers(
    edition: TextbookEdition,
    document_kind: str,
    lesson_numbers: tuple[int, ...],
) -> tuple[int, ...]:
    if not edition.is_verified:
        raise ValueError(f"검수 완료되지 않은 교과서 판본입니다: {edition.subject} / {edition.publisher}")
    if not lesson_numbers:
        raise ValueError("선택된 교과서 파트가 없습니다.")
    if document_kind == "핵심문제":
        for number in lesson_numbers:
            lesson = edition.lesson(number)
            if lesson is None:
                raise ValueError(
                    f"{edition.subject} / {edition.publisher}에 없는 파트입니다: {number}"
                )
            if not edition.lesson_available(lesson, document_kind):
                raise ValueError(
                    f"{edition.subject} / {edition.publisher} / {lesson.display_label} "
                    f"핵심문제 자료를 인쇄할 수 없습니다: "
                    f"{edition.lesson_unavailable_reason(lesson, document_kind)}"
                )
        selected = set(lesson_numbers)
        return tuple(range(1, edition.core_cover_pages + 1)) + tuple(
            page
            for section in edition.core_sections
            if any(section.includes_lesson(number) for number in selected)
            for page in range(section.page_start, section.page_end + 1)
        )
    pages: list[int] = []
    seen_pages: set[int] = set()
    for number in lesson_numbers:
        lesson = edition.lesson(number)
        if lesson is None:
            raise ValueError(
                f"{edition.subject} / {edition.publisher}에 없는 파트입니다: {number}"
            )
        if not lesson.is_available(document_kind):
            reason = lesson.unavailable_reason(document_kind)
            raise ValueError(
                f"{edition.subject} / {edition.publisher} / {lesson.display_label} "
                f"{document_kind} 자료를 인쇄할 수 없습니다: {reason}"
            )
        for page in _flatten_ranges(lesson.page_ranges(document_kind)):
            if page not in seen_pages:
                pages.append(page)
                seen_pages.add(page)
    return tuple(pages)


def selected_textbook_variant_page_numbers(
    edition: TextbookEdition, problem_numbers: tuple[int, ...]
) -> tuple[int, ...]:
    """Return the two cover pages followed by selected A/B/C problem pages."""
    if not edition.is_verified or edition.variant is None:
        raise ValueError("검수 완료된 핵심문제 변형 PDF가 없습니다.")
    if not problem_numbers:
        raise ValueError("핵심문제 번호를 선택해 주세요.")
    if any(number < 1 or number > len(edition.variant_problem_page_ranges)
           for number in problem_numbers):
        raise ValueError("선택한 핵심문제 번호가 자료 범위를 벗어났습니다.")
    pages = {1, 2}
    for number in problem_numbers:
        start, end = edition.variant_problem_page_ranges[number - 1]
        pages.update(range(start, end + 1))
    return tuple(sorted(pages))


def selected_textbook_variant_problem_numbers(
    edition: TextbookEdition, lesson_numbers: tuple[int, ...]
) -> tuple[int, ...]:
    """Map named textbook lessons to the matching variant A/B/C problem numbers."""
    if not edition.is_verified or edition.variant is None:
        raise ValueError("검수 완료된 핵심문제 변형 PDF가 없습니다.")
    if not lesson_numbers:
        raise ValueError("교과서 단원을 선택해 주세요.")
    if len(edition.variant_section_problem_counts) != len(edition.core_sections):
        raise ValueError("핵심문제 변형 단원 대응표가 완전하지 않습니다.")
    for number in lesson_numbers:
        lesson = edition.lesson(number)
        if lesson is None or not edition.lesson_available(lesson, "핵심문제"):
            raise ValueError(f"{edition.subject} / {edition.publisher}에서 선택할 수 없는 단원입니다: {number}")
    selected = set(lesson_numbers)
    numbers: list[int] = []
    first = 1
    for section, count in zip(edition.core_sections, edition.variant_section_problem_counts):
        if any(section.includes_lesson(number) for number in selected):
            numbers.extend(range(first, first + count))
        first += count
    return tuple(numbers)


def verify_textbook_asset(asset: TextbookPdfAsset, *, verify_sha256: bool = False) -> None:
    if not asset.path.is_file():
        raise FileNotFoundError(f"교과서 PDF를 찾을 수 없습니다: {asset.path}")
    actual_size = asset.path.stat().st_size
    if actual_size != asset.size_bytes:
        raise ValueError(
            f"교과서 PDF 크기가 카탈로그와 다릅니다: {asset.path.name} "
            f"({actual_size}/{asset.size_bytes}바이트)"
        )
    actual_page_count = get_pdf_page_count(asset.path)
    if actual_page_count != asset.page_count:
        raise ValueError(
            f"교과서 PDF 페이지 수가 카탈로그와 다릅니다: {asset.path.name} "
            f"({actual_page_count}/{asset.page_count}쪽)"
        )
    if not verify_sha256:
        return
    digest = hashlib.sha256()
    with asset.path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk)
    actual_sha256 = digest.hexdigest()
    if actual_sha256 != asset.sha256:
        raise ValueError(f"교과서 PDF SHA-256이 카탈로그와 다릅니다: {asset.path.name}")
