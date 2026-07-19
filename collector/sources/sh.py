from __future__ import annotations

from datetime import date, datetime

from collector.models import Notice
from collector.sources.common import collect_pages

# 서울주거포털의 통합 목록과 유형별 목록을 함께 확인합니다.
# 공식 목록에는 과거 모집마감 공고도 계속 남아 있으므로,
# 개인 대시보드에는 현재 확인할 가치가 있는 공고만 남깁니다.
SH_URLS = [
    "https://housing.seoul.go.kr/site/main/sh/publicLease/list",
    *[
        f"https://housing.seoul.go.kr/site/main/sh/publicLease/{index:02d}/list"
        for index in range(1, 10)
    ],
]


def _is_active(notice: Notice) -> bool:
    status = str(notice.status or "").replace(" ", "")

    # 서울주거포털에서 이미 모집마감으로 표시한 공고는 제외합니다.
    if "마감" in status or "종료" in status:
        return False

    # 마감일이 명시돼 있고 이미 지난 경우도 제외합니다.
    if notice.applyEnd:
        try:
            if datetime.fromisoformat(notice.applyEnd).date() < date.today():
                return False
        except ValueError:
            pass

    return True


def collect() -> list[Notice]:
    notices = collect_pages("SH", SH_URLS)
    active = [notice for notice in notices if _is_active(notice)]
    print(f"[SH] 공식 목록 {len(notices)}건 중 현재 공고 {len(active)}건만 저장")
    return active
