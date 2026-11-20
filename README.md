# 빠른 인쇄기 · HDHprinter

Windows에서 시험지·교재·교과서·OMR·직접 선택한 PDF를 하나의 작업 큐로 인쇄하는 Python 3.12 / Tkinter 앱입니다. 이 저장소에는 현재 소스, 테스트, 설치 도구, 카탈로그, Pretendard 글꼴과 라이선스를 보관합니다.

## 인쇄물 PDF 전체 자료

[전체 자료 Google Drive 폴더](https://drive.google.com/drive/folders/1U0WEcqJkNUgUU9aiYeYX3l8YL2dlxkXa)

2026-11-20에 확인한 전체 자료 기준본은 `빠른인쇄기_정본_20261107_학군지기말_r2`이며, 등록 PDF 1,891개를 포함합니다. 위 폴더에서 같은 이름으로 시작하는 원샷 프롬프트를 열고 해당 판의 지시서를 따르세요. 전송 조각은 총 65개입니다. 일부 번호에는 `.a`와 `.b`가 모두 필요합니다. 조각 목록과 해시를 확인해 합친 ZIP은 6,572,896,352바이트이며 SHA-256은 `b57e6c814e0364ae50fbf5292c02391aef65d2ddde922a45e9dea233dda620d2`입니다.

r2 설치는 원샷 지시서의 `-SkipAssetSync` 옵션을 사용합니다. 2026-11-20 증분 채널은 8월 기준본을 전제로 하며 r2와 필수 PDF 6개의 경로 또는 내용이 일치하지 않습니다. 전체 설치 이후 추가된 자료까지 한 번에 적용하는 절차는 아래 개선 계획의 첫 번째 배포 과제입니다.

Git의 최신 코드와 연결할 때는 압축 안의 `data` 폴더만 사용합니다. Drive 배포본의 코드나 `release_manifest.json`을 Git 폴더에 덮어쓰지 않습니다. 앱 기본 구분지 `unit_print_separator_a4.pdf`는 코드와 함께 관리합니다. 그 외 인쇄물 PDF, 학생 주문, 실행 이력, 복구 기록, PC 설정과 인증값은 Git에 포함하지 않습니다.

## 소스에서 실행

Windows에 Python 3.12와 SumatraPDF를 준비합니다. 저장소 루트에서 다음 명령을 실행합니다.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r unit_print_manager_requirements.txt
```

자료는 다음 중 한 위치로 연결합니다.

- 앱 폴더에 `data`를 놓습니다.
- 자료를 별도로 보관하면 `quick_printer_local_paths.example.json`을 `quick_printer_local_paths.json`으로 복사합니다. `release_root`에는 `data`를 포함한 상위 폴더의 절대 경로를 입력합니다. 예를 들어 자료가 `D:\빠른인쇄기_자료\data`에 있으면 `D:\빠른인쇄기_자료`를 지정합니다.

앱을 실행하기 전에 [글꼴 설치 안내](fonts/README.md)에 따라 Pretendard Regular/Bold/Medium/SemiBold 네 TTF를 설치합니다. 라이선스는 `fonts/Pretendard/LICENSE.txt`에 있습니다.

```powershell
.\.venv\Scripts\python.exe scripts\verify_quick_printer_fonts.py
.\.venv\Scripts\python.exe scripts\verify_quick_printer_environment.py
.\.venv\Scripts\python.exe unit_print_bootstrap.py
```

콘솔 없는 실행에는 `run_unit_print_manager.cmd` 또는 `run_unit_print_manager_hidden.vbs`를 사용합니다. 프린터 대상과 Canon 상태 연결은 해당 PC에서 설정합니다. 부서 ID/PIN은 저장소에 넣지 않습니다.

## 문서와 검사

- [2026-11-20 전체 검토와 구현 계획](docs/빠른인쇄기_전체검토_구현계획_20261120.md)
- [자료 등록·버전 발행·수신 PC 업데이트](docs/빠른인쇄기_자료_업데이트.md)
- [개발 기록: 사용자 목표·오류와 해결·구조 변화](docs/빠른인쇄기_개발기록/README.md)
- [제품 동작 정본](빠른인쇄기_제품동작_정본.md)
- [운영 안내](unit_print_manager_README.md)
- [키보드 사용 안내](빠른인쇄기_키보드_사용가이드.md)
- [화면 디자인과 출처](빠른인쇄기_GUI_디자인_참고.md)
- [구조와 개발 지침](AGENTS.md)

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_unit_print*.py"
```

검사는 실제 프린터로 인쇄하지 않습니다. 설치·실행기 검사 일부는 Windows Script Host를 사용합니다. 2026-10-09 검증에서는 관련 검사 131개가 통과했고, VBS 실행기 검사 한 메서드의 6개 하위 조건은 `cscript.exe` 시간 초과로 확인하지 못했습니다. 단순 출력만 하는 VBS도 같은 시간 초과를 보였습니다.

2026-10-12 인쇄 중 큐 편집 패치는 관련 회귀 검사 257개를 통과했습니다. 다른 블록을 인쇄 중이어도 미전송 `대기`·`예약`·`추가됨` 블록을 묶거나 이름을 채울 수 있습니다. 예약 블록과 새 대기 블록을 함께 묶으면 묶음 전체가 현재 인쇄에 포함됩니다. 이미 전송이 시작된 작업은 보호합니다. 검사는 모의 프린터로 Canon 연동과 Windows 대기열 전송 경로를 확인했으며 실제 출력은 수행하지 않았습니다.

2026-11-20 소스에는 선택 블록 인쇄, Canon 장비 오류 해소 후 자동 재개, 주문서 전체 행 수 표시와 첫 오류 이동, APEX 최근 자료 불러오기, 자료 버전 검사·준비·전환 명령이 포함됩니다.
