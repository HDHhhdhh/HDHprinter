"""Exercise the real operator constructor and idle UI with registered local assets.

The root stays withdrawn. External printing/device/native integration and every
operator-state output are isolated; catalogs and PDF metadata remain real reads.
"""

from __future__ import annotations

import shutil
import tempfile
import time
import tkinter as tk
from tkinter import font as tkfont
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import unit_print_history_events
import unit_print_manager as manager
import unit_print_materials
import unit_print_pdf_metadata


class StartupConstructorTests(unittest.TestCase):
    def test_registered_catalog_startup_finishes_real_constructor_and_idle_ui_without_dispatch(self):
        temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        apex_cache = temporary / "material_catalog.json"
        self.assertTrue(manager.MATERIAL_CATALOG_PATH.is_file(), "Startup metadata cache must accompany registered APEX assets.")
        shutil.copy2(manager.MATERIAL_CATALOG_PATH, apex_cache)
        config = {
            "root_dir": str(manager.BUNDLED_DATA_ROOT / "단원평가"),
            "printer": "TEST startup printer",
            "engine": "SumatraPDF",
            "queue_batch_size": 12,
            "queue_refill_threshold": 6,
            "device_monitor": {"enabled": False},
        }
        self.enterContext(patch.object(manager, "load_config", return_value=config))
        save_config = self.enterContext(patch.object(manager, "save_config"))
        self.enterContext(patch.object(manager, "CONFIG_PATH", temporary / "config.json"))
        self.enterContext(patch.object(manager, "MATERIAL_CATALOG_PATH", apex_cache))
        self.enterContext(patch.object(manager, "RECOVERY_DIRECTORY", temporary / "recovery"))
        self.enterContext(patch.object(unit_print_history_events, "HISTORY_DIRECTORY", temporary / "history"))
        self.enterContext(patch.object(unit_print_pdf_metadata, "_default_cache",
                                      unit_print_pdf_metadata.PdfPageCountCache(temporary / "page_counts.json")))
        self.enterContext(patch.object(manager, "find_sumatra_pdf", return_value="TEST Sumatra.exe"))
        analysis = self.enterContext(patch.object(
            unit_print_materials, "analyze_apex_document",
            side_effect=AssertionError("A registered startup cache missed: migration must preserve PDF size and mtime."),
        ))
        forbidden = {}
        for name in ("load_printers", "run_powershell", "sumatra_enqueue_pdfs",
                     "save_device_credential", "load_device_credential", "delete_device_credential"):
            forbidden[name] = self.enterContext(patch.object(manager, name, side_effect=AssertionError(f"External startup operation: {name}")))
        for name in ("activate_windows_korean_input", "set_windows_ime_composition_font",
                     "complete_windows_ime_composition", "take_windows_ime_composition"):
            self.enterContext(patch.object(manager, name, return_value=False if name != "take_windows_ime_composition" else ""))
        dialogs = {}
        for name in ("showerror", "showwarning", "showinfo", "askyesno"):
            dialogs[name] = self.enterContext(patch.object(manager.messagebox, name, side_effect=AssertionError("Unexpected startup dialog")))
        callbacks = {}
        for name in ("maximize_on_start", "initialize_taskbar_indicator", "initialize_windows_notifier",
                     "warm_printer_inventory_cache", "initialize_device_monitor_from_config", "poll_device_status"):
            callbacks[name] = self.enterContext(patch.object(manager.PrintManagerApp, name))
        notify = self.enterContext(patch.object(manager.PrintManagerApp, "notify_operator", return_value=False))
        # ttkbootstrap keeps a process-wide singleton tied to its Tcl interpreter.
        # Do not reuse another test's Style or leave this withdrawn root behind.
        self.enterContext(patch.object(manager.ttkbootstrap.Style, "instance", None))
        root = manager.TkinterDnD.Tk() if manager.TkinterDnD is not None else tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        callback_errors = []
        root.report_callback_exception = lambda error_type, error, tb: callback_errors.append(error)
        started = time.monotonic()
        app = manager.PrintManagerApp(root)
        constructor_elapsed = time.monotonic() - started

        def close_services():
            app.is_closing = True
            app.close_pdf_work_services()
            app.queue_recovery_writer.close()

        self.addCleanup(close_services)
        root.update_idletasks()
        root.update()
        idle_marker = []
        root.after_idle(lambda: idle_marker.append(True))
        root.update_idletasks()
        self.assertEqual([True], idle_marker, "Real UI idle processing did not finish.")
        self.assertEqual([], callback_errors, "Constructor-created UI callbacks failed.")
        self.assertEqual("withdrawn", root.state())
        self.assertEqual(manager.APP_TITLE, root.title())
        self.assertTrue(app.builder_notebook.winfo_exists())
        self.assertTrue(app.queue_canvas.winfo_exists())
        self.assertTrue(app.preview_text.winfo_exists())
        # Query real ttk resolution, including overlapping pointer/disabled
        # states. No native input or printer invocation is required.
        primary = app.style
        self.assertEqual(app.colors["disabled_bg"], primary.lookup(
            "Primary.TButton", "background", ("disabled", "active"),
        ))
        self.assertEqual(app.colors["focus"], primary.lookup(
            "SoftAction.TButton", "bordercolor", ("focus",),
        ))
        self.assertEqual(app.colors["text"], primary.lookup(
            "TCombobox", "foreground", ("readonly",),
        ))
        for field in (app.subject_combo, app.unit_selected_numbers_entry, app.block_copies_spinbox):
            actual_font = tkfont.Font(root=root, font=field.cget("font")).actual()
            self.assertEqual(11, actual_font["size"])
        start_command = app.start_print_button.cget("command")
        app.start_print_button.state(["alternate"])
        self.assertEqual(app.colors["success_soft"], primary.lookup(
            "Primary.TButton", "background", app.start_print_button.state(),
        ))
        app.refresh_print_action_buttons()
        self.assertNotIn("alternate", app.start_print_button.state())
        self.assertEqual("인쇄 시작", app.start_print_button.cget("text"))
        self.assertEqual(start_command, app.start_print_button.cget("command"))
        self.assertEqual(62, app.queue_card_height)
        self.assertEqual(8, app.queue_card_gap)
        self.assertEqual(8, app.queue_padding)
        self.assertEqual("Surface.TFrame", app.header_frame.cget("style"))
        self.assertEqual(app.colors["panel"], primary.lookup("Surface.TFrame", "background"))
        self.assertEqual(2, app.header_frame.grid_size()[0])
        self.assertEqual(app.colors["complete"], app.status_badge.cget("fg"))
        self.assertFalse(hasattr(app, "aurora_header_canvas"))
        self.assertFalse(hasattr(app, "header_eta_var"))
        app.app_status_var.set("전송 완료")
        app.refresh_status_badge()
        self.assertEqual(app.colors["complete"], app.status_badge.cget("fg"))
        app.app_status_var.set("실제 인쇄 확인 중")
        app.refresh_status_badge()
        self.assertEqual(app.colors["success"], app.status_badge.cget("fg"))
        # Inspect real Canvas items, not a screenshot. No source file is opened
        # and the draw call does not change the queue, history, or print flow.
        sample = manager.PrintBlock(
            subject="검증 과목", level="Lv.3", round_name="1",
            start=1, end=1, copies=3,
            files=[manager.ExamPdf(1, "긴 자료명 " * 20, Path("TEST.pdf"))],
            name_separator_label="검증 학생",
        )
        app.block_status_by_id[id(sample)] = "완료"
        app.queue_canvas.delete("all")
        app.draw_queue_card(0, sample, 900)
        texts = [item for item in app.queue_canvas.find_all() if app.queue_canvas.type(item) == "text"]
        body = next(item for item in texts if app.queue_canvas.itemcget(item, "anchor") == "w")
        title = next(item for item in texts if app.queue_canvas.itemcget(item, "text") == "단원평가 / 검증 과목")
        copies = next(item for item in texts if app.queue_canvas.itemcget(item, "text") == "3부")
        completed = next(item for item in texts if app.queue_canvas.itemcget(item, "text") == "전송 완료")
        self.assertNotIn("\n", app.queue_canvas.itemcget(body, "text"))
        self.assertEqual(11, tkfont.Font(root=root, font=app.queue_canvas.itemcget(body, "font")).actual()["size"])
        self.assertEqual("bold", tkfont.Font(root=root, font=app.queue_canvas.itemcget(body, "font")).actual()["weight"])
        self.assertEqual(app.colors["complete"], app.queue_canvas.itemcget(body, "fill"))
        self.assertEqual(app.colors["queue_title"], app.queue_canvas.itemcget(title, "fill"))
        self.assertEqual("bold", tkfont.Font(root=root, font=app.queue_canvas.itemcget(title, "font")).actual()["weight"])
        self.assertEqual(10, tkfont.Font(root=root, font=app.queue_canvas.itemcget(copies, "font")).actual()["size"])
        self.assertLessEqual(app.queue_canvas.bbox(body)[2], app.queue_canvas.bbox(copies)[0] + 2)
        self.assertTrue(all(app.queue_canvas.bbox(item)[2] <= app.queue_canvas.bbox(copies)[0] + 2
                            for item in texts if app.queue_canvas.itemcget(item, "anchor") == "w"))
        self.assertEqual(app.colors["complete"], app.queue_canvas.itemcget(completed, "fill"))
        app.block_detail_by_id[id(sample)] = "실제 인쇄 완료"
        app.queue_canvas.delete("all")
        app.draw_queue_card(0, sample, 900)
        self.assertTrue(any(app.queue_canvas.itemcget(item, "text") == "인쇄 완료"
                            for item in app.queue_canvas.find_all()
                            if app.queue_canvas.type(item) == "text"))
        app.block_status_by_id.pop(id(sample))
        app.block_detail_by_id.pop(id(sample))
        app.draw_queue()
        self.assertEqual(0, app.builder_notebook.index("current"))
        self.assertEqual(0, app.test_tabs.index("current"))
        self.assertGreater(len(app.apex_material_catalog.documents), 0)
        self.assertEqual(42, len(app.textbook_catalog.verified_editions))
        self.assertTrue(app.catalog)
        self.assertEqual([], app.blocks)
        self.assertFalse(app.is_printing)
        self.assertIsNone(app.print_thread)
        self.assertIsNone(app.device_monitor)
        self.assertEqual(1, app.block_copies_var.get())
        self.assertEqual(1, app.edit_copies_var.get())
        self.assertLess(constructor_elapsed, 30, "Registered startup took too long before the event loop.")
        analysis.assert_not_called()
        callbacks["initialize_taskbar_indicator"].assert_called_once()
        callbacks["initialize_windows_notifier"].assert_called_once()
        callbacks["maximize_on_start"].assert_called_once()
        save_config.assert_not_called()
        notify.assert_not_called()
        for operation in forbidden.values():
            operation.assert_not_called()
        for dialog in dialogs.values():
            dialog.assert_not_called()
        self.assertTrue((temporary / "history").is_dir())
        self.assertFalse(app.queue_recovery_writer.busy)

        # Exercise the constructed editor's real Entry/Spinbox variables and
        # Canvas handlers. PDF work is isolated; this cannot dispatch printing.
        with patch.object(app, "update_preview"):
            first, second = (
                manager.PrintBlock("첫 자료", "직접", "파일", 1, 1, 1,
                    [manager.ExamPdf(1, "첫 자료", temporary / "editor-a.pdf")], source="custom"),
                manager.PrintBlock("둘째 자료", "직접", "파일", 1, 1, 1,
                    [manager.ExamPdf(1, "둘째 자료", temporary / "editor-b.pdf")], source="custom"),
            )
            app.blocks = [first, second]
            app.update_queue_view(select_index=0)
            app.name_separator_page_var.set(True)
            app.refresh_name_separator_editor_state()
            app.edit_name_separator_entry.insert(0, "검증 이름")
            app.edit_copies_spinbox.delete(0, "end")
            app.edit_copies_spinbox.insert(0, "3")
            self.assertTrue(app.keyboard_state.editor_dirty)
            self.assertEqual(1, first.copies)
            self.assertTrue(app.select_queue_index(1))
            self.assertEqual(("검증 이름", 3), (
                app.blocks[0].name_separator_label, app.blocks[0].copies,
            ))
            self.assertIs(second, app.editor_block)
            self.assertEqual("", app.edit_name_separator_entry.get())
            self.assertFalse(app.keyboard_state.editor_dirty)
            app.edit_copies_spinbox.delete(0, "end")
            app.edit_copies_spinbox.insert(0, "0")
            with patch.object(manager.messagebox, "showwarning") as save_warning:
                self.assertFalse(app.select_queue_index(0))
                save_warning.assert_called_once()
            self.assertEqual(1, app.selected_block_index())
            self.assertEqual("0", app.edit_copies_spinbox.get())
            app.handle_escape_shortcut(SimpleNamespace(widget=app.edit_copies_spinbox))
            self.assertEqual("1", app.edit_copies_spinbox.get())
            self.assertFalse(app.keyboard_state.editor_dirty)
            self.assertEqual([], callback_errors)
        save_config.assert_not_called()
        notify.assert_not_called()
        for operation in forbidden.values():
            operation.assert_not_called()


if __name__ == "__main__":
    unittest.main()
