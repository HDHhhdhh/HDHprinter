from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from unit_print_paths import DATA_ROOT


MODULE_DIR: Final = Path(__file__).resolve().parent
BUNDLED_GYOPYEONG_ROOT: Final = DATA_ROOT / "교평 기출"
LEGACY_GYOPYEONG_ROOT: Final = Path(r"D:\교재\고3 기출")
DEFAULT_GYOPYEONG_ROOT: Final = (
    BUNDLED_GYOPYEONG_ROOT
    if BUNDLED_GYOPYEONG_ROOT.is_dir()
    else LEGACY_GYOPYEONG_ROOT
)

_FILENAME_PATTERN = re.compile(
    r"^(?P<subject>\S+)\s+(?P<part>중간|기말|전범위)\s+(?P<label>.+?)$"
)
_SUBJECT_ALIASES: Final = {
    "공수1": "공통수학1",
    "공수2": "공통수학2",
    "미적1": "미적분1",
    "미적2": "미적분2",
    "확통": "확률과 통계",
}
_SUBJECT_ORDER: Final = {
    name: index
    for index, name in enumerate(
        ("공통수학1", "공통수학2", "기하", "대수", "미적분1", "미적분2", "확률과 통계")
    )
}
_PART_ORDER: Final = {"중간": 0, "기말": 1, "전범위": 2}


@dataclass(frozen=True, slots=True)
class GyopyeongLesson:
    number: int
    label: str
    path: Path

    @property
    def choice_label(self) -> str:
        return f"{self.number:02d}. {self.label}"


@dataclass(frozen=True, slots=True)
class GyopyeongPart:
    name: str
    lessons: tuple[GyopyeongLesson, ...]

    def find_lesson(self, number: int) -> GyopyeongLesson | None:
        return next((lesson for lesson in self.lessons if lesson.number == number), None)


@dataclass(frozen=True, slots=True)
class GyopyeongSubject:
    name: str
    parts: tuple[GyopyeongPart, ...]

    @property
    def part_names(self) -> tuple[str, ...]:
        return tuple(part.name for part in self.parts)

    @property
    def has_full_range_only(self) -> bool:
        return self.part_names == ("전범위",)

    def find_part(self, name: str) -> GyopyeongPart | None:
        return next((part for part in self.parts if part.name == name), None)


@dataclass(frozen=True, slots=True)
class GyopyeongCatalog:
    subjects: tuple[GyopyeongSubject, ...]

    @property
    def subject_names(self) -> tuple[str, ...]:
        return tuple(subject.name for subject in self.subjects)

    def find_subject(self, name: str) -> GyopyeongSubject | None:
        return next((subject for subject in self.subjects if subject.name == name), None)


def parse_gyopyeong_filename(path: Path) -> tuple[str, str, str, bool] | None:
    """Return subject, part, lesson label, and whether the PDF is a solution."""
    if path.suffix.casefold() != ".pdf":
        return None
    stem = path.stem.strip()
    is_solution = bool(re.search(r"(?:_|\s|-)*해설$", stem))
    if is_solution:
        stem = re.sub(r"(?:_|\s|-)*해설$", "", stem).strip()
    match = _FILENAME_PATTERN.fullmatch(stem)
    if match is None:
        return None
    subject_token = re.sub(r"\s+", "", match.group("subject"))
    subject = _SUBJECT_ALIASES.get(subject_token, subject_token)
    label = re.sub(r"\s+", " ", match.group("label")).strip(" _-")
    if not label:
        return None
    return subject, match.group("part"), label, is_solution


def _lesson_sort_key(label: str) -> tuple[int, str]:
    if "준킬러" in label:
        return 0, label.casefold()
    if "킬러" in label:
        return 1, label.casefold()
    return 2, label.casefold()


def scan_gyopyeong_catalog(root: Path = DEFAULT_GYOPYEONG_ROOT) -> GyopyeongCatalog:
    """Scan student-facing problem PDFs; matching solution PDFs stay excluded."""
    if not root.is_dir():
        return GyopyeongCatalog(())

    grouped: dict[tuple[str, str], dict[str, Path]] = {}
    for path in sorted(root.glob("*.pdf"), key=lambda item: item.name.casefold()):
        parsed = parse_gyopyeong_filename(path)
        if parsed is None:
            continue
        subject, part, label, is_solution = parsed
        if is_solution:
            continue
        grouped.setdefault((subject, part), {}).setdefault(label, path)

    parts_by_subject: dict[str, list[GyopyeongPart]] = {}
    for (subject, part), lessons_by_label in grouped.items():
        ordered = sorted(lessons_by_label.items(), key=lambda item: _lesson_sort_key(item[0]))
        lessons = tuple(
            GyopyeongLesson(number=index, label=label, path=path)
            for index, (label, path) in enumerate(ordered, start=1)
        )
        parts_by_subject.setdefault(subject, []).append(GyopyeongPart(part, lessons))

    subjects = tuple(
        GyopyeongSubject(
            name=subject,
            parts=tuple(sorted(parts, key=lambda item: _PART_ORDER.get(item.name, 99))),
        )
        for subject, parts in sorted(
            parts_by_subject.items(),
            key=lambda item: (_SUBJECT_ORDER.get(item[0], 99), item[0].casefold()),
        )
    )
    return GyopyeongCatalog(subjects)
