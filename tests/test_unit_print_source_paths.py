from __future__ import annotations

import ctypes
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from unit_print_manager import ExamPdf, PrintBlock, PrintManagerApp
from unit_print_source_paths import SourceFolderOpenResult, open_source_folders, open_windows_folder, source_folder_targets
import unit_print_source_paths as source_paths


class SourceFolderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def pdf(self, folder, filename):
        directory = self.root / folder
        directory.mkdir(exist_ok=True)
        path = directory / filename
        path.touch()
        return path

    def test_multiple_blocks_and_files_share_one_folder_open_in_first_appearance_order(self):
        first = self.pdf("한글 자료, ; &", "first.pdf")
        second = self.pdf("한글 자료, ; &", "second.pdf")
        third = self.pdf("other", "third.pdf")
        opener = Mock()
        result = open_source_folders((first, second, first, third), opener=opener)
        self.assertEqual((first.parent, third.parent), result.opened)
        self.assertEqual([(first.parent, (first, second)), (third.parent, (third,))],
                         [call.args for call in opener.call_args_list])
        self.assertEqual(2, len(source_folder_targets((first, second, first, third))[0][1]))

    def test_missing_file_still_opens_its_existing_folder_and_missing_folder_is_reported(self):
        present = self.pdf("existing", "present.pdf")
        absent = present.parent / "absent.pdf"
        missing_folder = self.root / "not-created" / "missing.pdf"
        opener = Mock()
        result = open_source_folders((absent, present, missing_folder), opener=opener)
        opener.assert_called_once_with(present.parent, (present,))
        self.assertEqual((absent,), result.missing_files)
        self.assertEqual((missing_folder.parent,), result.missing_folders)
        self.assertFalse(missing_folder.parent.exists())

    def test_all_missing_pdfs_still_open_the_existing_folder_without_selection(self):
        directory = self.root / "existing"
        directory.mkdir()
        absent = directory / "absent.pdf"
        opener = Mock()
        result = open_source_folders((absent,), opener=opener)
        opener.assert_called_once_with(directory, ())
        self.assertEqual((directory,), result.opened)
        self.assertEqual((absent,), result.missing_files)

    def test_at_most_ten_folders_open_with_remaining_count(self):
        paths = tuple(self.pdf(str(index), "sample.pdf") for index in range(12))
        opener = Mock()
        result = open_source_folders(paths, opener=opener)
        self.assertEqual(10, opener.call_count)
        self.assertEqual(2, result.skipped_folders)

    def test_launch_failure_does_not_block_other_existing_folders(self):
        first = self.pdf("first", "sample.pdf")
        second = self.pdf("second", "sample.pdf")
        opener = Mock(side_effect=(OSError("synthetic shell failure"), None))
        result = open_source_folders((first, second), opener=opener)
        self.assertEqual((first.parent,), result.failed_folders)
        self.assertEqual((second.parent,), result.opened)

    def test_cancel_prevents_later_folder_launches(self):
        paths = tuple(self.pdf(str(index), "sample.pdf") for index in range(3))
        cancel = threading.Event()
        opener = Mock(side_effect=lambda _folder, _files: cancel.set())
        result = open_source_folders(paths, opener=opener, cancelled=cancel.is_set)
        self.assertEqual(1, opener.call_count)
        self.assertTrue(result.cancelled)

    @unittest.skipUnless(os.name == "nt", "Windows shell path contract")
    def test_windows_shell_receives_one_literal_directory_path(self):
        folder = self.pdf("한글 자료, ; &", "sample.pdf").parent
        with patch("unit_print_source_paths.os.startfile") as startfile:
            with patch("unit_print_source_paths._load_shell_libraries") as loader:
                open_windows_folder(folder, ())
        loader.assert_not_called()
        startfile.assert_called_once_with(str(folder))


class FakeShellApis:
    """Allocated buffers stand in for owned PIDLs; no Windows API is invoked."""
    def __init__(self):
        self.buffers = []
        self.addresses = []
        self.freed = []
        self.events = []
        self.shell = SimpleNamespace(
            SHParseDisplayName=Mock(side_effect=self.parse),
            ILIsParent=Mock(return_value=True),
            ILFindLastID=Mock(side_effect=lambda address: address + 8),
            SHOpenFolderAndSelectItems=Mock(side_effect=self.open),
        )
        self.ole = SimpleNamespace(
            CoInitializeEx=Mock(return_value=0),
            CoTaskMemFree=Mock(side_effect=self.free),
            CoUninitialize=Mock(side_effect=lambda: self.events.append("uninitialize")),
        )

    def parse(self, _path, _bind, output, _attributes, _out_attributes):
        buffer = ctypes.create_string_buffer(32)
        address = ctypes.addressof(buffer)
        self.buffers.append(buffer)
        self.addresses.append(address)
        ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = address
        return 0

    def open(self, folder, count, children, flags):
        if self.freed:
            raise AssertionError("PIDLs were freed before the Shell call returned")
        if folder != self.addresses[0] or flags != 0:
            raise AssertionError("Invalid folder or flags")
        if list(children) != [address + 8 for address in self.addresses[1:]]:
            raise AssertionError("Selection must use borrowed relative child pointers")
        if count != len(self.addresses) - 1:
            raise AssertionError("Invalid child count")
        self.events.append("open")
        return 0

    def free(self, pointer):
        self.freed.append(pointer.value)
        self.events.append("free")


class WindowsPdfSelectionTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeShellApis()
        self.native_loader = source_paths._load_shell_libraries
        self.loader = patch("unit_print_source_paths._load_shell_libraries",
                            return_value=(self.api.shell, self.api.ole)).start()
        self.addCleanup(patch.stopall)
        self.startfile = patch("unit_print_source_paths.os.startfile", create=True).start()
        self.folder = Path(os.path.abspath("synthetic/한글 자료, ; &"))
        self.files = (self.folder / "첫 PDF, ; &.pdf", self.folder / "다음 PDF.pdf")

    def assert_released(self):
        self.assertEqual(self.api.addresses[::-1], self.api.freed)
        self.assertEqual(len(self.api.freed), len(set(self.api.freed)))
        self.api.ole.CoUninitialize.assert_called_once_with()
        self.assertEqual("uninitialize", self.api.events[-1])
        self.startfile.assert_not_called()

    def test_one_call_selects_multiple_literal_paths_and_frees_only_owned_pidls(self):
        open_windows_folder(self.folder, self.files + self.files[:1])
        self.assertEqual([str(self.folder), *(str(path) for path in self.files)],
                         [call.args[0] for call in self.api.shell.SHParseDisplayName.call_args_list])
        self.api.ole.CoInitializeEx.assert_called_once_with(None, 2)
        self.api.shell.SHOpenFolderAndSelectItems.assert_called_once()
        self.assertEqual([(self.api.addresses[0], address, True) for address in self.api.addresses[1:]],
                         [call.args for call in self.api.shell.ILIsParent.call_args_list])
        self.assert_released()

    def test_s_false_com_initialization_still_balances_uninitialize(self):
        self.api.ole.CoInitializeEx.return_value = 1
        open_windows_folder(self.folder, self.files[:1])
        self.assert_released()

    def test_com_failure_does_not_call_shell_or_uninitialize(self):
        self.api.ole.CoInitializeEx.return_value = 0x80010106  # RPC_E_CHANGED_MODE
        with self.assertRaises(OSError):
            open_windows_folder(self.folder, self.files)
        self.api.shell.SHParseDisplayName.assert_not_called()
        self.api.shell.SHOpenFolderAndSelectItems.assert_not_called()
        self.api.ole.CoTaskMemFree.assert_not_called()
        self.api.ole.CoUninitialize.assert_not_called()
        self.startfile.assert_not_called()

    def test_relative_and_different_parent_paths_are_rejected_before_shell_initialization(self):
        cases = ((Path("relative"), self.files),
                 (self.folder, (Path("relative.pdf"),)),
                 (self.folder, (self.folder.parent / "outside.pdf",)))
        for folder, files in cases:
            with self.subTest(folder=folder, files=files), self.assertRaises(OSError):
                open_windows_folder(folder, files)
        self.loader.assert_not_called()

    def test_failures_release_every_allocated_pidl_and_never_fall_back_to_folder_open(self):
        for failure in ("null_folder", "failed_file_parse", "wrong_parent", "null_child", "open_failure"):
            with self.subTest(failure=failure):
                self.api = FakeShellApis()
                self.loader.return_value = (self.api.shell, self.api.ole)
                if failure == "null_folder":
                    self.api.shell.SHParseDisplayName.side_effect = None
                    self.api.shell.SHParseDisplayName.return_value = 0
                elif failure == "failed_file_parse":
                    def failed_parse(*args):
                        self.api.parse(*args)
                        return 0x80004005 if len(self.api.addresses) == 2 else 0
                    self.api.shell.SHParseDisplayName.side_effect = failed_parse
                elif failure == "wrong_parent":
                    self.api.shell.ILIsParent.return_value = False
                elif failure == "null_child":
                    self.api.shell.ILFindLastID.side_effect = None
                    self.api.shell.ILFindLastID.return_value = None
                else:
                    self.api.shell.SHOpenFolderAndSelectItems.side_effect = None
                    self.api.shell.SHOpenFolderAndSelectItems.return_value = 0x80004005
                with self.assertRaises(OSError):
                    open_windows_folder(self.folder, self.files)
                self.assert_released()

    def test_cancel_during_pidl_preparation_prevents_shell_launch_and_releases_memory(self):
        cancel = threading.Event()
        def parse_and_cancel(*args):
            result = self.api.parse(*args)
            if len(self.api.addresses) == 3:
                cancel.set()
            return result
        self.api.shell.SHParseDisplayName.side_effect = parse_and_cancel
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            files = (folder / "first.pdf", folder / "second.pdf")
            for path in files:
                path.touch()
            result = open_source_folders(files, cancelled=cancel.is_set)
        self.assertTrue(result.cancelled)
        self.assertEqual((), result.opened)
        self.assertEqual((), result.failed_folders)
        self.api.shell.SHOpenFolderAndSelectItems.assert_not_called()
        self.assert_released()

    def test_selection_api_failure_is_reported_as_failed_folder(self):
        self.api.shell.SHOpenFolderAndSelectItems.side_effect = None
        self.api.shell.SHOpenFolderAndSelectItems.return_value = 0x80004005
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "sample.pdf"
            path.touch()
            result = open_source_folders((path,))
        self.assertEqual((path.parent,), result.failed_folders)
        self.assertEqual((), result.opened)
        self.assert_released()

    @unittest.skipUnless(os.name == "nt", "Windows ctypes ABI")
    def test_windows_api_bindings_preserve_pointer_width_and_hresult_contract(self):
        with patch("unit_print_source_paths.ctypes.WinDLL", side_effect=(self.api.shell, self.api.ole)) as dll:
            shell, ole = self.native_loader()
        self.assertEqual([("shell32",), ("ole32",)], [call.args for call in dll.call_args_list])
        self.assertEqual((ctypes.c_wchar_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                          ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)), shell.SHParseDisplayName.argtypes)
        self.assertIs(ctypes.c_long, shell.SHParseDisplayName.restype)
        self.assertEqual((ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p), ctypes.c_ulong),
                         shell.SHOpenFolderAndSelectItems.argtypes)
        self.assertIs(ctypes.c_long, shell.SHOpenFolderAndSelectItems.restype)
        self.assertIs(ctypes.c_void_p, shell.ILFindLastID.restype)
        self.assertEqual((ctypes.c_void_p,), ole.CoTaskMemFree.argtypes)
        self.assertIsNone(ole.CoTaskMemFree.restype)
        self.assertIsNone(ole.CoUninitialize.restype)
        self.assertEqual(4, ctypes.sizeof(ctypes.c_long))
        self.assertEqual(ctypes.sizeof(ctypes.c_void_p), ctypes.sizeof((ctypes.c_void_p * 1)()))
        shell.SHParseDisplayName.assert_not_called()
        shell.SHOpenFolderAndSelectItems.assert_not_called()
        ole.CoInitializeEx.assert_not_called()


class SourceFolderUiTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        self.warning = patch("unit_print_manager.messagebox.showwarning").start()
        self.info = patch("unit_print_manager.messagebox.showinfo").start()
        self.app = PrintManagerApp.__new__(PrintManagerApp)
        self.app.root = Mock()
        self.app.root.after.return_value = "source-poll"
        self.app.is_closing = False
        self.app.is_printing = True
        paths = [Path(os.path.abspath(f"synthetic/{index}/sample.pdf")) for index in range(3)]
        self.app.blocks = [PrintBlock("자료", "", "", 1, 1, 1, [ExamPdf(1, "sample", path)]) for path in paths]
        self.app.selected_block_indices = {2, 0}
        self.app.block_status_by_id = {id(block): "진행 중" for block in self.app.blocks}
        self.app.block_detail_by_id = {id(block): "same" for block in self.app.blocks}
        self.app.print_pending_jobs = [object()]
        self.app.retry_batch = object()
        self.app.history_run_id = "unchanged-run"
        self.app.app_status_var = object()
        self.app.current_work_var = object()
        self.app.latest_resource_estimate = object()
        self.work = []
        self.app.launch_source_paths_worker = self.work.append

    def test_snapshot_allows_printing_without_mutating_state_and_blocks_duplicate_requests(self):
        before = (dict(self.app.block_status_by_id), dict(self.app.block_detail_by_id),
                  list(self.app.print_pending_jobs), self.app.retry_batch, self.app.app_status_var,
                  self.app.current_work_var, self.app.latest_resource_estimate)
        expected = (self.app.blocks[0].files[0].path, self.app.blocks[2].files[0].path)
        opener = patch("unit_print_manager.open_source_folders", return_value=SourceFolderOpenResult(opened=(expected[0].parent,))).start()
        self.app.open_selected_block_source_folders()
        self.app.open_selected_block_source_folders()
        self.assertEqual(1, len(self.work))
        self.app.blocks[0].files.clear()
        self.work[0]()
        self.app.poll_source_paths_open_results()
        self.assertEqual(expected, opener.call_args.args[0])
        self.assertFalse(self.app.source_paths_open_pending)
        self.assertEqual(before, (dict(self.app.block_status_by_id), dict(self.app.block_detail_by_id),
                                list(self.app.print_pending_jobs), self.app.retry_batch, self.app.app_status_var,
                                self.app.current_work_var, self.app.latest_resource_estimate))
        self.warning.assert_not_called()
        self.info.assert_not_called()

    def test_worker_exception_publishes_completion_and_releases_pending_poll(self):
        patch("unit_print_manager.open_source_folders", side_effect=RuntimeError("synthetic failure")).start()
        self.app.open_selected_block_source_folders()
        self.work[0]()
        self.app.root.after.reset_mock()
        self.app.poll_source_paths_open_results()
        self.assertFalse(self.app.source_paths_open_pending)
        self.assertIsNone(self.app.source_paths_open_request)
        self.app.root.after.assert_not_called()
        self.warning.assert_called_once()

    def test_close_cancels_pending_open_and_ignores_late_completion(self):
        patch("unit_print_manager.open_source_folders", return_value=SourceFolderOpenResult(failed_folders=(Path("missing"),))).start()
        self.app.open_selected_block_source_folders()
        request = self.app.source_paths_open_request
        self.app.cancel_source_paths_open()
        self.assertTrue(request[1].is_set())
        self.app.root.after_cancel.assert_called_once_with("source-poll")
        self.work[0]()
        self.app.poll_source_paths_open_results()
        self.warning.assert_not_called()

    def test_confirmed_close_cancels_folder_request_before_waiting_for_physical_worker(self):
        opener = patch("unit_print_manager.open_source_folders",
                       side_effect=lambda _paths, cancelled: SourceFolderOpenResult(cancelled=cancelled())).start()
        self.app.print_cancel_event = threading.Event()
        self.app.cancel_pending_block_preparations = Mock()
        self.app.set_status = Mock()
        self.app.set_current_work = Mock()
        self.app.refresh_print_action_buttons = Mock()
        self.app.open_selected_block_source_folders()
        request = self.app.source_paths_open_request
        with patch("unit_print_manager.messagebox.askyesno", return_value=True):
            self.app.on_close()
        self.assertTrue(self.app.is_closing)
        self.assertTrue(request[1].is_set())
        self.assertTrue(self.app.print_cancel_event.is_set())
        self.assertIsNone(self.app.source_paths_open_request)
        self.work[0]()
        self.assertTrue(opener.call_args.kwargs["cancelled"]())
        self.app.poll_source_paths_open_results()
        self.warning.assert_not_called()

    def test_same_pdf_set_has_short_repeat_guard_and_warning_counts_skipped_folders(self):
        response = SourceFolderOpenResult(opened=(Path("a"),), skipped_folders=2)
        patch("unit_print_manager.open_source_folders", return_value=response).start()
        self.app.open_selected_block_source_folders()
        self.work[0]()
        self.app.poll_source_paths_open_results()
        self.app.open_selected_block_source_folders()
        self.assertEqual(1, len(self.work))
        self.assertIn("나머지 2개", self.warning.call_args.args[1])

    def test_different_pdf_selection_in_same_folder_is_not_dropped_by_repeat_guard(self):
        first = self.app.blocks[0].files[0].path
        second = first.parent / "second.pdf"
        self.app.blocks[1].files = [ExamPdf(2, "second", second)]
        self.app.selected_block_indices = {0}
        opener = patch("unit_print_manager.open_source_folders",
                       return_value=SourceFolderOpenResult(opened=(first.parent,))).start()
        with patch("unit_print_manager.time.monotonic", side_effect=(10.0, 10.5)):
            self.app.open_selected_block_source_folders()
            self.work[0]()
            self.app.poll_source_paths_open_results()
            self.app.selected_block_indices = {1}
            self.app.open_selected_block_source_folders()
        self.assertEqual(2, len(self.work))
        self.work[1]()
        self.app.poll_source_paths_open_results()
        self.assertEqual([(first,), (second,)], [call.args[0] for call in opener.call_args_list])

    def test_context_menu_read_only_command_remains_available_during_printing(self):
        menu = Mock()
        self.app.queue_canvas = Mock()
        self.app.selected_block_indices = {0}
        self.app.block_clipboard = ()
        with patch("unit_print_manager.tk.Menu", return_value=menu):
            self.app.post_queue_context_menu(1, 2)
        commands = {call.kwargs.get("label"): call.kwargs for call in menu.add_command.call_args_list}
        self.assertEqual("normal", commands["원본 경로 열기"]["state"])
        self.assertEqual(self.app.open_selected_block_source_folders, commands["원본 경로 열기"]["command"])


if __name__ == "__main__":
    unittest.main()
