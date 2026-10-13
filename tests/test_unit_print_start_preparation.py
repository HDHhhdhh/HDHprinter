from __future__ import annotations

import tempfile
import threading
import time
import unittest
from concurrent.futures import Future
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pypdf import PdfWriter

from test_unit_print_queue_lifecycle import make_app
from unit_print_device_state import DeviceHealth, DeviceSnapshot
from unit_print_history import HistoryWriteError
from unit_print_manager import ExamPdf, PrintBlock, PrintManagerApp, PrintRunOptions
from unit_print_printer_binding import PrinterBindingInventory, PrinterQueueBinding
from unit_print_retry import RetryBatch


class UiVariable:
    def __init__(self, value):
        self.value = value
        self.owner = threading.get_ident()

    def get(self):
        assert threading.get_ident() == self.owner, "Worker read a Tk variable"
        return self.value

    def set(self, value):
        assert threading.get_ident() == self.owner, "Worker wrote a Tk variable"
        self.value = value


class UiButton:
    def __init__(self, text):
        self.owner = threading.get_ident()
        self.values = {"text": text, "state": "normal"}
        self.states = set()

    def state(self, states=None):
        assert threading.get_ident() == self.owner
        for state in states or ():
            if state.startswith("!"):
                self.states.discard(state[1:])
            else:
                self.states.add(state)
        return tuple(self.states)

    def cget(self, key):
        assert threading.get_ident() == self.owner
        return self.values[key]

    def configure(self, **values):
        assert threading.get_ident() == self.owner
        self.values.update(values)


class UiLoop:
    def __init__(self):
        self.owner = threading.get_ident()
        self.callbacks = {}
        self.next_id = 0

    def after(self, delay, callback):
        assert threading.get_ident() == self.owner, "Worker called root.after"
        self.next_id += 1
        self.callbacks[self.next_id] = (time.monotonic() + delay / 1000, callback)
        return self.next_id

    def after_cancel(self, identifier):
        assert threading.get_ident() == self.owner
        self.callbacks.pop(identifier, None)

    def pump_until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                raise AssertionError("UI operation did not finish")
            now = time.monotonic()
            ready = [identifier for identifier, (when, _) in self.callbacks.items() if when <= now]
            for identifier in ready:
                entry = self.callbacks.pop(identifier, None)
                if entry is not None:
                    entry[1]()
            time.sleep(0.002)


class PrintStartPreparationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.pdf = self.directory / "body.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        with self.pdf.open("wb") as stream:
            writer.write(stream)
        self.block = PrintBlock("test", "", "", 1, 1, 2, [ExamPdf(1, "body", self.pdf)])
        self.app = make_app([self.block])
        del self.app._launch_print_preparation
        self.app.root = UiLoop()
        self.app.is_closing = False
        self.app.device_hold_active = False
        self.app.print_queue_revision = 0
        self.app.pdf_preflight_by_path = {}
        self.app.pdf_preflight_signature_by_path = {}
        self.app.device_monitor = SimpleNamespace(
            client=SimpleNamespace(base_url="http://192.0.2.1"),
            latest_snapshot=DeviceSnapshot(
                observed_at=datetime.now().astimezone(), health=DeviceHealth.READY,
                authenticated=True,
            ),
        )
        self.options = PrintRunOptions("FAKE", "sumatra", "FAKE.exe", True, False, 3, 1,
                                       device_completion_mode=True)
        self.current_options = self.options
        owner = threading.get_ident()

        def options():
            self.assertEqual(owner, threading.get_ident(), "Worker read current UI options")
            return self.current_options

        self.app.current_print_run_options = options
        self.app.print_run_options = self.options
        for name, value in (
            ("app_status_var", "idle"), ("current_work_var", "idle"),
            ("queue_status_var", "idle"), ("separator_page_var", False),
            ("name_separator_page_var", False), ("printer_var", "FAKE"),
            ("profile_status_var", "idle"),
        ):
            setattr(self.app, name, UiVariable(value))
        self.app.set_status = self.app.app_status_var.set
        self.app.set_current_work = self.app.current_work_var.set
        self.app.start_print_button = UiButton("인쇄 시작")
        self.app.cancel_print_button = UiButton("전송 중지")
        self.app.retry_button = UiButton("재시도")
        self.app.refresh_print_action_buttons = lambda: PrintManagerApp.refresh_print_action_buttons(self.app)
        self.app.begin_taskbar_print_run = Mock()
        self.app.set_taskbar_display_state = Mock()
        self.app.print_blocks = Mock()
        self.app.begin_history_run = Mock()
        self.app.record_history = Mock()
        self.app.set_resource_estimate_text = Mock()
        self.io_started = threading.Event()
        self.io_release = threading.Event()
        self.io_release.set()
        self.addCleanup(self.io_release.set)
        self.io_threads = []

        def validate(blocks, **kwargs):
            self.io_threads.append(threading.get_ident())
            self.io_started.set()
            if not self.io_release.wait(2):
                raise TimeoutError("Fake IO was not released")
            return PrintManagerApp.validate_print_blocks(self.app, blocks, **kwargs)

        self.app.validate_print_blocks = Mock(side_effect=validate)
        self.app.validate_print_jobs = lambda jobs: PrintManagerApp.validate_print_jobs(self.app, jobs)
        self.inventory = PrinterBindingInventory(tuple(
            PrinterQueueBinding(name, "IP_test", "192.0.2.1", "TCPMON.DLL", "Local")
            for name in ("FAKE", "FAKE - 세로", "FAKE - 가로", "FAKE - 구분지", "FAKE -A:ble", "Override")
        ))
        self.app.cached_printer_inventory.return_value = frozenset(binding.name for binding in self.inventory.queues)
        self.app.ensure_orientation_profiles_ready = lambda *args, **kwargs: PrintManagerApp.ensure_orientation_profiles_ready(self.app, *args, **kwargs)
        self.app.ensure_block_printers_ready = lambda *args, **kwargs: PrintManagerApp.ensure_block_printers_ready(self.app, *args, **kwargs)
        self.app.ensure_job_printers_ready = lambda *args, **kwargs: PrintManagerApp.ensure_job_printers_ready(self.app, *args, **kwargs)

        def binding_inventory():
            self.io_threads.append(threading.get_ident())
            self.assertNotEqual(owner, threading.get_ident(), "Binding inventory blocked the UI")
            return self.inventory

        self.binding_reader = self.enterContext(patch(
            "unit_print_manager.read_printer_binding_inventory", side_effect=binding_inventory,
        ))
        self.confirm = self.enterContext(patch("unit_print_manager.messagebox.askyesno", return_value=True))
        self.warning = self.enterContext(patch("unit_print_manager.messagebox.showwarning"))
        self.info = self.enterContext(patch("unit_print_manager.messagebox.showinfo"))
        for name in ("sumatra_enqueue_pdfs", "save_config", "load_printers", "run_powershell"):
            self.enterContext(patch("unit_print_manager." + name, side_effect=AssertionError(name)))
        self.addCleanup(self.close_worker)

    def close_worker(self):
        self.io_release.set()
        self.app.cancel_pending_print_preparation(restore=False)
        worker = getattr(self.app, "_print_preparation_worker", None)
        if worker is not None:
            worker.close()
            if worker._thread is not None:
                worker._thread.join(timeout=3)

    def finish(self):
        self.app.root.pump_until(lambda: getattr(self.app, "_print_preparation_request", None) is None)

    def test_slow_preflight_returns_immediately_and_ui_heartbeat_continues(self):
        self.io_release.clear()
        beats = []

        def heartbeat():
            beats.append(time.monotonic())
            self.app.root.after(5, heartbeat)

        self.app.root.after(0, heartbeat)
        started = time.perf_counter()
        self.app.start_print_thread()
        self.assertLess(time.perf_counter() - started, 0.5)
        self.assertTrue(self.io_started.wait(1))
        self.assertEqual("준비 중", self.app.start_print_button.cget("text"))
        self.assertIn("disabled", self.app.start_print_button.state())
        self.assertEqual("normal", self.app.cancel_print_button.cget("state"))
        self.app.root.pump_until(lambda: len(beats) >= 8)
        self.confirm.assert_not_called()
        self.app.start_print_thread()
        self.assertEqual(1, self.app.validate_print_blocks.call_count)
        self.io_release.set()
        self.finish()
        self.app.root.pump_until(lambda: self.app.print_blocks.called)
        self.assertTrue(self.app.is_printing)
        self.assertNotIn(threading.get_ident(), self.io_threads)
        self.binding_reader.assert_called_once()
        self.assertEqual(id(self.block), self.app.print_pending_jobs[0].block_uid)

    def test_waiting_for_forecast_does_not_block_ui(self):
        future = Future()
        self.app.resource_estimate_future = future
        self.app.resource_estimate_revision = 1
        self.app.resource_estimate_request_key = ("forecast",)
        self.app.start_print_thread()
        self.app.root.pump_until(lambda: self.binding_reader.called)
        self.confirm.assert_not_called()
        marker = []
        self.app.root.after(0, lambda: marker.append(True))
        self.app.root.pump_until(lambda: bool(marker))
        future.set_result(SimpleNamespace(display_text="ready", tone="secondary", detail_text="forecast"))
        self.finish()
        self.app.set_resource_estimate_text.assert_any_call("ready", "secondary")
        self.assertIn("예상 완료 시간 계산 중", self.app.set_resource_estimate_text.call_args.args[0])

    def test_changed_block_selection_or_options_aborts_before_confirmation(self):
        mutations = (
            lambda: setattr(self.block, "copies", 3),
            lambda: self.app.selected_block_indices.clear(),
            lambda: setattr(self, "current_options", replace(self.options, printer_name="changed")),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.io_release.clear()
                self.io_started.clear()
                self.app.start_print_thread()
                self.assertTrue(self.io_started.wait(1))
                mutation()
                self.io_release.set()
                self.finish()
                self.confirm.assert_not_called()
                self.app.begin_history_run.assert_not_called()
                self.app.print_blocks.assert_not_called()
                self.block.copies = 2
                self.app.selected_block_indices = {0}
                self.current_options = self.options

    def test_cancel_or_close_discards_late_results(self):
        for closing in (False, True):
            with self.subTest(closing=closing):
                self.io_release.clear()
                self.io_started.clear()
                self.app.start_print_thread()
                self.assertTrue(self.io_started.wait(1))
                request = self.app._print_preparation_request
                if closing:
                    self.app.is_closing = True
                    self.app.close_pdf_work_services()
                else:
                    self.app.cancel_current_print()
                self.assertTrue(request.cancel.is_set())
                self.assertIsNone(self.app._print_preparation_request)
                self.io_release.set()
                worker = self.app._print_preparation_worker
                self.app.root.pump_until(lambda: worker._active is None)
                self.assertTrue(self.app._print_preparation_results.empty())
                self.app.poll_print_preparation_results()
                self.confirm.assert_not_called()
                self.app.print_blocks.assert_not_called()
                self.app.is_closing = False
                while not self.app._print_preparation_results.empty():
                    self.app._print_preparation_results.get_nowait()

    def test_repeated_cancel_and_start_keeps_one_worker_and_latest_pending(self):
        self.io_release.clear()
        self.app.start_print_thread()
        self.assertTrue(self.io_started.wait(1))
        worker = self.app._print_preparation_worker
        thread = worker._thread
        for _ in range(8):
            self.app.cancel_current_print()
            self.app.start_print_thread()
            self.assertIs(thread, worker._thread)
            self.assertIs(worker._pending[0], self.app._print_preparation_request.cancel)
            self.assertTrue(self.app._print_preparation_results.empty())
        self.assertEqual(1, self.app.validate_print_blocks.call_count)
        self.io_release.set()
        self.finish()
        self.assertEqual(2, self.app.validate_print_blocks.call_count)
        self.assertTrue(self.app._print_preparation_results.empty())

    def test_launch_failure_restores_pending_display_and_buttons(self):
        self.app._launch_print_preparation = Mock(side_effect=RuntimeError("cannot start worker"))
        self.app.start_print_thread()
        self.assertIsNone(self.app._print_preparation_request)
        self.assertEqual("idle", self.app.app_status_var.get())
        self.assertEqual("인쇄 시작", self.app.start_print_button.cget("text"))
        self.assertNotIn("disabled", self.app.start_print_button.state())
        self.warning.assert_called_once()
        self.app.print_blocks.assert_not_called()

    def test_pdf_change_during_binding_rejects_old_preflight(self):
        def binding_inventory():
            self.pdf.write_bytes(self.pdf.read_bytes() + b"changed during binding")
            return self.inventory

        self.binding_reader.side_effect = binding_inventory
        self.app.start_print_thread()
        self.finish()
        self.confirm.assert_not_called()
        self.app.begin_history_run.assert_not_called()
        self.app.print_blocks.assert_not_called()
        self.assertIn("PDF가 변경", self.warning.call_args.args[1])

    def test_missing_profile_validation_never_writes_tk_from_worker(self):
        self.app.cached_printer_inventory.return_value = frozenset()
        self.app.start_print_thread()
        self.finish()
        self.assertIn("전용 프린터 프로필", self.warning.call_args.args[1])
        self.app.print_blocks.assert_not_called()

    def test_confirmation_nested_event_loop_rechecks_queue_and_pdf(self):
        for mutation in (
            lambda: setattr(self.block, "copies", 3),
            lambda: self.pdf.write_bytes(self.pdf.read_bytes() + b"changed"),
        ):
            with self.subTest(mutation=mutation):
                self.confirm.side_effect = lambda *args: (mutation(), True)[1]
                self.app.start_print_thread()
                self.finish()
                self.app.begin_history_run.assert_not_called()
                self.app.print_blocks.assert_not_called()
                self.block.copies = 2

    def test_worker_error_restores_buttons_and_reports_on_ui(self):
        self.app.validate_print_blocks.side_effect = ValueError("bad PDF")
        self.app.start_print_thread()
        self.finish()
        self.warning.assert_called_once()
        self.assertIn("bad PDF", self.warning.call_args.args[1])
        self.assertEqual("인쇄 시작", self.app.start_print_button.cget("text"))
        self.assertNotIn("disabled", self.app.start_print_button.state())
        self.app.print_blocks.assert_not_called()

    def test_required_history_failure_does_not_commit_or_dispatch(self):
        self.app.begin_history_run.side_effect = HistoryWriteError(self.directory, OSError("disk full"))
        self.app.start_print_thread()
        self.finish()
        self.assertFalse(self.app.is_printing)
        self.assertEqual([], self.app.print_pending_jobs)
        self.app.print_blocks.assert_not_called()
        self.warning.assert_called_once()

    def test_config_failure_before_commit_preserves_idle_or_retry_state(self):
        for retry in (False, True):
            with self.subTest(retry=retry):
                remaining = self.app.build_print_jobs([self.block])[0]
                self.app.retry_batch = RetryBatch((remaining,)) if retry else None
                previous = self.app.retry_batch
                self.app.save_current_config.side_effect = OSError("cannot save config")
                self.app.start_print_thread()
                self.finish()
                self.assertFalse(self.app.is_printing)
                self.assertEqual(previous, self.app.retry_batch)
                self.assertEqual([], self.app.print_pending_jobs)
                self.app.print_blocks.assert_not_called()
                self.app.begin_history_run.assert_not_called()

    def test_dispatch_thread_failure_preserves_unsent_job_ids(self):
        class FailingThread:
            ident = None

            def __init__(self, **kwargs):
                pass

            def start(self):
                raise RuntimeError("cannot start dispatch")

        for retry in (False, True):
            with self.subTest(retry=retry):
                remaining = self.app.build_print_jobs([self.block])[0]
                self.app.retry_batch = RetryBatch((remaining,)) if retry else None
                previous = self.app.retry_batch
                patches = []
                captured_batches = []

                def confirm(*args):
                    captured_batches.append(self.app.retry_batch)
                    replacement = patch("unit_print_execution.threading.Thread", FailingThread)
                    replacement.start()
                    patches.append(replacement)
                    return True

                self.confirm.side_effect = confirm
                try:
                    self.app.start_print_thread()
                    self.finish()
                finally:
                    for replacement in patches:
                        replacement.stop()
                self.assertFalse(self.app.is_printing)
                self.assertIsNone(self.app.print_thread)
                self.assertIsNone(self.app._print_preparation_request)
                self.assertEqual([], self.app.print_pending_jobs)
                self.assertEqual(id(self.block), self.app.retry_batch.jobs[0].block_uid)
                if retry:
                    self.assertIs(captured_batches[0], self.app.retry_batch)
                    self.assertEqual(remaining.job_id, self.app.retry_batch.jobs[0].job_id)
                self.app.print_blocks.assert_not_called()
                self.assertNotIn("disabled", self.app.start_print_button.state())
                self.assertEqual("인쇄 시작", self.app.start_print_button.cget("text"))

    def test_retry_preserves_only_remaining_jobs_and_reads_binding_fresh(self):
        remaining = self.app.build_print_jobs([self.block])[0]
        self.app.retry_batch = RetryBatch((remaining,))
        self.app.retry_remaining_jobs()
        self.finish()
        self.app.root.pump_until(lambda: self.app.print_blocks.called)
        self.binding_reader.assert_called_once()
        self.assertEqual([remaining], self.app.print_pending_jobs)
        self.assertEqual(remaining.job_id, self.app.print_pending_jobs[0].job_id)
        self.app.begin_history_run.assert_called_once()
        self.assertTrue(self.app.begin_history_run.call_args.kwargs["retry"])

    def test_retry_repairs_name_groups_on_worker_and_preserves_body_job_id(self):
        self.block.name_separator_label = "Student A"
        body = self.app.build_print_jobs([self.block])[0]
        self.app.retry_batch = RetryBatch((body,), include_name_separator_pages=True)
        self.app.sync_retry_queue = Mock(side_effect=AssertionError("Synchronous retry PDF work"))
        owner = threading.get_ident()

        def name_pdf(*args):
            self.assertNotEqual(owner, threading.get_ident(), "Name PDF blocked UI")
            return self.pdf

        with patch("unit_print_manager.materialize_name_separator_pdf", side_effect=name_pdf):
            self.app.start_print_thread()
            self.finish()
        self.assertEqual(2, len(self.app.print_pending_jobs))
        self.assertTrue(self.app.print_pending_jobs[0].is_name_separator)
        self.assertEqual(body.job_id, self.app.print_pending_jobs[1].job_id)
        self.assertTrue(all(job.block_uid == id(self.block) for job in self.app.print_pending_jobs))
        self.app.sync_retry_queue.assert_not_called()

    def prepare_append(self):
        self.app.print_run_options = replace(
            self.options, device_monitor_base_url="http://192.0.2.1",
            device_verified_printers=frozenset({"FAKE", "FAKE - 세로"}),
        )
        self.app.is_printing = True
        self.app.print_scheduled_block_ids = {id(self.block)}
        self.app.print_pending_jobs = self.app.build_print_jobs([self.block])
        self.app.print_next_block_index = 2
        added = replace(self.block, files=list(self.block.files), printer_override="Override")
        self.app.blocks.append(added)
        return added

    def test_append_reads_binding_fresh_and_commits_original_block_identity(self):
        added = self.prepare_append()
        self.app.append_new_blocks_to_active_print()
        self.finish()
        self.binding_reader.assert_called_once()
        self.assertEqual(2, len(self.app.print_pending_jobs))
        self.assertEqual(id(added), self.app.print_pending_jobs[-1].block_uid)
        self.assertIn("Override", self.app.print_run_options.device_verified_printers)
        self.app.print_blocks.assert_not_called()
        self.app.record_history.assert_any_call(
            "append_requested", required=True,
            blocks=[self.app.print_pending_jobs[-1].history_block],
            job_ids=[self.app.print_pending_jobs[-1].job_id],
        )

    def test_append_rejects_finished_run_and_required_history_failure(self):
        self.prepare_append()
        previous = list(self.app.print_pending_jobs)
        self.confirm.side_effect = lambda *args: (setattr(self.app, "print_accepting_appends", False), True)[1]
        self.app.append_new_blocks_to_active_print()
        self.finish()
        self.assertEqual(previous, self.app.print_pending_jobs)
        self.app.record_history.assert_not_called()
        self.app.print_accepting_appends = True
        self.confirm.side_effect = None
        self.app.record_history.side_effect = HistoryWriteError(self.directory, OSError("disk full"))
        self.app.append_new_blocks_to_active_print()
        self.finish()
        self.assertEqual(previous, self.app.print_pending_jobs)
        self.warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
