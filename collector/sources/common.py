from __future__ import annotations

import time
from collections.abc import Iterable

import requests

from collector.models import Notice
from collector.normalize import parse_notice_elements

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; RentalNoticeDashboard/1.0; +personal-use)",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5",
}


def fetch_url(url: str, *, timeout: int = 25) -> str:
    response = requests.get(url, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding
    return response.text


def collect_pages(agency: str, urls: Iterable[str], delay_seconds: float = 0.5) -> list[Notice]:
    merged: dict[str, Notice] = {}
    errors: list[str] = []
    for url in urls:
        try:
            html = fetch_url(url)
            for notice in parse_notice_elements(html, agency, url):
                merged[notice.id] = notice
        except Exception as exc:  # network and markup failures are handled per page
            errors.append(f"{url}: {exc}")
        time.sleep(delay_seconds)
    if not merged and errors:
        raise RuntimeError(" / ".join(errors[:3]))
    return list(merged.values())
