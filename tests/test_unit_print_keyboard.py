import unittest

from unit_print_keyboard import KeyboardInteraction, KeyboardState, KeyboardZone, shortcut_help


class KeyboardStateTests(unittest.TestCase):
    def test_builder_to_combo_help(self) -> None:
        state = KeyboardState(builder_tab=1, material_tab=0)
        context, message = shortcut_help(state)
        self.assertEqual(context, "교재 선택 · APEX")
        self.assertIn("Ctrl+Enter", message)
        self.assertIn("←→", message)
        self.assertEqual(len(message.splitlines()), 7)
        self.assertTrue(all("  " in line for line in message.splitlines()))
        self.assertIn("Tab / Shift+Tab  필드 이동", message)
        self.assertIn("F6  영역 이동", message)

        state.open_combo(".apex.variant")
        context, message = shortcut_help(state)
        self.assertEqual(context, "교재 선택 · 드롭다운")
        self.assertIn("Esc", message)

    def test_queue_selection_help(self) -> None:
        state = KeyboardState()
        state.enter_queue(cursor=2)
        context, message = shortcut_help(state)
        self.assertEqual(state.zone, KeyboardZone.QUEUE_LIST)
        self.assertEqual(context, "작업 큐")
        self.assertIn("Shift+↑↓", message)
        self.assertIn("Ctrl+C / V", message)
        self.assertIn("블록 복사 / 붙여넣기", message)
        for command in ("Home", "End", "PageUp", "PageDown", "Ctrl+↑↓", "Ctrl+D", "Ctrl+Z", "F2", "Shift+F10", "F6"):
            with self.subTest(command=command):
                self.assertIn(command, message)
        self.assertIn("커서만 이동", message)

    def test_editor_dirty_help_and_cancel_state(self) -> None:
        state = KeyboardState()
        state.enter_editor(cursor=1)
        state.editor_dirty = True
        context, message = shortcut_help(state)
        self.assertEqual(state.zone, KeyboardZone.QUEUE_EDITOR)
        self.assertEqual(context, "작업 큐 · 블록 수정 · 미저장")
        self.assertIn("Ctrl+Enter", message)
        self.assertIn("저장", message)
        self.assertIn("Ctrl+Shift+Enter", message)
        self.assertIn("인쇄 시작", message)
        self.assertIn("←→", message)
        self.assertIn("Enter  수정 저장", message)
        self.assertIn("닫힌 드롭다운은 열기", message)
        self.assertIn("Tab / Shift+Tab  필드 이동", message)
        self.assertIn("F6  영역 이동", message)

        state.enter_queue(cursor=1)
        self.assertEqual(state.interaction, KeyboardInteraction.FIELDS)

    def test_editor_combo_help_only_lists_native_popup_actions(self) -> None:
        state = KeyboardState()
        state.enter_editor(cursor=1)
        state.open_combo(".editor.route")
        context, message = shortcut_help(state)
        self.assertEqual(context, "작업 큐 · 드롭다운")
        self.assertNotIn("Ctrl+Enter", message)
        self.assertIn("Enter  선택 확정", message)

    def test_pdf_list_is_nested_builder_interaction(self) -> None:
        state = KeyboardState(builder_tab=3)
        state.enter_pdf_list()
        context, message = shortcut_help(state)
        self.assertEqual(state.zone, KeyboardZone.BUILDER)
        self.assertEqual(context, "교재 선택 · PDF 목록")
        self.assertIn("Delete", message)
        self.assertIn("Ctrl+A  전체 선택", message)
        self.assertIn("Shift+클릭 / ↑↓  범위 선택", message)
        self.assertIn("Ctrl+클릭  선택 토글", message)

    def test_saved_editor_help_keeps_enter_save_and_field_navigation(self) -> None:
        state = KeyboardState()
        state.enter_editor(cursor=1)
        context, message = shortcut_help(state)

        self.assertEqual(context, "작업 큐 · 블록 수정")
        self.assertIn("Enter  수정 저장", message)
        self.assertIn("닫힌 드롭다운은 열기", message)
        self.assertIn("Tab / Shift+Tab  필드 이동", message)
        self.assertIn("F6  영역 이동", message)

    def test_every_builder_tab_documents_field_and_zone_navigation(self) -> None:
        for builder_tab in range(5):
            with self.subTest(builder_tab=builder_tab):
                _context, message = shortcut_help(KeyboardState(builder_tab=builder_tab))
                self.assertIn("Tab / Shift+Tab  필드 이동", message)
                self.assertIn("F6  영역 이동", message)

    def test_blank_review_and_order_tabs_have_dedicated_help(self) -> None:
        review_context, review_message = shortcut_help(KeyboardState(builder_tab=2))
        order_context, order_message = shortcut_help(KeyboardState(builder_tab=4))

        self.assertEqual("교재 선택 · 백지리뷰 / OMR", review_context)
        self.assertIn("현재 양식 블록 추가", review_message)
        self.assertEqual("교재 선택 · 대량 주문서", order_context)
        self.assertIn("주문서 블록 추가", order_message)

    def test_training_pack_help_names_the_new_material_tab(self) -> None:
        state = KeyboardState(builder_tab=1, material_tab=3)
        context, message = shortcut_help(state)

        self.assertEqual(context, "교재 선택 · 트레이닝팩")
        self.assertIn("트팩 블록 추가", message)
        self.assertIn("Ctrl+Tab", message)

    def test_frequent_type_help_tracks_the_nested_material_tab(self) -> None:
        state = KeyboardState(builder_tab=1, material_tab=3, lesson_material_tab=1)
        context, message = shortcut_help(state)

        self.assertEqual(context, "교재 선택 · 빈출유형")
        self.assertIn("빈출유형 블록 추가", message)
        self.assertIn("Ctrl+Tab", message)

    def test_apex_boost_help_names_the_separate_material_tab(self) -> None:
        state = KeyboardState(builder_tab=1, material_tab=5)
        context, message = shortcut_help(state)

        self.assertEqual(context, "교재 선택 · APEX Boost")
        self.assertIn("APEX Boost 블록 추가", message)
        self.assertIn("Ctrl+Tab", message)

    def test_textbook_help_names_the_appended_material_tab(self) -> None:
        state = KeyboardState(builder_tab=1, material_tab=6)
        context, message = shortcut_help(state)

        self.assertEqual(context, "교재 선택 · 교과서")
        self.assertIn("교과서 블록 추가", message)
        self.assertIn("Ctrl+Tab", message)

    def test_able_and_selected_exam_help_follow_the_reordered_tabs(self) -> None:
        able_context, able_message = shortcut_help(
            KeyboardState(builder_tab=1, material_tab=1)
        )
        selected_context, selected_message = shortcut_help(
            KeyboardState(builder_tab=1, material_tab=2)
        )

        self.assertEqual(able_context, "교재 선택 · A:ble")
        self.assertIn("Ctrl+Enter", able_message)
        self.assertEqual(selected_context, "교재 선택 · 기출엄선")
        self.assertIn("기출엄선 블록 추가", selected_message)

    def test_gyopyeong_help_names_the_separate_material_tab(self) -> None:
        state = KeyboardState(builder_tab=1, material_tab=4)
        context, message = shortcut_help(state)

        self.assertEqual(context, "교재 선택 · 교평 기출")
        self.assertIn("교평 기출 블록 추가", message)
        self.assertIn("Ctrl+Tab", message)

    def test_each_level_based_test_tab_has_dedicated_help(self) -> None:
        expected = ("복습테스트", "학군지기출", "일산기출")
        for index, name in enumerate(expected, start=1):
            with self.subTest(name=name):
                state = KeyboardState(builder_tab=0, test_tab=index)
                context, message = shortcut_help(state)

                self.assertEqual(f"테스트 선택 · {name}", context)
                self.assertIn("Ctrl+Enter", message)
                self.assertIn("Ctrl+Tab", message)

    def test_unit_assessment_help_uses_test_context(self) -> None:
        context, message = shortcut_help(KeyboardState(builder_tab=0, test_tab=0))

        self.assertEqual("테스트 선택 · 단원평가", context)
        self.assertIn("블록 추가", message)


if __name__ == "__main__":
    unittest.main()
