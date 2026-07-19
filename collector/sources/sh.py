from __future__ import annotations

from collector.models import Notice
from collector.sources.common import collect_pages

# 통합 목록과 유형별 목록을 함께 확인합니다. 중복 공고는 ID로 제거됩니다.
SH_URLS = [
    "https://housing.seoul.go.kr/site/main/sh/publicLease/list",
    *[
        f"https://housing.seoul.go.kr/site/main/sh/publicLease/{index:02d}/list"
        for index in range(1, 10)
    ],
]


def collect() -> list[Notice]:
    return collect_pages("SH", SH_URLS)
