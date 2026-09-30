from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from unit_print_server_catalog import (  # noqa: E402
    build_default_registered_test_assets,
    export_registered_test_catalog_csv,
    write_server_order_template,
)


DEFAULT_OUTPUT_DIR = APP_DIR / "빠른인쇄기_서버연동"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="빠른 인쇄기 서버 연동용 테스트 목록과 주문 양식을 생성합니다."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="출력 폴더",
    )
    args = parser.parse_args()

    assets = build_default_registered_test_assets()
    output_dir = args.output_dir.resolve()
    catalog_path = export_registered_test_catalog_csv(
        output_dir / "빠른인쇄기_등록_테스트목록.csv",
        assets,
    )
    template_path = write_server_order_template(
        output_dir / "빠른인쇄기_서버주문_양식.csv"
    )
    missing = [asset for asset in assets if not asset.path.is_file()]
    counts = Counter(asset.source for asset in assets)

    print(f"테스트 목록: {catalog_path}")
    print(f"주문 양식: {template_path}")
    print(f"전체 자료: {len(assets)}개")
    for source, count in counts.items():
        print(f"- {source}: {count}개")
    print(f"누락 PDF: {len(missing)}개")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
