"""Read local print history and report ETA errors without order or device identifiers."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict, deque
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from unit_print_eta import EtaTimingSample, EtaWorkload, estimate_workload_seconds  # noqa: E402

CHANGED_RUN = {
    "device_dispatch_held", "append_requested", "jobs_added", "reserved_jobs_removed", "cancel_requested",
}


def summarize(values: list[float]) -> dict:
    if not values:
        return {"runs": 0}
    absolute = sorted(map(abs, values))
    return {
        "runs": len(values),
        "signed_median_seconds": round(statistics.median(values), 2),
        "absolute_median_seconds": round(statistics.median(absolute), 2),
        "absolute_p90_seconds": round(absolute[math.ceil(len(absolute) * .9) - 1], 2),
        "worst_absolute_seconds": round(absolute[-1], 2),
    }


def parse_timestamp(value: object) -> datetime | None:
    if isinstance(value, str):
        try:
            stamp = datetime.fromisoformat(value)
            if stamp.utcoffset() is not None:
                return stamp
        except ValueError:
            pass
    return None


def number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def report(history: Path, since: date | None) -> dict:
    # Use all events for runs crossing the selected day's boundary.
    run_events = defaultdict(set)
    last_submitted = {}
    submission_gaps = defaultdict(float)
    last_device_completion = {}
    run_settings = {}
    first_submitted = {}
    jobs_by_run = defaultdict(dict)
    timings_by_run = defaultdict(dict)
    finishes = {}
    invalid_lines = 0
    for path in sorted(history.glob("????-??-??.jsonl")):
        with path.open(encoding="utf-8-sig") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except ValueError:
                    invalid_lines += 1
                    continue
                if not isinstance(event, dict) or not isinstance(event.get("run_id"), str) or not event["run_id"]:
                    continue
                key = (str(event.get("session_id")), event["run_id"])
                kind = event.get("event")
                if not isinstance(kind, str):
                    continue
                stamp = parse_timestamp(event.get("timestamp"))
                if kind in CHANGED_RUN:
                    run_events[key].add(kind)
                elif kind == "run_started" and type(event.get("duplex")) is bool:
                    run_settings[key] = (str(event.get("printer", "")), event["duplex"])
                elif kind == "job_submitted" and stamp is not None:
                    first_submitted.setdefault(key, stamp)
                    previous = last_submitted.get(key)
                    if previous is not None:
                        submission_gaps[key] = max(submission_gaps[key], (stamp - previous).total_seconds())
                    last_submitted[key] = stamp
                    sheets, copies = event.get("estimated_sheets"), event.get("copies")
                    if (number(sheets) and sheets > 0 and type(copies) is int and copies > 0
                            and isinstance(event.get("job_id"), str)):
                        jobs_by_run[key][event["job_id"]] = (sheets, copies, str(event.get("kind", "")))
                elif kind == "job_physically_completed":
                    ended = parse_timestamp(event.get("device_ended_at"))
                    if ended is not None:
                        last_device_completion[key] = max(ended, last_device_completion.get(key, ended))
                    observed = event.get("eta_service_seconds")
                    if number(observed) and observed > 0 and isinstance(event.get("job_id"), str):
                        timings_by_run[key][event["job_id"]] = observed
                elif (kind == "run_finished" and event.get("physical_output_verified") is True
                      and number(event.get("eta_initial_error_seconds"))
                      and (since is None or (stamp is not None and stamp.date() >= since))):
                    finishes[key] = {
                        name: event.get(name) for name in (
                            "timestamp", "completed_jobs", "eta_initial_job_count",
                            "eta_initial_error_seconds", "eta_checkpoint_errors",
                        )
                    }
    short, long, changed = [], [], []
    milestones = defaultdict(list)
    ranges = defaultdict(list)
    gaps, lag = [], []
    for key, finish in finishes.items():
        error = finish["eta_initial_error_seconds"]
        jobs = finish["completed_jobs"]
        if run_events[key] or not number(jobs) or jobs != finish["eta_initial_job_count"]:
            changed.append(error)
            continue
        (long if jobs >= 50 else short).append(error)
        gaps.append(submission_gaps[key])
        finish_time = parse_timestamp(finish["timestamp"])
        device_time = last_device_completion.get(key)
        if finish_time is not None and device_time is not None and finish_time >= device_time:
            lag.append((finish_time - device_time).total_seconds())
        checkpoints = finish["eta_checkpoint_errors"]
        if not isinstance(checkpoints, dict):
            continue
        for percent, errors in checkpoints.items():
            if percent in {"25", "50", "75"} and isinstance(errors, list) and len(errors) == 3 and all(map(number, errors)):
                milestones[percent].append(errors[1])
                ranges[percent].append(errors[0] >= 0 >= errors[2])
    return {
        "invalid_lines": invalid_lines,
        "unchanged_1_to_49_jobs": summarize(short),
        "unchanged_50_or_more_jobs": summarize(long),
        "changed_or_held": summarize(changed),
        "checkpoint_errors": {percent: {**summarize(milestones[percent]),
                                        "within_estimated_range": sum(ranges[percent])}
                              for percent in ("25", "50", "75")},
        "largest_submission_gap_seconds": max(gaps, default=None),
        "device_completion_to_confirmation_median_seconds": round(statistics.median(lag), 2) if lag else None,
        "model_comparison_from_first_submission": compare_models(
            finishes, run_events, run_settings, first_submitted, jobs_by_run, timings_by_run,
        ),
    }


def compare_models(finishes, run_events, run_settings, first_submitted, jobs_by_run, timings_by_run) -> dict:
    """Fit each run using only samples from runs finished before its first submission."""
    runs = sorted(
        (first_submitted[key], key, finish_time) for key, finish in finishes.items()
        if key in first_submitted and key in run_settings
        and (finish_time := parse_timestamp(finish["timestamp"])) is not None
        and not run_events[key] and len(jobs_by_run[key]) == finish["completed_jobs"]
        and finish["completed_jobs"] == finish["eta_initial_job_count"]
    )
    training = iter(sorted(runs, key=lambda item: item[2]))
    next_training = next(training, None)
    device_samples = defaultdict(lambda: deque(maxlen=100))
    separator_samples = defaultdict(lambda: deque(maxlen=50))
    errors = defaultdict(list)
    for start, key, end in runs:
        while next_training is not None and next_training[2] < start:
            _, earlier, _ = next_training
            printer, duplex = run_settings[earlier]
            for job_id, seconds in timings_by_run[earlier].items():
                job = jobs_by_run[earlier].get(job_id)
                if job is not None:
                    sheets, copies, kind = job
                    device_samples[printer].append(EtaTimingSample(sheets, copies, seconds, duplex))
                    if kind in {"name_separator", "omr", "separator"}:
                        separator_samples[(printer, duplex, kind, sheets, copies)].append(seconds)
            next_training = next(training, None)
        printer, duplex = run_settings[key]
        samples = tuple(device_samples[printer])
        if not samples:
            continue
        baseline = candidate = 0.0
        for sheets, copies, kind in jobs_by_run[key].values():
            estimate = estimate_workload_seconds(EtaWorkload(sheets, copies, duplex=duplex), samples)
            baseline += estimate.seconds_expected
            comparable = separator_samples[(printer, duplex, kind, sheets, copies)]
            candidate += statistics.median(comparable) if len(comparable) >= 5 else estimate.seconds_expected
        duration = (end - start).total_seconds()
        size = "long" if len(jobs_by_run[key]) >= 50 else "short"
        errors[(size, "current")].append(duration - baseline)
        errors[(size, "kind_specific")].append(duration - candidate)
    return {size: {model: summarize(errors[(size, model)]) for model in ("current", "kind_specific")}
            for size in ("short", "long")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, default=ROOT / "output" / "unit_print_manager" / "history")
    parser.add_argument("--since", type=date.fromisoformat)
    args = parser.parse_args()
    print(json.dumps(report(args.history, args.since), indent=2))


if __name__ == "__main__":
    main()
