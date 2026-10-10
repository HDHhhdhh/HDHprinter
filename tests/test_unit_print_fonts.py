from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import build_quick_printer_release as release
from scripts import verify_quick_printer_fonts as fonts


class FontVerificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        directory = self.root / "fonts" / "Pretendard"
        directory.mkdir(parents=True)
        self.ttf = directory / "Pretendard-Regular.ttf"
        self.ttf.write_bytes(b"synthetic-font")
        license_path = directory / "LICENSE.txt"
        license_path.write_bytes(b"synthetic-license")
        self.manifest = {
            "schema_version": 1, "expected_ui_family": "Pretendard",
            "license_path": "fonts/Pretendard/LICENSE.txt",
            "license_sha256": hashlib.sha256(license_path.read_bytes()).hexdigest(),
            "files": [{"relative_path": "fonts/Pretendard/Pretendard-Regular.ttf",
                       "filename": self.ttf.name, "size": self.ttf.stat().st_size,
                       "sha256": hashlib.sha256(self.ttf.read_bytes()).hexdigest()}],
        }
        self.save_manifest()

    def save_manifest(self):
        (self.root / fonts.FONT_MANIFEST).write_text(json.dumps(self.manifest), encoding="utf-8")

    def test_bundle_requires_exact_font_bytes_and_original_license(self):
        _manifest, issues = fonts.verify_bundled_fonts(self.root)
        self.assertEqual([], issues)
        self.ttf.write_bytes(b"changed-font")
        (self.root / self.manifest["license_path"]).unlink()
        _manifest, issues = fonts.verify_bundled_fonts(self.root)
        self.assertEqual(2, len(issues))
        self.assertTrue(any("배포 글꼴 해시 불일치" in issue for issue in issues))
        self.assertTrue(any("라이선스" in issue for issue in issues))

    def test_manifest_cannot_read_files_outside_the_font_bundle(self):
        self.manifest["files"][0]["relative_path"] = "fonts/../../outside.ttf"
        self.save_manifest()
        with self.assertRaises(ValueError):
            fonts.verify_bundled_fonts(self.root)

    def test_missing_font_and_same_name_installed_conflict_fail_without_mutation(self):
        self.assertTrue(fonts.verify_installed_fonts(self.manifest, ()))
        other = self.root / self.ttf.name
        other.write_bytes(b"different-installed-font")
        before = other.read_bytes()
        issues = fonts.verify_installed_fonts(self.manifest, (self.ttf, other))
        self.assertEqual(1, len(issues))
        self.assertIn("설치 글꼴 해시 불일치", issues[0])
        self.assertEqual(before, other.read_bytes())

    def test_duplicate_identical_registrations_are_accepted_and_extra_faces_are_reported(self):
        self.assertEqual([], fonts.verify_installed_fonts(self.manifest, (self.ttf, self.ttf)))
        extra = self.ttf.parent / "Pretendard-Black.ttf"
        extra.touch()
        self.assertTrue(fonts.verify_installed_fonts(self.manifest, (self.ttf, extra)))

    def test_probe_uses_actual_app_family_and_always_destroys_hidden_root(self):
        root = Mock()
        root.tk.call.side_effect = lambda *args: "8.6.15" if args[0] == "info" else 1.3333333333333333
        root.winfo_screenwidth.return_value = 1440
        root.winfo_screenheight.return_value = 900
        factory = Mock(side_effect=lambda **kwargs: Mock(actual=Mock(return_value={
            "family": kwargs["family"], "weight": kwargs["weight"], "size": kwargs["size"]})))
        with patch("unit_print_manager.tkfont.families", return_value=("Yu Gothic UI", "Pretendard")) as family_list:
            report = fonts.probe_tk_font_environment(root_factory=lambda: root, font_factory=factory)
        self.assertEqual("Pretendard", report["ui_font_family"])
        self.assertEqual(["normal", "bold"], [call.kwargs["weight"] for call in factory.call_args_list])
        root.withdraw.assert_called_once_with()
        root.destroy.assert_called_once_with()
        family_list.assert_called_once_with(root)

    def test_probe_error_destroys_root_without_creating_app(self):
        root = Mock()
        resolver = Mock(side_effect=RuntimeError("synthetic error"))
        with self.assertRaises(RuntimeError):
            fonts.probe_tk_font_environment(root_factory=lambda: root, family_resolver=resolver)
        root.destroy.assert_called_once_with()

    def test_cli_rejects_actual_tk_fallback_even_when_installed_hashes_match(self):
        runtime = {"ui_font_family": "Pretendard", "actual_faces": {
            "normal": {"family": "Malgun Gothic"}, "bold": {"family": "Pretendard"}}}
        with (patch.object(sys, "argv", ["verify_fonts", "--root", str(self.root)]),
              patch.object(fonts, "installed_pretendard_paths", return_value=(self.ttf,)),
              patch.object(fonts, "probe_tk_font_environment", return_value=runtime),
              patch("builtins.print") as output):
            self.assertEqual(1, fonts.main())
        self.assertTrue(json.loads(output.call_args.args[0])["issues"])

    def test_bundle_only_does_not_inspect_windows_or_create_tk_root(self):
        with (patch.object(sys, "argv", ["verify_fonts", "--root", str(self.root), "--bundle-only"]),
              patch.object(fonts, "installed_pretendard_paths") as installed,
              patch.object(fonts, "probe_tk_font_environment") as probe,
              patch("builtins.print")):
            self.assertEqual(0, fonts.main())
        installed.assert_not_called()
        probe.assert_not_called()


