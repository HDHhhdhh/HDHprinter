from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from unit_print_materials import (  # noqa: E402
    DEFAULT_ABLE_ROOT,
    DEFAULT_APEX_ROOT,
    DEFAULT_FREQUENT_TYPE_ROOT,
    DEFAULT_TRAINING_PACK_ROOT,
    parse_apex_filename,
    scan_able_catalog,
    scan_frequent_type_catalog,
    scan_training_pack_catalog,
)
from unit_print_gyopyeong import (  # noqa: E402
    DEFAULT_GYOPYEONG_ROOT,
    scan_gyopyeong_catalog,
)
from unit_print_selected_exams import (  # noqa: E402
    DEFAULT_SELECTED_EXAM_ROOT,
    scan_selected_exam_catalog,
)
from unit_print_test_catalog import (  # noqa: E402
    DEFAULT_TEST_ROOTS,
    TEST_KIND_ORDER,
    scan_test_catalog,
)
from unit_print_paths import DATA_ROOT  # noqa: E402
from unit_print_textbooks import (  # noqa: E402
    load_textbook_catalog,
    verify_textbook_asset,
)


APP_FILES = (
    "unit_print_manager.py",
    "unit_print_paths.py",
    "unit_print_keyboard.py",
    "unit_print_text_shortcuts.py",
    "unit_print_gyopyeong.py",
    "unit_print_selected_exams.py",
    "unit_print_orders.py",
    "unit_print_server_catalog.py",
    "unit_print_materials.py",
    "unit_print_name_separator.py",
    "unit_print_pdf_printing.py",
    "unit_print_pdf_metadata.py",
    "unit_print_device_state.py",
    "unit_print_canon_status.py",
    "unit_print_device_credentials.py",
    "unit_print_device_monitor.py",
    "unit_print_dispatch_state.py",
    "unit_print_device_scheduler.py",
    "unit_print_device_ui.py",
    "unit_print_eta.py",
    "unit_print_resource_estimate.py",
    "unit_print_notifications.py",
    "unit_print_taskbar.py",
    "unit_print_printer_domain.py",
    "unit_print_printer_binding.py",
    "unit_print_printer_profiles.py",
    "unit_print_printer_setup.py",
    "unit_print_retry.py",
    "unit_print_progress.py",
    "unit_print_preparation.py",
    "unit_print_queue_state.py",
    "unit_print_queue_render.py",
    "unit_print_queue_recovery.py",
    "unit_print_recovery_history.py",
    "unit_print_history.py",
    "unit_print_history_events.py",
    "unit_print_toggle_help.py",
    "unit_print_test_catalog.py",
    "unit_print_textbooks.py",
    "unit_print_textbook_catalog.json",
    "unit_print_catalog.json",
    "unit_print_manager_requirements.txt",
    "unit_print_manager_README.md",
    "빠른인쇄기_키보드_사용가이드.md",
    "빠른인쇄기_대량주문서_양식.csv",
    "빠른인쇄기_대량주문서_가이드.md",
    "빠른인쇄기_서버연동/빠른인쇄기_등록_테스트목록.csv",
    "빠른인쇄기_서버연동/빠른인쇄기_서버주문_양식.csv",
    "빠른인쇄기_서버연동/빠른인쇄기_서버연동_가이드.md",
    "빠른인쇄기_새PC_설치_인수검사_프롬프트.md",
    "빠른인쇄기_프린터_ComputerUse_설정검증.md",
    "APEX_공통수학1_차시별_인쇄페이지.md",
    "APEX_공통수학2_차시별_인쇄페이지.md",
    "APEX_대수_차시별_인쇄페이지.md",
    "APEX_Boost_차시별_인쇄페이지.md",
    "트레이닝팩_차시별_인쇄페이지.md",
    "빈출유형_차시별_인쇄페이지.md",
    "run_unit_print_manager.cmd",
    "run_unit_print_manager_hidden.vbs",
    "install_quick_printer.cmd",
    "scripts/install_quick_printer.ps1",
    "scripts/setup_quick_printer_profiles.py",
    "scripts/verify_quick_printer_release.py",
    "scripts/verify_quick_printer_environment.py",
    "scripts/benchmark_quick_printer_preparation.py",
    "scripts/build_quick_printer_release.py",
    "scripts/quick_printer_asset_sync.py",
    "scripts/split_quick_printer_release.ps1",
    "scripts/merge_quick_printer_release_parts.ps1",
    "scripts/build_unit_print_catalog.py",
    "scripts/capture_quick_print_gui.py",
    "scripts/smoke_quick_print_orders.py",
    "scripts/export_quick_printer_server_catalog.py",
    "scripts/verify_quick_printer_server_integration.py",
    "quick_printer_asset_channel.example.json",
    "quick_printer_local_paths.example.json",
    "sync_quick_printer_assets.cmd",
    "빠른인쇄기_제품동작_정본.md",
    "output/unit_print_manager/quick_print_icon.ico",
    "output/unit_print_manager/quick_print_icon.png",
)

MUTABLE_RELEASE_FILES = frozenset(
    {
        "unit_print_manager_config.json",
        "unit_print_material_catalog.json",
        "release_manifest.json",
        "quick_printer_asset_channel.local.json",
        ".quick_printer_asset_manifest.json",
        ".quick_printer_asset_state.json",
    }
)

