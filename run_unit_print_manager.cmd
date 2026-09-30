@echo off
setlocal
cd /d "%~dp0"
set "VENV_PYW=%~dp0.venv\Scripts\pythonw.exe"
if exist "%VENV_PYW%" (
  start "" "%VENV_PYW%" "%~dp0unit_print_manager.py"
  exit /b 0
)
set "PYW312=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if exist "%PYW312%" (
  start "" "%PYW312%" "%~dp0unit_print_manager.py"
  exit /b 0
)
where pythonw >nul 2>nul
if not errorlevel 1 (
  start "" pythonw "%~dp0unit_print_manager.py"
  exit /b 0
)
start "" pyw -3 "%~dp0unit_print_manager.py"
exit /b 0
