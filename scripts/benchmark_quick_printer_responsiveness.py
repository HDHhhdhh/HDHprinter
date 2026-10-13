"""Measure hidden real Tk callbacks with synthetic queues and isolated state.

This is a timing tool, not visual QA or a live printer test. Registered catalogs
are read-only; every operator-state output uses a temporary directory. The real
Canvas and font measurements run against a fixed logical viewport while its
root stays withdrawn. No native input, device, credential or print operation is
allowed. Run with the same interpreter/options before and after a change.
"""

from __future__ import annotations

import argparse
import cProfile
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import pstats
import shutil
import statistics
import sys
import tempfile
import time
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MANAGER_SHA256 = hashlib.sha256((ROOT / "unit_print_manager.py").read_bytes()).hexdigest()

import unit_print_history_events
import unit_print_manager as manager
import unit_print_materials
import unit_print_pdf_metadata
from pypdf import PdfWriter


def timing_summary(samples: list[float]) -> dict[str, object]:
    return {"median_ms": round(statistics.median(samples), 3),
            "max_ms": round(max(samples), 3), "samples_ms": [round(x, 3) for x in samples]}


def isolated_app(stack: ExitStack, temporary: Path, phase_timings: dict[str, list[float]], args):
    cache = temporary / "material_catalog.json"
    if not manager.MATERIAL_CATALOG_PATH.is_file():
        raise RuntimeError("Registered material metadata cache is required")
    shutil.copy2(manager.MATERIAL_CATALOG_PATH, cache)
    config = {"root_dir": str(manager.BUNDLED_DATA_ROOT / "단원평가"),
              "printer": "TEST benchmark printer", "engine": "SumatraPDF",
              "queue_batch_size": 12, "queue_refill_threshold": 6,
              "device_monitor": {"enabled": False}}
    for owner, name, value in (
        (manager, "CONFIG_PATH", temporary / "config.json"),
        (manager, "MATERIAL_CATALOG_PATH", cache),
        (manager, "RECOVERY_DIRECTORY", temporary / "recovery"),
        (unit_print_history_events, "HISTORY_DIRECTORY", temporary / "history"),
        (unit_print_pdf_metadata, "_default_cache",
         unit_print_pdf_metadata.PdfPageCountCache(temporary / "page_counts.json")),
    ):
        stack.enter_context(patch.object(owner, name, value))
    stack.enter_context(patch.object(manager, "load_config", return_value=config))
    stack.enter_context(patch.object(manager, "save_config", side_effect=AssertionError("Unexpected config write")))
    stack.enter_context(patch.object(manager, "find_sumatra_pdf", return_value="TEST Sumatra.exe"))
    stack.enter_context(patch.object(unit_print_materials, "analyze_apex_document",
                                    side_effect=AssertionError("Registered APEX cache miss")))
    forbidden = []
    for name in ("load_printers", "run_powershell", "sumatra_enqueue_pdfs",
                 "save_device_credential", "load_device_credential", "delete_device_credential"):
        forbidden.append(stack.enter_context(patch.object(
            manager, name, side_effect=AssertionError(f"Forbidden external operation: {name}"))))
    for name in ("activate_windows_korean_input", "set_windows_ime_composition_font",
                 "complete_windows_ime_composition", "take_windows_ime_composition"):
        stack.enter_context(patch.object(manager, name,
                                        return_value="" if name == "take_windows_ime_composition" else False))
    for name in ("showerror", "showwarning", "showinfo", "askyesno"):
        stack.enter_context(patch.object(manager.messagebox, name,
                                        side_effect=AssertionError("Unexpected benchmark dialog")))
    for name in ("maximize_on_start", "initialize_taskbar_indicator", "initialize_windows_notifier",
                 "warm_printer_inventory_cache", "initialize_device_monitor_from_config",
                 "poll_device_status", "apply_app_icon", "notify_operator"):
        stack.enter_context(patch.object(manager.PrintManagerApp, name, return_value=None))
    stack.enter_context(patch.object(manager.ttkbootstrap.Style, "instance", None))
    if args.prototype_bulk_preview_insert:
        def bulk_preview_insert(app):
            app.preview_render_after_id = None
            if app.is_closing or app.preview_render_chunks is None:
                return
            text = "".join(app.preview_render_chunks)
            app.preview_render_chunks = None
            started = time.perf_counter()
            app.preview_text.insert(manager.END, text)
            app._benchmark_bulk_insert_ms = (time.perf_counter() - started) * 1000
            app.schedule_preview_preflight(set(app.preview_render_unresolved))
        stack.enter_context(patch.object(manager.PrintManagerApp, "insert_preview_chunk", bulk_preview_insert))
    # Wrappers retain production behavior and measure inclusive wall time.
    for owner, name in ([(manager, name) for name in (
        "load_unit_catalog", "scan_default_test_catalogs", "load_apex_catalog",
        "scan_able_catalog", "scan_training_pack_catalog", "scan_frequent_type_catalog",
        "scan_gyopyeong_catalog", "scan_selected_exam_catalog", "load_textbook_catalog")]
    + [(manager.PrintManagerApp, "configure_style"), (manager.PrintManagerApp, "build_ui_reset")]):
        original = getattr(owner, name)
        def measured(*args, _original=original, _name=name, **kwargs):
            started = time.perf_counter()
            try:
                return _original(*args, **kwargs)
            finally:
                phase_timings.setdefault(_name, []).append((time.perf_counter() - started) * 1000)
        stack.enter_context(patch.object(owner, name, measured))
    root = manager.TkinterDnD.Tk() if manager.TkinterDnD is not None else tk.Tk()
    root.withdraw()
    stack.callback(root.destroy)
    errors = []
    root.report_callback_exception = lambda error_type, error, tb: errors.append(type(error).__name__)
    started = time.perf_counter()
    app = manager.PrintManagerApp(root)
    elapsed = (time.perf_counter() - started) * 1000
    def close():
        app.is_closing = True
        app.close_pdf_work_services()
        app.queue_recovery_writer.close()
        # Tcl keeps pending after scripts independently of a root's commands.
        # Cancel them before destroying this interpreter to avoid stale callbacks
        # being reported while the next constructor's event loop runs.
        for after_id in root.tk.splitlist(root.tk.call("after", "info")):
            root.after_cancel(after_id)
    stack.callback(close)
    # These are geometry only. Tcl Canvas drawing and tkfont.Font.measure stay real.
    stack.enter_context(patch.object(app.queue_canvas, "winfo_width", return_value=520))
    stack.enter_context(patch.object(app.queue_canvas, "winfo_height", return_value=560))
    root.update_idletasks()
    root.update()
    if errors or root.state() != "withdrawn":
        raise RuntimeError("Hidden constructor callback/state failure")
    return app, root, elapsed, errors, forbidden


