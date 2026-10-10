from __future__ import annotations

import unittest
from pathlib import Path

from unit_print_device_state import DeviceConsumables, PaperSupply
from unit_print_eta import estimated_sheets
from unit_print_resource_estimate import (
    PaperReadiness,
    ResourceJob,
    ResourceEstimateCancelled,
    assess_paper_readiness,
    estimate_a4_capacity,
    estimate_print_resources,
    source_capacity_sheets,
)


class ResourceEstimateTests(unittest.TestCase):
    def test_duplex_rounds_each_copy_before_multiplying(self) -> None:
        self.assertEqual(6, estimated_sheets(5, 2, True))
        self.assertEqual(10, estimated_sheets(5, 2, False))

    def test_canon_a4_sensor_bands_use_documented_drawer_capacities(self) -> None:
        consumables = DeviceConsumables(
            paper=(
                PaperSupply("카세트 1", "A4", 66, 100, "66~100%"),
                PaperSupply("카세트 2", "A4", 66, 100, "66~100%"),
                PaperSupply("카세트 3", "B4", 1, 33, "1~33%"),
            )
        )

        capacity = estimate_a4_capacity(consumables)

        self.assertIsNotNone(capacity)
        assert capacity is not None
        self.assertEqual(1980, capacity.lower_sheets)
        self.assertEqual(3000, capacity.upper_sheets)
        self.assertTrue(capacity.is_complete)

    def test_source_capacity_recognizes_standard_drawers_and_bypass(self) -> None:
        self.assertEqual(1500, source_capacity_sheets("카세트 1"))
        self.assertEqual(550, source_capacity_sheets("Paper Drawer 4"))
        self.assertEqual(100, source_capacity_sheets("다목적 트레이"))
        self.assertIsNone(source_capacity_sheets("외부 페이퍼 데크"))

    def test_four_up_duplex_and_copies_are_counted_per_output_set(self) -> None:
        job = ResourceJob(Path("lesson.pdf"), copies=2, pages_per_sheet=4)

        estimate = estimate_print_resources(
            (job,),
            page_counter=lambda _path: 96,
            duplex=True,
        )

        self.assertEqual(24, estimate.total_sheets)
        self.assertEqual(2, estimate.staple_sets)
        self.assertEqual(2, estimate.output_sets)

    def test_single_sheet_jobs_do_not_consume_a_staple(self) -> None:
        estimate = estimate_print_resources(
            (
                ResourceJob(None, known_page_count=1, stapled=True),
                ResourceJob(None, known_page_count=2, stapled=True),
            ),
            page_counter=lambda _path: 0,
            duplex=False,
        )

        self.assertEqual(1, estimate.staple_sets)

    def test_bulk_copies_keep_paper_counts_without_adding_300_setup_delays(self) -> None:
        estimate = estimate_print_resources(
            (ResourceJob(None, copies=300, known_page_count=2),),
            page_counter=lambda _path: 0,
            duplex=True,
        )
        bundled = estimate_print_resources(
            (ResourceJob(None, copies=300, known_page_count=2, dispatch_jobs=300),),
            page_counter=lambda _path: 0,
            duplex=True,
        )
        self.assertEqual(300, estimate.total_sheets)
        self.assertEqual(300, estimate.output_sets)
        self.assertEqual(0, estimate.staple_sets)
        self.assertLess(estimate.eta.seconds_expected, 10 * 60)
        self.assertGreater(bundled.eta.seconds_expected - estimate.eta.seconds_expected, 2000)

    def test_readiness_uses_lower_band_for_sufficient_and_upper_for_shortage(self) -> None:
        capacity = estimate_a4_capacity(
            DeviceConsumables(
                paper=(PaperSupply("카세트 1", "A4", 66, 100, "66~100%"),)
            )
        )
        assert capacity is not None
        self.assertEqual(
            PaperReadiness.SUFFICIENT,
            assess_paper_readiness(900, capacity, estimate_complete=True),
        )
        self.assertEqual(
            PaperReadiness.BORDERLINE,
            assess_paper_readiness(1200, capacity, estimate_complete=True),
        )
        self.assertEqual(
            PaperReadiness.INSUFFICIENT,
            assess_paper_readiness(1600, capacity, estimate_complete=True),
        )

    def test_unknown_pdf_marks_forecast_partial_without_hiding_known_shortage(self) -> None:
        consumables = DeviceConsumables(
            paper=(PaperSupply("카세트 3", "A4", 1, 33, "1~33%"),)
        )
        jobs = (
            ResourceJob(Path("known.pdf"), copies=20),
            ResourceJob(Path("missing.pdf")),
        )

        def pages(path: Path) -> int:
            if path.name == "missing.pdf":
                raise OSError("missing")
            return 40

        estimate = estimate_print_resources(
            jobs,
            page_counter=pages,
            duplex=False,
            consumables=consumables,
        )

        self.assertEqual(800, estimate.total_sheets)
        self.assertEqual(1, estimate.unknown_documents)
        self.assertEqual(PaperReadiness.INSUFFICIENT, estimate.paper_readiness)
        self.assertIn("A4 최소 800장", estimate.display_text)

    def test_staple_capacity_warning_counts_output_sets(self) -> None:
        estimate = estimate_print_resources(
            (ResourceJob(Path("large.pdf"), copies=3),),
            page_counter=lambda _path: 132,
            duplex=True,
        )

        self.assertEqual(3, estimate.staple_sets)
        self.assertEqual(3, estimate.oversize_staple_sets)
        self.assertEqual("danger", estimate.tone)
        self.assertIn("65장을 넘는", estimate.detail_text)

    def test_superseded_forecast_stops_before_reading_the_next_pdf(self) -> None:
        checks = iter((False, True))
        read_paths: list[Path] = []

        with self.assertRaises(ResourceEstimateCancelled):
            estimate_print_resources(
                (
                    ResourceJob(Path("first.pdf")),
                    ResourceJob(Path("second.pdf")),
                ),
                page_counter=lambda path: read_paths.append(path) or 2,
                duplex=True,
                cancelled=lambda: next(checks),
            )

        self.assertEqual([Path("first.pdf")], read_paths)


if __name__ == "__main__":
    unittest.main()
