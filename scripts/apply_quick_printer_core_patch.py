"""Apply a verified core-problem delta to one exact Quick Printer release."""

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
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_release(root: Path) -> None:
    manifest = json.loads((root / "release_manifest.json").read_text(encoding="utf-8"))
    for key in ("immutable_files", "pdfs"):
        for entry in manifest[key]:
            path = (root / entry["relative_path"]).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise RuntimeError(f"배포본 필수 파일이 없습니다: {entry['relative_path']}")
            if path.stat().st_size != entry["size"] or sha256(path) != entry["sha256"]:
                raise RuntimeError(f"배포본 SHA-256 검증 실패: {entry['relative_path']}")
    print(f"배포본 검증: 앱 파일 {len(manifest['immutable_files'])}개, "
          f"등록 PDF {len(manifest['pdfs'])}개 SHA-256 일치")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--installed", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args()
    package = Path(__file__).resolve().parent
    manifest = json.loads((package / "patch_manifest.json").read_text(encoding="utf-8"))
    installed = args.installed.resolve()
    backup = args.backup.resolve()
    if backup.is_relative_to(installed):
        raise ValueError("백업은 설치 폴더 밖에 두어야 합니다.")
    base_manifest = installed / "release_manifest.json"
    if not base_manifest.is_file():
        raise FileNotFoundError(f"기존 정본 매니페스트가 없습니다: {base_manifest}")
    if sha256(base_manifest) == manifest["target_release_manifest_sha256"]:
        verify_release(installed)
        print("이미 핵심문제 교체 패치가 적용되어 있습니다.")
        return
    if sha256(base_manifest) != manifest["base_release_manifest_sha256"]:
        raise ValueError("지정된 이전 배포본과 매니페스트가 일치하지 않습니다.")
    if backup.exists():
        raise FileExistsError(f"백업 폴더가 이미 있습니다: {backup}")
    verify_release(installed)
    entries = manifest["files"]
    for entry in entries:
        relative = Path(entry["path"])
        target = (installed / relative).resolve()
        source = (package / "files" / relative).resolve()
        if not target.is_relative_to(installed) or not source.is_relative_to(package / "files"):
            raise ValueError(f"안전하지 않은 패치 경로입니다: {relative}")
        if not source.is_file() or sha256(source) != entry["new_sha256"]:
            raise ValueError(f"패치 파일 SHA-256 불일치: {relative}")
        if entry["old_sha256"] is None:
            if target.exists():
                raise ValueError(f"새 파일 경로가 이미 있습니다: {relative}")
        elif not target.is_file() or sha256(target) != entry["old_sha256"]:
            raise ValueError(f"기존 파일 SHA-256 불일치: {relative}")

    backup.mkdir(parents=True)
    applied = []
    try:
        for entry in entries:
            relative = Path(entry["path"])
            target = installed / relative
            source = package / "files" / relative
            if entry["old_sha256"] is not None:
                saved = backup / relative
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, saved)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".core-patch.tmp")
            if temporary.exists():
                raise FileExistsError(f"이전 임시 파일이 남아 있습니다: {temporary}")
            shutil.copy2(source, temporary)
            if sha256(temporary) != entry["new_sha256"]:
                raise ValueError(f"임시 패치 파일 SHA-256 불일치: {relative}")
            os.replace(temporary, target)
            applied.append(entry)
        verify_release(installed)
    except Exception:
        for entry in reversed(applied):
            relative = Path(entry["path"])
            target = installed / relative
            if entry["old_sha256"] is None:
                target.unlink(missing_ok=True)
            else:
                shutil.copy2(backup / relative, target)
        raise
    print(f"핵심문제 교체 완료: {len(applied)}개 파일, 백업: {backup}")


if __name__ == "__main__":
    main()
