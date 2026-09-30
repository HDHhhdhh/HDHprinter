"""Queue selection and retry membership, independent of Tk and printers."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import math
from typing import Any, Callable, TypeVar

from unit_print_retry import RetryBatch

Block = TypeVar("Block")


def queue_hit_index(
    count: int, canvas_y: float, *, padding: float, card_height: float, card_gap: float,
) -> int | None:
    """Find a uniform queue card without scanning the whole queue.

    Card edges are inclusive and the space between cards is not selectable.
    At a shared edge with no gap, retain the earlier card's hit priority.
    """
    if count <= 0 or canvas_y < padding or not math.isfinite(canvas_y):
        return None
    if card_height <= 0 or card_gap < 0:
        raise ValueError("Queue cards need a positive height and nonnegative gap.")
    stride = card_height + card_gap
    offset = canvas_y - padding
    index = math.floor(offset / stride)
    if card_gap == 0 and index > 0 and offset == index * stride:
        index -= 1
    if index >= count or offset - index * stride > card_height:
        return None
    return index


def queue_drop_index(
    count: int, canvas_y: float, *, padding: float, card_height: float, card_gap: float,
) -> int:
    """Find the insertion slot by card midpoint in constant time."""
    if count <= 0:
        return 0
    if card_height <= 0 or card_gap < 0:
        raise ValueError("Queue cards need a positive height and nonnegative gap.")
    if not math.isfinite(canvas_y):
        return 0 if canvas_y < 0 else count
    first_midpoint = padding + card_height / 2
    slot = math.floor((canvas_y - first_midpoint) / (card_height + card_gap)) + 1
    return max(0, min(count, slot))


def waiting_blocks(
    blocks: Iterable[Block], statuses: Mapping[int, str], scheduled_ids: Iterable[int] = (),
) -> list[Block]:
    scheduled = set(scheduled_ids)
    return [block for block in blocks
            if id(block) not in scheduled and statuses.get(id(block), "대기") == "대기"]


def retain_visible_retry_jobs(batch: RetryBatch | None, blocks: Iterable[object]) -> RetryBatch | None:
    if batch is None:
        return None
    visible_ids = {id(block) for block in blocks}
    jobs = tuple(job for job in batch.jobs if job.block_uid in visible_ids)
    return (
        RetryBatch(
            jobs,
            include_separator_pages=batch.include_separator_pages,
            include_name_separator_pages=batch.include_name_separator_pages,
            preceding_name_separator_label=batch.preceding_name_separator_label,
        )
        if jobs else None
    )


@dataclass(frozen=True)
class QueueSnapshot:
    # Keep original block identity: re-creating completed blocks would make
    # them look new and detach the unsent jobs from their visible blocks.
    blocks: tuple[Any, ...]
    statuses: dict[int, str]
    details: dict[int, str]
    selected_index: int | None
    retry_batch: RetryBatch | None
    selected_indices: tuple[int, ...] | None = None
    selection_anchor: int | None = None


@dataclass(frozen=True)
class QueueReorderResult:
    """Projected queue order after moving one or more selected items as a group."""

    items: tuple[Any, ...]
    selected_indices: tuple[int, ...]
    focus_index: int | None
    insertion_index: int
    changed: bool


@dataclass(frozen=True)
class PendingNameRepair:
    """A rebuilt, unstarted queue suffix and its observable job-ID changes."""

    jobs: tuple[Any, ...]
    removed_job_ids: tuple[str, ...]
    added_jobs: tuple[Any, ...]
    trailing_name_label: str


def reconcile_pending_name_groups(
    jobs: Iterable[Any],
    ordered_scheduled_blocks: Iterable[Block],
    removed_block_ids: Iterable[int],
    protected_block_ids: Iterable[int],
    *,
    enabled: bool,
    preceding_name_label: object,
    normalize_label: Callable[[object], str],
    block_uses_name_separator: Callable[[Block], bool],
    block_name_label: Callable[[Block], object],
    job_name_label: Callable[[Any], object],
    make_name_separator_job: Callable[[Block, str, Any], Any],
) -> PendingNameRepair:
    """Repair name separators while preserving the started prefix byte-for-byte.

    The function performs no file, printer, history, or Tk work.  Non-name
    jobs keep identity and order; only name-separator jobs may be removed or
    created in the unprotected suffix.
    """

    original = tuple(jobs)
    removed_ids = set(removed_block_ids)
    protected_ids = set(protected_block_ids)
    if removed_ids & protected_ids:
        raise ValueError("시작된 블록은 실행 큐에서 제거할 수 없습니다.")
    if not enabled:
        kept = tuple(job for job in original if job.block_uid not in removed_ids)
        kept_job_ids = {job.job_id for job in kept}
        return PendingNameRepair(
            kept,
            tuple(job.job_id for job in original if job.job_id not in kept_job_ids),
            (),
            normalize_label(preceding_name_label),
        )

    blocks = tuple(
        block for block in ordered_scheduled_blocks if id(block) not in removed_ids
    )
    scheduled_ids = {id(block) for block in blocks}
    protected_end = max(
        (
            index
            for index, job in enumerate(original)
            if job.block_uid in protected_ids
        ),
        default=-1,
    )
    protected_prefix = original[: protected_end + 1]
    suffix = original[protected_end + 1 :]
    bodies: dict[int, list[Any]] = {}
    existing_names: dict[int, Any] = {}
    for job in suffix:
        if job.block_uid not in scheduled_ids:
            continue
        if job.is_name_separator:
            existing_names.setdefault(job.block_uid, job)
        else:
            bodies.setdefault(job.block_uid, []).append(job)

    previous = normalize_label(preceding_name_label)
    rebuilt: list[Any] = []
    added: list[Any] = []
    for block in blocks:
        if id(block) in protected_ids:
            continue
        body_jobs = bodies.get(id(block), [])
        if not block_uses_name_separator(block):
            expected_label = ""
            previous = ""
        else:
            current_label = normalize_label(block_name_label(block))
            expected_label = current_label if current_label and current_label != previous else ""
            previous = current_label
        if expected_label and body_jobs:
            name_job = existing_names.get(id(block))
            if name_job is None or normalize_label(job_name_label(name_job)) != expected_label:
                name_job = make_name_separator_job(block, expected_label, body_jobs[0])
                added.append(name_job)
            rebuilt.append(name_job)
        rebuilt.extend(body_jobs)

    repaired = protected_prefix + tuple(rebuilt)
    repaired_ids = {job.job_id for job in repaired}
    return PendingNameRepair(
        repaired,
        tuple(job.job_id for job in original if job.job_id not in repaired_ids),
        tuple(added),
        previous,
    )


def reorder_selected_items(
    items: Iterable[Block],
    selected_indices: Iterable[int],
    drop_index: int,
    *,
    focus_index: int | None = None,
) -> QueueReorderResult:
    """Move selected items together while preserving their relative order.

    ``drop_index`` is an insertion slot in the original queue, from zero through
    ``len(items)``. Removing the selection first means selected rows before that
    slot must be subtracted to find the insertion slot in the remaining queue.
    """

    original = tuple(items)
    selected = tuple(sorted({index for index in selected_indices if 0 <= index < len(original)}))
    if not selected:
        bounded_drop = max(0, min(drop_index, len(original)))
        return QueueReorderResult(original, (), None, bounded_drop, False)

    bounded_drop = max(0, min(drop_index, len(original)))
    selected_set = set(selected)
    moved = tuple(original[index] for index in selected)
    remaining = tuple(item for index, item in enumerate(original) if index not in selected_set)
    insertion_index = bounded_drop - sum(index < bounded_drop for index in selected)
    insertion_index = max(0, min(insertion_index, len(remaining)))
    reordered = remaining[:insertion_index] + moved + remaining[insertion_index:]
    new_selected = tuple(range(insertion_index, insertion_index + len(moved)))

    if focus_index in selected_set:
        new_focus = insertion_index + selected.index(focus_index)
    else:
        new_focus = new_selected[0]
    changed = any(before is not after for before, after in zip(original, reordered))
    return QueueReorderResult(reordered, new_selected, new_focus, insertion_index, changed)
