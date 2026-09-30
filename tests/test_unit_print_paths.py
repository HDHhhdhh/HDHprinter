from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from unit_print_paths import RELEASE_ROOT_ENV, resolve_release_root, select_configured_root


class QuickPrinterPathTests(unittest.TestCase):
    def test_app_directory_is_default_release_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app_dir = Path(temporary)
            self.assertEqual(resolve_release_root(app_dir=app_dir, environ={}), app_dir)

    def test_local_config_selects_external_release_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app_dir = Path(temporary)
            configured = app_dir / "local-assets"
            config_path = app_dir / "quick_printer_local_paths.json"
            config_path.write_text(
                json.dumps({"release_root": str(configured)}), encoding="utf-8"
            )
            self.assertEqual(
                resolve_release_root(
                    app_dir=app_dir, config_path=config_path, environ={}
                ),
                configured,
            )

    def test_environment_override_wins_over_local_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app_dir = Path(temporary)
            config_path = app_dir / "quick_printer_local_paths.json"
            config_path.write_text(
                json.dumps({"release_root": str(app_dir / "config")}),
                encoding="utf-8",
            )
            environment_root = app_dir / "environment"
            self.assertEqual(
                resolve_release_root(
                    app_dir=app_dir,
                    config_path=config_path,
                    environ={RELEASE_ROOT_ENV: str(environment_root)},
                ),
                environment_root,
            )

    def test_legacy_saved_root_migrates_to_available_portable_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            portable = base / "portable" / "단원평가"
            portable.mkdir(parents=True)
            legacy = base / "legacy" / "단원평가"
            legacy.mkdir(parents=True)
            self.assertEqual(
                select_configured_root(
                    str(legacy), portable_root=portable, legacy_root=legacy
                ),
                portable,
            )

    def test_existing_custom_root_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            portable = base / "portable" / "단원평가"
            custom = base / "custom"
            portable.mkdir(parents=True)
            custom.mkdir()
            self.assertEqual(
                select_configured_root(
                    str(custom),
                    portable_root=portable,
                    legacy_root=base / "legacy",
                ),
                custom,
            )


if __name__ == "__main__":
    unittest.main()
