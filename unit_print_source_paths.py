"""Open folders and select the PDFs currently referenced by queue blocks."""

from __future__ import annotations

import ctypes
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path


MAX_SOURCE_FOLDERS_PER_REQUEST = 10


@dataclass(frozen=True, slots=True)
class SourceFolderOpenResult:
    opened: tuple[Path, ...] = ()
    missing_files: tuple[Path, ...] = ()
    missing_folders: tuple[Path, ...] = ()
    failed_folders: tuple[Path, ...] = ()
    skipped_folders: int = 0
    cancelled: bool = False


def source_folder_targets(paths: Iterable[Path]) -> tuple[tuple[Path, tuple[Path, ...]], ...]:
    """Group lexical absolute paths without filesystem reads or PDF access."""
    folders: dict[str, tuple[Path, dict[str, Path]]] = {}
    for value in paths:
        path = Path(os.path.abspath(value))
        folder = path.parent
        folder_key = os.path.normcase(str(folder))
        if folder_key not in folders:
            folders[folder_key] = (folder, {})
        folders[folder_key][1].setdefault(os.path.normcase(str(path)), path)
    return tuple((folder, tuple(files.values())) for folder, files in folders.values())


class _SourceFolderOpenCancelled(Exception):
    pass


def _load_shell_libraries():
    if os.name != "nt":
        raise OSError("Windows 폴더 열기를 지원하지 않습니다.")
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    ole = ctypes.WinDLL("ole32", use_last_error=True)
    shell.SHParseDisplayName.argtypes = (ctypes.c_wchar_p, ctypes.c_void_p,
                                        ctypes.POINTER(ctypes.c_void_p), ctypes.c_ulong,
                                        ctypes.POINTER(ctypes.c_ulong))
    shell.SHParseDisplayName.restype = ctypes.c_long
    shell.ILIsParent.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int)
    shell.ILIsParent.restype = ctypes.c_int
    shell.ILFindLastID.argtypes = (ctypes.c_void_p,)
    shell.ILFindLastID.restype = ctypes.c_void_p
    shell.SHOpenFolderAndSelectItems.argtypes = (ctypes.c_void_p, ctypes.c_uint,
                                               ctypes.POINTER(ctypes.c_void_p), ctypes.c_ulong)
    shell.SHOpenFolderAndSelectItems.restype = ctypes.c_long
    ole.CoInitializeEx.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoTaskMemFree.argtypes = (ctypes.c_void_p,)
    ole.CoTaskMemFree.restype = None
    ole.CoUninitialize.argtypes = ()
    ole.CoUninitialize.restype = None
    return shell, ole


def _check_hresult(result: int, action: str) -> None:
    if ctypes.c_int32(result).value < 0:
        raise OSError(f"{action} 실패 (0x{result & 0xffffffff:08X})")


def open_windows_folder(folder: Path, files: tuple[Path, ...], *,
                        cancelled: Callable[[], bool] = lambda: False) -> None:
    """Use literal paths; only owned absolute PIDLs are freed, never child views."""
    folder = Path(folder)
    if not folder.is_absolute():
        raise OSError("폴더 경로는 절대 경로여야 합니다.")
    folder = Path(os.path.abspath(folder))
    selected: dict[str, Path] = {}
    for value in files:
        path = Path(value)
        if not path.is_absolute():
            raise OSError("PDF 경로는 절대 경로여야 합니다.")
        path = Path(os.path.abspath(path))
        if os.path.normcase(str(path.parent)) != os.path.normcase(str(folder)):
            raise OSError("PDF는 선택한 폴더의 파일이어야 합니다.")
        selected.setdefault(os.path.normcase(str(path)), path)
    if cancelled():
        raise _SourceFolderOpenCancelled
    if not selected:
        if os.name != "nt":
            raise OSError("Windows 폴더 열기를 지원하지 않습니다.")
        # cidl=0 would open the parent and select this folder. An empty file
        # selection instead opens the folder's contents through the shell.
        os.startfile(str(folder))
        return

    shell, ole = _load_shell_libraries()
    _check_hresult(ole.CoInitializeEx(None, 2), "Windows Shell 초기화")
    owned: list[int] = []
    try:
        def parse(path: Path) -> int:
            if cancelled():
                raise _SourceFolderOpenCancelled
            pointer = ctypes.c_void_p()
            result = shell.SHParseDisplayName(str(path), None, ctypes.byref(pointer), 0, None)
            if pointer.value:
                owned.append(pointer.value)
            _check_hresult(result, "Windows 경로 확인")
            if not pointer.value:
                raise OSError("Windows 경로를 확인하지 못했습니다.")
            return pointer.value

        folder_pidl = parse(folder)
        children: list[int] = []
        for path in selected.values():
            file_pidl = parse(path)
            if not shell.ILIsParent(folder_pidl, file_pidl, True):
                raise OSError("PDF의 Windows 폴더 위치가 일치하지 않습니다.")
            child = shell.ILFindLastID(file_pidl)
            if not child:
                raise OSError("Windows PDF 항목을 확인하지 못했습니다.")
            children.append(child)
        child_array = (ctypes.c_void_p * len(children))(*children)
        if cancelled():
            raise _SourceFolderOpenCancelled
        # Each child is a borrowed relative pointer inside a file PIDL.
        # Retain all absolute owners and the array until the Shell call returns.
        _check_hresult(shell.SHOpenFolderAndSelectItems(folder_pidl, len(children), child_array, 0),
                       "PDF 선택하여 폴더 열기")
    finally:
        try:
            for pointer in reversed(owned):
                ole.CoTaskMemFree(ctypes.c_void_p(pointer))
        finally:
            # S_OK and S_FALSE both require a matching uninitialization.
            ole.CoUninitialize()


def open_source_folders(paths: Iterable[Path], *,
                        opener: Callable[[Path, tuple[Path, ...]], None] | None = None,
                        cancelled: Callable[[], bool] = lambda: False,
                        limit: int = MAX_SOURCE_FOLDERS_PER_REQUEST) -> SourceFolderOpenResult:
    targets = source_folder_targets(paths)
    count = max(0, int(limit))
    opened: list[Path] = []
    missing_files: list[Path] = []
    missing_folders: list[Path] = []
    failed: list[Path] = []
    stopped = False
    for folder, files in targets[:count]:
        if cancelled():
            stopped = True
            break
        try:
            if not folder.is_dir():
                missing_folders.append(folder)
                continue
            existing: list[Path] = []
            for path in files:
                if cancelled():
                    stopped = True
                    break
                if not path.is_file():
                    missing_files.append(path)
                else:
                    existing.append(path)
            if stopped or cancelled():
                stopped = True
                break
            if opener is None:
                open_windows_folder(folder, tuple(existing), cancelled=cancelled)
            else:
                opener(folder, tuple(existing))
        except _SourceFolderOpenCancelled:
            stopped = True
            break
        except OSError:
            failed.append(folder)
        else:
            opened.append(folder)
    return SourceFolderOpenResult(tuple(opened), tuple(missing_files), tuple(missing_folders),
                                  tuple(failed), max(0, len(targets) - count), stopped)
