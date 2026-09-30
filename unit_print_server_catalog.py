"""Stable server-facing identifiers for registered Quick Printer tests."""

from __future__ import annotations

import csv
import hashlib
import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from unit_print_paths import DATA_ROOT
from unit_print_test_catalog import (
    TERM_FILTER_TEST_KINDS,
    TEST_KIND_ORDER,
    TEST_SOURCE_BY_KIND,
    TestCatalog,
    scan_default_test_catalogs,
    test_filter_choices,
    test_filter_label,
)


APP_DIR = Path(__file__).resolve().parent
UNIT_CATALOG_PATH = APP_DIR / "unit_print_catalog.json"
DEFAULT_UNIT_ROOT = DATA_ROOT / "단원평가"

REGISTERED_TEST_CSV_HEADERS = (
    "자료ID",
    "자료유형",
    "과목",
    "레벨",
    "시험구분",
    "회차",
    "개별번호",
    "표시이름",
    "파일명",
    "상대경로",
)
SERVER_ORDER_CSV_HEADERS = (
    "주문번호",
    "자료ID",
    "학생이름",
    "부수",
    "메모",
)

_ID_PREFIX_BY_SOURCE = {
    "catalog": "UNIT",
    TEST_SOURCE_BY_KIND["복습테스트"]: "REVIEW",
    TEST_SOURCE_BY_KIND["학군지기출"]: "SCHOOL",
    TEST_SOURCE_BY_KIND["일산기출"]: "ILSAN",
}

# Server-side identifiers registered for the 2026-09 Ilsan midterm refresh.
# The relative path remains the authority: renaming a PDF must not silently keep
# an identifier that now points at different material.
ILSAN_SERVER_TEST_ID_BY_RELATIVE_PATH = {
    "공수2/중간/중상/1회차 25 저동고.pdf": "t_ilsan_past_gongsu2_mid_high_midterm_001",
    "공수2/중간/중상/2회차 25 대진고.pdf": "t_ilsan_past_gongsu2_mid_high_midterm_002",
    "공수2/중간/중상/3회차 25 일산동고.pdf": "t_ilsan_past_gongsu2_mid_high_midterm_003",
    "공수2/중간/중상/4회차 25 안곡고.pdf": "t_ilsan_past_gongsu2_mid_high_midterm_004",
    "공수2/중간/중상/5회차 25 화정고.pdf": "t_ilsan_past_gongsu2_mid_high_midterm_005",
    "공수2/중간/중상/6회차 25 무원고.pdf": "t_ilsan_past_gongsu2_mid_high_midterm_006",
    "공수2/중간/중상/7회차 25 가좌고.pdf": "t_ilsan_past_gongsu2_mid_high_midterm_007",
    "공수2/중간/중상/8회차 25 백양고.pdf": "t_ilsan_past_gongsu2_mid_high_midterm_008",
    "공수2/중간/중상/9회차 25 행신고.pdf": "t_ilsan_past_gongsu2_mid_high_midterm_009",
    "공수2/중간/중상/10회차 25 백마고.pdf": "t_ilsan_past_gongsu2_mid_high_midterm_010",
    "공수2/중간/상/1회차 25 대화고.pdf": "t_ilsan_past_gongsu2_high_midterm_001",
    "공수2/중간/상/2회차 25 정발고.pdf": "t_ilsan_past_gongsu2_high_midterm_002",
    "공수2/중간/상/3회차 25 고국고.pdf": "t_ilsan_past_gongsu2_high_midterm_003",
    "공수2/중간/상/4회차 25 저현고.pdf": "t_ilsan_past_gongsu2_high_midterm_004",
    "공수2/중간/상/5회차 25 운정고.pdf": "t_ilsan_past_gongsu2_high_midterm_005",
    "공수2/중간/상/6회차 25 백석고.pdf": "t_ilsan_past_gongsu2_high_midterm_006",
    "미적1/중간/중상/1회차 25 저동고.pdf": "t_ilsan_past_mijeok1_mid_high_midterm_001",
    "미적1/중간/중상/2회차 25 일산동고.pdf": "t_ilsan_past_mijeok1_mid_high_midterm_002",
    "미적1/중간/중상/3회차 25 대진고.pdf": "t_ilsan_past_mijeok1_mid_high_midterm_003",
    "미적1/중간/중상/4회차 25 안곡고.pdf": "t_ilsan_past_mijeok1_mid_high_midterm_004",
    "미적1/중간/중상/5회차 25 가좌고.pdf": "t_ilsan_past_mijeok1_mid_high_midterm_005",
    "미적1/중간/중상/6회차 25 정발고.pdf": "t_ilsan_past_mijeok1_mid_high_midterm_006",
    "미적1/중간/중상/7회차 25 저현고(문과).pdf": "t_ilsan_past_mijeok1_mid_high_midterm_007",
    "미적1/중간/중상/8회차 25 덕이고.pdf": "t_ilsan_past_mijeok1_mid_high_midterm_008",
    "미적1/중간/중상/9회차 25 동패고.pdf": "t_ilsan_past_mijeok1_mid_high_midterm_009",
    "미적1/중간/중상/10회차 25 중산고.pdf": "t_ilsan_past_mijeok1_mid_high_midterm_010",
    "미적1/중간/상/1회차 25 대화고.pdf": "t_ilsan_past_mijeok1_high_midterm_001",
    "미적1/중간/상/2회차 25 백신고.pdf": "t_ilsan_past_mijeok1_high_midterm_002",
    "미적1/중간/상/3회차 25 백석고.pdf": "t_ilsan_past_mijeok1_high_midterm_003",
    "미적1/중간/상/4회차 25 주엽고.pdf": "t_ilsan_past_mijeok1_high_midterm_004",
    "미적1/중간/상/5회차 25 운정고.pdf": "t_ilsan_past_mijeok1_high_midterm_005",
    "미적1/중간/상/6회차 25 저현고.pdf": "t_ilsan_past_mijeok1_high_midterm_006",
    "미적1/중간/상/7회차 24 저현고.pdf": "t_ilsan_past_mijeok1_high_midterm_007",
    "미적1/중간/상/8회차 23 저현고.pdf": "t_ilsan_past_mijeok1_high_midterm_008",
}


