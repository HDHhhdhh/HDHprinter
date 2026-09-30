[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$appRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $appRoot ".venv\Scripts\python.exe"

function Test-PythonCommand {
    param([string]$Command, [string[]]$Prefix = @())
    try {
        & $Command @Prefix -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function New-AppVenv {
    $python312 = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
    if ((Test-Path -LiteralPath $python312) -and (Test-PythonCommand $python312)) {
        & $python312 -m venv (Join-Path $appRoot ".venv")
        return
    }
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python -and (Test-PythonCommand $python.Source)) {
        & $python.Source -m venv (Join-Path $appRoot ".venv")
        return
    }
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py -and (Test-PythonCommand $py.Source @("-3"))) {
        & $py.Source -3 -m venv (Join-Path $appRoot ".venv")
        return
    }
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "Python 3.11+과 winget을 찾지 못했습니다. Python 3.12를 먼저 설치해 주세요."
    }
    & $winget.Source install --id Python.Python.3.12 --exact --accept-package-agreements --accept-source-agreements --silent
    if (-not (Test-Path -LiteralPath $python312)) {
        throw "Python 설치 후 실행 파일을 찾지 못했습니다. 새 터미널에서 설치를 다시 실행해 주세요."
    }
    & $python312 -m venv (Join-Path $appRoot ".venv")
}

function Find-RcloneExecutable {
    $command = Get-Command rclone.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    $wingetRoot = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    if (Test-Path -LiteralPath $wingetRoot) {
        $candidate = Get-ChildItem -LiteralPath $wingetRoot -Filter rclone.exe -File -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -like "*Rclone.Rclone*" } |
            Select-Object -First 1
        if ($candidate) {
            return $candidate.FullName
        }
    }
    return $null
}

Set-Location -LiteralPath $appRoot
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "[1/9] Python 가상환경을 준비합니다."
    New-AppVenv
}

Write-Host "[2/9] 앱 패키지를 설치합니다."
& $venvPython -m pip install --disable-pip-version-check -r (Join-Path $appRoot "unit_print_manager_requirements.txt")

Write-Host "[3/9] SumatraPDF를 확인합니다."
$sumatraCandidates = @(
    (Join-Path $env:LOCALAPPDATA "SumatraPDF\SumatraPDF.exe"),
    (Join-Path $env:ProgramFiles "SumatraPDF\SumatraPDF.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "SumatraPDF\SumatraPDF.exe")
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
if (-not $sumatraCandidates) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "SumatraPDF가 없고 winget도 없습니다. SumatraPDF를 설치한 뒤 다시 실행해 주세요."
    }
    & $winget.Source install --id SumatraPDF.SumatraPDF --exact --accept-package-agreements --accept-source-agreements --silent
}

Write-Host "[4/9] rclone을 확인합니다."
$rclone = Find-RcloneExecutable
if (-not $rclone) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($winget) {
        & $winget.Source install --id Rclone.Rclone --exact --accept-package-agreements --accept-source-agreements --silent
        $rclone = Find-RcloneExecutable
    }
}
if (-not $rclone) {
    Write-Warning "rclone을 찾지 못했습니다. 앱 설치 후 새 자료 동기화는 수동으로 준비해야 합니다."
}

Write-Host "[5/9] 새 인쇄 자료를 확인합니다."
$assetConfig = Join-Path $appRoot "quick_printer_asset_channel.local.json"
if ($rclone -and (Test-Path -LiteralPath $assetConfig)) {
    $assetChannel = Get-Content -LiteralPath $assetConfig -Raw | ConvertFrom-Json
    $remoteName = [string]$assetChannel.remote
    $configuredRemotes = @(& $rclone listremotes 2>$null)
    if ($configuredRemotes -contains $remoteName) {
        & $venvPython (Join-Path $PSScriptRoot "quick_printer_asset_sync.py") pull `
            --channel-config $assetConfig `
            --root $appRoot
        if ($LASTEXITCODE -ne 0) {
            throw "인쇄 자료 증분 동기화에 실패했습니다."
        }
    } else {
        Write-Warning "rclone 원격 '$remoteName' 인증이 없습니다. 'rclone config' 후 sync_quick_printer_assets.cmd를 실행하세요."
    }
}

Write-Host "[6/9] 앱 스냅샷과 배포 자료를 검증합니다."
& $venvPython (Join-Path $PSScriptRoot "verify_quick_printer_release.py") --root $appRoot --fast
if ($LASTEXITCODE -ne 0) {
    throw "배포 자료 검증에 실패했습니다."
}

Write-Host "[7/9] Python 소스를 컴파일합니다."
& $venvPython -m py_compile `
    (Join-Path $appRoot "unit_print_manager.py") `
    (Join-Path $appRoot "unit_print_paths.py") `
    (Join-Path $appRoot "unit_print_keyboard.py") `
    (Join-Path $appRoot "unit_print_gyopyeong.py") `
    (Join-Path $appRoot "unit_print_selected_exams.py") `
    (Join-Path $appRoot "unit_print_materials.py") `
    (Join-Path $appRoot "unit_print_name_separator.py") `
    (Join-Path $appRoot "unit_print_pdf_printing.py") `
    (Join-Path $appRoot "unit_print_printer_setup.py") `
    (Join-Path $PSScriptRoot "quick_printer_asset_sync.py")
if ($LASTEXITCODE -ne 0) {
    throw "Python 소스 컴파일 검사에 실패했습니다."
}

Write-Host "[8/9] 앱 단위 테스트를 실행합니다."
& $venvPython -m unittest discover -s (Join-Path $appRoot "tests") -p "test_unit_print*.py"
if ($LASTEXITCODE -ne 0) {
    throw "앱 단위 테스트에 실패했습니다."
}

Write-Host "[9/9] 바로가기를 만듭니다."
$shell = New-Object -ComObject WScript.Shell
$launcher = Join-Path $appRoot "run_unit_print_manager_hidden.vbs"
$icon = Join-Path $appRoot "output\unit_print_manager\quick_print_icon.ico"
$shortcutTargets = @(
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "빠른 인쇄기.lnk"),
    (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\빠른 인쇄기.lnk")
)
foreach ($shortcutPath in $shortcutTargets) {
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = "$env:WINDIR\System32\wscript.exe"
    $shortcut.Arguments = '"' + $launcher + '"'
    $shortcut.WorkingDirectory = $appRoot
    $shortcut.IconLocation = "$icon,0"
    $shortcut.Description = "빠른 인쇄기"
    $shortcut.Save()
}

Write-Host ""
Write-Host "설치 완료: $appRoot" -ForegroundColor Green
Write-Host "다음 단계: 새 PC의 Codex에 '빠른인쇄기_프린터_ComputerUse_설정검증.md'를 전달하세요."
Write-Host "Codex는 Canon 설정 화면을 열되, 부서 ID/PIN 입력은 사용자가 직접 수행해야 합니다."
