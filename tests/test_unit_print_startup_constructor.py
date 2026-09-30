"""Exercise the real operator constructor and idle UI with registered local assets.

The root stays withdrawn. External printing/device/native integration and every
operator-state output are isolated; catalogs and PDF metadata remain real reads.
"""

from __future__ import annotations

import shutil
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
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
        for name in ("load_printers", "run_powershell", "shell_print_pdf", "sumatra_enqueue_pdfs",
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


if __name__ == "__main__":
    unittest.main()
