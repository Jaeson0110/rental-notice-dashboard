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

# 사이트에는 수도권 공고만 노출합니다.
CAPITAL_REGION_LABELS = ("서울특별시", "경기도", "인천광역시")

SEOUL_HINTS = (
    "서울특별시", "서울시", "서울",
)
GYEONGGI_HINTS = (
    "경기도", "경기",
    "수원시", "용인시", "고양시", "화성시", "성남시", "부천시", "남양주시",
    "안산시", "평택시", "안양시", "시흥시", "파주시", "김포시", "의정부시",
    "광주시", "하남시", "광명시", "군포시", "양주시", "오산시", "이천시",
    "안성시", "구리시", "의왕시", "포천시", "여주시", "동두천시", "과천시",
    "양평군", "가평군", "연천군",
)
INCHEON_HINTS = (
    "인천광역시", "인천시", "인천",
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


def classify_capital_region(item: dict) -> dict | None:
    """수도권 공고만 남기고 상위 지역명을 표준화합니다.

    SH는 서울, GH는 경기 기관이므로 기관 자체를 지역 근거로 사용합니다.
    LH는 API/공고 제목에 서울·경기·인천 또는 경기도 시군명이 확인될 때만 남깁니다.
    전국/지역 미분류 공고는 엄격 모드에서 제외됩니다.
    """
    agency = str(item.get("agency") or "").upper()
    original_regions = [str(value) for value in (item.get("regions") or []) if value]
    text = " ".join([str(item.get("title") or ""), *original_regions])

    parents: list[str] = []
    if agency == "SH" or any(hint in text for hint in SEOUL_HINTS):
        parents.append("서울특별시")
    if agency == "GH" or any(hint in text for hint in GYEONGGI_HINTS):
        parents.append("경기도")
    if any(hint in text for hint in INCHEON_HINTS):
        parents.append("인천광역시")

    parents = _dedupe(parents)
    if not parents:
        return None

    # 상위 지역을 맨 앞에 넣어 '서울/경기/인천' 필터가 항상 작동하게 합니다.
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
