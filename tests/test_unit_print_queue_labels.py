from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from unit_print_manager import DeviceHealth, ExamPdf, PrintBlock, PrintManagerApp
from unit_print_theme import WORKBENCH_COLORS


class Font:
    def __init__(self, width):
        self.width = width

    def measure(self, value):
        return len(value) * self.width


def block(**changes):
    value = PrintBlock(
        subject="공통수학1", level="Lv.3", round_name="1", start=1, end=2,
        copies=3, files=[ExamPdf(1, "첫 단원", Path("sample-1.pdf")),
                         ExamPdf(2, "끝 단원", Path("sample-2.pdf"))],
        name_separator_label="검증 학생",
    )
    return replace(value, **changes)


class QueueLabelTests(unittest.TestCase):
    def setUp(self):
        self.app = PrintManagerApp.__new__(PrintManagerApp)
        self.app.colors = dict(WORKBENCH_COLORS)
        self.app.queue_canvas = Mock()
        self.app.queue_line_font = Font(5)
        self.app.queue_line_bold_font = Font(8)

    def test_catalog_label_starts_with_name_tab_subject_and_omits_normal_marker(self):
        value = block()
        before = asdict(value)
        prefix, _ = self.app.queue_block_line_parts(value)
        self.assertTrue(prefix.startswith("[검증 학생] / 단원평가 / 공통수학1 / Lv.3 / 1회차 / "))
        self.assertNotIn("일반", prefix)
        self.assertEqual(before, asdict(value))
        unnamed, _ = self.app.queue_block_line_parts(replace(value, name_separator_label=""))
        self.assertTrue(unnamed.startswith("단원평가 / 공통수학1 / "))

    def test_bundle_marker_is_retained_after_source_and_subject(self):
        name, title, details = self.app.queue_block_text_parts(block(bundled=True))
        self.assertEqual("[검증 학생] / ", name)
        self.assertEqual("단원평가 / 공통수학1", title)
        self.assertTrue(details.startswith(" / [묶음] / "))

    def test_registered_sources_have_exact_operator_subtabs(self):
        cases = {
            "catalog": "단원평가", "test_review": "복습테스트",
            "test_school_district": "학군지기출", "test_ilsan": "일산기출",
            "material_apex": "APEX", "material_able": "A:ble",
            "material_training_pack": "트레이닝팩", "material_frequent_type": "빈출유형",
            "material_gyopyeong": "교평 기출", "material_selected_exam": "기출엄선",
            "material_textbook": "교과서", "material_textbook_variant": "교과서 핵심문제 변형",
            "blank_review": "백지리뷰", "omr": "OMR", "custom": "PDF파일",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(expected, self.app.queue_subtab_label(block(source=source)))
        self.assertEqual("APEX Boost", self.app.queue_subtab_label(block(source="material_apex", level="APEX Boost")))
        self.assertEqual("묶음 블록", self.app.queue_subtab_label(block(source="custom", material_kind="queue_bundle")))

    def test_textbook_variant_publisher_document_and_filename_remain_in_regular_details(self):
        value = block(source="material_textbook_variant", level="검증 출판사",
                      display_label="교과서 / 공통수학1 / 검증 출판사 / 핵심문제 변형 / 01~02파트")
        _, title, details = self.app.queue_block_text_parts(value)
        self.assertEqual("교과서 핵심문제 변형 / 공통수학1", title)
        self.assertIn("검증 출판사 / 핵심문제 변형 / 01~02파트", details)
        self.assertIn("sample-1 ~ sample-2", details)

    def test_custom_titles_are_not_inferred_as_registered_materials(self):
        value = block(source="custom", display_label="APEX / 임의 PDF 설명")
        _, title, details = self.app.queue_block_text_parts(value)
        self.assertEqual("PDF파일 / 공통수학1", title)
        self.assertIn("APEX / 임의 PDF 설명", details)

    def test_rich_runs_use_blue_bold_name_black_bold_title_and_regular_details(self):
        self.app.draw_queue_title_runs(block(), 30, 39, 1500)
        calls = self.app.queue_canvas.create_text.call_args_list
        self.assertEqual(3, len(calls))
        self.assertIs(self.app.queue_line_bold_font, calls[0].kwargs["font"])
        self.assertEqual(self.app.colors["complete"], calls[0].kwargs["fill"])
        self.assertIs(self.app.queue_line_bold_font, calls[1].kwargs["font"])
        self.assertEqual("#000000", calls[1].kwargs["fill"])
        self.assertIs(self.app.queue_line_font, calls[2].kwargs["font"])
        for previous, current in zip(calls, calls[1:]):
            self.assertEqual(previous.args[0] + previous.kwargs["font"].measure(previous.kwargs["text"]), current.args[0])
        self.assertEqual({39}, {call.args[1] for call in calls})

    def test_long_name_clips_with_its_bold_font_and_stops_before_remaining_runs(self):
        self.app.draw_queue_title_runs(block(name_separator_label="합성 이름 " * 80), 30, 39, 125)
        calls = self.app.queue_canvas.create_text.call_args_list
        self.assertEqual(1, len(calls))
        self.assertTrue(calls[0].kwargs["text"].endswith("…"))
        self.assertLessEqual(calls[0].args[0] + calls[0].kwargs["font"].measure(calls[0].kwargs["text"]), 153)

    def test_tiny_remaining_width_draws_nothing_and_newlines_cannot_make_a_second_line(self):
        self.app.draw_queue_title_runs(block(), 30, 39, 3)
        self.app.queue_canvas.create_text.assert_not_called()
        parts = self.app.queue_block_text_parts(block(subject="합성\n과목", name_separator_label="검증\n학생"))
        self.assertTrue(all("\n" not in part for part in parts))


class OperatorStatusColorTests(unittest.TestCase):
    def setUp(self):
        self.app = PrintManagerApp.__new__(PrintManagerApp)
        self.app.colors = dict(WORKBENCH_COLORS)
        self.app.status_badge = SimpleNamespace(configure=Mock())
        self.app.device_status_badge = SimpleNamespace(configure=Mock())

    def test_normal_application_idle_is_blue_but_active_work_is_green(self):
        for message, expected in (("대기 중", "complete"), ("인쇄 전송 중", "success"),
                                  ("실제 인쇄 확인 중", "success"), ("인쇄 완료", "complete"),
                                  ("프린터 오류", "danger"), ("작업 경고", "danger")):
            with self.subTest(message=message):
                self.app.app_status_var = SimpleNamespace(get=lambda: message)
                self.app.refresh_status_badge()
                self.assertEqual(self.app.colors[expected], self.app.status_badge.configure.call_args.kwargs["fg"])

    def test_ready_supply_bands_and_unknown_staples_are_normal_blue(self):
        message = "프린터 인쇄 가능 · A4 카세트 1 66~100%, 카세트 2 0% · 스테이플 알 수 없음"
        self.app.device_status_var = SimpleNamespace(get=lambda: message)
        self.app.refresh_device_status_badge(DeviceHealth.READY)
        self.assertEqual(self.app.colors["complete"], self.app.device_status_badge.configure.call_args.kwargs["fg"])
        self.app.refresh_device_status_badge(DeviceHealth.BUSY)
        self.assertEqual(self.app.colors["success"], self.app.device_status_badge.configure.call_args.kwargs["fg"])

    def test_actual_device_errors_are_red_and_only_queue_waiting_is_orange(self):
        self.app.device_status_var = SimpleNamespace(get=lambda: "프린터 오류 · 용지 걸림")
        self.app.refresh_device_status_badge(DeviceHealth.ERROR)
        self.assertEqual(self.app.colors["danger"], self.app.device_status_badge.configure.call_args.kwargs["fg"])
        for status in ("대기", "예약", "추가됨"):
            self.assertEqual(self.app.colors["warning"], self.app.queue_status_colors(status)[1])


if __name__ == "__main__":
    unittest.main()
