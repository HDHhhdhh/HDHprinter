"""Replace validated operator core PDFs, retaining a rollback copy."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--operator-root", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args()

    old = json.loads(args.catalog.read_text(encoding="utf-8"))
    new = json.loads((args.staging / "unit_print_textbook_catalog.json").read_text(encoding="utf-8"))
    old_entries = {
        edition["core"]["relative_path"]: edition["core"]
        for subject in old["subjects"] for edition in subject["editions"]
        if edition.get("mapping_status") == "verified"
    }
    new_entries = {
        edition["core"]["relative_path"]: edition["core"]
        for subject in new["subjects"] for edition in subject["editions"]
        if edition.get("mapping_status") == "verified"
    }
    if set(old_entries) != set(new_entries) or len(old_entries) != 42:
        raise ValueError("핵심문제 42개 대응이 일치하지 않습니다.")
    if args.backup.exists():
        raise FileExistsError(f"백업 폴더가 이미 있습니다: {args.backup}")
    operator_root = args.operator_root.resolve()
    entries = []
    for relative, old_asset in old_entries.items():
        source = (operator_root / relative).resolve()
        staged = args.staging / "data" / "교과서" / relative
        if not source.is_relative_to(operator_root) or not source.is_file() or not staged.is_file():
            raise ValueError(f"자료 경로가 없거나 범위를 벗어났습니다: {relative}")
        if sha256(source) != old_asset["sha256"] or sha256(staged) != new_entries[relative]["sha256"]:
            raise ValueError(f"교체 전후 SHA-256이 카탈로그와 다릅니다: {relative}")
        entries.append((relative, source, staged))

    args.backup.mkdir(parents=True)
    for relative, source, _staged in entries:
        backup = args.backup / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, backup)
    installed = []
    try:
        for relative, source, staged in entries:
            temporary = source.with_name(source.name + ".core-cover-install.tmp")
            shutil.copy2(staged, temporary)
            if sha256(temporary) != new_entries[relative]["sha256"]:
                raise ValueError(f"임시 교체본 SHA-256 불일치: {relative}")
            os.replace(temporary, source)
            installed.append((relative, source))
        for relative, source in installed:
            if sha256(source) != new_entries[relative]["sha256"]:
                raise ValueError(f"교체본 SHA-256 불일치: {relative}")
    except Exception:
        for relative, source in installed:
            shutil.copy2(args.backup / relative, source)
        raise
    print(f"Installed {len(installed)} core PDFs; backup: {args.backup}")


if __name__ == "__main__":
    main()
