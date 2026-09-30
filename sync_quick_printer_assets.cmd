@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

if not exist "quick_printer_asset_channel.local.json" (
  echo [오류] quick_printer_asset_channel.local.json 파일이 없습니다.
  echo quick_printer_asset_channel.example.json을 복사한 뒤 비공개 Drive 폴더 ID를 입력하세요.
  pause
  exit /b 1
)

"%PYTHON%" "scripts\quick_printer_asset_sync.py" pull ^
  --channel-config "quick_printer_asset_channel.local.json"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
  echo.
  echo [실패] 인쇄 자료 증분 동기화에 실패했습니다.
  pause
  exit /b %EXIT_CODE%
)

echo.
echo [완료] 새로 추가되거나 변경된 인쇄 자료만 반영했습니다.
pause
exit /b 0
