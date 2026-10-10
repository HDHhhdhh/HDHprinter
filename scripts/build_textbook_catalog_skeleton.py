from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from pypdf import PdfReader


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = Path.home() / "Desktop" / "교과서"
DEFAULT_OUTPUT = PROJECT_ROOT / "unit_print_textbook_catalog.json"
SUBJECT_ORDER = ("공수1", "공수2", "기하", "대수", "미적1", "미적2", "확통")
PUBLISHER_ORDER = (
    "동아출판",
    "미래엔",
    "비상",
    "지학사",
    "천재(전인태)",
    "천재(홍진곤)",
    "YBM",
)
GENERIC_CHEONJAE_LABEL = {
    "기하": "천재(전인태)",
    "대수": "천재(전인태)",
    "미적1": "천재(홍진곤)",
    "미적2": "천재(홍진곤)",
    "확통": "천재(전인태)",
}
FILENAME_PATTERN = re.compile(
    r"^(?P<subject>공수1|공수2|기하|대수|미적1|미적2|확통) "
    r"(?P<publisher>.+) (?P<kind>문제|해설)[.]pdf$",
    re.IGNORECASE,
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_existing(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        (str(subject.get("id", "")), str(edition.get("publisher", ""))): edition
        for subject in payload.get("subjects", [])
        if isinstance(subject, dict)
        for edition in subject.get("editions", [])
        if isinstance(edition, dict)
    }


def publisher_label(subject: str, filename_token: str) -> str:
    if filename_token == "천재":
        try:
            return GENERIC_CHEONJAE_LABEL[subject]
        except KeyError as error:
            raise ValueError(f"저자 대응이 없는 천재 파일입니다: {subject}") from error
    return filename_token


def asset_row(path: Path, root: Path) -> dict[str, object]:
    return {
        "relative_path": path.relative_to(root).as_posix(),
        "page_count": len(PdfReader(path).pages),
        "size_bytes": path.stat().st_size,
        "sha256": file_sha256(path),
    }


def metadata_matches(existing: object, current: dict[str, object]) -> bool:
    if not isinstance(existing, dict):
        return False
    return all(existing.get(key) == value for key, value in current.items())


def build_catalog(root: Path, existing_path: Path) -> dict[str, object]:
    files = sorted(root.rglob("*.pdf"), key=lambda path: str(path).casefold())
    if len(files) != 84:
        raise ValueError(f"교과서 PDF는 84개여야 합니다: {len(files)}개")
    pairs: dict[tuple[str, str], dict[str, Path]] = {}
    for path in files:
        match = FILENAME_PATTERN.fullmatch(path.name)
        if match is None:
            raise ValueError(f"교과서 파일명을 해석할 수 없습니다: {path}")
        subject = match.group("subject")
        publisher = publisher_label(subject, match.group("publisher"))
        kind = match.group("kind")
        pair = pairs.setdefault((subject, publisher), {})
        if kind in pair:
            raise ValueError(f"교과서 {kind} PDF가 중복되었습니다: {subject}/{publisher}")
        pair[kind] = path
    if len(pairs) != 42 or any(set(pair) != {"문제", "해설"} for pair in pairs.values()):
        raise ValueError("교과서 42종의 문제/해설 쌍이 완전하지 않습니다.")

    existing = load_existing(existing_path)
    publisher_order = {name: index for index, name in enumerate(PUBLISHER_ORDER)}
    subjects: list[dict[str, object]] = []
    for subject in SUBJECT_ORDER:
        editions: list[dict[str, object]] = []
        subject_pairs = sorted(
            ((publisher, pair) for (pair_subject, publisher), pair in pairs.items() if pair_subject == subject),
            key=lambda item: publisher_order.get(item[0], len(publisher_order)),
        )
        for publisher, pair in subject_pairs:
            problem = asset_row(pair["문제"], root)
            solution = asset_row(pair["해설"], root)
            prior = existing.get((subject, publisher), {})
            preserve_mapping = (
                metadata_matches(prior.get("problem"), problem)
                and metadata_matches(prior.get("solution"), solution)
            )
            editions.append(
                {
                    "publisher": publisher,
                    "mapping_status": (
                        str(prior.get("mapping_status", "pending"))
                        if preserve_mapping
                        else "pending"
                    ),
                    "problem": problem,
                    "solution": solution,
                    "lessons": list(prior.get("lessons", [])) if preserve_mapping else [],
                }
            )
        subjects.append({"id": subject, "label": subject, "editions": editions})
    return {"schema_version": 1, "subjects": subjects}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inventory the 84 textbook PDFs without inventing lesson page mappings."
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    output = args.output.resolve()
    catalog = build_catalog(root, output)
    output.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    edition_count = sum(len(subject["editions"]) for subject in catalog["subjects"])
    print(f"Wrote {output}: {edition_count} textbook editions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
