from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any
from urllib.parse import unquote

import requests

from collector.models import Notice
from collector.normalize import infer_regions, infer_targets, parse_date, stable_id
from collector.sources.common import HEADERS, collect_pages

LH_URL = "https://apply.lh.or.kr/lhapply/apply/wt/wrtanc/selectWrtancList.do?mi=1026"
LH_API_URL = "https://apis.data.go.kr/B552555/lhLeaseNoticeInfo1/lhLeaseNoticeInfo1"

# 행정안전부 광역시도 코드: 서울 11, 인천 28, 경기 41
CAPITAL_REGION_CODES = {
    "11": "서울특별시",
    "28": "인천광역시",
    "41": "경기도",
}


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


def _find_first(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = _find_first(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_first(child, key)
            if found is not None:
                return found
    return None


def _request_region(service_key: str, region_code: str) -> list[dict[str, Any]]:
    today = date.today()

    # Encoding 키를 넣었어도 requests가 이중 인코딩하지 않도록 한 번 풀어서 사용합니다.
    normalized_key = unquote(service_key.strip())

    params = {
        "ServiceKey": normalized_key,
        "PG_SZ": 1000,
        "PAGE": 1,
        "UPP_AIS_TP_CD": "06",
        "CNP_CD": region_code,
        "PAN_NT_ST_DT": (today - timedelta(days=365)).strftime("%Y.%m.%d"),
        "CLSG_DT": (today + timedelta(days=365)).strftime("%Y.%m.%d"),
    }

    response = requests.get(
        LH_API_URL,
        params=params,
        headers={**HEADERS, "Accept": "application/json"},
        timeout=45,
    )
    response.raise_for_status()

    try:
        payload = response.json()
    except ValueError as exc:
        body = response.text[:300].replace("\n", " ")
        raise RuntimeError(f"JSON 응답이 아닙니다: HTTP {response.status_code}, {body}") from exc

    result_code = _find_first(payload, "SS_CODE")
    if result_code not in (None, "Y"):
        message = (
            _find_first(payload, "SS_MSG")
            or _find_first(payload, "RESULT_MSG")
            or _find_first(payload, "returnAuthMsg")
            or "알 수 없는 API 오류"
        )
        raise RuntimeError(f"SS_CODE={result_code}, 메시지={message}")

    return _find_notice_rows(payload)


def _collect_api(service_key: str) -> list[Notice]:
    notices: list[Notice] = []
    seen_ids: set[str] = set()
    region_counts: list[str] = []

    for region_code, fallback_region in CAPITAL_REGION_CODES.items():
        rows = _request_region(service_key, region_code)
        region_counts.append(f"{fallback_region} {len(rows)}건")

        for row in rows:
            title = str(row.get("PAN_NM") or "").strip()
            if not title:
                continue

            detail_url = str(row.get("DTL_URL") or LH_URL).strip()
            published = parse_date(str(row.get("PAN_NT_ST_DT") or row.get("PAN_DT") or ""))
            deadline = parse_date(str(row.get("CLSG_DT") or row.get("PAN_NT_TO_DT") or ""))
            notice_type = str(
                row.get("AIS_TP_CD_NM") or row.get("UPP_AIS_TP_NM") or "임대주택"
            ).strip()
            region = str(row.get("CNP_CD_NM") or fallback_region).strip()
            text = " ".join([title, notice_type, region])

            notice_id = stable_id("LH", title, published, detail_url)
            if notice_id in seen_ids:
                continue
            seen_ids.add(notice_id)

            notices.append(
                Notice(
                    id=notice_id,
                    agency="LH",
                    title=title,
                    noticeType=notice_type,
                    targetGroups=infer_targets(text),
                    regions=infer_regions(text) or [region],
                    publishedAt=published,
                    applyEnd=deadline,
                    status=str(row.get("PAN_SS") or "공고중").strip(),
                    officialUrl=detail_url,
                )
            )

    print(f"[LH] 공식 API 사용 성공: {', '.join(region_counts)} / 합계 {len(notices)}건")

    if not notices:
        raise RuntimeError("공식 API 응답에서 수도권 임대 공고를 찾지 못했습니다.")

    return notices


def collect() -> list[Notice]:
    service_key = os.getenv("DATA_GO_KR_SERVICE_KEY", "").strip()

    if service_key:
        # API 키가 있는데 실패하면 원인을 숨기지 않습니다.
        try:
            return _collect_api(service_key)
        except Exception as exc:
            raise RuntimeError(f"LH 공식 API 실패: {exc}") from exc

    print("[LH] DATA_GO_KR_SERVICE_KEY가 없어 홈페이지 임시 수집을 사용합니다.")
    return collect_pages("LH", [LH_URL], delay_seconds=0)
