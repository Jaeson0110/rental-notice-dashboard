from __future__ import annotations

import re
import time
from datetime import date

from bs4 import BeautifulSoup

from collector.models import Notice
from collector.sources.common import collect_pages, fetch_url

# 서울주거포털의 통합 목록과 유형별 목록을 함께 확인합니다.
SH_URLS = [
    "https://housing.seoul.go.kr/site/main/sh/publicLease/list",
    *[
        f"https://housing.seoul.go.kr/site/main/sh/publicLease/{index:02d}/list"
        for index in range(1, 10)
    ],
]

FULL_DATE_RE = re.compile(
    r"(?P<year>20\d{2})\s*[.\-/년]\s*"
    r"(?P<month>\d{1,2})\s*[.\-/월]\s*"
    r"(?P<day>\d{1,2})\s*(?:일)?"
)

DATE_RANGE_RE = re.compile(
    r"(?P<sy>20\d{2})\s*[.\-/년]\s*"
    r"(?P<sm>\d{1,2})\s*[.\-/월]\s*"
    r"(?P<sd>\d{1,2})\s*(?:일)?"
    r"[^0-9]{0,24}(?:~|∼|～|부터|[-–—])\s*"
    r"(?:(?P<ey>20\d{2})\s*[.\-/년]\s*)?"
    r"(?P<em>\d{1,2})\s*[.\-/월]\s*"
    r"(?P<ed>\d{1,2})\s*(?:일)?"
)

APPLICATION_LABELS = (
    "인터넷접수",
    "온라인접수",
    "방문접수",
    "현장접수",
    "청약접수",
    "신청접수",
    "신청기간",
    "접수기간",
    "청약기간",
    "접수일",
    "신청일정",
    "청약일정",
)

# 아래 날짜는 신청 마감일이 아니므로 접수기간 후보에서 제외합니다.
EXCLUDED_LABELS = (
    "서류",
    "당첨",
    "발표",
    "계약",
    "입주",
    "소인",
    "도착",
    "심사",
)


def _clean_lines(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    lines: list[str] = []

    for raw in soup.get_text("\n", strip=True).splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if line:
            lines.append(line)

    return lines


def _make_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _extract_date_range(text: str) -> tuple[str, str] | None:
    range_match = DATE_RANGE_RE.search(text)

    if range_match:
        start = _make_date(
            int(range_match.group("sy")),
            int(range_match.group("sm")),
            int(range_match.group("sd")),
        )
        if start is None:
            return None

        end_year = (
            int(range_match.group("ey"))
            if range_match.group("ey")
            else start.year
        )
        end_month = int(range_match.group("em"))
        end_day = int(range_match.group("ed"))

        # 종료일에 연도가 생략되고 12월에서 1월로 넘어가는 경우를 보정합니다.
        if not range_match.group("ey") and end_month < start.month:
            end_year += 1

        end = _make_date(end_year, end_month, end_day)
        if end is None or end < start:
            return None

        return start.isoformat(), end.isoformat()

    single_match = FULL_DATE_RE.search(text)
    if not single_match:
        return None

    single = _make_date(
        int(single_match.group("year")),
        int(single_match.group("month")),
        int(single_match.group("day")),
    )
    if single is None:
        return None

    value = single.isoformat()
    return value, value


def _extract_application_period(html: str) -> tuple[str, str] | None:
    lines = _clean_lines(html)
    periods: list[tuple[str, str]] = []

    for index, line in enumerate(lines):
        compact = line.replace(" ", "")

        if not any(label in compact for label in APPLICATION_LABELS):
            continue

        if any(label in compact for label in EXCLUDED_LABELS):
            continue

        # 날짜가 같은 줄에 있으면 해당 줄만 사용합니다.
        period = _extract_date_range(line)

        # '■ 접수일'처럼 제목만 있는 줄이면 바로 뒤 두 줄까지 확인합니다.
        if period is None:
            nearby = " ".join(lines[index : index + 3])
            period = _extract_date_range(nearby)

        if period and period not in periods:
            periods.append(period)

    if not periods:
        return None

    starts = [date.fromisoformat(start) for start, _ in periods]
    ends = [date.fromisoformat(end) for _, end in periods]

    return min(starts).isoformat(), max(ends).isoformat()


def _status_from_application_period(start: str, end: str) -> str:
    today = date.today()
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)

    if today < start_date:
        return "접수예정"
    if today <= end_date:
        return "접수중"
    return "마감"


def collect() -> list[Notice]:
    notices = collect_pages("SH", SH_URLS)

    detailed_count = 0
    failed_count = 0

    for notice in notices:
        # 서울주거포털 목록의 두 번째 날짜는 '발표일'입니다.
        # 발표일을 신청 마감일로 잘못 쓰지 않도록 먼저 제거합니다.
        notice.applyStart = None
        notice.applyEnd = None

        if "i-sh.co.kr" not in notice.officialUrl:
            continue

        try:
            html = fetch_url(notice.officialUrl)
            period = _extract_application_period(html)

            if period:
                notice.applyStart, notice.applyEnd = period
                notice.status = _status_from_application_period(*period)
                detailed_count += 1

        except Exception:
            # 상세페이지 한 건이 실패해도 전체 SH 수집은 계속 진행합니다.
            failed_count += 1

        time.sleep(0.15)

    print(
        f"[SH] 목록 {len(notices)}건 / 실제 접수기간 확인 "
        f"{detailed_count}건 / 상세조회 실패 {failed_count}건"
    )
    return notices
