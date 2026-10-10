from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from unit_print_manager import ExamPdf, PrintBlock, PrintManagerApp


class Variable:
    def __init__(self, value=False):
        self.value = value

    def get(self):
        return self.value


class Root:
    def __init__(self):
        self.callbacks = {}
        self.next_id = 0

    def after(self, _delay, callback):
        self.next_id += 1
        self.callbacks[self.next_id] = callback
        return self.next_id

    def after_cancel(self, identifier):
        self.callbacks.pop(identifier, None)

    def pump(self):
        deadline = time.monotonic() + 3
        while self.callbacks:
            callbacks = tuple(self.callbacks.values())
            self.callbacks.clear()
            for callback in callbacks:
                callback()
            if time.monotonic() > deadline:
                raise AssertionError("preview did not finish")
            time.sleep(0.001)


class TextBuffer:
    def __init__(self):
        self.text = ""
        self.inserts = 0

    def delete(self, *_args):
        self.text = ""

    def insert(self, _position, text):
        self.inserts += 1
        self.text += text


class PreviewPerformanceTests(unittest.TestCase):
    def make_app(self, path):
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.preview_text = TextBuffer()
        app.root = Root()
        app.printer_var = Variable("프린터")
        app.is_closing = False
        app.pdf_preflight_by_path = {}
        app.pdf_preflight_signature_by_path = {}
        app.name_separator_page_var = Variable()
        app.separator_page_var = Variable()
        app.schedule_preview_preflight = Mock()
        app.schedule_resource_estimate = Mock()
        app.log = Mock()
        app.blocks = [
            PrintBlock(subject="검증", level="", round_name="", start=1, end=1,
                       files=[ExamPdf(1, "자료", path)], copies=20, bundled=True,
                       source="custom", printer_override=printer)
            for printer in ("프린터A", "프린터B")
        ]
        return app

    def test_bundled_copies_reuse_file_check_but_keep_distinct_printer_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "자료.pdf"
            path.touch()
            app = self.make_app(path)
            checks = []
            original_exists = Path.exists

            def exists(item):
                checks.append(item)
                return original_exists(item)

            with patch.object(Path, "exists", exists):
                app.update_preview()
                self.assertFalse(checks)
                app.root.pump()

            self.assertEqual([path], checks)
            self.assertEqual(20, app.preview_text.text.count("x 1부  [전용 -> 프린터A]"))
            self.assertEqual(20, app.preview_text.text.count("x 1부  [전용 -> 프린터B]"))
            self.assertIn("040. 자료.pdf", app.preview_text.text)
            self.assertNotIn("[파일 없음]", app.preview_text.text)

    def test_each_refresh_rechecks_a_deleted_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "자료.pdf"
            path.touch()
            app = self.make_app(path)
            app.update_preview()
            app.root.pump()
            path.unlink()
            app.update_preview()
            app.root.pump()
            self.assertEqual(40, app.preview_text.text.count("[파일 없음]"))

    def test_empty_queue_keeps_operator_message(self):
        app = self.make_app(Path("unused.pdf"))
        app.blocks = []
        app.update_preview()
        app.root.pump()
        self.assertIn("작업 큐가 비어 있습니다", app.preview_text.text)
        app.schedule_preview_preflight.assert_not_called()

    def test_slow_file_check_runs_off_ui_and_newest_edit_wins(self):
        app = self.make_app(Path("first.pdf"))
        app.blocks = app.blocks[:1]
        entered = threading.Event()
        release = threading.Event()
        caller = threading.get_ident()
        worker_threads = []

        def exists(_path):
            worker_threads.append(threading.get_ident())
            entered.set()
            self.assertTrue(release.wait(2))
            return True

        with patch.object(Path, "exists", exists):
            app.update_preview()
            callbacks = tuple(app.root.callbacks.values())
            app.root.callbacks.clear()
            for callback in callbacks:
                callback()
            self.assertTrue(entered.wait(2))
            app.blocks[0].files = [ExamPdf(2, "새 자료", Path("latest.pdf"))]
            app.update_preview()
            release.set()
            app.root.pump()
        self.assertNotIn(caller, worker_threads)
        self.assertIn("latest.pdf", app.preview_text.text)
        self.assertNotIn("first.pdf", app.preview_text.text)
        app.log.assert_not_called()

    def test_requests_coalesce_and_worker_failure_is_explicitly_retryable(self):
        app = self.make_app(Path("first.pdf"))
        app.update_preview()
        app.update_preview()
        self.assertEqual(1, len(app.root.callbacks))
        app.blocks[0].copies = 2
        with patch("unit_print_preview.build_preview", side_effect=RuntimeError("failure")):
            app.root.pump()
        self.assertIsNone(app.preview_render_after_id)
        self.assertIn("갱신하지 못했습니다", app.preview_text.text)
        self.assertEqual(1, app.log.call_count)
        app.update_preview()
        app.root.pump()
        self.assertIn("묶음 002/002", app.preview_text.text)

    def test_captured_preview_values_are_detached_from_in_place_edits(self):
        app = self.make_app(Path("original.pdf"))
        app.blocks = app.blocks[:1]
        entered = threading.Event()
        release = threading.Event()
        from unit_print_preview import build_preview

        def delayed(request, cancelled):
            entered.set()
            self.assertTrue(release.wait(2))
            return build_preview(request, cancelled)

        with patch("unit_print_preview.build_preview", delayed):
            app.update_preview()
            callbacks = tuple(app.root.callbacks.values())
            app.root.callbacks.clear()
            for callback in callbacks:
                callback()
            self.assertTrue(entered.wait(2))
            app.blocks[0].copies = 3
            app.blocks[0].files.append(ExamPdf(2, "later", Path("later.pdf")))
            release.set()
            app.root.pump()
        self.assertIn("묶음 020/020", app.preview_text.text)
        self.assertNotIn("later.pdf", app.preview_text.text)

    def test_close_discards_pending_preview(self):
        app = self.make_app(Path("first.pdf"))
        app.update_preview()
        app.is_closing = True
        app.cancel_preview_render()
        app.root.pump()
        self.assertEqual("", app.preview_text.text)
        self.assertFalse(app.root.callbacks)


if __name__ == "__main__":
    unittest.main()
