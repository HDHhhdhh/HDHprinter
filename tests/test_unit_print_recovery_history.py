from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from unit_print_history import HistoryWriteError, PrintHistory
from unit_print_recovery_history import classify_recovery_queue


TZ = timezone(timedelta(hours=9))
START = datetime(2026, 9, 29, 11, 0, tzinfo=TZ)
SAVED = START + timedelta(minutes=10)
NOW = SAVED + timedelta(minutes=20)


class RecoveryHistoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / "history"
        self.history = PrintHistory(self.directory, session_id="source-session")
        self.snapshot = {
            "schema_version": 1,
            "snapshot_id": "snapshot-1",
            "session_id": "source-session",
            "session_started_at": START.isoformat(),
            "created_at": SAVED.isoformat(),
            "queue": [
                {"block": {"history_id": block_id}, "state": "대기", "detail": "", "risk": "unsent"}
                for block_id in ("block-a", "block-b", "block-c")
            ],
            "run": {"is_printing": False, "run_id": None},
        }
        self.record("session_started", at=START)

    def record(self, event: str, *, at: datetime = NOW, history: PrintHistory | None = None, **data: object) -> dict:
        with patch("unit_print_history._local_now", return_value=at):
            return (history or self.history).record(event, **data)

    def attempt(self, block_id: str, *, event: str = "job_attempt", at: datetime = NOW, history: PrintHistory | None = None) -> None:
        self.record(event, at=at, history=history, block_id=block_id, job_id=f"job-{block_id}", run_id="run-1")

    def review(self, *, now: datetime = NOW):
        return classify_recovery_queue(
            self.snapshot, self.directory, now=now, consumed_block_ids=frozenset(),
        )

    def assert_all_held(self, review) -> None:
        self.assertEqual(frozenset(), review.safe_block_ids)
        self.assertEqual(frozenset({"block-a", "block-b", "block-c"}), review.risky_block_ids)
        self.assertIn("복구 보류", review.warning)

    def test_intact_journal_without_attempts_proves_waiting_blocks(self) -> None:
        review = self.review()
        self.assertEqual(frozenset({"block-a", "block-b", "block-c"}), review.safe_block_ids)
        self.assertEqual(frozenset(), review.risky_block_ids)
        self.assertEqual("", review.warning)

    def test_any_process_boundary_marker_holds_only_its_whole_block(self) -> None:
        for event in ("job_attempt", "job_submitted", "job_unconfirmed", "job_physically_completed"):
            with self.subTest(event=event):
                self.attempt("block-a", event=event)
                review = self.review()
                self.assertEqual(frozenset({"block-b", "block-c"}), review.safe_block_ids)
                self.assertEqual(frozenset({"block-a"}), review.risky_block_ids)
                self.assertEqual(frozenset(), review.completed_block_ids)

    def test_same_block_marker_in_another_session_also_prevents_recovery(self) -> None:
        other = PrintHistory(self.directory, session_id="other-session")
        self.attempt("block-b", history=other)
        self.assertEqual(frozenset({"block-b"}), self.review().risky_block_ids)

    def test_attempt_before_snapshot_and_attempt_after_snapshot_both_hold(self) -> None:
        self.attempt("block-a", at=START + timedelta(minutes=1))
        self.attempt("block-b", at=NOW)
        review = self.review()
        self.assertEqual(frozenset({"block-c"}), review.safe_block_ids)
        self.assertEqual(frozenset({"block-a", "block-b"}), review.risky_block_ids)

    def test_saved_non_unsent_risk_never_becomes_waiting_without_marker(self) -> None:
        self.snapshot["queue"][0]["risk"] = "submitted"
        self.snapshot["queue"][1]["risk"] = "uncertain"
        self.snapshot["queue"][2]["risk"] = "completed"
        review = self.review()
        self.assertEqual(frozenset(), review.safe_block_ids)
        self.assertEqual(frozenset({"block-a", "block-b", "block-c"}), review.risky_block_ids)
        self.assertEqual(frozenset({"block-c"}), review.completed_block_ids)

    def test_partial_consumption_holds_originals_and_preserves_untouched_blocks(self) -> None:
        self.record("session_closed", at=NOW)
        review = classify_recovery_queue(
            self.snapshot, self.directory, now=NOW + timedelta(days=3),
            consumed_block_ids=frozenset({"block-a", "unrelated-original"}),
        )
        self.assertEqual(frozenset({"block-b", "block-c"}), review.safe_block_ids)
        self.assertEqual(frozenset({"block-a"}), review.risky_block_ids)
        self.assertEqual(frozenset(), review.completed_block_ids)
        self.assertEqual("", review.warning)

    def test_unchecked_or_invalid_consumption_proof_never_returns_safe_blocks(self) -> None:
        for consumed in (None, "block-a", {"block-a", ""}):
            with self.subTest(consumed=consumed):
                self.assert_all_held(classify_recovery_queue(
                    self.snapshot, self.directory, now=NOW, consumed_block_ids=consumed,
                ))

    def test_missing_journal_never_means_no_attempt(self) -> None:
        (self.directory / "2026-09-29.jsonl").unlink()
        review = self.review()
        self.assert_all_held(review)
        self.assertIn("2026-09-29.jsonl", review.warning)

    def test_read_failure_never_returns_a_partial_safe_list(self) -> None:
        with patch("pathlib.Path.open", side_effect=PermissionError("private-path/student-name")):
            review = self.review()
        self.assert_all_held(review)
        self.assertNotIn("student-name", review.warning)
        self.assertNotIn("private-path", review.warning)

    def test_torn_and_non_object_rows_hold_all(self) -> None:
        path = self.directory / "2026-09-29.jsonl"
        original = path.read_bytes()
        for tail in (b'{"event":"job_attempt"', b'[]\n', b'\xff\n'):
            with self.subTest(tail=tail):
                path.write_bytes(original + tail)
                self.assert_all_held(self.review())

    def test_complete_json_without_final_newline_is_still_unconfirmed(self) -> None:
        path = self.directory / "2026-09-29.jsonl"
        self.record("queue_reordered", block_order=[])
        path.write_bytes(path.read_bytes().rstrip(b"\n"))
        self.assert_all_held(self.review())

    def test_unknown_required_schema_or_missing_block_session_data_holds_all(self) -> None:
        path = self.directory / "2026-09-29.jsonl"
        original = path.read_bytes()
        base = self.record("queue_reordered", block_order=[])
        for change in (
            {"schema_version": 2},
            {"schema_version": True},
            {"event": "job_attempt", "job_id": "job-a", "run_id": "run-a"},
            {"event": "job_attempt", "block_id": "block-a", "job_id": "job-a", "run_id": "run-a", "session_id": None},
            {"event": "job_attempt", "block_id": "block-a", "job_id": "job-a", "run_id": None},
        ):
            with self.subTest(change=change):
                row = {**base, **change}
                path.write_bytes(original + json.dumps(row).encode("utf-8") + b"\n")
                self.assert_all_held(self.review())

    def test_duplicate_block_event_and_nested_keys_never_hide_an_attempt(self) -> None:
        path = self.directory / "2026-09-29.jsonl"
        before_attempt = path.read_bytes()
        self.attempt("block-a")
        marker = path.read_bytes()[len(before_attempt):]
        self.assertEqual(frozenset({"block-a"}), self.review().risky_block_ids)
        damaged_markers = (
            marker.replace(b'"block_id":"block-a"', b'"block_id":"block-a","block_id":"unrelated"'),
            marker.replace(b'"event":"job_attempt"', b'"event":"job_attempt","event":"queue_reordered"'),
            marker.rstrip(b"\n")[:-1] + b',"metadata":{"number":1,"number":2}}\n',
        )
        for damaged in damaged_markers:
            with self.subTest(marker=damaged):
                path.write_bytes(before_attempt + damaged)
                review = self.review()
                self.assert_all_held(review)
                self.assertIn("중복된 JSON 필드", review.warning)

    def test_missing_source_session_start_holds_all(self) -> None:
        self.snapshot["session_id"] = "missing-session"
        self.assert_all_held(self.review())

    def test_mismatched_session_start_timestamp_holds_all(self) -> None:
        self.snapshot["session_started_at"] = (START - timedelta(minutes=1)).isoformat()
        self.assert_all_held(self.review())

    def test_snapshot_saved_on_later_day_reads_source_start_day(self) -> None:
        next_day = START + timedelta(days=1)
        self.snapshot["created_at"] = next_day.isoformat()
        self.record("queue_reordered", at=next_day, block_order=[])
        self.attempt("block-a", at=START + timedelta(hours=1))
        review = self.review(now=next_day + timedelta(minutes=1))
        self.assertEqual(frozenset({"block-b", "block-c"}), review.safe_block_ids)
        self.assertEqual(frozenset({"block-a"}), review.risky_block_ids)

    def test_crashed_session_needs_every_day_through_now(self) -> None:
        last_day = NOW + timedelta(days=2)
        self.record("queue_reordered", at=last_day, block_order=[])
        review = self.review(now=last_day)
        self.assert_all_held(review)
        self.assertIn("2026-09-30.jsonl", review.warning)

    def test_closed_session_does_not_require_later_weekend_journals(self) -> None:
        self.record("session_closed", at=NOW)
        review = self.review(now=NOW + timedelta(days=3))
        self.assertEqual(frozenset({"block-a", "block-b", "block-c"}), review.safe_block_ids)
        self.assertEqual("", review.warning)

    def test_old_closed_backup_reads_only_its_short_source_session(self) -> None:
        self.record("session_closed", at=NOW)
        review = self.review(now=NOW + timedelta(days=400))
        self.assertEqual(frozenset({"block-a", "block-b", "block-c"}), review.safe_block_ids)
        self.assertEqual("", review.warning)

    def test_closed_multiday_session_requires_all_days_until_close(self) -> None:
        close_day = NOW + timedelta(days=2)
        self.record("session_closed", at=close_day)
        self.assert_all_held(self.review(now=close_day + timedelta(days=1)))

    def test_unrelated_old_or_post_close_malformed_files_are_not_read(self) -> None:
        (self.directory / "2026-09-28.jsonl").write_bytes(b"old malformed\n")
        (self.directory / "2026-09-30.jsonl").write_bytes(b"future malformed\n")
        self.record("session_closed")
        self.assertEqual("", self.review(now=NOW + timedelta(days=1)).warning)

    def test_valid_required_history_failure_without_marker_does_not_send(self) -> None:
        sender = Mock()
        with patch("unit_print_history.os.fsync", side_effect=OSError("disk failure")):
            with self.assertRaises(HistoryWriteError):
                self.attempt("block-a")
                sender()
        sender.assert_not_called()
        # Failed fsync may leave a complete marker. It is safe to hold that
        # block rather than guessing that no printer process was launched.
        self.assertEqual(frozenset({"block-a"}), self.review().risky_block_ids)

    def test_size_and_date_bounds_fail_closed(self) -> None:
        with patch("unit_print_recovery_history.MAX_RECORD_BYTES", 8):
            self.assert_all_held(self.review())
        with patch("unit_print_recovery_history.MAX_REVIEW_BYTES", 8):
            self.assert_all_held(self.review())
        with patch("unit_print_recovery_history.MAX_REVIEW_RECORDS", 0):
            self.assert_all_held(self.review())
        self.assert_all_held(self.review(now=NOW + timedelta(days=367)))
        for offset in (1, 2):
            self.record("queue_reordered", at=NOW + timedelta(days=offset), block_order=[])
        with patch("unit_print_recovery_history.MAX_REVIEW_DAYS", 2):
            review = self.review(now=NOW + timedelta(days=2))
        self.assert_all_held(review)
        self.assertIn("2일을 초과", review.warning)

    def test_invalid_snapshot_metadata_holds_all_without_mutating_it(self) -> None:
        self.snapshot["session_started_at"] = START.replace(tzinfo=None).isoformat()
        before = copy.deepcopy(self.snapshot)
        self.assert_all_held(self.review())
        self.assertEqual(before, self.snapshot)


if __name__ == "__main__":
    unittest.main()
