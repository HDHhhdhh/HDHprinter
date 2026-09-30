from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from unit_print_paths import DATA_ROOT


APP_DIR = Path(__file__).resolve().parent
BUNDLED_DATA_ROOT = DATA_ROOT
TEST_LEVEL_CHOICES = ("중상", "상")
TEST_TERM_CHOICES = ("중간", "기말")
TEST_KIND_ORDER = ("복습테스트", "학군지기출", "일산기출")
TERM_FILTER_TEST_KINDS = frozenset({"학군지기출", "일산기출"})
TEST_SOURCE_BY_KIND = {
    "복습테스트": "test_review",
    "학군지기출": "test_school_district",
    "일산기출": "test_ilsan",
}
TEST_KIND_BY_SOURCE = {source: kind for kind, source in TEST_SOURCE_BY_KIND.items()}


def portable_or_legacy_root(folder_name: str, legacy_path: str) -> Path:
    portable = BUNDLED_DATA_ROOT / folder_name
    return portable if portable.is_dir() else Path(legacy_path)


DEFAULT_REVIEW_TEST_ROOT = portable_or_legacy_root(
    "복습 테스트", r"D:\테스트\복습 테스트"
)
DEFAULT_SCHOOL_DISTRICT_ROOT = portable_or_legacy_root(
    "학군지 기출", r"D:\테스트\학군지 기출"
)
DEFAULT_ILSAN_ROOT = portable_or_legacy_root(
    "일산 기출", r"D:\테스트\일산 기출"
)
DEFAULT_TEST_ROOTS = {
    "복습테스트": DEFAULT_REVIEW_TEST_ROOT,
    "학군지기출": DEFAULT_SCHOOL_DISTRICT_ROOT,
    "일산기출": DEFAULT_ILSAN_ROOT,
}


SUBJECT_ALIASES = {
    "공수1": "공통수학1",
    "공수2": "공통수학2",
    "미적1": "미적분1",
    "미적2": "미적분2",
    "확통": "확률과 통계",
}

EXCLUDED_FILE_MARKERS = (
    "해설",
    "손풀이",
    "정답",
    "답지",
    "참고용",
    "solution",
)
EXCLUDED_DIRECTORY_MARKERS = (
    "안씀",
    "사용안",
    "한글 및 해설",
)


def natural_key(text: str) -> tuple[object, ...]:
    return tuple(
        int(part) if part.isdigit() else part
        for part in re.split(r"(\d+)", text.casefold())
    )


def normalize_subject_name(value: str) -> str:
    normalized = value.strip()
    return SUBJECT_ALIASES.get(normalized, normalized)


def level_tokens(value: str) -> tuple[str, ...]:
    return tuple(
        token
        for token in re.split(r"[\s._()\[\]{}\-]+", value.strip())
        if token
    )


def detect_test_level(parent_parts: tuple[str, ...], kind: str) -> str | None:
    tokens = tuple(token for part in parent_parts for token in level_tokens(part))
    if "중상" in tokens:
        return "중상"
    if kind == "복습테스트" and "중" in tokens:
        return "중상"
    if "상" in tokens:
        return "상"
    return None


def detect_test_term(path_parts: tuple[str, ...]) -> str | None:
    for term in TEST_TERM_CHOICES:
        if any(term in part for part in path_parts):
            return term
    return None


def test_filter_choices(kind: str) -> tuple[str, ...]:
    return TEST_TERM_CHOICES if kind in TERM_FILTER_TEST_KINDS else TEST_LEVEL_CHOICES


def test_filter_label(kind: str) -> str:
    return "시험 구분" if kind in TERM_FILTER_TEST_KINDS else "레벨"


def is_level_part(value: str, kind: str) -> bool:
    return detect_test_level((value,), kind) is not None


def is_excluded_directory(value: str) -> bool:
    lowered = value.casefold().strip()
    if lowered.startswith(("_backup", "backup", ".")):
        return True
    if lowered == "잡":
        return True
    return any(marker.casefold() in lowered for marker in EXCLUDED_DIRECTORY_MARKERS)


def is_problem_pdf(path: Path) -> bool:
    lowered = path.stem.casefold()
    return not any(marker.casefold() in lowered for marker in EXCLUDED_FILE_MARKERS)


