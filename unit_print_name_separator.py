from __future__ import annotations

import hashlib
import os
import threading
import unicodedata
from collections.abc import Iterable
from pathlib import Path

from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


NAME_SEPARATOR_LAYOUT_VERSION = "2"
MAX_NAME_SEPARATOR_LABEL_LENGTH = 24
MAX_FONT_SIZE = 32.0
MIN_FONT_SIZE = 18.0
SIDE_MARGIN_RATIO = 0.10
TOP_MARGIN = 11.0 * mm

_FONT_LOCK = threading.Lock()
_REGISTERED_FONTS: dict[Path, str] = {}


def normalize_name_separator_label(value: object) -> str:
    """Return a printable one-line label for a per-block name separator."""
    text = unicodedata.normalize("NFC", str(value or ""))
    text = " ".join(text.split())
    if not text:
        return ""
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in text):
        raise ValueError("이름 구분지에는 제어 문자를 사용할 수 없습니다.")
    if len(text) > MAX_NAME_SEPARATOR_LABEL_LENGTH:
        raise ValueError(
            f"이름 구분지는 {MAX_NAME_SEPARATOR_LABEL_LENGTH}자 이내로 입력해 주세요."
        )
    return text


def name_separator_fill_plan(labels: Iterable[object]) -> tuple[str, tuple[int, ...]]:
    """Find the one shared name and positions of selected blank labels."""
    normalized = tuple(normalize_name_separator_label(label) for label in labels)
    names = {label for label in normalized if label}
    if not names:
        raise ValueError("선택한 블록 중 이름 구분지가 입력된 블록이 없습니다.")
    if len(names) != 1:
        raise ValueError("서로 다른 이름 구분지가 선택되었습니다. 이름별로 나누어 선택해 주세요.")
    blanks = tuple(index for index, label in enumerate(normalized) if not label)
    if not blanks:
        raise ValueError("선택한 블록에 이름을 채울 빈 블록이 없습니다.")
    return next(iter(names)), blanks


def find_name_separator_font() -> Path:
    windows_dir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    candidates = (
        windows_dir / "Fonts" / "malgunbd.ttf",
        windows_dir / "Fonts" / "malgun.ttf",
        windows_dir / "Fonts" / "NanumGothicBold.ttf",
        windows_dir / "Fonts" / "NanumGothic.ttf",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "이름 구분지용 한글 글꼴을 찾을 수 없습니다. Windows의 '맑은 고딕' 글꼴을 확인해 주세요."
    )


def _registered_font_name(font_path: Path) -> str:
    resolved = font_path.resolve()
    with _FONT_LOCK:
        registered = _REGISTERED_FONTS.get(resolved)
        if registered is not None:
            return registered
        digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]
        font_name = f"QuickPrinterNameSeparator_{digest}"
        pdfmetrics.registerFont(TTFont(font_name, str(resolved)))
        _REGISTERED_FONTS[resolved] = font_name
        return font_name


def _cache_fingerprint(label: str, font_path: Path) -> str:
    stat = font_path.stat()
    payload = "\n".join(
        (
            NAME_SEPARATOR_LAYOUT_VERSION,
            label,
            str(font_path.resolve()),
            str(stat.st_size),
            str(stat.st_mtime_ns),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _is_valid_cached_separator(path: Path, label: str) -> bool:
    if not path.is_file() or path.stat().st_size < 500:
        return False
    try:
        reader = PdfReader(str(path))
        if len(reader.pages) != 1:
            return False
        page = reader.pages[0]
        page_size = A4
        return (
            abs(float(page.mediabox.width) - page_size[0]) < 1
            and abs(float(page.mediabox.height) - page_size[1]) < 1
            and normalize_name_separator_label(page.extract_text() or "") == label
        )
    except Exception:
        return False


def materialize_name_separator_pdf(label: object, output_dir: Path) -> Path:
    """Create or reuse a single-page portrait A4 name separator."""
    normalized = normalize_name_separator_label(label)
    if not normalized:
        raise ValueError("이름 구분지에 사용할 이름을 입력해 주세요.")

    font_path = find_name_separator_font()
    output_dir.mkdir(parents=True, exist_ok=True)
    fingerprint = _cache_fingerprint(normalized, font_path)
    destination = output_dir / f"name_separator_{fingerprint}.pdf"
    if _is_valid_cached_separator(destination, normalized):
        return destination

    font_name = _registered_font_name(font_path)
    page_width, page_height = A4
    usable_width = page_width * (1.0 - 2.0 * SIDE_MARGIN_RATIO)
    width_at_one_point = pdfmetrics.stringWidth(normalized, font_name, 1.0)
    if width_at_one_point <= 0:
        raise ValueError("이름 구분지 글자 폭을 계산할 수 없습니다.")
    font_size = min(MAX_FONT_SIZE, usable_width / width_at_one_point)
    if font_size < MIN_FONT_SIZE:
        raise ValueError("이름 구분지 이름이 너무 깁니다. 더 짧게 입력해 주세요.")

    temporary = output_dir / (
        f".{destination.stem}.{os.getpid()}.{threading.get_ident()}.tmp.pdf"
    )
    try:
        document = canvas.Canvas(str(temporary), pagesize=(page_width, page_height))
        document.setTitle(f"이름 구분지 - {normalized}")
        document.setAuthor("빠른 인쇄기")
        document.setFillColorRGB(1, 1, 1)
        document.rect(0, 0, page_width, page_height, stroke=0, fill=1)
        document.setFillColorRGB(0, 0, 0)
        document.setFont(font_name, font_size)
        ascent, _descent = pdfmetrics.getAscentDescent(font_name, font_size)
        baseline = page_height - TOP_MARGIN - ascent
        document.drawCentredString(page_width / 2.0, baseline, normalized)
        document.showPage()
        document.save()
        if not _is_valid_cached_separator(temporary, normalized):
            raise RuntimeError("이름 구분지 PDF 생성 결과를 검증하지 못했습니다.")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination
