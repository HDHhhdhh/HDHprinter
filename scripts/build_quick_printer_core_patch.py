"""Package the exact changed files between two immutable Quick Printer releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def indexed(manifest: dict, key: str) -> dict[str, dict]:
    return {entry["relative_path"]: entry for entry in manifest[key]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    if args.destination.exists():
        raise FileExistsError(args.destination)
    base_manifest_path = args.base / "release_manifest.json"
    target_manifest_path = args.target / "release_manifest.json"
    old = json.loads(base_manifest_path.read_text(encoding="utf-8"))
    new = json.loads(target_manifest_path.read_text(encoding="utf-8"))
    if old["registered_pdf_count"] != new["registered_pdf_count"]:
        raise ValueError("등록 PDF 수가 달라 핵심문제 교체 패치가 아닙니다.")
    changes: dict[str, dict] = {}
    for key in ("immutable_files", "pdfs"):
        old_index = indexed(old, key)
        new_index = indexed(new, key)
        if old_index.keys() != new_index.keys():
            raise ValueError(f"{key} 경로 집합이 다릅니다.")
        for relative, entry in new_index.items():
            prior = old_index[relative]
            if prior["sha256"] != entry["sha256"]:
                changes[relative] = {
                    "path": relative, "old_sha256": prior["sha256"],
                    "new_sha256": entry["sha256"], "size_bytes": entry.get("size"),
                }
    core_changes = [name for name in changes if name.startswith("data/교과서/") and name.endswith(" 핵심문제.pdf")]
    if len(core_changes) != 42:
        raise ValueError(f"핵심문제 변경 수가 42개가 아닙니다: {len(core_changes)}")
    changes["release_manifest.json"] = {
        "path": "release_manifest.json", "old_sha256": sha256(base_manifest_path),
        "new_sha256": sha256(target_manifest_path),
        "size_bytes": target_manifest_path.stat().st_size,
    }
    destination = args.destination
    files_root = destination / "files"
    files_root.mkdir(parents=True)
    for entry in changes.values():
        relative = entry["path"]
        source = args.target / relative
        target = files_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if sha256(target) != entry["new_sha256"]:
            raise ValueError(f"복사 후 SHA-256 불일치: {relative}")
    shutil.copy2(Path(__file__).with_name("apply_quick_printer_core_patch.py"), destination / "apply_quick_printer_core_patch.py")
    patch_manifest = {
        "schema_version": 1,
        "base_release_name": args.base.name,
        "target_release_name": args.target.name,
        "base_release_manifest_sha256": sha256(base_manifest_path),
        "target_release_manifest_sha256": sha256(target_manifest_path),
        "changed_core_pdf_count": len(core_changes),
        "files": [changes[name] for name in sorted(changes)],
    }
    (destination / "patch_manifest.json").write_text(
        json.dumps(patch_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    archive = destination.with_suffix(".zip")
    if archive.exists():
        raise FileExistsError(archive)
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_STORED, allowZip64=True) as output:
        for path in sorted(destination.rglob("*")):
            if path.is_file():
                output.write(path, (Path(destination.name) / path.relative_to(destination)).as_posix())
    archive_sha = sha256(archive)
    archive.with_suffix(".zip.sha256").write_text(
        f"{archive_sha} *{archive.name}\n", encoding="utf-8"
    )
    print(json.dumps({
        "archive": str(archive.resolve()), "bytes": archive.stat().st_size,
        "sha256": archive_sha, "changed_files": len(changes),
        "changed_core_pdfs": len(core_changes),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
