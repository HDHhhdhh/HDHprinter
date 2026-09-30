"""Verify that Quick Printer runs with its documented Python and package pins."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REQUIREMENTS = PROJECT_ROOT / "unit_print_manager_requirements.txt"
PIN_PATTERN = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*==\s*([^\s;]+)\s*(?:;.*)?$")


def canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).casefold()


def load_pins(path: Path) -> dict[str, tuple[str, str]]:
    pins: dict[str, tuple[str, str]] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        match = PIN_PATTERN.fullmatch(line)
        if match is None:
            raise ValueError(f"{path.name}:{line_number}: 정확한 == 버전 핀이 필요합니다: {raw_line}")
        distribution, version = match.groups()
        pins[canonical_name(distribution)] = (distribution, version)
    return pins


def inspect_environment(requirements: Path) -> dict[str, object]:
    pins = load_pins(requirements)
    packages: list[dict[str, object]] = []
    mismatches: list[str] = []
    for _canonical, (distribution, expected) in sorted(pins.items()):
        try:
            actual = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            actual = None
        matches = actual == expected
        packages.append(
            {
                "distribution": distribution,
                "expected": expected,
                "actual": actual,
                "matches": matches,
            }
        )
        if not matches:
            mismatches.append(
                f"{distribution}: 필요 {expected}, 현재 {actual or '설치 안 됨'}"
            )
    python_matches = sys.version_info[:2] == (3, 12)
    if not python_matches:
        mismatches.insert(
            0,
            f"Python: 필요 3.12.x, 현재 {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        )
    return {
        "ok": python_matches and not mismatches,
        "python_executable": sys.executable,
        "python_version": ".".join(map(str, sys.version_info[:3])),
        "requirements": str(requirements.resolve()),
        "packages": packages,
        "mismatches": mismatches,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requirements", type=Path, default=DEFAULT_REQUIREMENTS)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        report = inspect_environment(args.requirements)
    except Exception as error:
        print(f"환경 검사 실패: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Python: {report['python_version']} ({report['python_executable']})")
        for package in report["packages"]:
            marker = "OK" if package["matches"] else "불일치"
            print(
                f"[{marker}] {package['distribution']} "
                f"{package['actual'] or '설치 안 됨'} / 필요 {package['expected']}"
            )
        if report["mismatches"]:
            print("환경 불일치:", file=sys.stderr)
            for mismatch in report["mismatches"]:
                print(f"- {mismatch}", file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
