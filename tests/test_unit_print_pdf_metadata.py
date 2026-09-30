from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter
from pypdf.errors import FileNotDecryptedError, PdfReadError

import unit_print_pdf_metadata as metadata
from unit_print_materials import PageSpan, _scan_lesson_material_catalog


def write_pdf(path: Path, pages: int, *, password: str | None = None) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    if password is not None:
        writer.encrypt(password)
    with path.open("wb") as handle:
        writer.write(handle)
    writer.close()


class PdfPageCountCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source.pdf"
        self.cache_path = self.root / "cache" / "counts.json"
        self.cache = metadata.PdfPageCountCache(self.cache_path)

    def test_repeated_and_restarted_reads_reuse_successful_page_count(self) -> None:
        write_pdf(self.source, 3)
        with patch.object(metadata, "PdfReader", wraps=PdfReader) as reader:
            self.assertEqual(3, self.cache.get_page_count(self.source))
            self.assertEqual(3, self.cache.get_page_count(self.source))
            restarted = metadata.PdfPageCountCache(self.cache_path)
            self.assertEqual(3, restarted.get_page_count(self.source))
        self.assertEqual(1, reader.call_count)

    def test_changed_source_replaces_the_cached_page_count(self) -> None:
        write_pdf(self.source, 1)
        self.assertEqual(1, self.cache.get_page_count(self.source))
        write_pdf(self.source, 4)
        with patch.object(metadata, "PdfReader", wraps=PdfReader) as reader:
            self.assertEqual(4, self.cache.get_page_count(self.source))
            self.assertEqual(4, metadata.PdfPageCountCache(self.cache_path).get_page_count(self.source))
        self.assertEqual(1, reader.call_count)

    def test_same_size_and_mtime_replacement_invalidates_cached_count(self) -> None:
        replacement = self.root / "replacement.pdf"
        write_pdf(self.source, 1)
        write_pdf(replacement, 2)
        original_bytes, replacement_bytes = self.source.read_bytes(), replacement.read_bytes()
        padded_size = max(len(original_bytes), len(replacement_bytes)) + 20
        self.source.write_bytes(original_bytes.ljust(padded_size, b" "))
        replacement.write_bytes(replacement_bytes.ljust(padded_size, b" "))
        original_stat = self.source.stat()
        self.assertEqual(1, self.cache.get_page_count(self.source))
        before = metadata._file_signature(self.source)
        os.utime(replacement, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        os.replace(replacement, self.source)
        after = metadata._file_signature(self.source)
        self.assertEqual(before[:2], after[:2])
        self.assertNotEqual(before, after)
        self.assertEqual(2, self.cache.get_page_count(self.source))

    def test_same_size_in_place_edit_with_new_mtime_invalidates_cached_count(self) -> None:
        replacement = self.root / "replacement.pdf"
        write_pdf(self.source, 1)
        write_pdf(replacement, 2)
        original_bytes, replacement_bytes = self.source.read_bytes(), replacement.read_bytes()
        padded_size = max(len(original_bytes), len(replacement_bytes)) + 20
        self.source.write_bytes(original_bytes.ljust(padded_size, b" "))
        original_stat = self.source.stat()
        self.assertEqual(1, self.cache.get_page_count(self.source))
        self.source.write_bytes(replacement_bytes.ljust(padded_size, b" "))
        os.utime(self.source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns + 2_000_000_000))
        self.assertEqual(original_stat.st_size, self.source.stat().st_size)
        self.assertEqual(original_stat.st_ino, self.source.stat().st_ino)
        self.assertEqual(2, self.cache.get_page_count(self.source))

    def test_source_changed_during_read_is_retried_before_caching(self) -> None:
        write_pdf(self.source, 1)
        calls = 0

        def changing_reader(*args: object, **kwargs: object) -> PdfReader:
            nonlocal calls
            reader = PdfReader(*args, **kwargs)
            calls += 1
            if calls == 1:
                write_pdf(self.source, 2)
            return reader

        with patch.object(metadata, "PdfReader", side_effect=changing_reader):
            self.assertEqual(2, self.cache.get_page_count(self.source))
        self.assertEqual(2, calls)
        with patch.object(metadata, "PdfReader", side_effect=AssertionError("unexpected PDF read")):
            self.assertEqual(2, metadata.PdfPageCountCache(self.cache_path).get_page_count(self.source))

    def test_repeated_source_changes_fail_without_saving_a_count(self) -> None:
        write_pdf(self.source, 1)
        calls = 0

        def changing_reader(*args: object, **kwargs: object) -> PdfReader:
            nonlocal calls
            reader = PdfReader(*args, **kwargs)
            calls += 1
            write_pdf(self.source, calls + 1)
            return reader

        with patch.object(metadata, "PdfReader", side_effect=changing_reader):
            with self.assertRaisesRegex(OSError, "PDF changed"):
                self.cache.get_page_count(self.source)
        self.assertEqual(2, calls)
        self.assertFalse(self.cache_path.exists())
        self.assertEqual(3, self.cache.get_page_count(self.source))

    def test_missing_previously_cached_pdf_still_fails(self) -> None:
        write_pdf(self.source, 1)
        self.cache.get_page_count(self.source)
        self.source.unlink()
        with self.assertRaises(FileNotFoundError):
            self.cache.get_page_count(self.source)
        with self.assertRaises(FileNotFoundError):
            metadata.PdfPageCountCache(self.cache_path).get_page_count(self.source)

    def test_corrupt_and_encrypted_failures_are_not_cached(self) -> None:
        for kind, error in (("corrupt", PdfReadError), ("encrypted", FileNotDecryptedError)):
            with self.subTest(kind=kind):
                source = self.root / f"{kind}.pdf"
                if kind == "corrupt":
                    source.write_bytes(b"%PDF-1.4\n%%EOF\n")
                else:
                    write_pdf(source, 2, password="required")
                with patch.object(metadata, "PdfReader", wraps=PdfReader) as reader:
                    for _ in range(2):
                        with self.assertRaises(error):
                            self.cache.get_page_count(source)
                self.assertEqual(2, reader.call_count)
                self.assertFalse(self.cache_path.exists())
        write_pdf(self.source, 1)
        self.assertEqual(1, self.cache.get_page_count(self.source))

    def test_corrupt_cache_content_falls_back_to_reading_original(self) -> None:
        write_pdf(self.source, 2)
        self.cache_path.parent.mkdir()
        for content in ("not JSON", '{"schema_version":1,"entries":[]}', "[]"):
            with self.subTest(content=content):
                self.cache_path.write_text(content, encoding="utf-8")
                with patch.object(metadata, "PdfReader", wraps=PdfReader) as reader:
                    cache = metadata.PdfPageCountCache(self.cache_path)
                    self.assertEqual(2, cache.get_page_count(self.source))
                self.assertEqual(1, reader.call_count)
                self.assertEqual(1, json.loads(self.cache_path.read_text())["schema_version"])

    def test_invalid_entry_does_not_supply_a_page_count(self) -> None:
        write_pdf(self.source, 2)
        self.cache_path.parent.mkdir()
        payload = {
            "schema_version": 1,
            "entries": {os.path.normcase(str(self.source.resolve())): {
                "signature": metadata._file_signature(self.source), "page_count": True,
            }},
        }
        self.cache_path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(2, self.cache.get_page_count(self.source))

    def test_unreadable_and_unwritable_cache_does_not_block_original_read(self) -> None:
        write_pdf(self.source, 2)
        self.cache_path.parent.mkdir()
        self.cache_path.write_text("unreadable cache", encoding="utf-8")
        with patch.object(Path, "read_text", side_effect=PermissionError("read-only")) as read_text, patch.object(
            metadata.os, "replace", side_effect=PermissionError("read-only")
        ), patch.object(metadata, "PdfReader", wraps=PdfReader) as reader:
            self.assertEqual(2, self.cache.get_page_count(self.source))
            self.assertEqual(2, self.cache.get_page_count(self.source))
        self.assertEqual(1, read_text.call_count)
        self.assertEqual(1, reader.call_count)
        self.assertEqual("unreadable cache", self.cache_path.read_text(encoding="utf-8"))
        self.assertEqual([], list(self.cache_path.parent.glob("*.tmp")))

    def test_unavailable_cache_directory_falls_back_to_memory(self) -> None:
        write_pdf(self.source, 2)
        self.cache_path.parent.write_text("not a directory", encoding="utf-8")
        with patch.object(metadata, "PdfReader", wraps=PdfReader) as reader:
            self.assertEqual(2, self.cache.get_page_count(self.source))
            self.assertEqual(2, self.cache.get_page_count(self.source))
        self.assertEqual(1, reader.call_count)

    def test_failed_atomic_replace_preserves_previous_cache(self) -> None:
        write_pdf(self.source, 1)
        self.cache.get_page_count(self.source)
        previous = self.cache_path.read_bytes()
        other = self.root / "other.pdf"
        write_pdf(other, 3)
        with patch.object(metadata.os, "replace", side_effect=PermissionError("locked")):
            self.assertEqual(3, self.cache.get_page_count(other))
        self.assertEqual(previous, self.cache_path.read_bytes())
        self.assertEqual([], list(self.cache_path.parent.glob("*.tmp")))
        self.assertEqual(3, metadata.PdfPageCountCache(self.cache_path).get_page_count(other))

    def test_cache_bounds_memory_and_persisted_entries(self) -> None:
        cache = metadata.PdfPageCountCache(self.cache_path, max_entries=2)
        sources = [self.root / f"{index}.pdf" for index in range(3)]
        for index, source in enumerate(sources, 1):
            write_pdf(source, index)
        with patch.object(metadata, "PdfReader", wraps=PdfReader) as reader:
            for index in (0, 1, 0, 2, 0):
                self.assertEqual(index + 1, cache.get_page_count(sources[index]))
            self.assertEqual(3, reader.call_count)
            self.assertEqual(2, cache.get_page_count(sources[1]))
            self.assertEqual(4, reader.call_count)
        payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        self.assertEqual(2, len(payload["entries"]))
        restarted = metadata.PdfPageCountCache(self.cache_path, max_entries=1)
        self.assertEqual(2, restarted.get_page_count(sources[1]))
        self.assertEqual(1, len(restarted._entries))

    def test_concurrent_readers_share_one_successful_read(self) -> None:
        write_pdf(self.source, 3)
        barrier = threading.Barrier(6)

        def read() -> int:
            barrier.wait(timeout=5)
            return self.cache.get_page_count(self.source)

        with patch.object(metadata, "PdfReader", wraps=PdfReader) as reader:
            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = [pool.submit(read) for _ in range(6)]
                self.assertEqual([3] * 6, [future.result(timeout=5) for future in futures])
        self.assertEqual(1, reader.call_count)

    def test_default_cache_path_can_be_isolated_and_reset(self) -> None:
        write_pdf(self.source, 2)
        self.addCleanup(metadata.reset_pdf_page_count_cache)
        with patch.object(metadata, "DEFAULT_CACHE_PATH", self.cache_path):
            metadata.reset_pdf_page_count_cache()
            self.assertEqual(2, metadata.get_pdf_page_count(self.source))
            metadata.reset_pdf_page_count_cache()
            with patch.object(metadata, "PdfReader", side_effect=AssertionError("unexpected PDF read")):
                self.assertEqual(2, metadata.get_pdf_page_count(self.source))
        self.assertTrue(self.cache_path.is_file())

    def test_lesson_scanner_reuses_count_and_preserves_range_validation(self) -> None:
        write_pdf(self.source, 3)
        page_map = {"subject": (PageSpan(1, "lesson", 1, 3, "part"),)}
        with patch("unit_print_materials.get_pdf_page_count", side_effect=self.cache.get_page_count), patch.object(
            metadata, "PdfReader", wraps=PdfReader
        ) as reader:
            first = _scan_lesson_material_catalog(self.root, page_map, lambda _name: "subject")
            second = _scan_lesson_material_catalog(self.root, page_map, lambda _name: "subject")
            self.assertEqual(first, second)
            self.assertEqual(3, first.subjects[0].page_count)
            self.assertEqual(1, reader.call_count)
            write_pdf(self.source, 2)
            invalid = _scan_lesson_material_catalog(self.root, page_map, lambda _name: "subject")
            self.assertEqual((), invalid.subjects)
            self.assertEqual(2, reader.call_count)


if __name__ == "__main__":
    unittest.main()
