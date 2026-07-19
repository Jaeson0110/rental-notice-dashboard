from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Callable

# Allows `python collector/main.py` from the repository root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collector.models import Notice, utc_now_iso  # noqa: E402
from collector.sources import gh, lh, sh  # noqa: E402

Collector = Callable[[], list[Notice]]
SOURCES: dict[str, Collector] = {"LH": lh.collect, "SH": sh.collect, "GH": gh.collect}

# 기관별 기본 지역과 제목/지역 데이터에서 판별할 수도권 별칭입니다.
SEOUL_HINTS = (
    "서울특별시", "서울시", "서울", "서울지역본부",
    "종로구", "용산구", "성동구", "광진구", "동대문구", "중랑구",
    "성북구", "강북구", "도봉구", "노원구", "은평구", "서대문구",
    "마포구", "양천구", "강서구", "구로구", "금천구", "영등포구",
    "동작구", "관악구", "서초구", "강남구", "송파구", "강동구",
)

# '화성시'뿐 아니라 LH 제목에 자주 쓰이는 '화성', '화성서부' 같은 표기도 잡습니다.
GYEONGGI_HINTS = (
    "경기도", "경기지역본부", "경기북부지역본부", "경기남부지역본부", "경기",
    "수원시", "수원", "용인시", "용인", "고양시", "고양", "화성시", "화성",
    "성남시", "성남", "부천시", "부천", "남양주시", "남양주", "안산시", "안산",
    "평택시", "평택", "안양시", "안양", "시흥시", "시흥", "파주시", "파주",
    "김포시", "김포", "의정부시", "의정부", "양주시", "양주", "광주시", "경기광주",
    "하남시", "하남", "광명시", "광명", "군포시", "군포", "오산시", "오산",
    "이천시", "이천", "안성시", "안성", "구리시", "구리", "의왕시", "의왕",
    "포천시", "포천", "여주시", "여주", "동두천시", "동두천", "과천시", "과천",
    "양평군", "양평", "가평군", "가평", "연천군", "연천",
)

INCHEON_HINTS = (
    "인천광역시", "인천시", "인천", "인천지역본부",
    "미추홀구", "연수구", "남동구", "부평구", "계양구", "강화군", "옹진군",
)


def read_existing(path: Path) -> tuple[list[dict], dict]:
    if not path.exists():
        return [], {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return [], {}
    if isinstance(payload, list):
        return payload, {}
    return payload.get("notices", []), payload.get("sourceStatus", {})


def merge_source(existing: list[dict], agency: str, fresh: list[Notice], checked_at: str) -> list[dict]:
    old_by_id = {item.get("id"): item for item in existing if item.get("agency") == agency and item.get("id")}
    today = date.today().isoformat()
    output: list[dict] = []
    for notice in fresh:
        item = notice.to_dict()
        old = old_by_id.get(notice.id)
        first_collected = (old or {}).get("firstCollectedAt") or checked_at
        item["firstCollectedAt"] = first_collected
        item["lastCheckedAt"] = checked_at
        item["isNew"] = str(first_collected).startswith(today)
        output.append(item)
    return output


def _dedupe(values: list[str]) -> list[str]:
    output: list[str] = []
    for value in values:
        value = str(value or "").strip()
        if value and value not in output:
            output.append(value)
    return output


def _contains_any(text: str, hints: tuple[str, ...]) -> bool:
    return any(hint in text for hint in hints)


def classify_capital_region(item: dict) -> dict | None:
    """수도권 공고만 남기고 서울/경기/인천 상위 지역을 보정합니다.

    LH 공고는 제목에 '화성서부', '고양', '평택'처럼 '시'가 빠진 경우가 많아
    시·군의 짧은 별칭까지 판별합니다. SH와 GH는 기관 자체를 지역 근거로 씁니다.
    """
    agency = str(item.get("agency") or "").upper()
    original_regions = [str(value) for value in (item.get("regions") or []) if value]
    text = " ".join([str(item.get("title") or ""), *original_regions])

    parents: list[str] = []
    if agency == "SH" or _contains_any(text, SEOUL_HINTS):
        parents.append("서울특별시")
    if agency == "GH" or _contains_any(text, GYEONGGI_HINTS):
        parents.append("경기도")
    if _contains_any(text, INCHEON_HINTS):
        parents.append("인천광역시")

    parents = _dedupe(parents)
    if not parents:
        return None

    item["regions"] = _dedupe([*parents, *original_regions])
    return item


def filter_capital_notices(items: list[dict]) -> list[dict]:
    filtered: list[dict] = []
    for item in items:
        normalized = classify_capital_region(item)
        if normalized is not None:
            filtered.append(normalized)
    return filtered


def sort_key(item: dict) -> tuple[str, str]:
    return (item.get("publishedAt") or "0000-00-00", item.get("title") or "")


def run(output_path: Path) -> int:
    existing, old_status = read_existing(output_path)
    checked_at = utc_now_iso()
    all_notices: list[dict] = []
    source_status = dict(old_status)
    success_count = 0

    for agency, collector in SOURCES.items():
        try:
            fresh = collector()
            if not fresh:
                raise RuntimeError("수집 결과가 0건입니다.")
            merged = merge_source(existing, agency, fresh, checked_at)
            capital_only = filter_capital_notices(merged)
            all_notices.extend(capital_only)
            source_status[agency] = {
                "ok": True,
                "count": len(capital_only),
                "rawCount": len(merged),
                "checkedAt": checked_at,
                "error": None,
            }
            success_count += 1
            print(f"[OK] {agency}: 원본 {len(merged)}건 / 수도권 {len(capital_only)}건")
        except Exception as exc:
            preserved = filter_capital_notices([item for item in existing if item.get("agency") == agency])
            all_notices.extend(preserved)
            source_status[agency] = {
                "ok": False,
                "count": len(preserved),
                "checkedAt": checked_at,
                "error": str(exc)[:500],
            }
            print(f"[WARN] {agency}: 수집 실패, 기존 수도권 {len(preserved)}건 유지 - {exc}")

    all_notices.sort(key=sort_key, reverse=True)
    payload = {
        "updatedAt": checked_at,
        "sourceStatus": source_status,
        "notices": all_notices,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장 완료: {output_path} ({len(all_notices)}건, 수도권만)")
    return 0 if success_count else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="LH·SH·GH 수도권 임대 공고 수집기")
    parser.add_argument("--output", default="data/notices.json")
    args = parser.parse_args()
    return run(ROOT / args.output)


if __name__ == "__main__":
    raise SystemExit(main())
