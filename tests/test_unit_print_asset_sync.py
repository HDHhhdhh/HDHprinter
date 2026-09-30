from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.quick_printer_asset_sync import (
    AssetEntry,
    asset_set_sha256,
    build_publish_plan,
    build_pull_plan,
    load_asset_manifest,
    normalize_relative_path,
    sha256_file,
)


def entry(name: str, content: bytes, *, origin: str = "base") -> AssetEntry:
    import hashlib

    return AssetEntry(
        category="테스트",
        relative_path=f"data/테스트/{name}.pdf",
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        origin=origin,
    )


class QuickPrinterAssetSyncTests(unittest.TestCase):
    def test_publish_plan_uploads_only_new_and_changed_assets(self) -> None:
        baseline = (
            entry("keep", b"keep"),
            entry("change", b"old"),
            entry("remove", b"remove"),
        )
        current = (
            entry("keep", b"keep"),
            entry("change", b"new"),
            entry("add", b"add"),
        )

        plan = build_publish_plan(current, baseline, baseline)

        self.assertEqual(
            ("data/테스트/add.pdf", "data/테스트/change.pdf"),
            plan.upload_paths,
        )
        self.assertEqual(("data/테스트/add.pdf",), plan.added_paths)
        self.assertEqual(("data/테스트/change.pdf",), plan.changed_paths)
        self.assertEqual(("data/테스트/remove.pdf",), plan.removed_paths)
        origins = {item.relative_path: item.origin for item in plan.entries}
        self.assertEqual("base", origins["data/테스트/keep.pdf"])
        self.assertEqual("overlay", origins["data/테스트/change.pdf"])

    def test_unchanged_overlay_is_not_uploaded_again(self) -> None:
        baseline = (entry("keep", b"keep"), entry("change", b"old"))
        prior = (entry("keep", b"keep"), entry("change", b"new", origin="overlay"))

        plan = build_publish_plan(prior, prior, baseline)

        self.assertEqual((), plan.upload_paths)
        origins = {item.relative_path: item.origin for item in plan.entries}
        self.assertEqual("overlay", origins["data/테스트/change.pdf"])

    def test_reverting_to_baseline_uploads_downloadable_overlay(self) -> None:
        baseline = (entry("change", b"old"),)
        prior = (entry("change", b"new", origin="overlay"),)

        plan = build_publish_plan(baseline, prior, baseline)

        self.assertEqual(("data/테스트/change.pdf",), plan.upload_paths)
        self.assertEqual("overlay", plan.entries[0].origin)

    def test_unchanged_rollback_overlay_does_not_upload_again(self) -> None:
        baseline = (entry("change", b"old"),)
        prior = (entry("change", b"old", origin="overlay"),)

        plan = build_publish_plan(baseline, prior, baseline)

        self.assertEqual((), plan.upload_paths)
        self.assertEqual("overlay", plan.entries[0].origin)

    def test_legacy_base_manifest_is_repaired_using_remote_overlay_inventory(self) -> None:
        baseline = (entry("change", b"old"), entry("keep", b"keep"))

        plan = build_publish_plan(
            baseline, baseline, baseline,
            existing_overlay_paths=("DATA/테스트/CHANGE.PDF",),
        )

        self.assertEqual(("data/테스트/change.pdf",), plan.upload_paths)
        origins = {item.relative_path: item.origin for item in plan.entries}
        self.assertEqual("overlay", origins["data/테스트/change.pdf"])
        self.assertEqual("base", origins["data/테스트/keep.pdf"])

    def test_same_size_modified_base_fails_hash_check(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            baseline = entry("base", b"old")
            target = root / baseline.relative_path
            target.parent.mkdir(parents=True)
            target.write_bytes(b"new")

            plan = build_pull_plan(root, (baseline,))

            self.assertEqual((baseline.relative_path,), plan.missing_base_paths)
            self.assertEqual((), plan.download_paths)

    def test_matching_base_needs_no_download(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            baseline = entry("base", b"old")
            target = root / baseline.relative_path
            target.parent.mkdir(parents=True)
            target.write_bytes(b"old")

            plan = build_pull_plan(root, (baseline,))

            self.assertEqual((), plan.missing_base_paths)
            self.assertEqual((), plan.download_paths)

    def test_pull_plan_never_treats_missing_base_as_downloadable_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            matching = entry("matching", b"same", origin="overlay")
            missing_overlay = entry("download", b"new", origin="overlay")
            missing_base = entry("base", b"base", origin="base")
            matching_path = root / matching.relative_path
            matching_path.parent.mkdir(parents=True)
            matching_path.write_bytes(b"same")

            plan = build_pull_plan(root, (matching, missing_overlay, missing_base))

            self.assertEqual((missing_overlay.relative_path,), plan.download_paths)
            self.assertEqual((missing_base.relative_path,), plan.missing_base_paths)
            self.assertEqual((matching.relative_path,), plan.unchanged_overlay_paths)

    def test_manifest_digest_rejects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            original = entry("one", b"one", origin="overlay")
            manifest = {
                "schema_version": 1,
                "asset_set_sha256": asset_set_sha256((original,)),
                "assets": [original.to_json()],
            }
            path = root / "latest_assets.json"
            path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            self.assertEqual((original,), tuple(load_asset_manifest(path)["_entries"]))

            manifest["assets"][0]["size"] += 1
            path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                load_asset_manifest(path)

    def test_relative_path_rejects_parent_escape(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsafe asset path"):
            normalize_relative_path("../outside.pdf")

    def test_sha256_file_reads_binary_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "asset.pdf"
            path.write_bytes(b"pdf-content")
            self.assertEqual(entry("asset", b"pdf-content").sha256, sha256_file(path))


if __name__ == "__main__":
    unittest.main()
