"""Stage and install consistent titles on the 15 textbook-variant PDF covers.

The stage step does not change registered assets or the live catalog. The
install step checks the old and new hashes, backs up originals, then replaces
the PDFs and catalog together. No problem/solution PDF is touched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont


COVER_RECT = pymupdf.Rect(60, 184, 535, 260)
TITLE_RECT = pymupdf.Rect(60, 201, 535, 247)
EXPECTED_VARIANTS = 15


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def variant_entries(catalog: dict) -> list[tuple[str, str, dict]]:
    entries = [
        (subject["id"], edition["publisher"], edition["variant"])
        for subject in catalog["subjects"]
        for edition in subject["editions"]
        if edition.get("variant") is not None
    ]
    if len(entries) != EXPECTED_VARIANTS:
        raise ValueError(f"핵심문제 변형 자료는 15종이어야 합니다: {len(entries)}")
    return entries


def asset_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or path.suffix.lower() != ".pdf":
        raise ValueError(f"교과서 자료 폴더 밖의 PDF입니다: {relative}")
    return path


def patch_cover(source: Path, destination: Path, title: str, font_file: Path) -> None:
    with pymupdf.open(source) as document:
        page = document[0]
        title_spans = [
            span
            for block in page.get_text("dict")["blocks"] if "lines" in block
            for line in block["lines"] for span in line["spans"]
            if pymupdf.Rect(span["bbox"]).intersects(COVER_RECT)
        ]
        if not title_spans or any(
            not COVER_RECT.contains(pymupdf.Rect(span["bbox"]))
            for span in title_spans
        ):
            raise ValueError(f"표지 제목 영역을 확인할 수 없습니다: {source}")
        page.add_redact_annot(COVER_RECT, fill=(1, 1, 1))
        page.apply_redactions(images=0, graphics=0)
        page.insert_font(fontname="VariantCover", fontfile=str(font_file))
        if page.insert_textbox(
            TITLE_RECT, title, fontsize=29, fontname="VariantCover",
            align=pymupdf.TEXT_ALIGN_CENTER, color=(0, 0, 0),
        ) < 0:
            raise ValueError(f"표지 제목이 영역을 넘습니다: {title}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        document.save(destination, garbage=4, deflate=True)


def verify_patch(original: Path, patched: Path, title: str, page_count: int) -> None:
    with pymupdf.open(original) as before, pymupdf.open(patched) as after:
        if len(before) != page_count or len(after) != page_count:
            raise ValueError(f"PDF 페이지 수가 바뀌었습니다: {patched}")
        first_text = after[0].get_text().replace(" ", "").replace("\n", "")
        if title.replace(" ", "") not in first_text:
            raise ValueError(f"표지 제목 검증 실패: {patched}")
        for page_number in range(1, page_count):
            if before[page_number].get_text() != after[page_number].get_text():
                raise ValueError(f"본문 텍스트가 바뀌었습니다: {patched} {page_number + 1}쪽")
        for page_number in {1, page_count // 2, page_count - 1}:
            if page_number == 0:
                continue
            before_pixels = before[page_number].get_pixmap(alpha=False).samples
            after_pixels = after[page_number].get_pixmap(alpha=False).samples
            if before_pixels != after_pixels:
                raise ValueError(f"본문 화면이 바뀌었습니다: {patched} {page_number + 1}쪽")


def make_contact_sheet(paths: list[tuple[str, Path]], destination: Path, font_file: Path) -> None:
    width, height, label_height = 320, 455, 28
    sheet = Image.new("RGB", (width * 5, (height + label_height) * 3), "#e7edf3")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype(str(font_file), 15)
    for index, (title, path) in enumerate(paths):
        with pymupdf.open(path) as document:
            page = document[0]
            pix = page.get_pixmap(matrix=pymupdf.Matrix(width / page.rect.width, height / page.rect.height), alpha=False)
            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        x, y = (index % 5) * width, (index // 5) * (height + label_height)
        sheet.paste(image, (x, y))
        draw.text((x + 5, y + height + 4), title, fill="#1b334d", font=font)
    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(destination)


def stage(catalog_path: Path, source_root: Path, output_root: Path, font_file: Path) -> None:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    entries = variant_entries(catalog)
    # Check the complete input set before making staged output.
    for _subject, _publisher, asset in entries:
        source = asset_path(source_root, asset["relative_path"])
        if not source.is_file() or source.stat().st_size != asset["size_bytes"] or sha256(source) != asset["sha256"]:
            raise ValueError(f"등록된 원본과 파일이 다릅니다: {source}")

    manifest: list[dict] = []
    covers: list[tuple[str, Path]] = []
    for subject, publisher, asset in entries:
        relative = asset["relative_path"]
        source = asset_path(source_root, relative)
        destination = asset_path(output_root / "data" / "교과서", relative)
        title = f"{subject} {publisher} 핵심문제 변형"
        patch_cover(source, destination, title, font_file)
        verify_patch(source, destination, title, asset["page_count"])
        old_sha = asset["sha256"]
        asset["size_bytes"] = destination.stat().st_size
        asset["sha256"] = sha256(destination)
        manifest.append({
            "relative_path": relative, "title": title,
            "page_count": asset["page_count"],
            "old_sha256": old_sha, "new_sha256": asset["sha256"],
        })
        covers.append((title, destination))
        print(f"{len(manifest):02d}/15 {title}", flush=True)

    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "unit_print_textbook_catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_root / "variant_cover_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    make_contact_sheet(covers, output_root / "variant_cover_contact_sheet.png", font_file)


def _replace_from(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".variant-cover-", suffix=".pdf", dir=destination.parent)
    os.close(fd)
    temporary_path = Path(temporary)
    try:
        shutil.copy2(source, temporary_path)
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def install(catalog_path: Path, source_root: Path, output_root: Path) -> None:
    manifest = json.loads((output_root / "variant_cover_manifest.json").read_text(encoding="utf-8"))
    if len(manifest) != EXPECTED_VARIANTS:
        raise ValueError("설치할 변형 표지가 15종이 아닙니다.")
    current_catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    staged_catalog = json.loads((output_root / "unit_print_textbook_catalog.json").read_text(encoding="utf-8"))
    if len(variant_entries(current_catalog)) != EXPECTED_VARIANTS or len(variant_entries(staged_catalog)) != EXPECTED_VARIANTS:
        raise ValueError("교과서 카탈로그의 변형 자료 수가 달라졌습니다.")
    backup_root = output_root / "original_backup"
    checks: list[tuple[Path, Path, Path]] = []
    for row in manifest:
        relative = row["relative_path"]
        current = asset_path(source_root, relative)
        staged = asset_path(output_root / "data" / "교과서", relative)
        backup = asset_path(backup_root, relative)
        if sha256(current) != row["old_sha256"] or sha256(staged) != row["new_sha256"]:
            raise ValueError(f"설치 전 PDF 해시가 다릅니다: {relative}")
        checks.append((current, staged, backup))
    old_catalog = catalog_path.read_bytes()
    installed: list[tuple[Path, Path]] = []
    try:
        for current, _staged, backup in checks:
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(current, backup)
        for current, staged, backup in checks:
            _replace_from(staged, current)
            installed.append((current, backup))
        catalog_path.write_bytes((output_root / "unit_print_textbook_catalog.json").read_bytes())
    except Exception:
        for current, backup in reversed(installed):
            _replace_from(backup, current)
        catalog_path.write_bytes(old_catalog)
        raise
    for row in manifest:
        if sha256(asset_path(source_root, row["relative_path"])) != row["new_sha256"]:
            raise ValueError(f"설치 후 PDF 해시가 다릅니다: {row['relative_path']}")
    print("15종 PDF와 카탈로그 설치 완료", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("stage", "install"))
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--font", type=Path, default=Path(r"C:\Windows\Fonts\HANBatangB.ttf"))
    args = parser.parse_args()
    if args.mode == "stage":
        stage(args.catalog, args.source_root, args.output, args.font)
    else:
        install(args.catalog, args.source_root, args.output)


if __name__ == "__main__":
    main()
