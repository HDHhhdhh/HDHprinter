from __future__ import annotations

import hashlib
import json
import re
import tempfile
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from pypdf import PdfReader, PdfWriter

from unit_print_paths import DATA_ROOT
from unit_print_pdf_metadata import get_pdf_page_count


MODULE_DIR: Final = Path(__file__).resolve().parent
BUNDLED_MATERIAL_ROOT: Final = DATA_ROOT
LEGACY_APEX_ROOT: Final = Path(r"C:\Users\gkseh\OneDrive\바탕 화면\APEX 원고")
LEGACY_ABLE_ROOT: Final = Path(r"D:\교재\Able\렉쳐노트")
LEGACY_TRAINING_PACK_ROOT: Final = Path(r"D:\숙제\트팩 완성본")
LEGACY_FREQUENT_TYPE_ROOT: Final = Path(r"D:\숙제\빈출유형 완성본")
DEFAULT_APEX_ROOT: Final = (
    BUNDLED_MATERIAL_ROOT / "APEX"
    if (BUNDLED_MATERIAL_ROOT / "APEX").is_dir()
    else LEGACY_APEX_ROOT
)
DEFAULT_ABLE_ROOT: Final = (
    BUNDLED_MATERIAL_ROOT / "Able" / "렉쳐노트"
    if (BUNDLED_MATERIAL_ROOT / "Able" / "렉쳐노트").is_dir()
    else LEGACY_ABLE_ROOT
)
DEFAULT_ABLE_FINAL_ROOT: Final = DEFAULT_ABLE_ROOT / "[최종본 모음]"
DEFAULT_TRAINING_PACK_ROOT: Final = (
    BUNDLED_MATERIAL_ROOT / "트레이닝팩"
    if (BUNDLED_MATERIAL_ROOT / "트레이닝팩").is_dir()
    else LEGACY_TRAINING_PACK_ROOT
)
DEFAULT_TRAINING_PACK_PAGE_MAP_PATH: Final = Path(__file__).with_name("트레이닝팩_차시별_인쇄페이지.md")
DEFAULT_FREQUENT_TYPE_ROOT: Final = (
    BUNDLED_MATERIAL_ROOT / "빈출유형"
    if (BUNDLED_MATERIAL_ROOT / "빈출유형").is_dir()
    else LEGACY_FREQUENT_TYPE_ROOT
)
DEFAULT_FREQUENT_TYPE_PAGE_MAP_PATH: Final = Path(__file__).with_name("빈출유형_차시별_인쇄페이지.md")
APEX_PROFILE_NAME: Final = "iR-ADV 8585 III - 세로"
ABLE_PROFILE_NAME: Final = "iR-ADV 8585 III -A:ble"

# Publishing a cache file is a very short critical section.  Striped locks
# keep same-process builders for one target from racing on Windows without
# serializing unrelated PDF preparation work.  The existence check inside
# the lock also lets a waiter reuse the complete file just published by the
# winner.  Separate app processes still use unique temporary files and the
# same atomic replace protocol.
_PDF_CACHE_PUBLISH_LOCKS: Final = tuple(threading.Lock() for _ in range(64))

# Page ranges in the combined A:ble lecture-note PDFs. These are 1-based PDF
# pages and intentionally live beside the material catalog so the UI and the
# print builder use one source of truth.
ABLE_LESSON_PAGE_RANGES: Final[dict[str, tuple[tuple[int, int], ...]]] = {
    "공통수학1": (
        (1, 32), (33, 56), (57, 96), (97, 124), (125, 156),
        (157, 188), (189, 220), (221, 240), (241, 256), (257, 280),
        (281, 300), (301, 324), (325, 348), (349, 372), (373, 412),
    ),
    "공통수학2": (
        (1, 32), (33, 60), (61, 92), (93, 120), (121, 152),
        (153, 180), (181, 208), (209, 240), (241, 264), (265, 296),
        (297, 324), (325, 356), (357, 384),
    ),
    "기하": (
        (1, 20), (21, 40), (41, 60), (61, 76), (77, 92),
        (93, 108), (109, 128), (129, 144), (145, 164), (165, 180),
        (181, 196), (197, 212), (213, 228), (229, 248),
    ),
    "대수": (
        (1, 28), (29, 56), (57, 80), (81, 104), (105, 132),
        (133, 160), (161, 188), (189, 220), (221, 248), (249, 272),
        (273, 304), (305, 328),
    ),
    "미적분1": (
        (1, 20), (21, 40), (41, 60), (61, 76), (77, 96),
        (97, 112), (113, 132), (133, 148), (149, 164), (165, 180),
        (181, 200), (201, 220), (221, 236), (237, 252), (253, 260),
        (261, 272),
    ),
    "확률과 통계": (
        (1, 16), (17, 32), (33, 52), (53, 72), (73, 92),
        (93, 108), (109, 124), (125, 140), (141, 156), (157, 172),
        (173, 188), (189, 204), (205, 216), (217, 232),
    ),
}

# APEX Boost 교재는 본문 차시 사이에 빠른정답이 이어져 있지 않다. 문서에
# 기록된 1-based PDF 페이지를 그대로 보존해, 차시별 출력 시
# ``본문 문제 페이지 -> 해당 차시 빠른정답 1쪽`` 순서로 재조합한다.
APEX_BOOST_LESSON_PAGE_RANGES: Final[
    dict[str, tuple[tuple[str, int, int, int], ...]]
] = {
    "공통수학2": (
        ("평면좌표", 2, 5, 98),
        ("직선의 방정식", 6, 13, 99),
        ("원의 방정식", 14, 25, 100),
        ("도형의 이동", 26, 37, 101),
        ("집합", 38, 45, 102),
        ("명제", 46, 55, 103),
        ("함수", 56, 65, 104),
        ("함수2", 66, 79, 105),
        ("유리함수", 80, 89, 106),
        ("무리함수", 90, 97, 107),
    ),
    "대수": (
        ("지수", 1, 5, 91),
        ("로그", 6, 12, 92),
        ("지수함수", 13, 22, 93),
        ("로그함수", 23, 32, 94),
        ("삼각함수", 33, 42, 95),
        ("삼각함수의 그래프", 43, 52, 96),
        ("등차수열", 53, 62, 97),
        ("등비수열", 63, 70, 98),
        ("수열의 합", 71, 80, 99),
        ("수열의 귀납적 추론", 81, 90, 100),
    ),
    "미적분1": (
        ("함수의 극한", 1, 10, 89),
        ("함수의 연속", 11, 20, 90),
        ("미분계수와 도함수", 21, 30, 91),
        ("도함수의 활용1", 31, 40, 92),
        ("도함수의 활용2", 41, 50, 93),
        ("도함수의 활용3", 51, 60, 94),
        ("부정적분", 61, 66, 95),
        ("정적분", 67, 78, 96),
        ("정적분의 활용", 79, 88, 97),
    ),
}

