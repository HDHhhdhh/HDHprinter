@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist "%~dp0unit_print_bootstrap.py" (
  echo 빠른 인쇄기 실행 파일 unit_print_bootstrap.py가 없습니다.
  echo 검증된 백업 또는 배포본에서 파일을 복원해 주세요.
  pause
  exit /b 1
)
set "VENV_PYW=%~dp0.venv\Scripts\pythonw.exe"
if exist "%VENV_PYW%" (
  start "" "%VENV_PYW%" "%~dp0unit_print_bootstrap.py"
  exit /b 0
)
set "PYW312=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if exist "%PYW312%" (
  start "" "%PYW312%" "%~dp0unit_print_bootstrap.py"
  exit /b 0
)
where pyw >nul 2>nul
if not errorlevel 1 (
  pyw -3.12 -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>nul
  if not errorlevel 1 (
    start "" pyw -3.12 "%~dp0unit_print_bootstrap.py"
    exit /b 0
  )
)
where pythonw >nul 2>nul
if not errorlevel 1 (
  pythonw -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>nul
  if not errorlevel 1 (
    start "" pythonw "%~dp0unit_print_bootstrap.py"
    exit /b 0
  )
)
echo 빠른 인쇄기에 필요한 Python 3.12 실행 파일을 찾지 못했습니다.
echo Python 3.12를 설치하거나 install_quick_printer.cmd를 실행해 주세요.
pause
exit /b 1
