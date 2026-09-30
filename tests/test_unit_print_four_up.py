from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from unit_print_pdf_printing import (
    A4_HEIGHT_POINTS,
    A4_WIDTH_POINTS,
    PdfOrientation,
    materialize_four_up_pdf,
    preflight_pdf,
)


def write_pages(path: Path, count: int, *, width: float = 595, height: float = 842) -> None:
    writer = PdfWriter()
    for _page_number in range(count):
        writer.add_blank_page(width=width, height=height)
    with path.open("wb") as pdf_file:
        writer.write(pdf_file)


class FourUpLayoutTest(unittest.TestCase):
    def test_sixteen_landscape_source_pages_become_four_pages_for_two_duplex_sheets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = directory / "two-sheet-staple-test.pdf"
            write_pages(source, 16, width=1024, height=765)

            output = materialize_four_up_pdf(source, directory / "cache")

            reader = PdfReader(str(output))
            self.assertEqual(4, len(reader.pages))
            self.assertTrue(all(float(page.mediabox.width) > float(page.mediabox.height) for page in reader.pages))

    def test_materializes_four_source_pages_per_landscape_a4_sheet(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = directory / "source.pdf"
            cache = directory / "cache"
            write_pages(source, 5)

            output = materialize_four_up_pdf(source, cache)

            reader = PdfReader(str(output))
            self.assertEqual(2, len(reader.pages))
            for page in reader.pages:
                self.assertAlmostEqual(A4_HEIGHT_POINTS, float(page.mediabox.width), places=3)
                self.assertAlmostEqual(A4_WIDTH_POINTS, float(page.mediabox.height), places=3)
            result = preflight_pdf(output)
            self.assertTrue(result.is_printable)
            self.assertIs(PdfOrientation.LANDSCAPE, result.orientation)

    def test_places_pages_left_to_right_then_top_to_bottom(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = directory / "ordered.pdf"
            writer = PdfWriter()
            for page_number in range(1, 5):
                page = writer.add_blank_page(width=595, height=842)
                marker = DecodedStreamObject()
                marker.set_data(f"/P{page_number} BMC EMC".encode("ascii"))
                page[NameObject("/Contents")] = marker
            with source.open("wb") as pdf_file:
                writer.write(pdf_file)

            output = materialize_four_up_pdf(source, directory / "cache")
            contents = PdfReader(str(output)).pages[0].get_contents()
            assert contents is not None
            content = contents.get_data()
            marker_positions = [content.index(f"/P{number} BMC".encode("ascii")) for number in range(1, 5)]
            transforms: list[tuple[float, float]] = []
            for marker_position in marker_positions:
                prefix = content[:marker_position]
                matches = list(
                    re.finditer(
                        rb"([0-9.]+) 0(?:[.]0)? 0(?:[.]0)? ([0-9.]+) ([0-9.]+) ([0-9.]+) cm",
                        prefix,
                    )
                )
                non_identity = [match for match in matches if float(match.group(1)) != 1.0]
                transform = non_identity[-1]
                transforms.append((float(transform.group(3)), float(transform.group(4))))

            self.assertEqual(sorted(marker_positions), marker_positions)
            self.assertLess(transforms[0][0], transforms[1][0])
            self.assertEqual(transforms[0][1], transforms[1][1])
            self.assertLess(transforms[2][0], transforms[3][0])
            self.assertEqual(transforms[2][1], transforms[3][1])
            self.assertGreater(transforms[0][1], transforms[2][1])
            self.assertGreater(transforms[1][1], transforms[3][1])

    def test_reuses_cache_until_the_source_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = directory / "source.pdf"
            cache = directory / "cache"
            write_pages(source, 4)

            first = materialize_four_up_pdf(source, cache)
            second = materialize_four_up_pdf(source, cache)
            write_pages(source, 5)
            changed = materialize_four_up_pdf(source, cache)

            self.assertEqual(first, second)
            self.assertNotEqual(first, changed)
            self.assertEqual(2, len(PdfReader(str(changed)).pages))


if __name__ == "__main__":
    unittest.main()
