"""Deduplicated Windows notifications for Quick Printer state transitions."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from unit_print_taskbar import taskbar_window_handle


NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010
NIF_REALTIME = 0x00000040
NIIF_INFO = 0x00000001
NIIF_WARNING = 0x00000002
NIIF_ERROR = 0x00000003
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040
IDI_APPLICATION = 32512
QUICK_PRINT_NOTIFICATION_ID = 0x51504E46  # "QPNF"


class NotificationLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class OperatorNotification:
    title: str
    message: str
    level: NotificationLevel = NotificationLevel.INFO


class NotificationGate:
    """Deliver one alert per category/key transition until it is cleared."""

    def __init__(self) -> None:
        self._keys: dict[str, str] = {}

    def allow(self, category: str, key: object) -> bool:
        normalized_category = str(category).strip()
        normalized_key = str(key).strip()
        if self._keys.get(normalized_category) == normalized_key:
            return False
        self._keys[normalized_category] = normalized_key
        return True

    def clear(self, category: str) -> None:
        self._keys.pop(str(category).strip(), None)

    def clear_all(self) -> None:
        self._keys.clear()


class NotificationBackend(Protocol):
    def show(self, notification: OperatorNotification, *, replace: bool) -> bool: ...

    def hide(self) -> None: ...

    def close(self) -> None: ...


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HANDLE),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeoutOrVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", _GUID),
        ("hBalloonIcon", wintypes.HANDLE),
    ]


class CtypesShellNotificationBackend:
    """Small Shell_NotifyIcon wrapper; no separate tray process is created."""

    def __init__(
        self,
        hwnd: int,
        *,
        icon_path: Path | None = None,
        notification_id: int = QUICK_PRINT_NOTIFICATION_ID,
    ) -> None:
        if os.name != "nt":
            raise OSError("Windows 알림은 Windows에서만 사용할 수 있습니다.")
        self.hwnd = int(hwnd)
        self.notification_id = int(notification_id)
        self._active = False
        self._owned_icon = False
        self._icon_handle = self._load_icon(icon_path)
        shell32 = ctypes.windll.shell32
        self._notify = shell32.Shell_NotifyIconW
        self._notify.argtypes = [wintypes.DWORD, ctypes.POINTER(_NOTIFYICONDATAW)]
        self._notify.restype = wintypes.BOOL

    def _load_icon(self, icon_path: Path | None) -> int:
        user32 = ctypes.windll.user32
        if icon_path is not None and Path(icon_path).is_file():
            load_image = user32.LoadImageW
            load_image.argtypes = [
                wintypes.HINSTANCE,
                wintypes.LPCWSTR,
                wintypes.UINT,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.UINT,
            ]
            load_image.restype = wintypes.HANDLE
            handle = int(
                load_image(
                    None,
                    str(icon_path),
                    IMAGE_ICON,
                    0,
                    0,
                    LR_LOADFROMFILE | LR_DEFAULTSIZE,
                )
                or 0
            )
            if handle:
                self._owned_icon = True
                return handle
        load_icon = user32.LoadIconW
        load_icon.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
        load_icon.restype = wintypes.HANDLE
        resource = ctypes.cast(ctypes.c_void_p(IDI_APPLICATION), wintypes.LPCWSTR)
        return int(load_icon(None, resource) or 0)

    def _data(self, notification: OperatorNotification | None = None) -> _NOTIFYICONDATAW:
        data = _NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
        data.hWnd = self.hwnd
        data.uID = self.notification_id
        if notification is not None:
            data.uFlags = NIF_ICON | NIF_TIP | NIF_INFO | NIF_REALTIME
            data.hIcon = self._icon_handle
            data.szTip = "빠른 인쇄기"
            data.szInfo = notification.message[:255]
            data.szInfoTitle = notification.title[:63]
            data.dwInfoFlags = {
                NotificationLevel.INFO: NIIF_INFO,
                NotificationLevel.WARNING: NIIF_WARNING,
                NotificationLevel.ERROR: NIIF_ERROR,
            }[notification.level]
            data.uTimeoutOrVersion = 10_000
        return data

    def show(self, notification: OperatorNotification, *, replace: bool) -> bool:
        data = self._data(notification)
        operation = NIM_MODIFY if replace and self._active else NIM_ADD
        succeeded = bool(self._notify(operation, ctypes.byref(data)))
        if not succeeded and operation == NIM_MODIFY:
            succeeded = bool(self._notify(NIM_ADD, ctypes.byref(data)))
        self._active = succeeded
        return succeeded

    def hide(self) -> None:
        if not self._active:
            return
        data = self._data()
        self._notify(NIM_DELETE, ctypes.byref(data))
        self._active = False

    def close(self) -> None:
        self.hide()
        if self._owned_icon and self._icon_handle:
            try:
                ctypes.windll.user32.DestroyIcon(self._icon_handle)
            finally:
                self._icon_handle = 0
                self._owned_icon = False


class WindowsBalloonNotifier:
    """Stateful notifier that replaces a visible Quick Printer balloon."""

    def __init__(
        self,
        tk_window_id: int,
        *,
        icon_path: Path | None = None,
        backend: NotificationBackend | None = None,
    ) -> None:
        if backend is not None:
            self.backend = backend
        else:
            hwnd = taskbar_window_handle(tk_window_id)
            self.backend = CtypesShellNotificationBackend(hwnd, icon_path=icon_path)
        self._visible = False

    def show(
        self,
        title: str,
        message: str,
        level: NotificationLevel = NotificationLevel.INFO,
    ) -> bool:
        shown = self.backend.show(
            OperatorNotification(
                title=" ".join(str(title).split()),
                message=" ".join(str(message).split()),
                level=level,
            ),
            replace=self._visible,
        )
        self._visible = bool(shown)
        return self._visible

    def hide(self) -> None:
        self.backend.hide()
        self._visible = False

    def close(self) -> None:
        self.backend.close()
        self._visible = False
