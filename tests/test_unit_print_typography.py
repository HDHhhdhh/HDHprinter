"""Korean wrapping rules and real withdrawn Tk labels; no native UI control."""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont, ttk
import unicodedata
import unittest
from unittest.mock import Mock

from unit_print_typography import korean_label, wrap_korean_text


class KoreanWrappingTests(unittest.TestCase):
    def test_words_stay_together_until_a_whole_word_exceeds_the_width(self):
        self.assertEqual("우리 가족 맞춤형\n미국여행 코스", wrap_korean_text(
            "우리 가족 맞춤형 미국여행 코스", 9, len, mode="normal",
        ))

    def test_pretty_reduces_last_word_isolation_without_creating_another_orphan(self):
        text = "여행을 떠나는 가장 쉬운 방법"
        self.assertEqual("여행을 떠나는 가장 쉬운\n방법", wrap_korean_text(text, 13, len, mode="normal"))
        self.assertEqual("여행을 떠나는 가장\n쉬운 방법", wrap_korean_text(text, 13, len))
        self.assertEqual("가나다 라마바\n사아자", wrap_korean_text("가나다 라마바 사아자", 7, len))

    def test_title_balance_keeps_the_original_line_count(self):
        text = "여행을 떠나는 가장 쉬운 방법"
        balanced = wrap_korean_text(text, 13, len, mode="balance")
        self.assertEqual("여행을 떠나는\n가장 쉬운 방법", balanced)
        self.assertEqual(2, len(balanced.splitlines()))

    def test_long_terms_and_file_names_fit_without_losing_characters(self):
        for text in ("개인정보처리방침", "공수2_YBM_핵심문제_변형_01-16파트.pdf"):
            with self.subTest(text=text):
                rendered = wrap_korean_text(text, 6, len)
                self.assertEqual(text, rendered.replace("\n", ""))
                self.assertTrue(all(len(line) <= 6 for line in rendered.splitlines()))

    def test_combining_marks_hangul_jamo_and_joined_emoji_stay_intact_on_overflow(self):
        text = "각남달"
        normalized_width = lambda value: len(unicodedata.normalize("NFC", value))
        self.assertEqual("각\n남\n달", wrap_korean_text(text, 1, normalized_width))
        text = "a\u0301b\u0301"
        self.assertEqual("a\u0301\nb\u0301", wrap_korean_text(text, 1, normalized_width))
        self.assertEqual("👩‍💻\n👍🏽", wrap_korean_text("👩‍💻👍🏽", 3, len))

    def test_explicit_newlines_and_empty_paragraphs_survive(self):
        self.assertEqual("제목\n\n본문 안내\n", wrap_korean_text("제목\r\n\r\n본문 안내\r\n", 30, len))
        self.assertEqual("", wrap_korean_text("", 30, len))

    def test_operator_labels_collapse_extra_horizontal_spaces_but_not_nbsp(self):
        self.assertEqual("이름 구분지", wrap_korean_text("  이름   \t 구분지  ", 30, len))
        self.assertEqual("공수2\u00a0YBM\n핵심문제", wrap_korean_text("공수2\u00a0YBM 핵심문제", 7, len))

    def test_nowrap_and_zero_width_leave_source_text_untouched(self):
        text = "원본  경로\n그대로"
        self.assertEqual(text, wrap_korean_text(text, 3, len, mode="nowrap"))
        self.assertEqual(text, wrap_korean_text(text, 0, len))

    def test_pretty_does_not_move_a_word_that_would_overflow(self):
        self.assertEqual("가나다 라마바사아\n자차카타", wrap_korean_text("가나다 라마바사아 자차카타", 10, len))

    def test_all_modes_keep_normal_word_order_and_width_at_multiple_sizes(self):
        text = "현재 작업이 중지되었습니다 용지를 확인한 뒤 해결 후 이어서를 눌러 주세요"
        for mode in ("normal", "pretty", "balance"):
            for width in range(10, 45):
                with self.subTest(mode=mode, width=width):
                    rendered = wrap_korean_text(text, width, len, mode=mode)
                    self.assertEqual(text, " ".join(rendered.split()))
                    self.assertTrue(all(len(line) <= width for line in rendered.splitlines()))


class KoreanLabelTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.errors = []
        self.root.report_callback_exception = lambda kind, error, trace: self.errors.append(error)
        self.frame = tk.Frame(self.root)
        self.frame.place(x=0, y=0, width=480, height=400)
        self.font = tkfont.Font(root=self.root, family="Malgun Gothic", size=10)

    def label(self, source, *, mode="pretty", width=150, native=True):
        options = {"textvariable": source} if isinstance(source, tk.Variable) else {"text": source}
        label = korean_label(self.frame, native=native, mode=mode, font=self.font, wraplength=width, **options)
        label.pack()
        self.root.update_idletasks()
        self.assertEqual([], self.errors)
        return label

    def test_source_variable_is_preserved_and_repeated_updates_are_coalesced(self):
        original = "우리 가족 맞춤형 미국여행 코스\n\n  확인   안내"
        source = tk.StringVar(self.root, original)
        label = self.label(source, width=self.font.measure("우리 가족 맞춤형"), mode="normal")
        self.assertEqual(original, source.get())
        self.assertIn("\n", label.cget("text"))
        for line in label.cget("text").splitlines():
            self.assertLessEqual(self.font.measure(line), int(label.cget("wraplength")))
        controller = label._korean_wrap
        controller.font.measure = Mock(wraps=controller.font.measure)
        for _ in range(10):
            source.set(original)
        self.root.update_idletasks()
        controller.font.measure.assert_not_called()
        source.set("새로운 안내문을 표시합니다")
        self.root.update_idletasks()
        self.assertEqual("새로운 안내문을 표시합니다", source.get())
        self.assertEqual(source.get(), " ".join(label.cget("text").split()))
        self.assertEqual([], self.errors)

    def test_wraplength_resize_reflows_without_changing_source_or_original_paragraphs(self):
        original = "우리 가족 맞춤형 미국여행 코스\n\n다음 안내문"
        source = tk.StringVar(self.root, original)
        label = self.label(source, width=280, mode="normal")
        wide = label.cget("text")
        label.configure(wraplength=100)
        label._korean_wrap.render()
        narrow = label.cget("text")
        self.assertGreater(len(narrow.splitlines()), len(wide.splitlines()))
        self.assertIn("\n\n", narrow)
        self.assertEqual(original, source.get())
        label.configure(wraplength=280)
        label._korean_wrap.render()
        self.assertEqual(wide, label.cget("text"))

    def test_styled_ttk_label_and_native_popup_use_the_same_policy(self):
        original = "여행을 떠나는 가장 쉬운 방법"
        native = self.label(original, mode="balance", width=230)
        themed = self.label(original, mode="balance", width=230, native=False)
        self.assertEqual(native.cget("text"), themed.cget("text"))
        self.assertEqual([], self.errors)

    def test_nowrap_disables_native_widget_wrapping(self):
        label = self.label("숙소 예약 확정하기", mode="nowrap", width=30)
        self.assertEqual(0, int(label.cget("wraplength")))
        self.assertEqual("숙소 예약 확정하기", label.cget("text"))

    def test_ttk_style_font_is_measured_when_the_widget_has_no_font_override(self):
        style = ttk.Style(self.root)
        style.configure("OperatorKorean.TLabel", font=("Malgun Gothic", 11, "bold"))
        label = korean_label(self.frame, text="우리 가족 맞춤형 미국여행 코스",
                             style="OperatorKorean.TLabel", wraplength=150)
        label.pack()
        self.root.update_idletasks()
        self.assertEqual("bold", label._korean_wrap.font.actual("weight"))
        self.assertEqual(11, label._korean_wrap.font.actual("size"))
        self.assertEqual([], self.errors)

    def test_named_font_size_change_invalidates_cached_measurements(self):
        source = tk.StringVar(self.root, "우리 가족 맞춤형 미국여행 코스")
        label = self.label(source, width=160)
        before = label.cget("text")
        self.font.configure(size=16)
        source.set(source.get())
        self.root.update_idletasks()
        self.assertEqual(16, label._korean_wrap.font.actual("size"))
        self.assertGreater(len(label.cget("text").splitlines()), len(before.splitlines()))
        self.assertEqual([], self.errors)

    def test_containing_frame_resize_reflows_without_a_new_source_update(self):
        text = "현재 작업이 중지되었습니다 용지를 확인한 뒤 해결 후 이어서를 눌러 주세요"
        source = tk.StringVar(self.root, text)
        label = self.label(source, width=390)
        wide = label.cget("text")
        self.frame.place_configure(width=190)
        self.root.update()
        # Withdrawn windows suppress Configure notifications. Invoke the
        # registered Tcl callback against real geometry; this is not native QA.
        binding = label._korean_wrap.parent_binding
        self.assertIn(binding, self.frame.bind("<Configure>"))
        self.frame.tk.call(binding)
        self.root.update_idletasks()
        narrow = label.cget("text")
        self.assertGreater(len(narrow.splitlines()), len(wide.splitlines()))
        for line in narrow.splitlines():
            self.assertLessEqual(self.font.measure(line), 190)
        self.frame.place_configure(width=480)
        self.root.update()
        self.frame.tk.call(binding)
        self.root.update_idletasks()
        self.assertEqual(wide, label.cget("text"))
        self.assertEqual(text, source.get())
        self.assertEqual([], self.errors)

    def test_destroy_removes_callbacks_but_keeps_operator_source_variable(self):
        source = tk.StringVar(self.root, "자료 확인 안내")
        label = self.label(source)
        self.assertEqual(1, len(source.trace_info()))
        source.set("변경 대기")
        self.assertIsNotNone(label._korean_wrap.pending)
        label.destroy()
        self.assertEqual([], source.trace_info())
        source.set("창을 닫아도 원본은 유지")
        self.root.update_idletasks()
        self.assertEqual("창을 닫아도 원본은 유지", source.get())
        self.assertEqual([], self.errors)


if __name__ == "__main__":
    unittest.main()
