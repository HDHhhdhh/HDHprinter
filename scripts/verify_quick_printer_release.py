from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


LOCAL_ASSET_MANIFEST = ".quick_printer_asset_manifest.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify the extracted Quick Printer release.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--fast", action="store_true", help="Check paths and sizes without hashing every PDF.")
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        while chunk := input_file.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def asset_set_sha256(entries: list[dict[str, object]]) -> str:
    rows = [
        {
            "category": str(entry.get("category", "")),
            "relative_path": str(entry["relative_path"]).replace("\\", "/"),
            "size": int(entry["size"]),
            "sha256": str(entry["sha256"]).casefold(),
        }
        for entry in sorted(entries, key=lambda item: str(item["relative_path"]).casefold())
    ]
    encoded = json.dumps(
        {"assets": rows},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_pdf_inventory(root: Path, release_manifest: dict[str, object]) -> tuple[list[dict[str, object]], int, int, bool]:
    local_manifest_path = root / LOCAL_ASSET_MANIFEST
    if not local_manifest_path.is_file():
        entries = release_manifest.get("pdfs", [])
        if not isinstance(entries, list):
            raise ValueError("release manifest PDF inventory is invalid")
        return (
            entries,
            int(release_manifest.get("registered_pdf_count", -1)),
            int(release_manifest.get("registered_pdf_bytes", -1)),
            True,
        )

    asset_manifest = json.loads(local_manifest_path.read_text(encoding="utf-8"))
    entries = asset_manifest.get("assets", [])
    if int(asset_manifest.get("schema_version", 0)) != 1 or not isinstance(entries, list):
        raise ValueError(f"invalid local asset manifest: {local_manifest_path}")
    expected_digest = str(asset_manifest.get("asset_set_sha256", "")).casefold()
    if not expected_digest or asset_set_sha256(entries) != expected_digest:
        raise ValueError(f"local asset manifest digest mismatch: {local_manifest_path}")
    return (
        entries,
        int(asset_manifest.get("asset_count", -1)),
        int(asset_manifest.get("asset_bytes", -1)),
        False,
    )


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    manifest_path = root / "release_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    try:
        entries, expected_pdf_count, expected_pdf_bytes, strict_extra_pdfs = load_pdf_inventory(root, manifest)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(f"Release verification failed:\n- {error}")
        return 1
    immutable_entries = manifest.get("immutable_files", [])
    errors: list[str] = []
    warnings: list[str] = []

    if int(manifest.get("schema_version", 0)) < 2 or not isinstance(immutable_entries, list):
        errors.append("manifest does not contain the immutable application-file inventory")

    immutable_bytes = 0
    for entry in immutable_entries if isinstance(immutable_entries, list) else []:
        relative_path = str(entry["relative_path"])
        path = root / Path(relative_path)
        if not path.is_file():
            errors.append(f"missing immutable file: {relative_path}")
            continue
        size = path.stat().st_size
        immutable_bytes += size
        if size != int(entry["size"]):
            errors.append(f"immutable size mismatch: {relative_path}")
            continue
        if sha256(path).casefold() != str(entry["sha256"]).casefold():
            errors.append(f"immutable hash mismatch: {relative_path}")

    if len(immutable_entries) != int(manifest.get("immutable_file_count", -1)):
        errors.append("immutable file count mismatch")
    if immutable_bytes != int(manifest.get("immutable_file_bytes", -1)):
        errors.append("immutable file byte total mismatch")

    total_bytes = 0
    for index, entry in enumerate(entries, start=1):
        relative_path = str(entry["relative_path"])
        path = root / Path(relative_path)
        if not path.is_file():
            errors.append(f"missing: {relative_path}")
            continue
        size = path.stat().st_size
        total_bytes += size
        if size != int(entry["size"]):
            errors.append(f"size mismatch: {relative_path}")
            continue
        if not args.fast and sha256(path).casefold() != str(entry["sha256"]).casefold():
            errors.append(f"hash mismatch: {relative_path}")
        if index % 100 == 0 or index == len(entries):
            print(f"Verified {index:,}/{len(entries):,} PDFs", flush=True)

    actual_pdfs = {path.relative_to(root).as_posix().casefold() for path in root.rglob("*.pdf")}
    expected_pdfs = {str(entry["relative_path"]).casefold() for entry in entries}
    for extra in sorted(actual_pdfs - expected_pdfs):
        message = f"unexpected PDF: {extra}"
        if strict_extra_pdfs:
            errors.append(message)
        else:
            warnings.append(message)
    if len(entries) != expected_pdf_count:
        errors.append("manifest count mismatch")
    if total_bytes != expected_pdf_bytes:
        errors.append("manifest byte total mismatch")

    if errors:
        print("Release verification failed:")
        for error in errors[:100]:
            print(f"- {error}")
        return 1
    if warnings:
        print("Release verification warnings (left untouched by incremental sync):")
        for warning in warnings[:100]:
            print(f"- {warning}")
    mode = "paths/sizes" if args.fast else "SHA-256"
    print(
        f"OK: {len(immutable_entries):,} immutable application files verified by SHA-256; "
        f"{len(entries):,} registered PDFs verified by {mode}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
