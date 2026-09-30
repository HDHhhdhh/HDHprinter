from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter

from unit_print_manager import ExamPdf, PrintJob, route_job_to_profile


def write_pdf(path: Path, width: float, height: float) -> None:
    writer = PdfWriter()
    _ = writer.add_blank_page(width=width, height=height)
    with path.open("wb") as pdf_file:
        _ = writer.write(pdf_file)


def make_job(
    path: Path,
    *,
    is_separator: bool = False,
    is_name_separator: bool = False,
    printer_override: str | None = None,
) -> PrintJob:
    return PrintJob(
        block_index=1,
        block_total=1,
        block_uid=1,
        block_summary="test block",
        item=ExamPdf(1, path.stem, path),
        copies=1,
        is_separator=is_separator,
        is_name_separator=is_name_separator,
        printer_override=printer_override,
    )


class PrintManagerRoutingTest(unittest.TestCase):
    def test_material_job_uses_its_saved_printer_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "able.pdf"
            write_pdf(path, 842, 595)

            route = route_job_to_profile(
                "iR-ADV 8585 III",
                make_job(path, printer_override="iR-ADV 8585 III -A:ble"),
                {},
            )

            self.assertEqual("iR-ADV 8585 III -A:ble", route)

    def test_routes_portrait_landscape_and_separator_jobs_to_dedicated_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            portrait_path = directory / "portrait.pdf"
            landscape_path = directory / "landscape.pdf"
            separator_path = directory / "separator.pdf"
            name_separator_path = directory / "name-separator.pdf"
            write_pdf(portrait_path, 595, 842)
            write_pdf(landscape_path, 842, 595)
            write_pdf(separator_path, 595, 842)
            write_pdf(name_separator_path, 595, 842)
            preflight_by_path = {}

            routes = (
                route_job_to_profile("iR-ADV 8585 III", make_job(portrait_path), preflight_by_path),
                route_job_to_profile("iR-ADV 8585 III", make_job(landscape_path), preflight_by_path),
                route_job_to_profile("iR-ADV 8585 III", make_job(separator_path, is_separator=True), preflight_by_path),
                route_job_to_profile(
                    "iR-ADV 8585 III",
                    make_job(
                        name_separator_path,
                        is_separator=True,
                        is_name_separator=True,
                    ),
                    preflight_by_path,
                ),
            )

            self.assertEqual(
                (
                    "iR-ADV 8585 III - 세로",
                    "iR-ADV 8585 III - 가로",
                    "iR-ADV 8585 III - 구분지",
                    "iR-ADV 8585 III - 세로",
                ),
                routes,
            )
