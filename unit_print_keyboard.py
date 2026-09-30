"""Keyboard navigation state and user-facing shortcut copy for 빠른 인쇄기.

The Tk widgets remain owned by ``unit_print_manager``.  This module only
describes the operator's current keyboard state so the state machine and its
help text can be tested without opening a window.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum


class KeyboardZone(str, Enum):
    BUILDER = "builder"
    QUEUE_LIST = "queue_list"
    QUEUE_EDITOR = "queue_editor"


class KeyboardInteraction(str, Enum):
    FIELDS = "fields"
    COMBO_OPEN = "combo_open"
    PDF_LIST = "pdf_list"


@dataclass
class KeyboardState:
    """Explicit keyboard context shared by focus, commands, and help UI."""

    zone: KeyboardZone = KeyboardZone.BUILDER
    interaction: KeyboardInteraction = KeyboardInteraction.FIELDS
    builder_tab: int = 0
    test_tab: int = 0
    material_tab: int = 0
    lesson_material_tab: int = 0
    combo_widget_path: str | None = None
    queue_cursor: int | None = None
    queue_anchor: int | None = None
    editor_dirty: bool = False
    last_builder_focus: dict[str, str] = field(default_factory=dict)
    keyboard_flash_token: int = 0

    def enter_builder(self, *, tab: int | None = None) -> None:
        self.zone = KeyboardZone.BUILDER
        self.interaction = KeyboardInteraction.FIELDS
        self.combo_widget_path = None
        if tab is not None:
            self.builder_tab = tab

    def enter_queue(self, *, cursor: int | None = None) -> None:
        self.zone = KeyboardZone.QUEUE_LIST
        self.interaction = KeyboardInteraction.FIELDS
        self.combo_widget_path = None
        if cursor is not None:
            self.queue_cursor = cursor

    def enter_editor(self, *, cursor: int | None = None) -> None:
        self.zone = KeyboardZone.QUEUE_EDITOR
        self.interaction = KeyboardInteraction.FIELDS
        self.combo_widget_path = None
        if cursor is not None:
            self.queue_cursor = cursor

    def open_combo(self, widget_path: str) -> None:
        self.interaction = KeyboardInteraction.COMBO_OPEN
        self.combo_widget_path = widget_path

    def close_popup(self) -> None:
        self.interaction = KeyboardInteraction.FIELDS
        self.combo_widget_path = None

    def enter_pdf_list(self) -> None:
        self.interaction = KeyboardInteraction.PDF_LIST

    def leave_nested_interaction(self) -> None:
        self.close_popup()

    def mark_keyboard_activity(self) -> None:
        self.keyboard_flash_token += 1


@dataclass(frozen=True)
class QueueSelection:
    """A queue selection with independent cursor and stable range anchor."""

    selected_indices: frozenset[int] = frozenset()
    focus_index: int | None = None
    anchor_index: int | None = None


def _valid_queue_index(count: int, index: int | None) -> bool:
    return isinstance(index, int) and 0 <= index < count


def _normalized_queue_selection(
    count: int,
    selected_indices: Iterable[int],
    focus_index: int | None,
    anchor_index: int | None,
) -> QueueSelection:
    if count <= 0:
        return QueueSelection()
    return QueueSelection(
        frozenset(index for index in selected_indices if _valid_queue_index(count, index)),
        focus_index if _valid_queue_index(count, focus_index) else None,
        anchor_index if _valid_queue_index(count, anchor_index) else None,
    )


def _queue_range_selection(
    state: QueueSelection, target_index: int, *, control: bool
) -> QueueSelection:
    anchor = state.anchor_index
    if anchor is None:
        anchor = state.focus_index if state.focus_index is not None else target_index
    selected = frozenset(range(min(anchor, target_index), max(anchor, target_index) + 1))
    if control:
        selected |= state.selected_indices
    return QueueSelection(selected, target_index, anchor)


def queue_click_selection(
    count: int,
    selected_indices: Iterable[int],
    focus_index: int | None,
    anchor_index: int | None,
    index: int,
    *,
    control: bool = False,
    shift: bool = False,
) -> QueueSelection:
    """Apply an ordinary, Ctrl, Shift, or Ctrl+Shift queue click.

    Ctrl can remove the last selected row while leaving the cursor on that row.
    Shift keeps the existing anchor so extending and shrinking a range agree.
    Invalid rows are discarded, and an invalid click leaves that cleaned state.
    """
    state = _normalized_queue_selection(count, selected_indices, focus_index, anchor_index)
    if not _valid_queue_index(count, index):
        return state
    if shift:
        return _queue_range_selection(state, index, control=control)
    if control:
        return QueueSelection(state.selected_indices ^ frozenset({index}), index, index)
    return QueueSelection(frozenset({index}), index, index)


def queue_navigation_selection(
    count: int,
    selected_indices: Iterable[int],
    focus_index: int | None,
    anchor_index: int | None,
    target_index: int,
    *,
    control: bool = False,
    shift: bool = False,
) -> QueueSelection:
    """Apply navigation, allowing Ctrl to move the cursor without selecting."""
    state = _normalized_queue_selection(count, selected_indices, focus_index, anchor_index)
    if not _valid_queue_index(count, target_index):
        return state
    if shift:
        return _queue_range_selection(state, target_index, control=control)
    if control:
        return QueueSelection(state.selected_indices, target_index, state.anchor_index)
    return QueueSelection(frozenset({target_index}), target_index, target_index)


def queue_navigation_target(
    count: int, current: int | None, key: str, page_size: int = 1
) -> int | None:
    """Resolve a Tk navigation keysym to a bounded row, or None for no target.

    An absent or invalid cursor starts at the first row, except End selects the
    last row.  Page movement always advances by at least one row.
    """
    if count <= 0:
        return None
    keys = {
        "Up": "Up", "KP_Up": "Up",
        "Down": "Down", "KP_Down": "Down",
        "Home": "Home", "KP_Home": "Home",
        "End": "End", "KP_End": "End",
        "Prior": "Prior", "KP_Prior": "Prior", "PageUp": "Prior", "KP_Page_Up": "Prior",
        "Next": "Next", "KP_Next": "Next", "PageDown": "Next", "KP_Page_Down": "Next",
    }
    action = keys.get(key)
    if action is None:
        return None
    if action == "Home":
        return 0
    if action == "End":
        return count - 1
    if not _valid_queue_index(count, current):
        return 0
    step = max(1, page_size) if action in {"Prior", "Next"} else 1
    offset = -step if action in {"Up", "Prior"} else step
    return min(count - 1, max(0, current + offset))


def _shortcut_rows(*rows: tuple[str, str]) -> str:
    """Keep each shortcut and its action on one readable row."""
    return "\n".join(f"{command}  {action}" for command, action in rows)


def shortcut_help(state: KeyboardState) -> tuple[str, str]:
    """Return the context label and compact help copy for the current state."""
    if state.interaction is KeyboardInteraction.COMBO_OPEN:
        if state.zone is KeyboardZone.QUEUE_EDITOR:
            return (
                "작업 큐 · 드롭다운",
                _shortcut_rows(
                    ("↑↓", "목록 이동"),
                    ("Enter", "선택 확정"),
                    ("Esc", "닫기"),
                ),
            )
        return (
            "교재 선택 · 드롭다운",
            _shortcut_rows(
                ("↑↓", "목록 이동"),
                ("Enter", "선택 확정"),
                ("Esc", "닫기"),
            ),
        )
    if state.interaction is KeyboardInteraction.PDF_LIST:
        return (
            "교재 선택 · PDF 목록",
            _shortcut_rows(
                ("↑↓", "파일 이동"),
                ("Shift+클릭 / ↑↓", "범위 선택"),
                ("Ctrl+클릭", "선택 토글"),
                ("Ctrl+A", "전체 선택"),
                ("Delete", "선택 삭제"),
                ("Esc", "입력 영역 이동"),
                ("Ctrl+Enter", "PDF 블록 추가"),
                ("Tab / Shift+Tab", "필드 이동"),
                ("F6", "영역 이동"),
            ),
        )
    if state.zone is KeyboardZone.QUEUE_EDITOR:
        if state.editor_dirty:
            return (
                "작업 큐 · 블록 수정 · 미저장",
                _shortcut_rows(
                    ("←→", "같은 행 이동"),
                    ("↑↓", "다음 행 이동"),
                    ("Enter", "수정 저장 (닫힌 드롭다운은 열기)"),
                    ("Ctrl+Enter", "저장"),
                    ("Ctrl+Shift+Enter", "인쇄 시작"),
                    ("Esc", "취소"),
                    ("Tab / Shift+Tab", "필드 이동"),
                    ("F6", "영역 이동"),
                ),
            )
        return (
            "작업 큐 · 블록 수정",
            _shortcut_rows(
                ("←→", "같은 행 이동"),
                ("↑↓", "다음 행 이동"),
                ("Enter", "수정 저장 (닫힌 드롭다운은 열기)"),
                ("Ctrl+Enter", "저장"),
                ("Ctrl+Shift+Enter", "인쇄 시작"),
                ("Esc", "작업 큐 이동"),
                ("Tab / Shift+Tab", "필드 이동"),
                ("F6", "영역 이동"),
            ),
        )
    if state.zone is KeyboardZone.QUEUE_LIST:
        return (
            "작업 큐",
            _shortcut_rows(
                ("↑↓ / Home / End", "블록 이동"),
                ("PageUp / PageDown", "페이지 이동"),
                ("Ctrl+↑↓ / Space", "커서만 이동 / 선택 토글"),
                ("Shift+↑↓", "범위 선택 (Ctrl: 추가)"),
                ("Ctrl+A", "전체 선택"),
                ("Ctrl+C / V", "블록 복사 / 붙여넣기"),
                ("Ctrl+D", "선택 복제"),
                ("Ctrl+Z / Y", "실행 취소 / 다시 실행"),
                ("F2 / Enter", "선택 블록 수정"),
                ("Delete", "블록 삭제"),
                ("Alt+↑↓", "순서 이동"),
                ("Shift+F10", "작업 메뉴"),
                ("Ctrl+Shift+Enter", "인쇄 시작"),
                ("Tab / F6", "수정 / 영역 이동"),
            ),
        )
    if state.builder_tab == 0 and state.test_tab > 0:
        test_names = ("단원평가", "복습테스트", "학군지기출", "일산기출")
        test_name = test_names[state.test_tab] if state.test_tab < len(test_names) else "테스트"
        context = f"테스트 선택 · {test_name}"
        message = _shortcut_rows(
            ("←→", "같은 행 이동"),
            ("↑↓", "다음 행 이동"),
            ("Enter", "드롭다운 열기/실행"),
            ("Ctrl+Enter", "블록 추가"),
            ("Ctrl+Tab", "자료 탭 이동"),
            ("Tab / Shift+Tab", "필드 이동"),
            ("F6", "영역 이동"),
        )
    elif state.builder_tab == 1 and state.material_tab == 6:
        context = "교재 선택 · 교과서"
        message = _shortcut_rows(
            ("←→", "같은 행 이동"),
            ("↑↓", "다음 행 이동"),
            ("Enter", "드롭다운 열기/실행"),
            ("Ctrl+Enter", "교과서 블록 추가"),
            ("Ctrl+Tab", "자료 탭 이동"),
            ("Tab / Shift+Tab", "필드 이동"),
            ("F6", "영역 이동"),
        )
    elif state.builder_tab == 1 and state.material_tab == 5:
        context = "교재 선택 · APEX Boost"
        message = _shortcut_rows(
            ("←→", "같은 행 이동"),
            ("↑↓", "다음 행 이동"),
            ("Enter", "드롭다운 열기/실행"),
            ("Ctrl+Enter", "APEX Boost 블록 추가"),
            ("Ctrl+Tab", "자료 탭 이동"),
            ("Tab / Shift+Tab", "필드 이동"),
            ("F6", "영역 이동"),
        )
    elif state.builder_tab == 1 and state.material_tab == 4:
        context = "교재 선택 · 교평 기출"
        message = _shortcut_rows(
            ("←→", "같은 행 이동"),
            ("↑↓", "다음 행 이동"),
            ("Enter", "드롭다운 열기/실행"),
            ("Ctrl+Enter", "교평 기출 블록 추가"),
            ("Ctrl+Tab", "자료 탭 이동"),
            ("Tab / Shift+Tab", "필드 이동"),
            ("F6", "영역 이동"),
        )
    elif state.builder_tab == 1 and state.material_tab == 3:
        lesson_name = "빈출유형" if state.lesson_material_tab == 1 else "트레이닝팩"
        add_name = "빈출유형" if state.lesson_material_tab == 1 else "트팩"
        context = f"교재 선택 · {lesson_name}"
        message = _shortcut_rows(
            ("←→", "같은 행 이동"),
            ("↑↓", "다음 행 이동"),
            ("Enter", "드롭다운 열기/실행"),
            ("Ctrl+Enter", f"{add_name} 블록 추가"),
            ("Ctrl+Tab", "자료 탭 이동"),
            ("Tab / Shift+Tab", "필드 이동"),
            ("F6", "영역 이동"),
        )
    elif state.builder_tab == 1 and state.material_tab == 2:
        context = "교재 선택 · 기출엄선"
        message = _shortcut_rows(
            ("←→", "같은 행 이동"),
            ("↑↓", "다음 행 이동"),
            ("Enter", "드롭다운 열기/실행"),
            ("Ctrl+Enter", "기출엄선 블록 추가"),
            ("Ctrl+Tab", "자료 탭 이동"),
            ("Tab / Shift+Tab", "필드 이동"),
            ("F6", "영역 이동"),
        )
    elif state.builder_tab == 1 and state.material_tab == 1:
        context = "교재 선택 · A:ble"
        message = _shortcut_rows(
            ("←→", "같은 행 이동"),
            ("↑↓", "다음 행 이동"),
            ("Enter", "드롭다운 열기/실행"),
            ("Ctrl+Enter", "블록 추가"),
            ("Ctrl+Tab", "자료 탭 이동"),
            ("Tab / Shift+Tab", "필드 이동"),
            ("F6", "영역 이동"),
        )
    elif state.builder_tab == 1:
        context = "교재 선택 · APEX"
        message = _shortcut_rows(
            ("←→", "같은 행 이동"),
            ("↑↓", "다음 행 이동"),
            ("Enter", "드롭다운 열기/실행"),
            ("Ctrl+Enter", "블록 추가"),
            ("Ctrl+Tab", "자료 탭 이동"),
            ("Tab / Shift+Tab", "필드 이동"),
            ("F6", "영역 이동"),
        )
    elif state.builder_tab == 2:
        context = "교재 선택 · 백지리뷰 / OMR"
        message = _shortcut_rows(
            ("←→", "같은 행 이동"),
            ("↑↓", "다음 행 이동"),
            ("Enter", "실행"),
            ("Ctrl+Enter", "현재 양식 블록 추가"),
            ("Ctrl+Tab", "자료 탭 이동"),
            ("Tab / Shift+Tab", "필드 이동"),
            ("F6", "영역 이동"),
        )
    elif state.builder_tab == 3:
        context = "교재 선택 · PDF 파일"
        message = _shortcut_rows(
            ("←→", "같은 행 이동"),
            ("↑↓", "다음 행 이동"),
            ("Enter", "드롭다운 열기/실행"),
            ("Ctrl+Enter", "PDF 블록 추가"),
            ("Ctrl+Tab", "자료 탭 이동"),
            ("Tab / Shift+Tab", "필드 이동"),
            ("F6", "영역 이동"),
        )
    elif state.builder_tab == 4:
        context = "교재 선택 · 대량 주문서"
        message = _shortcut_rows(
            ("←→", "같은 행 이동"),
            ("↑↓", "다음 행 이동"),
            ("Enter", "실행"),
            ("Ctrl+Enter", "주문서 블록 추가"),
            ("Ctrl+Tab", "자료 탭 이동"),
            ("Tab / Shift+Tab", "필드 이동"),
            ("F6", "영역 이동"),
        )
    else:
        context = "테스트 선택 · 단원평가"
        message = _shortcut_rows(
            ("←→", "같은 행 이동"),
            ("↑↓", "다음 행 이동"),
            ("Enter", "드롭다운 열기/실행"),
            ("Ctrl+Enter", "블록 추가"),
            ("Ctrl+Tab", "자료 탭 이동"),
            ("Tab / Shift+Tab", "필드 이동"),
            ("F6", "영역 이동"),
        )
    return context, message