# APEX 차시 선택 목록에 표시할 영상관리 차시명이다. 페이지 맵의 차시 번호와
# 순서가 같아, PDF 페이지 범위와 표시 이름을 서로 섞지 않고 함께 사용할 수 있다.
APEX_LESSON_NAMES: Final[dict[str, tuple[str, ...]]] = {
    "공통수학1": (
        "다항식의 연산",
        "곱셈 공식의 활용",
        "다항식 나눗셈",
        "항등식의 이해",
        "나머지 정리",
        "다항식 구하기",
        "[고] 다항식 구하기2",
        "연속조립제법",
        "인수분해",
        "[고] 인수분해 활용",
        "복소수",
        "복소수 거듭제곱",
        "이차방정식과 판별식",
        "근계관",
        "근계관 활용",
        "이차함수 위치관계",
        "이차함수 그래프",
        "이차함수 최대최소",
        "이차함수 도형",
        "케이스분류",
        "[고] 이차함수의 해석1",
        "[고] 이차함수의 해석2",
        "[고] 최대최소 정의된 함수",
        "[고] 이차함수 추론",
        "삼차방정식의 풀이",
        "삼차방정식의 근",
        "오메가",
        "사차방정식의 풀이",
        "사차방정식의 근과 활용",
        "연립방정식",
        "연립방정식의 활용",
        "부정방정식",
        "연립일차부등식",
        "이차부등식",
        "이차부등식2",
        "연립이차부등식",
        "[고] 이차부등식 추론",
        "[고] 근의 분리",
        "경우의 수 1",
        "경우의 수 2",
        "순열1",
        "순열2",
        "[고] 순열3",
        "조합1",
        "조합2",
        "조합3",
        "[고] 조합4",
        "조합5",
        "행렬의 연산",
        "행렬의 곱셈",
        "행렬의 의미",
    ),
    "공통수학2": (
        "두 점 사이의 거리",
        "거리 합의 최대최소",
        "내분과 무게중심",
        "도형과 평면좌표",
        "[고] 도형의 성질과 좌표",
        "직선의 방정식",
        "직선 사이의 관계",
        "점과 직선 사이의 거리",
        "자취의 방정식",
        "[고] 직선의 방정식 활용",
        "원의 방정식",
        "좌표축과 원",
        "두 원의 교점을 지나는 원",
        "원과 직선의 위치관계",
        "접선의 방정식",
        "원과 자취의 방정식",
        "[고] 원의 방정식 활용",
        "도형의 평행이동",
        "도형의 대칭이동",
        "도형의 이동",
        "거리의 최소와 대칭",
        "[고] 도형의 이동 활용",
        "집합",
        "부분집합의 개수",
        "[고] 집합의 해석",
        "집합의 연산",
        "집합의 연산법칙",
        "집합의 표현",
        "원소의 개수",
        "[고] 집합의 해석2",
        "명제",
        "역과 대우",
        "충분조건과 필요조건",
        "절대부등식",
        "[고] 명제의 해석",
        "함수의 뜻과 종류",
        "함수의 개수",
        "합성함수",
        "역함수",
        "[고] 함수의 해석",
        "유리식의 계산",
        "유리함수의 그래프",
        "유리함수 그래프 활용",
        "유리함수와 합성함수, 역함수",
        "유리함수 자투리",
        "[고] 유리함수의 해석",
        "무리식의 계산",
        "무리함수의 그래프",
        "무리함수 그래프의 활용",
        "[고] 무리함수의 해석",
    ),
    "대수": (
        "거듭제곱근",
        "지수 법칙",
        "로그의 정의와 연산",
        "로그의 성질",
        "상용로그",
        "[고] 로그의 활용",
        "지수함수의 그래프",
        "지수함수의 그래프와 길이",
        "지수함수의 최대최소",
        "지수함수와 방부등식",
        "지수함수의 활용",
        "[고] 지수함수의 해석",
        "로그함수의 그래프",
        "로그함수의 그래프와 길이",
        "로그함수의 최대최소",
        "지수함수와 로그함수",
        "로그함수와 방부등식",
        "[고] 로그함수의 해석",
        "일반각",
        "부채꼴",
        "삼각함수의 정의",
        "삼각함수의 계산",
        "삼각함수의 활용",
        "삼각함수의 그래프",
        "삼각함수의 그래프 활용",
        "삼각방정식",
        "삼각부등식",
        "[고] 삼각함수의 그래프 해석",
        "사인법칙",
        "코사인법칙",
        "삼각형의 해석",
        "삼각형의 넓이",
        "[고] 복잡한 삼각형의 해석",
        "등차수열",
        "등차수열의 합",
        "등비수열",
        "등비수열의 합",
        "등비수열의 활용",
        "[고] 수열의 해석",
        "시그마의 뜻과 성질",
        "망원화",
        "일반항 구하기",
        "시그마의 활용",
        "등차, 등비수열의 점화식",
        "여러가지 점화식",
        "수학적 귀납법",
        "[고] 수열의 귀납적 추론",
    ),
    "미적분1": (
        "함수의 극한",
        "극한식의 꼴",
        "샌드위치와 근사",
        "미정계수의 결정",
        "다항함수 구하기",
        "극한의 활용",
        "[고] 극한의 해석",
        "함수의 연속",
        "연속 조건의 활용",
        "사잇값 정리",
        "[고] 연속의 해석",
        "미분계수",
        "곱의 미분법",
        "다항함수 구하기",
        "미분가능성",
        "[고] 도함수의 활용",
        "접선의 방정식",
        "접선의 방정식2",
        "접선의 활용",
        "[고] 도함수의 해석",
        "함수의 증감",
        "극대와 극소",
        "삼차함수의 비율",
        "최대와 최소",
        "[고] 도함수의 해석2",
        "다항함수의 근",
        "화면에 행 없음",
        "부등식과 미분",
        "속도와 가숙도",
        "[고] 다항함수 그래프의 해석",
        "부정적분",
        "다항함수 구하기",
        "도함수와 그래프",
        "정적분의 계산",
        "화면에 행 없음",
        "화면에 행 없음",
        "정적분과 다항함수",
        "화면에 행 없음",
        "정적분으로 정의된 함수",
        "정적분으로 정의된 함수2",
        "[고] 적분의 해석",
        "[고] 적분의 해석2",
        "다항함수의 넓이",
        "화면에 행 없음",
        "화면에 행 없음",
        "속도와 가속도",
        "화면에 행 없음",
    ),
}

