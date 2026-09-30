from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pypdf import PdfWriter

from test_unit_print_queue_lifecycle import Variable, make_app
from unit_print_device_state import DeviceHealth, DeviceSnapshot
from unit_print_manager import ExamPdf, PrintBlock, PrintJob, PrintRunOptions
from unit_print_pdf_printing import preflight_pdfs
from unit_print_printer_binding import PrinterBindingInventory, PrinterBindingReadError, PrinterQueueBinding
from unit_print_retry import RetryBatch


class PrinterBindingWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.pdf = self.directory / "시험지.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        with self.pdf.open("wb") as stream:
            writer.write(stream)
        self.block = PrintBlock("대수", "상", "1", 1, 1, 1, [ExamPdf(1, "시험지", self.pdf)])
        self.app = make_app([self.block])
        self.app.pdf_preflight_by_path = {result.path: result for result in preflight_pdfs([self.pdf]).results}
        self.monitor = SimpleNamespace(
            client=SimpleNamespace(base_url="http://192.168.0.248:8000"),
            latest_snapshot=DeviceSnapshot(observed_at=datetime.now().astimezone(), health=DeviceHealth.READY, authenticated=True),
        )
        self.app.device_monitor = self.monitor
        self.options = PrintRunOptions("FAKE", "sumatra", "FAKE.exe", True, False, 3, 1, device_completion_mode=True)
        self.app.current_print_run_options = Mock(return_value=self.options)
        queues = ("FAKE", "FAKE - 세로", "FAKE - 가로", "FAKE - 구분지", "FAKE -A:ble", "SameDeviceOverride")
        self.inventory = PrinterBindingInventory(tuple(self.binding(name) for name in queues))
        self.reader = patch("unit_print_manager.read_printer_binding_inventory", return_value=self.inventory).start()
        self.addCleanup(patch.stopall)
        self.warning = patch("unit_print_manager.messagebox.showwarning").start()
        patch("unit_print_manager.messagebox.showinfo").start()
        self.confirm = patch("unit_print_manager.messagebox.askyesno", return_value=True).start()
        self.worker = patch("unit_print_manager.threading.Thread").start()
        for name in ("sumatra_enqueue_pdfs", "shell_print_pdf", "save_config", "load_printers", "run_powershell"):
            patch("unit_print_manager." + name, side_effect=AssertionError(name)).start()

    @staticmethod
    def binding(name, host="192.168.0.248"):
        return PrinterQueueBinding(name, "IP_" + host, host, "TCPMON.DLL", "Local")

    def test_block_destination_set_uses_real_overrides_and_auxiliary_routes(self):
        self.block.source = "test_ilsan"
        self.block.printer_route = "세로"
        self.block.printer_override = "SameDeviceOverride"
        self.block.files = [replace(self.block.files[0], printer_override="UnusedItemOverride")]
        able = replace(self.block, source="material_able", files=[replace(self.block.files[0], printer_override="FAKE -A:ble")])
        with patch("unit_print_manager.materialize_name_separator_pdf", side_effect=AssertionError("PDF build")), \
             patch.object(self.app, "build_print_jobs", side_effect=AssertionError("job build")):
            targets = self.app.block_print_destinations([self.block, able], "FAKE",
                                                      include_separator_pages=True, include_name_separator_pages=True)
        self.assertEqual(targets, frozenset({"FAKE", "SameDeviceOverride", "FAKE -A:ble", "FAKE - 세로", "FAKE - 가로", "FAKE - 구분지"}))

    def test_binding_success_captures_source_and_actual_destinations_once(self):
        verified = self.app.ensure_device_printer_binding(self.options, blocks=[self.block])
        self.reader.assert_called_once()
        self.assertEqual(verified.device_monitor_base_url, self.monitor.client.base_url)
        self.assertEqual(verified.device_verified_printers, frozenset({"FAKE", "FAKE - 세로"}))
        self.assertEqual(self.options.device_verified_printers, frozenset())

    def test_binding_disabled_or_preview_never_reads_windows_inventory(self):
        for options in (replace(self.options, device_completion_mode=False), replace(self.options, dry_run=True)):
            self.assertIs(self.app.ensure_device_printer_binding(options, blocks=[self.block]), options)
        self.reader.assert_not_called()

    def test_retry_verifies_actual_remaining_job_route_before_start(self):
        override_job = PrintJob(1, 1, id(self.block), self.block.summary, self.block.files[0], 1, printer_override="OtherDevice")
        self.app.retry_batch = RetryBatch((override_job,))
        self.reader.return_value = PrinterBindingInventory(self.inventory.queues + (self.binding("OtherDevice", "192.168.0.20"),))
        self.app.retry_remaining_jobs()
        self.reader.assert_called_once()
        self.worker.assert_not_called()
        self.confirm.assert_not_called()
        self.assertIn("OtherDevice", self.warning.call_args.args[1])
        self.assertIsNotNone(self.app.retry_batch)

    def test_start_read_failure_stops_before_confirmation_or_worker(self):
        self.app.validate_blocks = Mock(return_value=[self.block])
        self.reader.side_effect = PrinterBindingReadError("프린터 연결 조회 실패")
        self.app.start_print_thread()
        self.reader.assert_called_once()
        self.worker.assert_not_called()
        self.confirm.assert_not_called()
        self.warning.assert_called_once()
        self.assertFalse(self.app.is_printing)

    def test_append_validation_failure_does_not_register_or_send_jobs(self):
        self.app.is_printing = True
        verified = self.app.ensure_device_printer_binding(self.options, blocks=[self.block])
        self.app.print_run_options = verified
        self.app.print_scheduled_block_ids = {id(self.block)}
        added = replace(self.block, printer_override="OtherDevice")
        self.app.blocks.append(added)
        self.reader.reset_mock()
        self.reader.return_value = PrinterBindingInventory(self.inventory.queues + (self.binding("OtherDevice", "192.168.0.20"),))
        self.app.append_new_blocks_to_active_print()
        self.reader.assert_called_once()
        self.assertEqual(self.app.print_pending_jobs, [])
        self.assertNotIn(id(added), self.app.print_scheduled_block_ids)
        self.confirm.assert_not_called()

    def test_verified_append_is_available_to_captured_worker_options(self):
        verified = self.app.ensure_device_printer_binding(self.options, blocks=[self.block])
        self.app.print_run_options = verified
        self.app.is_printing = True
        self.app.print_scheduled_block_ids = {id(self.block)}
        added = replace(self.block, printer_override="SameDeviceOverride")
        self.app.blocks.append(added)
        self.reader.reset_mock()
        self.app.append_new_blocks_to_active_print()
        self.reader.assert_called_once()
        self.assertIn(id(added), self.app.print_scheduled_block_ids)
        self.assertEqual(len(self.app.print_pending_jobs), 1)
        self.assertNotIn("SameDeviceOverride", verified.device_verified_printers)
        self.app.assert_device_binding_for_dispatch(verified, self.monitor, "SameDeviceOverride")

    def test_guard_never_requeries_inventory_or_accepts_monitor_replacement(self):
        verified = self.app.ensure_device_printer_binding(self.options, blocks=[self.block])
        self.app.print_run_options = verified
        self.reader.reset_mock()
        for _ in range(5):
            self.app.assert_device_binding_for_dispatch(verified, self.monitor, "FAKE - 세로")
        self.reader.assert_not_called()
        self.app.device_monitor = SimpleNamespace(client=self.monitor.client)
        with self.assertRaises(RuntimeError):
            self.app.assert_device_binding_for_dispatch(verified, self.monitor, "FAKE - 세로")

    def test_device_connection_cannot_change_during_active_print(self):
        self.app.is_printing = True
        with self.assertRaises(RuntimeError):
            self.app.activate_device_monitor(Mock())
        self.assertIs(self.app.device_monitor, self.monitor)
        with patch("unit_print_manager.tk.Toplevel", side_effect=AssertionError("dialog opened")):
            self.app.open_device_monitor_dialog()


if __name__ == "__main__":
    unittest.main()
