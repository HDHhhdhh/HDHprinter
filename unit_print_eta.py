"""Physical-print ETA from classified, queue-free job timings.

The estimate is deliberately simple.  It learns only from device-confirmed
normal completions and falls back to an operator-friendly range when there is
not enough local evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median


ETA_PRINT_RATE_CONFIG_KEY = "eta_print_seconds_per_sheet"
ETA_SAMPLE_LIMIT = 50
MIN_RATE_SAMPLE_SHEETS = 4
DEFAULT_FINISHING_SECONDS_PER_JOB = 8.0
ETA_TIMING_CONFIG_KEY = "eta_job_timing_samples"
ETA_TIMING_DEVICE_KEY = "eta_job_timing_device"
ETA_TIMING_SAMPLE_LIMIT = 100


@dataclass(frozen=True, slots=True)
class EtaWorkload:
    sheets: int
    copies: int = 1
    dispatch_jobs: int = 1
    duplex: bool = True


@dataclass(frozen=True, slots=True)
class EtaTimingSample:
    """One completed device job, with queue waiting removed from its duration."""

    sheets: int
    copies: int
    seconds: float
    duplex: bool = True


def timing_device_key(base_url: str) -> str:
    return hashlib.sha256(base_url.strip().rstrip("/").casefold().encode()).hexdigest()


def validated_timing_samples(raw: object) -> tuple[EtaTimingSample, ...]:
    if not isinstance(raw, list):
        return ()
    result = []
    for item in raw[-ETA_TIMING_SAMPLE_LIMIT:]:
        if not isinstance(item, dict):
            continue
        sheets, copies, seconds = (item.get(key) for key in ("sheets", "copies", "seconds"))
        duplex = item.get("duplex")
        if (type(sheets) is not int or type(copies) is not int
                or not 1 <= copies <= sheets <= 1_000_000
                or type(seconds) not in (int, float) or not math.isfinite(seconds)
                or not 0 < seconds <= 6 * 3600 or type(duplex) is not bool):
            continue
        result.append(EtaTimingSample(sheets, copies, float(seconds), duplex))
    return tuple(result)


def initialize_timing_samples(config: dict[str, object], bootstrap_path: Path) -> None:
    """Import an optional, bounded local numeric calibration once at startup."""
    key = timing_device_key(str(config.get("base_url", "")))
    if config.get(ETA_TIMING_DEVICE_KEY) == key and validated_timing_samples(config.get(ETA_TIMING_CONFIG_KEY)):
        return
    try:
        if bootstrap_path.stat().st_size > 64 * 1024:
            return
        data = json.loads(bootstrap_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("device_key") != key:
            return
        samples = validated_timing_samples(data.get("samples"))
    except (OSError, ValueError):
        return
    if samples:
        config[ETA_TIMING_CONFIG_KEY] = [asdict(sample) for sample in samples]
        config[ETA_TIMING_DEVICE_KEY] = key


def completed_job_timing(
    started_at: datetime | None,
    ended_at: datetime | None,
    sheets: int,
    copies: int,
    *,
    previous_completed_at: datetime | None = None,
    duplex: bool = True,
) -> EtaTimingSample | None:
    if started_at is None or ended_at is None:
        return None
    if previous_completed_at is not None:
        started_at = max(started_at, previous_completed_at)
    raw = [{"sheets": sheets, "copies": copies,
            "seconds": (ended_at - started_at).total_seconds(), "duplex": duplex}]
    samples = validated_timing_samples(raw)
    return samples[0] if samples else None


class ActivePrintTime:
    """Count observed printing time; queued work and holds consume no time."""

    def __init__(self) -> None:
        self._job_id: str | None = None
        self._observed_at = 0.0
        self._elapsed: dict[str, float] = {}

    def observe(self, job_id: str | None, now: float, *, started_at: float | None = None) -> None:
        if job_id is not None and job_id not in self._elapsed and started_at is not None:
            self._elapsed[job_id] = max(0.0, now - started_at)
        if job_id is not None and job_id == self._job_id:
            self._elapsed[job_id] = self._elapsed.get(job_id, 0.0) + max(0.0, now - self._observed_at)
        self._job_id, self._observed_at = job_id, now

    def elapsed(self, job_id: str) -> float:
        return self._elapsed.get(job_id, 0.0)


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
    seconds_expected: int | None = None

    @property
    def display_text(self) -> str:
        def minutes(seconds: int) -> int:
            return max(1, round(seconds / 60))

        low = minutes(self.seconds_low)
        high = minutes(self.seconds_high)
        if low == high:
            return f"약 {low}분"
        return f"약 {low}~{high}분"


@dataclass(frozen=True)
class CompletionTimeProjection:
    """An anchored forecast, never a new duration added on each UI refresh."""

    eta: EtaEstimate
    observed_at: datetime

    @property
    def completion_at(self) -> datetime:
        seconds = max(0.0, self.eta.seconds_expected if self.eta.seconds_expected is not None
                      else (self.eta.seconds_low + self.eta.seconds_high) / 2)
        target = self.observed_at + timedelta(seconds=seconds)
        # A minute-resolution clock must not round a forecast earlier.
        if target.second or target.microsecond:
            target = target.replace(second=0, microsecond=0) + timedelta(minutes=1)
        return target

    def display_text(self, now: datetime, *, overdue_text: str = "완료 확인 중") -> str:
        target = self.completion_at
        if target.tzinfo is not None and now.tzinfo is not None:
            target = target.astimezone(now.tzinfo)
        days = (target.date() - now.date()).days
        date_label = ""
        if days == 1:
            date_label = "내일 "
        elif days != 0:
            date_label = f"{target.month}/{target.day} "
            if target.year != now.year:
                date_label = f"{target.year}/{date_label}"
        period = "오전" if target.hour < 12 else "오후"
        hour = target.hour % 12 or 12
        text = f"예상 완료 시간 {date_label}{period} {hour}:{target.minute:02d}"
        # A forecast passing its clock time is not evidence of completion.
        if now > target:
            text += f" · {overdue_text}"
        return text


@dataclass(frozen=True)
class PrintEtaObservation:
    """Immutable numeric progress delivered from the worker to the UI."""

    run_id: str | None
    eta: EtaEstimate
    observed_at: datetime
    remaining_job_ids: tuple[str, ...]
    rate_samples: tuple[float, ...]
    remaining_sheets: int
    held: bool = False
    complete: bool = True
    timing_samples: tuple[EtaTimingSample, ...] = ()
    projection_key: tuple[object, ...] | None = None

    @property
    def anchor_key(self) -> tuple[object, ...]:
        # Submission moves a job from pending to outstanding. It does not
        # remove it from the unfinished work or reset the clock.
        if self.projection_key is not None:
            return self.projection_key
        return (self.run_id, self.remaining_job_ids, self.rate_samples, self.timing_samples, self.eta,
                self.held, self.complete)

    def display_text(self, projection: CompletionTimeProjection, now: datetime) -> str:
        if self.held:
            return "인쇄 중지 · 재개 시 재계산"
        if not self.complete:
            return "예상 완료 시간 일부 계산 불가"
        if not self.remaining_job_ids:
            return "완료 확인 중"
        return projection.display_text(now)


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


def _work_class(sheets: float, copies: int) -> str:
    return "repeat" if copies > 1 else "long" if sheets >= 20 else "short"


def estimate_workload_seconds(
    workload: EtaWorkload,
    samples: tuple[EtaTimingSample, ...] = (),
) -> EtaEstimate:
    """Estimate per dispatch, separating repeated copies from short PDFs."""
    jobs = max(1, workload.dispatch_jobs)
    sheets = max(0, workload.sheets) / jobs
    copies = max(1, workload.copies // jobs)
    candidates = [sample for sample in samples if sample.duplex == workload.duplex]
    kind = _work_class(sheets, copies)
    similar = [sample for sample in candidates if _work_class(sample.sheets, sample.copies) == kind]
    size = max(1.0, sheets / copies)
    near = [sample for sample in similar if 0.5 <= (sample.sheets / sample.copies) / size <= 2.0]
    similar = (near or similar)[-9:]
    # Long jobs provide engine-rate evidence. A 300-sheet completion must not
    # have the same influence as a four-sheet job dominated by handling time.
    engine_candidates = similar if kind != "short" and similar else candidates
    engine = [sample for sample in engine_candidates
              if sample.sheets >= 20 and sample.seconds > DEFAULT_FINISHING_SECONDS_PER_JOB][-8:]
    learned_rate = None
    if engine:
        rate = sum(sample.seconds - DEFAULT_FINISHING_SECONDS_PER_JOB for sample in engine) / sum(sample.sheets for sample in engine)
        if 0.05 <= rate <= 5.0:
            learned_rate = rate
    rate = learned_rate if learned_rate is not None else 1.725
    handling = DEFAULT_FINISHING_SECONDS_PER_JOB
    if kind == "short" and similar:
        handling = median(max(0.0, sample.seconds - rate * sample.sheets) for sample in similar)
    expected = max(0.0, sheets * rate + handling)
    learned = learned_rate is not None or (kind == "short" and bool(similar))
    if learned:
        deviations = [abs(sample.seconds - (sample.sheets * rate + handling)) / sample.seconds for sample in similar]
        margin = min(0.5, max(0.15 if len(similar) >= 3 else 0.25, 2 * median(deviations) if deviations else 0.0))
        low, high = expected * (1 - margin), expected * (1 + margin)
    else:
        low, high = sheets * 0.45 + handling, sheets * 3.0 + handling
    return EtaEstimate(round(low * jobs), round(high * jobs),
                       "유사 작업 학습" if learned else "초기 추정", round(expected * jobs))


def estimate_workloads_seconds(
    workloads: tuple[EtaWorkload, ...],
    samples: tuple[EtaTimingSample, ...] = (),
    *,
    elapsed_seconds: tuple[float, ...] = (),
) -> EtaEstimate:
    # Hundreds of order blocks often share a small set of sheet/copy counts.
    # Fit each distinct workload once per observation, not once per PDF.
    cache: dict[EtaWorkload, EtaEstimate] = {}
    estimates = []
    for work in workloads:
        if work not in cache:
            cache[work] = estimate_workload_seconds(work, samples)
        estimates.append(cache[work])
    totals = [0, 0, 0]
    for index, estimate in enumerate(estimates):
        elapsed = max(0.0, elapsed_seconds[index]) if index < len(elapsed_seconds) else 0.0
        for target, seconds in enumerate((estimate.seconds_low, estimate.seconds_high, estimate.seconds_expected)):
            totals[target] += max(0, round(seconds - elapsed))
    confidence = "유사 작업 학습" if estimates and all(item.confidence != "초기 추정" for item in estimates) else "초기 추정"
    return EtaEstimate(*totals[:2], confidence, totals[2])


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
