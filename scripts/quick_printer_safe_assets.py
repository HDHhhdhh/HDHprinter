"""Verify complete PDF copies before publishing a local asset directory.

Source PDFs are never removed or modified. Failed copies remain in a named
staging directory for inspection; an incomplete final directory is never
published. This tool does not launch the printer app or write PDF caches.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

MANIFEST_NAME = "verified_asset_copy_manifest.json"
_CHUNK_BYTES = 4 * 1024 * 1024


class AssetCopyError(ValueError):
    """No incomplete or unverified copy may be published."""


@dataclass(frozen=True, slots=True)
class AssetSpec:
    category: str
    source: Path
    relative_path: str
    size: int
    sha256: str
    page_count: int | None = None


def _relative_path(value: str) -> Path:
    text = str(value)
    windows = PureWindowsPath(text)
    posix = PurePosixPath(text.replace("\\", "/"))
    if (
        not text or windows.drive or windows.root or posix.is_absolute()
        or any(part in ("", ".", "..") for part in text.replace("\\", "/").split("/"))
        or any(":" in part for part in posix.parts)
    ):
        raise AssetCopyError("자료 상대 경로가 안전하지 않습니다.")
    return Path(*posix.parts)


def _inside(path: Path, parent: Path) -> Path:
    resolved = Path(path).resolve()
    try:
        resolved.relative_to(parent.resolve())
    except ValueError as error:
        raise AssetCopyError("대상 경로가 지정된 안전 폴더를 벗어났습니다.") from error
    return resolved


def _signature(path: Path) -> tuple[int, int, int, int, int]:
    stat = path.stat()
    return (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_dev, stat.st_ino)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _page_count(path: Path) -> int:
    from pypdf import PdfReader
    with path.open("rb") as stream:
        return len(PdfReader(stream).pages)


def _validated_specs(specs: Iterable[AssetSpec]) -> tuple[AssetSpec, ...]:
    result = tuple(specs)
    if not result:
        raise AssetCopyError("자료 목록이 비어 있습니다.")
    seen: set[str] = set()
    for item in result:
        relative = _relative_path(item.relative_path)
        key = relative.as_posix().casefold()
        if key in seen or key == MANIFEST_NAME.casefold():
            raise AssetCopyError("대상 자료 경로가 중복됩니다.")
        seen.add(key)
        if (
            relative.suffix.casefold() != ".pdf" or not item.source.is_file()
            or type(item.size) is not int or item.size <= 0
            or len(item.sha256) != 64
            or any(char not in "0123456789abcdef" for char in item.sha256)
            or (item.page_count is not None and (
                type(item.page_count) is not int or item.page_count < 1
            ))
        ):
            raise AssetCopyError(f"자료 또는 검증 기대값이 올바르지 않습니다: {relative.as_posix()}")
    return result


def _copy_verified(item: AssetSpec, target: Path) -> tuple[int, int, int, int, int]:
    before = _signature(item.source)
    if before[0] != item.size:
        raise AssetCopyError(f"원본 크기가 검증값과 다릅니다: {item.relative_path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with item.source.open("rb") as source, target.open("xb") as output:
        while chunk := source.read(_CHUNK_BYTES):
            output.write(chunk)
            digest.update(chunk)
            size += len(chunk)
        output.flush()
        os.fsync(output.fileno())
    if _signature(item.source) != before:
        raise AssetCopyError(f"복사 중 원본이 변경되었습니다: {item.relative_path}")
    if size != item.size or digest.hexdigest() != item.sha256:
        raise AssetCopyError(f"원본 해시가 검증값과 다릅니다: {item.relative_path}")
    if target.stat().st_size != item.size or sha256_file(target) != item.sha256:
        raise AssetCopyError(f"복사본 해시가 검증값과 다릅니다: {item.relative_path}")
    if item.page_count is not None and _page_count(target) != item.page_count:
        raise AssetCopyError(f"복사본 PDF 페이지 수가 검증값과 다릅니다: {item.relative_path}")
    # Catalog analysis caches use relative path + size + mtime. Preserve the
    # verified source mtime so relocating identical PDFs does not force costly
    # full-text analysis before the operator window can be created.
    os.utime(target, ns=(item.source.stat().st_atime_ns, before[1]))
    if target.stat().st_mtime_ns != before[1]:
        raise AssetCopyError(f"복사본 수정 시각을 보존하지 못했습니다: {item.relative_path}")
    return before


def publish_verified_assets(
    specs: Iterable[AssetSpec], destination: Path, *, allowed_parent: Path,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, object]:
    """Publish one complete directory, refusing any existing destination.

    Staging and final are siblings, so Windows rename publishes on the same
    volume. Failed staging is retained; no recursive deletion is performed.
    """
    items = _validated_specs(specs)
    allowed = allowed_parent.resolve()
    final = _inside(destination, allowed)
    if final == allowed:
        raise AssetCopyError("안전 부모 폴더 자체를 교체할 수 없습니다.")
    if final.exists():
        raise FileExistsError(f"기존 대상 폴더를 덮어쓰지 않습니다: {final}")
    for item in items:
        source = item.source.resolve()
        if source == final or final in source.parents:
            raise AssetCopyError("원본이 대상 폴더 안에 있습니다.")
    final.parent.mkdir(parents=True, exist_ok=True)
    # Recheck after directory creation, before creating a sibling staging path.
    final = _inside(final, allowed)
    # tempfile.mkdtemp uses mode 0700, which creates a protected Windows ACL.
    # Asset directories need the same inherited access as their chosen parent.
    staging = _inside(final.parent / f".{final.name}.staging-{uuid.uuid4().hex}", allowed)
    staging.mkdir(mode=0o777, exist_ok=False)
    signatures: list[tuple[AssetSpec, tuple[int, int, int, int, int]]] = []
    manifest: dict[str, object] = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "copying",
        "file_count": len(items),
        "total_bytes": sum(item.size for item in items),
        "files": [],
    }
    try:
        files: list[dict[str, object]] = []
        for number, item in enumerate(items, 1):
            target = _inside(staging / _relative_path(item.relative_path), staging)
            signatures.append((item, _copy_verified(item, target)))
            files.append({
                "category": item.category,
                "relative_path": _relative_path(item.relative_path).as_posix(),
                "size": item.size,
                "sha256": item.sha256,
                **({"page_count": item.page_count} if item.page_count is not None else {}),
            })
            if progress is not None:
                progress(number, len(items), item.relative_path)
        for item, before in signatures:
            if _signature(item.source) != before:
                raise AssetCopyError(f"공개 전 원본이 변경되었습니다: {item.relative_path}")
        manifest["files"] = files
        manifest["status"] = "verified"
        manifest_path = staging / MANIFEST_NAME
        with manifest_path.open("x", encoding="utf-8") as output:
            json.dump(manifest, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        if final.exists():
            raise FileExistsError(f"복사 중 대상 폴더가 생성되어 공개를 중단했습니다: {final}")
        _inside(final, allowed)
        _inside(staging, allowed)
        os.rename(staging, final)
    except BaseException:
        # Keep evidence without risking a destructive cleanup of either source
        # or an unrelated directory. The existing final path is never removed.
        raise
    return {**manifest, "destination": str(final)}


def textbook_asset_specs(catalog_path: Path, source_root: Path) -> tuple[AssetSpec, ...]:
    from unit_print_textbooks import load_textbook_catalog
    catalog = load_textbook_catalog(catalog_path, source_root)
    if catalog.issues:
        raise AssetCopyError("교과서 catalog가 불완전합니다: " + " | ".join(catalog.issues))
    specs: list[AssetSpec] = []
    for edition in catalog.verified_editions:
        assets = [edition.asset(kind) for kind in ("핵심문제", "문제", "해설")]
        if edition.variant is not None:
            assets.append(edition.variant)
        for asset in assets:
            specs.append(AssetSpec(
                "교과서", asset.path, asset.relative_path.as_posix(),
                asset.size_bytes, asset.sha256, asset.page_count,
            ))
    return _validated_specs(specs)


def registered_asset_specs(
    config_path: Path, baseline_manifest: Path, *, textbook_source_root: Path,
    catalog_path: Path = PROJECT_ROOT / "unit_print_textbook_catalog.json",
) -> tuple[AssetSpec, ...]:
    """Collect existing registered assets against baseline plus current textbook hashes."""
    from unittest.mock import patch
    from scripts import build_quick_printer_release as release
    baseline = json.loads(baseline_manifest.read_text(encoding="utf-8"))
    expected = {
        str(item["relative_path"]): item for item in baseline["pdfs"]
    }
    current_textbooks = textbook_asset_specs(catalog_path, textbook_source_root)
    for item in current_textbooks:
        expected[f"data/교과서/{item.relative_path}"] = {
            "size": item.size, "sha256": item.sha256, "page_count": item.page_count,
        }
    config = json.loads(config_path.read_text(encoding="utf-8"))
    # Avoid the collector's cache-writing textbook verification; the catalog
    # supplies expectations and each copied PDF is independently verified.
    with patch(
        "scripts.build_quick_printer_release.collect_textbook_sources",
        return_value=[release.PdfSource(
            "교과서", item.source, Path("data") / "교과서" / _relative_path(item.relative_path),
        ) for item in current_textbooks],
    ):
        sources = release.collect_registered_pdfs(config)
    collected_paths = {source.relative_path.as_posix() for source in sources}
    if collected_paths != set(expected):
        raise AssetCopyError("현재 등록 목록과 검증 기준 자료 목록이 다릅니다.")
    specs: list[AssetSpec] = []
    for source in sources:
        relative = source.relative_path.as_posix()
        signature = expected.get(relative)
        if signature is None:
            raise AssetCopyError(f"등록 자료에 검증 기대값이 없습니다: {relative}")
        # The fixed separator also belongs in the safety backup's data folder.
        data_relative = relative.removeprefix("data/")
        specs.append(AssetSpec(
            source.category, source.source, data_relative,
            int(signature["size"]), str(signature["sha256"]),
            signature.get("page_count"),
        ))
    return _validated_specs(specs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("textbooks", "backup"))
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--allowed-parent", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=PROJECT_ROOT / "unit_print_textbook_catalog.json")
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "unit_print_manager_config.json")
    parser.add_argument("--baseline-manifest", type=Path)
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    if args.operation == "textbooks":
        specs = textbook_asset_specs(args.catalog, args.source_root)
    else:
        if args.baseline_manifest is None:
            parser.error("backup에는 --baseline-manifest가 필요합니다.")
        specs = registered_asset_specs(
            args.config, args.baseline_manifest,
            textbook_source_root=args.source_root, catalog_path=args.catalog,
        )
    last_update = 0.0

    def progress(done: int, total: int, relative: str) -> None:
        nonlocal last_update
        current = time.monotonic()
        if done == total or current - last_update >= 10:
            print(f"검증 복사 {done}/{total}: {relative}", flush=True)
            last_update = current

    result = publish_verified_assets(
        specs, args.destination, allowed_parent=args.allowed_parent, progress=progress,
    )
    if args.evidence is not None:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "status", "file_count", "total_bytes", "destination",
    )}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
