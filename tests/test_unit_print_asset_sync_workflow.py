from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import quick_printer_asset_sync as sync


class LocalAssetChannel:
    """Exercise real publish/pull commands against a mocked local rclone remote."""

    relative_path = "data/test/lesson.pdf"
    baseline_content = b"old"

    def __init__(self, root: Path):
        self.root = root
        self.remote = root / "remote"
        self.remote.mkdir()
        self.installed = root / "installed"
        self.target = self.installed / self.relative_path
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(self.baseline_content)
        self.source = root / "publisher.pdf"
        self.source.write_bytes(self.baseline_content)
        self.baseline = self.entry(self.baseline_content)
        self.corrupt_download = False
        self.commands: list[list[str]] = []
        self.write_manifest("v0", self.baseline)

    def entry(self, content: bytes, origin: str = "base") -> sync.AssetEntry:
        return sync.AssetEntry(
            "test", self.relative_path, len(content),
            hashlib.sha256(content).hexdigest(), origin,
        )

    def write_manifest(self, version: str, entry: sync.AssetEntry) -> None:
        payload = sync.manifest_payload(
            version=version,
            baseline={"asset_set_sha256": sync.asset_set_sha256((self.baseline,))},
            baseline_entries=(self.baseline,),
            plan=sync.PublishPlan((entry,), (), (), (), ()),
        )
        sync.write_json_atomic(self.remote / "latest_assets.json", payload)

    def read_manifest(self) -> dict:
        return json.loads((self.remote / "latest_assets.json").read_text(encoding="utf-8"))

    def resolve(self, value: str) -> Path:
        prefix = "mock:assets"
        if value == prefix:
            return self.remote
        if value.startswith(prefix + "/"):
            return sync.safe_target(self.remote, value[len(prefix) + 1:])
        return Path(value)

    def run_rclone(self, executable, channel, arguments, *, check=True):
        self.commands.append(arguments)
        stdout = ""
        if arguments[0] == "lsf":
            stdout = "\n".join(
                path.relative_to(self.remote).as_posix()
                for path in sorted(self.remote.rglob("*")) if path.is_file()
            )
        elif arguments[0] == "copyto":
            source, destination = self.resolve(arguments[1]), self.resolve(arguments[2])
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        elif arguments[0] in {"copy", "check"}:
            source_root, destination_root = self.resolve(arguments[1]), self.resolve(arguments[2])
            file_list = Path(arguments[arguments.index("--files-from") + 1])
            for relative_path in file_list.read_text(encoding="utf-8").splitlines():
                source = sync.safe_target(source_root, relative_path)
                destination = sync.safe_target(destination_root, relative_path)
                if arguments[0] == "copy":
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
                    if source_root == self.remote and self.corrupt_download:
                        destination.write_bytes(b"corrupted download")
                else:
                    assert source.read_bytes() == destination.read_bytes()
        else:
            raise AssertionError(f"Unexpected rclone command: {arguments}")
        return subprocess.CompletedProcess(arguments, 0, stdout, "")

    @contextlib.contextmanager
    def mocked_remote(self):
        with (
            patch.object(sync, "load_channel_config", return_value={"remote": "mock:", "remote_prefix": "assets"}),
            patch.object(sync, "find_rclone", return_value=Path("mock-rclone.exe")),
            patch.object(sync, "run_rclone", side_effect=self.run_rclone),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            yield

    def publish(self, content: bytes, version: str) -> dict:
        self.source.write_bytes(content)
        args = argparse.Namespace(
            channel_config=self.root / "mock_config.json", config=self.root / "mock_app.json",
            hash_cache=self.root / "mock_cache.json", version=version,
            initialize=False, baseline_manifest=None, dry_run=False,
        )
        with self.mocked_remote(), patch.object(
            sync, "collect_current_assets",
            return_value=((self.entry(content),), {self.relative_path: self.source}),
        ):
            assert sync.command_publish(args) == 0
        return self.read_manifest()

    def pull(self) -> int:
        args = argparse.Namespace(
            root=self.installed, channel_config=self.root / "mock_config.json",
            state=None, dry_run=False,
        )
        with self.mocked_remote():
            return sync.command_pull(args)


class QuickPrinterAssetSyncWorkflowTests(unittest.TestCase):
    def test_registered_root_separator_remains_updateable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            channel = LocalAssetChannel(Path(temporary))
            channel.relative_path = "unit_print_separator_a4.pdf"

            channel.publish(b"updated separator", "v1-separator")
            self.assertEqual(0, channel.pull())

            self.assertEqual(
                b"updated separator",
                (channel.installed / channel.relative_path).read_bytes(),
            )

    def test_manifest_cannot_replace_app_files_or_use_windows_special_paths(self) -> None:
        for relative_path in (
            "unit_print_manager.py", "run_unit_print_manager.cmd",
            "scripts/install_quick_printer.ps1", "unit_print_manager_config.json",
            ".quick_printer_asset_manifest.json", "DATA/test/lesson.pdf",
            "data/test/code.py", "data/test/code.PY", "data/../unit_print_manager.py",
            "data/test/code.py:payload.pdf", "data/test./lesson.pdf",
            "data/test /lesson.pdf", "data/NUL.pdf", "data/test/\nlesson.pdf",
            "C:/data/lesson.pdf", "//server/share/data/lesson.pdf",
        ):
            with self.subTest(path=relative_path), tempfile.TemporaryDirectory() as temporary:
                channel = LocalAssetChannel(Path(temporary))
                channel.pull()
                app_source = channel.installed / "unit_print_manager.py"
                app_source.write_bytes(b"original app source")
                snapshots = {
                    path.relative_to(channel.installed): path.read_bytes()
                    for path in channel.installed.rglob("*") if path.is_file()
                }
                malicious = sync.AssetEntry(
                    "test", relative_path, 3, hashlib.sha256(b"new").hexdigest(), "overlay",
                )
                manifest = channel.read_manifest()
                manifest["assets"] = [malicious.to_json()]
                digest_entry = malicious.to_json()
                digest_entry.pop("origin")
                manifest["asset_set_sha256"] = sync.json_sha256({"assets": [digest_entry]})
                sync.write_json_atomic(channel.remote / "latest_assets.json", manifest)
                channel.commands.clear()

                with self.assertRaisesRegex(ValueError, "Unsafe .*asset path"):
                    channel.pull()

                self.assertEqual(snapshots, {
                    path.relative_to(channel.installed): path.read_bytes()
                    for path in channel.installed.rglob("*") if path.is_file()
                })
                self.assertFalse(any(command[0] == "copy" for command in channel.commands))

    def test_data_junction_into_app_directory_is_rejected_before_download(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            channel = LocalAssetChannel(Path(temporary))
            app_directory = channel.installed / "scripts"
            app_directory.mkdir()
            sentinel = app_directory / "lesson.pdf"
            sentinel.write_bytes(b"not an updateable asset")
            junction = channel.installed / "data" / "linked"
            if os.name == "nt":
                subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(junction), str(app_directory)],
                    capture_output=True, check=True,
                )
            else:
                junction.symlink_to(app_directory, target_is_directory=True)
            try:
                malicious = sync.AssetEntry(
                    "test", "data/linked/lesson.pdf", 3,
                    hashlib.sha256(b"new").hexdigest(), "overlay",
                )
                channel.write_manifest("linked", malicious)

                with self.assertRaisesRegex(ValueError, "escapes the data directory"):
                    channel.pull()

                self.assertEqual(b"not an updateable asset", sentinel.read_bytes())
                self.assertFalse(any(command[0] == "copy" for command in channel.commands))
            finally:
                if os.name == "nt":
                    junction.rmdir()
                else:
                    junction.unlink()

    def test_baseline_overlay_baseline_roundtrip_same_and_different_sizes(self) -> None:
        for updated_content in (b"new", b"new content with different size"):
            with self.subTest(updated_content=updated_content), tempfile.TemporaryDirectory() as temporary:
                channel = LocalAssetChannel(Path(temporary))
                extra = channel.installed / "operator-extra.pdf"
                extra.write_bytes(b"do not delete")
                channel.publish(updated_content, "v1-update")
                self.assertEqual(0, channel.pull())
                self.assertEqual(updated_content, channel.target.read_bytes())

                rollback = channel.publish(channel.baseline_content, "v2-rollback")
                self.assertEqual("overlay", rollback["assets"][0]["origin"])
                self.assertEqual([channel.relative_path], rollback["changes"]["uploaded"])
                self.assertEqual(0, channel.pull())
                self.assertEqual(channel.baseline_content, channel.target.read_bytes())
                state_path = channel.installed / sync.DEFAULT_STATE_FILE
                self.assertEqual("v2-rollback", json.loads(state_path.read_text())['version'])
                self.assertEqual(b"do not delete", extra.read_bytes())

                unchanged = channel.publish(channel.baseline_content, "v3-unchanged")
                self.assertEqual("overlay", unchanged["assets"][0]["origin"])
                self.assertEqual([], unchanged["changes"]["uploaded"])
                self.assertEqual(0, channel.pull())
                self.assertEqual([], json.loads(state_path.read_text())["downloaded_paths"])

    def test_republishing_legacy_base_manifest_repairs_clients_without_change_history(self) -> None:
        for updated_content in (b"new", b"longer updated bytes"):
            with self.subTest(updated_content=updated_content), tempfile.TemporaryDirectory() as temporary:
                channel = LocalAssetChannel(Path(temporary))
                channel.publish(updated_content, "v1-update")
                channel.pull()
                # Legacy rollback and subsequent no-change publish forgot the
                # origin and changed-path history; its remote overlay survives.
                channel.write_manifest("legacy-base-with-empty-changes", channel.baseline)

                repaired = channel.publish(channel.baseline_content, "v2-repair")

                self.assertEqual("overlay", repaired["assets"][0]["origin"])
                self.assertEqual([channel.relative_path], repaired["changes"]["uploaded"])
                self.assertEqual(0, channel.pull())
                self.assertEqual(channel.baseline_content, channel.target.read_bytes())

    def test_base_mismatch_cannot_advance_existing_state_or_manifest(self) -> None:
        for modified_content in (b"new", b"longer", None):
            with self.subTest(modified_content=modified_content), tempfile.TemporaryDirectory() as temporary:
                channel = LocalAssetChannel(Path(temporary))
                channel.pull()
                snapshots = {
                    name: (channel.installed / name).read_bytes()
                    for name in (sync.DEFAULT_STATE_FILE, sync.DEFAULT_LOCAL_MANIFEST)
                }
                channel.write_manifest("v1-base", channel.baseline)
                if modified_content is None:
                    channel.target.unlink()
                else:
                    channel.target.write_bytes(modified_content)

                with self.assertRaisesRegex(FileNotFoundError, "SHA-256 mismatch"):
                    channel.pull()

                for name, original in snapshots.items():
                    self.assertEqual(original, (channel.installed / name).read_bytes())
                self.assertFalse(any(command[0] == "copy" for command in channel.commands))

    def test_bad_rollback_download_does_not_replace_pdf_or_advance_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            channel = LocalAssetChannel(Path(temporary))
            channel.publish(b"new", "v1-update")
            channel.pull()
            snapshots = {
                name: (channel.installed / name).read_bytes()
                for name in (sync.DEFAULT_STATE_FILE, sync.DEFAULT_LOCAL_MANIFEST)
            }
            channel.publish(channel.baseline_content, "v2-rollback")
            channel.corrupt_download = True

            with self.assertRaisesRegex(ValueError, "failed SHA-256 verification"):
                channel.pull()

            self.assertEqual(b"new", channel.target.read_bytes())
            for name, original in snapshots.items():
                self.assertEqual(original, (channel.installed / name).read_bytes())

    def test_untouched_base_has_no_overlay_folder_and_needs_no_upload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            channel = LocalAssetChannel(Path(temporary))

            manifest = channel.publish(channel.baseline_content, "v1-unchanged-base")

            self.assertEqual("base", manifest["assets"][0]["origin"])
            self.assertEqual([], manifest["changes"]["uploaded"])
            self.assertFalse((channel.remote / "overlay").exists())

    def test_overlay_inventory_failure_does_not_publish_an_unsafe_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            channel = LocalAssetChannel(Path(temporary))
            channel.publish(b"new", "v1-update")
            channel.write_manifest("legacy-base", channel.baseline)
            original_manifest = (channel.remote / "latest_assets.json").read_bytes()
            original_overlay = (channel.remote / "overlay" / channel.relative_path).read_bytes()
            run_rclone = channel.run_rclone

            def fail_inventory(executable, config, arguments, *, check=True):
                if arguments[0] == "lsf":
                    raise RuntimeError("Mock remote inventory failure")
                return run_rclone(executable, config, arguments, check=check)

            with patch.object(channel, "run_rclone", side_effect=fail_inventory):
                with self.assertRaisesRegex(RuntimeError, "inventory failure"):
                    channel.publish(channel.baseline_content, "v2-repair")

            self.assertEqual(original_manifest, (channel.remote / "latest_assets.json").read_bytes())
            self.assertEqual(original_overlay, (channel.remote / "overlay" / channel.relative_path).read_bytes())


if __name__ == "__main__":
    unittest.main()
