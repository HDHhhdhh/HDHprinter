from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from unit_print_queue_recovery import (
    RecoveryReadError,
    RecoverySessionActiveError,
    RecoveryStore,
    RecoveryValidationError,
    RecoveryWriteError,
    RecoveryWriter,
    validate_snapshot,
)


SESSION_1 = "a816d918-70ec-4b29-8f15-ef8e23cd1201"
SESSION_2 = "a816d918-70ec-4b29-8f15-ef8e23cd1202"
SESSION_3 = "a816d918-70ec-4b29-8f15-ef8e23cd1203"


def snapshot(*, session: str = SESSION_1, sequence: int = 1) -> dict:
    return {
        "schema_version": 1,
        "snapshot_id": f"snapshot-{sequence}",
        "created_at": "2026-09-30T14:12:00+09:00",
        "session_id": session,
        "session_started_at": "2026-09-29T23:58:00+09:00",
        "reason": "블록 추가",
        "run": {"is_printing": False, "run_id": None},
        "queue": [{
            "state": "대기", "detail": "", "risk": "unsent",
            "block": {
                "subject": "공수2", "level": "교과서", "round_name": "직접",
                "start": 7, "end": 9, "copies": 3, "bundled": True,
                "source": "material_able", "printer_override": "Canon - A:ble",
                "display_label": "학생 자료", "pages_per_sheet": 1, "printer_route": "A:ble",
                "selected_numbers": [7, 9], "name_separator_label": "김학생",
                "material_kind": "교재", "history_id": "block-1", "order_id": "order-1",
                "material_id": "MAT-1", "replaces_block_id": "block-old",
                "files": [
                    {"number": 7, "name": "백지리뷰", "path": "D:/테스트/7.pdf", "printer_override": "Canon - 세로", "pages_per_sheet": 4},
                    {"number": 9, "name": "렉쳐노트", "path": "D:/테스트/7.pdf", "printer_override": "Canon - A:ble", "pages_per_sheet": None},
                ],
            },
        }],
    }


