from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar, override


Job = TypeVar("Job")


@dataclass(frozen=True, slots=True)
class RetryIndexError(IndexError):
    failed_index: int
    job_count: int

    @override
    def __str__(self) -> str:
        return f"Failed job index {self.failed_index} is outside the pending range 0..{self.job_count - 1}."


@dataclass(frozen=True, slots=True)
class RetryBatch(Generic[Job]):
    jobs: tuple[Job, ...]
    # These values describe the original run, not what happens to be present
    # in the retained suffix.  A name separator may already have been sent,
    # so inspecting only ``jobs`` can incorrectly disable separator handling.
    include_separator_pages: bool = False
    include_name_separator_pages: bool = False
    preceding_name_separator_label: str = ""


def retry_from_failure(
    pending_jobs: Sequence[Job],
    failed_index: int,
    *,
    include_separator_pages: bool = False,
    include_name_separator_pages: bool = False,
    preceding_name_separator_label: str = "",
) -> RetryBatch[Job]:
    if not 0 <= failed_index < len(pending_jobs):
        raise RetryIndexError(failed_index, len(pending_jobs))
    return RetryBatch(
        tuple(pending_jobs[failed_index:]),
        include_separator_pages=include_separator_pages,
        include_name_separator_pages=include_name_separator_pages,
        preceding_name_separator_label=preceding_name_separator_label,
    )