def embedded_round_number(value: str) -> int | None:
    match = re.search(r"(?:^|\D)0*(\d+)\s*회차", value)
    if match:
        return int(match.group(1))
    match = re.match(r"^\s*0*(\d+)\s*[.]", value)
    return int(match.group(1)) if match else None


def term_order(parent_parts: tuple[str, ...]) -> int:
    if any("중간" in part for part in parent_parts):
        return 0
    if any("기말" in part for part in parent_parts):
        return 1
    return 2


def clean_document_stem(value: str) -> str:
    cleaned = re.sub(r"\s+인쇄\s*$", "", value).strip()
    cleaned = re.sub(r"(?i)\s*pdf\s*$", "", cleaned).strip()
    return re.sub(r"\s+", " ", cleaned)


def document_label(relative_path: Path, kind: str) -> str:
    if kind in TERM_FILTER_TEST_KINDS:
        return clean_document_stem(relative_path.stem)
    parent_parts = tuple(
        part
        for part in relative_path.parts[1:-1]
        if not is_level_part(part, kind)
    )
    context = [part for part in parent_parts if part not in {"자료", "PDF"}]
    stem = clean_document_stem(relative_path.stem)
    return " / ".join((*context, stem)) if context else stem


def term_document_label(document: "TestDocument", level: str, position: int) -> str:
    descriptor = document.label.replace("_", " ")
    descriptor = re.sub(r"^\s*0*\d+\s*(?:회차|[.])\s*", "", descriptor)
    descriptor = " ".join(
        token
        for token in descriptor.split()
        if token not in {*TEST_LEVEL_CHOICES, *TEST_TERM_CHOICES}
    )
    prefix = f"{level} {position}회차"
    return f"{prefix} {descriptor}" if descriptor else prefix


@dataclass(frozen=True, slots=True)
class TestDocument:
    number: int
    label: str
    path: Path
    relative_path: Path
    embedded_number: int | None = None
    level: str = ""
    term: str | None = None


@dataclass(frozen=True, slots=True)
class TestSubject:
    name: str
    source_directory_name: str
    middle_high: tuple[TestDocument, ...]
    high: tuple[TestDocument, ...]

    def documents(self, selection: str) -> tuple[TestDocument, ...]:
        if selection == "중상":
            return self.middle_high
        if selection == "상":
            return self.high
        if selection in TEST_TERM_CHOICES:
            return self.term_documents(selection)
        return ()

    def term_documents(self, term: str) -> tuple[TestDocument, ...]:
        middle_high = tuple(document for document in self.middle_high if document.term == term)
        high = tuple(document for document in self.high if document.term == term)

        def remap(
            document: TestDocument,
            *,
            number: int,
            level: str,
            position: int,
        ) -> TestDocument:
            return TestDocument(
                number=number,
                label=term_document_label(document, level, position),
                path=document.path,
                relative_path=document.relative_path,
                embedded_number=document.embedded_number,
                level=level,
                term=term,
            )

        primary_middle = [
            remap(document, number=position, level="중상", position=position)
            for position, document in enumerate(middle_high[:10], start=1)
        ]
        high_documents = [
            remap(document, number=position + 10, level="상", position=position)
            for position, document in enumerate(high, start=1)
        ]
        overflow_middle = [
            remap(
                document,
                number=10 + len(high) + offset,
                level="중상",
                position=position,
            )
            for offset, (position, document) in enumerate(
                enumerate(middle_high[10:], start=11),
                start=1,
            )
        ]
        return tuple((*primary_middle, *high_documents, *overflow_middle))


@dataclass(frozen=True, slots=True)
class TestCatalog:
    kind: str
    root: Path
    subjects: tuple[TestSubject, ...]
    scanned_pdf_count: int = 0
    excluded_solution_count: int = 0
    excluded_directory_count: int = 0
    unclassified_level_count: int = 0
    unclassified_term_count: int = 0
    excluded_legacy_fallback_count: int = 0

    @property
    def subject_names(self) -> tuple[str, ...]:
        return tuple(subject.name for subject in self.subjects)

    @property
    def file_count(self) -> int:
        return sum(
            len(subject.middle_high) + len(subject.high)
            for subject in self.subjects
        )

    def find_subject(self, name: str) -> TestSubject | None:
        return next((subject for subject in self.subjects if subject.name == name), None)

    def documents(self, subject: str, level: str) -> tuple[TestDocument, ...]:
        found = self.find_subject(subject)
        return found.documents(level) if found is not None else ()


