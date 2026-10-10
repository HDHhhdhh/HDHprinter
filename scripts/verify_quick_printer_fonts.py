"""Verify the release's font bytes and the family's actual Tk resolution.

This tool never installs fonts, constructs the app, or contacts a printer.
Its optional Tk probe imports the existing app module and uses a withdrawn root.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FONT_MANIFEST = "fonts/quick_printer_fonts.json"


def _bundled_path(root: Path, relative: str) -> Path:
    value = Path(relative)
    if value.is_absolute() or ".." in value.parts or value.parts[:1] != ("fonts",):
        raise ValueError("Invalid bundled font path")
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Bundled font path escapes release")
    return path


def verify_bundled_fonts(root: Path) -> tuple[dict, list[str]]:
    manifest = json.loads((root / FONT_MANIFEST).read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or not manifest.get("files"):
        raise ValueError("Invalid font manifest")
    issues: list[str] = []
    for entry in manifest["files"]:
        path = _bundled_path(root, entry["relative_path"])
        if not path.is_file():
            issues.append(f"{entry['filename']}: 배포 글꼴 없음")
        else:
            data = path.read_bytes()
            if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
                issues.append(f"{entry['filename']}: 배포 글꼴 해시 불일치")
    license_path = _bundled_path(root, manifest["license_path"])
    if not license_path.is_file() or hashlib.sha256(license_path.read_bytes()).hexdigest() != manifest["license_sha256"]:
        issues.append("Pretendard: 라이선스 파일 없음 또는 해시 불일치")
    return manifest, issues


def installed_pretendard_paths() -> tuple[Path, ...]:
    if os.name != "nt":
        raise OSError("Windows font verification required")
    import winreg

    result: dict[str, Path] = {}
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            registry = winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts")
        except FileNotFoundError:
            continue
        with registry:
            index = 0
            while True:
                try:
                    label, value, _kind = winreg.EnumValue(registry, index)
                except OSError as error:
                    if error.winerror == 259:  # ERROR_NO_MORE_ITEMS
                        break
                    raise
                index += 1
                if "pretendard" not in label.casefold() or not isinstance(value, str):
                    continue
                path = Path(os.path.expandvars(value))
                if not path.is_absolute():
                    path = Path(os.environ["WINDIR"]) / "Fonts" / path
                result.setdefault(os.path.normcase(str(path)), path)
    return tuple(result.values())


def verify_installed_fonts(manifest: dict, paths: tuple[Path, ...]) -> list[str]:
    by_name: dict[str, list[Path]] = {}
    for path in paths:
        by_name.setdefault(path.name.casefold(), []).append(path)
    issues: list[str] = []
    for entry in manifest["files"]:
        matches = by_name.get(entry["filename"].casefold(), [])
        if not matches:
            issues.append(f"{entry['filename']}: Windows 설치 없음")
        elif any(not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]
                 for path in matches):
            issues.append(f"{entry['filename']}: 설치 글꼴 해시 불일치")
    expected_names = {entry["filename"].casefold() for entry in manifest["files"]}
    if set(by_name) - expected_names:
        issues.append("정본 네 파일 외의 Pretendard 설치가 있습니다. 동일 글꼴 조건을 확인해 주세요.")
    return issues


def _app_family(root) -> str:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from unit_print_manager import PrintManagerApp

    app = PrintManagerApp.__new__(PrintManagerApp)
    app.root = root
    return app.resolve_ui_font_family()


def probe_tk_font_environment(*, root_factory=None, font_factory=None, family_resolver=None) -> dict:
    import tkinter as tk
    from tkinter import font as tkfont

    root = (root_factory or tk.Tk)()
    try:
        root.withdraw()
        family = (family_resolver or _app_family)(root)
        faces = {weight: (font_factory or tkfont.Font)(root=root, family=family, size=11, weight=weight).actual()
                 for weight in ("normal", "bold")}
        return {"ui_font_family": family, "actual_faces": faces,
                "python": sys.version.split()[0], "tk": str(root.tk.call("info", "patchlevel")),
                "tk_scaling": float(root.tk.call("tk", "scaling")),
                "screen_width": root.winfo_screenwidth(), "screen_height": root.winfo_screenheight()}
    finally:
        root.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(description="Check exact bundled/installed fonts without printing or installing.")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--bundle-only", action="store_true", help="Check bundled bytes/license without Windows or Tk.")
    args = parser.parse_args()
    try:
        manifest, issues = verify_bundled_fonts(args.root)
        report = {"expected_ui_family": manifest["expected_ui_family"], "font_count": len(manifest["files"]),
                  "issues": issues}
        if not args.bundle_only:
            issues.extend(verify_installed_fonts(manifest, installed_pretendard_paths()))
            runtime = report["runtime"] = probe_tk_font_environment()
            expected = manifest["expected_ui_family"].casefold()
            if runtime["ui_font_family"].casefold() != expected:
                issues.append("앱이 선택한 글꼴이 정본 Pretendard와 다릅니다.")
            if any(face["family"].casefold() != expected for face in runtime["actual_faces"].values()):
                issues.append("Tk가 실제로 선택한 글꼴이 정본 Pretendard와 다릅니다.")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if issues else 0
    except Exception as error:
        # Do not expose registry file paths or raw exception text.
        print(json.dumps({"issues": ["글꼴 검증 실패"], "error_type": type(error).__name__}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