LEGACY_BLANK_REVIEW_PATH = Path(r"D:\교재\APEX\백지 리뷰 짧.pdf")
LEGACY_OMR_PATH = Path(r"D:\테스트\OMR 내신.pdf")
PORTABLE_BLANK_REVIEW_PATH = DATA_ROOT / "백지리뷰" / "백지 리뷰 짧.pdf"
PORTABLE_OMR_PATH = DATA_ROOT / "OMR" / "OMR 내신.pdf"
DEFAULT_BLANK_REVIEW_PATH = (
    PORTABLE_BLANK_REVIEW_PATH
    if PORTABLE_BLANK_REVIEW_PATH.is_file()
    else LEGACY_BLANK_REVIEW_PATH
)
DEFAULT_OMR_PATH = PORTABLE_OMR_PATH if PORTABLE_OMR_PATH.is_file() else LEGACY_OMR_PATH
PRIVATE_TEMPLATE_FILES = (
    "빠른인쇄기_새PC_설치_인수검사_프롬프트.md",
    "빠른인쇄기_프린터_ComputerUse_설정검증.md",
)


@dataclass(frozen=True, slots=True)
class PdfSource:
    category: str
    source: Path
    relative_path: Path


@dataclass(frozen=True, slots=True)
class PdfManifestEntry:
    category: str
    relative_path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ReleaseFileManifestEntry:
    relative_path: str
    size: int
    sha256: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a portable Quick Printer release with every registered PDF.")
    parser.add_argument("--destination", type=Path, required=True, help="New release directory; it must not exist.")
    parser.add_argument("--archive", type=Path, required=True, help="Output Zip64 archive path; it must not exist.")
    parser.add_argument(
        "--department-id",
        default="수신 PC에서 사용자가 직접 입력",
        help="Instruction shown in the guide; omit to keep credentials out of the release.",
    )
    parser.add_argument(
        "--department-pin",
        default="수신 PC에서 사용자가 직접 입력",
        help="Instruction shown in the guide; omit to keep credentials out of the release.",
    )
    parser.add_argument(
        "--reference-gui",
        type=Path,
        help="Optional visual-QA directory copied verbatim into reference_gui/.",
    )
    parser.add_argument(
        "--drive-folder-url",
        required=True,
        help="Google Drive folder URL written to the external Codex handoff guide.",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object expected: {path}")
    return payload


def extract_drive_folder_id(value: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError("Google Drive folder URL is required.")
    parsed = urlparse(text)
    if not parsed.scheme and "/" not in text and "?" not in text:
        folder_id = text
    else:
        query_id = parse_qs(parsed.query).get("id", [""])[0]
        parts = [part for part in parsed.path.split("/") if part]
        folder_id = query_id
        if "folders" in parts:
            folder_index = parts.index("folders")
            if folder_index + 1 < len(parts):
                folder_id = parts[folder_index + 1]
    if not folder_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for character in folder_id):
        raise ValueError(f"Google Drive folder ID를 URL에서 읽지 못했습니다: {value}")
    return folder_id


def collect_unit_sources(config: dict[str, Any]) -> list[PdfSource]:
    catalog = load_json(PROJECT_ROOT / "unit_print_catalog.json")
    portable_root = DATA_ROOT / "단원평가"
    root = Path(
        str(
            config.get("root_dir")
            or (portable_root if portable_root.is_dir() else r"D:\테스트\단원평가")
        )
    )
    sources: list[PdfSource] = []
    for subject in catalog.get("subjects", []):
        for level in subject.get("levels", []):
            for round_info in level.get("rounds", []):
                for item in round_info.get("files", []):
                    relative = Path(str(item["relative_path"]))
                    sources.append(PdfSource("단원평가", root / relative, Path("data") / "단원평가" / relative))
    return sources


def collect_test_sources() -> list[PdfSource]:
    portable_folders = {
        "복습테스트": "복습 테스트",
        "학군지기출": "학군지 기출",
        "일산기출": "일산 기출",
    }
    sources: list[PdfSource] = []
    for kind in TEST_KIND_ORDER:
        catalog = scan_test_catalog(DEFAULT_TEST_ROOTS[kind], kind)
        for subject in catalog.subjects:
            for document in (*subject.middle_high, *subject.high):
                sources.append(
                    PdfSource(
                        kind,
                        document.path,
                        Path("data") / portable_folders[kind] / document.relative_path,
                    )
                )
    return sources


def collect_apex_sources() -> list[PdfSource]:
    return [
        PdfSource("APEX", path, Path("data") / "APEX" / path.name)
        for path in sorted(DEFAULT_APEX_ROOT.glob("*.pdf"), key=lambda value: value.name.casefold())
        if parse_apex_filename(path) is not None
    ]


def collect_able_sources() -> list[PdfSource]:
    catalog = scan_able_catalog(DEFAULT_ABLE_ROOT)
    paths: set[Path] = set()
    for subject in catalog.subjects:
        paths.update(subject.full_documents)
        paths.update(path for _number, path in subject.lesson_documents)
        if subject.review_document is not None:
            paths.add(subject.review_document)
    return [
        PdfSource(
            "A:ble",
            path,
            Path("data") / "Able" / "렉쳐노트" / path.relative_to(DEFAULT_ABLE_ROOT),
        )
        for path in sorted(paths, key=lambda value: str(value).casefold())
    ]


def collect_training_sources() -> list[PdfSource]:
    catalog = scan_training_pack_catalog(DEFAULT_TRAINING_PACK_ROOT)
    return [
        PdfSource("트레이닝팩", subject.path, Path("data") / "트레이닝팩" / subject.path.name)
        for subject in catalog.subjects
    ]


def collect_frequent_type_sources() -> list[PdfSource]:
    catalog = scan_frequent_type_catalog(DEFAULT_FREQUENT_TYPE_ROOT)
    return [
        PdfSource("빈출유형", subject.path, Path("data") / "빈출유형" / subject.path.name)
        for subject in catalog.subjects
    ]


def collect_gyopyeong_sources() -> list[PdfSource]:
    catalog = scan_gyopyeong_catalog(DEFAULT_GYOPYEONG_ROOT)
    return [
        PdfSource(
            "교평 기출",
            lesson.path,
            Path("data") / "교평 기출" / lesson.path.name,
        )
        for subject in catalog.subjects
        for part in subject.parts
        for lesson in part.lessons
    ]


def collect_selected_exam_sources() -> list[PdfSource]:
    catalog = scan_selected_exam_catalog(DEFAULT_SELECTED_EXAM_ROOT)
    return [
        PdfSource(
            "기출엄선",
            lesson.path,
            Path("data") / "기출엄선" / subject.name / lesson.path.name,
        )
        for subject in catalog.subjects
        for lesson in subject.lessons
        if lesson.is_available
    ]


def collect_textbook_sources() -> list[PdfSource]:
    catalog = load_textbook_catalog()
    if catalog.issues:
        raise ValueError("교과서 카탈로그 검증 실패: " + " | ".join(catalog.issues))
    sources: list[PdfSource] = []
    for edition in catalog.verified_editions:
        for document_kind in ("핵심문제", "문제", "해설"):
            asset = edition.asset(document_kind)
            verify_textbook_asset(asset, verify_sha256=True)
            sources.append(
                PdfSource(
                    "교과서",
                    asset.path,
                    Path("data") / "교과서" / asset.relative_path,
                )
            )
        if edition.variant is not None:
            verify_textbook_asset(edition.variant, verify_sha256=True)
            sources.append(
                PdfSource(
                    "교과서",
                    edition.variant.path,
                    Path("data") / "교과서" / edition.variant.relative_path,
                )
            )
    return sources


def collect_blank_review_sources() -> list[PdfSource]:
    return [
        PdfSource(
            "백지리뷰",
            DEFAULT_BLANK_REVIEW_PATH,
            Path("data") / "백지리뷰" / DEFAULT_BLANK_REVIEW_PATH.name,
        ),
        PdfSource(
            "OMR",
            DEFAULT_OMR_PATH,
            Path("data") / "OMR" / DEFAULT_OMR_PATH.name,
        ),
    ]


def collect_registered_pdfs(config: dict[str, Any]) -> list[PdfSource]:
    records = (
        collect_unit_sources(config)
        + collect_test_sources()
        + collect_apex_sources()
        + collect_able_sources()
        + collect_training_sources()
        + collect_frequent_type_sources()
        + collect_selected_exam_sources()
        + collect_gyopyeong_sources()
        + collect_textbook_sources()
        + collect_blank_review_sources()
        + [PdfSource("구분지", PROJECT_ROOT / "unit_print_separator_a4.pdf", Path("unit_print_separator_a4.pdf"))]
    )
    seen_targets: dict[str, Path] = {}
    for record in records:
        if not record.source.is_file():
            raise FileNotFoundError(f"Registered PDF is missing: {record.source}")
        if record.source.suffix.casefold() != ".pdf":
            raise ValueError(f"Registered file is not a PDF: {record.source}")
        key = record.relative_path.as_posix().casefold()
        prior = seen_targets.get(key)
        if prior is not None and prior != record.source:
            raise ValueError(f"Release path collision: {record.relative_path}\n- {prior}\n- {record.source}")
        seen_targets[key] = record.source
    return records


def copy_with_sha256(source: Path, destination: Path) -> tuple[int, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as input_file, destination.open("xb") as output_file:
        while chunk := input_file.read(4 * 1024 * 1024):
            output_file.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    shutil.copystat(source, destination)
    return size, digest.hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        while chunk := input_file.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def collect_immutable_release_files(destination: Path) -> list[ReleaseFileManifestEntry]:
    entries: list[ReleaseFileManifestEntry] = []
    for path in sorted(destination.rglob("*"), key=lambda value: str(value).casefold()):
        if not path.is_file() or path.suffix.casefold() == ".pdf":
            continue
        relative_path = path.relative_to(destination).as_posix()
        if relative_path in MUTABLE_RELEASE_FILES:
            continue
        entries.append(
            ReleaseFileManifestEntry(
                relative_path=relative_path,
                size=path.stat().st_size,
                sha256=file_sha256(path),
            )
        )
    return entries


def copy_app_files(destination: Path, reference_gui: Path | None = None) -> None:
    for relative_text in APP_FILES:
        relative = Path(relative_text)
        source = PROJECT_ROOT / relative
        if not source.is_file():
            raise FileNotFoundError(f"Required application file is missing: {source}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    tests_target = destination / "tests"
    tests_target.mkdir(parents=True, exist_ok=True)
    for source in sorted((PROJECT_ROOT / "tests").glob("test_unit_print*.py")):
        shutil.copy2(source, tests_target / source.name)

    if reference_gui is not None:
        source_dir = reference_gui.resolve()
        if not source_dir.is_dir():
            raise FileNotFoundError(f"Reference GUI directory is missing: {source_dir}")
        target_dir = destination / "reference_gui"
        shutil.copytree(source_dir, target_dir)


def render_private_release_templates(
    destination: Path,
    department_id: str,
    department_pin: str,
) -> None:
    replacements = {
        "{{CANON_DEPARTMENT_ID}}": department_id,
        "{{CANON_DEPARTMENT_PIN}}": department_pin,
    }
    for relative_path in PRIVATE_TEMPLATE_FILES:
        path = destination / relative_path
        text = path.read_text(encoding="utf-8")
        for placeholder, value in replacements.items():
            if placeholder not in text:
                raise ValueError(f"Private release placeholder is missing in {relative_path}: {placeholder}")
            text = text.replace(placeholder, value)
        path.write_text(text, encoding="utf-8")


def write_portable_config(destination: Path) -> None:
    config = {
        "printer": "iR-ADV 8585 III",
        "copies": 1,
        "custom_copies": 1,
        "custom_bundle": False,
        "custom_printer_route": "자동 감지",
        "queue_batch_size": 12,
        "queue_refill_threshold": 6,
        "separator_page": False,
        "name_separator_page": False,
        "duplex": True,
        "engine": "SumatraPDF",
        "device_monitor": {
            "enabled": False,
            "base_url": "",
            "strict_ordering": True,
            "idle_interval_seconds": 12.0,
            "active_interval_seconds": 1.0,
            "dispatch_window_size": 6,
            "dispatch_policy_version": 2,
            "completion_timeout_seconds": 7200,
            "eta_seconds_per_sheet": [],
        },
        "blocks": [],
    }
    (destination / "unit_print_manager_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def write_portable_asset_channel(destination: Path, drive_folder_url: str) -> None:
    config = {
        "schema_version": 1,
        "remote": "gdrive:",
        "drive_root_folder_id": extract_drive_folder_id(drive_folder_url),
        "remote_prefix": "quick-printer-assets/v1",
        "rclone_exe": "",
        "transfers": 12,
        "checkers": 24,
        "drive_chunk_size": "64M",
    }
    (destination / "quick_printer_asset_channel.local.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def write_portable_apex_cache(destination: Path, apex_records: list[PdfSource]) -> int:
    source_cache_path = PROJECT_ROOT / "unit_print_material_catalog.json"
    source_cache = load_json(source_cache_path) if source_cache_path.exists() else {}
    portable: dict[str, Any] = {}
    for record in apex_records:
        candidates = (str(record.source.resolve()), record.source.name)
        entry = next((source_cache[key] for key in candidates if key in source_cache), None)
        if not isinstance(entry, dict):
            continue
        copied = dict(entry)
        copied["portable"] = True
        copied["mtime_ns"] = 0
        portable[record.source.name] = copied
    (destination / "unit_print_material_catalog.json").write_text(
        json.dumps(portable, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return len(portable)


def write_release_docs(
    destination: Path,
    counts: dict[str, int],
    total_bytes: int,
    department_id: str,
    department_pin: str,
) -> None:
    guide = f"""# 빠른 인쇄기 설치 가이드

이 폴더에는 앱과 현재 등록된 PDF {sum(counts.values()):,}개({total_bytes / 1024**3:.2f} GiB)가 함께 들어 있습니다.

## 가장 중요한 원칙

- 이 ZIP은 제작 PC에서 검증한 **전체 실행 파일 스냅샷**입니다. 새 PC의 Codex는 앱을 다시 구현하거나 소스 구조를 재설계하지 않습니다.
- 설치 작업은 압축 해제, 의존성 설치, 바로가기 생성, Canon 큐/드라이버 설정, 검증으로 한정합니다.
- 소스가 다르거나 손상되었다면 임의로 고치지 말고 ZIP을 다시 압축 해제합니다.
- `release_manifest.json`의 `immutable_files`에는 앱·스크립트·테스트·문서·아이콘의 SHA-256이 들어 있습니다. `--fast` 검사에서도 이 파일들은 항상 해시로 검증됩니다.

## 이 스냅샷의 기능 계약

- 메인 탭은 `테스트 / 교재자료 / 백지리뷰/OMR / PDF파일 / 대량주문서` 다섯 개입니다.
- 테스트 하위 탭은 `단원평가 / 복습테스트 / 학군지기출 / 일산기출`이며, 학군지기출과 일산기출 블록 앞에는 OMR 내신 1장이 자동으로 들어갑니다.
- 교재자료 하위 탭 순서는 `APEX / A:ble / 기출엄선 / 트레이닝팩 / 교평 기출 / APEX Boost / 교과서`입니다. 트레이닝팩 안에는 `트레이닝팩 / 빈출유형` 하위 탭이 있습니다.
- 단원평가·테스트·APEX·A:ble·기출엄선·트레이닝팩·빈출유형·교과서의 개별 선택은 `1,2,5`, `1-3`, `1-3, 6, 7-9` 문법을 지원합니다. 기출엄선과 교과서는 입력 순서대로 한 블록에 묶고 작업 큐에서 다시 수정할 수 있습니다.
- 교과서는 원본 기반으로 검수 완료된 42개 판본과 핵심문제·문제·해설 PDF 126개, 별도 `핵심문제 변형` PDF 15개를 배포·선택합니다. 기존 핵심문제와 변형 모두 단원명 범위로 고르며, 선택 파트의 중단원 자료와 그 파트가 속한 대단원 평가를 함께 인쇄합니다. 핵심문제는 제목 표지와 해당 판본의 Drive 해설 QR 표지 1·2쪽을, 변형은 자체 표지 1·2쪽을 본문 앞에 한 번 붙입니다. 불완전하거나 경계가 불확실한 판본이 생기면 해당 판본 전체를 보류합니다.
- 구분지와 이름 구분지는 별도 기능입니다. 같은 학생 이름의 연속 블록에는 이름 구분지를 한 번만 넣습니다.
- 앱을 닫았다 다시 열면 작업 큐는 비어 있고 모든 부수는 1로 시작합니다. 실제 인쇄는 항상 흑백·A4·양면이며, 세로/가로/A:ble/구분지 전용 큐로 분기합니다.
- `reference_gui`는 제작 PC에서 실행한 무인쇄 GUI 기준 자료입니다. 새 PC에서는 기능을 다시 디자인하지 말고 이 구조와 비교합니다.

## 설치

1. ZIP을 원하는 고정 폴더에 완전히 압축 해제합니다. 권장 위치는 `D:\\빠른 인쇄기`입니다.
2. `install_quick_printer.cmd`를 실행합니다. Python 3.11 이상이 없으면 Python 3.12를, SumatraPDF가 없으면 SumatraPDF를 `winget`으로 설치합니다. 이어서 앱 가상환경, 필수 패키지, 바탕화면/시작 메뉴 바로가기를 준비합니다.
3. 설치 후 바탕화면의 `빠른 인쇄기`를 실행합니다.
4. 기본 프린터 `iR-ADV 8585 III`가 Windows에 설치되어 있어야 합니다.
5. 관리자 PowerShell에서 아래 명령을 실행해 전용 큐 4개를 만듭니다.

```powershell
& ".venv\\Scripts\\python.exe" "scripts\\setup_quick_printer_profiles.py" --base-printer "iR-ADV 8585 III"
```

6. 다음 큐가 모두 있어야 합니다.
   - `iR-ADV 8585 III - 세로`: A4, 흑백, 세로, 양면 긴 쪽 넘김, 좌측 상단 스테이플
   - `iR-ADV 8585 III - 가로`: A4, 흑백, 가로, 양면 짧은 쪽 넘김, 좌측 상단 스테이플
   - `iR-ADV 8585 III - 구분지`: A4, 흑백, 가로, 스테이플 없음
   - `iR-ADV 8585 III -A:ble`: A4, 흑백, 가로, 양면 짧은 쪽 넘김, 좌측 상단 스테이플. 드라이버 모아찍기는 1쪽이며 앱이 2×2 PDF를 만듭니다.

7. `빠른인쇄기_프린터_ComputerUse_설정검증.md`를 새 PC의 Codex에 전달합니다. Codex는 Computer Use로 Canon 드라이버 화면을 직접 확인하고 기본 큐와 전용 큐 4개를 각각 설정합니다.
8. 인증값은 Canon 인증 창에서 사용자가 직접 입력합니다.
   - 부서 ID: `{department_id}`
   - PIN/PW: `{department_pin}`
   - `PIN 설정 허가`: 켬
   - `인쇄 시 부서 ID/PIN 확인`: 끔

### 인증 정보를 검증할 수 없다는 메시지가 나올 때

이 메시지만으로 ID/PIN 오류로 판단하지 않습니다. 관리자 PowerShell에서 확인합니다.

```powershell
Get-Printer -Name "iR-ADV 8585 III" | Format-List Name,DriverName,PortName
Test-NetConnection 192.168.0.248 -Port 9100
```

- 드라이버는 `Canon Generic Plus UFR II`, 포트는 `IP_192.168.0.248` 또는 같은 주소의 Standard TCP/IP 포트여야 합니다.
- `WSD-...`/`USB...` 포트에서는 Canon의 인증 `검증` 기능을 사용할 수 없습니다. 기본 큐를 Standard TCP/IP 포트로 바꾸고 전용 큐 4개를 다시 생성합니다.
- Canon 전체 드라이버 설치 시 `Canon Driver Information Assist Service`를 포함합니다.
- 정상 TCP/IP 포트인데 `검증`만 실패하면 값을 저장한 뒤 1쪽 테스트 인쇄로 실제 인증을 확인합니다. 테스트도 거부되면 프린터 본체/Remote UI의 부서 ID 등록 상태를 확인합니다.

## Python 또는 SumatraPDF 자동 설치가 안 될 때

관리자 PowerShell에서 다음 명령을 각각 실행한 뒤 `install_quick_printer.cmd`를 다시 실행합니다.

```powershell
winget install --id Python.Python.3.12 --exact --accept-package-agreements --accept-source-agreements
winget install --id SumatraPDF.SumatraPDF --exact --accept-package-agreements --accept-source-agreements
```

`winget`이 없는 PC에서는 Microsoft의 App Installer를 먼저 설치한 뒤 위 명령을 실행합니다.

## 이후 인쇄 자료만 업데이트

- 최초 ZIP은 전체 PDF 기준본입니다. 이후에는 `sync_quick_printer_assets.cmd`가 Drive 매니페스트와 로컬 파일을 비교해 새 PDF와 변경 PDF만 내려받습니다.
- 설치 스크립트는 rclone을 준비하고, 이 PC에 `gdrive:` 인증이 이미 있으면 증분 동기화도 실행합니다.
- `gdrive:`가 없으면 PowerShell에서 `rclone config`를 실행해 Google Drive 로그인을 한 번 완료한 뒤 `sync_quick_printer_assets.cmd`를 실행합니다.
- 동기화는 원격 또는 로컬의 여분 파일을 자동 삭제하지 않습니다. 기준본 파일이 빠졌다면 전체 ZIP에서 먼저 복원합니다.

## 자료 수록 현황

""" + "\n".join(f"- {name}: {count:,}개" for name, count in counts.items()) + """

## 검증

- 빠른 검사: `.venv\\Scripts\\python.exe scripts\\verify_quick_printer_release.py --fast` (앱 파일 SHA-256 + PDF 경로/크기)
- 전체 SHA-256 검사: `.venv\\Scripts\\python.exe scripts\\verify_quick_printer_release.py`
- 코드 테스트: `.venv\\Scripts\\python.exe -m unittest discover -s tests -p \"test_unit_print*.py\"`

실제 종이를 쓰는 인쇄 테스트는 사용자의 명시적 승인 후 진행합니다.
"""
    (destination / "빠른인쇄기_설치_가이드.md").write_text(guide, encoding="utf-8")
    agents = """# 빠른 인쇄기 배포본

- 이 폴더는 제작 PC에서 검증한 실행 파일을 그대로 담은 정본이다. 앱을 재구현하거나 파일 구조를 바꾸지 않는다.
- 먼저 `빠른인쇄기_설치_가이드.md`, `빠른인쇄기_프린터_ComputerUse_설정검증.md`, `release_manifest.json`을 읽는다.
- `scripts/verify_quick_printer_release.py --fast`가 실패하면 소스를 수정해 맞추지 말고 ZIP을 다시 압축 해제한다.
- 앱 자료 경로는 이 폴더의 `data` 아래를 자동 감지하므로 절대경로로 다시 고정하지 않는다.
- `install_quick_printer.cmd`를 실행하고 검증 스크립트와 단위 테스트를 통과시킨다.
- 프린터 설정은 PowerShell 결과만으로 합격 처리하지 않는다. Computer Use로 Canon GUI의 현재 사용자 기본값과 시스템 인쇄 기본값을 모두 확인한다.
- 인증 창은 자동 조작하지 않는다. 사용자가 ID/PIN을 직접 입력하도록 제어권을 넘기고, 저장 뒤 무팝업 인쇄를 확인한다.
- 부서 ID/PIN을 코드, JSON, 스크린샷, 일반 로그에 기록하지 않는다.
- 실제 인쇄는 사용자에게 현재 작업 승인을 받은 뒤 최소 테스트 세트로 진행한다.
- 메인/하위 탭, 개별 번호 문법, 작업 큐 편집, OMR 자동 삽입, 이름 구분지 묶기, 프린터 라우팅은 이 ZIP의 테스트가 고정한 계약이다. 설치 과정에서 기능을 축소하거나 새로 해석하지 않는다.
- `reference_gui`가 있으면 새 PC 무인쇄 캡처와 비교한다. 차이가 있으면 소스를 재작성하지 말고 DPI·Tk·글꼴·창 크기와 설치 경로부터 조사한다.
"""
    (destination / "AGENTS.md").write_text(agents, encoding="utf-8")


def write_codex_handoff(
    path: Path,
    *,
    release_name: str,
    archive_name: str,
    archive_sha256: str,
    checksum_name: str,
    archive_bytes: int,
    counts: dict[str, int],
    registered_pdf_bytes: int,
    immutable_file_count: int,
    drive_folder_url: str,
    department_id: str,
    department_pin: str,
) -> None:
    category_lines = "\n".join(f"- {name}: {count:,}개" for name, count in counts.items())
    guide = f"""# 빠른 인쇄기 동일환경 설치 지시서

이 문서는 새 Windows PC의 Codex에게 전달하는 설치 지시서다. Canon 부서 인증값은 포함하지 않으며 수신 PC에서 사용자가 직접 입력한다.

## Codex에 함께 입력할 한 줄

`이 지시서를 끝까지 읽고, 앱을 새로 구현하지 말고 Google Drive의 정본 ZIP을 그대로 설치·검증해줘. 실제 종이 인쇄는 내 승인 전에는 하지 마.`

## 정본 배포 파일

- Google Drive 폴더: {drive_folder_url}
- ZIP 파일명: `{archive_name}`
- 체크섬 파일명: `{checksum_name}`
- 압축 해제 후 최상위 폴더: `{release_name}`
- ZIP 크기: `{archive_bytes:,} bytes`
- ZIP SHA-256: `{archive_sha256}`
- 등록 PDF: `{sum(counts.values()):,}개`, `{registered_pdf_bytes:,} bytes`
- 해시 고정 앱·테스트·문서·아이콘·GUI 기준 파일: `{immutable_file_count:,}개`

## 절대 원칙

1. 이 배포본은 제작 PC에서 실행·테스트한 **전체 파일 스냅샷**이다. Codex는 앱을 다시 만들거나 비슷한 코드로 대체하지 않는다.
2. ZIP 내부 파일명, 한글 경로, 디렉터리 구조를 바꾸지 않는다. 설치용 로컬 설정 외에는 소스·PDF·아이콘을 수정하지 않는다.
3. 다운로드한 ZIP의 SHA-256이 위 값과 다르면 압축 해제하거나 실행하지 않는다.
4. 압축 해제 뒤 `release_manifest.json` 검증이 실패하면 코드를 고쳐 통과시키지 않는다. ZIP을 다시 내려받아 빈 고정 폴더에 다시 푼다.
5. 실제 종이를 쓰는 테스트는 사용자의 현재 승인을 받은 뒤에만 실행한다.

## 현재 정본 기능 기준

- 메인 탭: `테스트 / 교재자료 / 백지리뷰/OMR / PDF파일 / 대량주문서`
- 테스트 하위 탭: `단원평가 / 복습테스트 / 학군지기출 / 일산기출`
- 교재자료 하위 탭: `APEX / A:ble / 기출엄선 / 트레이닝팩 / 교평 기출 / APEX Boost / 교과서`
- 트레이닝팩 내부 탭: `트레이닝팩 / 빈출유형`
- 개별 선택 문법: `1,2,5`, `1-3`, `1-3, 6, 7-9`이며 공백은 무관합니다. 기출엄선과 교과서도 복수 파트 블록 생성과 작업 큐 재수정을 지원합니다.
- 교과서 활성 판본은 42개이며 핵심문제·문제·해설 PDF 126개와 별도 `핵심문제 변형` PDF 15개를 선택·배포합니다. 기존 핵심문제와 변형 모두 단원명 범위로 고르며, 선택 파트의 중단원 자료와 그 파트가 속한 대단원 평가를 함께 인쇄합니다. 핵심문제는 제목 표지와 해당 판본의 Drive 해설 QR 표지 1·2쪽을, 변형은 자체 표지 1·2쪽을 본문 앞에 한 번 붙입니다. 불완전하거나 경계가 불확실한 판본이 생기면 해당 판본 전체를 보류합니다.
- 학군지기출·일산기출은 각 시험지 앞에 OMR 내신 1장을 자동 삽입합니다.
- 연속된 같은 학생 이름 블록에는 이름 구분지를 한 번만 삽입합니다.
- 이 목록이나 동작이 다르면 새로 구현하지 말고 먼저 ZIP 해시, 추출 폴더, 바로가기 실행 경로, `release_manifest.json`, 테스트 결과를 확인합니다.

## 1. 다운로드와 해시 확인

Google Drive 폴더에서 `{archive_name}`과 `{checksum_name}`을 같은 폴더에 내려받는다.

```powershell
$zip = Resolve-Path ".\\{archive_name}"
$actual = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
$expected = "{archive_sha256}"
if ($actual -ne $expected) {{ throw "ZIP SHA-256 불일치: 다시 다운로드하세요." }}
```

## 2. 정본 설치

1. ZIP을 완전히 압축 해제한다. 권장 고정 위치는 `D:\\빠른 인쇄기`다.
2. ZIP 안의 `{release_name}` 폴더 내용을 고정 위치에 둔다.
3. `install_quick_printer.cmd`를 실행한다.
4. 설치 스크립트가 Python 3.11+ 가상환경, 고정 버전 Python 패키지, SumatraPDF, 바로가기를 준비하고 앱 파일·PDF·테스트를 검증하도록 끝까지 기다린다.

Python 또는 SumatraPDF 자동 설치가 막히면 관리자 PowerShell에서 먼저 실행한다.

```powershell
winget install --id Python.Python.3.12 --exact --accept-package-agreements --accept-source-agreements
winget install --id SumatraPDF.SumatraPDF --exact --accept-package-agreements --accept-source-agreements
```

## 3. 동일 파일 검증

앱 폴더에서 실행한다.

```powershell
& ".venv\\Scripts\\python.exe" "scripts\\verify_quick_printer_release.py"
& ".venv\\Scripts\\python.exe" -m py_compile unit_print_manager.py unit_print_paths.py unit_print_keyboard.py unit_print_text_shortcuts.py unit_print_materials.py unit_print_selected_exams.py unit_print_test_catalog.py unit_print_server_catalog.py
& ".venv\\Scripts\\python.exe" -m unittest discover -s tests -p "test_unit_print*.py"
```

- 전체 SHA-256 검사와 모든 단위 테스트가 성공해야 한다.
- `reference_gui`가 있으면 `python scripts\\capture_quick_print_gui.py --output output\\new_pc_gui_qa`로 기본 최대화 무인쇄 캡처를 만든다. 두 폴더의 `quick_print_gui_metrics.json`에서 `runtime`의 Python/Tk·글꼴·배율·화면 크기를 먼저 맞춘 뒤 메인 탭·서브 탭·좌우 패널 배치와 이미지를 비교한다.
- 픽셀 단위 동일성이 필요하면 정본 metrics에 기록된 Windows 화면 크기·`tk_scaling`·`ui_font_family`와 Python/Tk 버전까지 같아야 한다. 화면 환경이 다르면 단축키·탭 순서·컨트롤 상태 테스트는 동일하게 통과할 수 있어도 글자 폭과 여백은 달라질 수 있다.
- 메인 탭 `테스트`, `교재자료`, `백지리뷰/OMR`, `PDF파일`, `대량주문서`가 모두 한 줄로 온전히 보여야 한다.

## 4. Canon 큐와 전용 프로필

기본 큐는 정확히 `iR-ADV 8585 III`, Canon Generic Plus UFR II 드라이버, `192.168.0.248`의 Standard TCP/IP 포트를 사용한다. 관리자 PowerShell에서 실행한다.

```powershell
& ".venv\\Scripts\\python.exe" "scripts\\setup_quick_printer_profiles.py" --base-printer "iR-ADV 8585 III"
```

Computer Use로 **현재 사용자 인쇄 기본 설정과 프린터 속성 > 고급 > 인쇄 기본값을 모두** 열어 다음을 확인한다.

| 큐 | 방향 | 양면 | 스테이플 | 드라이버 모아찍기 |
|---|---|---|---|---|
| `iR-ADV 8585 III - 세로` | 세로 | 긴 쪽 넘김 | 좌측 상단 | 1쪽 |
| `iR-ADV 8585 III - 가로` | 가로 | 짧은 쪽 넘김 | 좌측 상단 | 1쪽 |
| `iR-ADV 8585 III - 구분지` | 가로 | 짧은 쪽 넘김 | 없음 | 1쪽 |
| `iR-ADV 8585 III -A:ble` | 가로 | 짧은 쪽 넘김 | 좌측 상단 | 1쪽 |

모든 큐는 A4·흑백이다. A:ble의 2×2는 앱이 PDF를 재배치하므로 Canon 드라이버의 모아찍기를 2×2로 설정하지 않는다.

## 5. 부서 인증 저장

기본 큐와 전용 큐 4개에 각각 설정한다.

- 부서 ID: `{department_id}`
- PIN/PW: `{department_pin}`
- `PIN 설정 허가`: 켬
- `인쇄 시 부서 ID/PIN 확인`: 끔
- `디바이스에서 부서 ID/PIN 인증`: 켬

인증값 입력은 사용자가 직접 수행하고 Codex는 창 이동과 저장 여부 확인만 담당한다. `입력된 인증 정보를 검증할 수 없습니다`가 나오면 ID/PIN부터 바꾸지 말고 Canon 드라이버, Standard TCP/IP 포트, Driver Information Assist Service, 프린터 네트워크 연결을 확인한다.

## 6. 수록 자료

{category_lines}

## 7. 완료 판정

- ZIP과 추출 파일의 해시 검증 성공
- 모든 단위 테스트 성공
- 등록 PDF 누락·추가 파일 없음
- 앱 시작 시 작업 큐가 비어 있고 부수는 1
- 바탕화면과 작업표시줄 아이콘 일치
- 모든 메인 탭이 한 줄로 표시되고 정본 GUI와 구조가 일치
- 기본 큐와 전용 큐 4개가 존재하고 Canon GUI 설정이 표와 일치
- ID/PIN을 매 작업마다 다시 묻지 않도록 각 큐에 저장됨

실제 인쇄 승인을 받았다면 세로 2쪽, 가로 2쪽, 구분지 1쪽, A:ble 2쪽 분량을 최소 세트로 확인한다. 승인 전에는 여기까지를 무인쇄 설치 완료로 보고한다.
"""
    path.write_text(guide, encoding="utf-8")


def build_archive(source_dir: Path, archive_path: Path) -> str:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if archive_path.exists():
        raise FileExistsError(f"Archive already exists: {archive_path}")
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for path in sorted(source_dir.rglob("*"), key=lambda value: str(value).casefold()):
            if path.is_file():
                archive.write(path, (Path(source_dir.name) / path.relative_to(source_dir)).as_posix())
    digest = hashlib.sha256()
    with archive_path.open("rb") as archive_file:
        while chunk := archive_file.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    args = parse_args()
    destination = args.destination.resolve()
    archive_path = args.archive.resolve()
    if destination.exists():
        raise FileExistsError(f"Destination already exists: {destination}")
    if archive_path.exists():
        raise FileExistsError(f"Archive already exists: {archive_path}")

    config = load_json(PROJECT_ROOT / "unit_print_manager_config.json")
    records = collect_registered_pdfs(config)
    destination.mkdir(parents=True)
    copy_app_files(destination, args.reference_gui)
    render_private_release_templates(destination, args.department_id, args.department_pin)
    write_portable_config(destination)
    write_portable_asset_channel(destination, args.drive_folder_url)

    manifest_entries: list[PdfManifestEntry] = []
    counts: dict[str, int] = {}
    total_bytes = 0
    for index, record in enumerate(records, start=1):
        size, digest = copy_with_sha256(record.source, destination / record.relative_path)
        manifest_entries.append(PdfManifestEntry(record.category, record.relative_path.as_posix(), size, digest))
        counts[record.category] = counts.get(record.category, 0) + 1
        total_bytes += size
        if index % 100 == 0 or index == len(records):
            print(f"Copied {index:,}/{len(records):,} registered PDFs", flush=True)

    apex_records = [record for record in records if record.category == "APEX"]
    portable_cache_entries = write_portable_apex_cache(destination, apex_records)
    write_release_docs(destination, counts, total_bytes, args.department_id, args.department_pin)

    (destination / "registered_pdfs.sha256").write_text(
        "".join(f"{entry.sha256} *{entry.relative_path}\n" for entry in manifest_entries),
        encoding="utf-8",
    )
    immutable_entries = collect_immutable_release_files(destination)
    immutable_bytes = sum(entry.size for entry in immutable_entries)

    generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    manifest = {
        "schema_version": 2,
        "release_name": destination.name,
        "generated_at": generated_at,
        "registered_pdf_count": len(manifest_entries),
        "registered_pdf_bytes": total_bytes,
        "category_counts": counts,
        "portable_apex_cache_entries": portable_cache_entries,
        "saved_queue_blocks_in_source_config": len(config.get("blocks", [])),
        "immutable_file_count": len(immutable_entries),
        "immutable_file_bytes": immutable_bytes,
        "mutable_files": sorted(MUTABLE_RELEASE_FILES - {"release_manifest.json"}),
        "immutable_files": [asdict(entry) for entry in immutable_entries],
        "pdfs": [asdict(entry) for entry in manifest_entries],
    }
    (destination / "release_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"Creating Zip64 archive: {archive_path}", flush=True)
    archive_sha256 = build_archive(destination, archive_path)
    checksum_path = archive_path.with_suffix(archive_path.suffix + ".sha256")
    checksum_path.write_text(f"{archive_sha256} *{archive_path.name}\n", encoding="utf-8")
    handoff_path = archive_path.with_name(f"{destination.name}_Codex_설치지시서.md")
    write_codex_handoff(
        handoff_path,
        release_name=destination.name,
        archive_name=archive_path.name,
        archive_sha256=archive_sha256,
        checksum_name=checksum_path.name,
        archive_bytes=archive_path.stat().st_size,
        counts=counts,
        registered_pdf_bytes=total_bytes,
        immutable_file_count=len(immutable_entries),
        drive_folder_url=args.drive_folder_url,
        department_id=args.department_id,
        department_pin=args.department_pin,
    )
    result = {
        "release_dir": str(destination),
        "archive": str(archive_path),
        "archive_bytes": archive_path.stat().st_size,
        "archive_sha256": archive_sha256,
        "checksum_file": str(checksum_path),
        "codex_handoff": str(handoff_path),
        "registered_pdf_count": len(manifest_entries),
        "registered_pdf_bytes": total_bytes,
        "immutable_file_count": len(immutable_entries),
        "immutable_file_bytes": immutable_bytes,
        "category_counts": counts,
        "portable_apex_cache_entries": portable_cache_entries,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
