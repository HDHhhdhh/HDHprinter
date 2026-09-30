from __future__ import annotations

import csv
import sys
import tempfile
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from unit_print_manager import (  # noqa: E402
    PrintManagerApp,
    block_requires_omr_sheet,
    load_unit_catalog,
    name_separator_group_labels,
)
from unit_print_orders import load_order_csv  # noqa: E402
from unit_print_server_catalog import (  # noqa: E402
    DEFAULT_UNIT_ROOT,
    build_default_registered_test_assets,
)
from unit_print_test_catalog import scan_default_test_catalogs  # noqa: E402


class _Variable:
    def __init__(self, value: object) -> None:
        self.value = value

    def get(self) -> object:
        return self.value


def main() -> int:
    assets = build_default_registered_test_assets()
    if not assets:
        raise RuntimeError("등록 테스트가 없습니다.")
    ids = [asset.material_id.casefold() for asset in assets]
    if len(ids) != len(set(ids)):
        raise RuntimeError("자료ID가 중복되었습니다.")
    missing = [asset.path for asset in assets if not asset.path.is_file()]
    if missing:
        raise FileNotFoundError(f"등록 PDF {len(missing)}개를 찾을 수 없습니다: {missing[0]}")

    selected = [
        next(asset for asset in assets if asset.source == "단원평가"),
        next(asset for asset in assets if asset.source == "학군지기출"),
        next(asset for asset in assets if asset.server_test_id),
    ]
    with tempfile.TemporaryDirectory() as temp_dir:
        csv_path = Path(temp_dir) / "server-orders.csv"
        with csv_path.open("w", encoding="utf-8-sig", newline="") as output_file:
            writer = csv.writer(output_file)
            writer.writerow(("주문번호", "자료ID", "학생이름", "부수", "메모"))
            writer.writerow(("VERIFY-1", selected[0].material_id, "한동훈", 1, ""))
            writer.writerow(("VERIFY-2", selected[1].material_id, "한동훈", 1, ""))
            writer.writerow(("VERIFY-3", selected[2].server_test_id, "장규리", 1, ""))
        parsed = load_order_csv(csv_path)
        if parsed.issues:
            raise RuntimeError("CSV 파싱 실패: " + parsed.issues[0].format())

        app = PrintManagerApp.__new__(PrintManagerApp)
        app.catalog = load_unit_catalog()
        app.root_dir_var = _Variable(str(DEFAULT_UNIT_ROOT))
        app.test_catalogs = scan_default_test_catalogs()
        app.printer_var = _Variable("iR-ADV 8585 III")
        blocks, issues = app.resolve_order_csv_blocks(parsed.rows)
        if issues:
            raise RuntimeError("블록 변환 실패: " + issues[0].format())

        linked_ilsan = tuple(asset for asset in assets if asset.server_test_id)
        all_ilsan_path = Path(temp_dir) / "server-ilsan-33.csv"
        with all_ilsan_path.open("w", encoding="utf-8-sig", newline="") as output_file:
            writer = csv.writer(output_file)
            writer.writerow(("주문번호", "파일코드", "학생이름"))
            for number, asset in enumerate(linked_ilsan, start=1):
                writer.writerow((f"ILSAN-{number:02d}", asset.server_test_id, "검증학생"))
        all_ilsan_parsed = load_order_csv(all_ilsan_path)
        if all_ilsan_parsed.issues:
            raise RuntimeError("일산 34종 CSV 파싱 실패: " + all_ilsan_parsed.issues[0].format())
        all_ilsan_blocks, all_ilsan_issues = app.resolve_order_csv_blocks(
            all_ilsan_parsed.rows
        )
        if all_ilsan_issues:
            raise RuntimeError("일산 34종 블록 변환 실패: " + all_ilsan_issues[0].format())
        if len(all_ilsan_blocks) != 34:
            raise RuntimeError(f"일산 서버 시험 ID는 34종이어야 합니다: {len(all_ilsan_blocks)}")
        if tuple(block.material_id for block in all_ilsan_blocks) != tuple(
            asset.material_id for asset in linked_ilsan
        ):
            raise RuntimeError("일산 34종의 정본 자료ID 변환 순서가 일치하지 않습니다.")
        if not all(block_requires_omr_sheet(block) for block in all_ilsan_blocks):
            raise RuntimeError("일산 34종 중 OMR 자동 삽입 대상이 아닌 블록이 있습니다.")

    if name_separator_group_labels(blocks) != ("한동훈", None, "장규리"):
        raise RuntimeError("연속 이름 구분지 병합 규칙이 일치하지 않습니다.")
    if tuple(block_requires_omr_sheet(block) for block in blocks) != (False, True, True):
        raise RuntimeError("학군지/일산 OMR 자동 삽입 규칙이 일치하지 않습니다.")
    if any(block.printer_route != "세로" for block in blocks):
        raise RuntimeError("등록 테스트가 세로 프로필로 변환되지 않았습니다.")
    if blocks[2].material_id != selected[2].material_id:
        raise RuntimeError("서버 시험 ID가 빠른 인쇄기 자료ID로 정규화되지 않았습니다.")

    print(f"등록 테스트: {len(assets)}개 / 자료ID 중복 0 / 누락 PDF 0")
    print("서버 CSV 파싱: 3행 성공")
    print("블록 변환: 단원평가 + 학군지기출 + 서버 시험 ID 일산기출 성공")
    print("이름 구분지 병합: 한동훈 1장, 장규리 1장")
    print("OMR 자동 삽입: 학군지기출/일산기출 확인")
    print("신규 일산 서버 시험 ID: 공수2 16종 + 미적분1 18종 전체 변환 성공")
    print("실제 인쇄 전송: 실행하지 않음")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
