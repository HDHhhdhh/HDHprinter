from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from unit_print_server_catalog import (
    ILSAN_SERVER_TEST_ID_BY_RELATIVE_PATH,
    REGISTERED_TEST_CSV_HEADERS,
    RegisteredTestAsset,
    build_default_registered_test_assets,
    export_registered_test_catalog_csv,
    registered_test_asset_index,
    stable_test_material_id,
    write_server_order_template,
)


class ServerCatalogTests(unittest.TestCase):
    def test_material_id_is_stable_for_windows_path_spelling(self) -> None:
        left = stable_test_material_id("catalog", r"공통수학1\Lv.3\1\시험지.PDF")
        right = stable_test_material_id("catalog", "공통수학1/lv.3/1/시험지.pdf")

        self.assertEqual(left, right)
        self.assertTrue(left.startswith("QT-UNIT-"))

    def test_catalog_and_order_templates_are_excel_friendly_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            asset = RegisteredTestAsset(
                material_id="QT-UNIT-0123456789ABCDEF",
                source="단원평가",
                source_key="catalog",
                subject="공통수학1",
                filter_label="레벨",
                filter_value="Lv.3",
                level="Lv.3",
                term="",
                round_name="1",
                number=1,
                display_name="다항식의 연산",
                path=directory / "시험지.pdf",
                relative_path="공통수학1/Lv.3/1/시험지.pdf",
            )
            catalog_path = export_registered_test_catalog_csv(
                directory / "catalog.csv", (asset,)
            )
            order_path = write_server_order_template(directory / "orders.csv")

            self.assertTrue(catalog_path.read_bytes().startswith(b"\xef\xbb\xbf"))
            self.assertTrue(order_path.read_bytes().startswith(b"\xef\xbb\xbf"))
            catalog_lines = catalog_path.read_text(encoding="utf-8-sig").splitlines()
            self.assertEqual(",".join(REGISTERED_TEST_CSV_HEADERS), catalog_lines[0])
            self.assertIn(asset.material_id, catalog_lines[1])
            self.assertEqual(
                "주문번호,자료ID,학생이름,부수,메모",
                order_path.read_text(encoding="utf-8-sig").strip(),
            )

    def test_ilsan_server_test_ids_bind_all_34_registered_files(self) -> None:
        assets = build_default_registered_test_assets()
        linked = tuple(asset for asset in assets if asset.server_test_id)

        self.assertEqual(34, len(ILSAN_SERVER_TEST_ID_BY_RELATIVE_PATH))
        self.assertEqual(34, len(linked))
        self.assertEqual(
            set(ILSAN_SERVER_TEST_ID_BY_RELATIVE_PATH),
            {asset.relative_path for asset in linked},
        )
        self.assertEqual(
            set(ILSAN_SERVER_TEST_ID_BY_RELATIVE_PATH.values()),
            {asset.server_test_id for asset in linked},
        )

    def test_server_test_id_and_quick_printer_id_resolve_same_asset(self) -> None:
        assets = build_default_registered_test_assets()
        target = next(
            asset
            for asset in assets
            if asset.server_test_id == "t_ilsan_past_mijeok1_high_midterm_008"
        )
        index = registered_test_asset_index(assets)

        self.assertIs(target, index[target.material_id.casefold()])
        self.assertIs(target, index[target.server_test_id.casefold()])
        self.assertEqual("QT-ILSAN-7023A5A927AB644A", target.material_id)
        self.assertEqual("미적1/중간/상/8회차 23 저현고.pdf", target.relative_path)

    def test_new_gongsu2_baekseok_asset_has_server_alias_and_high_slot(self) -> None:
        assets = build_default_registered_test_assets()
        target = next(
            asset
            for asset in assets
            if asset.server_test_id == "t_ilsan_past_gongsu2_high_midterm_006"
        )

        self.assertEqual("QT-ILSAN-F1B384B124619774", target.material_id)
        self.assertEqual("공수2/중간/상/6회차 25 백석고.pdf", target.relative_path)
        self.assertEqual(16, target.number)
        self.assertEqual("상 6회차 25 백석고", target.display_name)


if __name__ == "__main__":
    unittest.main()