def _normalized_relative_path(relative_path: str | Path) -> str:
    return unicodedata.normalize(
        "NFC", str(relative_path).replace("\\", "/").strip("/")
    ).casefold()


_ILSAN_SERVER_TEST_ID_BY_NORMALIZED_PATH = {
    _normalized_relative_path(path): server_test_id
    for path, server_test_id in ILSAN_SERVER_TEST_ID_BY_RELATIVE_PATH.items()
}


def stable_test_material_id(source_key: str, relative_path: str | Path) -> str:
    """Return an ID that stays stable while the source-relative path is unchanged."""
    normalized_path = _normalized_relative_path(relative_path)
    payload = f"{source_key}\0{normalized_path}".encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:16].upper()
    prefix = _ID_PREFIX_BY_SOURCE.get(source_key, "TEST")
    return f"QT-{prefix}-{digest}"


@dataclass(frozen=True, slots=True)
class RegisteredTestAsset:
    material_id: str
    source: str
    source_key: str
    subject: str
    filter_label: str
    filter_value: str
    level: str
    term: str
    round_name: str
    number: int
    display_name: str
    path: Path
    relative_path: str
    server_test_id: str = ""

    @property
    def display_label(self) -> str:
        return f"{self.number:02d}. {self.display_name}"

    def csv_row(self) -> tuple[object, ...]:
        return (
            self.material_id,
            self.source,
            self.subject,
            self.level,
            self.term,
            self.round_name,
            self.number,
            self.display_label,
            self.path.name,
            self.relative_path,
        )


def _unit_assets(unit_catalog: Mapping[str, object], unit_root: Path) -> list[RegisteredTestAsset]:
    assets: list[RegisteredTestAsset] = []
    for subject_data in unit_catalog.get("subjects", []):
        if not isinstance(subject_data, dict):
            continue
        subject = str(subject_data.get("name", "")).strip()
        for level_data in subject_data.get("levels", []):
            if not isinstance(level_data, dict):
                continue
            level = str(level_data.get("name", "")).strip()
            for round_data in level_data.get("rounds", []):
                if not isinstance(round_data, dict):
                    continue
                round_name = str(round_data.get("name", "")).strip()
                for file_data in round_data.get("files", []):
                    if not isinstance(file_data, dict):
                        continue
                    relative_path = str(file_data.get("relative_path", "")).strip()
                    if not relative_path:
                        continue
                    number = int(file_data.get("number", 0))
                    display_name = str(file_data.get("name", "")).strip()
                    assets.append(
                        RegisteredTestAsset(
                            material_id=stable_test_material_id("catalog", relative_path),
                            source="단원평가",
                            source_key="catalog",
                            subject=subject,
                            filter_label="레벨",
                            filter_value=level,
                            level=level,
                            term="",
                            round_name=round_name,
                            number=number,
                            display_name=display_name,
                            path=unit_root / Path(relative_path),
                            relative_path=relative_path.replace("\\", "/"),
                        )
                    )
    return assets


