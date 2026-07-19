from __future__ import annotations

from collector.models import Notice
from collector.sources.common import collect_pages

GH_URLS = [
    "https://www.gh.or.kr/gh/announcement-of-salerental001.do?article.offset=0&articleLimit=100",
    "https://apply.gh.or.kr/",
]


def collect() -> list[Notice]:
    return collect_pages("GH", GH_URLS)
