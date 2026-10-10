# 빠른 인쇄기 · HDHprinter

Windows에서 시험지·교재·교과서·OMR·직접 선택한 PDF를 하나의 작업 큐로 인쇄하는 Python 3.12 / Tkinter 앱입니다. 이 저장소에는 현재 소스, 테스트, 설치 도구, 카탈로그, Pretendard 글꼴과 라이선스를 보관합니다.

## 인쇄물 PDF 전체 자료

[전체 자료 Google Drive 폴더](https://drive.google.com/drive/folders/1U0WEcqJkNUgUU9aiYeYX3l8YL2dlxkXa)

현재 자료 기준본은 `빠른인쇄기_정본_20261007_큐표시`이며, 등록 PDF 1,852개를 포함합니다. 위 폴더에서 `.zip.part001`~`.zip.part049` 49개, `.zip.parts.sha256`, `_합치기.py`, `.zip.sha256`을 같은 다운로드 폴더에 받습니다. 조각을 합친 ZIP은 약 6.56 GB입니다.

병합기 SHA-256은 `e7733666d669229b5fd9e5c63d37a8198ae6a347f9a8999e076fb04fec202fca`, 조각 매니페스트 SHA-256은 `00c6327f98906c646bec9d728543a16770f3b1a349b129714eba05dcc5bf0653`입니다. 두 파일을 확인한 뒤 Python 3.12로 실행합니다.

```powershell
py -3.12 .\빠른인쇄기_정본_20261007_큐표시_합치기.py --directory <다운로드_폴더>
```

완성 ZIP의 SHA-256은 `82df8a3bf09373f06dbdb52dc4f1520618c23f9d4905557e00a46e90a9253599`입니다. 조각별 해시와 전체 ZIP 해시를 확인하고 압축을 해제합니다.

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

- [제품 동작 정본](빠른인쇄기_제품동작_정본.md)
- [운영 안내](unit_print_manager_README.md)
- [키보드 사용 안내](빠른인쇄기_키보드_사용가이드.md)
- [화면 디자인과 출처](빠른인쇄기_GUI_디자인_참고.md)
- [구조와 개발 지침](AGENTS.md)

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_unit_print*.py"
```

검사는 실제 프린터로 인쇄하지 않습니다. 설치·실행기 검사 일부는 Windows Script Host를 사용합니다. 2026-10-09 검증에서는 관련 검사 131개가 통과했고, VBS 실행기 검사 한 메서드의 6개 하위 조건은 `cscript.exe` 시간 초과로 확인하지 못했습니다. 단순 출력만 하는 VBS도 같은 시간 초과를 보였습니다.

Git 소스에는 2026-10-09까지의 화면 반응속도 개선, 선택 블록 자동저장, 실제 인쇄 시간 기반 예측, 한국어 줄바꿈 패치를 포함합니다. Drive의 2026-10-07 배포본은 전체 PDF 자료 기준본이며, 이후 소스 변경은 이 저장소를 기준으로 확인합니다.
