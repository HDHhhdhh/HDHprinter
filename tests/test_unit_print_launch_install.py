from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@unittest.skipUnless(os.name == "nt", "Windows launchers")
class LauncherControlFlowTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def run_cmd(self, mode):
        # Stub only external interpreter/process calls; cmd executes the real
        # launcher's branch ordering and errorlevel handling.
        source = (PROJECT / "run_unit_print_manager.cmd").read_text(encoding="utf-8")
        helper = self.root / "interpreter_stub.cmd"
        helper.write_text(
            '@echo off\n'
            'if "%1"=="exists_pyw" exit /b 0\n'
            'if "%1"=="exists_pythonw" exit /b 0\n'
            'if "%1"=="check_pyw" (\n'
            '  if "%TEST_PRINT_LAUNCH_MODE%"=="pyw" exit /b 0\n'
            '  exit /b 1\n'
            ')\n'
            'if "%1"=="check_pythonw" (\n'
            '  if "%TEST_PRINT_LAUNCH_MODE%"=="pythonw" exit /b 0\n'
            '  exit /b 1\n'
            ')\n'
            'echo launch:%1\nexit /b 0\n',
            encoding="ascii",
        )
        boundary = f'call "{helper}"'
        source = source.replace("where pyw >nul 2>nul", boundary + " exists_pyw")
        source = source.replace("where pythonw >nul 2>nul", boundary + " exists_pythonw")
        predicate = '"import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)"'
        source = source.replace("pyw -3.12 -c " + predicate + " >nul 2>nul", boundary + " check_pyw")
        source = source.replace("pythonw -c " + predicate + " >nul 2>nul", boundary + " check_pythonw")
        source = source.replace('start "" "%VENV_PYW%"', boundary + " venv")
        source = source.replace('start "" "%PYW312%"', boundary + " local")
        source = source.replace('start "" pyw -3.12', boundary + " pyw")
        source = source.replace('start "" pythonw', boundary + " pythonw")
        source = source.replace("\npause\n", "\necho notice:missing_python312\n")
        target = self.root / "launcher_test.cmd"
        target.write_text(source, encoding="utf-8")
        (self.root / "unit_print_bootstrap.py").write_text("", encoding="ascii")
        local = self.root / "localappdata"
        if mode == "venv":
            candidate = self.root / ".venv/Scripts/pythonw.exe"
        elif mode == "local":
            candidate = local / "Programs/Python/Python312/pythonw.exe"
        else:
            candidate = None
        if candidate is not None:
            candidate.parent.mkdir(parents=True)
            candidate.write_bytes(b"test stub; never executed")
        return subprocess.run(
            [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(target)],
            env=dict(os.environ, LOCALAPPDATA=str(local), TEST_PRINT_LAUNCH_MODE=mode),
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=15, creationflags=NO_WINDOW,
        )

    def run_vbs(self, mode):
        # Execute VBScript itself with app-owned COM boundaries replaced; no
        # Python GUI, MsgBox, or real process launch can occur.
        source = (PROJECT / "run_unit_print_manager_hidden.vbs").read_text(encoding="utf-16")
        source = source.replace('Set shell = CreateObject("WScript.Shell")', "Set shell = New FakeShell")
        source = source.replace('Set fso = CreateObject("Scripting.FileSystemObject")', "Set fso = New FakeFSO")
        source = source.replace("MsgBox ", "TestNotice ")
        source = source.replace("Option Explicit", 'Option Explicit\nDim testMode\ntestMode = "' + mode + '"', 1)
        source += r'''

Sub TestNotice(message, icon, title)
  WScript.Echo "notice:missing_python312"
End Sub

Class FakeFSO
  Function GetParentFolderName(path)
    GetParentFolderName = "APP"
  End Function
  Function FileExists(path)
    If InStr(path, "unit_print_bootstrap.py") > 0 Then
      FileExists = True
    ElseIf InStr(path, ".venv") > 0 Then
      FileExists = (testMode = "venv")
    ElseIf InStr(path, "Python312") > 0 Then
      FileExists = (testMode = "local")
    Else
      FileExists = False
    End If
  End Function
End Class

Class FakeShell
  Public CurrentDirectory
  Function ExpandEnvironmentStrings(value)
    ExpandEnvironmentStrings = "LOCAL"
  End Function
  Function Run(command, style, wait)
    If wait Then
      If InStr(command, "pyw -3.12") = 1 Then
        If testMode = "missing-pyw" Then Err.Raise 53
        If testMode = "pyw" Then Run = 0 Else Run = 1
      ElseIf InStr(command, "pythonw -c") = 1 Then
        If testMode = "pythonw" Or testMode = "missing-pyw" Then Run = 0 Else Run = 1
      Else
        Err.Raise 5
      End If
    Else
      If InStr(command, ".venv") > 0 Then
        WScript.Echo "launch:venv"
      ElseIf InStr(command, "Python312") > 0 Then
        WScript.Echo "launch:local"
      ElseIf InStr(command, "pyw -3.12") = 1 Then
        WScript.Echo "launch:pyw"
      Else
        WScript.Echo "launch:pythonw"
      End If
      Run = 0
    End If
  End Function
End Class
'''
        target = self.root / "launcher_test.vbs"
        target.write_text(source, encoding="utf-16")
        return subprocess.run(
            ["cscript.exe", "//Nologo", str(target)],
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=15, creationflags=NO_WINDOW,
        )

    def test_cmd_uses_preferred_supported_interpreter_and_rejects_wrong_versions(self):
        for mode in ("venv", "local", "pyw", "pythonw", "none"):
            with self.subTest(mode=mode):
                # Separate installations prevent a prior scenario's file from
                # affecting the actual if-exist branches.
                with tempfile.TemporaryDirectory() as directory:
                    self.root = Path(directory)
                    result = self.run_cmd(mode)
                if mode == "none":
                    self.assertEqual(1, result.returncode, result.stdout + result.stderr)
                    self.assertNotIn("launch:", result.stdout)
                    self.assertIn("notice:missing_python312", result.stdout)
                else:
                    self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                    self.assertIn("launch:" + mode, result.stdout)
                    self.assertEqual(1, result.stdout.count("launch:"))

    def test_vbs_uses_supported_interpreter_and_clears_a_failed_pyw_probe(self):
        for mode in ("venv", "local", "pyw", "pythonw", "missing-pyw", "none"):
            with self.subTest(mode=mode):
                result = self.run_vbs(mode)
                if mode == "none":
                    self.assertEqual(1, result.returncode, result.stdout + result.stderr)
                    self.assertNotIn("launch:", result.stdout)
                    self.assertIn("notice:missing_python312", result.stdout)
                else:
                    expected = "pythonw" if mode == "missing-pyw" else mode
                    self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                    self.assertIn("launch:" + expected, result.stdout)
                    self.assertEqual(1, result.stdout.count("launch:"))


