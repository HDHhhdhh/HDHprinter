from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter

from unit_print_manager import sumatra_enqueue_pdfs


def write_pdf(path: Path, *, width: float, height: float) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=width, height=height)
    with path.open("wb") as pdf_file:
        writer.write(pdf_file)


def print_settings(command: list[str]) -> set[str]:
    settings_index = command.index("-print-settings") + 1
    return set(command[settings_index].split(","))


class SumatraPrintSettingsTest(unittest.TestCase):
    def test_missing_pdf_produces_no_sumatra_command(self) -> None:
        # Given: a path that does not exist.
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "missing.pdf"

            # When: Sumatra dispatch is requested for the missing PDF.
            with patch("unit_print_manager.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
                call_count = sumatra_enqueue_pdfs(
                    "SumatraPDF.exe",
                    [pdf_path],
                    "Test Printer",
                    copies=1,
                    duplex=True,
                )

            # Then: no command is submitted and the reported batch count is zero.
            self.assertEqual(0, call_count)
            run.assert_not_called()

    def test_encrypted_pdf_produces_no_sumatra_command(self) -> None:
        # Given: a PDF encrypted with a user password.
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "encrypted.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=595, height=842)
            writer.encrypt("secret")
            with pdf_path.open("wb") as pdf_file:
                writer.write(pdf_file)

            # When: Sumatra dispatch is requested for the encrypted PDF.
            with patch("unit_print_manager.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
                call_count = sumatra_enqueue_pdfs(
                    "SumatraPDF.exe",
                    [pdf_path],
                    "Test Printer",
                    copies=1,
                    duplex=True,
                )

            # Then: no command is submitted and the reported batch count is zero.
            self.assertEqual(0, call_count)
            run.assert_not_called()

    def test_zero_page_pdf_produces_no_sumatra_command(self) -> None:
        # Given: a valid PDF containing no pages.
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "zero-pages.pdf"
            writer = PdfWriter()
            with pdf_path.open("wb") as pdf_file:
                writer.write(pdf_file)

            # When: Sumatra dispatch is requested for the zero-page PDF.
            with patch("unit_print_manager.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
                call_count = sumatra_enqueue_pdfs(
                    "SumatraPDF.exe",
                    [pdf_path],
                    "Test Printer",
                    copies=1,
                    duplex=True,
                )

            # Then: no command is submitted and the reported batch count is zero.
            self.assertEqual(0, call_count)
            run.assert_not_called()

    def test_landscape_pdf_uses_short_edge_duplex_without_content_rotation(self) -> None:
        # Given: a landscape PDF and duplex printing.
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "landscape.pdf"
            write_pdf(pdf_path, width=842, height=595)

            def capture_print_command(
                command: list[str],
                *,
                capture_output: bool,
                text: bool,
                timeout: int,
                creationflags: int,
            ) -> subprocess.CompletedProcess[str]:
                del capture_output, text, timeout, creationflags
                return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

            # When: Sumatra dispatch is built for the PDF.
            with patch("unit_print_manager.subprocess.run") as run:
                run.side_effect = capture_print_command

                sumatra_enqueue_pdfs(
                    "SumatraPDF.exe",
                    [pdf_path],
                    "Test Printer",
                    copies=2,
                    duplex=True,
                )

            # Then: landscape pages flip on the short edge and keep their content orientation.
            command = run.call_args_list[0].args[0]
            settings = print_settings(command)
            self.assertEqual(pdf_path, Path(command[-1]))
            self.assertIn("2x", settings)
            self.assertIn("duplexshort", settings)
            self.assertIn("fit", settings)
            self.assertIn("monochrome", settings)
            self.assertIn("paper=A4", settings)
            self.assertNotIn("landscape", settings)

    def test_portrait_pdf_uses_standard_sumatra_settings_with_simplex(self) -> None:
        # Given: a portrait PDF and simplex printing.
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "portrait.pdf"
            write_pdf(pdf_path, width=595, height=842)

            # When: Sumatra dispatch is built for the PDF.
            with patch("unit_print_manager.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

                sumatra_enqueue_pdfs(
                    "SumatraPDF.exe",
                    [pdf_path],
                    "Test Printer",
                    copies=1,
                    duplex=False,
                )

            # Then: its command requests every standard simplex setting.
            command = run.call_args_list[0].args[0]
            settings = print_settings(command)
            self.assertIn("simplex", settings)
            self.assertIn("fit", settings)
            self.assertIn("monochrome", settings)
            self.assertIn("paper=A4", settings)
            self.assertNotIn("landscape", settings)

    def test_portrait_pdf_uses_long_edge_duplex(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "portrait.pdf"
            write_pdf(pdf_path, width=595, height=842)

            with patch("unit_print_manager.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
                sumatra_enqueue_pdfs(
                    "SumatraPDF.exe",
                    [pdf_path],
                    "Test Printer",
                    copies=1,
                    duplex=True,
                )

            settings = print_settings(run.call_args_list[0].args[0])
            self.assertIn("duplexlong", settings)
            self.assertNotIn("duplexshort", settings)

    def test_portrait_and_landscape_pdfs_split_by_duplex_binding_and_keep_order(self) -> None:
        # Given: separate valid portrait and landscape PDFs.
        with tempfile.TemporaryDirectory() as temp_dir:
            portrait_path = Path(temp_dir) / "portrait.pdf"
            landscape_path = Path(temp_dir) / "landscape.pdf"
            write_pdf(portrait_path, width=595, height=842)
            write_pdf(landscape_path, width=842, height=595)

            # When: both PDFs are sent to Sumatra in one dispatch request.
            with patch("unit_print_manager.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

                sumatra_enqueue_pdfs(
                    "SumatraPDF.exe",
                    [portrait_path, landscape_path],
                    "Test Printer",
                    copies=1,
                    duplex=True,
                )

            # Then: each orientation receives its correct left-hand binding without reordering.
            self.assertEqual(2, run.call_count)
            portrait_command = run.call_args_list[0].args[0]
            landscape_command = run.call_args_list[1].args[0]
            self.assertEqual(str(portrait_path), portrait_command[-1])
            self.assertEqual(str(landscape_path), landscape_command[-1])
            self.assertIn("duplexlong", print_settings(portrait_command))
            self.assertIn("duplexshort", print_settings(landscape_command))
            for command in (portrait_command, landscape_command):
                settings = print_settings(command)
                self.assertIn("fit", settings)
                self.assertIn("monochrome", settings)
                self.assertIn("paper=A4", settings)
                self.assertNotIn("landscape", settings)

    def test_mixed_orientation_pdf_produces_no_sumatra_command(self) -> None:
        # Given: a PDF with both portrait and landscape pages.
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "mixed.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=595, height=842)
            writer.add_blank_page(width=842, height=595)
            with pdf_path.open("wb") as pdf_file:
                writer.write(pdf_file)

            # When: Sumatra dispatch is requested for the mixed PDF.
            with patch("unit_print_manager.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
                call_count = sumatra_enqueue_pdfs(
                    "SumatraPDF.exe",
                    [pdf_path],
                    "Test Printer",
                    copies=1,
                    duplex=True,
                )

            # Then: no command is submitted and the reported batch count is zero.
            self.assertEqual(0, call_count)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