class FontReleaseInstructionTests(unittest.TestCase):
    def test_release_includes_four_current_faces_and_license_without_windows_fonts(self):
        expected = {"fonts/Pretendard/Pretendard-Regular.ttf", "fonts/Pretendard/Pretendard-Bold.ttf",
                    "fonts/Pretendard/Pretendard-Medium.ttf", "fonts/Pretendard/Pretendard-SemiBold.ttf",
                    "fonts/Pretendard/LICENSE.txt", "fonts/quick_printer_fonts.json", "fonts/README.md",
                    "scripts/verify_quick_printer_fonts.py"}
        self.assertTrue(expected <= set(release.APP_FILES))
        self.assertEqual({name for name in expected if name.endswith(".ttf")},
                         {name for name in release.APP_FILES if name.endswith(".ttf")})
        manifest, issues = fonts.verify_bundled_fonts(release.PROJECT_ROOT)
        self.assertEqual(4, len(manifest["files"]))
        self.assertEqual([], issues)

    def test_generated_guides_install_exact_fonts_before_skip_sync_install_and_full_verify(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release.write_release_docs(root, {"synthetic": 1}, 1, "manual-input", "manual-input")
            handoff = root / "handoff.md"
            release.write_codex_handoff(handoff, release_name="synthetic", archive_name="synthetic.zip",
                                       archive_sha256="0" * 64, checksum_name="synthetic.zip.sha256", archive_bytes=1,
                                       counts={"synthetic": 1}, registered_pdf_bytes=1, immutable_file_count=1,
                                       drive_folder_url="https://drive.google.com/drive/folders/download_only",
                                       asset_drive_folder_url="https://drive.google.com/drive/folders/asset_only",
                                       department_id="manual-input", department_pin="manual-input")
            for guide in (root / "빠른인쇄기_설치_가이드.md", handoff):
                text = guide.read_text(encoding="utf-8")
                self.assertLess(text.index("### 정본 글꼴 설치와 확인"), text.index("-SkipAssetSync"))
                self.assertIn("네 TTF 파일만", text)
                self.assertIn("scripts\\verify_quick_printer_fonts.py", text)
                self.assertIn("verify_quick_printer_release.py", text)
                self.assertIn("전체 SHA-256", text)
            self.assertIn("download_only", handoff.read_text(encoding="utf-8"))
            self.assertIn("asset_only", handoff.read_text(encoding="utf-8"))

    def test_optional_asset_channel_preserves_legacy_parser_default(self):
        base = ["builder", "--destination", "new", "--archive", "new.zip", "--drive-folder-url", "download_only"]
        with patch.object(sys, "argv", base):
            self.assertIsNone(release.parse_args().asset_drive_folder_url)
        with patch.object(sys, "argv", base + ["--asset-drive-folder-url", "asset_only"]):
            self.assertEqual("asset_only", release.parse_args().asset_drive_folder_url)

    def test_builder_rejects_modified_font_before_creating_release_or_reading_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = SimpleNamespace(destination=root / "new", archive=root / "new.zip")
            with (patch.object(release, "parse_args", return_value=args),
                  patch.object(release, "verify_bundled_fonts", return_value=({}, ["synthetic hash mismatch"])),
                  patch.object(release, "load_json") as config):
                with self.assertRaises(ValueError):
                    release.main()
            config.assert_not_called()
            self.assertFalse(args.destination.exists())
            self.assertFalse(args.archive.exists())

    def test_production_main_writes_separate_update_channel_or_legacy_fallback(self):
        for asset_url in ("asset_only", None):
            with self.subTest(asset_url=asset_url), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                args = SimpleNamespace(destination=root / "release", archive=root / "release.zip",
                                       reference_gui=None, department_id="manual-input", department_pin="manual-input",
                                       drive_folder_url="download_only", asset_drive_folder_url=asset_url)
                def synthetic_archive(_source, path):
                    path.write_bytes(b"synthetic-archive")
                    return "0" * 64
                with (patch.object(release, "parse_args", return_value=args),
                      patch.object(release, "load_json", return_value={}),
                      patch.object(release, "collect_registered_pdfs", return_value=[]),
                      patch.object(release, "copy_app_files"), patch.object(release, "render_private_release_templates"),
                      patch.object(release, "write_portable_config"),
                      patch.object(release, "write_portable_asset_channel") as channel,
                      patch.object(release, "write_portable_apex_cache", return_value=0),
                      patch.object(release, "write_release_docs"),
                      patch.object(release, "collect_immutable_release_files", return_value=[]),
                      patch.object(release, "build_archive", side_effect=synthetic_archive),
                      patch.object(release, "write_codex_handoff") as handoff,
                      patch("builtins.print")):
                    self.assertEqual(0, release.main())
                channel.assert_called_once_with(args.destination, asset_url or "download_only")
                self.assertEqual("download_only", handoff.call_args.kwargs["drive_folder_url"])
                self.assertEqual(asset_url or "download_only", handoff.call_args.kwargs["asset_drive_folder_url"])


if __name__ == "__main__":
    unittest.main()