@unittest.skipUnless(os.name == "nt", "Windows installer")
class InstallerFailureBoundaryTests(unittest.TestCase):
    def run_installer(self, failure):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = (PROJECT / "scripts/install_quick_printer.ps1").read_text(encoding="utf-8")
            literal = str(root).replace("'", "''")
            source = source.replace("$appRoot = Split-Path -Parent $PSScriptRoot", "$appRoot = '" + literal + "'")
            source = source.replace('$venvPython = Join-Path $appRoot ".venv\\Scripts\\python.exe"', '$venvPython = "TestPython"')
            source = source.replace('[Console]::OutputEncoding = [Text.Encoding]::UTF8', "")
            installer = root / "installer.ps1"
            installer.write_text(source, encoding="utf-8-sig")
            harness = root / "harness.ps1"
            harness.write_text(
                r'''
function Test-Path {
  param([string]$LiteralPath)
  if ($LiteralPath -eq "TestPython") { return $true }
  return Microsoft.PowerShell.Management\Test-Path -LiteralPath $LiteralPath
}
function TestPython {
  if ($args -contains "-c") { $global:LASTEXITCODE = 0; return }
  if ($args -contains "pip") {
    Write-Output "boundary:pip"
    $global:LASTEXITCODE = if ($env:TEST_PRINT_INSTALL_FAILURE -eq "pip") { 17 } else { 0 }
    return
  }
  if ([string]$args[0] -like "*verify_quick_printer_environment.py") {
    Write-Output "boundary:environment"
    $global:LASTEXITCODE = if ($env:TEST_PRINT_INSTALL_FAILURE -eq "environment") { 19 } else { 0 }
    return
  }
  throw "unexpected external Python operation"
}
function Write-Host {
  if ([string]$args[0] -like "[[]3/9[]]*") {
    Write-Output "boundary:verified-next-stage"
    throw "TEST STOP BEFORE APP INSTALL OPERATIONS"
  }
}
try {
  . 'INSTALLER_LITERAL'
} catch {
  Write-Output ("stopped:" + $_.Exception.Message)
  exit 23
}
'''.replace("INSTALLER_LITERAL", str(installer).replace("'", "''")),
                encoding="utf-8-sig",
            )
            return subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
                env=dict(os.environ, TEST_PRINT_INSTALL_FAILURE=failure),
                capture_output=True, encoding="utf-8", errors="replace",
                timeout=20, creationflags=NO_WINDOW,
            )

    def test_pip_failure_stops_before_environment_or_installer_operations(self):
        result = self.run_installer("pip")
        self.assertEqual(23, result.returncode, result.stdout + result.stderr)
        self.assertIn("boundary:pip", result.stdout)
        self.assertNotIn("boundary:environment", result.stdout)
        self.assertNotIn("boundary:verified-next-stage", result.stdout)

    def test_environment_mismatch_stops_before_installer_operations(self):
        result = self.run_installer("environment")
        self.assertEqual(23, result.returncode, result.stdout + result.stderr)
        self.assertIn("boundary:environment", result.stdout)
        self.assertNotIn("boundary:verified-next-stage", result.stdout)

    def test_successful_pip_and_pins_are_required_before_next_stage(self):
        result = self.run_installer("none")
        self.assertEqual(23, result.returncode, result.stdout + result.stderr)
        self.assertIn("boundary:verified-next-stage", result.stdout)
        self.assertLess(result.stdout.index("boundary:pip"), result.stdout.index("boundary:environment"))
        self.assertLess(result.stdout.index("boundary:environment"), result.stdout.index("boundary:verified-next-stage"))


if __name__ == "__main__":
    unittest.main()
