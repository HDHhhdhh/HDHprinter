from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pypdf import PdfWriter

from unit_print_manager import ExamPdf, PrintBlock, PrintManagerApp
from unit_print_orders import (
    ORDER_CSV_HEADERS,
    OrderCsvIssue,
    OrderCsvResult,
    OrderRow,
    load_order_csv,
    name_separator_order_key,
    write_order_csv_template,
)
from unit_print_server_catalog import RegisteredTestAsset


class FakeVariable:
    def __init__(self, value: object) -> None:
        self.value = value

    def get(self) -> object:
        return self.value

    def set(self, value: object) -> None:
        self.value = value


def write_pdf(path: Path, page_count: int = 2) -> None:
    writer = PdfWriter()
    for _page in range(page_count):
        writer.add_blank_page(width=595, height=842)
    with path.open("wb") as output_file:
        writer.write(output_file)


def order_row(**overrides: object) -> OrderRow:
    values: dict[str, object] = {
        "row_number": 2,
        "order_id": "1001",
        "source": "백지리뷰",
        "subject": "",
        "level": "",
        "round_name": "",
        "document_kind": "교재",
        "mode": "",
        "start": None,
        "end": None,
        "selected_numbers": (),
        "copies": 2,
        "bundled": False,
        "printer_profile": "자동 감지",
        "file_paths": (),
        "note": "",
        "material_id": "",
        "student_name": "",
    }
    values.update(overrides)
    return OrderRow(**values)  # type: ignore[arg-type]


