"""Windows taskbar progress and durable status overlays for Quick Printer."""

from __future__ import annotations

import ctypes
import math
import os
import tempfile
from ctypes import wintypes
from pathlib import Path
from typing import Protocol


TBPF_NOPROGRESS = 0x00000000
TBPF_INDETERMINATE = 0x00000001
TBPF_NORMAL = 0x00000002
TBPF_ERROR = 0x00000004
TBPF_PAUSED = 0x00000008


def four_stage_progress(completed: int, total: int) -> int:
    """Return a stable 0..4 taskbar stage without repainting every document."""

    if total <= 0 or completed <= 0:
        return 0
    if completed >= total:
        return 4
    return min(3, math.floor((completed * 4) / total))


class TaskbarBackend(Protocol):
    def set_progress_state(self, hwnd: int, state: int) -> None: ...

    def set_progress_value(self, hwnd: int, completed: int, total: int) -> None: ...

    def set_overlay_icon(self, hwnd: int, icon_handle: int, description: str) -> None: ...

    def close(self) -> None: ...


class ComtypesTaskbarBackend:
    """Thin ITaskbarList4 wrapper loaded after Tk has shown its first frame."""

    def __init__(self) -> None:
        from comtypes import COMMETHOD, GUID, HRESULT, IUnknown
        from comtypes.client import CreateObject

        hwnd_type = wintypes.HWND
        void_pointer = ctypes.c_void_p

        # This Windows shell advertises ITaskbarList4 directly.  Its vtable
        # begins with every ITaskbarList3 method used below.
        class ITaskbarList4(IUnknown):
            _iid_ = GUID("{C43DC798-95D1-4BEA-9030-BB99E2983A1A}")
            _methods_ = [
                COMMETHOD([], HRESULT, "HrInit"),
                COMMETHOD([], HRESULT, "AddTab", (["in"], hwnd_type, "hwnd")),
                COMMETHOD([], HRESULT, "DeleteTab", (["in"], hwnd_type, "hwnd")),
                COMMETHOD([], HRESULT, "ActivateTab", (["in"], hwnd_type, "hwnd")),
                COMMETHOD([], HRESULT, "SetActiveAlt", (["in"], hwnd_type, "hwnd")),
                COMMETHOD(
                    [],
                    HRESULT,
                    "MarkFullscreenWindow",
                    (["in"], hwnd_type, "hwnd"),
                    (["in"], wintypes.BOOL, "fullscreen"),
                ),
                COMMETHOD(
                    [],
                    HRESULT,
                    "SetProgressValue",
                    (["in"], hwnd_type, "hwnd"),
                    (["in"], ctypes.c_ulonglong, "completed"),
                    (["in"], ctypes.c_ulonglong, "total"),
                ),
                COMMETHOD(
                    [],
                    HRESULT,
                    "SetProgressState",
                    (["in"], hwnd_type, "hwnd"),
                    (["in"], ctypes.c_uint, "flags"),
                ),
                COMMETHOD([], HRESULT, "RegisterTab", (["in"], hwnd_type, "tab"), (["in"], hwnd_type, "mdi")),
                COMMETHOD([], HRESULT, "UnregisterTab", (["in"], hwnd_type, "tab")),
                COMMETHOD([], HRESULT, "SetTabOrder", (["in"], hwnd_type, "tab"), (["in"], hwnd_type, "before")),
                COMMETHOD(
                    [],
                    HRESULT,
                    "SetTabActive",
                    (["in"], hwnd_type, "tab"),
                    (["in"], hwnd_type, "mdi"),
                    (["in"], wintypes.DWORD, "reserved"),
                ),
                COMMETHOD([], HRESULT, "ThumbBarAddButtons", (["in"], hwnd_type, "hwnd"), (["in"], wintypes.UINT, "count"), (["in"], void_pointer, "buttons")),
                COMMETHOD([], HRESULT, "ThumbBarUpdateButtons", (["in"], hwnd_type, "hwnd"), (["in"], wintypes.UINT, "count"), (["in"], void_pointer, "buttons")),
                COMMETHOD([], HRESULT, "ThumbBarSetImageList", (["in"], hwnd_type, "hwnd"), (["in"], void_pointer, "images")),
                COMMETHOD(
                    [],
                    HRESULT,
                    "SetOverlayIcon",
                    (["in"], hwnd_type, "hwnd"),
                    (["in"], wintypes.HICON, "icon"),
                    (["in"], wintypes.LPCWSTR, "description"),
                ),
            ]

        self._taskbar = CreateObject(
            GUID("{56FDF344-FD6D-11D0-958A-006097C9A090}"),
            interface=ITaskbarList4,
        )
        self._taskbar.HrInit()

    def set_progress_state(self, hwnd: int, state: int) -> None:
        self._taskbar.SetProgressState(hwnd, state)

    def set_progress_value(self, hwnd: int, completed: int, total: int) -> None:
        self._taskbar.SetProgressValue(hwnd, completed, total)

    def set_overlay_icon(self, hwnd: int, icon_handle: int, description: str) -> None:
        self._taskbar.SetOverlayIcon(hwnd, icon_handle or None, description)

    def close(self) -> None:
        self._taskbar = None