def timed_callback(root, callback, app, observe_background=False):
    """Observe the request, its ready event, and any deferred preview chunks."""
    marker = []
    gaps = []
    monitor_after = [None]
    timed_out = []
    preview_idle = []
    preview_gap = []
    started = time.perf_counter()
    last_tick = [started]
    def heartbeat():
        now = time.perf_counter()
        gaps.append((now - last_tick[0]) * 1000)
        last_tick[0] = now
        if not marker:
            marker.append((now - started) * 1000)
        if not preview_pending() and not preview_idle:
            preview_idle.append((now - started) * 1000)
            preview_gap.append(max(gaps))
        if now - started >= 10:
            timed_out.append(True)
            root.quit()
            return
        if not observed_pending():
            root.quit()
            return
        monitor_after[0] = root.after(1, heartbeat)
    root.after(0, heartbeat)
    callback_started = time.perf_counter()
    callback()
    elapsed = (time.perf_counter() - callback_started) * 1000
    def preview_pending():
        return any(getattr(app, name, None) for name in (
            "preview_render_after_id", "_preview_render_after_id",
            "preview_update_after_id", "_preview_update_after_id",
            "queue_redraw_after_id",
        ))
    def observed_pending():
        if preview_pending():
            return True
        if not observe_background:
            return False
        writer = getattr(app, "queue_recovery_writer", None)
        future = getattr(app, "resource_estimate_future", None)
        return bool(
            (writer is not None and writer.busy)
            or (future is not None and not future.done())
            or getattr(app, "preview_preflight_active", False)
            or getattr(app, "preview_preflight_pending_paths", ())
        )
    # Use Tk's own event loop. Python Sleep(1) polling adds a separate scheduling
    # delay on Windows and can overstate deferred rendering completion time.
    root.mainloop()
    if timed_out:
        raise RuntimeError("Deferred preview did not finish within 10 seconds")
    completion = (time.perf_counter() - started) * 1000
    if monitor_after[0] is not None:
        root.after_cancel(monitor_after[0])
    if not marker:
        raise RuntimeError("Tk heartbeat did not execute")
    return (elapsed, marker[0], preview_idle[0], preview_gap[0], completion, max(gaps))


