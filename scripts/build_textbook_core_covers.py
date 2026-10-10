"""Stage two-page core-problem covers and the matching catalog update.

The source PDFs and catalog are never changed by this command. A reviewed staging
directory can then be installed as one batch after validation.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path

import pymupdf
import qrcode
import zxingcpp
from PIL import Image


SUBJECT_TITLES = {
    "공수1": "공통수학1",
    "공수2": "공통수학2",
    "기하": "기하",
    "대수": "대수",
    "미적1": "미적분1",
    "미적2": "미적분2",
    "확통": "확률과 통계",
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_qr(url: str) -> bytes:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def add_covers(template: Path, source: Path, destination: Path, title: str, url: str) -> None:
    covers = pymupdf.open(template)
    original = pymupdf.open(source)
    if len(covers) < 2:
        raise ValueError(f"표지 템플릿에 2쪽이 없습니다: {template}")
    first, second = covers[0], covers[1]
    first.add_redact_annot(pymupdf.Rect(77, 198, 519, 245), fill=(1, 1, 1))
    first.apply_redactions(images=0, graphics=0)
    second.add_redact_annot(pymupdf.Rect(85, 69, 501, 246), fill=(1, 1, 1))
    second.add_redact_annot(pymupdf.Rect(128, 269, 445, 309), fill=(1, 1, 1))
    second.apply_redactions(images=2, graphics=0)

    font_file = r"C:\Windows\Fonts\malgunbd.ttf"
    for page in (first, second):
        page.insert_font(fontname="CoverHangul", fontfile=font_file)
    size = 23 if len(title) <= 19 else 20 if len(title) <= 24 else 18
    inserted = first.insert_textbox(
        pymupdf.Rect(72, 201, 523, 247), title,
        fontsize=size, fontname="CoverHangul", align=pymupdf.TEXT_ALIGN_CENTER,
        color=(0, 0, 0),
    )
    if inserted < 0:
        raise ValueError(f"표지 제목이 영역을 넘습니다: {title}")
    second.insert_image(pymupdf.Rect(208, 71, 387, 250), stream=make_qr(url))
    second.insert_textbox(
        pymupdf.Rect(142, 277, 454, 307), "해설 PDF",
        fontsize=18, fontname="CoverHangul", align=pymupdf.TEXT_ALIGN_CENTER,
        color=(0, 0, 0),
    )

    combined = pymupdf.open()
    combined.insert_pdf(covers, from_page=0, to_page=1)
    combined.insert_pdf(original)
    combined.subset_fonts()
    destination.parent.mkdir(parents=True, exist_ok=True)
    combined.save(destination, garbage=4, deflate=True)
    combined.close()
    covers.close()
    original.close()


def verify_cover(path: Path, old_page_count: int, title: str, url: str) -> None:
    document = pymupdf.open(path)
    if len(document) != old_page_count + 2:
        raise ValueError(f"본문 페이지가 누락되었습니다: {path}")
    first_text = document[0].get_text().replace(" ", "")
    if title.replace(" ", "") not in first_text:
        raise ValueError(f"표지 제목 검증 실패: {path}")
    second = document[1]
    pixmap = second.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    decoded = [item.text for item in zxingcpp.read_barcodes(image)]
    if decoded != [url]:
        raise ValueError(f"QR 검증 실패: {path}: {decoded!r}")
    document.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--links", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    links = json.loads(args.links.read_text(encoding="utf-8"))
    manifest = []
    for subject in catalog["subjects"]:
        for edition in subject["editions"]:
            if edition.get("mapping_status") != "verified":
                continue
            if edition.get("core_cover_pages"):
                raise ValueError("이미 표지가 등록된 카탈로그에서 다시 만들 수 없습니다.")
            core = edition["core"]
            relative = core["relative_path"]
            source = args.source_root / relative
            if file_sha256(source) != core["sha256"]:
                raise ValueError(f"원본 핵심문제 SHA-256 불일치: {relative}")
            solution_relative = edition["solution"]["relative_path"]
            link = links.get(solution_relative)
            if not link or not link.startswith("https://drive.google.com/file/d/"):
                raise ValueError(f"해설 Drive 링크 누락: {solution_relative}")
            title = f"{SUBJECT_TITLES[subject['id']]} {edition['publisher']} 핵심문제"
            destination = args.output / "data" / "교과서" / relative
            add_covers(args.template, source, destination, title, link)
            verify_cover(destination, core["page_count"], title, link)
            old_count = core["page_count"]
            core.update(
                page_count=old_count + 2,
                size_bytes=destination.stat().st_size,
                sha256=file_sha256(destination),
            )
            edition["core_cover_pages"] = 2
            for section in edition["core_sections"]:
                section["page_start"] += 2
                section["page_end"] += 2
            edition["core_excluded_pages"] = [page + 2 for page in edition.get("core_excluded_pages", [])]
            manifest.append({
                "subject": subject["id"], "publisher": edition["publisher"],
                "relative_path": relative, "title": title,
                "solution_drive_url": link, "pages_before": old_count,
                "pages_after": old_count + 2, "sha256": core["sha256"],
                "size_bytes": core["size_bytes"],
            })
            print(f"{len(manifest):02d}/42 {title} ({old_count + 2}쪽)", flush=True)
    if len(manifest) != 42:
        raise ValueError(f"검증된 핵심문제 수가 42개가 아닙니다: {len(manifest)}")
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "unit_print_textbook_catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output / "core_cover_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
