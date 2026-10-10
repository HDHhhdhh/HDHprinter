# 빠른 인쇄기 글꼴

이 폴더는 제작 PC에 설치된 Pretendard Regular, Bold, Medium, SemiBold 네 파일을 그대로 담습니다. 각 파일은 [공식 v1.3.9](https://github.com/orioncactus/pretendard/releases/tag/v1.3.9)의 `static/alternative` TTF와 바이트 단위로 일치합니다. 버전 이름은 `Version 1.309`입니다. 파일별 크기와 SHA-256은 `quick_printer_fonts.json`에 있습니다.

Pretendard는 SIL Open Font License 1.1입니다. 글꼴을 앱과 함께 배포할 때 원래 저작권과 라이선스를 함께 유지합니다. 이 배포본은 원본을 수정하지 않았으며 [공식 라이선스](https://github.com/orioncactus/pretendard/blob/v1.3.9/LICENSE)를 `Pretendard/LICENSE.txt`에 그대로 포함합니다.

새 PC에서는 앱을 닫고 `Pretendard` 폴더의 **이 네 TTF 파일만** 선택해 Windows의 **설치**를 실행합니다. 다른 굵기, Variable, OTF 또는 새 버전을 추가하지 않습니다. Windows의 [파일 글꼴 설치 절차](https://support.microsoft.com/en-us/windows/experience/personalization/manage-fonts-in-windows)를 따릅니다. 현재 런타임에 네이티브 조작이 없으면 사용자가 이 설치 단계만 직접 수행합니다. 지원되지 않는 조작 경로로 우회하지 않습니다.

설치 후 새 프로세스로 다음 검사를 실행합니다. 이 명령은 글꼴을 설치하지 않으며, 숨김 Tk 루트에서 앱의 실제 글꼴 선택 규칙만 확인합니다. 앱 창과 프린터 작업은 만들지 않습니다.

```powershell
& ".venv\Scripts\python.exe" "scripts\verify_quick_printer_fonts.py"
```

`issues`가 빈 목록이고 `ui_font_family`와 normal/bold의 실제 family가 모두 `Pretendard`여야 합니다. 기존 동일 이름 글꼴의 해시가 다르면 자동 덮어쓰거나 삭제하지 않습니다. 다른 버전을 별도로 확인한 뒤 이 정본과 일치시키고 다시 검사합니다.

기준 글꼴 관측은 Python 3.12, Tk 8.6.15, `tk_scaling=1.3333333333333333`, 화면 1440×900에서 이루어졌습니다. 글꼴 일치만으로 화면의 픽셀 동일성을 판정하지 않습니다. 기준 GUI 자료가 있으면 그 자료의 Python/Tk, DPI, 창 크기, 화면 크기도 맞춘 뒤 비교합니다.

Microsoft와 윤고딕 글꼴 파일은 포함하지 않습니다. [Microsoft의 Windows 글꼴 재배포 규칙](https://learn.microsoft.com/en-us/typography/fonts/font-faq)을 따릅니다. Windows 글꼴이 빠졌다면 해당 PC의 Windows 설치와 [공식 추가 글꼴 기능](https://learn.microsoft.com/en-us/windows/deployment/windows-missing-fonts)을 확인합니다. 맑은 고딕을 다른 PC에서 복사하지 않습니다. 한국어 추가 글꼴 기능은 바탕/돋움/굴림/궁서 계열을 제공하므로 Pretendard 설치의 대체가 아닙니다.
