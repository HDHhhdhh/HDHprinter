from __future__ import annotations

import ast
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import unit_print_bootstrap as bootstrap


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Lease:
    def __init__(self, acquired=True):
        self.acquired = acquired
        self.close = Mock()
        self.acquire = Mock(return_value=acquired)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp)
        self.notify = Mock()

    def populate_files(self):
        for name in bootstrap.STARTUP_REQUIRED_FILES:
            (self.root / name).write_text("", encoding="utf-8")

    def launch(self, *, lease=None, importer=None, validator=None):
        lease = lease if lease is not None else Lease()
        return bootstrap.main(
            app_root=self.root, instance_factory=lambda: lease,
            importer=importer or Mock(return_value=types.SimpleNamespace(run_application=Mock())),
            validator=validator or (lambda root: None), notify=self.notify,
        )

    def logs(self):
        return list((self.root / "output/unit_print_manager/startup_logs").glob("*.log"))

    def test_validation_identifies_missing_module_before_import_without_reading_pdfs(self):
        self.populate_files()
        (self.root / "unit_print_paths.py").unlink()
        with patch.object(Path, "read_bytes", side_effect=AssertionError("PDF hashing at startup")):
            with self.assertRaises(bootstrap.StartupValidationError) as caught:
                bootstrap.validate_startup(self.root, find_spec=lambda package: object())
        self.assertEqual(("unit_print_paths.py",), caught.exception.files)

    def test_missing_mutex_module_is_diagnosed_before_attempting_native_lock(self):
        with patch.dict(sys.modules, {"unit_print_instance": None}):
            result = bootstrap.main(app_root=self.root, notify=self.notify)
        self.assertEqual(1, result)
        self.assertIn("unit_print_instance.py", self.notify.call_args.args[0])
        self.assertIn("Missing app files: unit_print_instance.py", self.logs()[0].read_text(encoding="utf-8"))

    def test_dependency_missing_is_actionable_and_prevents_manager_import(self):
        self.populate_files()
        importer = Mock()
        result = self.launch(
            importer=importer,
            validator=lambda root: bootstrap.validate_startup(
                root, find_spec=lambda package: None if package == "pypdf" else object()
            ),
        )
        self.assertEqual(1, result)
        importer.assert_not_called()
        self.assertIn("pypdf", self.notify.call_args.args[0])
        self.assertIn("install_quick_printer.cmd", self.notify.call_args.args[0])
        self.assertEqual(1, len(self.logs()))

    def test_catalog_folder_scan_fallback_remains_available_when_metadata_is_missing(self):
        self.populate_files()
        self.assertFalse((self.root / "unit_print_catalog.json").exists())
        bootstrap.validate_startup(self.root, find_spec=lambda package: object())

    def test_only_supported_python_312_environment_passes_validation(self):
        self.populate_files()
        for version in ((3, 11, 12), (3, 13, 2), (3, 14, 0)):
            with self.subTest(version=version), patch.object(bootstrap.sys, "version_info", version):
                with self.assertRaises(bootstrap.StartupValidationError) as caught:
                    bootstrap.validate_startup(self.root, find_spec=lambda package: object())
                self.assertTrue(caught.exception.python_unsupported)
        with patch.object(bootstrap.sys, "version_info", (3, 12, 10)):
            bootstrap.validate_startup(self.root, find_spec=lambda package: object())

    def test_missing_local_file_error_names_only_missing_known_files(self):
        self.populate_files()
        (self.root / "unit_print_pdf_printing.py").unlink()
        importer = Mock()
        result = self.launch(
            importer=importer,
            validator=lambda root: bootstrap.validate_startup(root, find_spec=lambda package: object()),
        )
        self.assertEqual(1, result)
        importer.assert_not_called()
        self.assertIn("unit_print_pdf_printing.py", self.notify.call_args.args[0])
        self.assertIn("복원", self.notify.call_args.args[0])

    def test_all_required_files_missing_keeps_notice_bounded_and_logs_complete_list(self):
        importer = Mock()
        result = self.launch(
            importer=importer,
            validator=lambda root: bootstrap.validate_startup(root, find_spec=lambda package: object()),
        )
        self.assertEqual(1, result)
        importer.assert_not_called()
        message = self.notify.call_args.args[0]
        self.assertIn(f"파일 {len(bootstrap.STARTUP_REQUIRED_FILES)}개", message)
        self.assertIn(f"나머지 {len(bootstrap.STARTUP_REQUIRED_FILES) - 8}개", message)
        for name in bootstrap.STARTUP_REQUIRED_FILES[:8]:
            self.assertIn(name, message)
        for name in bootstrap.STARTUP_REQUIRED_FILES[8:]:
            self.assertNotIn(name, message)
        self.assertLess(len(message.splitlines()), 20)
        log = self.logs()[0].read_text(encoding="utf-8")
        for name in bootstrap.STARTUP_REQUIRED_FILES:
            self.assertIn(name, log)

    def test_duplicate_does_not_import_validate_or_create_gui(self):
        lease, importer, validator = Lease(False), Mock(), Mock()
        self.assertEqual(0, self.launch(lease=lease, importer=importer, validator=validator))
        importer.assert_not_called()
        validator.assert_not_called()
        lease.close.assert_called_once()
        self.assertIn("이미 실행 중", self.notify.call_args.args[0])
        self.assertEqual([], self.logs())

    def test_import_failure_is_logged_utf8_and_releases_lifetime_lease(self):
        lease = Lease()
        error = ModuleNotFoundError("secret-PIN-2468 student-name")
        self.assertEqual(1, self.launch(lease=lease, importer=Mock(side_effect=error)))
        lease.close.assert_called_once()
        text = self.logs()[0].read_text(encoding="utf-8")
        self.assertIn("빠른 인쇄기 실행 진단", text)
        self.assertIn("Traceback", text)
        self.assertIn("ModuleNotFoundError", text)
        self.assertNotIn("secret-PIN", text)
        self.assertNotIn("student-name", text)
        self.assertNotIn("secret-PIN", self.notify.call_args.args[0])

    def test_syntax_failure_during_hidden_launcher_import_has_location_without_source_text(self):
        error = SyntaxError("private", ("unit_print_manager.py", 23, 4, "PIN='9999'"))
        self.assertEqual(1, self.launch(importer=Mock(side_effect=error)))
        text = self.logs()[0].read_text(encoding="utf-8")
        self.assertIn("unit_print_manager.py:23:4", text)
        self.assertNotIn("9999", text)

    def test_application_constructor_failure_diagnosed_and_hook_restored(self):
        lease = Lease()
        old_hook = threading.excepthook
        app = Mock(side_effect=RuntimeError("PIN in app config"))
        self.assertEqual(1, self.launch(lease=lease, importer=Mock(return_value=types.SimpleNamespace(run_application=app))))
        app.assert_called_once()
        self.assertIs(threading.excepthook, old_hook)
        lease.close.assert_called_once()
        self.assertIn("Phase: application", self.logs()[0].read_text(encoding="utf-8"))

    def test_later_callback_failure_writes_log_and_keeps_main_lease_until_loop_exit(self):
        lease = Lease()

        def app(*, error_handler):
            lease.close.assert_not_called()
            try:
                raise ValueError("private student data")
            except ValueError as error:
                error_handler(type(error), error, error.__traceback__)
            lease.close.assert_not_called()
            self.assertIn("처리 오류", self.notify.call_args.args[0])

        self.assertEqual(0, self.launch(lease=lease, importer=Mock(return_value=types.SimpleNamespace(run_application=app))))
        lease.close.assert_called_once()
        text = self.logs()[0].read_text(encoding="utf-8")
        self.assertIn("Phase: callback", text)
        self.assertNotIn("private student data", text)

    def test_thread_exception_hook_logs_then_restores_after_run(self):
        old_hook = threading.excepthook

        def app(*, error_handler):
            error = RuntimeError("PIN=9999")
            threading.excepthook(types.SimpleNamespace(exc_value=error, exc_traceback=None))

        self.assertEqual(0, self.launch(importer=Mock(return_value=types.SimpleNamespace(run_application=app))))
        self.assertIs(old_hook, threading.excepthook)
        text = self.logs()[0].read_text(encoding="utf-8")
        self.assertIn("Phase: thread", text)
        self.assertNotIn("PIN=9999", text)

    def test_recursive_entry_does_not_acquire_second_reentrant_windows_mutex_or_gui(self):
        second_factory, second_import = Mock(), Mock()

        def app(*, error_handler):
            result = bootstrap.main(
                app_root=self.root, instance_factory=second_factory, importer=second_import,
                validator=Mock(), notify=self.notify,
            )
            self.assertEqual(0, result)

        first_factory = Mock(return_value=Lease())
        self.assertEqual(0, bootstrap.main(
            app_root=self.root, instance_factory=first_factory,
            importer=Mock(return_value=types.SimpleNamespace(run_application=app)),
            validator=Mock(), notify=self.notify,
        ))
        first_factory.assert_called_once()
        second_factory.assert_not_called()
        second_import.assert_not_called()

    def test_diagnostics_fallback_from_read_only_install_without_config_reads(self):
        preferred = self.root / "readonly"
        fallback = self.root / "localappdata"
        original_mkdir = Path.mkdir

        def mkdir(path, *args, **kwargs):
            if preferred in path.parents:
                raise PermissionError("read only")
            return original_mkdir(path, *args, **kwargs)

        with patch.object(Path, "mkdir", mkdir), patch.dict("os.environ", {"LOCALAPPDATA": str(fallback)}):
            log = bootstrap.write_diagnostic(preferred, ValueError("confidential-fixture-8246"), "import")
        self.assertIsNotNone(log)
        self.assertIn(fallback, log.parents)
        self.assertNotIn("confidential-fixture-8246", log.read_text(encoding="utf-8"))

    def test_safe_traceback_omits_exception_chain_messages_and_frame_locals(self):
        try:
            pin = "department-PIN-1212"
            try:
                raise ValueError(pin)
            except ValueError as inner:
                raise RuntimeError("student-name") from inner
        except RuntimeError as outer:
            text = bootstrap.safe_traceback(outer)
        self.assertIn("ValueError", text)
        self.assertIn("RuntimeError", text)
        self.assertIn("test_safe_traceback", text)
        self.assertNotIn("department-PIN", text)
        self.assertNotIn("student-name", text)

    def test_error_notification_failure_does_not_leak_entry_lock(self):
        notify = Mock(side_effect=OSError("UI unavailable"))
        with patch.object(sys, "stderr", None):
            result = bootstrap.main(
                app_root=self.root, instance_factory=lambda: Lease(),
                validator=Mock(side_effect=ValueError("private")), notify=notify,
            )
        self.assertEqual(1, result)
        self.assertEqual(0, self.launch())

    def test_direct_entry_delegates_before_third_party_imports_and_uses_one_bootstrap(self):
        source = (PROJECT_ROOT / "unit_print_manager.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        early_guard = tree.body[1]
        self.assertIsInstance(early_guard, ast.If)
        stub_main = Mock(return_value=0)
        compiled = compile(ast.Module(body=[early_guard], type_ignores=[]), "direct_entry", "exec")
        with patch.dict(sys.modules, {"unit_print_bootstrap": types.SimpleNamespace(main=stub_main)}):
            with self.assertRaises(SystemExit) as caught:
                exec(compiled, {"__name__": "__main__"})
        self.assertEqual(0, caught.exception.code)
        stub_main.assert_called_once_with()
        stub_main.reset_mock()
        exec(compiled, {"__name__": "unit_print_manager"})
        stub_main.assert_not_called()

    def test_launchers_route_to_bootstrap_and_handle_missing_bootstrap(self):
        cmd = (PROJECT_ROOT / "run_unit_print_manager.cmd").read_text(encoding="utf-8")
        vbs = (PROJECT_ROOT / "run_unit_print_manager_hidden.vbs").read_text(encoding="utf-16")
        self.assertNotIn('"%~dp0unit_print_manager.py"', cmd)
        self.assertNotIn('appDir & "\\unit_print_manager.py"', vbs)
        self.assertIn("unit_print_bootstrap.py", cmd)
        self.assertIn("unit_print_bootstrap.py", vbs)
        self.assertIn("If Not fso.FileExists(appPath)", vbs)
        self.assertIn("MsgBox", vbs)
        self.assertIn("if not exist", cmd)
        self.assertIn("Python312", cmd)
        self.assertIn("Python312", vbs)
        self.assertIn("shell.Run command, 0, False", vbs)

    def test_application_initialization_failure_destroys_partial_root(self):
        tree = ast.parse((PROJECT_ROOT / "unit_print_manager.py").read_text(encoding="utf-8"))
        run = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run_application")
        root = Mock()
        root.winfo_exists.return_value = True
        init = Mock(side_effect=ValueError("constructor failed"))
        namespace = {
            "set_windows_app_user_model_id": Mock(), "set_windows_dpi_awareness": Mock(),
            "TkinterDnD": None, "Tk": Mock(return_value=root), "PrintManagerApp": init,
            "tk": types.SimpleNamespace(TclError=RuntimeError),
        }
        exec(compile(ast.Module(body=[run], type_ignores=[]), "entry_function", "exec"), namespace)
        handler = Mock()
        with self.assertRaises(ValueError):
            namespace["run_application"](error_handler=handler)
        self.assertIs(root.report_callback_exception, handler)
        root.mainloop.assert_not_called()
        root.destroy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
