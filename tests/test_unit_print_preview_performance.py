from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from unit_print_manager import ExamPdf, PrintBlock, PrintManagerApp


class Variable:
    def get(self):
        return False


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
        app.name_separator_page_var = Variable()
        app.separator_page_var = Variable()
        app.preview_route_text = Mock(side_effect=lambda _item, printer, _unresolved: f" [{printer}]")
        app.schedule_preview_preflight = Mock()
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

            self.assertEqual([path], checks)
            self.assertEqual(2, app.preview_route_text.call_count)
            self.assertEqual(1, app.preview_text.inserts)
            self.assertEqual(20, app.preview_text.text.count("x 1부 [프린터A]"))
            self.assertEqual(20, app.preview_text.text.count("x 1부 [프린터B]"))
            self.assertIn("040. 자료.pdf", app.preview_text.text)
            self.assertNotIn("[파일 없음]", app.preview_text.text)

    def test_each_refresh_rechecks_a_deleted_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "자료.pdf"
            path.touch()
            app = self.make_app(path)
            app.update_preview()
            path.unlink()
            app.update_preview()
            self.assertEqual(40, app.preview_text.text.count("[파일 없음]"))
            self.assertEqual(4, app.preview_route_text.call_count)

    def test_empty_queue_keeps_operator_message(self):
        app = self.make_app(Path("unused.pdf"))
        app.blocks = []
        app.update_preview()
        self.assertIn("작업 큐가 비어 있습니다", app.preview_text.text)
        app.schedule_preview_preflight.assert_not_called()


if __name__ == "__main__":
    unittest.main()
