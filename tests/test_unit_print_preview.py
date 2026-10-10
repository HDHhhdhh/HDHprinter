from __future__ import annotations

import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from unit_print_domain import AUTO_OMR_TEST_SOURCES, ExamPdf, PrintBlock
from unit_print_pdf_printing import PdfOrientation, PdfPreflightPage, PdfPreflightResult
from unit_print_preview import PreviewRequest, PreviewWorker, build_preview


def request(blocks=(), **values):
    return PreviewRequest(tuple(blocks), "검증", False, False, None, {}, {}, **values)


class PreviewTests(unittest.TestCase):
    def test_bundle_file_order_and_independent_copy_counts_match_original_preview(self):
        block = PrintBlock("검증", "", "", 1, 2, 2,
                           [ExamPdf(1, "1", Path("1.pdf")), ExamPdf(2, "2", Path("2.pdf"))],
                           source="custom", bundled=True, printer_override="전용", pages_per_sheet=4)
        with patch.object(Path, "exists", return_value=True):
            result = build_preview(request([block]), lambda: False)
        self.assertEqual(
            "[블록 1] 새 PDF / PDF 2개 / 2부 / 묶음 / 2x2\n"
            "  - 묶음 001/002\n"
            "    001. 1.pdf x 1부  [A4 가로 2x2]  [전용 -> 전용]\n"
            "    002. 2.pdf x 1부  [A4 가로 2x2]  [전용 -> 전용]\n"
            "  - 묶음 002/002\n"
            "    003. 1.pdf x 1부  [A4 가로 2x2]  [전용 -> 전용]\n"
            "    004. 2.pdf x 1부  [A4 가로 2x2]  [전용 -> 전용]\n\n",
            "".join(result.chunks),
        )

    def test_name_groups_automatic_omr_and_separator_keep_original_sequence(self):
        body, omr = Path("body.pdf"), Path("OMR.pdf")
        block = PrintBlock("수학", "1", "1", 1, 1, 1, [ExamPdf(1, "본문", body)],
                           name_separator_label="검증학생", source=next(iter(AUTO_OMR_TEST_SOURCES)), printer_override="전용")
        captured = replace(request([block, replace(block)]), name_separator=True, separator=True,
                           automatic_omr=ExamPdf(0, "OMR", omr, printer_override="가로"))
        with patch.object(Path, "exists", return_value=True):
            result = build_preview(captured, lambda: False)
        text = "".join(result.chunks)
        self.assertEqual(1, text.count("[이름 구분지]"))
        self.assertEqual(2, text.count("[자동 OMR]"))
        self.assertIn("001. [이름 구분지] 검증학생", text)
        self.assertIn("002. [자동 OMR] OMR.pdf", text)
        self.assertIn("003. body.pdf", text)
        self.assertIn("004. [구분지]", text)
        self.assertIn("005. [자동 OMR]", text)
        self.assertIn("007. [구분지]", text)

    def test_cached_orientation_is_used_only_for_matching_file_signature(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.pdf"
            path.write_bytes(b"original")
            block = PrintBlock("검증", "", "", 1, 1, 1, [ExamPdf(1, "본문", path)])
            stat = path.stat()
            cached = PdfPreflightResult(path, (PdfPreflightPage(1, PdfOrientation.PORTRAIT),), ())
            captured = replace(request([block]), cached_results={path: cached},
                               cached_signatures={path: (stat.st_size, stat.st_mtime_ns)})
            result = build_preview(captured, lambda: False)
            self.assertIn("[세로 ->", "".join(result.chunks))
            self.assertFalse(result.unresolved_paths)
            path.write_bytes(b"changed to longer contents")
            result = build_preview(captured, lambda: False)
            self.assertIn("[자동 감지 확인 중]", "".join(result.chunks))
            self.assertEqual(frozenset({path}), result.unresolved_paths)

    def test_chunking_never_drops_or_reorders_lines(self):
        block = PrintBlock("검증", "", "", 1, 1, 200, [ExamPdf(1, "본문", Path("body.pdf"))], bundled=True)
        with patch.object(Path, "exists", return_value=False), patch("unit_print_preview.PREVIEW_CHUNK_CHARS", 200):
            result = build_preview(request([block]), lambda: False)
        self.assertGreater(len(result.chunks), 2)
        text = "".join(result.chunks)
        self.assertEqual(200, text.count("[파일 없음]"))
        self.assertIn("200. body.pdf", text)
        self.assertLess(text.index("001. body.pdf"), text.index("200. body.pdf"))

    def test_cancelled_request_produces_no_publishable_partial_content(self):
        block = PrintBlock("검증", "", "", 1, 1, 1, [ExamPdf(1, "본문", Path("body.pdf"))])
        with patch.object(Path, "exists") as exists:
            self.assertIsNone(build_preview(request([block]), lambda: True))
        exists.assert_not_called()

    def test_worker_start_failure_is_published_and_next_request_can_retry(self):
        worker = PreviewWorker()
        with patch("unit_print_preview.threading.Thread") as thread:
            thread.return_value.start.side_effect = RuntimeError("start failed")
            worker.submit(1, request())
        revision, error = worker.results.get(timeout=2)
        self.assertEqual(1, revision)
        self.assertIsInstance(error, RuntimeError)
        worker.submit(2, request())
        revision, result = worker.results.get(timeout=2)
        self.assertEqual(2, revision)
        self.assertIn("작업 큐가 비어 있습니다", result.chunks[0])

    def test_pending_requests_coalesce_after_blocked_worker(self):
        worker = PreviewWorker()
        entered, release = threading.Event(), threading.Event()
        calls = []

        def delayed(captured, cancelled):
            calls.append(captured.printer)
            if captured.printer == "old":
                entered.set()
                self.assertTrue(release.wait(2))
            return None if cancelled() else build_preview(captured, cancelled)

        with patch("unit_print_preview.build_preview", delayed):
            worker.submit(1, replace(request(), printer="old"))
            self.assertTrue(entered.wait(2))
            worker.submit(2, replace(request(), printer="discarded"))
            worker.submit(3, replace(request(), printer="latest"))
            release.set()
            revision, _result = worker.results.get(timeout=2)
        self.assertEqual(3, revision)
        self.assertEqual(["old", "latest"], calls)


if __name__ == "__main__":
    unittest.main()
