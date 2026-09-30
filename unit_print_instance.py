"""One Quick Printer process per Windows user, independent of install path."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


WAIT_OBJECT_0 = 0
WAIT_ABANDONED = 0x80
WAIT_TIMEOUT = 0x102
WAIT_FAILED = 0xFFFFFFFF


class InstanceLockError(RuntimeError):
    """Windows could not establish exclusive application ownership."""


class WindowsMutexAPI:
    """Small native API boundary; no GUI or printer operations."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise InstanceLockError("빠른 인쇄기의 중복 실행 검사는 Windows에서만 지원됩니다.")
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        self.kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        self.kernel32.CreateMutexW.restype = wintypes.HANDLE
        self.kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        self.kernel32.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
        self.kernel32.ReleaseMutex.restype = wintypes.BOOL
        self.kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
        self.kernel32.LocalFree.restype = ctypes.c_void_p
        self.advapi32.OpenProcessToken.argtypes = (
            wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)
        )
        self.advapi32.OpenProcessToken.restype = wintypes.BOOL
        self.advapi32.GetTokenInformation.argtypes = (
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        )
        self.advapi32.GetTokenInformation.restype = wintypes.BOOL
        self.advapi32.ConvertSidToStringSidW.argtypes = (
            ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)
        )
        self.advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL

    def user_sid(self) -> str:
        token = wintypes.HANDLE()
        if not self.advapi32.OpenProcessToken(self.kernel32.GetCurrentProcess(), 0x8, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            needed = wintypes.DWORD()
            self.advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
            if needed.value == 0:
                raise ctypes.WinError(ctypes.get_last_error())
            buffer = ctypes.create_string_buffer(needed.value)
            if not self.advapi32.GetTokenInformation(token, 1, buffer, needed, ctypes.byref(needed)):
                raise ctypes.WinError(ctypes.get_last_error())
            # TOKEN_USER begins with SID_AND_ATTRIBUTES; its first member is a SID pointer.
            sid_pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
            sid_text = wintypes.LPWSTR()
            if not self.advapi32.ConvertSidToStringSidW(sid_pointer, ctypes.byref(sid_text)):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                return sid_text.value
            finally:
                self.kernel32.LocalFree(ctypes.cast(sid_text, ctypes.c_void_p))
        finally:
            self.close(token)

    def create(self, name: str):
        handle = self.kernel32.CreateMutexW(None, False, name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    def wait(self, handle) -> int:
        return self.kernel32.WaitForSingleObject(handle, 0)

    def release(self, handle) -> None:
        if not self.kernel32.ReleaseMutex(handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self, handle) -> None:
        self.kernel32.CloseHandle(handle)


def instance_mutex_name(user_sid: str) -> str:
    """Global Windows namespace also covers multiple sessions for the same account."""
    if not user_sid or not user_sid.startswith("S-") or any(c not in "S-0123456789" for c in user_sid):
        raise InstanceLockError("Windows 사용자 식별 정보를 확인하지 못했습니다.")
    return "Global\\QuickPrinter.UnitPrint." + user_sid


class SingleInstance:
    """Keep ownership until process shutdown; Windows releases it after a crash."""

    def __init__(self, api=None, *, name: str | None = None) -> None:
        self.api = api if api is not None else WindowsMutexAPI()
        self.name = name if name is not None else instance_mutex_name(self.api.user_sid())
        self.handle = None

    def acquire(self) -> bool:
        if self.handle is not None:
            raise InstanceLockError("중복 실행 검사 핸들이 이미 열려 있습니다.")
        handle = self.api.create(self.name)
        try:
            result = self.api.wait(handle)
            if result in (WAIT_OBJECT_0, WAIT_ABANDONED):
                self.handle = handle
                return True
            if result == WAIT_TIMEOUT:
                return False
            raise InstanceLockError("Windows 중복 실행 잠금을 확인하지 못했습니다.")
        finally:
            if self.handle is None:
                self.api.close(handle)

    def close(self) -> None:
        if self.handle is None:
            return
        handle, self.handle = self.handle, None
        try:
            self.api.release(handle)
        finally:
            self.api.close(handle)
