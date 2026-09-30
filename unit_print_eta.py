"""Conservative physical-print ETA estimates.

The estimate is deliberately simple.  It learns only from device-confirmed
normal completions and falls back to an operator-friendly range when there is
not enough local evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import median


ETA_PRINT_RATE_CONFIG_KEY = "eta_print_seconds_per_sheet"
ETA_SAMPLE_LIMIT = 50
MIN_RATE_SAMPLE_SHEETS = 4
DEFAULT_FINISHING_SECONDS_PER_JOB = 8.0


def estimated_sheets(page_count: int, copies: int, duplex: bool) -> int:
    pages_per_copy = max(0, int(page_count))
    copy_count = max(0, int(copies))
    sheets_per_copy = (pages_per_copy + 1) // 2 if duplex else pages_per_copy
    return sheets_per_copy * copy_count


@dataclass(frozen=True)
class EtaEstimate:
    seconds_low: int
    seconds_high: int
    confidence: str

    @property
    def display_text(self) -> str:
        def minutes(seconds: int) -> int:
            return max(1, round(seconds / 60))

        low = minutes(self.seconds_low)
        high = minutes(self.seconds_high)
        if low == high:
            return f"약 {low}분"
        return f"약 {low}~{high}분"


def estimate_remaining_seconds(
    sheets: int,
    verified_seconds_per_sheet: list[float] | tuple[float, ...] = (),
    *,
    finishing_seconds_per_job: float = DEFAULT_FINISHING_SECONDS_PER_JOB,
    remaining_jobs: int = 1,
) -> EtaEstimate:
    # These samples represent printing time after fixed per-document handling
    # has been removed.  Larger values are queue/hold latency, not engine rate.
    clean = [value for value in verified_seconds_per_sheet if 0.05 <= value <= 5.0]
    if len(clean) >= 5:
        center = median(clean[-50:])
        low_rate, high_rate = center * 0.8, center * 1.35
        confidence = "학습됨"
    else:
        # Wide bounds are more honest than presenting the engine's rated PPM as
        # an exact promise; duplexing, stapling, and rasterization dominate short
        # jobs.
        # Keep the upper bound broad enough for rasterization and mixed
        # duplex work while avoiding the old queue-wait contamination.
        low_rate, high_rate = 0.45, 3.0
        confidence = "초기 추정"
    finishing = max(0, remaining_jobs) * max(0.0, finishing_seconds_per_job)
    return EtaEstimate(
        seconds_low=max(0, round(max(0, sheets) * low_rate + finishing)),
        seconds_high=max(0, round(max(0, sheets) * high_rate + finishing)),
        confidence=confidence,
    )


def completed_job_seconds_per_sheet(
    started_at: datetime | None,
    ended_at: datetime | None,
    sheets: int,
    *,
    finishing_seconds_per_job: float = DEFAULT_FINISHING_SECONDS_PER_JOB,
    minimum_sheets: int = MIN_RATE_SAMPLE_SHEETS,
) -> float | None:
    """Return engine time per sheet from Canon's own job timestamps.

    Submission-to-completion time is intentionally rejected because a rolling
    window includes time spent behind earlier documents.  Very short jobs are
    also excluded: their fixed setup/finishing cost dominates the sheet rate.
    """

    sheet_count = max(0, int(sheets))
    if started_at is None or ended_at is None or sheet_count < max(1, minimum_sheets):
        return None
    elapsed = (ended_at - started_at).total_seconds()
    if elapsed <= 0:
        return None
    variable_seconds = elapsed - max(0.0, finishing_seconds_per_job)
    if variable_seconds <= 0:
        return None
    rate = variable_seconds / sheet_count
    if not 0.05 <= rate <= 5.0:
        return None
    return rate
