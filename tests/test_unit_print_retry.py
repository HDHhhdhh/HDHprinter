from __future__ import annotations

import unittest


class PrintRetryTest(unittest.TestCase):
    def test_retry_from_failure_keeps_failed_and_later_jobs_in_order(self) -> None:
        # Given: a print run whose second job is the first unconfirmed job.
        from unit_print_retry import retry_from_failure

        pending_jobs = ("first.pdf", "second.pdf", "third.pdf")

        # When: the run records the failed job index.
        retry = retry_from_failure(pending_jobs, failed_index=1)

        # Then: retry starts from the failed job without resending confirmed work.
        self.assertEqual(("second.pdf", "third.pdf"), retry.jobs)

    def test_retry_from_failure_rejects_an_out_of_range_failed_index(self) -> None:
        # Given: a completed print run with no failed job.
        from unit_print_retry import retry_from_failure

        # When / Then: requesting a retry snapshot past the final job is invalid.
        with self.assertRaises(IndexError):
            retry_from_failure(("only.pdf",), failed_index=1)

    def test_retry_preserves_separator_context_even_when_separator_was_already_sent(self) -> None:
        from unit_print_retry import retry_from_failure

        retry = retry_from_failure(
            ("name.pdf", "body-1.pdf", "body-2.pdf"),
            failed_index=1,
            include_separator_pages=True,
            include_name_separator_pages=True,
            preceding_name_separator_label="학생A",
        )

        self.assertEqual(("body-1.pdf", "body-2.pdf"), retry.jobs)
        self.assertTrue(retry.include_separator_pages)
        self.assertTrue(retry.include_name_separator_pages)
        self.assertEqual("학생A", retry.preceding_name_separator_label)


if __name__ == "__main__":
    unittest.main()