def _supplemental_test_assets(
    test_catalogs: Mapping[str, TestCatalog],
) -> list[RegisteredTestAsset]:
    assets: list[RegisteredTestAsset] = []
    for kind in TEST_KIND_ORDER:
        catalog = test_catalogs.get(kind)
        if catalog is None:
            continue
        source_key = TEST_SOURCE_BY_KIND[kind]
        filter_label = test_filter_label(kind)
        for subject in catalog.subjects:
            for filter_value in test_filter_choices(kind):
                for document in subject.documents(filter_value):
                    relative_path = document.relative_path.as_posix()
                    server_test_id = (
                        _ILSAN_SERVER_TEST_ID_BY_NORMALIZED_PATH.get(
                            _normalized_relative_path(relative_path), ""
                        )
                        if source_key == TEST_SOURCE_BY_KIND["일산기출"]
                        else ""
                    )
                    assets.append(
                        RegisteredTestAsset(
                            material_id=stable_test_material_id(source_key, relative_path),
                            source=kind,
                            source_key=source_key,
                            subject=subject.name,
                            filter_label=filter_label,
                            filter_value=filter_value,
                            level=document.level or filter_value,
                            term=(
                                filter_value
                                if kind in TERM_FILTER_TEST_KINDS
                                else (document.term or "")
                            ),
                            round_name="",
                            number=document.number,
                            display_name=document.label,
                            path=document.path,
                            relative_path=relative_path,
                            server_test_id=server_test_id,
                        )
                    )
    return assets


def build_registered_test_assets(
    unit_catalog: Mapping[str, object],
    unit_root: Path,
    test_catalogs: Mapping[str, TestCatalog],
) -> tuple[RegisteredTestAsset, ...]:
    assets = [
        *_unit_assets(unit_catalog, Path(unit_root)),
        *_supplemental_test_assets(test_catalogs),
    ]
    seen: dict[str, RegisteredTestAsset] = {}
    for asset in assets:
        for identifier in (asset.material_id, asset.server_test_id):
            if not identifier:
                continue
            key = identifier.casefold()
            previous = seen.get(key)
            if previous is not None:
                raise ValueError(
                    "등록 테스트 식별자가 중복되었습니다: "
                    f"{identifier} ({previous.relative_path}, {asset.relative_path})"
                )
            seen[key] = asset
    return tuple(assets)


def registered_test_asset_index(
    assets: tuple[RegisteredTestAsset, ...] | list[RegisteredTestAsset],
) -> dict[str, RegisteredTestAsset]:
    index: dict[str, RegisteredTestAsset] = {}
    for asset in assets:
        index[asset.material_id.casefold()] = asset
        if asset.server_test_id:
            index[asset.server_test_id.casefold()] = asset
    return index


def load_default_unit_catalog(path: Path = UNIT_CATALOG_PATH) -> dict[str, object]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_default_registered_test_assets() -> tuple[RegisteredTestAsset, ...]:
    return build_registered_test_assets(
        load_default_unit_catalog(),
        DEFAULT_UNIT_ROOT,
        scan_default_test_catalogs(),
    )


def export_registered_test_catalog_csv(
    path: Path,
    assets: tuple[RegisteredTestAsset, ...] | list[RegisteredTestAsset],
) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8-sig", newline="") as output_file:
        writer = csv.writer(output_file)
        writer.writerow(REGISTERED_TEST_CSV_HEADERS)
        writer.writerows(asset.csv_row() for asset in assets)
    return destination


def write_server_order_template(path: Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8-sig", newline="") as output_file:
        csv.writer(output_file).writerow(SERVER_ORDER_CSV_HEADERS)
    return destination
