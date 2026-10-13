from __future__ import annotations

import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from unit_print_manager import PrintManagerApp
from unit_print_pdf_printing import (
    PdfOrientation,
    PdfPreflightPage,
    PdfPreflightReport,
    PdfPreflightResult,
)


class Root:
    def __init__(self):
        self.callbacks = {}
        self.next_id = 0

    def after(self, _delay, callback):
        self.next_id += 1
        task_id = str(self.next_id)
        self.callbacks[task_id] = callback
        return task_id

    def flush(self):
        callbacks = tuple(self.callbacks.values())
        self.callbacks.clear()
        for callback in callbacks:
            callback()


def make_app():
    app = PrintManagerApp.__new__(PrintManagerApp)
    app.root = Root()
    app.is_closing = False
    app.preview_preflight_lock = threading.Lock()
    app.preview_preflight_pending_paths = set()
    app.preview_preflight_active = False
    app.preview_preflight_completed = None
    app.preview_preflight_poll_after_id = None
    app.pdf_preflight_by_path = {}
    app.pdf_preflight_signature_by_path = {}
    app.pdf_preflight_signature = Mock(return_value=(100, 200))
    app.update_preview = Mock()
    app.log = Mock()
    return app


def report(path):
    result = PdfPreflightResult(
        path=path,
        pages=(PdfPreflightPage(1, PdfOrientation.PORTRAIT),),
        findings=(),
    )
    return PdfPreflightReport((result,))


class PreviewPreflightTests(unittest.TestCase):
    def setUp(self):
        self.worker = patch("unit_print_manager.threading.Thread").start()
        self.inspect = patch("unit_print_manager.preflight_pdfs").start()
        self.addCleanup(patch.stopall)
        # These preview tests must never launch a process, print, or write history.
        for name in ("sumatra_enqueue_pdfs", "run_powershell"):
            patch("unit_print_manager." + name, side_effect=AssertionError(name)).start()

    def run_worker(self):
        self.worker.call_args.kwargs["target"]()

    def test_unexpected_worker_failure_releases_poll_and_does_not_auto_retry(self):
        app = make_app()
        path = Path("first.pdf")
        self.inspect.side_effect = RuntimeError("unexpected parser failure")
        app.schedule_preview_preflight({path})
        self.run_worker()

        # Worker only publishes completion; UI work belongs to the polling turn.
        app.log.assert_not_called()
        app.update_preview.assert_not_called()
        self.assertTrue(app.preview_preflight_active)
        app.root.flush()

        self.assertFalse(app.preview_preflight_active)
        self.assertIsNone(app.preview_preflight_completed)
        self.assertIsNone(app.preview_preflight_poll_after_id)
        self.assertFalse(app.root.callbacks)
        self.assertFalse(app.pdf_preflight_by_path)
        self.assertEqual(1, app.log.call_count)
        app.update_preview.assert_not_called()
        self.assertEqual(1, self.worker.call_count)
        app.poll_preview_preflight_result()
        self.assertEqual(1, app.log.call_count)
        self.assertFalse(app.root.callbacks)

    def test_same_path_can_be_requested_again_after_failure(self):
        app = make_app()
        path = Path("first.pdf")
        success = report(path)
        self.inspect.side_effect = [RuntimeError("unexpected"), success]

        app.schedule_preview_preflight({path})
        self.run_worker()
        app.root.flush()
        app.schedule_preview_preflight({path})
        self.run_worker()
        app.root.flush()

        self.assertEqual(success.results[0], app.cached_preflight_result(path))
        self.assertEqual(2, self.worker.call_count)
        self.assertEqual(1, app.log.call_count)
        app.update_preview.assert_called_once_with()
        self.assertFalse(app.preview_preflight_active)
        self.assertFalse(app.root.callbacks)

    def test_pending_next_batch_continues_after_unexpected_failure(self):
        app = make_app()
        first, second = Path("first.pdf"), Path("second.pdf")
        self.inspect.side_effect = [RuntimeError("unexpected"), report(second)]
        app.schedule_preview_preflight({first})
        app.schedule_preview_preflight({second})
        self.assertEqual(1, self.worker.call_count)

        self.run_worker()
        app.root.flush()
        self.assertEqual(2, self.worker.call_count)
        self.assertTrue(app.preview_preflight_active)
        self.run_worker()
        app.root.flush()

        self.assertNotIn(first, app.pdf_preflight_by_path)
        self.assertIn(second, app.pdf_preflight_by_path)
        self.assertFalse(app.preview_preflight_active)
        self.assertFalse(app.root.callbacks)
        app.update_preview.assert_called_once_with()

    def test_worker_start_failure_is_delivered_then_retryable(self):
        app = make_app()
        path = Path("first.pdf")
        self.worker.return_value.start.side_effect = RuntimeError("cannot start worker")
        app.schedule_preview_preflight({path})
        self.inspect.assert_not_called()
        app.root.flush()

        self.assertFalse(app.preview_preflight_active)
        self.assertFalse(app.root.callbacks)
        self.worker.return_value.start.side_effect = None
        self.inspect.return_value = report(path)
        app.schedule_preview_preflight({path})
        self.run_worker()
        app.root.flush()
        self.assertIn(path, app.pdf_preflight_by_path)

    def test_signature_failure_also_releases_batch_state(self):
        app = make_app()
        app.preview_preflight_pending_paths.add(Path("first.pdf"))
        app.pdf_preflight_signature.side_effect = RuntimeError("unexpected filesystem error")
        app.start_preview_preflight_batch()
        self.run_worker()
        app.root.flush()
        self.assertFalse(app.preview_preflight_active)
        self.assertIsNone(app.preview_preflight_completed)
        self.assertFalse(app.root.callbacks)
        self.assertEqual(1, self.worker.call_count)
        self.inspect.assert_not_called()
        app.log.assert_called_once()

    def test_closed_app_consumes_completion_without_ui_updates_or_next_batch(self):
        app = make_app()
        path = Path("first.pdf")
        self.inspect.side_effect = RuntimeError("unexpected")
        app.schedule_preview_preflight({path})
        self.run_worker()
        app.is_closing = True
        app.root.flush()
        self.assertFalse(app.preview_preflight_active)
        self.assertIsNone(app.preview_preflight_completed)
        self.assertFalse(app.root.callbacks)
        app.log.assert_not_called()
        app.update_preview.assert_not_called()


if __name__ == "__main__":
    unittest.main()