class OrderCsvParserTests(unittest.TestCase):
    def test_template_is_utf8_bom_and_has_canonical_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "template.csv"
            write_order_csv_template(path)

            payload = path.read_bytes()
            self.assertTrue(payload.startswith(b"\xef\xbb\xbf"))
            self.assertEqual(",".join(ORDER_CSV_HEADERS), path.read_text(encoding="utf-8-sig").strip())

    def test_utf8_order_sheet_parses_all_lightweight_types(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            pdf = directory / "직접.pdf"
            write_pdf(pdf)
            csv_path = directory / "orders.csv"
            csv_path.write_text(
                ",".join(ORDER_CSV_HEADERS)
                + "\n"
                + "1001,단원평가,공수1,Lv.3,1,,,1,5,,2,N,,,,\n"
                + "1002,백지리뷰,,,,,,,,,3,N,,,,\n"
                + f'1003,PDF,,,,,,,,,1,N,세로,"{pdf}",메모\n',
                encoding="utf-8-sig",
            )

            result = load_order_csv(csv_path)

            self.assertEqual((), result.issues)
            self.assertEqual(("단원평가", "백지리뷰", "PDF"), tuple(row.source for row in result.rows))
            self.assertEqual(2, result.rows[0].copies)
            self.assertEqual(3, result.rows[1].copies)
            self.assertEqual((pdf,), result.rows[2].file_paths)

    def test_cp949_alias_headers_and_non_contiguous_lessons(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "orders.csv"
            text = (
                "주문,자료,과목,문서종류,파트,개별차시,수량\n"
                '7,APEX Boost,대수,교재,차시별,"1-3,5",2\n'
            )
            path.write_bytes(text.encode("cp949"))

            result = load_order_csv(path)

            self.assertEqual("cp949", result.encoding)
            self.assertEqual((), result.issues)
            self.assertEqual((1, 2, 3, 5), result.rows[0].selected_numbers)
            self.assertEqual("차시 별", result.rows[0].mode)

    def test_server_minimal_row_parses_material_id_and_student_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "server-orders.csv"
            path.write_text(
                "주문번호,자료ID,학생이름,부수,메모\n"
                "A-17,QT-UNIT-0123456789ABCDEF,한동훈,2,오늘 배정\n",
                encoding="utf-8-sig",
            )

            result = load_order_csv(path)

            self.assertEqual((), result.issues)
            self.assertEqual("", result.rows[0].source)
            self.assertEqual("QT-UNIT-0123456789ABCDEF", result.rows[0].material_id)
            self.assertEqual("한동훈", result.rows[0].student_name)
            self.assertEqual(2, result.rows[0].copies)

    def test_server_file_code_header_is_accepted_as_material_lookup_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "server-orders.csv"
            path.write_text(
                "주문번호,파일코드,학생이름\n"
                "A-18,t_ilsan_past_gongsu2_high_midterm_005,장규리\n",
                encoding="utf-8-sig",
            )

            result = load_order_csv(path)

            self.assertEqual((), result.issues)
            self.assertEqual(
                "t_ilsan_past_gongsu2_high_midterm_005",
                result.rows[0].material_id,
            )

    def test_able_order_rows_preserve_selected_document_kind(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "able-orders.csv"
            path.write_text(
                "자료,과목,문서,인쇄구성,개별번호,부수\n"
                'A:ble,기하,백지리뷰,차시별,"(3,4,5)",1\n'
                "A:ble,확통,개념정리,풀 세트,,1\n",
                encoding="utf-8-sig",
            )

            result = load_order_csv(path)

            self.assertEqual((), result.issues)
            self.assertEqual(("백지리뷰", "개념정리"), tuple(row.document_kind for row in result.rows))
            self.assertEqual((3, 4, 5), result.rows[0].selected_numbers)

    def test_able_concept_order_rejects_lesson_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bad-able-order.csv"
            path.write_text(
                "자료,과목,문서,인쇄구성,개별번호,부수\n"
                'A:ble,기하,개념정리,차시별,"(1)",1\n',
                encoding="utf-8-sig",
            )

            result = load_order_csv(path)

            self.assertEqual((), result.rows)
            self.assertIn("개념정리는 풀 세트", result.issues[0].message)

    def test_invalid_rows_are_collected_with_source_line_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bad.csv"
            path.write_text(
                ",".join(ORDER_CSV_HEADERS)
                + "\n"
                + "1,단원평가,공통수학1,Lv.3,1,,,,,,1,N,,,,\n"
                + "2,알수없음,,,,,,,,,1,N,,,,\n",
                encoding="utf-8-sig",
            )

            result = load_order_csv(path)

            self.assertEqual(0, len(result.rows))
            self.assertEqual((2, 3), tuple(issue.row_number for issue in result.issues))


class OrderBlockTests(unittest.TestCase):
    def test_name_separator_order_key_is_nfc_and_places_blank_last(self) -> None:
        self.assertLess(name_separator_order_key("김가람"), name_separator_order_key("박가람"))
        self.assertEqual(name_separator_order_key("김가람"), name_separator_order_key("김가람"))
        self.assertLess(name_separator_order_key("윤가람"), name_separator_order_key(""))

    def test_blank_review_block_is_two_page_portrait_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf = Path(temp_dir) / "백지 리뷰 짧.pdf"
            write_pdf(pdf, 2)
            app = PrintManagerApp.__new__(PrintManagerApp)
            app.printer_var = FakeVariable("iR-ADV 8585 III")

            with patch("unit_print_manager.find_blank_review_pdf", return_value=pdf):
                block = app.make_blank_review_block(3)

            self.assertEqual(3, block.copies)
            self.assertEqual("세로", block.printer_route)
            self.assertEqual("iR-ADV 8585 III - 세로", block.printer_override)
            self.assertEqual("백지리뷰 / 2쪽", block.display_label)

    def test_omr_block_is_one_page_landscape_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf = Path(temp_dir) / "OMR 내신.pdf"
            write_pdf(pdf, 1)
            app = PrintManagerApp.__new__(PrintManagerApp)
            app.printer_var = FakeVariable("iR-ADV 8585 III")

            with patch("unit_print_manager.find_omr_pdf", return_value=pdf):
                block = app.make_omr_block(5)

            self.assertEqual("omr", block.source)
            self.assertEqual(5, block.copies)
            self.assertEqual("가로", block.printer_route)
            self.assertEqual("iR-ADV 8585 III - 가로", block.printer_override)
            self.assertEqual("OMR 내신 / 1쪽", block.display_label)

            restored = app.block_from_config(app.block_to_config(block))

            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual("omr", restored.source)
            self.assertEqual("가로", restored.printer_route)
            self.assertEqual("iR-ADV 8585 III - 가로", restored.printer_override)

    def test_order_id_is_added_to_queue_label(self) -> None:
        item = ExamPdf(1, "백지", Path("백지.pdf"))
        block = PrintBlock("백지리뷰", "고정", "단일", 1, 1, 1, [item], display_label="백지리뷰 / 2쪽")
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.make_blank_review_block = Mock(return_value=block)

        result = app.make_block_from_order_row(order_row(order_id="A-17"))

        self.assertEqual("주문 A-17 / 백지리뷰 / 2쪽", result.display_label)

    def test_registered_test_id_builds_exact_named_block(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf = Path(temp_dir) / "시험지.pdf"
            write_pdf(pdf)
            asset = RegisteredTestAsset(
                material_id="QT-SCHOOL-0123456789ABCDEF",
                source="학군지기출",
                source_key="test_school_district",
                subject="대수",
                filter_label="시험 구분",
                filter_value="기말",
                level="상",
                term="기말",
                round_name="",
                number=11,
                display_name="상 1회차 휘문고",
                path=pdf,
                relative_path="대수/기말/상/시험지.pdf",
            )
            app = PrintManagerApp.__new__(PrintManagerApp)
            app.printer_var = FakeVariable("iR-ADV 8585 III")
            row = order_row(
                source="학군지기출",
                material_id=asset.material_id,
                student_name="한동훈",
                copies=3,
            )

            block = app.make_block_from_order_row(
                row,
                {asset.material_id.casefold(): asset},
            )

            self.assertEqual("test_school_district", block.source)
            self.assertEqual((11,), block.selected_numbers)
            self.assertEqual(3, block.copies)
            self.assertEqual("한동훈", block.name_separator_label)
            self.assertEqual(pdf, block.files[0].path)
            self.assertEqual("세로", block.printer_route)

    def test_blank_review_survives_undo_serialization(self) -> None:
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.printer_var = FakeVariable("iR-ADV 8585 III")
        item = ExamPdf(1, "백지 리뷰 짧", Path("백지 리뷰 짧.pdf"))
        original = PrintBlock(
            "백지리뷰",
            "고정",
            "단일",
            1,
            1,
            4,
            [item],
            source="blank_review",
            display_label="백지리뷰 / 2쪽",
            printer_route="세로",
        )

        restored = app.block_from_config(app.block_to_config(original))

        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual("blank_review", restored.source)
        self.assertEqual(4, restored.copies)
        self.assertEqual("세로", restored.printer_route)
        self.assertEqual("백지 리뷰 짧.pdf", restored.files[0].path.name)

    def test_any_resolution_error_keeps_queue_unchanged(self) -> None:
        row = order_row()
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.order_csv_result = OrderCsvResult((row,), (), "utf-8-sig")
        app.blocks = []
        app.resolve_order_csv_blocks = Mock(
            return_value=([], [OrderCsvIssue(2, "백지리뷰", "파일 없음")])
        )
        app.push_undo_state = Mock()

        with patch("unit_print_manager.messagebox.showwarning") as warning:
            added = app.add_order_csv_blocks()

        self.assertFalse(added)
        self.assertEqual([], app.blocks)
        app.push_undo_state.assert_not_called()
        warning.assert_called_once()

    def test_valid_order_rows_are_appended_in_one_undo_step(self) -> None:
        first = PrintBlock(
            "첫째",
            "",
            "",
            1,
            1,
            1,
            [ExamPdf(1, "a", Path("a.pdf"))],
            name_separator_label="한동훈",
        )
        second = PrintBlock("둘째", "", "", 1, 1, 1, [ExamPdf(1, "b", Path("b.pdf"))])
        rows = (order_row(row_number=2), order_row(row_number=3, order_id="1002"))
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.order_csv_result = OrderCsvResult(rows, (), "utf-8-sig")
        app.order_csv_path = Path("orders.csv")
        app.blocks = []
        app.block_status_by_id = {}
        app.name_separator_page_var = FakeVariable(False)
        app.resolve_order_csv_blocks = Mock(return_value=([first, second], []))
        app.push_undo_state = Mock()
        app.update_queue_view = Mock()
        app.update_preview = Mock()
        app.log = Mock()
        app.clear_order_csv = Mock()

        added = app.add_order_csv_blocks()

        self.assertTrue(added)
        self.assertEqual([first, second], app.blocks)
        app.push_undo_state.assert_called_once_with("대량 주문서 블록 추가")
        self.assertEqual("대기", app.block_status_by_id[id(first)])
        self.assertEqual("대기", app.block_status_by_id[id(second)])
        self.assertTrue(app.name_separator_page_var.get())
        app.update_queue_view.assert_called_once_with(select_index=1)
        app.update_preview.assert_called_once()
        app.clear_order_csv.assert_called_once()

    def test_bulk_order_blocks_are_stably_sorted_by_student_name(self) -> None:
        def block(label: str, student_name: str) -> PrintBlock:
            return PrintBlock(
                label,
                "",
                "",
                1,
                1,
                1,
                [ExamPdf(1, label, Path(f"{label}.pdf"))],
                name_separator_label=student_name,
            )

        existing = block("기존", "")
        yun = block("윤-첫째", "윤서진")
        kim_first = block("김-첫째", "김가람")
        unnamed = block("이름없음", "")
        kim_second = block("김-둘째", "김가람")
        rows = tuple(order_row(row_number=number) for number in range(2, 6))
        app = PrintManagerApp.__new__(PrintManagerApp)
        app.order_csv_result = OrderCsvResult(rows, (), "utf-8-sig")
        app.order_csv_path = Path("orders.csv")
        app.blocks = [existing]
        app.block_status_by_id = {id(existing): "대기"}
        app.name_separator_page_var = FakeVariable(False)
        app.resolve_order_csv_blocks = Mock(
            return_value=([yun, kim_first, unnamed, kim_second], [])
        )
        app.push_undo_state = Mock()
        app.update_queue_view = Mock()
        app.update_preview = Mock()
        app.log = Mock()
        app.clear_order_csv = Mock()

        added = app.add_order_csv_blocks()

        self.assertTrue(added)
        self.assertEqual(
            [existing, kim_first, kim_second, yun, unnamed],
            app.blocks,
        )
        self.assertTrue(app.name_separator_page_var.get())
        app.update_queue_view.assert_called_once_with(select_index=4)
        app.log.assert_called_once()
        self.assertIn("이름 구분지 가나다순", app.log.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
