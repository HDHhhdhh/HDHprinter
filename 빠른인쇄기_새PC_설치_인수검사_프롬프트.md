# 빠른 인쇄기 새 PC 설치 인수검사 및 복구 프롬프트

이 문서를 **빠른 인쇄기가 설치된 새 Windows PC의 Codex에 그대로 전달**한다. 이 작업은 설치 여부를 대충 확인하는 점검이 아니다. 인증 반복 입력, 출력 잘림, 잘못된 양면 방향, 스테이플 누락을 실제 사용 경로와 종이 결과로 검증하고, 발견한 문제는 가능한 범위에서 바로 복구하는 인수검사다.

이 문서에는 비공개 프린터 인증 정보가 포함되어 있지 않다. 인증값은 사용자가 직접 입력하며, 화면 캡처, 명령 출력, 로그, 결과 보고서에 PIN을 노출하지 않는다.

`빠른인쇄기_프린터_ComputerUse_설정검증.md`를 함께 읽고 그 문서의 GUI 점검 절차를 우선 적용한다. 배포 ZIP의 앱 파일은 제작 PC에서 검증한 정본이므로 이 인수검사 중 앱을 다시 구현하거나 파일 구조를 재설계하지 않는다. 무결성 검사가 실패하면 소스를 임의 수정하지 말고 ZIP을 다시 압축 해제한다.

## 역할과 완료 조건

당신은 이 PC의 빠른 인쇄기 설치를 감사하고 복구하는 담당자다. 아래 사항을 모두 지킨다.

1. 설치 폴더, 실행 중인 앱, Windows 프린터 큐, Canon 드라이버, 현재 사용자 설정을 직접 조사한다.
2. 기존 설치 지시서나 설치 스크립트의 성공 문구를 증거로 인정하지 않는다.
3. 문제를 찾으면 원인을 설명하는 데서 멈추지 말고, 안전하게 고칠 수 있는 것은 고친 뒤 같은 검사를 다시 실행한다.
4. 실제 인쇄 직전에 사용자에게 종이 사용 승인을 한 번 받는다. 승인 전에는 인쇄 명령을 보내지 않는다.
5. 종이 결과는 Codex가 직접 볼 수 없으므로 사진을 받거나 사용자가 체크 항목별로 확인한 답을 받아야 한다. 확인 없이 `PASS`라고 쓰지 않는다.
6. 앱 코드 단위 테스트만 통과한 상태, 큐 이름만 존재하는 상태, Windows 테스트 페이지만 나온 상태를 최종 합격으로 처리하지 않는다.
7. 앱과 무관한 프린터, 문서, 프로그램 설정은 변경하지 않는다.
8. `computer-use:computer-use` 스킬을 사용해 Canon 드라이버의 실제 화면을 확인한다. PowerShell이나 PrintTicket XML만으로 합격 처리하지 않는다.
9. 인증 입력 창은 자동 조작하지 않는다. 창을 연 뒤 사용자에게 제어권을 넘기고, 저장 후 검증을 다시 이어간다.

최종 합격은 다음 조건을 모두 만족할 때만 가능하다.

- 인쇄할 때 부서 ID/PIN 입력 창이 나타나지 않는다.
- 앱을 완전히 종료하고 다시 실행해도 입력 창이 나타나지 않는다.
- Windows 재부팅 뒤에도 입력 창이 나타나지 않는다.
- 세로, 가로, 구분지, A:ble 네 경로가 지정된 용지 방향·양면 넘김·스테이플·배율로 나온다.
- 시험지의 네 방향 내용과 10 mm 안전선이 잘리지 않는다.
- A:ble은 앱에서 만든 2×2만 적용되고 Canon 드라이버가 다시 모아찍지 않는다.

## 고정 기준

### 프린터와 인증

