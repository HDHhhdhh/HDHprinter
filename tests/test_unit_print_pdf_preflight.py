from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter

import unit_print_pdf_printing as printing
from unit_print_pdf_printing import pdf_has_landscape_page, sumatra_pdf_batches


class PdfPreflightCharacterizationTest(unittest.TestCase):
    def test_pdf_has_landscape_page_when_portrait_page_is_rotated_clockwise(self) -> None:
        # Given: a portrait page whose effective display rotation is landscape.
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "rotated.pdf"
            writer = PdfWriter()
            page = writer.add_blank_page(width=595, height=842)
            page.rotate(90)
            with pdf_path.open("wb") as pdf_file:
                writer.write(pdf_file)

            # When: the existing orientation helper inspects the page.
            actual = pdf_has_landscape_page(pdf_path)

            # Then: it treats the page as landscape after applying /Rotate.
            self.assertTrue(actual)


class PdfPreflightTest(unittest.TestCase):
    def test_preflight_pdf_classifies_rotated_page_by_effective_orientation(self) -> None:
        # Given: a portrait media box rotated into landscape display orientation.
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "rotated.pdf"
            writer = PdfWriter()
            page = writer.add_blank_page(width=595, height=842)
            page.rotate(90)
            with pdf_path.open("wb") as pdf_file:
                writer.write(pdf_file)

            # When: the PDF is preflighted.
            result = printing.preflight_pdf(pdf_path)

            # Then: its sole page is classified as landscape and printable.
            self.assertTrue(result.is_printable)
            self.assertEqual((printing.PdfOrientation.LANDSCAPE,), tuple(page.orientation for page in result.pages))


class PdfPreflightFailureTest(unittest.TestCase):
    def test_preflight_pdf_reports_missing_file(self) -> None:
        # Given: a path that does not exist.
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "missing.pdf"

            # When: the path is preflighted.
            result = printing.preflight_pdf(pdf_path)

            # Then: the missing document is a blocking file-level finding.
            self.assertFalse(result.is_printable)
            self.assertEqual(
                ((pdf_path, None, printing.PdfPreflightFindingCode.MISSING),),
                tuple((finding.path, finding.page_number, finding.code) for finding in result.findings),
            )

    def test_preflight_pdf_reports_unreadable_file(self) -> None:
        # Given: a fresh malformed PDF fixture.
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "corrupt.pdf"
            pdf_path.write_bytes(b"this is not a PDF")

            # When: the malformed file is preflighted.
            result = printing.preflight_pdf(pdf_path)

            # Then: parsing failure is a blocking file-level finding.
            self.assertFalse(result.is_printable)
            self.assertEqual(
                ((pdf_path, None, printing.PdfPreflightFindingCode.UNREADABLE),),
                tuple((finding.path, finding.page_number, finding.code) for finding in result.findings),
            )

    def test_preflight_pdf_reports_encrypted_file(self) -> None:
        # Given: a PDF encrypted with a user password.
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "encrypted.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=595, height=842)
            writer.encrypt("secret")
            with pdf_path.open("wb") as pdf_file:
                writer.write(pdf_file)

            # When: the encrypted file is preflighted without a password.
            result = printing.preflight_pdf(pdf_path)

            # Then: encryption is a blocking file-level finding.
            self.assertFalse(result.is_printable)
            self.assertEqual(
                ((pdf_path, None, printing.PdfPreflightFindingCode.ENCRYPTED),),
                tuple((finding.path, finding.page_number, finding.code) for finding in result.findings),
            )

    def test_preflight_pdf_reports_zero_page_file(self) -> None:
        # Given: a valid PDF containing no pages.
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "zero-pages.pdf"
            writer = PdfWriter()
            with pdf_path.open("wb") as pdf_file:
                writer.write(pdf_file)

            # When: the zero-page file is preflighted.
            result = printing.preflight_pdf(pdf_path)

            # Then: the empty document is a blocking file-level finding.
            self.assertFalse(result.is_printable)
            self.assertEqual(
                ((pdf_path, None, printing.PdfPreflightFindingCode.ZERO_PAGES),),
                tuple((finding.path, finding.page_number, finding.code) for finding in result.findings),
            )

    def test_preflight_pdf_reports_every_conflicting_mixed_orientation_page(self) -> None:
        # Given: a PDF containing portrait, landscape, and portrait pages.
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "mixed.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=595, height=842)
            writer.add_blank_page(width=842, height=595)
            writer.add_blank_page(width=595, height=842)
            with pdf_path.open("wb") as pdf_file:
                writer.write(pdf_file)

            # When: the mixed-orientation PDF is preflighted.
            result = printing.preflight_pdf(pdf_path)

            # Then: every page is classified and conflicting pages block printing.
            self.assertFalse(result.is_printable)
            self.assertEqual(
                (
                    printing.PdfOrientation.PORTRAIT,
                    printing.PdfOrientation.LANDSCAPE,
                    printing.PdfOrientation.PORTRAIT,
                ),
                tuple(page.orientation for page in result.pages),
            )
            self.assertEqual(
                ((pdf_path, 2, printing.PdfPreflightFindingCode.MIXED_ORIENTATION),),
                tuple((finding.path, finding.page_number, finding.code) for finding in result.findings),
            )


