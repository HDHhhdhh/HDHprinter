from __future__ import annotations

import unittest

from pypdf import PdfReader

from unit_print_manager import SEPARATOR_PAGE_PATH, ensure_separator_pdf
from unit_print_pdf_printing import PdfOrientation, preflight_pdf


class FixedSeparatorPdfTest(unittest.TestCase):
    def test_fixed_separator_is_one_landscape_a4_page_with_center_label(self) -> None:
        path = ensure_separator_pdf()

        self.assertEqual(SEPARATOR_PAGE_PATH, path)
        reader = PdfReader(str(path))
        self.assertEqual(1, len(reader.pages))
        page = reader.pages[0]
        self.assertAlmostEqual(841.8898, float(page.mediabox.width), places=2)
        self.assertAlmostEqual(595.2756, float(page.mediabox.height), places=2)
        self.assertEqual(0, int(page.get("/Rotate", 0)) % 360)
        self.assertEqual("구분지", (page.extract_text() or "").strip())
        text_runs: list[tuple[str, float, float]] = []
        page.extract_text(
            visitor_text=lambda text, _cm, text_matrix, _font, font_size: text_runs.append(
                (text.strip(), float(text_matrix[4]), float(font_size))
            )
        )
        label_run = next(run for run in text_runs if run[0] == "구분지")
        self.assertAlmostEqual(float(page.mediabox.width) * 0.1, label_run[1], places=2)
        self.assertGreater(label_run[2], 200)

        preflight = preflight_pdf(path)
        self.assertTrue(preflight.is_printable)
        self.assertIs(PdfOrientation.LANDSCAPE, preflight.orientation)


if __name__ == "__main__":
    unittest.main()