class RecoveryValidationTests(unittest.TestCase):
    def test_does_not_coerce_or_normalize_exact_block_metadata(self) -> None:
        payload = snapshot()
        original = copy.deepcopy(payload)
        validate_snapshot(payload)
        self.assertEqual(original, payload)

    def test_malformed_metadata_rejects_whole_snapshot(self) -> None:
        mutations = [
            lambda p: p.update(schema_version=2),
            lambda p: p.update(schema_version=True),
            lambda p: p.pop("session_started_at"),
            lambda p: p.update(extra="unexpected"),
            lambda p: p.update(session_id="../../outside"),
            lambda p: p.update(session_id=SESSION_1.upper()),
            lambda p: p.update(created_at="2026-09-30T14:12:00"),
            lambda p: p.update(session_started_at="2026-10-01T14:12:00+09:00"),
            lambda p: p["run"].update(is_printing=1),
            lambda p: p["queue"][0].update(risk="ready"),
            lambda p: p["queue"][0]["block"].update(copies=True),
            lambda p: p["queue"][0]["block"].update(copies=0),
            lambda p: p["queue"][0]["block"].update(end=1),
            lambda p: p["queue"][0]["block"].update(bundled=1),
            lambda p: p["queue"][0]["block"].update(pages_per_sheet=2),
            lambda p: p["queue"][0]["block"].update(selected_numbers=[7, 7]),
            lambda p: p["queue"][0]["block"].update(selected_numbers=[]),
            lambda p: p["queue"][0]["block"].update(files=[]),
            lambda p: p["queue"][0]["block"].pop("order_id"),
            lambda p: p["queue"][0]["block"]["files"][0].update(path="bad\x00.pdf"),
            lambda p: p["queue"][0]["block"]["files"][0].update(path="bad\ud800.pdf"),
            lambda p: p["queue"][0]["block"]["files"][0].update(pages_per_sheet=True),
            lambda p: p["queue"][0]["block"]["files"][0].pop("printer_override"),
            lambda p: p["queue"].append(copy.deepcopy(p["queue"][0])),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                payload = snapshot()
                mutate(payload)
                with self.assertRaises(RecoveryValidationError):
                    validate_snapshot(payload)

    def test_queue_and_file_count_limits_are_checked_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "recovery"
            store = RecoveryStore(directory)
            with patch("unit_print_queue_recovery.MAX_QUEUE_BLOCKS", 0):
                with self.assertRaises(RecoveryValidationError):
                    store.save(snapshot())
            with patch("unit_print_queue_recovery.MAX_TOTAL_FILES", 1):
                with self.assertRaises(RecoveryValidationError):
                    store.save(snapshot())
            self.assertFalse(directory.exists())


class RecoveryStoreTests(unittest.TestCase):
    def test_full_round_trip_keeps_file_order_overrides_ids_and_risk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = RecoveryStore(Path(temporary))
            payload = snapshot()
            payload["queue"][0].update(state="진행 중", detail="전송 결과 확인 필요", risk="uncertain")
            self.assertTrue(store.save(payload))
            self.assertEqual(payload, store.load())
            self.assertEqual((Path(temporary) / f"session-{SESSION_1}.json",), store.list_candidates())
            raw = store.list_candidates()[0].read_text(encoding="utf-8")
            self.assertIn("김학생", raw)
            self.assertNotIn("\\u", raw)

    def test_new_session_edits_and_empty_launch_preserve_crashed_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            store = RecoveryStore(directory)
            crashed = snapshot(session=SESSION_1)
            store.save(crashed)
            crash_path = directory / f"session-{SESSION_1}.json"
            original = crash_path.read_bytes()
            for sequence in range(1, 6):
                store.save(snapshot(session=SESSION_2, sequence=sequence))
            empty = snapshot(session=SESSION_3)
            empty["queue"] = []
            self.assertFalse(store.save(empty))
            self.assertEqual(original, crash_path.read_bytes())
            self.assertEqual(crashed, store.load(crash_path))
            self.assertEqual(2, len(store.list_candidates()))
            self.assertEqual(5, int(store.load(directory / f"session-{SESSION_2}.json")["snapshot_id"].split("-")[-1]))

    def test_candidates_order_by_write_time_without_filtering_corrupt_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = RecoveryStore(Path(temporary))
            store.save(snapshot(session=SESSION_1))
            store.save(snapshot(session=SESSION_2))
            old_path = Path(temporary) / f"session-{SESSION_1}.json"
            newest = Path(temporary) / f"session-{SESSION_2}.json"
            old_path.write_bytes(b"broken source")
            os.utime(old_path, ns=(1_000_000_000, 1_000_000_000))
            os.utime(newest, ns=(2_000_000_000, 2_000_000_000))
            self.assertEqual((newest, old_path), store.list_candidates())
            self.assertEqual(SESSION_2, store.load()["session_id"])
            newest.write_bytes(b"broken newest")
            with self.assertRaises(RecoveryReadError):
                store.load()
            self.assertEqual((newest, old_path), store.list_candidates())

    def test_missing_candidate_does_not_create_storage_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "recovery"
            store = RecoveryStore(directory)
            self.assertEqual((), store.list_candidates())
            self.assertIsNone(store.load())
            self.assertIsNone(store.load(directory / "missing.json"))
            self.assertFalse(directory.exists())

    def test_fsync_failure_preserves_old_candidate_and_cleans_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = RecoveryStore(Path(temporary))
            before = snapshot()
            store.save(before)
            with patch("unit_print_queue_recovery.os.fsync", side_effect=OSError("disk full")):
                with self.assertRaises(RecoveryWriteError):
                    store.save(snapshot(sequence=2))
            self.assertEqual(before, store.load())
            self.assertEqual([], list(Path(temporary).glob("*.tmp")))

    def test_replace_failure_preserves_old_candidate_and_cleans_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = RecoveryStore(Path(temporary))
            before = snapshot()
            store.save(before)
            with patch("unit_print_queue_recovery.os.replace", side_effect=OSError("sharing violation")):
                with self.assertRaises(RecoveryWriteError):
                    store.save(snapshot(sequence=2))
            self.assertEqual(before, store.load())
            self.assertEqual([], list(Path(temporary).glob("*.tmp")))

    def test_fsync_completes_before_atomic_replace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = RecoveryStore(Path(temporary))
            order = []
            original_replace = os.replace

            def replace(source, target):
                self.assertEqual(["fsync"], order)
                order.append("replace")
                original_replace(source, target)

            with patch("unit_print_queue_recovery.os.fsync", side_effect=lambda _fd: order.append("fsync")), patch("unit_print_queue_recovery.os.replace", side_effect=replace):
                store.save(snapshot())
            self.assertEqual(["fsync", "replace"], order)
            self.assertEqual(snapshot(), store.load())

    def test_corrupt_or_second_invalid_block_is_never_partially_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = RecoveryStore(Path(temporary))
            path = Path(temporary) / f"session-{SESSION_1}.json"
            payload = snapshot()
            invalid_entry = copy.deepcopy(payload["queue"][0])
            invalid_entry["block"]["history_id"] = "block-2"
            invalid_entry["block"]["copies"] = "3"
            payload["queue"].append(invalid_entry)
            bad_contents = [
                b"{\"schema_version\":1,",
                b"\xff\xfe",
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                json.dumps(snapshot()).replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1').encode(),
                json.dumps(snapshot()).replace('"copies": 3', '"copies": NaN').encode(),
            ]
            for content in bad_contents:
                with self.subTest(content=content[:30]):
                    path.write_bytes(content)
                    with self.assertRaises(RecoveryReadError):
                        store.load(path)

    def test_session_filename_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = RecoveryStore(Path(temporary))
            mismatched = Path(temporary) / f"session-{SESSION_2}.json"
            mismatched.write_text(json.dumps(snapshot()), encoding="utf-8")
            with self.assertRaises(RecoveryReadError):
                store.load(mismatched)

    def test_file_size_limit_blocks_both_save_and_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = RecoveryStore(Path(temporary))
            path = Path(temporary) / f"session-{SESSION_1}.json"
            store.save(snapshot())
            with patch("unit_print_queue_recovery.MAX_SNAPSHOT_BYTES", 32):
                with self.assertRaises(RecoveryValidationError):
                    store.save(snapshot())
                with self.assertRaises(RecoveryReadError):
                    store.load(path)


class RecoveryWriterTests(unittest.TestCase):
    def test_pending_writes_coalesce_without_mutating_submitted_payloads(self) -> None:
        started = threading.Event()
        release = threading.Event()
        calls = []

        class Store(RecoveryStore):
            def save(self, payload):
                calls.append(copy.deepcopy(payload))
                if len(calls) == 1:
                    started.set()
                    if not release.wait(2):
                        raise TimeoutError("test gate")
                return True

        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        writer = RecoveryWriter(Store(directory))
        try:
            first = snapshot(sequence=1)
            self.assertEqual(1, writer.submit(first))
            self.assertTrue(started.wait(1))
            first["queue"][0]["block"]["copies"] = 99
            self.assertEqual(2, writer.submit(snapshot(sequence=2)))
            third = snapshot(sequence=3)
            self.assertEqual(3, writer.submit(third))
            third["queue"][0]["block"]["files"][0]["path"] = "mutated.pdf"
            empty = snapshot(sequence=4)
            empty["queue"] = []
            self.assertEqual(3, writer.submit(empty))
            release.set()
            writer.flush(timeout=2)
            self.assertEqual(["snapshot-1", "snapshot-3"], [call["snapshot_id"] for call in calls])
            self.assertEqual(3, calls[0]["queue"][0]["block"]["copies"])
            self.assertEqual("D:/테스트/7.pdf", calls[1]["queue"][0]["block"]["files"][0]["path"])
        finally:
            release.set()
            writer.close(timeout=2)

    def test_close_flushes_last_queued_snapshot_and_rejects_later_submissions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = RecoveryStore(Path(temporary))
            writer = RecoveryWriter(store)
            latest = snapshot(sequence=4)
            writer.submit(snapshot(sequence=1))
            writer.submit(latest)
            writer.close(timeout=2)
            self.assertEqual(latest, store.load())
            with self.assertRaises(RuntimeError):
                writer.submit(snapshot())

    def test_error_is_visible_once_and_flush_cannot_claim_failed_save_succeeded(self) -> None:
        class Store(RecoveryStore):
            fail = True

            def save(self, payload):
                if self.fail:
                    raise OSError("disk full")
                return True

        store = Store(Path(self.enterContext(tempfile.TemporaryDirectory())))
        writer = RecoveryWriter(store)
        try:
            revision = writer.submit(snapshot())
            with self.assertRaises(RecoveryWriteError):
                writer.flush(revision, timeout=2)
            self.assertIsInstance(writer.pop_error(), RecoveryWriteError)
            self.assertIsNone(writer.pop_error())
            store.fail = False
            writer.save_required(snapshot(sequence=2), timeout=2)
            self.assertIsNone(writer.pop_error())
        finally:
            writer.close(timeout=2)

    def test_flush_timeout_does_not_cancel_eventual_snapshot_write(self) -> None:
        started = threading.Event()
        release = threading.Event()
        calls = []

        class Store(RecoveryStore):
            def save(self, payload):
                started.set()
                if not release.wait(2):
                    raise TimeoutError("test gate")
                calls.append(payload)
                return True

        writer = RecoveryWriter(Store(Path(self.enterContext(tempfile.TemporaryDirectory()))))
        try:
            revision = writer.submit(snapshot())
            self.assertTrue(started.wait(1))
            with self.assertRaises(TimeoutError):
                writer.flush(revision, timeout=0.001)
            release.set()
            writer.flush(revision, timeout=2)
            self.assertEqual(1, len(calls))
        finally:
            release.set()
            writer.close(timeout=2)

    def test_empty_launch_never_starts_writer_or_creates_a_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "recovery"
            writer = RecoveryWriter(RecoveryStore(directory))
            empty = snapshot()
            empty["queue"] = []
            self.assertEqual(0, writer.submit(empty))
            writer.close(timeout=1)
            self.assertFalse(directory.exists())

    def test_session_lease_remains_active_while_idle_and_close_releases_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = RecoveryStore(Path(temporary))
            writer = RecoveryWriter(store)
            try:
                writer.save_required(snapshot(), timeout=2)
                self.assertFalse(writer.busy)
                self.assertTrue(store.is_session_active(SESSION_1))
                with self.assertRaises(RecoverySessionActiveError):
                    with store.restore_guard(SESSION_1):
                        self.fail("active source was accepted")
            finally:
                writer.close(timeout=2)
            self.assertFalse(store.is_session_active(SESSION_1))
            with store.restore_guard(SESSION_1):
                self.assertEqual(snapshot(), store.load())

    def test_guard_holds_exclusion_until_manual_commit_and_writer_cannot_overwrite_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = RecoveryStore(Path(temporary))
            source = RecoveryWriter(store)
            source.save_required(snapshot(), timeout=2)
            source.close(timeout=2)
            with store.restore_guard(SESSION_1):
                self.assertTrue(store.is_session_active(SESSION_1))
                competing = RecoveryWriter(store)
                competing.submit(snapshot(sequence=2))
                with self.assertRaises(RecoveryWriteError):
                    competing.flush(timeout=2)
                with self.assertRaises(RecoveryWriteError):
                    competing.close(timeout=2)
                self.assertEqual("snapshot-1", store.load()["snapshot_id"])
            self.assertFalse(store.is_session_active(SESSION_1))

    def test_missing_lifetime_proof_fails_closed_without_creating_a_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "recovery"
            store = RecoveryStore(directory)
            with self.assertRaises(RecoveryReadError):
                store.is_session_active(SESSION_1)
            with self.assertRaises(RecoveryReadError):
                with store.restore_guard(SESSION_1):
                    self.fail("missing source lifetime proof was accepted")
            self.assertFalse(directory.exists())

    def test_writer_rejects_cross_session_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            writer = RecoveryWriter(RecoveryStore(Path(temporary)))
            try:
                writer.save_required(snapshot(), timeout=2)
                with self.assertRaises(ValueError):
                    writer.submit(snapshot(session=SESSION_2))
            finally:
                writer.close(timeout=2)

    def test_writer_can_borrow_own_guard_without_releasing_source_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = RecoveryStore(Path(temporary))
            writer = RecoveryWriter(store)
            try:
                writer.save_required(snapshot(), timeout=2)
                with writer.restore_guard(SESSION_1):
                    self.assertEqual(snapshot(), store.load())
                    self.assertTrue(store.is_session_active(SESSION_1))
                self.assertTrue(store.is_session_active(SESSION_1))
            finally:
                writer.close(timeout=2)
            self.assertFalse(store.is_session_active(SESSION_1))

    def test_writer_guard_refuses_other_running_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = RecoveryStore(Path(temporary))
            first = RecoveryWriter(store)
            second = RecoveryWriter(store)
            try:
                first.save_required(snapshot(session=SESSION_1), timeout=2)
                second.save_required(snapshot(session=SESSION_2), timeout=2)
                with self.assertRaises(RecoverySessionActiveError):
                    with second.restore_guard(SESSION_1):
                        self.fail("other live source was accepted")
                with second.restore_guard(SESSION_2):
                    self.assertTrue(store.is_session_active(SESSION_2))
            finally:
                first.close(timeout=2)
                second.close(timeout=2)

    def test_busy_covers_current_write_and_pending_work(self) -> None:
        started = threading.Event()
        release = threading.Event()

        class Store(RecoveryStore):
            def save(self, payload):
                started.set()
                if not release.wait(2):
                    raise TimeoutError("test gate")
                return True

        writer = RecoveryWriter(Store(Path(self.enterContext(tempfile.TemporaryDirectory()))))
        try:
            self.assertFalse(writer.busy)
            writer.submit(snapshot())
            self.assertTrue(started.wait(1))
            self.assertTrue(writer.busy)
            writer.submit(snapshot(sequence=2))
            self.assertTrue(writer.busy)
            release.set()
            writer.flush(timeout=2)
            self.assertFalse(writer.busy)
        finally:
            release.set()
            writer.close(timeout=2)

    def test_process_crash_releases_lifetime_lease_but_preserves_full_snapshot(self) -> None:
        script = """
import json, sys, time
from pathlib import Path
from unit_print_queue_recovery import RecoveryStore, RecoveryWriter
directory, payload_path, ready = map(Path, sys.argv[1:4])
writer = RecoveryWriter(RecoveryStore(directory))
writer.save_required(json.loads(payload_path.read_text(encoding='utf-8')))
ready.write_text('ready', encoding='utf-8')
time.sleep(30)
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "recovery"
            payload_path = root / "payload.json"
            payload_path.write_text(json.dumps(snapshot()), encoding="utf-8")
            ready = root / "ready"
            process = subprocess.Popen(
                [sys.executable, "-c", script, str(directory), str(payload_path), str(ready)],
                cwd=Path(__file__).resolve().parents[1],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
            )
            store = RecoveryStore(directory)
            try:
                deadline = time.monotonic() + 5
                while not ready.is_file():
                    if process.poll() is not None:
                        stdout, stderr = process.communicate(timeout=2)
                        self.fail(stdout + stderr)
                    if time.monotonic() >= deadline:
                        self.fail("source process did not publish a snapshot")
                    time.sleep(0.005)
                self.assertTrue(store.is_session_active(SESSION_1))
                with self.assertRaises(RecoverySessionActiveError):
                    with store.restore_guard(SESSION_1):
                        self.fail("active process was accepted")
                process.terminate()
                process.communicate(timeout=5)
                with store.restore_guard(SESSION_1):
                    self.assertEqual(snapshot(), store.load())
            finally:
                if process.poll() is None:
                    process.terminate()
                process.communicate(timeout=5)


class RecoveryConsumptionTests(unittest.TestCase):
    def publish(self, directory: Path) -> RecoveryStore:
        store = RecoveryStore(directory)
        writer = RecoveryWriter(store)
        writer.save_required(snapshot(), timeout=2)
        writer.close(timeout=2)
        return store

    def test_empty_consumption_receipt_is_durable_before_first_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = RecoveryStore(Path(temporary))
            original = store._atomic_write
            written = []

            def write(path, content):
                written.append(path.name)
                if path.name == f"session-{SESSION_1}.json":
                    self.assertEqual(frozenset(), store.consumed_block_ids(SESSION_1))
                original(path, content)

            writer = RecoveryWriter(store)
            try:
                with patch.object(store, "_atomic_write", side_effect=write):
                    writer.save_required(snapshot(), timeout=2)
                self.assertEqual([f"session-{SESSION_1}.consumed.json", f"session-{SESSION_1}.json"], written)
                self.assertEqual((store.directory / f"session-{SESSION_1}.json",), store.list_candidates())
            finally:
                writer.close(timeout=2)

    def test_consumed_ids_accumulate_and_are_never_reset_by_later_autosave(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = RecoveryStore(Path(temporary))
            writer = RecoveryWriter(store)
            try:
                writer.save_required(snapshot(), timeout=2)
                with writer.restore_guard(SESSION_1):
                    self.assertEqual(frozenset({"block-1"}), store.mark_recovered(SESSION_1, ["block-1"]))
                    self.assertEqual(frozenset({"block-1", "block-2"}), store.mark_recovered(SESSION_1, ["block-2"]))
                writer.save_required(snapshot(sequence=2), timeout=2)
                self.assertEqual(frozenset({"block-1", "block-2"}), store.consumed_block_ids(SESSION_1))
            finally:
                writer.close(timeout=2)
            restarted = RecoveryWriter(store)
            try:
                restarted.save_required(snapshot(sequence=3), timeout=2)
                self.assertEqual(frozenset({"block-1", "block-2"}), store.consumed_block_ids(SESSION_1))
            finally:
                restarted.close(timeout=2)

    def test_consumed_or_duplicate_selected_ids_refuse_second_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self.publish(Path(temporary))
            with store.restore_guard(SESSION_1):
                store.mark_recovered(SESSION_1, ["block-1"])
                with self.assertRaises(RecoveryReadError):
                    store.mark_recovered(SESSION_1, ["block-1", "block-2"])
                with self.assertRaises(RecoveryValidationError):
                    store.mark_recovered(SESSION_1, ["block-2", "block-2"])
                self.assertEqual(frozenset({"block-1"}), store.consumed_block_ids(SESSION_1))

    def test_missing_or_corrupt_existing_receipt_is_never_silently_initialized(self) -> None:
        for bad in (None, b"broken receipt"):
            with self.subTest(bad=bad):
                with tempfile.TemporaryDirectory() as temporary:
                    store = self.publish(Path(temporary))
                    receipt = store.directory / f"session-{SESSION_1}.consumed.json"
                    original = store.load()
                    if bad is None:
                        receipt.unlink()
                    else:
                        receipt.write_bytes(bad)
                    with self.assertRaises(RecoveryReadError):
                        store.consumed_block_ids(SESSION_1)
                    writer = RecoveryWriter(store)
                    with self.assertRaises(RecoveryWriteError):
                        writer.save_required(snapshot(sequence=2), timeout=2)
                    with self.assertRaises(RecoveryWriteError):
                        writer.close(timeout=2)
                    self.assertEqual(original, store.load())
                    self.assertEqual(bad, receipt.read_bytes() if receipt.exists() else None)

    def test_receipt_schema_session_and_all_ids_are_validated_before_any_mark(self) -> None:
        invalid = [
            {"schema_version": True, "session_id": SESSION_1, "block_ids": []},
            {"schema_version": 1, "session_id": SESSION_2, "block_ids": []},
            {"schema_version": 1, "session_id": SESSION_1, "block_ids": ["block-1", "block-1"]},
            {"schema_version": 1, "session_id": SESSION_1, "block_ids": ["block-1", 3]},
            {"schema_version": 1, "session_id": SESSION_1, "block_ids": [], "unexpected": 1},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            store = self.publish(Path(temporary))
            receipt = store.directory / f"session-{SESSION_1}.consumed.json"
            for payload in invalid:
                with self.subTest(payload=payload):
                    raw = json.dumps(payload).encode()
                    receipt.write_bytes(raw)
                    with store.restore_guard(SESSION_1):
                        with self.assertRaises(RecoveryReadError):
                            store.mark_recovered(SESSION_1, ["block-2"])
                    self.assertEqual(raw, receipt.read_bytes())

    def test_required_mark_fsync_failure_preserves_previous_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self.publish(Path(temporary))
            with store.restore_guard(SESSION_1):
                with patch("unit_print_queue_recovery.os.fsync", side_effect=OSError("disk full")):
                    with self.assertRaises(RecoveryWriteError):
                        store.mark_recovered(SESSION_1, ["block-1"])
                self.assertEqual(frozenset(), store.consumed_block_ids(SESSION_1))
            self.assertEqual([], list(store.directory.glob("*.tmp")))

    def test_guard_prevents_two_windows_consuming_the_same_source_before_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self.publish(Path(temporary))
            first_has_guard = threading.Event()
            allow_mark = threading.Event()
            errors = []

            def first_window():
                try:
                    with store.restore_guard(SESSION_1):
                        self.assertEqual(frozenset(), store.consumed_block_ids(SESSION_1))
                        first_has_guard.set()
                        if not allow_mark.wait(2):
                            raise TimeoutError("test gate")
                        store.mark_recovered(SESSION_1, ["block-1"])
                except BaseException as error:
                    errors.append(error)

            thread = threading.Thread(target=first_window)
            thread.start()
            try:
                self.assertTrue(first_has_guard.wait(1))
                with self.assertRaises(RecoverySessionActiveError):
                    with store.restore_guard(SESSION_1):
                        store.mark_recovered(SESSION_1, ["block-1"])
            finally:
                allow_mark.set()
                thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertEqual([], errors)
            with store.restore_guard(SESSION_1):
                self.assertEqual(frozenset({"block-1"}), store.consumed_block_ids(SESSION_1))
                with self.assertRaises(RecoveryReadError):
                    store.mark_recovered(SESSION_1, ["block-1"])


if __name__ == "__main__":
    unittest.main()