_APEX_FILENAME = re.compile(r"^APEX(?P<boost> Boost)?\s+(?P<subject>.+?)(?P<solution>\s+해설)?$", re.IGNORECASE)
_ABLE_LESSON_FILENAME = re.compile(r"^(?P<subject>.+?)\s+(?P<lesson>\d+)차시(?:[.]ppt)?$", re.IGNORECASE)
_QUESTION_ONE = re.compile(r"(?<![\d.])1\s*[.]")
_TOPIC_HEADER = re.compile(
    r"(?P<chapter>[IVX]{1,5})[.]\s*(?P<chapter_title>[^0-9]{1,45}?)(?P<topic>\d{2})[.]?\s*(?P<title>[^0-9]{0,45})",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class PageSpan:
    number: int
    label: str
    start_page: int
    end_page: int
    part: str = "전체"
    page_ranges: tuple[tuple[int, int], ...] = ()

    def __post_init__(self) -> None:
        if self.number < 1 or self.start_page < 1 or self.end_page < self.start_page:
            raise ValueError(f"Invalid page span: {self!r}")
        ranges = self.page_ranges or ((self.start_page, self.end_page),)
        if any(start < 1 or end < start for start, end in ranges):
            raise ValueError(f"Invalid page ranges: {self!r}")
        if min(start for start, _end in ranges) != self.start_page:
            raise ValueError(f"Page range start does not match span: {self!r}")
        if max(end for _start, end in ranges) != self.end_page:
            raise ValueError(f"Page range end does not match span: {self!r}")

    @property
    def effective_page_ranges(self) -> tuple[tuple[int, int], ...]:
        return self.page_ranges or ((self.start_page, self.end_page),)

    @property
    def page_numbers(self) -> tuple[int, ...]:
        return flatten_page_ranges(self.effective_page_ranges)

    @property
    def page_range_label(self) -> str:
        return format_page_ranges(self.effective_page_ranges)


@dataclass(frozen=True, slots=True)
class ApexManualLesson:
    label: str
    part: str
    page_ranges: tuple[tuple[int, int], ...]
    question_ranges: tuple[tuple[int, int], ...]
    answer_pages: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.page_ranges or not self.question_ranges or not self.answer_pages:
            raise ValueError(f"APEX 차시 매핑이 비어 있습니다: {self!r}")
        if any(start < 1 or end < start for start, end in self.page_ranges):
            raise ValueError(f"APEX 차시 페이지 범위가 잘못되었습니다: {self!r}")
        if any(start < 1 or end < start for start, end in self.question_ranges):
            raise ValueError(f"APEX 차시 문제 범위가 잘못되었습니다: {self!r}")
        if any(page < 1 for page in self.answer_pages):
            raise ValueError(f"APEX 빠른정답 페이지가 잘못되었습니다: {self!r}")


# APEX 원고의 차시 페이지는 직접 검수한 1-based PDF 페이지를 사용한다.
# question_ranges는 빠른정답 페이지를 검증하고 나중에 맵을 이어서 작성할 때
# 기준으로 삼기 위한 문제번호 범위이며, page_ranges는 겹치거나 끊긴 본문
# 페이지를 그대로 보존한다.
APEX_MANUAL_LESSON_PAGE_MAP: Final[dict[str, tuple[ApexManualLesson, ...]]] = {
    "공통수학1": (
        ApexManualLesson("01차시", "part.1", ((3, 14),), ((1, 56),), (263,)),
        ApexManualLesson("02차시", "part.1", ((15, 23),), ((57, 76),), (263, 264)),
        ApexManualLesson("03차시", "part.1", ((24, 29),), ((77, 96),), (264,)),
        ApexManualLesson("04차시", "part.1", ((30, 34),), ((97, 124),), (264,)),
        ApexManualLesson("05차시", "part.1", ((35, 50),), ((125, 199),), (264, 265)),
        ApexManualLesson("06차시", "part.1", ((35, 50),), ((125, 199),), (264, 265)),
        ApexManualLesson("07차시", "part.1", ((35, 50),), ((125, 199),), (264, 265)),
        ApexManualLesson("08차시", "part.1", ((49, 50),), ((192, 199),), (265,)),
        ApexManualLesson(
            "09차시",
            "part.1",
            ((51, 57), (60, 62)),
            ((200, 239), (248, 258)),
            (265, 266),
        ),
        ApexManualLesson("10차시", "part.1", ((55, 59),), ((226, 247),), (266,)),
        ApexManualLesson("11차시", "part.1", ((63, 70),), ((259, 302),), (266, 267)),
        ApexManualLesson("12차시", "part.1", ((71, 73),), ((303, 317),), (267,)),
        ApexManualLesson("13차시", "part.1", ((76, 84),), ((326, 363),), (267,)),
        ApexManualLesson("14차시", "part.1", ((85, 98),), ((364, 423),), (267, 268)),
        ApexManualLesson("15차시", "part.1", ((85, 98),), ((364, 423),), (267, 268)),
        ApexManualLesson("16차시", "part.1", ((99, 115),), ((424, 473),), (268, 269)),
        ApexManualLesson("17차시", "part.1", ((99, 115),), ((424, 473),), (268, 269)),
        ApexManualLesson("18차시", "part.1", ((116, 139),), ((474, 533),), (269, 270)),
        ApexManualLesson("19차시", "part.1", ((116, 139),), ((474, 533),), (269, 270)),
        ApexManualLesson("20차시", "part.1", ((116, 139),), ((474, 533),), (269, 270)),
        ApexManualLesson("21차시", "part.1", ((99, 139),), ((424, 533),), (268, 269, 270)),
        ApexManualLesson("22차시", "part.1", ((99, 139),), ((424, 533),), (268, 269, 270)),
        ApexManualLesson("23차시", "part.1", ((99, 139),), ((424, 533),), (268, 269, 270)),
        ApexManualLesson("24차시", "part.1", ((99, 139),), ((424, 533),), (268, 269, 270)),
        ApexManualLesson("25차시", "part.2", ((141, 150),), ((1, 49),), (271,)),
        ApexManualLesson("26차시", "part.2", ((141, 150),), ((1, 49),), (271,)),
        ApexManualLesson("27차시", "part.2", ((150, 153),), ((44, 63),), (271,)),
        ApexManualLesson("28차시", "part.2", ((153, 161),), ((59, 98),), (271, 272)),
        ApexManualLesson("29차시", "part.2", ((153, 161),), ((59, 98),), (271, 272)),
        ApexManualLesson("30차시", "part.2", ((162, 165),), ((99, 122),), (272,)),
        ApexManualLesson("31차시", "part.2", ((166, 168),), ((123, 131),), (272, 273)),
        ApexManualLesson("32차시", "part.2", ((169, 171),), ((132, 145),), (273,)),
        ApexManualLesson("33차시", "part.2", ((171, 178),), ((142, 180),), (273,)),
        ApexManualLesson("34차시", "part.2", ((179, 190),), ((181, 242),), (273, 274)),
        ApexManualLesson("35차시", "part.2", ((179, 190),), ((181, 242),), (273, 274)),
        ApexManualLesson("36차시", "part.2", ((190, 196),), ((238, 270),), (274, 275)),
        ApexManualLesson("37차시", "part.2", ((196, 204),), ((267, 308),), (274, 275)),
        ApexManualLesson("38차시", "part.2", ((196, 204),), ((267, 308),), (274, 275)),
        ApexManualLesson("39차시", "part.2", ((205, 215),), ((309, 369),), (275, 276)),
        ApexManualLesson("40차시", "part.2", ((205, 215),), ((309, 369),), (275, 276)),
        ApexManualLesson("41차시", "part.2", ((215, 224),), ((364, 412),), (276, 277)),
        ApexManualLesson("42차시", "part.2", ((215, 224),), ((364, 412),), (276, 277)),
        ApexManualLesson("43차시", "part.2", ((215, 224),), ((364, 412),), (276, 277)),
        ApexManualLesson("44차시", "part.2", ((225, 244),), ((413, 506),), (277, 278)),
        ApexManualLesson("45차시", "part.2", ((225, 244),), ((413, 506),), (277, 278)),
        ApexManualLesson("46차시", "part.2", ((225, 244),), ((413, 506),), (277, 278)),
        ApexManualLesson("47차시", "part.2", ((225, 244),), ((413, 506),), (277, 278)),
        ApexManualLesson("48차시", "part.2", ((225, 244),), ((413, 506),), (277, 278)),
        ApexManualLesson("49차시", "part.2", ((245, 262),), ((507, 595),), (278, 279, 280)),
        ApexManualLesson("50차시", "part.2", ((245, 262),), ((507, 595),), (278, 279, 280)),
        ApexManualLesson("51차시", "part.2", ((245, 262),), ((507, 595),), (278, 279, 280)),
    ),
    "공통수학2": (
        ApexManualLesson("01차시", "part.1", ((3, 6),), ((1, 23),), (172,)),
        ApexManualLesson("02차시", "part.1", ((6, 7),), ((18, 29),), (172,)),
        ApexManualLesson("03차시", "part.1", ((8, 12),), ((30, 57),), (172,)),
        ApexManualLesson("04차시", "part.1", ((12, 14),), ((52, 66),), (172,)),
        ApexManualLesson("05차시", "part.1", ((15, 18),), ((67, 75),), (172, 173)),
        ApexManualLesson("06차시", "part.1", ((19, 22),), ((76, 98),), (173,)),
        ApexManualLesson("07차시", "part.1", ((23, 25),), ((99, 115),), (173,)),
        ApexManualLesson("08차시", "part.1", ((26, 28),), ((116, 133),), (173,)),
        ApexManualLesson("09차시", "part.1", ((28, 29),), ((128, 138),), (173,)),
        ApexManualLesson("10차시", "part.1", ((29, 34),), ((134, 150),), (173, 174)),
        ApexManualLesson("11차시", "part.1", ((35, 37),), ((151, 168),), (174,)),
        ApexManualLesson("12차시", "part.1", ((38, 40),), ((169, 185),), (174, 175)),
        ApexManualLesson("13차시", "part.1", ((40, 41),), ((181, 191),), (174, 175)),
        ApexManualLesson("14차시", "part.1", ((42, 44),), ((192, 207),), (174,)),
        ApexManualLesson("15차시", "part.1", ((44, 46),), ((203, 219),), (174, 175)),
        ApexManualLesson("16차시", "part.1", ((46, 47),), ((214, 225),), (174, 175)),
        ApexManualLesson("17차시", "part.1", ((48, 53),), ((226, 239),), (175,)),
        ApexManualLesson("18차시", "part.1", ((54, 58),), ((240, 266),), (175,)),
        ApexManualLesson("19차시", "part.1", ((58, 61),), ((262, 280),), (175,)),
        ApexManualLesson("20차시", "part.1", ((62, 65),), ((281, 296),), (175, 176)),
        ApexManualLesson("21차시", "part.1", ((65, 66),), ((292, 299),), (176,)),
        ApexManualLesson("22차시", "part.1", ((67, 72),), ((300, 311),), (176,)),
        ApexManualLesson("23차시", "part.1", ((73, 76),), ((312, 336),), (176,)),
        ApexManualLesson("24차시", "part.1", ((77, 79),), ((337, 357),), (176,)),
        ApexManualLesson("25차시", "part.1", ((79, 81),), ((352, 368),), (176, 177)),
        ApexManualLesson("26차시", "part.2", ((83, 94),), ((1, 65),), (178,)),
        ApexManualLesson("27차시", "part.2", ((83, 94),), ((1, 65),), (178,)),
        ApexManualLesson("28차시", "part.2", ((83, 94),), ((1, 65),), (178,)),
        ApexManualLesson("29차시", "part.2", ((83, 94),), ((1, 65),), (178,)),
        ApexManualLesson("30차시", "part.2", ((94, 99),), ((61, 77),), (178, 179)),
        ApexManualLesson("31차시", "part.2", ((100, 106),), ((78, 116),), (179,)),
        ApexManualLesson("32차시", "part.2", ((100, 106),), ((78, 116),), (179,)),
        ApexManualLesson("33차시", "part.2", ((107, 110),), ((117, 131),), (179,)),
        ApexManualLesson("34차시", "part.2", ((111, 114),), ((132, 156),), (179, 180)),
        ApexManualLesson("35차시", "part.2", ((115, 117),), ((157, 167),), (180,)),
        ApexManualLesson("36차시", "part.2", ((118, 121),), ((168, 189),), (180,)),
        ApexManualLesson("37차시", "part.2", ((122, 123),), ((190, 200),), (180,)),
        ApexManualLesson("38차시", "part.2", ((123, 126),), ((196, 217),), (180,)),
        ApexManualLesson("39차시", "part.2", ((126, 129),), ((213, 231),), (180, 181)),
        ApexManualLesson("40차시", "part.2", ((129, 134),), ((228, 244),), (181,)),
        ApexManualLesson("41차시", "part.2", ((135, 137),), ((245, 261),), (181,)),
        ApexManualLesson("42차시", "part.2", ((137, 144),), ((257, 298),), (181,)),
        ApexManualLesson("43차시", "part.2", ((137, 144),), ((257, 298),), (181,)),
        ApexManualLesson("44차시", "part.2", ((145, 147),), ((299, 314),), (182,)),
        ApexManualLesson("45차시", "part.2", ((148, 150),), ((315, 328),), (182,)),
        ApexManualLesson("46차시", "part.2", ((150, 158),), ((325, 348),), (182,)),
        ApexManualLesson("47차시", "part.2", ((159, 160),), ((349, 360),), (182,)),
        ApexManualLesson("48차시", "part.2", ((161, 168),), ((361, 394),), (182, 183)),
        ApexManualLesson("49차시", "part.2", ((161, 168),), ((361, 394),), (182, 183)),
        ApexManualLesson("50차시", "part.2", ((168, 171),), ((392, 400),), (183,)),
    ),
    "대수": (
        ApexManualLesson("01차시", "part.1", ((3, 6),), ((1, 23),), (154,)),
        ApexManualLesson("02차시", "part.1", ((7, 12),), ((24, 60),), (154,)),
        ApexManualLesson("03차시", "part.1", ((13, 17),), ((61, 92),), (154, 155)),
        ApexManualLesson("04차시", "part.1", ((13, 17),), ((61, 92),), (154, 155)),
        ApexManualLesson("05차시", "part.1", ((18, 20),), ((93, 107),), (155,)),
        ApexManualLesson("06차시", "part.1", ((21, 23),), ((108, 117),), (155,)),
        ApexManualLesson("07차시", "part.1", ((24, 26),), ((118, 129),), (155,)),
        ApexManualLesson("08차시", "part.1", ((27, 30),), ((130, 144),), (155, 156)),
        ApexManualLesson("09차시", "part.1", ((30, 31),), ((139, 150),), (155, 156)),
        ApexManualLesson("10차시", "part.1", ((31, 34),), ((145, 167),), (156,)),
        ApexManualLesson("11차시", "part.1", ((31, 34),), ((145, 167),), (156,)),
        ApexManualLesson("12차시", "part.1", ((35, 40),), ((168, 180),), (156,)),
        ApexManualLesson("13차시", "part.1", ((41, 44),), ((181, 201),), (156,)),
        ApexManualLesson("14차시", "part.1", ((43, 46),), ((191, 209),), (156,)),
        ApexManualLesson("15차시", "part.1", ((46, 47),), ((206, 215),), (156,)),
        ApexManualLesson("16차시", "part.1", ((48, 51),), ((216, 230),), (157,)),
        ApexManualLesson("17차시", "part.1", ((52, 54),), ((231, 246),), (157,)),
        ApexManualLesson("18차시", "part.1", ((54, 59),), ((243, 257),), (157,)),
        ApexManualLesson("19차시", "part.1", ((60, 62),), ((258, 273),), (157,)),
        ApexManualLesson("20차시", "part.1", ((62, 64),), ((267, 284),), (157,)),
        ApexManualLesson("21차시", "part.1", ((64, 70),), ((280, 314),), (157, 158)),
        ApexManualLesson("22차시", "part.1", ((64, 70),), ((280, 314),), (157, 158)),
        ApexManualLesson("23차시", "part.1", ((64, 70),), ((280, 314),), (157, 158)),
        ApexManualLesson("24차시", "part.2", ((72, 77),), ((1, 34),), (159,)),
        ApexManualLesson("25차시", "part.2", ((72, 77),), ((1, 34),), (159,)),
        ApexManualLesson("26차시", "part.2", ((77, 81),), ((27, 57),), (159,)),
        ApexManualLesson("27차시", "part.2", ((77, 81),), ((27, 57),), (159,)),
        ApexManualLesson("28차시", "part.2", ((82, 88),), ((58, 74),), (159, 160)),
        ApexManualLesson("29차시", "part.2", ((89, 91),), ((75, 91),), (160,)),
        ApexManualLesson("30차시", "part.2", ((92, 95),), ((92, 105),), (160,)),
        ApexManualLesson("31차시", "part.2", ((96, 98),), ((106, 112),), (160,)),
        ApexManualLesson("32차시", "part.2", ((99, 102),), ((113, 129),), (160,)),
        ApexManualLesson("33차시", "part.2", ((103, 107),), ((130, 139),), (160, 161)),
        ApexManualLesson("34차시", "part.2", ((108, 111),), ((140, 159),), (161,)),
        ApexManualLesson("35차시", "part.2", ((112, 115),), ((160, 182),), (161,)),
        ApexManualLesson("36차시", "part.2", ((115, 117),), ((177, 194),), (161,)),
        ApexManualLesson("37차시", "part.2", ((118, 121),), ((195, 216),), (161, 162)),
        ApexManualLesson("38차시", "part.2", ((118, 121),), ((195, 216),), (161, 162)),
        ApexManualLesson("39차시", "part.2", ((122, 125),), ((217, 226),), (162,)),
        ApexManualLesson("40차시", "part.2", ((126, 130),), ((227, 255),), (162,)),
        ApexManualLesson("41차시", "part.2", ((131, 133),), ((256, 273),), (162,)),
        ApexManualLesson("42차시", "part.2", ((133, 136),), ((268, 292),), (162, 163)),
        ApexManualLesson("43차시", "part.2", ((136, 140),), ((287, 305),), (162, 163)),
        ApexManualLesson("44차시", "part.2", ((141, 142),), ((306, 317),), (163,)),
        ApexManualLesson("45차시", "part.2", ((142, 146),), ((312, 335),), (163,)),
        ApexManualLesson("46차시", "part.2", ((146, 149),), ((333, 341),), (163,)),
        ApexManualLesson("47차시", "part.2", ((150, 153),), ((342, 350),), (163,)),
    ),
    "미적분1": (
        ApexManualLesson("01차시", "part.1", ((3, 6),), ((1, 17),), (157,)),
        ApexManualLesson("02차시", "part.1", ((7, 9),), ((18, 35),), (157,)),
        ApexManualLesson("03차시", "part.1", ((9, 11),), ((31, 45),), (157,)),
        ApexManualLesson("04차시", "part.1", ((11, 13),), ((40, 57),), (157,)),
        ApexManualLesson("05차시", "part.1", ((14, 16),), ((58, 73),), (157,)),
        ApexManualLesson("06차시", "part.1", ((16, 18),), ((70, 80),), (157, 158)),
        ApexManualLesson("07차시", "part.1", ((19, 24),), ((81, 93),), (158,)),
        ApexManualLesson("08차시", "part.1", ((25, 28),), ((94, 115),), (158,)),
        ApexManualLesson("09차시", "part.1", ((29, 33),), ((116, 138),), (158,)),
        ApexManualLesson("10차시", "part.1", ((33, 34),), ((135, 143),), (158,)),
        ApexManualLesson("11차시", "part.1", ((34, 39),), ((139, 154),), (158, 159)),
        ApexManualLesson("12차시", "part.1", ((40, 45),), ((155, 189),), (159,)),
        ApexManualLesson("13차시", "part.1", ((46, 47),), ((190, 201),), (159,)),
        ApexManualLesson("14차시", "part.1", ((48, 50),), ((202, 218),), (159,)),
        ApexManualLesson("15차시", "part.1", ((50, 52),), ((213, 226),), (159, 160)),
        ApexManualLesson("16차시", "part.1", ((52, 55),), ((223, 234),), (159, 160)),
        ApexManualLesson("17차시", "part.1", ((56, 65),), ((235, 285),), (160,)),
        ApexManualLesson("18차시", "part.1", ((56, 65),), ((235, 285),), (160,)),
        ApexManualLesson("19차시", "part.1", ((56, 65),), ((235, 285),), (160,)),
        ApexManualLesson("20차시", "part.1", ((65, 68),), ((283, 294),), (160,)),
        ApexManualLesson("21차시", "part.1", ((69, 70),), ((295, 305),), (160, 161)),
        ApexManualLesson("22차시", "part.1", ((71, 74),), ((306, 327),), (161,)),
        ApexManualLesson("23차시", "part.1", ((71, 76),), ((306, 337),), (161,)),
        ApexManualLesson("24차시", "part.1", ((77, 78),), ((338, 348),), (161,)),
        ApexManualLesson("25차시", "part.1", ((79, 86),), ((349, 370),), (161,)),
        ApexManualLesson("26차시", "part.2", ((88, 92),), ((1, 27),), (162,)),
        ApexManualLesson("27차시", "part.2", ((88, 92),), ((1, 27),), (162,)),
        ApexManualLesson("28차시", "part.2", ((92, 93),), ((23, 32),), (162,)),
        ApexManualLesson("29차시", "part.2", ((94, 97),), ((33, 50),), (162,)),
        ApexManualLesson("30차시", "part.2", ((97, 101),), ((48, 58),), (162,)),
        ApexManualLesson("31차시", "part.2", ((102, 105),), ((59, 82),), (162, 163)),
        ApexManualLesson("32차시", "part.2", ((105, 107),), ((77, 92),), (163,)),
        ApexManualLesson("33차시", "part.2", ((108, 109),), ((93, 103),), (163,)),
        ApexManualLesson("34차시", "part.2", ((109, 119),), ((98, 155),), (163, 164)),
        ApexManualLesson("35차시", "part.2", ((109, 119),), ((98, 155),), (163, 164)),
        ApexManualLesson("36차시", "part.2", ((109, 119),), ((98, 155),), (163, 164)),
        ApexManualLesson("37차시", "part.2", ((109, 119),), ((98, 155),), (163, 164)),
        ApexManualLesson("38차시", "part.2", ((109, 119),), ((98, 155),), (163, 164)),
        ApexManualLesson("39차시", "part.2", ((120, 126),), ((156, 192),), (164,)),
        ApexManualLesson("40차시", "part.2", ((120, 126),), ((156, 192),), (164,)),
        ApexManualLesson("41차시", "part.2", ((127, 135),), ((193, 216),), (164,)),
        ApexManualLesson("42차시", "part.2", ((127, 135),), ((193, 216),), (164,)),
        ApexManualLesson("43차시", "part.2", ((136, 147),), ((217, 267),), (164, 165)),
        ApexManualLesson("44차시", "part.2", ((136, 147),), ((217, 267),), (164, 165)),
        ApexManualLesson("45차시", "part.2", ((136, 147),), ((217, 267),), (164, 165)),
        ApexManualLesson("46차시", "part.2", ((147, 151),), ((264, 284),), (165,)),
        ApexManualLesson("47차시", "part.2", ((151, 156),), ((282, 297),), (165,)),
    ),
}


def merge_page_ranges(ranges: Iterable[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """Return sorted, overlapping/adjacent page ranges without duplicates."""
    pending = sorted((int(start), int(end)) for start, end in ranges)
    if not pending:
        return ()
    merged: list[tuple[int, int]] = [pending[0]]
    for start, end in pending[1:]:
        previous_start, previous_end = merged[-1]
        if start <= previous_end + 1:
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return tuple(merged)


def flatten_page_ranges(ranges: Iterable[tuple[int, int]]) -> tuple[int, ...]:
    pages: list[int] = []
    for start, end in merge_page_ranges(ranges):
        pages.extend(range(start, end + 1))
    return tuple(pages)


def format_page_ranges(ranges: Iterable[tuple[int, int]]) -> str:
    return ",".join(
        str(start) if start == end else f"{start}-{end}"
        for start, end in merge_page_ranges(ranges)
    )


def apex_manual_lesson_page_spans(subject: str) -> tuple[PageSpan, ...]:
    """Return manually verified APEX lesson spans, including split ranges."""
    rows = APEX_MANUAL_LESSON_PAGE_MAP.get(subject, ())
    lesson_names = APEX_LESSON_NAMES.get(subject, ())
    use_lesson_names = len(lesson_names) == len(rows)
    return tuple(
        PageSpan(
            number=index,
            label=lesson_names[index - 1] if use_lesson_names else row.label,
            start_page=min(start for start, _end in row.page_ranges),
            end_page=max(end for _start, end in row.page_ranges),
            part=row.part,
            page_ranges=row.page_ranges,
        )
        for index, row in enumerate(rows, start=1)
    )


def apex_manual_page_numbers(subject: str, lesson_numbers: Iterable[int]) -> tuple[int, ...]:
    """Merge selected APEX body pages, then append unique quick-answer pages."""
    rows = APEX_MANUAL_LESSON_PAGE_MAP.get(subject)
    selected = tuple(lesson_numbers)
    if not rows:
        raise ValueError(f"{subject}의 수동 APEX 차시 페이지 매핑이 없습니다.")
    if not selected:
        raise ValueError("선택된 APEX 차시가 없습니다.")
    if len(set(selected)) != len(selected):
        raise ValueError("APEX 차시가 중복 선택되었습니다.")

    row_by_number = {number: row for number, row in enumerate(rows, start=1)}
    try:
        selected_rows = tuple(row_by_number[number] for number in selected)
    except KeyError as error:
        raise ValueError(f"APEX {error.args[0]}차시 매핑이 없습니다.") from error

    body_pages = flatten_page_ranges(
        page_range
        for row in selected_rows
        for page_range in row.page_ranges
    )
    answer_pages: list[int] = []
    seen_pages = set(body_pages)
    for row in selected_rows:
        for page in row.answer_pages:
            if page not in seen_pages:
                answer_pages.append(page)
                seen_pages.add(page)
    return body_pages + tuple(answer_pages)


@dataclass(frozen=True, slots=True)
class ApexDocument:
    variant: str
    subject: str
    document_kind: str
    path: Path
    page_count: int
    parts: tuple[PageSpan, ...]
    lessons: tuple[PageSpan, ...]

    def lessons_for_part(self, part: str) -> tuple[PageSpan, ...]:
        if part == "전체":
            return self.lessons
        return tuple(span for span in self.lessons if span.part == part)


def apex_boost_lesson_page_spans(subject: str) -> tuple[PageSpan, ...]:
    """Return the documented APEX Boost body-page span for each lesson."""
    rows = APEX_BOOST_LESSON_PAGE_RANGES.get(subject, ())
    return tuple(
        PageSpan(number=index, label=label, start_page=start_page, end_page=end_page)
        for index, (label, start_page, end_page, _answer_page) in enumerate(rows, start=1)
    )


def apex_boost_answer_page(subject: str, lesson_number: int) -> int | None:
    """Return the documented quick-answer page for one APEX Boost lesson."""
    rows = APEX_BOOST_LESSON_PAGE_RANGES.get(subject, ())
    if lesson_number < 1 or lesson_number > len(rows):
        return None
    return rows[lesson_number - 1][3]


def apex_boost_page_numbers(subject: str, lesson_numbers: Iterable[int]) -> tuple[int, ...]:
    """Expand lesson numbers into body pages followed by each answer page."""
    selected = tuple(lesson_numbers)
    rows = APEX_BOOST_LESSON_PAGE_RANGES.get(subject)
    if not rows:
        raise ValueError(f"{subject}의 APEX Boost 차시 페이지 매핑이 없습니다.")
    if not selected:
        raise ValueError("선택된 APEX Boost 차시가 없습니다.")

    page_numbers: list[int] = []
    for lesson_number in selected:
        if lesson_number < 1 or lesson_number > len(rows):
            raise ValueError(f"{subject}의 APEX Boost {lesson_number}차시 매핑이 없습니다.")
        _label, start_page, end_page, answer_page = rows[lesson_number - 1]
        page_numbers.extend(range(start_page, end_page + 1))
        page_numbers.append(answer_page)
    return tuple(page_numbers)


def apply_apex_boost_page_map(document: ApexDocument) -> ApexDocument:
    """Replace auto-detected Boost lesson spans when the supplied map fits."""
    if document.variant != "APEX Boost" or document.document_kind != "교재":
        return document
    mapped_lessons = apex_boost_lesson_page_spans(document.subject)
    if not mapped_lessons:
        return document
    try:
        mapped_pages = apex_boost_page_numbers(document.subject, (span.number for span in mapped_lessons))
    except ValueError:
        return document
    if max(mapped_pages) > document.page_count:
        return document
    return ApexDocument(
        variant=document.variant,
        subject=document.subject,
        document_kind=document.document_kind,
        path=document.path,
        page_count=document.page_count,
        parts=document.parts,
        lessons=mapped_lessons,
    )


def apply_apex_manual_page_map(document: ApexDocument) -> ApexDocument:
    """Replace auto-detected APEX lesson spans when a verified map exists."""
    if document.variant != "APEX" or document.document_kind != "교재":
        return document
    mapped_lessons = apex_manual_lesson_page_spans(document.subject)
    if not mapped_lessons:
        return document
    try:
        mapped_pages = apex_manual_page_numbers(document.subject, (span.number for span in mapped_lessons))
    except ValueError:
        return document
    if max(mapped_pages) > document.page_count:
        return document
    return ApexDocument(
        variant=document.variant,
        subject=document.subject,
        document_kind=document.document_kind,
        path=document.path,
        page_count=document.page_count,
        parts=document.parts,
        lessons=mapped_lessons,
    )


def apply_apex_page_map(document: ApexDocument) -> ApexDocument:
    """Apply the verified page map for the matching APEX variant and subject."""
    return apply_apex_manual_page_map(apply_apex_boost_page_map(document))


@dataclass(frozen=True, slots=True)
class ApexCatalog:
    documents: tuple[ApexDocument, ...]

    @property
    def variants(self) -> tuple[str, ...]:
        return tuple(sorted({document.variant for document in self.documents}, key=lambda value: ("Boost" in value, value)))

    def subjects(self, variant: str) -> tuple[str, ...]:
        return tuple(sorted({document.subject for document in self.documents if document.variant == variant}, key=_natural_key))

    def find(self, variant: str, subject: str, document_kind: str) -> ApexDocument | None:
        return next(
            (
                document
                for document in self.documents
                if document.variant == variant
                and document.subject == subject
                and document.document_kind == document_kind
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class AbleSubject:
    subject: str
    full_documents: tuple[Path, ...]
    lesson_documents: tuple[tuple[int, Path], ...]
    review_document: Path | None
    lesson_page_spans: tuple[PageSpan, ...] = ()

    @property
    def lesson_numbers(self) -> tuple[int, ...]:
        if self.lesson_page_spans:
            return tuple(span.number for span in self.lesson_page_spans)
        return tuple(number for number, _path in self.lesson_documents)

    def lesson_path(self, lesson_number: int) -> Path | None:
        return next((path for number, path in self.lesson_documents if number == lesson_number), None)

    def lesson_span(self, lesson_number: int) -> PageSpan | None:
        return next((span for span in self.lesson_page_spans if span.number == lesson_number), None)

    @property
    def combined_document(self) -> Path | None:
        return next((path for path in self.full_documents if "Able 합본" in path.stem), None)

    @property
    def concept_document(self) -> Path | None:
        return next((path for path in self.full_documents if "개념정리노트" in path.stem), None)

    @property
    def full_set_documents(self) -> tuple[Path, ...]:
        """Return the three documents required for a complete A:ble set."""
        concept = self.concept_document
        combined = self.combined_document
        review = self.review_document
        if concept is None or combined is None or review is None:
            return ()
        return concept, combined, review


@dataclass(frozen=True, slots=True)
class AbleCatalog:
    subjects: tuple[AbleSubject, ...]

    @property
    def subject_names(self) -> tuple[str, ...]:
        return tuple(subject.subject for subject in self.subjects)

    def find(self, subject: str) -> AbleSubject | None:
        return next((item for item in self.subjects if item.subject == subject), None)


@dataclass(frozen=True, slots=True)
class TrainingPackSubject:
    """One problem-only training-pack PDF and its verified lesson map."""

    subject: str
    path: Path
    page_count: int
    lessons: tuple[PageSpan, ...]

    @property
    def lesson_numbers(self) -> tuple[int, ...]:
        return tuple(span.number for span in self.lessons)

    def lesson_span(self, lesson_number: int) -> PageSpan | None:
        return next((span for span in self.lessons if span.number == lesson_number), None)


@dataclass(frozen=True, slots=True)
class TrainingPackCatalog:
    subjects: tuple[TrainingPackSubject, ...]

    @property
    def subject_names(self) -> tuple[str, ...]:
        return tuple(item.subject for item in self.subjects)

    def find(self, subject: str) -> TrainingPackSubject | None:
        return next((item for item in self.subjects if item.subject == subject), None)


def parse_apex_filename(path: Path) -> tuple[str, str, str] | None:
    if path.suffix.casefold() != ".pdf":
        return None
    match = _APEX_FILENAME.fullmatch(path.stem.strip())
    if match is None:
        return None
    variant = "APEX Boost" if match.group("boost") else "APEX"
    subject = _normalize_subject(match.group("subject"))
    document_kind = "해설" if match.group("solution") else "교재"
    return variant, subject, document_kind


def parse_able_lesson_filename(path: Path) -> tuple[str, int] | None:
    if path.suffix.casefold() != ".pdf":
        return None
    match = _ABLE_LESSON_FILENAME.fullmatch(path.stem.strip())
    if match is None:
        return None
    return _normalize_subject(match.group("subject")), int(match.group("lesson"))


def _load_lesson_material_page_map(
    page_map_path: Path,
    subject_from_filename: Callable[[str], str | None],
) -> dict[str, tuple[PageSpan, ...]]:
    """Read verified lesson ranges from a companion Markdown page map."""
    if not page_map_path.exists():
        return {}

    heading_pattern = re.compile(r"^##\s+(.+?\.pdf)(?:\s+\(\d+p\))?\s*$", re.IGNORECASE)
    row_pattern = re.compile(
        r"^\|\s*(\d+)\s*차시\s*\|\s*(.*?)\s*\|\s*p\.\s*(\d+)\s*(?:-\s*(\d+))?\s*\|\s*$",
        re.IGNORECASE,
    )
    page_map: dict[str, tuple[PageSpan, ...]] = {}
    current_subject: str | None = None
    current_spans: list[PageSpan] = []

    def flush() -> None:
        if current_subject is not None and current_spans:
            page_map[current_subject] = tuple(sorted(current_spans, key=lambda span: span.number))

    try:
        lines = page_map_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}

    for raw_line in lines:
        heading = heading_pattern.match(raw_line.strip())
        if heading:
            flush()
            current_subject = subject_from_filename(heading.group(1))
            current_spans = []
            continue
        if current_subject is None:
            continue
        row = row_pattern.match(raw_line.strip())
        if row is None:
            continue
        number = int(row.group(1))
        start_page = int(row.group(3))
        end_page = int(row.group(4) or start_page)
        current_spans.append(
            PageSpan(
                number=number,
                label=row.group(2).strip(),
                start_page=start_page,
                end_page=end_page,
            )
        )
    flush()
    return page_map


def load_training_pack_page_map(
    page_map_path: Path = DEFAULT_TRAINING_PACK_PAGE_MAP_PATH,
) -> dict[str, tuple[PageSpan, ...]]:
    """Read verified training-pack lesson ranges."""
    return _load_lesson_material_page_map(page_map_path, _training_pack_subject_from_filename)


def load_frequent_type_page_map(
    page_map_path: Path = DEFAULT_FREQUENT_TYPE_PAGE_MAP_PATH,
) -> dict[str, tuple[PageSpan, ...]]:
    """Read verified frequent-type lesson ranges."""
    return _load_lesson_material_page_map(page_map_path, _frequent_type_subject_from_filename)


def _lesson_material_page_numbers(
    subject: TrainingPackSubject,
    lesson_numbers: Iterable[int],
    material_label: str,
) -> tuple[int, ...]:
    """Expand selected lesson numbers into ordered, 1-based PDF pages."""
    selected = tuple(lesson_numbers)
    if not selected:
        raise ValueError(f"선택된 {material_label} 차시가 없습니다.")
    available = set(subject.lesson_numbers)
    missing = [number for number in selected if number not in available]
    if missing:
        missing_label = ", ".join(str(number) for number in missing)
        raise ValueError(f"{subject.subject} {material_label}에 없는 차시입니다: {missing_label}")
    if len(set(selected)) != len(selected):
        raise ValueError(f"{material_label} 차시를 중복해서 선택할 수 없습니다.")
    spans = [subject.lesson_span(number) for number in selected]
    if any(span is None for span in spans):
        raise ValueError(f"{material_label} 차시 페이지 매핑을 찾을 수 없습니다.")
    pages: list[int] = []
    for span in spans:
        if span is None:
            continue
        for start_page, end_page in span.effective_page_ranges:
            pages.extend(range(start_page, end_page + 1))
    return tuple(pages)


def training_pack_page_numbers(
    subject: TrainingPackSubject,
    lesson_numbers: Iterable[int],
) -> tuple[int, ...]:
    return _lesson_material_page_numbers(subject, lesson_numbers, "트레이닝팩")


def frequent_type_page_numbers(
    subject: TrainingPackSubject,
    lesson_numbers: Iterable[int],
) -> tuple[int, ...]:
    return _lesson_material_page_numbers(subject, lesson_numbers, "빈출유형")


def _scan_lesson_material_catalog(
    root: Path,
    page_map: dict[str, tuple[PageSpan, ...]],
    subject_from_filename: Callable[[str], str | None],
) -> TrainingPackCatalog:
    if not root.exists() or not page_map:
        return TrainingPackCatalog(())

    subjects: list[TrainingPackSubject] = []
    for path in root.glob("*.pdf"):
        if "해설" in path.stem:
            continue
        subject = subject_from_filename(path.name)
        spans = page_map.get(subject)
        if not subject or not spans:
            continue
        try:
            page_count = get_pdf_page_count(path)
        except Exception:
            continue
        if max(span.end_page for span in spans) > page_count:
            continue
        subjects.append(TrainingPackSubject(subject, path, page_count, spans))

    return TrainingPackCatalog(
        tuple(sorted(subjects, key=lambda item: _natural_key(item.subject)))
    )


def scan_training_pack_catalog(
    root: Path = DEFAULT_TRAINING_PACK_ROOT,
    page_map_path: Path = DEFAULT_TRAINING_PACK_PAGE_MAP_PATH,
) -> TrainingPackCatalog:
    """Find training-pack PDFs and attach only verified lesson maps."""
    return _scan_lesson_material_catalog(
        root,
        load_training_pack_page_map(page_map_path),
        _training_pack_subject_from_filename,
    )


def scan_frequent_type_catalog(
    root: Path = DEFAULT_FREQUENT_TYPE_ROOT,
    page_map_path: Path = DEFAULT_FREQUENT_TYPE_PAGE_MAP_PATH,
) -> TrainingPackCatalog:
    """Find frequent-type PDFs and attach only verified lesson maps."""
    return _scan_lesson_material_catalog(
        root,
        load_frequent_type_page_map(page_map_path),
        _frequent_type_subject_from_filename,
    )


def able_lesson_page_spans(subject: str) -> tuple[PageSpan, ...]:
    """Return the supplied 1-based lecture-note page ranges for one subject."""
    normalized_subject = re.sub(r"\s+", "", _normalize_subject(subject))
    ranges = next(
        (
            page_ranges
            for mapped_subject, page_ranges in ABLE_LESSON_PAGE_RANGES.items()
            if re.sub(r"\s+", "", mapped_subject) == normalized_subject
        ),
        (),
    )
    return tuple(
        PageSpan(number=index, label=f"{index:02d}차시", start_page=start, end_page=end)
        for index, (start, end) in enumerate(ranges, start=1)
    )


def able_document_print_settings(path: Path, display_name: str = "") -> tuple[str, int]:
    """Return the printer profile and pages-per-sheet for one A:ble document."""
    names = (path.stem.casefold(), display_name.casefold())
    is_combined = any("합본" in name for name in names)
    is_lesson_note = parse_able_lesson_filename(path) is not None or any(
        "렉쳐노트" in name for name in names
    )
    if is_combined or is_lesson_note:
        return ABLE_PROFILE_NAME, 4
    return APEX_PROFILE_NAME, 1


def scan_able_catalog(
    root: Path = DEFAULT_ABLE_ROOT,
    final_root: Path = DEFAULT_ABLE_FINAL_ROOT,
) -> AbleCatalog:
    lesson_paths: dict[str, dict[int, Path]] = {}
    if root.exists():
        for path in root.rglob("*.pdf"):
            if final_root in path.parents:
                continue
            parsed = parse_able_lesson_filename(path)
            if parsed is None:
                continue
            subject, lesson = parsed
            lesson_paths.setdefault(subject, {})[lesson] = path

    full_paths: dict[str, list[Path]] = {}
    reviews: dict[str, Path] = {}
    if final_root.exists():
        for path in final_root.glob("*.pdf"):
            subject = _subject_from_able_full_filename(path.name)
            if subject is None:
                continue
            if "백지" in path.stem:
                reviews[subject] = path
            else:
                full_paths.setdefault(subject, []).append(path)

    subject_names = sorted(set(lesson_paths) | set(full_paths) | set(reviews), key=_natural_key)
    subjects = tuple(
        _make_able_subject(subject, full_paths, lesson_paths, reviews)
        for subject in subject_names
    )
    return AbleCatalog(subjects)


def _make_able_subject(
    subject: str,
    full_paths: dict[str, list[Path]],
    lesson_paths: dict[str, dict[int, Path]],
    reviews: dict[str, Path],
) -> AbleSubject:
    full_documents = tuple(sorted(full_paths.get(subject, []), key=lambda path: _able_full_order(path.name)))
    has_combined_document = any("Able 합본" in path.stem for path in full_documents)
    return AbleSubject(
        subject=subject,
        full_documents=full_documents,
        lesson_documents=tuple(sorted(lesson_paths.get(subject, {}).items())),
        review_document=reviews.get(subject),
        lesson_page_spans=able_lesson_page_spans(subject) if has_combined_document else (),
    )


def load_apex_catalog(root: Path, cache_path: Path) -> ApexCatalog:
    cache = _read_cache(cache_path)
    changed = False
    documents: list[ApexDocument] = []
    if not root.exists():
        return ApexCatalog(())
    for path in sorted(root.glob("*.pdf"), key=lambda value: _natural_key(value.name)):
        parsed = parse_apex_filename(path)
        if parsed is None:
            continue
        variant, subject, document_kind = parsed
        stat = path.stat()
        cache_key = path.relative_to(root).as_posix()
        legacy_cache_key = str(path.resolve())
        cached = cache.get(cache_key, cache.get(legacy_cache_key))
        if not _cache_entry_matches(cached, stat.st_mtime_ns, stat.st_size):
            document = analyze_apex_document(path, variant, subject, document_kind)
            cache[cache_key] = _document_to_cache(document, stat.st_mtime_ns, stat.st_size)
            changed = True
        else:
            try:
                document = _document_from_cache(path, variant, subject, document_kind, cached)
                if cache_key not in cache or _cache_entry_is_portable(cached):
                    cache[cache_key] = _document_to_cache(document, stat.st_mtime_ns, stat.st_size)
                    changed = True
            except (KeyError, TypeError, ValueError):
                document = analyze_apex_document(path, variant, subject, document_kind)
                cache[cache_key] = _document_to_cache(document, stat.st_mtime_ns, stat.st_size)
                changed = True
        mapped_document = apply_apex_page_map(document)
        if mapped_document != document:
            document = mapped_document
            cache[cache_key] = _document_to_cache(document, stat.st_mtime_ns, stat.st_size)
            changed = True
        documents.append(document)
    if changed:
        _write_cache(cache_path, cache)
    return ApexCatalog(tuple(documents))


def analyze_apex_document(path: Path, variant: str, subject: str, document_kind: str) -> ApexDocument:
    reader = PdfReader(str(path))
    page_texts = tuple(_compact_text(page.extract_text() or "") for page in reader.pages)
    page_count = len(page_texts)
    if page_count < 1:
        raise ValueError(f"페이지가 없는 PDF입니다: {path}")

    answer_start = _first_answer_page(page_texts) if document_kind == "교재" else None
    content_end = (answer_start - 1) if answer_start is not None else page_count
    reset_pages = _question_reset_pages(page_texts, content_end)

    if variant == "APEX" and len(reset_pages) >= 2:
        part_starts = (reset_pages[0], reset_pages[1])
        parts = (
            PageSpan(1, "part.1", 1, part_starts[1] - 1, "part.1"),
            PageSpan(2, "part.2", part_starts[1], content_end, "part.2"),
        )
    else:
        parts = (PageSpan(1, "전체", 1, content_end, "전체"),)

    lessons: list[PageSpan] = []
    lesson_number = 1
    for part in parts:
        if variant == "APEX Boost":
            starts = [page for page in reset_pages if part.start_page <= page <= part.end_page]
            labels = {page: f"{lesson_number + index:02d}차시" for index, page in enumerate(starts)}
        else:
            starts, labels = _topic_starts(page_texts, part.start_page, part.end_page)
        if not starts:
            starts = [max(1, part.start_page)]
            labels = {starts[0]: f"{lesson_number:02d}차시"}
        for index, start_page in enumerate(starts):
            end_page = starts[index + 1] - 1 if index + 1 < len(starts) else part.end_page
            label = labels.get(start_page, f"{lesson_number:02d}차시")
            lessons.append(PageSpan(lesson_number, label, start_page, end_page, part.part))
            lesson_number += 1

    document = ApexDocument(
        variant=variant,
        subject=subject,
        document_kind=document_kind,
        path=path,
        page_count=page_count,
        parts=parts,
        lessons=tuple(lessons),
    )
    return apply_apex_page_map(document)


def _write_pdf_atomically(writer: PdfWriter, output_path: Path) -> None:
    """Publish a complete cache file even when two app instances race."""

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{output_path.stem}_",
            suffix=".tmp.pdf",
            dir=output_path.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            writer.write(temporary_file)
        lock_index = int.from_bytes(
            hashlib.blake2b(
                str(output_path.resolve()).casefold().encode("utf-8"),
                digest_size=2,
            ).digest(),
            "big",
        ) % len(_PDF_CACHE_PUBLISH_LOCKS)
        with _PDF_CACHE_PUBLISH_LOCKS[lock_index]:
            if output_path.exists():
                return
            try:
                temporary_path.replace(output_path)
            except PermissionError:
                # A separate app process can win between exists() and
                # replace().  Its target is complete because publication is
                # atomic and the cache key describes identical inputs.
                if not output_path.exists():
                    raise
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink(missing_ok=True)


def _stat_signature(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def _ensure_signatures_unchanged(
    expected: tuple[tuple[Path, tuple[int, int]], ...],
    output_path: Path,
) -> None:
    changed = [path for path, signature in expected if _stat_signature(path) != signature]
    if changed:
        output_path.unlink(missing_ok=True)
        raise OSError("PDF 준비 중 원본이 변경되었습니다: " + ", ".join(map(str, changed)))


def materialize_pdf_span(source: Path, span: PageSpan, cache_dir: Path, label: str) -> Path:
    source_stat = source.stat()
    source_signature = (source_stat.st_size, source_stat.st_mtime_ns)
    digest_source = (
        f"{source.resolve()}|{source_stat.st_size}|{source_stat.st_mtime_ns}|"
        f"{span.start_page}|{span.end_page}"
    )
    digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:12]
    safe_label = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", label).strip("_") or "range"
    output_path = cache_dir / f"{safe_label}_{digest}.pdf"
    if output_path.exists():
        return output_path
    reader = PdfReader(str(source))
    page_count = len(reader.pages)
    if span.start_page == 1 and span.end_page == page_count:
        return source
    if span.end_page > page_count:
        raise ValueError(f"{source.name}의 페이지 범위를 벗어났습니다: {span.start_page}-{span.end_page}/{page_count}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    for page_index in range(span.start_page - 1, span.end_page):
        _ = writer.add_page(reader.pages[page_index])
    _write_pdf_atomically(writer, output_path)
    _ensure_signatures_unchanged(((source, source_signature),), output_path)
    return output_path


def materialize_pdf_pages(source: Path, page_numbers: Iterable[int], cache_dir: Path, label: str) -> Path:
    """Write an ordered, possibly non-contiguous page selection to a cached PDF."""
    selected = tuple(page_numbers)
    if not selected:
        raise ValueError("선택된 PDF 페이지가 없습니다.")
    if any(page_number < 1 for page_number in selected):
        raise ValueError(f"{source.name}의 페이지 범위를 벗어났습니다: {selected}")
    source_stat = source.stat()
    source_signature = (source_stat.st_size, source_stat.st_mtime_ns)
    page_key = ",".join(str(page_number) for page_number in selected)
    digest_source = (
        f"{source.resolve()}|{source_stat.st_size}|{source_stat.st_mtime_ns}|{page_key}"
    )
    digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:12]
    safe_label = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", label).strip("_") or "pages"
    output_path = cache_dir / f"{safe_label}_{digest}.pdf"
    if output_path.exists():
        return output_path

    reader = PdfReader(str(source))
    page_count = len(reader.pages)
    if any(page_number > page_count for page_number in selected):
        raise ValueError(f"{source.name}의 페이지 범위를 벗어났습니다: {selected}/{page_count}")
    if selected == tuple(range(1, page_count + 1)):
        return source
    cache_dir.mkdir(parents=True, exist_ok=True)

    writer = PdfWriter()
    for page_number in selected:
        _ = writer.add_page(reader.pages[page_number - 1])
    _write_pdf_atomically(writer, output_path)
    _ensure_signatures_unchanged(((source, source_signature),), output_path)
    return output_path


def materialize_pdf_documents(sources: Iterable[Path], cache_dir: Path, label: str) -> Path:
    """Merge complete PDFs in order into one cached print document."""
    selected = tuple(sources)
    if not selected:
        raise ValueError("병합할 PDF가 없습니다.")
    if len(selected) == 1:
        return selected[0]
    signatures: list[str] = []
    expected_signatures: list[tuple[Path, tuple[int, int]]] = []
    for source in selected:
        stat = source.stat()
        signature = (stat.st_size, stat.st_mtime_ns)
        expected_signatures.append((source, signature))
        signatures.append(
            f"{source.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
        )
    digest = hashlib.sha256("||".join(signatures).encode("utf-8")).hexdigest()[:12]
    safe_label = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", label).strip("_") or "merged"
    output_path = cache_dir / f"{safe_label}_{digest}.pdf"
    if output_path.exists():
        return output_path

    cache_dir.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    for source in selected:
        reader = PdfReader(str(source))
        for page in reader.pages:
            _ = writer.add_page(page)
    _write_pdf_atomically(writer, output_path)
    _ensure_signatures_unchanged(tuple(expected_signatures), output_path)
    return output_path


def combined_span(spans: Iterable[PageSpan], label: str, part: str = "전체") -> PageSpan:
    selected = tuple(spans)
    if not selected:
        raise ValueError("선택된 차시가 없습니다.")
    return PageSpan(
        number=selected[0].number,
        label=label,
        start_page=min(span.start_page for span in selected),
        end_page=max(span.end_page for span in selected),
        part=part,
    )


def _first_answer_page(page_texts: tuple[str, ...]) -> int | None:
    for page_number, text in enumerate(page_texts, start=1):
        if "빠른 정답" in text or text.count("정답") >= 8:
            return page_number
    return None


def _question_reset_pages(page_texts: tuple[str, ...], content_end: int) -> list[int]:
    pages: list[int] = []
    for page_number, text in enumerate(page_texts[:content_end], start=1):
        head = text[:700]
        if "정답" in head or not _QUESTION_ONE.search(head):
            continue
        if not pages or page_number - pages[-1] >= 3:
            pages.append(page_number)
    return pages


def _topic_starts(
    page_texts: tuple[str, ...],
    start_page: int,
    end_page: int,
) -> tuple[list[int], dict[int, str]]:
    starts: list[int] = []
    labels: dict[int, str] = {}
    for page_number in range(start_page, end_page + 1):
        match = _TOPIC_HEADER.search(page_texts[page_number - 1][:700])
        if match is None:
            continue
        if page_number not in starts:
            starts.append(page_number)
        chapter_title = _compact_text(match.group("chapter_title")).strip(" .")
        title = _compact_text(match.group("title")).strip(" .")
        labels[page_number] = f"{match.group('chapter')}.{chapter_title} {match.group('topic')} {title}".strip()
    return starts, labels


def _subject_from_able_full_filename(filename: str) -> str | None:
    stem = Path(filename).stem.strip()
    if "Able 합본" in stem:
        return _normalize_subject(stem.split("Able 합본", 1)[0])
    if "개념정리노트" in stem:
        suffix = re.split(r"[-_]", stem, maxsplit=1)
        return _normalize_subject(suffix[-1])
    if "백지" in stem:
        suffix = stem.split("-", 1)
        if len(suffix) == 2:
            return _normalize_subject(suffix[1])
    return None


def _training_pack_subject_from_filename(filename: str) -> str | None:
    stem = Path(filename).stem.strip()
    match = re.match(r"^(?P<subject>.+?)\s+(?:트레이닝팩|트팩)$", stem, re.IGNORECASE)
    if match is None:
        return None
    return _normalize_subject(match.group("subject"))


def _frequent_type_subject_from_filename(filename: str) -> str | None:
    stem = Path(filename).stem.strip()
    match = re.match(r"^(?P<subject>.+?)\s+빈출유형$", stem, re.IGNORECASE)
    if match is None:
        return None
    return _normalize_subject(match.group("subject"))


def _able_full_order(filename: str) -> tuple[int, tuple[object, ...]]:
    if "개념정리노트" in filename:
        order = 0
    elif "Able 합본" in filename:
        order = 1
    else:
        order = 2
    return order, _natural_key(filename)


def _normalize_subject(value: str) -> str:
    compact = re.sub(r"\s+", "", value)
    aliases = {
        "공수1": "공통수학1",
        "공수2": "공통수학2",
        "미적1": "미적분1",
        "미적2": "미적분2",
        "미적분I": "미적분1",
        "확통": "확률과 통계",
    }
    return aliases.get(compact, compact)


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _natural_key(value: str) -> tuple[object, ...]:
    return tuple(int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value))


def _read_cache(cache_path: Path) -> dict[str, object]:
    if not cache_path.exists():
        return {}
    try:
        payload = cast(object, json.loads(cache_path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    result: dict[str, object] = {}
    for key, value in cast(dict[object, object], payload).items():
        if isinstance(key, str):
            result[key] = value
    return result


def _write_cache(cache_path: Path, cache: dict[str, object]) -> None:
    _ = cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _cache_entry_matches(entry: object, mtime_ns: int, size: int) -> bool:
    if not isinstance(entry, dict):
        return False
    typed_entry = cast(dict[object, object], entry)
    if typed_entry.get("portable") is True:
        return typed_entry.get("size") == size
    return typed_entry.get("mtime_ns") == mtime_ns and typed_entry.get("size") == size


def _cache_entry_is_portable(entry: object) -> bool:
    return isinstance(entry, dict) and cast(dict[object, object], entry).get("portable") is True


def _document_to_cache(document: ApexDocument, mtime_ns: int, size: int) -> dict[str, object]:
    return {
        "mtime_ns": mtime_ns,
        "size": size,
        "page_count": document.page_count,
        "parts": [_span_to_dict(span) for span in document.parts],
        "lessons": [_span_to_dict(span) for span in document.lessons],
    }


def _document_from_cache(
    path: Path,
    variant: str,
    subject: str,
    document_kind: str,
    entry: object,
) -> ApexDocument:
    if not isinstance(entry, dict):
        raise ValueError(f"잘못된 APEX 인덱스입니다: {path}")
    typed_entry = cast(dict[object, object], entry)
    raw_page_count = typed_entry.get("page_count")
    raw_parts = typed_entry.get("parts")
    raw_lessons = typed_entry.get("lessons")
    if not isinstance(raw_page_count, int) or not isinstance(raw_parts, list) or not isinstance(raw_lessons, list):
        raise ValueError(f"잘못된 APEX 인덱스입니다: {path}")
    return ApexDocument(
        variant=variant,
        subject=subject,
        document_kind=document_kind,
        path=path,
        page_count=raw_page_count,
        parts=tuple(_span_from_dict(item) for item in cast(list[object], raw_parts)),
        lessons=tuple(_span_from_dict(item) for item in cast(list[object], raw_lessons)),
    )


def _span_to_dict(span: PageSpan) -> dict[str, object]:
    return {
        "number": span.number,
        "label": span.label,
        "start_page": span.start_page,
        "end_page": span.end_page,
        "part": span.part,
        "page_ranges": [list(page_range) for page_range in span.effective_page_ranges],
    }


def _span_from_dict(entry: object) -> PageSpan:
    if not isinstance(entry, dict):
        raise ValueError("잘못된 페이지 범위 인덱스입니다.")
    typed_entry = cast(dict[object, object], entry)
    raw_number = typed_entry.get("number")
    raw_label = typed_entry.get("label")
    raw_start = typed_entry.get("start_page")
    raw_end = typed_entry.get("end_page")
    raw_part = typed_entry.get("part", "전체")
    raw_page_ranges = typed_entry.get("page_ranges")
    if not isinstance(raw_number, int) or not isinstance(raw_label, str):
        raise ValueError("잘못된 페이지 범위 인덱스입니다.")
    if not isinstance(raw_start, int) or not isinstance(raw_end, int) or not isinstance(raw_part, str):
        raise ValueError("잘못된 페이지 범위 인덱스입니다.")
    if raw_page_ranges is None:
        page_ranges: tuple[tuple[int, int], ...] = ()
    else:
        if not isinstance(raw_page_ranges, list):
            raise ValueError("잘못된 페이지 범위 인덱스입니다.")
        try:
            page_ranges = tuple(
                (int(page_range[0]), int(page_range[1]))
                for page_range in raw_page_ranges
                if isinstance(page_range, (list, tuple)) and len(page_range) == 2
            )
        except (TypeError, ValueError, IndexError) as error:
            raise ValueError("잘못된 페이지 범위 인덱스입니다.") from error
        if len(page_ranges) != len(raw_page_ranges):
            raise ValueError("잘못된 페이지 범위 인덱스입니다.")
    return PageSpan(
        number=raw_number,
        label=raw_label,
        start_page=raw_start,
        end_page=raw_end,
        part=raw_part,
        page_ranges=page_ranges,
    )
