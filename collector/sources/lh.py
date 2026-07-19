from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

import requests

from collector.models import Notice
from collector.normalize import (
    infer_regions,
    infer_targets,
    parse_date,
    stable_id,
)
from collector.sources.common import HEADERS, collect_pages

LH_URL = "https://apply.lh.or.kr/lhapply/apply/wt/wrtanc/selectWrtancList.do?mi=1026"
LH_API_URL = "https://apis.data.go.kr/B552555/lhLeaseNoticeInfo1/lhLeaseNoticeInfo1"


def _find_notice_rows(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if "PAN_NM" in value and ("DTL_URL" in value or "PAN_SS" in value):
            rows.append(value)
        for child in value.values():
            rows.extend(_find_notice_rows(child))
    elif isinstance(value, list):
        for child in value:
            rows.extend(_find_notice_rows(child))
    return rows


def _collect_api(service_key: str) -> list[Notice]:
    today = date.today()
    params = {
        "ServiceKey": service_key,
        "PG_SZ": 1000,
        "PAGE": 1,
        "UPP_AIS_TP_CD": "06",
        "PAN_NT_ST_DT": (today - timedelta(days=180)).strftime("%Y.%m.%d"),
        "CLSG_DT": (today + timedelta(days=365)).strftime("%Y.%m.%d"),
    }
    response = requests.get(LH_API_URL, params=params, headers=HEADERS, timeout=35)
    response.raise_for_status()
    payload = response.json()
    rows = _find_notice_rows(payload)
    notices: list[Notice] = []
    for row in rows:
        title = str(row.get("PAN_NM") or "").strip()
        if not title:
            continue
        detail_url = str(row.get("DTL_URL") or LH_URL).strip()
        published = parse_date(str(row.get("PAN_NT_ST_DT") or row.get("PAN_DT") or ""))
        deadline = parse_date(str(row.get("CLSG_DT") or row.get("PAN_NT_TO_DT") or ""))
        notice_type = str(row.get("AIS_TP_CD_NM") or row.get("UPP_AIS_TP_NM") or "임대주택").strip()
        region = str(row.get("CNP_CD_NM") or "").strip()
        text = " ".join([title, notice_type, region])
        notices.append(Notice(
            id=stable_id("LH", title, published, detail_url),
            agency="LH",
            title=title,
            noticeType=notice_type,
            targetGroups=infer_targets(text),
            regions=infer_regions(text) or ([region] if region else []),
            publishedAt=published,
            applyEnd=deadline,
            status=str(row.get("PAN_SS") or "공고중").strip(),
            officialUrl=detail_url,
        ))
    if not notices:
        raise RuntimeError("LH API 응답에서 공고를 찾지 못했습니다.")
    return notices


def collect() -> list[Notice]:
    service_key = os.getenv("DATA_GO_KR_SERVICE_KEY", "").strip()
    if service_key:
        try:
            return _collect_api(service_key)
        except Exception as api_error:
            try:
                return collect_pages("LH", [LH_URL], delay_seconds=0)
            except Exception as scrape_error:
                raise RuntimeError(f"API 실패: {api_error}; 홈페이지 수집 실패: {scrape_error}") from scrape_error
    return collect_pages("LH", [LH_URL], delay_seconds=0)
