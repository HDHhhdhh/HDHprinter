from __future__ import annotations

import argparse
import json
import platform
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import ImageGrab

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from unit_print_manager import PrintManagerApp, Tk, TkinterDnD, set_windows_app_user_model_id, set_windows_dpi_awareness


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture no-print visual QA screenshots for Quick Printer.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--windowed", action="store_true", help="Capture at 1440x900 instead of the normal maximized state.")
    parser.add_argument(
        "--geometry",
        help="Capture at an explicit Tk geometry such as 2000x1200+40+40.",
    )
    return parser.parse_args()


def widget_box(widget: object) -> dict[str, int | bool]:
    root = widget.winfo_toplevel()
    x = widget.winfo_rootx() - root.winfo_rootx()
    y = widget.winfo_rooty() - root.winfo_rooty()
    width = widget.winfo_width()
    height = widget.winfo_height()
    return {
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "inside_window": x >= 0 and y >= 0 and x + width <= root.winfo_width() and y + height <= root.winfo_height(),
    }


def notebook_tab_boxes(notebook: object) -> list[dict[str, int | str]]:
    boxes: list[dict[str, int | str]] = []
    for index in range(len(notebook.tabs())):
        x, y, width, height = notebook.bbox(index)
        boxes.append(
            {
                "text": str(notebook.tab(index, "text")),
                "x": x,
                "y": y,
                "width": width,
                "height": height,
            }
        )
    return boxes


def notebook_tab_hit_boxes(notebook: object) -> list[dict[str, int | str]]:
    """Measure Windows themed tabs by hit-testing when ttk.bbox returns zeros."""
    notebook.update_idletasks()
    width = notebook.winfo_width()
    height = min(90, notebook.winfo_height())
    best_y = 0
    best_hits = -1
    for y in range(0, height, 2):
        hits = 0
        for x in range(0, width, 4):
            try:
                notebook.index(f"@{x},{y}")
            except Exception:
                continue
            hits += 1
        if hits > best_hits:
            best_y = y
            best_hits = hits

    ranges: dict[int, list[int]] = {}
    for x in range(width):
        try:
            index = int(notebook.index(f"@{x},{best_y}"))
        except Exception:
            continue
        ranges.setdefault(index, [x, x])[1] = x
    boxes: list[dict[str, int | str]] = []
    for index, bounds in sorted(ranges.items()):
        center_x = (bounds[0] + bounds[1]) // 2
        vertical_hits: list[int] = []
        for y in range(height):
            try:
                if int(notebook.index(f"@{center_x},{y}")) == index:
                    vertical_hits.append(y)
            except Exception:
                continue
        boxes.append(
            {
                "text": str(notebook.tab(index, "text")),
                "x": bounds[0],
                "width": bounds[1] - bounds[0] + 1,
                "y": min(vertical_hits) if vertical_hits else best_y,
                "height": (
                    max(vertical_hits) - min(vertical_hits) + 1
                    if vertical_hits
                    else 0
                ),
            }
        )
    return boxes


