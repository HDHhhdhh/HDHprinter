from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import build_quick_printer_release as release_builder
from scripts.build_quick_printer_release import (
    APP_FILES,
    collect_immutable_release_files,
    extract_drive_folder_id,
    render_private_release_templates,
)
from scripts.quick_printer_asset_sync import AssetEntry, asset_set_sha256


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = PROJECT_ROOT / "scripts" / "verify_quick_printer_release.py"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ReleaseManifestTests(unittest.TestCase):
    def test_print_history_and_queue_modules_ship_without_runtime_history(self) -> None:
        for name in (
            "unit_print_queue_state.py",
            "unit_print_history.py",
            "unit_print_history_events.py",
            "unit_print_progress.py",
            "unit_print_preparation.py",
        ):
            self.assertIn(name, APP_FILES)
            self.assertTrue((PROJECT_ROOT / name).is_file())

    def test_environment_and_preparation_tools_ship_with_the_app(self) -> None:
        for name in (
            "scripts/verify_quick_printer_environment.py",
            "scripts/benchmark_quick_printer_preparation.py",
        ):
            self.assertIn(name, APP_FILES)
            self.assertTrue((PROJECT_ROOT / name).is_file())
        self.assertFalse(any("/history/" in path or path.endswith(".jsonl") for path in APP_FILES))

    def test_textbook_catalog_and_loader_ship_with_the_app(self) -> None:
        for name in ("unit_print_textbooks.py", "unit_print_textbook_catalog.json"):
            self.assertIn(name, APP_FILES)
            self.assertTrue((PROJECT_ROOT / name).is_file())

    def test_device_monitor_modules_ship_with_the_app(self) -> None:
        modules = (
            "unit_print_device_state.py",
            "unit_print_canon_status.py",
            "unit_print_device_credentials.py",
            "unit_print_device_monitor.py",
            "unit_print_dispatch_state.py",
            "unit_print_device_scheduler.py",
            "unit_print_device_ui.py",
            "unit_print_eta.py",
            "unit_print_resource_estimate.py",
            "unit_print_notifications.py",
            "unit_print_taskbar.py",
        )
        for name in modules:
            self.assertIn(name, APP_FILES)
            self.assertTrue((PROJECT_ROOT / name).is_file())

    def test_portable_device_monitor_defaults_are_secret_free(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            release_builder.write_portable_config(root)
            config = json.loads((root / "unit_print_manager_config.json").read_text(encoding="utf-8"))
        monitor = config["device_monitor"]
        self.assertFalse(monitor["enabled"])
        self.assertEqual(6, monitor["dispatch_window_size"])
        self.assertEqual(2, monitor["dispatch_policy_version"])
        self.assertNotIn("pin", json.dumps(config).casefold())

    def test_textbook_release_skips_whole_deferred_editions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            problem = SimpleNamespace(
                path=root / "ready-problem.pdf",
                relative_path=Path("공수1/공수1 준비 문제.pdf"),
            )
            solution = SimpleNamespace(
                path=root / "ready-solution.pdf",
                relative_path=Path("공수1/공수1 준비 해설.pdf"),
            )
            core = SimpleNamespace(
                path=root / "ready-core.pdf",
                relative_path=Path("공수1/공수1 준비 핵심문제.pdf"),
            )
            variant = SimpleNamespace(
                path=root / "ready-variant.pdf",
                relative_path=Path("공수1/공수1 준비 핵심문제 변형.pdf"),
            )
            ready = Mock()
            ready.variant = variant
            ready.asset.side_effect = {
                "핵심문제": core, "문제": problem, "해설": solution
            }.__getitem__
            deferred = SimpleNamespace(
                subject="기하", publisher="천재(전인태)", is_verified=False
            )
            catalog = SimpleNamespace(
                issues=(),
                verified_editions=(ready,),
                subjects=(SimpleNamespace(editions=(ready, deferred)),),
            )

            with (
                patch.object(release_builder, "load_textbook_catalog", return_value=catalog),
                patch.object(release_builder, "verify_textbook_asset") as verify,
            ):
                sources = release_builder.collect_textbook_sources()

            self.assertEqual(4, verify.call_count)
            self.assertEqual(
                [
                    "data/교과서/공수1/공수1 준비 핵심문제.pdf",
                    "data/교과서/공수1/공수1 준비 문제.pdf",
                    "data/교과서/공수1/공수1 준비 해설.pdf",
                    "data/교과서/공수1/공수1 준비 핵심문제 변형.pdf",
                ],
                [source.relative_path.as_posix() for source in sources],
            )

    def test_private_release_credentials_are_rendered_only_in_release_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for name in (
                "빠른인쇄기_새PC_설치_인수검사_프롬프트.md",
                "빠른인쇄기_프린터_ComputerUse_설정검증.md",
            ):
                (root / name).write_text(
                    "ID={{CANON_DEPARTMENT_ID}} PIN={{CANON_DEPARTMENT_PIN}}",
                    encoding="utf-8",
                )

            render_private_release_templates(root, "private-id", "private-pin")

            for name in (
                "빠른인쇄기_새PC_설치_인수검사_프롬프트.md",
                "빠른인쇄기_프린터_ComputerUse_설정검증.md",
            ):
                self.assertEqual(
                    "ID=private-id PIN=private-pin",
                    (root / name).read_text(encoding="utf-8"),
                )

    def test_drive_folder_id_is_extracted_without_a_hardcoded_private_link(self) -> None:
        self.assertEqual(
            "folder_123-ABC",
            extract_drive_folder_id("https://drive.google.com/drive/folders/folder_123-ABC"),
        )
        self.assertEqual(
            "folder_123-ABC",
            extract_drive_folder_id("https://drive.google.com/open?id=folder_123-ABC"),
        )

    def test_immutable_inventory_skips_runtime_state_and_pdfs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "app.py").write_bytes(b"print('ok')\n")
            (root / "document.pdf").write_bytes(b"pdf")
            (root / "unit_print_manager_config.json").write_bytes(b"{}")
            (root / "unit_print_material_catalog.json").write_bytes(b"{}")
            (root / "release_manifest.json").write_bytes(b"{}")

            entries = collect_immutable_release_files(root)

            self.assertEqual([entry.relative_path for entry in entries], ["app.py"])
            self.assertEqual(entries[0].sha256, digest(b"print('ok')\n"))

    def test_fast_verification_still_rejects_modified_app_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            app_data = b"verified app\n"
            pdf_data = b"registered pdf"
            (root / "app.py").write_bytes(app_data)
            (root / "data.pdf").write_bytes(pdf_data)
            manifest = {
                "schema_version": 2,
                "immutable_file_count": 1,
                "immutable_file_bytes": len(app_data),
                "immutable_files": [
                    {
                        "relative_path": "app.py",
                        "size": len(app_data),
                        "sha256": digest(app_data),
                    }
                ],
                "registered_pdf_count": 1,
                "registered_pdf_bytes": len(pdf_data),
                "pdfs": [
                    {
                        "relative_path": "data.pdf",
                        "size": len(pdf_data),
                        "sha256": digest(pdf_data),
                    }
                ],
            }
            (root / "release_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            passing = subprocess.run(
                [sys.executable, str(VERIFY_SCRIPT), "--root", str(root), "--fast"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(passing.returncode, 0, passing.stdout + passing.stderr)

            (root / "app.py").write_bytes(b"tampered app\n")
            failing = subprocess.run(
                [sys.executable, str(VERIFY_SCRIPT), "--root", str(root), "--fast"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(failing.returncode, 1)
            self.assertIn("immutable", failing.stdout)

    def test_asset_manifest_allows_incremental_pdf_inventory_and_keeps_extra_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            app_data = b"verified app\n"
            current_pdf = b"new registered pdf"
            extra_pdf = b"local extra"
            (root / "app.py").write_bytes(app_data)
            (root / "data.pdf").write_bytes(current_pdf)
            (root / "leftover.pdf").write_bytes(extra_pdf)
            release_manifest = {
                "schema_version": 2,
                "immutable_file_count": 1,
                "immutable_file_bytes": len(app_data),
                "immutable_files": [
                    {
                        "relative_path": "app.py",
                        "size": len(app_data),
                        "sha256": digest(app_data),
                    }
                ],
                "registered_pdf_count": 1,
                "registered_pdf_bytes": 3,
                "pdfs": [
                    {
                        "category": "old",
                        "relative_path": "old.pdf",
                        "size": 3,
                        "sha256": digest(b"old"),
                    }
                ],
            }
            (root / "release_manifest.json").write_text(
                json.dumps(release_manifest), encoding="utf-8"
            )
            current_entry = AssetEntry(
                category="new",
                relative_path="data.pdf",
                size=len(current_pdf),
                sha256=digest(current_pdf),
                origin="overlay",
            )
            asset_manifest = {
                "schema_version": 1,
                "asset_count": 1,
                "asset_bytes": len(current_pdf),
                "asset_set_sha256": asset_set_sha256((current_entry,)),
                "assets": [current_entry.to_json()],
            }
            (root / ".quick_printer_asset_manifest.json").write_text(
                json.dumps(asset_manifest), encoding="utf-8"
            )

            result = subprocess.run(
                [sys.executable, str(VERIFY_SCRIPT), "--root", str(root)],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("warnings", result.stdout)
            self.assertIn("leftover.pdf", result.stdout)


if __name__ == "__main__":
    unittest.main()
