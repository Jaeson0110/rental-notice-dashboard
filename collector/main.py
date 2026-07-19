from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
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
            all_notices.extend(merged)
            source_status[agency] = {"ok": True, "count": len(merged), "checkedAt": checked_at, "error": None}
            success_count += 1
            print(f"[OK] {agency}: {len(merged)}건")
        except Exception as exc:
            preserved = [item for item in existing if item.get("agency") == agency]
            all_notices.extend(preserved)
            source_status[agency] = {
                "ok": False,
                "count": len(preserved),
                "checkedAt": checked_at,
                "error": str(exc)[:500],
            }
            print(f"[WARN] {agency}: 수집 실패, 기존 {len(preserved)}건 유지 - {exc}")

    all_notices.sort(key=sort_key, reverse=True)
    payload = {
        "updatedAt": checked_at,
        "sourceStatus": source_status,
        "notices": all_notices,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장 완료: {output_path} ({len(all_notices)}건)")
    return 0 if success_count else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="LH·SH·GH 임대 공고 수집기")
    parser.add_argument("--output", default="data/notices.json")
    args = parser.parse_args()
    return run(ROOT / args.output)


if __name__ == "__main__":
    raise SystemExit(main())