@dataclass(frozen=True, slots=True)
class _RawDocument:
    label: str
    path: Path
    relative_path: Path
    embedded_number: int | None
    level: str
    term: str | None
    sort_key: tuple[object, ...]


def scan_test_catalog(root: Path, kind: str) -> TestCatalog:
    if kind not in TEST_KIND_ORDER:
        raise ValueError(f"알 수 없는 테스트 자료 종류입니다: {kind}")
    if not root.is_dir():
        return TestCatalog(kind, root, ())

    scanned_pdf_count = 0
    excluded_solution_count = 0
    excluded_directory_count = 0
    unclassified_level_count = 0
    unclassified_term_count = 0
    excluded_legacy_fallback_count = 0
    subjects: list[TestSubject] = []

    subject_directories = sorted(
        (path for path in root.iterdir() if path.is_dir()),
        key=lambda path: natural_key(path.name),
    )
    for subject_directory in subject_directories:
        by_level: dict[str, list[_RawDocument]] = {level: [] for level in TEST_LEVEL_CHOICES}
        explicit_terms: set[str] = set()
        legacy_flat_paths: set[Path] = set()
        for path in sorted(subject_directory.rglob("*.pdf"), key=lambda item: natural_key(str(item))):
            scanned_pdf_count += 1
            relative_path = path.relative_to(root)
            parent_parts = tuple(relative_path.parts[1:-1])
            if any(is_excluded_directory(part) for part in parent_parts):
                excluded_directory_count += 1
                continue
            if not is_problem_pdf(path):
                excluded_solution_count += 1
                continue
            term = detect_test_term((*parent_parts, path.stem))
            if kind in TERM_FILTER_TEST_KINDS and term is None:
                unclassified_term_count += 1
                continue
            level = detect_test_level(parent_parts, kind)
            if level is None:
                if kind != "일산기출":
                    unclassified_level_count += 1
                    continue
                if (
                    len(relative_path.parts) == 3
                    and relative_path.parts[1] == term
                    and re.fullmatch(r"\d+\s*회차", path.stem)
                ):
                    legacy_flat_paths.add(relative_path)
                level = "중상"
            elif kind == "일산기출" and term is not None:
                explicit_terms.add(term)
            label = document_label(relative_path, kind)
            number = embedded_round_number(path.stem)
            sort_key = (
                term_order(parent_parts),
                number if number is not None else 10**9,
                natural_key(label),
                natural_key(relative_path.as_posix()),
            )
            by_level[level].append(
                _RawDocument(label, path, relative_path, number, level, term, sort_key)
            )

        documents_by_level: dict[str, tuple[TestDocument, ...]] = {}
        for level, raw_documents in by_level.items():
            # Retain baseline PDFs on disk, but prefer the renewed level-based
            # collection over old term/N회차.pdf placeholders for the same term.
            selected_documents = [
                item for item in raw_documents
                if not (item.relative_path in legacy_flat_paths and item.term in explicit_terms)
            ]
            excluded_legacy_fallback_count += len(raw_documents) - len(selected_documents)
            ordered = sorted(selected_documents, key=lambda item: item.sort_key)
            documents_by_level[level] = tuple(
                TestDocument(
                    number=index,
                    label=item.label,
                    path=item.path,
                    relative_path=item.relative_path,
                    embedded_number=item.embedded_number,
                    level=item.level,
                    term=item.term,
                )
                for index, item in enumerate(ordered, start=1)
            )
        subjects.append(
            TestSubject(
                normalize_subject_name(subject_directory.name),
                subject_directory.name,
                documents_by_level["중상"],
                documents_by_level["상"],
            )
        )

    subjects.sort(key=lambda subject: natural_key(subject.name))
    return TestCatalog(
        kind,
        root,
        tuple(subjects),
        scanned_pdf_count,
        excluded_solution_count,
        excluded_directory_count,
        unclassified_level_count,
        unclassified_term_count,
        excluded_legacy_fallback_count,
    )


def scan_default_test_catalogs() -> dict[str, TestCatalog]:
    return {
        kind: scan_test_catalog(DEFAULT_TEST_ROOTS[kind], kind)
        for kind in TEST_KIND_ORDER
    }