def benchmark(args):
    phases: dict[str, list[float]] = {}
    constructors = []
    results = []
    profiles = []
    with tempfile.TemporaryDirectory(prefix="quick_printer_benchmark_") as directory:
        temporary = Path(directory)
        pdfs = []
        for file_no in range(args.files):
            pdf = temporary / f"synthetic_{file_no + 1}.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=595, height=842)
            writer.write(str(pdf))
            pdfs.append(pdf)
        for constructor_no in range(args.constructors):
            with ExitStack() as stack:
                instance_dir = temporary / str(constructor_no)
                instance_dir.mkdir()
                app, root, elapsed, errors, forbidden = isolated_app(stack, instance_dir, phases, args)
                constructors.append(elapsed)
                if constructor_no != args.constructors - 1:
                    continue
                for size in args.sizes:
                    blocks = [manager.PrintBlock(
                        subject="Synthetic", level="Test", round_name="1", start=1, end=1,
                        copies=args.copies, bundled=args.bundled,
                        source="custom", display_label=f"Benchmark {i:04d}",
                        name_separator_label=f"TEST Student {i % 25:02d}",
                        files=[manager.ExamPdf(file_no + 1, f"synthetic {file_no + 1}", pdf)
                               for file_no, pdf in enumerate(pdfs)],
                    ) for i in range(size)]
                    app.blocks = list(blocks)
                    app.block_status_by_id = {}
                    app.block_detail_by_id = {}
                    app.selected_block_index_value = 0
                    app.selected_block_indices = {0}
                    app.update_queue_view()
                    app.update_preview()
                    root.update()
                    operations = {
                        "selection_single": lambda: app.select_queue_indices(
                            {size // 2}, focus_index=size // 2, load_form=True),
                        "selection_all": app.select_all_queue,
                        "scroll_redraw": lambda: (app.queue_canvas.yview_moveto(.5), app.draw_queue()),
                        "copy_25": app.copy_selected_blocks,
                        "paste_25": app.paste_copied_blocks,
                        "preview_request": app.update_preview,
                    }
                    catalog_block = None
                    if args.include_extra_interactions:
                        if not app.exam_files:
                            raise RuntimeError("Extra catalog form workload requires a registered exam")
                        item = app.exam_files[0]
                        catalog_block = manager.PrintBlock(
                            subject=app.subject_var.get(), level=app.level_var.get(),
                            round_name=app.round_var.get(), start=item.number, end=item.number,
                            copies=1, source="catalog", files=[item],
                            name_separator_label="TEST Student catalog",
                        )
                        operations["catalog_form"] = lambda: app.select_queue_indices(
                            {0}, focus_index=0, load_form=True)
                        def motion_burst():
                            for event_no in range(100):
                                app.on_queue_drag_motion(SimpleNamespace(y=80 + event_no % 20 * 18))
                        operations["drag_motion_burst"] = motion_burst
                    for name, callback in operations.items():
                        if args.only_operations and name not in args.only_operations:
                            continue
                        samples, heartbeat, completions, heartbeat_gaps, bulk_inserts = [], [], [], [], []
                        background_completions, background_gaps = [], []
                        for repeat in range(args.repeats + 1):
                            if name == "catalog_form":
                                app.blocks = list(blocks)
                                app.blocks[0] = catalog_block
                            if name == "drag_motion_burst":
                                app.blocks = list(blocks)
                                app.cancel_lift_animation()
                                app.cancel_drop_animation()
                                app.queue_canvas.yview_moveto(0)
                                app.on_queue_drag_start(SimpleNamespace(y=25, state=0))
                            if name in {"copy_25", "paste_25"}:
                                app.blocks = list(blocks)
                                app.selected_block_indices = set(range(min(25, size)))
                                app.selected_block_index_value = 0
                                app.undo_stack.clear()
                                app.redo_stack.clear()
                                if name == "paste_25":
                                    app.copy_selected_blocks()
                            # Alternate position so scroll work isn't only a stable yview.
                            if name == "scroll_redraw":
                                app.queue_canvas.yview_moveto(0 if repeat % 2 == 0 else .95)
                            if args.settle_between_repeats:
                                timed_callback(root, lambda: None, app, observe_background=True)
                            value, delay, completion, gap, background_completion, background_gap = timed_callback(
                                root, callback, app, observe_background=args.observe_background)
                            if name == "drag_motion_burst":
                                app.cancel_lift_animation()
                                app.drag_active = False
                                app.drag_start_index = None
                                app.drag_selected_indices = ()
                                app.drag_drop_index = None
                                app.drag_pointer_y = None
                            if repeat:
                                samples.append(value)
                                heartbeat.append(delay)
                                completions.append(completion)
                                heartbeat_gaps.append(gap)
                                background_completions.append(background_completion)
                                background_gaps.append(background_gap)
                                if args.prototype_bulk_preview_insert and name == "preview_request":
                                    bulk_inserts.append(app._benchmark_bulk_insert_ms)
                        row = {"queue_blocks": size, "operation": name,
                                        "callback": timing_summary(samples),
                                        "ready_heartbeat_delay": timing_summary(heartbeat),
                                        "request_to_preview_idle": timing_summary(completions),
                                        "max_heartbeat_gap_until_preview_idle": timing_summary(heartbeat_gaps)}
                        if bulk_inserts:
                            row["prototype_bulk_text_insert"] = timing_summary(bulk_inserts)
                        if args.observe_background:
                            row["request_to_background_idle"] = timing_summary(background_completions)
                            row["max_heartbeat_gap_until_background_idle"] = timing_summary(background_gaps)
                        results.append(row)
                        if ((args.profile_paste and name == "paste_25")
                                or (args.profile_preview and name == "preview_request")
                                or (args.profile_selection and name == "selection_single")):
                            if name == "paste_25":
                                app.blocks = list(blocks)
                                app.selected_block_indices = set(range(min(25, size)))
                                app.selected_block_index_value = 0
                                app.undo_stack.clear()
                                app.redo_stack.clear()
                                app.copy_selected_blocks()
                            profile = cProfile.Profile()
                            if args.settle_between_repeats:
                                timed_callback(root, lambda: None, app, observe_background=True)
                            if name == "preview_request":
                                profile.runcall(timed_callback, root, callback, app)
                            else:
                                profile.runcall(callback)
                            root.update()
                            stats = pstats.Stats(profile).stats
                            rows = []
                            for (filename, line, function), values in sorted(
                                stats.items(), key=lambda item: item[1][3], reverse=True
                            )[:50]:
                                primitive, calls, own, cumulative, _callers = values
                                rows.append({"module": Path(filename).name, "line": line,
                                             "function": function, "primitive_calls": primitive,
                                             "calls": calls, "own_ms": round(own * 1000, 3),
                                             "cumulative_ms": round(cumulative * 1000, 3)})
                            profiles.append({"queue_blocks": size, "operation": name,
                                             "top_cumulative_functions": rows})
                    app.blocks = []
                    app.block_status_by_id = {}
                    app.block_detail_by_id = {}
                if errors:
                    raise RuntimeError("Tk callback failed: " + ", ".join(errors))
                if any(mock.called for mock in forbidden):
                    raise RuntimeError("An external operation was attempted")
    return {"schema_version": 1, "python": sys.version.split()[0],
            "label": args.label, "manager_sha256": MANAGER_SHA256,
            "method": {"root": "withdrawn", "logical_viewport": [520, 560],
                       "real_canvas_and_font_measure": True, "real_registered_catalog_reads": True,
                       "synthetic_pdf_pages": 1, "copies_per_block": args.copies,
                       "files_per_block": args.files, "bundled": args.bundled,
                       "temporary_operator_state": True, "warmup_callbacks": 1,
                       "repeats": args.repeats, "constructor_repeats": args.constructors,
                       "paste_selection": "First min(25, queue_blocks) blocks; restored before every paste repeat",
                       "preview_queue": ("Runs after paste with queue_blocks + min(25, queue_blocks) blocks"
                                         if not args.only_operations or "paste_25" in args.only_operations
                                         else "Uses exactly queue_blocks blocks"),
                       "heartbeat": "Ready after(0) event scheduled immediately before callback; includes callback delay",
                       "deferred_preview": "Real withdrawn Tk mainloop; follow preview render/update and redraw ids; 1ms heartbeat until idle",
                       "prototype_bulk_preview_insert": args.prototype_bulk_preview_insert,
                       "settle_background_before_each_repeat": args.settle_between_repeats,
                       "observe_background_until_idle": args.observe_background,
                       "scope": "CPU/Tk callback latency; no visual, print, network or device observation"},
            "constructor": timing_summary(constructors),
            "constructor_phases_inclusive": {name: timing_summary(values) for name, values in phases.items()},
            "operations": results, "profiles_separate_from_timings": profiles}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="baseline")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--constructors", type=int, default=3)
    parser.add_argument("--sizes", type=int, nargs="+", default=[1, 100, 500, 1000])
    parser.add_argument("--copies", type=int, default=1)
    parser.add_argument("--files", type=int, default=1)
    parser.add_argument("--bundled", action="store_true")
    parser.add_argument("--only-operations", nargs="+", choices=[
        "selection_single", "selection_all", "scroll_redraw", "copy_25", "paste_25", "preview_request",
        "catalog_form", "drag_motion_burst"])
    parser.add_argument("--profile-paste", action="store_true")
    parser.add_argument("--profile-preview", action="store_true")
    parser.add_argument("--profile-selection", action="store_true")
    parser.add_argument("--include-extra-interactions", action="store_true")
    parser.add_argument("--prototype-bulk-preview-insert", action="store_true")
    parser.add_argument("--settle-between-repeats", action="store_true")
    parser.add_argument("--observe-background", action="store_true")
    args = parser.parse_args()
    if min(args.repeats, args.constructors, args.copies, args.files, *args.sizes) < 1:
        parser.error("All counts must be positive")
    results = benchmark(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"label": args.label, "constructor": results["constructor"],
                      "operations": [{"queue_blocks": row["queue_blocks"], "operation": row["operation"],
                                      "median_ms": row["callback"]["median_ms"],
                                      "max_ms": row["callback"]["max_ms"]}
                                     for row in results["operations"]]}, ensure_ascii=True))


if __name__ == "__main__":
    main()
