"""Config I/O regressions without importing optional GUI/PDF dependencies."""

from __future__ import annotations

import ast
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import unit_print_bootstrap as bootstrap


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.directory / "unit_print_manager_config.json"
        tree = ast.parse((Path(__file__).resolve().parents[1] / "unit_print_manager.py").read_text(encoding="utf-8"))
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in ("load_config", "save_config")]
        namespace = dict(json=json, os=os, shutil=shutil, tempfile=tempfile,
                         datetime=datetime, Path=Path, CONFIG_PATH=self.path)
        exec(compile(ast.Module(body=functions, type_ignores=[]), "config_functions", "exec"), namespace)
        self.load = namespace["load_config"]
        self.save = namespace["save_config"]
        self.notice = self.enterContext(patch.object(bootstrap, "show_notice"))

    def test_missing_and_valid_config_do_not_notify(self):
        self.assertEqual({}, self.load())
        config = {"printer": "TEST", "device_monitor": {"enabled": False}}
        self.save(config)
        self.assertEqual(config, self.load())
        self.notice.assert_not_called()
        self.assertEqual([self.path], list(self.directory.iterdir()))

    def test_corrupt_config_is_preserved_before_defaults_and_notice(self):
        for content in (b'{"printer":', b"\xff"):
            with self.subTest(content=content):
                self.path.write_bytes(content)
                report = Mock()
                self.assertEqual({}, self.load(startup_report=report))
                report.assert_called_once_with("config_corrupt_backed_up")
                backups = list(self.directory.glob("*.corrupt-*.json"))
                self.assertTrue(any(path.read_bytes() == content for path in backups))
                self.assertEqual(content, self.path.read_bytes())
                self.notice.assert_called()

    def test_failed_backup_preserves_original_and_does_not_return_defaults(self):
        self.path.write_bytes(b"{")
        report = Mock()
        with patch.object(shutil, "copy2", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.load(startup_report=report)
        self.assertEqual(b"{", self.path.read_bytes())
        report.assert_called_once_with("config_corrupt_backup_failed")

    def test_failed_flush_or_replace_keeps_previous_config_and_cleans_temp(self):
        self.save({"printer": "old"})
        original = self.path.read_bytes()
        for operation in ("fsync", "replace"):
            with self.subTest(operation=operation), patch.object(os, operation, side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    self.save({"printer": "new"})
            self.assertEqual(original, self.path.read_bytes())
            self.assertEqual([self.path], list(self.directory.iterdir()))

    def test_replace_observes_fsynced_complete_temp_in_same_directory(self):
        replace = os.replace
        fsync = os.fsync
        events = []

        def synced(fd):
            fsync(fd)
            events.append("synced")

        def publish(source, target):
            self.assertEqual(["synced"], events)
            self.assertEqual(self.directory, source.parent)
            self.assertEqual({"printer": "new"}, json.loads(source.read_text(encoding="utf-8")))
            replace(source, target)

        with patch.object(os, "fsync", side_effect=synced), patch.object(os, "replace", side_effect=publish):
            self.save({"printer": "new"})


if __name__ == "__main__":
    unittest.main()
