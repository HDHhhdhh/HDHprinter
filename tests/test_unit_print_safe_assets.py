from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter

from scripts import quick_printer_safe_assets as assets


class VerifiedAssetCopyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "original.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        writer.add_blank_page(width=595, height=842)
        with self.source.open("wb") as stream:
            writer.write(stream)
        self.original = self.source.read_bytes()
        self.spec = assets.AssetSpec(
            "교과서", self.source, "공수2/YBM/핵심문제.pdf",
            len(self.original), hashlib.sha256(self.original).hexdigest(), 2,
        )
        self.allowed = self.root / "safe"
        self.final = self.allowed / "data"

    def publish(self, specs=None, **kwargs):
        return assets.publish_verified_assets(
            specs or [self.spec], self.final, allowed_parent=self.allowed, **kwargs,
        )

    def test_success_publishes_all_verified_files_together_and_preserves_original(self) -> None:
        second = replace(self.spec, relative_path="기하/미래엔/문제.pdf")
        visible = []
        result = self.publish(
            [self.spec, second],
            progress=lambda *_: visible.append(self.final.exists()),
        )
        self.assertEqual([False, False], visible)
        self.assertEqual(2, result["file_count"])
        self.assertEqual("verified", result["status"])
        self.assertEqual(self.original, self.source.read_bytes())
        self.assertEqual(self.original, (self.final / self.spec.relative_path).read_bytes())
        saved = json.loads((self.final / assets.MANIFEST_NAME).read_text(encoding="utf-8"))
        self.assertEqual(self.spec.sha256, saved["files"][0]["sha256"])
        self.assertEqual(2, saved["files"][0]["page_count"])

    @unittest.skipUnless(os.name == "nt", "Windows directory ACL regression")
    def test_published_directory_inherits_parent_windows_acl(self) -> None:
        import ctypes
        from ctypes import wintypes
        self.publish()
        security = ctypes.WinDLL("advapi32", use_last_error=True)
        get_named = security.GetNamedSecurityInfoW
        get_named.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
                              ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        get_named.restype = wintypes.DWORD
        descriptor = ctypes.c_void_p()
        self.assertEqual(0, get_named(str(self.final), 1, 4, None, None, None, None,
                                     ctypes.byref(descriptor)))
        try:
            control = wintypes.WORD()
            revision = wintypes.DWORD()
            get_control = security.GetSecurityDescriptorControl
            get_control.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.WORD),
                                    ctypes.POINTER(wintypes.DWORD)]
            get_control.restype = wintypes.BOOL
            self.assertTrue(get_control(descriptor, ctypes.byref(control), ctypes.byref(revision)))
            self.assertFalse(control.value & 0x1000)  # SE_DACL_PROTECTED
        finally:
            local_free = ctypes.WinDLL("kernel32").LocalFree
            local_free.argtypes = [ctypes.c_void_p]
            local_free.restype = ctypes.c_void_p
            local_free(descriptor)

    def test_hash_mismatch_keeps_original_and_never_creates_final(self) -> None:
        with self.assertRaisesRegex(assets.AssetCopyError, "원본 해시"):
            self.publish([replace(self.spec, sha256="0" * 64)])
        self.assertFalse(self.final.exists())
        self.assertEqual(self.original, self.source.read_bytes())
        self.assertEqual(1, len(list(self.allowed.glob(".data.staging-*"))))

    def test_page_count_mismatch_never_publishes(self) -> None:
        with self.assertRaisesRegex(assets.AssetCopyError, "페이지 수"):
            self.publish([replace(self.spec, page_count=3)])
        self.assertFalse(self.final.exists())

    def test_destination_hash_is_checked_independently(self) -> None:
        with patch.object(assets, "sha256_file", return_value="1" * 64):
            with self.assertRaisesRegex(assets.AssetCopyError, "복사본 해시"):
                self.publish()
        self.assertFalse(self.final.exists())

    def test_interruption_after_first_copy_never_exposes_partial_final(self) -> None:
        second = replace(self.spec, relative_path="second.pdf")
        def interrupt(*_):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.publish([self.spec, second], progress=interrupt)
        self.assertFalse(self.final.exists())
        self.assertEqual(self.original, self.source.read_bytes())

    def test_source_change_before_publication_is_detected(self) -> None:
        def mutate(*_):
            self.source.write_bytes(self.original + b"changed")
        with self.assertRaisesRegex(assets.AssetCopyError, "공개 전 원본"):
            self.publish(progress=mutate)
        self.assertFalse(self.final.exists())

    def test_source_change_during_copy_is_detected(self) -> None:
        real_fsync = assets.os.fsync
        def change_source(fd):
            real_fsync(fd)
            self.source.write_bytes(self.original + b"changed")
        with patch.object(assets.os, "fsync", side_effect=change_source):
            with self.assertRaisesRegex(assets.AssetCopyError, "복사 중 원본"):
                self.publish()
        self.assertFalse(self.final.exists())

    def test_copy_disk_failure_never_publishes(self) -> None:
        with patch.object(assets.os, "fsync", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.publish()
        self.assertFalse(self.final.exists())
        self.assertEqual(self.original, self.source.read_bytes())

    def test_publication_failure_keeps_verified_staging(self) -> None:
        with patch.object(assets.os, "rename", side_effect=OSError("rename failure")):
            with self.assertRaises(OSError):
                self.publish()
        self.assertFalse(self.final.exists())
        staging = list(self.allowed.glob(".data.staging-*"))
        self.assertTrue((staging[0] / assets.MANIFEST_NAME).is_file())

    def test_existing_target_is_preserved(self) -> None:
        self.final.mkdir(parents=True)
        marker = self.final / "existing.txt"
        marker.write_text("preserve", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self.publish()
        self.assertEqual("preserve", marker.read_text(encoding="utf-8"))

    def test_racing_target_is_preserved(self) -> None:
        def other_publisher(*_):
            self.final.mkdir()
            (self.final / "other.txt").write_text("preserve", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self.publish(progress=other_publisher)
        self.assertEqual("preserve", (self.final / "other.txt").read_text(encoding="utf-8"))

    def test_unsafe_or_duplicate_relative_paths_fail_before_staging(self) -> None:
        for value in ("../outside.pdf", "C:/outside.pdf", "/outside.pdf", "folder//file.pdf", "file.pdf:stream"):
            with self.subTest(value=value):
                with self.assertRaises(assets.AssetCopyError):
                    self.publish([replace(self.spec, relative_path=value)])
                self.assertFalse(self.allowed.exists())
        with self.assertRaises(assets.AssetCopyError):
            self.publish([self.spec, replace(self.spec, relative_path=self.spec.relative_path.upper())])

    def test_destination_outside_boundary_or_equal_parent_is_rejected(self) -> None:
        for destination in (self.root / "outside", self.allowed):
            with self.subTest(destination=destination):
                with self.assertRaises(assets.AssetCopyError):
                    assets.publish_verified_assets([self.spec], destination, allowed_parent=self.allowed)
        self.assertFalse(self.allowed.exists())

    def test_textbook_specs_use_catalog_metadata_without_page_cache_mutation(self) -> None:
        asset = type("Asset", (), {
            "path": self.source, "relative_path": Path("subject/book.pdf"),
            "size_bytes": self.spec.size, "sha256": self.spec.sha256, "page_count": 2,
        })()
        edition = type("Edition", (), {
            "asset": lambda _, kind: asset,
            "variant": None,
        })()
        # Three documents need distinct paths in a valid edition.
        document_assets = [replace(self.spec, relative_path=f"{index}.pdf") for index in range(3)]
        index_by_kind = {"핵심문제": 0, "문제": 1, "해설": 2}
        def document(kind):
            item = document_assets[index_by_kind[kind]]
            return type("Asset", (), {
                "path": item.source, "relative_path": Path(item.relative_path),
                "size_bytes": item.size, "sha256": item.sha256, "page_count": item.page_count,
            })()
        edition.asset = document
        catalog = type("Catalog", (), {"issues": (), "verified_editions": (edition,)})()
        with patch("unit_print_textbooks.load_textbook_catalog", return_value=catalog), patch(
            "unit_print_textbooks.get_pdf_page_count", side_effect=AssertionError("must not persist cache"),
        ):
            result = assets.textbook_asset_specs(self.root / "catalog.json", self.root)
        self.assertEqual(3, len(result))
        self.assertEqual((2, 2, 2), tuple(item.page_count for item in result))

    def registered_fixture(self):
        from scripts.build_quick_printer_release import PdfSource
        config = self.root / "config.json"
        config.write_text("{}", encoding="utf-8")
        baseline = self.root / "baseline.json"
        baseline.write_text(json.dumps({"pdfs": [{
            "relative_path": "data/교과서/공수2/YBM/핵심문제.pdf",
            "size": self.spec.size,
            "sha256": "0" * 64,
        }, {
            "relative_path": "unit_print_separator_a4.pdf",
            "size": self.spec.size,
            "sha256": self.spec.sha256,
        }]}), encoding="utf-8")
        sources = [
            PdfSource("교과서", self.source, Path("data/교과서") / self.spec.relative_path),
            PdfSource("구분지", self.source, Path("unit_print_separator_a4.pdf")),
        ]
        return config, baseline, sources

    def test_registered_backup_uses_current_catalog_hash_and_preserves_separator(self) -> None:
        config, baseline, sources = self.registered_fixture()
        with patch.object(assets, "textbook_asset_specs", return_value=(self.spec,)), patch(
            "scripts.build_quick_printer_release.collect_registered_pdfs", return_value=sources,
        ):
            specs = assets.registered_asset_specs(config, baseline, textbook_source_root=self.root)
        self.assertEqual(self.spec.sha256, specs[0].sha256)
        self.assertEqual("교과서/공수2/YBM/핵심문제.pdf", specs[0].relative_path)
        self.assertEqual("unit_print_separator_a4.pdf", specs[1].relative_path)

    def test_registered_backup_rejects_silently_missing_catalog_sources(self) -> None:
        config, baseline, sources = self.registered_fixture()
        with patch.object(assets, "textbook_asset_specs", return_value=(self.spec,)), patch(
            "scripts.build_quick_printer_release.collect_registered_pdfs", return_value=sources[:1],
        ):
            with self.assertRaisesRegex(assets.AssetCopyError, "등록 목록"):
                assets.registered_asset_specs(config, baseline, textbook_source_root=self.root)


if __name__ == "__main__":
    unittest.main()