- 기본 큐: `iR-ADV 8585 III`
- 전용 큐:
  - `iR-ADV 8585 III - 세로`
  - `iR-ADV 8585 III - 가로`
  - `iR-ADV 8585 III - 구분지`
  - `iR-ADV 8585 III -A:ble`
- 드라이버: `Canon Generic Plus UFR II`
- 프린터 주소: `192.168.0.248`
- 포트: 위 주소를 가리키는 **Standard TCP/IP 포트**
- 부서 ID: `{{CANON_DEPARTMENT_ID}}`
- PIN/PW: `{{CANON_DEPARTMENT_PIN}}`
- `PIN 설정 허가`: 켬
- `인쇄 시 부서 ID/PIN 확인`: 끔

WSD, USB, Microsoft IPP/Class Driver는 합격 처리하지 않는다. Canon 공식 문서상 USB/WSD에서는 부서 인증의 `검증` 기능을 사용할 수 없다. 정상 TCP/IP 연결에서도 `검증` 버튼만 실패할 수 있으므로, 실제 무팝업 인쇄 성공 여부를 최종 기준으로 삼는다.

### 프로필별 종이 기준

| 큐 | 용지/색상 | 방향 | 양면 | 페이지 배치 | 스테이플 |
|---|---|---|---|---|---|
| 세로 | A4/흑백 | 세로 | 긴 쪽 넘김 | 드라이버 1쪽 | 읽는 방향 좌측 상단 |
| 가로 | A4/흑백 | 가로 | 짧은 쪽 넘김 | 드라이버 1쪽 | 읽는 방향 좌측 상단 |
| 구분지 | A4/흑백 | 가로 | 결과에 영향 없음 | 드라이버 1쪽 | 없음 |
| A:ble | A4/흑백 | 가로 | 짧은 쪽 넘김 | **드라이버 1쪽**, 앱 PDF만 2×2 | 읽는 방향 좌측 상단 |

모든 일반 PDF 전송에는 SumatraPDF의 `fit`, `monochrome`, `paper=A4`가 포함되어야 한다. 세로에는 `duplexlong`, 가로와 A:ble에는 `duplexshort`가 포함되어야 한다. SumatraPDF나 앱에서 `landscape`로 내용을 강제 회전하지 않는다.

## 1단계: 설치본과 실행 경로 확정

1. 이 문서가 있는 폴더부터 `unit_print_manager.py`, `install_quick_printer.cmd`, `scripts` 폴더를 찾는다.
2. 바탕화면 `빠른 인쇄기.lnk`의 대상과 작업 폴더를 읽어 실제 실행 폴더를 확정한다.
3. 실행 중인 `pythonw.exe`의 명령행도 확인해 바로가기와 같은 폴더인지 대조한다.
4. 다른 날짜의 복사본이나 이전 배포본이 실행되고 있으면 사용자에게 경로를 보여준 뒤 바로가기를 현재 설치본으로 고친다.
5. 설치 폴더의 `AGENTS.md`, `빠른인쇄기_설치_가이드.md`, `unit_print_manager_README.md`를 읽는다.

아래 증거를 결과 보고서에 남긴다.

- 실제 앱 폴더
- 바로가기 대상/인수/작업 폴더
- Python 실행 파일
- SumatraPDF 실행 파일과 버전
- `release_manifest.json` 존재 여부와 배포 날짜

## 2단계: 코드와 배포 자료 검사