def main() -> int:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    set_windows_app_user_model_id()
    set_windows_dpi_awareness()
    root = TkinterDnD.Tk() if TkinterDnD is not None else Tk()
    history_directory = tempfile.TemporaryDirectory(prefix="quick_print_gui_history_")
    # Visual capture does not exercise printer, Explorer, or Remote UI
    # integration.  Keeping those background callbacks disabled makes the
    # screenshot deterministic and prevents a daemon warm-up thread from
    # surviving the short-lived Tk interpreter.
    with (
        patch(
            "unit_print_history_events.HISTORY_DIRECTORY",
            Path(history_directory.name),
        ),
        patch.object(PrintManagerApp, "initialize_taskbar_indicator"),
        patch.object(PrintManagerApp, "initialize_windows_notifier"),
        patch.object(PrintManagerApp, "initialize_device_monitor_from_config"),
        patch.object(PrintManagerApp, "poll_device_status"),
        patch.object(PrintManagerApp, "warm_printer_inventory_cache"),
    ):
        app = PrintManagerApp(root)
    if args.geometry:
        root.state("normal")
        root.geometry(args.geometry)
    elif args.windowed:
        root.state("normal")
        root.geometry("1440x900+60+60")
    else:
        root.state("zoomed")
    root.attributes("-topmost", True)
    root.deiconify()
    root.lift()
    root.focus_force()
    root.update_idletasks()
    root.update()
    time.sleep(0.2)

    names = ("tests", "materials", "blank_review", "pdf", "orders")
    captures: dict[str, object] = {}

    saved_app_status = app.app_status_var.get()
    saved_focus_context = app.focus_context_var.get()
    saved_device_status = app.device_status_var.get()
    app.set_resource_estimate_text(
        "예상 A4 842장 · 스테이플 37회 · 약 28~34분 · 용지 충분",
        "success",
    )
    app.app_status_var.set("실제 인쇄 확인 중")
    app.refresh_status_badge()
    app.focus_context_var.set("현재 작업 영역  작업 큐")
    app.set_device_status_text(
        "프린터 인쇄 중 · A4 카세트 1 66~100%, 카세트 2 66~100% · "
        "스테이플 알 수 없음"
    )
    # Let the header's responsive wrap change propagate through the outer grid.
    # A scheduled device poll may replace the smoke text during update(), so set
    # the deterministic values again after the geometry pass has settled.
    root.update_idletasks()
    root.update()
    app.app_status_var.set("실제 인쇄 확인 중")
    app.refresh_status_badge()
    app.focus_context_var.set("현재 작업 영역  작업 큐")
    app.set_device_status_text(
        "프린터 인쇄 중 · A4 카세트 1 66~100%, 카세트 2 66~100% · "
        "스테이플 알 수 없음"
    )
    root.update_idletasks()
    root.update_idletasks()
    x = root.winfo_rootx()
    y = root.winfo_rooty()
    width = root.winfo_width()
    height = root.winfo_height()
    ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True).save(
        args.output / "quick_print_header_status.png"
    )
    captures["header_status"] = {
        "header": widget_box(app.header_frame),
        "status": widget_box(app.status_badge),
        "focus": widget_box(app.focus_context_badge),
        "device": widget_box(app.device_status_badge),
        "resource_estimate": widget_box(app.resource_estimate_badge),
        "grid_size": list(app.header_frame.grid_size()),
    }
    app.app_status_var.set(saved_app_status)
    app.refresh_status_badge()
    app.focus_context_var.set(saved_focus_context)
    app.set_device_status_text(saved_device_status)

    for index, name in enumerate(names):
        app.builder_notebook.select(index)
        root.update_idletasks()
        root.update()
        time.sleep(0.12)
        x = root.winfo_rootx()
        y = root.winfo_rooty()
        width = root.winfo_width()
        height = root.winfo_height()
        ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True).save(
            args.output / f"quick_print_{index}_{name}.png"
        )
        captures[name] = {
            "window": {"width": width, "height": height},
            "builder": widget_box(app.builder_frame),
            "queue": widget_box(app.queue_canvas),
        }

    app.builder_notebook.select(0)
    test_tab_names = ("unit", "review", "school_district", "ilsan")
    test_tab_metrics: dict[str, object] = {}
    for index, name in enumerate(test_tab_names):
        app.test_tabs.select(index)
        current_kind = "단원평가" if index == 0 else app.test_kind_for_tab(index)
        current_canvas = app.test_page_canvases[current_kind]
        root.update_idletasks()
        root.update()
        current_canvas.yview_moveto(0.0)
        root.update_idletasks()
        root.update()
        time.sleep(0.12)
        x = root.winfo_rootx()
        y = root.winfo_rooty()
        width = root.winfo_width()
        height = root.winfo_height()
        ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True).save(
            args.output / f"quick_print_test_{index}_{name}.png"
        )
        if index == 0:
            initial_add_box = widget_box(app.add_unit_button)
            canvas = current_canvas
            app.ensure_test_widget_visible(app.add_unit_button)
            root.update_idletasks()
            root.update()
            time.sleep(0.08)
            ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True).save(
                args.output / "quick_print_test_0_unit_bottom.png"
            )
            test_tab_metrics[name] = {
                "subject": widget_box(app.subject_combo),
                "level": widget_box(app.level_combo),
                "round": widget_box(app.round_combo),
                "start": widget_box(app.start_combo),
                "end": widget_box(app.end_combo),
                "add": initial_add_box,
                "add_after_scroll": widget_box(app.add_unit_button),
                "canvas": widget_box(canvas),
            }
            canvas.yview_moveto(0.0)
        else:
            kind = current_kind
            controls = app.test_builder_controls[kind]
            variables = app.test_builder_vars[kind]
            initial_add_box = widget_box(controls["add"])
            canvas = app.test_page_canvases[kind]
            app.ensure_test_widget_visible(controls["level"])
            root.update_idletasks()
            root.update()
            time.sleep(0.08)
            ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True).save(
                args.output / f"quick_print_test_{index}_{name}_filter.png"
            )
            app.ensure_test_widget_visible(controls["start"])
            root.update_idletasks()
            root.update()
            time.sleep(0.08)
            ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True).save(
                args.output / f"quick_print_test_{index}_{name}_start.png"
            )
            app.ensure_test_widget_visible(controls["add"])
            root.update_idletasks()
            root.update()
            time.sleep(0.08)
            ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True).save(
                args.output / f"quick_print_test_{index}_{name}_bottom.png"
            )
            test_tab_metrics[name] = {
                "kind": kind,
                "subject": widget_box(controls["subject"]),
                "level": widget_box(controls["level"]),
                "start": widget_box(controls["start"]),
                "end": widget_box(controls["end"]),
                "selected": widget_box(controls["selected"]),
                "add": initial_add_box,
                "add_after_scroll": widget_box(controls["add"]),
                "canvas": widget_box(canvas),
                "form": widget_box(controls["page"]),
                "subject_values": list(controls["subject"]["values"]),
                "filter_label": str(controls["filter_label"].cget("text")),
                "level_values": list(controls["level"]["values"]),
                "start_values": len(controls["start"]["values"]),
                "start_value_samples": list(controls["start"]["values"][:12]),
                "status": str(variables["status"].get()),
            }
            canvas.yview_moveto(0.0)
    captures["test_tabs"] = test_tab_metrics

    app.builder_notebook.select(0)
    app.test_tabs.select(0)
    keyboard_tab_path: list[dict[str, int]] = []
    for _step in range(4):
        app.handle_next_builder_tab()
        root.update_idletasks()
        root.update()
        keyboard_tab_path.append(
            {
                "builder": app.builder_notebook.index("current"),
                "test": app.test_tabs.index("current"),
                "material": app.material_tabs.index("current"),
            }
        )
    captures["test_keyboard_tab_path"] = keyboard_tab_path

    app.builder_notebook.select(0)
    app.test_tabs.select(1)
    review_vars = app.test_builder_vars["복습테스트"]
    review_vars["selected"].set("1,3")
    queue_add_ok = app.add_test_catalog_block("복습테스트")
    root.update_idletasks()
    root.update()
    time.sleep(0.12)
    x = root.winfo_rootx()
    y = root.winfo_rooty()
    width = root.winfo_width()
    height = root.winfo_height()
    ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True).save(
        args.output / "quick_print_test_queue_smoke.png"
    )
    smoke_block = app.blocks[-1] if queue_add_ok and app.blocks else None
    restored_block = (
        app.block_from_config(app.block_to_config(smoke_block))
        if smoke_block is not None
        else None
    )
    smoke_jobs = app.build_print_jobs([smoke_block]) if smoke_block is not None else []
    captures["test_queue_smoke"] = {
        "added": queue_add_ok,
        "source": smoke_block.source if smoke_block is not None else "",
        "selected_numbers": list(smoke_block.selected_numbers or ()) if smoke_block is not None else [],
        "file_count": len(smoke_block.files) if smoke_block is not None else 0,
        "all_paths_exist": all(item.path.is_file() for item in smoke_block.files) if smoke_block is not None else False,
        "printer_route": smoke_block.printer_route if smoke_block is not None else "",
        "job_count": len(smoke_jobs),
        "restored": restored_block is not None,
        "restored_source": restored_block.source if restored_block is not None else "",
    }
    additional_queue_adds: list[bool] = []
    for selected_numbers in ("2", "4", "5"):
        review_vars["selected"].set(selected_numbers)
        additional_queue_adds.append(app.add_test_catalog_block("복습테스트"))
    multi_drag_metrics: dict[str, object] = {
        "additional_blocks_added": additional_queue_adds,
        "selection_preserved_on_drag_start": False,
        "relative_order_preserved": False,
        "selected_after_drop": [],
        "focus_after_drop": None,
    }
    if queue_add_ok and all(additional_queue_adds) and len(app.blocks) >= 4:
        original_blocks = tuple(app.blocks)
        app.select_queue_indices({1, 2}, focus_index=2, anchor_index=1)
        root.update_idletasks()
        root.update()
        time.sleep(0.08)
        ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True).save(
            args.output / "quick_print_queue_multi_select_before.png"
        )

        card_center_y = app.queue_card_top(2) + app.queue_card_height / 2
        event_y = int(card_center_y - app.queue_canvas.canvasy(0))
        app.on_queue_drag_start(SimpleNamespace(y=event_y, state=0))
        multi_drag_metrics["selection_preserved_on_drag_start"] = (
            app.drag_selected_indices == (1, 2)
            and app.selected_block_indices == {1, 2}
        )
        app.drag_active = True
        app.drag_drop_index = len(app.blocks)
        app.drag_pointer_y = app.queue_card_top(len(app.blocks) - 1) + app.queue_card_height
        app.on_queue_drag_release(SimpleNamespace(y=app.queue_canvas.winfo_height() - 1))
        root.update_idletasks()
        root.update()
        time.sleep(app.drop_animation_duration + 0.08)
        root.update()
        ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True).save(
            args.output / "quick_print_queue_multi_select_after.png"
        )
        expected_order = (
            original_blocks[0],
            original_blocks[3],
            original_blocks[1],
            original_blocks[2],
        )
        multi_drag_metrics.update(
            {
                "relative_order_preserved": all(
                    actual is expected
                    for actual, expected in zip(app.blocks, expected_order)
                ),
                "selected_after_drop": sorted(app.selected_block_indices),
                "focus_after_drop": app.selected_block_index_value,
            }
        )
    captures["queue_multi_drag"] = multi_drag_metrics
    app.blocks.clear()
    app.block_status_by_id.clear()
    app.block_detail_by_id.clear()
    app.update_queue_view()
    app.update_preview()

    app.builder_notebook.select(1)
    app.material_tabs.select(1)
    able_states: dict[str, object] = {}
    for kind, suffix in (
        ("풀세트", "full_set"),
        ("교재 + 백지리뷰", "textbook_review"),
        ("교재", "textbook"),
        ("백지리뷰", "review"),
        ("개념정리", "concept"),
    ):
        app.able_document_kind_var.set(kind)
        app.handle_able_document_kind_changed()
        root.update_idletasks()
        root.update()
        time.sleep(0.12)
        x = root.winfo_rootx()
        y = root.winfo_rooty()
        width = root.winfo_width()
        height = root.winfo_height()
        ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True).save(
            args.output / f"quick_print_material_able_{suffix}.png"
        )
        able_states[kind] = {
            "mode": app.able_mode_var.get(),
            "composition_values": list(app.able_document_kind_combo["values"]),
            "start_state": str(app.able_start_combo.cget("state")),
            "end_state": str(app.able_end_combo.cget("state")),
            "selected_numbers_state": str(app.able_selected_numbers_entry.cget("state")),
            "status": app.able_status_var.get(),
        }
    captures["able_controls"] = {
        "document_kind": widget_box(app.able_document_kind_combo),
        "start": widget_box(app.able_start_combo),
        "end": widget_box(app.able_end_combo),
        "selected_numbers": widget_box(app.able_selected_numbers_entry),
        "add": widget_box(app.able_add_button),
        "states": able_states,
    }

    app.material_tabs.select(2)
    app.selected_exam_selected_numbers_var.set("1-3")
    app.refresh_selected_exam_status()
    root.update_idletasks()
    root.update()
    time.sleep(0.12)
    x = root.winfo_rootx()
    y = root.winfo_rooty()
    width = root.winfo_width()
    height = root.winfo_height()
    ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True).save(
        args.output / "quick_print_material_selected_exam.png"
    )
    selected_exam_subject = app.current_selected_exam_subject()
    selected_exam_lesson = app.current_selected_exam_lesson()
    selected_exam_status = app.selected_exam_status_var.get()
    selected_exam_add_ok = app.add_selected_exam_material_block()
    selected_exam_block = (
        app.blocks[-1] if selected_exam_add_ok and app.blocks else None
    )
    captures["selected_exam_controls"] = {
        "subject": widget_box(app.selected_exam_subject_combo),
        "lesson": widget_box(app.selected_exam_lesson_combo),
        "selected_numbers": widget_box(app.selected_exam_selected_numbers_entry),
        "selected_numbers_value": "1-3",
        "add": widget_box(app.selected_exam_add_button),
        "subjects": list(app.selected_exam_subject_combo["values"]),
        "lesson_values": list(app.selected_exam_lesson_combo["values"]),
        "available_lessons": (
            sum(lesson.is_available for lesson in selected_exam_subject.lessons)
            if selected_exam_subject is not None
            else 0
        ),
        "selected_available": (
            selected_exam_lesson.is_available
            if selected_exam_lesson is not None
            else False
        ),
        "status": selected_exam_status,
    }
    captures["selected_exam_queue_smoke"] = {
        "added": selected_exam_add_ok,
        "source": selected_exam_block.source if selected_exam_block is not None else "",
        "file_count": len(selected_exam_block.files) if selected_exam_block is not None else 0,
        "selected_numbers": (
            list(selected_exam_block.selected_numbers or ())
            if selected_exam_block is not None
            else []
        ),
        "all_paths_exist": (
            all(item.path.is_file() for item in selected_exam_block.files)
            if selected_exam_block is not None
            else False
        ),
        "printer_route": (
            selected_exam_block.printer_route if selected_exam_block is not None else ""
        ),
    }
    missing_subject = next(
        (
            subject
            for subject in app.selected_exam_material_catalog.subjects
            if not any(lesson.is_available for lesson in subject.lessons)
        ),
        None,
    )
    if missing_subject is not None:
        app.selected_exam_subject_var.set(missing_subject.name)
        app.refresh_selected_exam_lesson_options()
        root.update_idletasks()
        root.update()
        time.sleep(0.08)
        ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True).save(
            args.output / "quick_print_material_selected_exam_missing.png"
        )
        captures["selected_exam_controls"]["missing_subject"] = missing_subject.name
        captures["selected_exam_controls"]["missing_add_state"] = str(
            app.selected_exam_add_button.cget("state")
        )
        captures["selected_exam_controls"]["missing_status"] = (
            app.selected_exam_status_var.get()
        )
    app.blocks.clear()
    app.block_status_by_id.clear()
    app.block_detail_by_id.clear()
    app.update_queue_view()
    app.update_preview()

    app.material_tabs.select(3)
    lesson_material_metrics: dict[str, object] = {}
    for index, name in enumerate(("training_pack", "frequent_type")):
        app.lesson_material_tabs.select(index)
        root.update_idletasks()
        root.update()
        time.sleep(0.12)
        x = root.winfo_rootx()
        y = root.winfo_rooty()
        width = root.winfo_width()
        height = root.winfo_height()
        ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True).save(
            args.output / f"quick_print_material_{name}.png"
        )
        prefix = "frequent_type" if index == 1 else "training_pack"
        lesson_material_metrics[name] = {
            "subject": widget_box(getattr(app, f"{prefix}_subject_combo")),
            "mode": widget_box(getattr(app, f"{prefix}_mode_combo")),
            "start": widget_box(getattr(app, f"{prefix}_start_combo")),
            "end": widget_box(getattr(app, f"{prefix}_end_combo")),
            "selected": widget_box(getattr(app, f"{prefix}_selected_numbers_entry")),
            "add": widget_box(getattr(app, f"{prefix}_add_button")),
            "subjects": list(getattr(app, f"{prefix}_subject_combo")["values"]),
            "status": getattr(app, f"{prefix}_status_var").get(),
        }
    captures["tab_hierarchy"] = {
        "main": notebook_tab_boxes(app.builder_notebook),
        "material": notebook_tab_boxes(app.material_tabs),
        "lesson_material": notebook_tab_boxes(app.lesson_material_tabs),
    }
    captures["main_tab_layout"] = {
        "notebook_width": app.builder_notebook.winfo_width(),
        "hit_boxes": notebook_tab_hit_boxes(app.builder_notebook),
        "material_hit_boxes": notebook_tab_hit_boxes(app.material_tabs),
        "style_padding": str(app.style.lookup("Builder.TNotebook.Tab", "padding")),
        "style_width": str(app.style.lookup("Builder.TNotebook.Tab", "width")),
        "tab_padding": [
            str(app.builder_notebook.tab(index, "padding"))
            for index in range(len(app.builder_notebook.tabs()))
        ],
    }
    captures["lesson_material_controls"] = lesson_material_metrics

    app.material_tabs.select(4)
    root.update_idletasks()
    root.update()
    time.sleep(0.12)
    ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True).save(
        args.output / "quick_print_material_gyopyeong.png"
    )
    regular_part_state = str(app.gyopyeong_part_combo.cget("state"))
    full_range_subject = next(
        (
            subject.name
            for subject in app.gyopyeong_material_catalog.subjects
            if subject.has_full_range_only
        ),
        "",
    )
    if full_range_subject:
        app.gyopyeong_subject_var.set(full_range_subject)
        app.refresh_gyopyeong_part_options()
        root.update_idletasks()
        root.update()
        time.sleep(0.08)
        ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True).save(
            args.output / "quick_print_material_gyopyeong_full_range.png"
        )
    captures["gyopyeong_controls"] = {
        "subject": widget_box(app.gyopyeong_subject_combo),
        "part": widget_box(app.gyopyeong_part_combo),
        "lesson": widget_box(app.gyopyeong_lesson_combo),
        "add": widget_box(app.gyopyeong_add_button),
        "subjects": list(app.gyopyeong_subject_combo["values"]),
        "regular_part_state": regular_part_state,
        "full_range_subject": full_range_subject,
        "full_range_part": app.gyopyeong_part_var.get(),
        "full_range_part_state": str(app.gyopyeong_part_combo.cget("state")),
        "status": app.gyopyeong_status_var.get(),
    }

    app.material_tabs.select(6)
    root.update_idletasks()
    root.update()
    time.sleep(0.12)
    ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True).save(
        args.output / "quick_print_material_textbook.png"
    )
    captures["textbook_controls"] = {
        "subject": widget_box(app.textbook_subject_combo),
        "publisher": widget_box(app.textbook_publisher_combo),
        "document": widget_box(app.textbook_document_kind_combo),
        "start": widget_box(app.textbook_start_combo),
        "end": widget_box(app.textbook_end_combo),
        "selected": widget_box(app.textbook_selected_numbers_entry),
        "add": widget_box(app.textbook_add_button),
        "subjects": list(app.textbook_subject_combo["values"]),
        "publishers": list(app.textbook_publisher_combo["values"]),
        "documents": list(app.textbook_document_kind_combo["values"]),
        "start_values": len(app.textbook_start_combo["values"]),
        "add_state": str(app.textbook_add_button.cget("state")),
        "status": app.textbook_status_var.get(),
    }
    repaired_textbook_states: dict[str, object] = {}
    for subject, file_stem in (("기하", "geometry"), ("확통", "probability")):
        app.textbook_subject_var.set(subject)
        app.refresh_textbook_publisher_options()
        publisher_values = tuple(app.textbook_publisher_combo["values"])
        publisher_present = "천재(전인태)" in publisher_values
        if publisher_present:
            app.textbook_publisher_var.set("천재(전인태)")
            app.refresh_textbook_document_options()
        document_states: dict[str, object] = {}
        for document_kind, document_stem in (
            ("핵심문제", "core"), ("문제", "problem"), ("해설", "solution")
        ):
            app.textbook_document_kind_var.set(document_kind)
            app.refresh_textbook_lesson_options()
            root.update_idletasks()
            root.update()
            time.sleep(0.08)
            ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True).save(
                args.output / f"quick_print_material_textbook_{file_stem}_{document_stem}.png"
            )
            edition = app.current_textbook_edition()
            document_states[document_kind] = {
                "document_values": list(app.textbook_document_kind_combo["values"]),
                "lesson_count": len(app.textbook_start_combo["values"]),
                "add_state": str(app.textbook_add_button.cget("state")),
                "status": app.textbook_status_var.get(),
                "asset_exists": (
                    edition.asset(document_kind).path.is_file()
                    if edition is not None
                    else False
                ),
                "asset_page_count": (
                    edition.asset(document_kind).page_count
                    if edition is not None
                    else 0
                ),
            }
        repaired_textbook_states[subject] = {
            "publisher_present": publisher_present,
            "publisher_values": list(publisher_values),
            "documents": document_states,
        }
    captures["textbook_controls"]["repaired_editions"] = repaired_textbook_states

    app.textbook_subtabs.select(1)
    root.update_idletasks()
    root.update()
    time.sleep(0.12)
    ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True).save(
        args.output / "quick_print_material_textbook_variant.png"
    )
    captures["textbook_variant_controls"] = {
        "subject": widget_box(app.textbook_variant_subject_combo),
        "publisher": widget_box(app.textbook_variant_publisher_combo),
        "start": widget_box(app.textbook_variant_start_combo),
        "end": widget_box(app.textbook_variant_end_combo),
        "selected": widget_box(app.textbook_variant_selected_numbers_entry),
        "add": widget_box(app.textbook_variant_add_button),
        "subjects": list(app.textbook_variant_subject_combo["values"]),
        "publishers": list(app.textbook_variant_publisher_combo["values"]),
        "lesson_count": len(app.textbook_variant_start_combo["values"]),
        "add_state": str(app.textbook_variant_add_button.cget("state")),
        "status": app.textbook_variant_status_var.get(),
    }
    app.textbook_subtabs.select(0)

    app.builder_notebook.select(1)
    app.material_tabs.select(3)
    app.lesson_material_tabs.select(0)
    nested_keyboard_path: list[dict[str, int]] = []
    for backwards in (False, False, True, True):
        app.handle_next_builder_tab(backwards=backwards)
        root.update_idletasks()
        root.update()
        nested_keyboard_path.append(
            {
                "builder": app.builder_notebook.index("current"),
                "material": app.material_tabs.index("current"),
                "lesson_material": app.lesson_material_tabs.index("current"),
            }
        )
    captures["lesson_material_keyboard_tab_path"] = nested_keyboard_path

    app.frequent_type_selected_numbers_var.set("1,3")
    frequent_type_add_ok = app.add_frequent_type_block()
    frequent_type_block = app.blocks[-1] if frequent_type_add_ok and app.blocks else None
    captures["frequent_type_queue_smoke"] = {
        "added": frequent_type_add_ok,
        "source": frequent_type_block.source if frequent_type_block is not None else "",
        "selected_numbers": list(frequent_type_block.selected_numbers or ()) if frequent_type_block is not None else [],
        "file_count": len(frequent_type_block.files) if frequent_type_block is not None else 0,
        "all_paths_exist": all(item.path.is_file() for item in frequent_type_block.files) if frequent_type_block is not None else False,
        "printer_route": frequent_type_block.printer_route if frequent_type_block is not None else "",
    }
    app.blocks.clear()
    app.block_status_by_id.clear()
    app.block_detail_by_id.clear()
    app.update_queue_view()
    app.update_preview()

    app.builder_notebook.select(1)
    app.material_tabs.select(4)
    gyopyeong_subjects = app.gyopyeong_material_catalog.subject_names
    if gyopyeong_subjects:
        app.gyopyeong_subject_var.set(gyopyeong_subjects[0])
        app.refresh_gyopyeong_part_options()
    app.keyboard_state.enter_builder(tab=1)
    app.keyboard_state.material_tab = 4
    root.update_idletasks()
    root.update()
    root.lift()
    root.focus_force()
    root.update()

    gyopyeong_subject_down_result = None
    gyopyeong_part_down_result = None
    gyopyeong_subject_down_focus_is_part = False
    gyopyeong_part_down_focus_is_lesson = False
    gyopyeong_keyboard_add_result = None
    initial_gyopyeong_block_count = len(app.blocks)
    if gyopyeong_subjects:
        app.gyopyeong_subject_combo.focus_set()
        root.update()
        gyopyeong_subject_down_result = app.handle_control_arrow(
            SimpleNamespace(widget=app.gyopyeong_subject_combo, keysym="Down", state=0)
        )
        root.update()
        gyopyeong_subject_down_focus_is_part = root.focus_get() is app.gyopyeong_part_combo
        gyopyeong_part_down_result = app.handle_control_arrow(
            SimpleNamespace(widget=app.gyopyeong_part_combo, keysym="Down", state=0)
        )
        root.update()
        gyopyeong_part_down_focus_is_lesson = root.focus_get() is app.gyopyeong_lesson_combo
        with (
            patch.object(app, "take_name_entry_ime", return_value=False),
            patch("unit_print_manager.complete_windows_ime_composition", return_value=False),
        ):
            gyopyeong_keyboard_add_result = app.handle_ctrl_enter_shortcut(
                SimpleNamespace(widget=app.gyopyeong_lesson_combo)
            )
            root.update()
    gyopyeong_add_ok = len(app.blocks) == initial_gyopyeong_block_count + 1
    gyopyeong_block = app.blocks[-1] if gyopyeong_add_ok and app.blocks else None
    captures["gyopyeong_keyboard_navigation"] = {
        "subject_down_result": gyopyeong_subject_down_result,
        "subject_down_focus_is_part": gyopyeong_subject_down_focus_is_part,
        "part_down_result": gyopyeong_part_down_result,
        "part_down_focus_is_lesson": gyopyeong_part_down_focus_is_lesson,
        "ctrl_enter_result": gyopyeong_keyboard_add_result,
        "ctrl_enter_added_block": gyopyeong_add_ok,
    }
    captures["gyopyeong_queue_smoke"] = {
        "added": gyopyeong_add_ok,
        "source": gyopyeong_block.source if gyopyeong_block is not None else "",
        "file_count": len(gyopyeong_block.files) if gyopyeong_block is not None else 0,
        "all_paths_exist": all(item.path.is_file() for item in gyopyeong_block.files) if gyopyeong_block is not None else False,
        "printer_route": gyopyeong_block.printer_route if gyopyeong_block is not None else "",
    }
    app.blocks.clear()
    app.block_status_by_id.clear()
    app.block_detail_by_id.clear()
    app.update_queue_view()
    app.update_preview()

    app.builder_notebook.select(2)
    root.update_idletasks()
    root.update()
    app.blank_review_canvas.yview_moveto(0.0)
    app.ensure_test_widget_visible(app.blank_review_copies_spinbox)
    root.update_idletasks()
    root.update()
    time.sleep(0.08)
    ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True).save(
        args.output / "quick_print_2_blank_review_form.png"
    )
    blank_review_boxes = {
        "copies": widget_box(app.blank_review_copies_spinbox),
        "add": widget_box(app.blank_review_add_button),
    }
    app.ensure_test_widget_visible(app.omr_copies_spinbox)
    root.update_idletasks()
    root.update()
    time.sleep(0.08)
    ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True).save(
        args.output / "quick_print_2_omr_form.png"
    )
    omr_boxes = {
        "copies": widget_box(app.omr_copies_spinbox),
        "add": widget_box(app.omr_add_button),
    }
    captures["blank_review_controls"] = {
        **blank_review_boxes,
        "status": app.blank_review_status_var.get(),
        "omr_copies": omr_boxes["copies"],
        "omr_add": omr_boxes["add"],
        "omr_status": app.omr_status_var.get(),
        "has_student_name_entry": hasattr(app, "blank_review_new_block_name_entry"),
    }
    omr_add_ok = app.add_omr_block()
    root.update_idletasks()
    root.update()
    time.sleep(0.08)
    ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True).save(
        args.output / "quick_print_omr_queue_smoke.png"
    )
    omr_block = app.blocks[-1] if omr_add_ok and app.blocks else None
    captures["omr_queue_smoke"] = {
        "added": omr_add_ok,
        "source": omr_block.source if omr_block is not None else "",
        "file_count": len(omr_block.files) if omr_block is not None else 0,
        "all_paths_exist": all(item.path.is_file() for item in omr_block.files) if omr_block is not None else False,
        "printer_route": omr_block.printer_route if omr_block is not None else "",
        "printer_override": omr_block.printer_override if omr_block is not None else "",
    }
    app.blocks.clear()
    app.block_status_by_id.clear()
    app.block_detail_by_id.clear()
    app.update_queue_view()
    app.update_preview()
    captures["order_controls"] = {
        "list": widget_box(app.order_csv_list),
        "choose": widget_box(app.order_csv_choose_button),
        "template": widget_box(app.order_csv_template_button),
        "clear": widget_box(app.order_csv_clear_button),
        "add": widget_box(app.order_csv_add_button),
    }
    app.builder_notebook.select(0)
    app.test_tabs.select(0)
    student_name_states: dict[str, object] = {}
    for enabled, suffix in ((False, "off"), (True, "on")):
        app.name_separator_page_var.set(enabled)
        app.refresh_toggle_buttons()
        app.handle_name_separator_toggle()
        root.update_idletasks()
        root.update()
        time.sleep(0.12)
        x = root.winfo_rootx()
        y = root.winfo_rooty()
        width = root.winfo_width()
        height = root.winfo_height()
        ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True).save(
            args.output / f"quick_print_student_name_{suffix}.png"
        )
        student_name_states[suffix] = {
            "state": str(app.unit_new_block_name_entry.cget("state")),
            "style": str(app.unit_new_block_name_entry.cget("style")),
            "box": widget_box(app.unit_new_block_name_entry),
        }
    captures["student_name_states"] = student_name_states

    # Keyboard-only QA: arrows move within the form, Tab changes workspace,
    # hidden subtab controls keep bindings, and ordinary Ctrl+Enter has no
    # fixed grace delay. No print command is invoked here.
    app.builder_notebook.select(0)
    app.test_tabs.select(0)
    app.keyboard_state.enter_builder(tab=0)
    app.keyboard_state.test_tab = 0
    root.update_idletasks()
    root.update()
    root.lift()
    root.focus_force()
    root.update()
    app.level_combo.focus_set()
    root.update()
    right_result = app.handle_control_arrow(
        SimpleNamespace(widget=app.level_combo, keysym="Right", state=0)
    )
    root.update()
    right_focus_is_round = root.focus_get() is app.round_combo

    app.handle_workspace_tab(SimpleNamespace(widget=app.round_combo, state=0))
    root.update()
    tab_focus_is_queue = root.focus_get() is app.queue_canvas
    app.handle_workspace_tab(SimpleNamespace(widget=app.queue_canvas, state=0))
    root.update()
    tab_focus_is_builder = app.widget_is_in_builder(root.focus_get())

    app.test_tabs.select(1)
    app.keyboard_state.enter_builder(tab=0)
    app.keyboard_state.test_tab = 1
    root.update_idletasks()
    root.update()
    review_controls = app.test_builder_controls["복습테스트"]
    review_controls["subject"].focus_set()
    root.update()
    down_result = app.handle_control_arrow(
        SimpleNamespace(widget=review_controls["subject"], keysym="Down", state=0)
    )
    root.update()
    down_focus_is_filter = root.focus_get() is review_controls["level"]

    app.material_dropdown_widget = review_controls["level"]
    app.keyboard_state.open_combo(str(review_controls["level"]))
    posted_arrow_result = app.handle_material_combo_arrow(
        SimpleNamespace(widget=review_controls["level"], keysym="Down", state=0)
    )
    app.material_dropdown_widget = None
    app.keyboard_state.close_popup()

    shortcut_called: list[float] = []
    started_at = time.perf_counter()
    with (
        patch.object(app, "take_name_entry_ime", return_value=False),
        patch("unit_print_manager.complete_windows_ime_composition", return_value=False),
    ):
        app.run_keyboard_after_ime(
            "gui-latency-smoke",
            lambda: shortcut_called.append(time.perf_counter()),
            source_widget=review_controls["selected"],
        )
        root.update()
    shortcut_latency_ms = (
        (shortcut_called[0] - started_at) * 1000 if shortcut_called else None
    )

    app.builder_notebook.select(0)
    app.test_tabs.select(0)
    app.keyboard_state.enter_builder(tab=0)
    app.keyboard_state.test_tab = 0
    app.name_separator_page_var.set(False)
    root.update_idletasks()
    root.update()
    initial_block_count = len(app.blocks)
    add_started_at = time.perf_counter()
    with (
        patch.object(app, "take_name_entry_ime", return_value=False),
        patch("unit_print_manager.complete_windows_ime_composition", return_value=False),
    ):
        app.handle_ctrl_enter_shortcut(SimpleNamespace(widget=app.start_combo))
        root.update()
    block_add_latency_ms = (time.perf_counter() - add_started_at) * 1000
    block_added = len(app.blocks) == initial_block_count + 1

    print_shortcut_called: list[float] = []
    print_started_at = time.perf_counter()
    with patch.object(
        app,
        "start_print_thread",
        side_effect=lambda: print_shortcut_called.append(time.perf_counter()),
    ):
        app.handle_ctrl_shift_enter_shortcut(SimpleNamespace(widget=app.start_combo))
    print_shortcut_latency_ms = (
        (print_shortcut_called[0] - print_started_at) * 1000
        if print_shortcut_called
        else None
    )

    captures["keyboard_navigation"] = {
        "right_result": right_result,
        "right_focus_is_round": right_focus_is_round,
        "tab_focus_is_queue": tab_focus_is_queue,
        "tab_focus_is_builder": tab_focus_is_builder,
        "hidden_test_tab_bindings": {
            "tab": bool(review_controls["selected"].bind("<Tab>")),
            "up": bool(review_controls["selected"].bind("<Up>")),
            "ctrl_enter": bool(review_controls["selected"].bind("<Control-Return>")),
        },
        "down_result": down_result,
        "down_focus_is_filter": down_focus_is_filter,
        "posted_combo_arrow_passes_to_native_list": posted_arrow_result is None,
        "ctrl_enter_callback_ran": bool(shortcut_called),
        "ctrl_enter_latency_ms": shortcut_latency_ms,
        "ctrl_enter_block_added": block_added,
        "ctrl_enter_block_add_latency_ms": block_add_latency_ms,
        "print_shortcut_dispatched": bool(print_shortcut_called),
        "print_shortcut_dispatch_latency_ms": print_shortcut_latency_ms,
        "blank_review_tab_bound": bool(app.blank_review_add_button.bind("<Tab>")),
        "order_tab_bound": bool(app.order_csv_add_button.bind("<Tab>")),
    }
    captures["runtime"] = {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "windows_release": platform.release(),
        "windows_version": platform.version(),
        "tcl_version": str(root.tk.call("info", "patchlevel")),
        "tk_version": str(root.tk.call("package", "require", "Tk")),
        "tk_scaling": float(root.tk.call("tk", "scaling")),
        "screen_width": root.winfo_screenwidth(),
        "screen_height": root.winfo_screenheight(),
        "ui_font_family": app.ui_font_family,
        "capture_mode": (
            f"geometry:{args.geometry}"
            if args.geometry
            else "windowed:1440x900"
            if args.windowed
            else "maximized"
        ),
    }
    (args.output / "quick_print_gui_metrics.json").write_text(
        json.dumps(captures, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    app.close_pdf_work_services()
    app.close_taskbar_indicator()
    root.destroy()
    history_directory.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