def taskbar_window_handle(tk_window_id: int) -> int:
    """Resolve Tk's child HWND to the root HWND that owns the taskbar button."""

    if os.name != "nt":
        return int(tk_window_id)
    get_ancestor = ctypes.windll.user32.GetAncestor
    get_ancestor.argtypes = [wintypes.HWND, wintypes.UINT]
    get_ancestor.restype = wintypes.HWND
    return int(get_ancestor(int(tk_window_id), 2) or int(tk_window_id))


def create_overlay_icon(label: str, *, background: str) -> int:
    """Create a small numbered HICON; the caller owns the returned handle."""

    from PIL import Image, ImageDraw, ImageFont

    size = 32
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((1, 1, size - 2, size - 2), fill=background, outline="#ffffff", width=2)
    try:
        font = ImageFont.truetype("arialbd.ttf", 22)
    except OSError:
        font = ImageFont.load_default()
    bounds = draw.textbbox((0, 0), label, font=font)
    x = (size - (bounds[2] - bounds[0])) / 2 - bounds[0]
    y = (size - (bounds[3] - bounds[1])) / 2 - bounds[1] - 1
    draw.text((x, y), label, font=font, fill="#ffffff")

    temp_path = Path(tempfile.gettempdir()) / f"quick_printer_taskbar_{os.getpid()}_{label}.ico"
    image.save(temp_path, format="ICO", sizes=[(16, 16), (20, 20), (24, 24), (32, 32)])
    try:
        load_image = ctypes.windll.user32.LoadImageW
        load_image.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        load_image.restype = wintypes.HANDLE
        return int(load_image(None, str(temp_path), 1, 32, 32, 0x0010) or 0)
    finally:
        temp_path.unlink(missing_ok=True)


class TaskbarIndicator:
    def __init__(self, tk_window_id: int, backend: TaskbarBackend | None = None) -> None:
        self.hwnd = taskbar_window_handle(tk_window_id)
        self.backend = backend or ComtypesTaskbarBackend()
        self._last_progress: tuple[int, int, int] | None = None
        self._overlay_kind: tuple[str, str] | None = None

    def invalidate_cache(self) -> None:
        """Explorer lost its copy; force the next apply to send every value."""

        self._last_progress = None
        self._overlay_kind = None

    def clear_progress(self) -> None:
        if self._last_progress == (TBPF_NOPROGRESS, 0, 0):
            return
        self.backend.set_progress_state(self.hwnd, TBPF_NOPROGRESS)
        self._last_progress = (TBPF_NOPROGRESS, 0, 0)

    def set_indeterminate(self) -> None:
        if self._last_progress == (TBPF_INDETERMINATE, 0, 0):
            return
        self.backend.set_progress_state(self.hwnd, TBPF_INDETERMINATE)
        self._last_progress = (TBPF_INDETERMINATE, 0, 0)

    def set_progress(self, completed: int, total: int) -> None:
        stage = four_stage_progress(completed, total)
        if stage == 0:
            self.set_indeterminate()
            return
        target = (TBPF_NORMAL, stage, 4)
        if self._last_progress == target:
            return
        self.backend.set_progress_state(self.hwnd, TBPF_NORMAL)
        self.backend.set_progress_value(self.hwnd, stage, 4)
        self._last_progress = target

    def set_paused(self) -> None:
        if not self._last_progress or self._last_progress[0] != TBPF_PAUSED:
            completed, total = self._progress_value_or_default()
            self.backend.set_progress_value(self.hwnd, completed, total)
            self.backend.set_progress_state(self.hwnd, TBPF_PAUSED)
            self._last_progress = (TBPF_PAUSED, completed, total)
        self.set_overlay("!", background="#9a6b24", description="빠른 인쇄기 작업 중지")

    def set_error(self) -> None:
        if not self._last_progress or self._last_progress[0] != TBPF_ERROR:
            completed, total = self._progress_value_or_default()
            self.backend.set_progress_value(self.hwnd, completed, total)
            self.backend.set_progress_state(self.hwnd, TBPF_ERROR)
            self._last_progress = (TBPF_ERROR, completed, total)
        self.set_overlay("!", background="#b7443e", description="빠른 인쇄기 오류")

    def set_completed(self) -> None:
        self.clear_progress()
        self.set_overlay("1", background="#2f6f9f", description="빠른 인쇄 완료 1건")

    def clear_overlay(self) -> None:
        if self._overlay_kind is None:
            return
        self.backend.set_overlay_icon(self.hwnd, 0, "")
        self._overlay_kind = None

    def set_overlay(self, label: str, *, background: str, description: str) -> None:
        overlay_kind = (label, background)
        if self._overlay_kind == overlay_kind:
            return
        icon_handle = create_overlay_icon(label, background=background)
        if not icon_handle:
            return
        try:
            self.backend.set_overlay_icon(self.hwnd, icon_handle, description)
            self._overlay_kind = overlay_kind
        finally:
            destroy_icon = ctypes.windll.user32.DestroyIcon
            destroy_icon.argtypes = [wintypes.HICON]
            destroy_icon.restype = wintypes.BOOL
            destroy_icon(icon_handle)

    def reset(self) -> None:
        self.clear_progress()
        self.clear_overlay()

    def close(self) -> None:
        try:
            self.reset()
        finally:
            self.backend.close()

    def _progress_value_or_default(self) -> tuple[int, int]:
        if self._last_progress and self._last_progress[2] > 0:
            return self._last_progress[1], self._last_progress[2]
        return 0, 4