앱을 실행하기 전에 `fonts/README.md`를 읽는다. `fonts/Pretendard`의 Regular/Bold/Medium/SemiBold **네 TTF 파일만** Windows에 설치한다. 제작 PC와 같은 Pretendard v1.3.9(`Version 1.309`) 파일이다. 다른 굵기, Variable, OTF 또는 최신 버전을 추가하지 않는다. [Microsoft의 파일 글꼴 설치 절차](https://support.microsoft.com/en-us/windows/experience/personalization/manage-fonts-in-windows)를 따른다. 현재 런타임이 네이티브 조작을 제공하지 않으면 사용자가 이 설치 단계만 직접 수행한다. 지원되지 않는 조작 경로로 우회하지 않는다.

앱을 닫고 설치 후 새 프로세스로 다음 명령을 실행한다.

```powershell
& ".venv\Scripts\python.exe" "scripts\verify_quick_printer_fonts.py"
```

`issues`가 빈 목록이며 앱의 `ui_font_family`와 normal/bold 실제 family가 모두 `Pretendard`여야 한다. 파일별 설치 SHA-256도 정본과 같아야 한다. 기존 같은 이름 글꼴이 다르면 자동 덮어쓰기나 삭제를 하지 않는다. 소스를 바꿔 대체 글꼴을 쓰면서 동일환경으로 판정하지 않는다. OFL 저작권과 라이선스는 `fonts/Pretendard/LICENSE.txt`에 포함한다. Windows 맑은 고딕/Yu Gothic 및 윤고딕 파일은 배포하지 않는다. 글꼴 일치만으로 픽셀 동일성을 판단하지 않으며 기준 GUI 자료의 Python/Tk·배율·화면/창 크기도 맞춘다.

앱 가상환경이 아직 없으면 글꼴 네 파일을 먼저 설치한 뒤 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_quick_printer.ps1 -SkipAssetSync`를 실행한다. 설치 스크립트는 Python 환경을 준비한 뒤 같은 글꼴 검사를 수행한다. 정본 설치 중 PDF 업데이트 동기화는 실행하지 않는다. 설치가 끝나면 아래 전체 SHA-256 검사도 실행한다. ZIP 다운로드 채널과 `quick_printer_asset_channel.local.json`의 이후 PDF 업데이트 채널은 목적이 다르다.

설치 폴더에서 가능한 검사를 모두 실행한다.

```powershell
& ".venv\Scripts\python.exe" "scripts\verify_quick_printer_release.py" --root . --fast
& ".venv\Scripts\python.exe" "scripts\verify_quick_printer_release.py" --root .
& ".venv\Scripts\python.exe" -m py_compile unit_print_manager.py unit_print_name_separator.py unit_print_pdf_printing.py unit_print_printer_setup.py
& ".venv\Scripts\python.exe" -m unittest discover -s tests -p "test_unit_print*.py"
```

`--fast`에서도 앱·스크립트·테스트·문서·아이콘은 SHA-256으로 검증되어야 한다. 배포본에 `tests`가 없거나 불변 파일 해시가 다르면 그 사실을 `BLOCKED` 근거로 남기고 ZIP을 다시 압축 해제한다. 테스트 실패는 숨기지 말고 실패 이름과 핵심 오류를 기록한다.

또한 소스에서 실제 SumatraPDF 명령 구성을 확인한다.

- 공통: `fit,monochrome,paper=A4`
- 세로 양면: `duplexlong`
- 가로 양면: `duplexshort`
- A:ble 조판 함수가 A4 가로 2×2 PDF를 만들고, 드라이버에는 1쪽 배치로 보내는지 확인

## 3단계: 프린터 큐·드라이버·통신 조사

먼저 아무 설정도 바꾸지 말고 현 상태를 수집한다.

```powershell
$names = @(
  "iR-ADV 8585 III",
  "iR-ADV 8585 III - 세로",
  "iR-ADV 8585 III - 가로",
  "iR-ADV 8585 III - 구분지",
  "iR-ADV 8585 III -A:ble"
)

Get-Printer |
  Where-Object Name -in $names |
  Select-Object Name,DriverName,PortName,PrinterStatus,Shared |
  Format-Table -AutoSize

Get-PrinterPort |
  Where-Object { $_.Name -match "192\.168\.0\.248|^IP_" } |
  Format-List Name,PrinterHostAddress,PortNumber,SNMPEnabled,SNMPCommunity

Test-NetConnection 192.168.0.248 -Port 9100
Get-Service | Where-Object { $_.Name -match "Canon" -or $_.DisplayName -match "Canon" } |
  Format-Table Name,DisplayName,Status -AutoSize
```

다음을 하나씩 판정한다.

- 5개 큐가 모두 존재하는가.
- 5개 큐가 모두 같은 Canon 드라이버와 같은 TCP/IP 포트를 사용하는가.
- `DriverName`이 정확한 Canon UFR II 계열인가.
- 포트 주소가 `192.168.0.248`인가.
- `TcpTestSucceeded`가 `True`인가.
- WSD/USB/IPP 포트가 섞이지 않았는가.
- Canon Driver Information Assist Service가 설치되어 있는가. 서비스 이름이 다르면 드라이버 설치 항목과 Canon 설치 로그로 확인한다.

### 큐 복구 규칙

- 기본 큐가 WSD/USB/IPP 또는 Microsoft Class Driver이면 Canon 전체 드라이버 설치본으로 `192.168.0.248` Standard TCP/IP 큐를 먼저 바로잡는다.
- 전용 큐만 잘못됐으면 현재 설정을 기록한 뒤 **정확히 네 전용 큐만** 삭제하고 아래 명령으로 다시 만든다.
- 기본 큐 삭제는 사용자 승인 없이 하지 않는다.

```powershell
& ".venv\Scripts\python.exe" "scripts\setup_quick_printer_profiles.py" --base-printer "iR-ADV 8585 III"
```

프로필 생성기가 “이미 준비됨”이라고 말해도 설정이 맞다는 증거가 아니다. 이후 단계에서 각 큐의 시스템 기본값과 현재 사용자 기본값을 다시 검사한다.

## 4단계: 인증 저장 검사 및 복구

이 단계는 **빠른 인쇄기를 실제로 실행하는 Windows 사용자 계정**으로 진행한다. 관리자 계정에서 저장한 값이 일반 사용자에게 자동 상속된다고 가정하지 않는다.

Computer Use로 `control printers` 또는 `설정 > Bluetooth 및 장치 > 프린터 및 스캐너`를 열고 큐별 Canon GUI를 직접 사용한다. 인증값 입력 직전에는 자동 조작을 멈추고 사용자에게 키보드/마우스 제어권을 넘긴다. 인증 창은 캡처하지 않는다.

5개 큐 각각에서 다음을 수행한다.

1. `프린터 속성 > 디바이스 설정 > 부서 ID 관리`를 연다.
2. `PIN 설정 허가`를 켠다.
3. 부서 ID `{{CANON_DEPARTMENT_ID}}`, PIN `{{CANON_DEPARTMENT_PIN}}`을 입력한다.
4. `인쇄 시 부서 ID/PIN 확인`을 끈다.
5. `디바이스에서 부서 ID/PIN 인증`을 켠다.
6. `적용`과 `확인`으로 닫는다.
7. 속성을 다시 열어 ID가 유지되고, PIN이 저장된 상태이며, 확인 옵션이 계속 꺼져 있는지 검사한다.

주의 사항:

- PIN 입력 화면을 캡처하거나 PIN을 결과 로그에 다시 쓰지 않는다.
- `검증할 수 없습니다`가 뜨면 먼저 3단계의 포트, 통신, Canon 보조 서비스를 고친다.
- TCP/IP와 서비스가 정상인데 `검증` 버튼만 실패하면 설정을 저장하고 실제 1쪽 인쇄로 인증을 판정한다.
- 인쇄 창이 매번 뜨면 앱 문제가 아니라 현재 사용자용 Canon DEVMODE/인증 설정이 큐별로 저장되지 않은 상태일 가능성을 우선 확인한다.
- 한 큐에서 성공했다고 다른 네 큐도 성공한 것으로 간주하지 않는다.

## 5단계: 프로필 설정 검사 및 복구

5개 큐의 `인쇄 기본 설정`과 `프린터 속성 > 고급 > 인쇄 기본값`을 모두 확인한다. SumatraPDF는 현재 사용자 설정과 시스템 기본값 중 드라이버가 제공하는 값을 사용할 수 있으므로 두 설정층이 충돌하면 안 된다.

이 단계도 Computer Use로 실제 Canon 창을 열어 수행한다. 큐별로 설정 전 화면을 읽고, 변경 후 모든 창을 닫았다가 다시 열어 저장 상태를 확인한다. 값이 보이지 않거나 다른 대화상자가 가린 캡처는 증거로 인정하지 않는다.

각 전용 큐에서 다음을 확인하고 고친다.

- 페이지 크기 A4
- 출력 크기 A4
- 흑백
- 자동 배율 조정 또는 A4에 맞춤
- 포스터/소책자/배너 사용 안 함
- 드라이버 페이지 레이아웃 `1면에 1쪽`
- 표에 지정된 방향과 양면 넘김
- 표에 지정된 스테이플

Canon 드라이버 내부 PrintTicket의 방향 이름이나 스테이플 좌표가 실제 종이와 반대로 매핑될 수 있다. XML 토큰만 보고 합격 처리하지 말고, 마지막 종이 결과의 **읽는 방향 좌측 상단**을 기준으로 보정한다.

## 6단계: 통제된 QA PDF 준비

설치 폴더 아래 `output\printer_acceptance`에 원본을 건드리지 않는 테스트 PDF를 만든다.

### 세로·가로 테스트 원본

각각 4페이지를 만든다. 모든 페이지에 다음 표시가 있어야 한다.

- 페이지 방향과 `PAGE 1/4` 형식의 큰 번호
- `TOP LEFT`, `TOP RIGHT`, `BOTTOM LEFT`, `BOTTOM RIGHT` 모서리 표기
- 실제 종이 가장자리에서 10 mm 안쪽의 사각 안전선
- 중앙의 100 mm 기준선
- 앞면/뒷면의 위쪽을 쉽게 구별하는 굵은 화살표

세로 PDF는 A4 세로, 가로 PDF는 A4 가로여야 한다. 글꼴 문제를 피하려면 ASCII 표기를 사용한다.

### A:ble 테스트 원본

A4 세로 16페이지를 만들고 각 페이지 중앙에 `ABLE 01`부터 `ABLE 16`까지 크게 표기한다. 네 모서리에는 10 mm 안전선을 넣는다. 프로젝트의 `materialize_four_up_pdf()`를 사용해 A4 가로 2×2 PDF로 조판한다.

조판 PDF를 화면 또는 렌더링 이미지로 먼저 검사한다.

- 1번째 출력면: 01, 02 / 03, 04
- 2번째 출력면: 05, 06 / 07, 08
- 3번째 출력면: 09, 10 / 11, 12
- 4번째 출력면: 13, 14 / 15, 16
- 각 칸이 잘리지 않고 모두 A4 가로 안에 들어감

## 7단계: 실제 앱 경로 종이 검사

여기서 사용자에게 다음 내용을 보여주고 한 번만 승인받는다.

```text
빠른 인쇄기 인수검사를 위해 기본 9장, 재부팅 후 2장까지 최대 11장을 사용합니다.
세로 2회, 가로 1회, 구분지 1회, A:ble 1회를 전송합니다.
지금 실제 인쇄를 진행할까요?
```

승인 후에는 Windows 테스트 페이지, UPDF, Adobe가 아니라 **빠른 인쇄기가 사용하는 SumatraPDF 전송 경로**로 실행한다. 앱의 `PDF 파일` 블록과 해당 프로필을 사용하거나, 동일한 앱 함수와 설정을 호출하는 전용 QA 스크립트를 임시로 만들어 사용한다.

### 최소 인쇄 세트

1. 세로 4페이지, 1부: 세로 큐
2. 같은 세로 4페이지, 1부를 앱 완전 종료·재실행 후 다시 전송: 세로 큐
3. 가로 4페이지, 1부: 가로 큐
4. 고정 `unit_print_separator_a4.pdf`, 1부: 구분지 큐
5. A:ble 16페이지를 앱 함수로 2×2 조판한 4페이지 PDF, 1부: A:ble 큐
6. Windows 재부팅 후 세로 4페이지, 1부를 한 번 더 전송

인쇄 대화상자에서 ID/PIN을 직접 타이핑해서 통과시키면 해당 케이스는 `FAIL`이다. 첫 번째 전송뿐 아니라 앱 재실행 후 두 번째 전송과 재부팅 후 전송에서도 팝업이 없어야 한다.

### 종이 확인표

사용자에게 출력물 사진을 받거나 아래 문항을 한 항목씩 확인받는다.

| 검사 | 합격 기준 |
|---|---|
| 세로 배율 | 네 모서리 표기와 10 mm 안전선이 모두 보이고 내용이 잘리지 않음 |
| 세로 양면 | 세로로 놓고 왼쪽으로 책처럼 넘겼을 때 뒷면이 바로 서 있음 |
| 세로 스테이플 | 2장 묶음의 읽는 방향 좌측 상단 |
| 가로 배율 | 네 모서리 표기와 10 mm 안전선이 모두 보이고 내용이 잘리지 않음 |
| 가로 양면 | 가로로 놓고 왼쪽으로 넘겼을 때 뒷면이 바로 서 있음 |
| 가로 스테이플 | 2장 묶음의 읽는 방향 좌측 상단 |
| 구분지 | A4 가로, 큰 `구분지`, 불필요한 축소·잘림 없음, 스테이플 없음 |
| A:ble 배치 | 한 면에 4쪽, 좌상단부터 가로 순서, 총 01~16 순서 유지 |
| A:ble 배율 | 각 칸의 10 mm 안전선과 번호가 모두 보이고 이중 축소·잘림 없음 |
| A:ble 양면 | 가로로 놓고 왼쪽으로 넘겼을 때 뒷면이 바로 서 있음 |
| A:ble 스테이플 | 2장 묶음의 읽는 방향 좌측 상단 |
| 인증 1차 | PIN 팝업 없음 |
| 인증 앱 재실행 | PIN 팝업 없음 |
| 인증 재부팅 | PIN 팝업 없음 |

실패한 경우 해당 프로필만 수정하고 그 케이스만 재인쇄한다. 출력물 방향을 억지로 돌려 “맞다”고 판정하지 않는다.

## 8단계: 결과 보고서

설치 폴더에 `빠른인쇄기_설치_인수검사_결과.md`를 만든다. 다음 형식을 지킨다.

```markdown
# 빠른 인쇄기 설치 인수검사 결과

- 검사 일시:
- Windows 사용자:
- 앱 경로:
- 드라이버:
- 포트/주소:
- SumatraPDF:

## 판정
- 전체: PASS / FAIL / BLOCKED
- 인증 저장: PASS / FAIL / BLOCKED
- 세로: PASS / FAIL / BLOCKED
- 가로: PASS / FAIL / BLOCKED
- 구분지: PASS / FAIL / BLOCKED
- A:ble: PASS / FAIL / BLOCKED

## 발견 문제와 수정
- 문제:
- 원인:
- 수정:
- 재검사 결과:

## 증거
- 실행한 명령과 핵심 출력:
- 종이 사진 또는 사용자 확인:
- PIN 팝업 확인:
- 앱 재실행 확인:
- Windows 재부팅 확인:

## 남은 일
- 없음 / 구체적인 미완료 항목
```

결과를 사용자에게 설명할 때는 `무엇을 확인했는지`, `무엇을 고쳤는지`, `종이로 무엇이 증명됐는지`, `아직 증명하지 못한 것이 무엇인지`를 짧고 분명하게 구분한다. 재부팅 후 검사나 종이 확인이 빠졌으면 전체 판정은 `PASS`가 될 수 없다.
