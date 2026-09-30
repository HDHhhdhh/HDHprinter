"""Disposable PDF page counts for catalog/UI use, never print preflight."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

from pypdf import PdfReader


DEFAULT_CACHE_PATH = (
    Path(__file__).resolve().parent
    / "output" / "unit_print_manager" / "cache" / "pdf_page_counts.json"
)
MAX_CACHE_ENTRIES = 512
_MAX_CACHE_BYTES = 2 * 1024 * 1024
_SCHEMA_VERSION = 1


def _file_signature(path: Path) -> tuple[int, ...]:
    stat = path.stat()
    return (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_dev, stat.st_ino)


class PdfPageCountCache:
    """Cache successful reads by absolute path and current filesystem metadata.

    Disk contents are optional: corrupt/unwritable caches only cost another PDF
    read. Separate processes may replace each other's cache, which only loses
    cache hits; every entry is still checked against its source on use.
    """

    def __init__(self, cache_path: Path, *, max_entries: int = MAX_CACHE_ENTRIES) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self.cache_path = Path(cache_path)
        self.max_entries = max_entries
        self._entries: OrderedDict[str, tuple[tuple[int, ...], int]] = OrderedDict()
        self._loaded = False
        self._lock = threading.RLock()

    def get_page_count(self, path: Path) -> int:
        source = Path(path).resolve()
        key = os.path.normcase(str(source))
        with self._lock:
            self._load()
            # One retry handles replacement during a read without storing a
            # count for a different version of the source.
            for _attempt in range(2):
                try:
                    before = _file_signature(source)
                    cached = self._entries.get(key)
                    if cached is not None and cached[0] == before:
                        self._entries.move_to_end(key)
                        return cached[1]
                    self._entries.pop(key, None)
                    reader = PdfReader(str(source))
                    try:
                        page_count = len(reader.pages)
                    finally:
                        reader.close()
                    after = _file_signature(source)
                except Exception:
                    self._entries.pop(key, None)
                    raise
                if before != after:
                    continue
                self._entries[key] = (after, page_count)
                self._trim()
                self._save()
                return page_count
            raise OSError(f"PDF changed while reading page count: {source}")

    def _trim(self) -> None:
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            if self.cache_path.stat().st_size > _MAX_CACHE_BYTES:
                return
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(payload, dict) or payload.get("schema_version") != _SCHEMA_VERSION:
            return
        entries = payload.get("entries")
        if not isinstance(entries, dict):
            return
        for key, entry in entries.items():
            if not isinstance(key, str) or not isinstance(entry, dict):
                continue
            signature, count = entry.get("signature"), entry.get("page_count")
            if (
                not isinstance(signature, list)
                or len(signature) != 5
                or any(type(value) is not int for value in signature)
                or type(count) is not int
                or count < 0
            ):
                continue
            self._entries[key] = (tuple(signature), count)
        self._trim()

    def _save(self) -> None:
        temporary: Path | None = None
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema_version": _SCHEMA_VERSION,
                "entries": {
                    key: {"signature": signature, "page_count": count}
                    for key, (signature, count) in self._entries.items()
                },
            }
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.cache_path.parent,
                prefix=f".{self.cache_path.name}.", suffix=".tmp", delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            os.replace(temporary, self.cache_path)
        except OSError:
            # Page counts remain available in memory when storage is read-only.
            pass
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass


_default_cache: PdfPageCountCache | None = None
_default_cache_lock = threading.Lock()


def reset_pdf_page_count_cache() -> None:
    """Drop the process cache; the next read uses DEFAULT_CACHE_PATH again.

    Tests/profilers can patch DEFAULT_CACHE_PATH before resetting. No files are
    deleted, and an already running read retains its original cache instance.
    """
    global _default_cache
    with _default_cache_lock:
        _default_cache = None


def get_pdf_page_count(path: Path) -> int:
    global _default_cache
    with _default_cache_lock:
        if _default_cache is None:
            _default_cache = PdfPageCountCache(DEFAULT_CACHE_PATH)
        cache = _default_cache
    return cache.get_page_count(path)
