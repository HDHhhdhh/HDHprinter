"""Optional Windows Credential Manager storage for Canon Remote UI login."""

from __future__ import annotations

import ctypes
import hashlib
import os
from ctypes import wintypes
from dataclasses import dataclass


class CredentialStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeviceCredential:
    department_id: str
    pin: str


def credential_target(base_url: str) -> str:
    digest = hashlib.sha256(base_url.strip().casefold().encode("utf-8")).hexdigest()[:20]
    return f"QuickPrinter/CanonRemoteUI/{digest}"


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _advapi32() -> object:
    if os.name != "nt":
        raise CredentialStoreError("Windows Credential Manager는 Windows에서만 사용할 수 있습니다.")
    return ctypes.WinDLL("Advapi32.dll", use_last_error=True)


def save_device_credential(base_url: str, department_id: str, pin: str) -> None:
    if not department_id or not pin:
        raise ValueError("부서 ID와 PIN을 모두 입력해 주세요.")
    advapi32 = _advapi32()
    cred_write = advapi32.CredWriteW
    cred_write.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
    cred_write.restype = wintypes.BOOL
    pin_bytes = pin.encode("utf-16-le")
    blob = (ctypes.c_ubyte * len(pin_bytes)).from_buffer_copy(pin_bytes)
    credential = _CREDENTIALW()
    credential.Type = 1  # CRED_TYPE_GENERIC
    credential.TargetName = credential_target(base_url)
    credential.Comment = "빠른 인쇄기 Canon Remote UI"
    credential.CredentialBlobSize = len(pin_bytes)
    credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = 2  # CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = department_id
    if not cred_write(ctypes.byref(credential), 0):
        error = ctypes.get_last_error()
        raise CredentialStoreError(f"자격 증명을 저장하지 못했습니다. Windows 오류 {error}")


def load_device_credential(base_url: str) -> DeviceCredential | None:
    advapi32 = _advapi32()
    cred_read = advapi32.CredReadW
    cred_read.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_CREDENTIALW))]
    cred_read.restype = wintypes.BOOL
    cred_free = advapi32.CredFree
    cred_free.argtypes = [ctypes.c_void_p]
    cred_free.restype = None
    pointer = ctypes.POINTER(_CREDENTIALW)()
    if not cred_read(credential_target(base_url), 1, 0, ctypes.byref(pointer)):
        error = ctypes.get_last_error()
        if error == 1168:  # ERROR_NOT_FOUND
            return None
        raise CredentialStoreError(f"자격 증명을 읽지 못했습니다. Windows 오류 {error}")
    try:
        credential = pointer.contents
        blob = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        pin = blob.decode("utf-16-le")
        return DeviceCredential(credential.UserName or "", pin)
    finally:
        cred_free(pointer)


def delete_device_credential(base_url: str) -> None:
    advapi32 = _advapi32()
    cred_delete = advapi32.CredDeleteW
    cred_delete.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    cred_delete.restype = wintypes.BOOL
    if not cred_delete(credential_target(base_url), 1, 0):
        error = ctypes.get_last_error()
        if error != 1168:
            raise CredentialStoreError(f"자격 증명을 지우지 못했습니다. Windows 오류 {error}")