class SumatraPreflightGateTest(unittest.TestCase):
    def test_sumatra_pdf_batches_returns_no_batch_when_file_is_missing(self) -> None:
        # Given: a path that does not exist.
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "missing.pdf"

            # When: batch construction receives the missing file.
            batches = sumatra_pdf_batches([pdf_path], copies=1, duplex=True)

            # Then: no Sumatra batch is produced.
            self.assertEqual([], batches)

    def test_sumatra_pdf_batches_returns_no_batch_when_file_is_unreadable(self) -> None:
        # Given: a fresh malformed PDF fixture.
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "corrupt.pdf"
            pdf_path.write_bytes(b"not a PDF")

            # When: batch construction receives the unreadable file.
            batches = sumatra_pdf_batches([pdf_path], copies=1, duplex=True)

            # Then: no Sumatra batch is produced.
            self.assertEqual([], batches)

    def test_sumatra_pdf_batches_returns_no_batch_when_file_is_encrypted(self) -> None:
        # Given: a PDF encrypted with a user password.
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "encrypted.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=595, height=842)
            writer.encrypt("secret")
            with pdf_path.open("wb") as pdf_file:
                writer.write(pdf_file)

            # When: batch construction receives the encrypted file.
            batches = sumatra_pdf_batches([pdf_path], copies=1, duplex=True)

            # Then: no Sumatra batch is produced.
            self.assertEqual([], batches)

    def test_sumatra_pdf_batches_returns_no_batch_when_file_has_zero_pages(self) -> None:
        # Given: a valid PDF containing no pages.
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "zero-pages.pdf"
            writer = PdfWriter()
            with pdf_path.open("wb") as pdf_file:
                writer.write(pdf_file)

            # When: batch construction receives the zero-page PDF.
            batches = sumatra_pdf_batches([pdf_path], copies=1, duplex=True)

            # Then: no Sumatra batch is produced.
            self.assertEqual([], batches)

    def test_sumatra_pdf_batches_returns_no_batch_when_pdf_orientation_is_mixed(self) -> None:
        # Given: a single PDF with portrait and landscape pages.
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "mixed.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=595, height=842)
            writer.add_blank_page(width=842, height=595)
            with pdf_path.open("wb") as pdf_file:
                writer.write(pdf_file)

            # When: batch construction receives the mixed-orientation PDF.
            batches = sumatra_pdf_batches([pdf_path], copies=1, duplex=True)

            # Then: no Sumatra batch is produced.
            self.assertEqual([], batches)


if __name__ == "__main__":
    unittest.main()
