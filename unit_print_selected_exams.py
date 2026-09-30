from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from unit_print_paths import DATA_ROOT


MODULE_DIR: Final = Path(__file__).resolve().parent
BUNDLED_SELECTED_EXAM_ROOT: Final = DATA_ROOT / "기출엄선"
LEGACY_SELECTED_EXAM_ROOT: Final = Path(r"D:\작업중\기출엄선")
DEFAULT_SELECTED_EXAM_ROOT: Final = (
    BUNDLED_SELECTED_EXAM_ROOT
    if BUNDLED_SELECTED_EXAM_ROOT.is_dir()
    else LEGACY_SELECTED_EXAM_ROOT
)

SELECTED_EXAM_SUBJECT_ORDER: Final = (
    "기하",
    "확통",
    "대수",
    "미적분1",
    "미적분2",
)
SELECTED_EXAM_LESSON_NUMBERS: Final = tuple(range(1, 11))


@dataclass(frozen=True, slots=True)
class SelectedExamLesson:
    number: int
    path: Path

    @property
    def label(self) -> str:
        return f"{self.number}회차"

    @property
    def choice_label(self) -> str:
        return f"{self.number:02d}. {self.label}"

    @property
    def is_available(self) -> bool:
        return self.path.is_file()


@dataclass(frozen=True, slots=True)
class SelectedExamSubject:
    name: str
    lessons: tuple[SelectedExamLesson, ...]

    def find_lesson(self, number: int) -> SelectedExamLesson | None:
        return next((lesson for lesson in self.lessons if lesson.number == number), None)


@dataclass(frozen=True, slots=True)
class SelectedExamCatalog:
    subjects: tuple[SelectedExamSubject, ...]

    @property
    def subject_names(self) -> tuple[str, ...]:
        return tuple(subject.name for subject in self.subjects)

    def find_subject(self, name: str) -> SelectedExamSubject | None:
        return next((subject for subject in self.subjects if subject.name == name), None)


def selected_exam_path(root: Path, subject: str, number: int) -> Path:
    return root / subject / f"기출엄선 {subject} {number}회차.pdf"


def scan_selected_exam_catalog(
    root: Path = DEFAULT_SELECTED_EXAM_ROOT,
) -> SelectedExamCatalog:
    """Return the fixed five-subject, ten-lesson catalog with live file paths."""
    subjects = tuple(
        SelectedExamSubject(
            name=subject,
            lessons=tuple(
                SelectedExamLesson(
                    number=number,
                    path=selected_exam_path(root, subject, number),
                )
                for number in SELECTED_EXAM_LESSON_NUMBERS
            ),
        )
        for subject in SELECTED_EXAM_SUBJECT_ORDER
    )
    return SelectedExamCatalog(subjects)
