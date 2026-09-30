"""Measure 4-up PDF preparation without sending anything to a printer."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import tempfile
import time
import tkinter
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from unit_print_pdf_printing import materialize_four_up_pdf  # noqa: E402
from unit_print_materials import materialize_pdf_pages  # noqa: E402
from unit_print_preparation import (  # noqa: E402
    PRINT_PREPARATION_PRIORITY,
    PrintPreparationService,
    PriorityWorkScheduler,
)


def measure(source: Path, cache_dir: Path, runs: int) -> dict[str, object]:
    job = SimpleNamespace(item=SimpleNamespace(path=source), pages_per_sheet=4)
    durations: list[float] = []
    outputs: list[str] = []
    for _index in range(runs):
        service = PrintPreparationService(cache_dir)
        started = time.perf_counter()
        output = service.resolve_job(job)
        durations.append(time.perf_counter() - started)
        outputs.append(str(output))
        service.close()
    return {
        "source": str(source.resolve()),
        "runs": runs,
        "seconds": durations,
        "median_seconds": statistics.median(durations),
        "minimum_seconds": min(durations),
        "maximum_seconds": max(durations),
        "unique_outputs": len(set(outputs)),
    }


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return ordered[index]


def parse_pages(value: str) -> tuple[int, ...]:
    numbers: list[int] = []
    for token in value.split(","):
        part = token.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start, end = int(start_text), int(end_text)
            if start < 1 or end < start:
                raise argparse.ArgumentTypeError(f"잘못된 페이지 범위입니다: {part}")
            numbers.extend(range(start, end + 1))
        else:
            number = int(part)
            if number < 1:
                raise argparse.ArgumentTypeError(f"페이지는 1 이상이어야 합니다: {part}")
            numbers.append(number)
    if not numbers:
        raise argparse.ArgumentTypeError("선택할 페이지가 없습니다.")
    return tuple(numbers)


def describe_pages(pages: tuple[int, ...] | None) -> str | None:
    if pages is None:
        return None
    if pages == tuple(range(pages[0], pages[-1] + 1)):
        return f"{pages[0]}-{pages[-1]}"
    return ",".join(str(page) for page in pages)


def repeat_path_work(count: int, work: Callable[[int], Path]) -> Path:
    result: Path | None = None
    for index in range(count):
        result = Path(work(index))
    assert result is not None
    return result


def measure_ui_heartbeat(
    source: Path,
    operation: str,
    work: Callable[[], Path],
    *,
    operation_runs: int = 1,
    interval_seconds: float = 0.020,
) -> tuple[dict[str, object], Path]:
    """Run the PDF worker while Tcl or a fallback loop keeps a 20 ms beat."""

    scheduler = PriorityWorkScheduler(thread_name="quick-print-benchmark")
    heartbeat_intervals: list[float] = []
    previous_tick = time.perf_counter()
    try:
        interpreter: tkinter.Tcl | None = tkinter.Tcl()
    except tkinter.TclError:
        # A restricted runner can block Tcl's library initialization even
        # though the installed desktop runtime works.  Keep the benchmark
        # useful and label the less representative fallback explicitly.
        interpreter = None
    heartbeat_driver = "tcl_event_loop" if interpreter is not None else "sleep_loop"
    heartbeat_after_id: str | None = None
    heartbeat_variable = "__quick_print_benchmark_heartbeat"
    if interpreter is not None:
        interpreter.setvar(heartbeat_variable, 0)
        heartbeat_after_id = str(
            interpreter.call(
                "after",
                round(interval_seconds * 1000),
                f"set {heartbeat_variable} 1",
            )
        )
    submit_started = time.perf_counter()
    future = scheduler.submit(
        work,
        priority=PRINT_PREPARATION_PRIORITY,
    )
    submit_seconds = time.perf_counter() - submit_started
    work_started = time.perf_counter()
    try:
        while not future.done():
            if interpreter is not None:
                interpreter.eval("update")
                if int(interpreter.getvar(heartbeat_variable)):
                    now = time.perf_counter()
                    heartbeat_intervals.append(now - previous_tick)
                    previous_tick = now
                    interpreter.setvar(heartbeat_variable, 0)
                    heartbeat_after_id = str(
                        interpreter.call(
                            "after",
                            round(interval_seconds * 1000),
                            f"set {heartbeat_variable} 1",
                        )
                    )
                time.sleep(0.001)
            else:
                deadline = previous_tick + interval_seconds
                time.sleep(max(0.0, deadline - time.perf_counter()))
                now = time.perf_counter()
                heartbeat_intervals.append(now - previous_tick)
                previous_tick = now
        output = Path(future.result())
    finally:
        scheduler.close(wait=True)
        if interpreter is not None:
            if heartbeat_after_id:
                interpreter.call("after", "cancel", heartbeat_after_id)
            interpreter.eval("update")
    total_seconds = time.perf_counter() - work_started
    delays_ms = [
        max(0.0, observed - interval_seconds) * 1000
        for observed in heartbeat_intervals
    ]
    return {
        "operation": operation,
        "operation_runs": operation_runs,
        "source": str(source.resolve()),
        "submit_return_ms": submit_seconds * 1000,
        "background_work_seconds": total_seconds,
        "heartbeat_interval_ms": interval_seconds * 1000,
        "heartbeat_driver": heartbeat_driver,
        "heartbeat_count": len(heartbeat_intervals),
        "heartbeat_p95_delay_ms": percentile(delays_ms, 0.95),
        "heartbeat_max_delay_ms": max(delays_ms, default=0.0),
        "output_exists": output.is_file(),
    }, output


def main() -> int:
    parser = argparse.ArgumentParser(
        description="실물 전송 없이 빠른 인쇄기 PDF 준비 시간을 측정합니다."
    )
    parser.add_argument("sources", nargs="+", type=Path)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--heartbeat-runs", type=int, default=5)
    parser.add_argument(
        "--pages",
        type=parse_pages,
        help="먼저 선택할 원본 페이지. 예: 1-96 또는 1-32,65-96",
    )
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs는 1 이상이어야 합니다.")
    if args.heartbeat_runs < 1:
        parser.error("--heartbeat-runs는 1 이상이어야 합니다.")
    missing = [path for path in args.sources if not path.is_file()]
    if missing:
        parser.error("PDF를 찾을 수 없습니다: " + ", ".join(map(str, missing)))

    if args.cache_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="quick-print-benchmark-")
        cache_dir = Path(temporary.name)
    else:
        temporary = None
        cache_dir = args.cache_dir
    try:
        measured_sources: list[Path] = []
        heartbeat_reports: list[dict[str, object]] = []
        for index, source in enumerate(args.sources):
            measured_source = source
            if args.pages is not None:
                selection_cache = cache_dir / "selection_heartbeat" / str(index)
                selection_report, measured_source = measure_ui_heartbeat(
                    source,
                    "select_pages",
                    lambda source=source, selection_cache=selection_cache: repeat_path_work(
                        args.heartbeat_runs,
                        lambda repeat: materialize_pdf_pages(
                            source,
                            args.pages,
                            selection_cache / str(repeat),
                            "benchmark_selection",
                        ),
                    ),
                    operation_runs=args.heartbeat_runs,
                )
                heartbeat_reports.append(selection_report)
            measured_sources.append(measured_source)

        measurements = [
            measure(path, cache_dir / "four_up" / str(index), args.runs)
            for index, path in enumerate(measured_sources)
        ]
        for index, source in enumerate(measured_sources):
            four_up_cache = cache_dir / "heartbeat" / str(index)
            four_up_report, _output = measure_ui_heartbeat(
                source,
                "four_up",
                lambda source=source, four_up_cache=four_up_cache: repeat_path_work(
                    args.heartbeat_runs,
                    lambda repeat: materialize_four_up_pdf(
                        source,
                        four_up_cache / str(repeat),
                    ),
                ),
                operation_runs=args.heartbeat_runs,
            )
            heartbeat_reports.append(four_up_report)
        report = {
            "printer_calls": 0,
            "cache_dir": str(cache_dir.resolve()),
            "selected_pages": describe_pages(args.pages),
            "selected_page_count": len(args.pages) if args.pages is not None else None,
            "heartbeat_runs": args.heartbeat_runs,
            "measurements": measurements,
            "ui_heartbeat": heartbeat_reports,
        }
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
    finally:
        if temporary is not None:
            temporary.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
