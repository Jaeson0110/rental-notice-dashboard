from __future__ import annotations

import re
import time
from datetime import date, timedelta
from urllib.parse import urlencode

from bs4 import BeautifulSoup, Tag

from collector.models import Notice
from collector.normalize import (
    all_dates,
    clean_text,
    infer_notice_type,
    infer_regions,
    infer_targets,
    parse_date,
    stable_id,
)
from collector.sources.common import collect_pages, fetch_url

# 기존 수집 결과의 ID를 가능한 한 유지하기 위한 보조 소스입니다.
# 아래 GH 청약센터 구조화 목록과 제목·공고일이 일치하면 정확한 일정으로 덮어씁니다.
LEGACY_GH_URLS = [
    "https://www.gh.or.kr/gh/announcement-of-salerental001.do?article.offset=0&articleLimit=100",
    "https://apply.gh.or.kr/",
]

# 실제 모집공고의 기준 소스입니다. 목록에 게시일, 마감일, 상태가 분리되어 있습니다.
GH_LISTS = [
    {
        "category": "임대주택",
        "list_url": "https://apply.gh.or.kr/sb/sr/sr7150/selectPbancRentHouseList.do",
        "detail_url": "https://apply.gh.or.kr/sb/sr/sr7150/selectPbancDetailView.do",
    },
    {
        "category": "매입임대",
        "list_url": "https://apply.gh.or.kr/sb/sr/sr7155/selectPbancRentHouseList.do",
        "detail_url": "https://apply.gh.or.kr/sb/sr/sr7155/selectPbancDetailView.do",
    },
]

STATUS_LABELS = ("공고중", "접수중", "접수마감", "접수예정")
FOLLOW_UP_KEYWORDS = (
    "당첨자 발표",
    "당첨자발표",
    "서류심사대상자 발표",
    "서류제출대상자 발표",
    "선정결과",
    "당첨결과",
    "예비입주자 순번",
)

DATE_RANGE_RE = re.compile(
    r"(?P<sy>20\d{2})\s*[.\-/년]\s*(?P<sm>\d{1,2})\s*[.\-/월]\s*(?P<sd>\d{1,2})\s*(?:일)?"
    r"(?:\s*\d{1,2}:\d{2})?\s*(?:~|∼|～|부터|[-–—])\s*"
    r"(?:(?P<ey>20\d{2})\s*[.\-/년]\s*)?"
    r"(?P<em>\d{1,2})\s*[.\-/월]\s*(?P<ed>\d{1,2})\s*(?:일)?"
    r"(?:\s*\d{1,2}:\d{2})?"
)
FULL_DATE_RE = re.compile(
    r"(?P<year>20\d{2})\s*[.\-/년]\s*(?P<month>\d{1,2})\s*[.\-/월]\s*(?P<day>\d{1,2})\s*(?:일)?"
)
PBANC_PATTERNS = (
    re.compile(r"pbancNo\s*[=:]\s*['\"]?(\d+)", re.I),
    re.compile(r"pbancNo=(\d+)", re.I),
    re.compile(r"(?:detail|view|select)[A-Za-z0-9_]*\s*\(\s*['\"]?(\d{2,})", re.I),
)

APPLICATION_LABELS = (
    "온라인접수기간",
    "인터넷접수기간",
    "현장접수기간",
    "방문접수기간",
    "청약접수기간",
    "신청접수기간",
)
DOCUMENT_LABELS = ("서류제출기간", "서류접수기간", "심사서류제출기간")
WINNER_LABELS = ("당첨자발표일", "당첨자 발표일", "당첨결과발표일")
CONTRACT_LABELS = ("계약기간", "온라인계약기간")


def _iso(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _date_range(value: str) -> tuple[str, str] | None:
    match = DATE_RANGE_RE.search(value or "")
    if match:
        start = _iso(int(match.group("sy")), int(match.group("sm")), int(match.group("sd")))
        if not start:
            return None
        start_date = date.fromisoformat(start)
        end_year = int(match.group("ey")) if match.group("ey") else start_date.year
        end_month = int(match.group("em"))
        if not match.group("ey") and end_month < start_date.month:
            end_year += 1
        end = _iso(end_year, end_month, int(match.group("ed")))
        if not end or date.fromisoformat(end) < start_date:
            return None
        return start, end

    single = FULL_DATE_RE.search(value or "")
    if not single:
        return None
    parsed = _iso(int(single.group("year")), int(single.group("month")), int(single.group("day")))
    return (parsed, parsed) if parsed else None


def _lines(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    return [
        line
        for line in (clean_text(raw) for raw in soup.get_text("\n", strip=True).splitlines())
        if line
    ]


def _ranges_for_labels(lines: list[str], labels: tuple[str, ...]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for index, line in enumerate(lines):
        compact = line.replace(" ", "")
        if not any(label.replace(" ", "") in compact for label in labels):
            continue
        candidate = " ".join(lines[index:index + 3])
        period = _date_range(candidate)
        if period and period not in found:
            found.append(period)
    return found


def _merge_ranges(ranges: list[tuple[str, str]]) -> tuple[str, str] | None:
    if not ranges:
        return None
    starts = [date.fromisoformat(start) for start, _ in ranges]
    ends = [date.fromisoformat(end) for _, end in ranges]
    return min(starts).isoformat(), max(ends).isoformat()


def _first_date_for_labels(lines: list[str], labels: tuple[str, ...]) -> str | None:
    for index, line in enumerate(lines):
        compact = line.replace(" ", "")
        if not any(label.replace(" ", "") in compact for label in labels):
            continue
        candidate = " ".join(lines[index:index + 3])
        match = FULL_DATE_RE.search(candidate)
        if match:
            return _iso(int(match.group("year")), int(match.group("month")), int(match.group("day")))
    return None


def _parse_detail(html: str) -> dict[str, str | None]:
    lines = _lines(html)
    application = _merge_ranges(_ranges_for_labels(lines, APPLICATION_LABELS))
    documents = _merge_ranges(_ranges_for_labels(lines, DOCUMENT_LABELS))
    contracts = _merge_ranges(_ranges_for_labels(lines, CONTRACT_LABELS))
    winner = _first_date_for_labels(lines, WINNER_LABELS)

    return {
        "applyStart": application[0] if application else None,
        "applyEnd": application[1] if application else None,
        "documentStart": documents[0] if documents else None,
        "documentEnd": documents[1] if documents else None,
        "winnerAt": winner,
        "contractStart": contracts[0] if contracts else None,
        "contractEnd": contracts[1] if contracts else None,
    }


def _extract_pbanc_no(row: Tag) -> str | None:
    raw = str(row)
    for pattern in PBANC_PATTERNS:
        match = pattern.search(raw)
        if match:
            return match.group(1)

    # 일부 목록은 data-* 또는 hidden input에 번호를 보관합니다.
    for element in row.select("[data-pbanc-no], input[name*='pbanc']"):
        value = element.get("data-pbanc-no") or element.get("value")
        if value and str(value).isdigit():
            return str(value)
    return None


def _title_from_cell(cell: Tag) -> str:
    candidates = [clean_text(anchor.get_text(" ", strip=True)) for anchor in cell.select("a")]
    candidates = [
        value for value in candidates
        if len(value) >= 8 and value.lower() not in {"hwp", "pdf", "zip", "첨부파일"}
    ]
    if candidates:
        return max(candidates, key=len)
    return clean_text(cell.get_text(" ", strip=True))


def _official_status(value: str) -> str:
    compact = (value or "").replace(" ", "")
    for label in STATUS_LABELS:
        if label in compact:
            return label
    return ""


def _status_from_schedule(
    apply_start: str | None,
    apply_end: str | None,
    official_status: str,
    title: str,
) -> str:
    compact_title = title.replace(" ", "")
    if any(keyword.replace(" ", "") in compact_title for keyword in FOLLOW_UP_KEYWORDS):
        return "후속공고"

    today = date.today()
    if apply_end:
        end = date.fromisoformat(apply_end)
        if end < today:
            return "마감"
    if apply_start:
        start = date.fromisoformat(apply_start)
        if today < start:
            return "접수예정"
        if not apply_end or today <= date.fromisoformat(apply_end):
            return "접수중"

    compact_status = official_status.replace(" ", "")
    if "접수마감" in compact_status or "마감" in compact_status:
        return "마감"
    if "접수중" in compact_status:
        return "접수중"
    if "접수예정" in compact_status:
        return "접수예정"
    if "공고중" in compact_status and apply_end:
        return "공고중"

    # 날짜가 전혀 없을 때는 발표일 등을 마감일로 추측하지 않습니다.
    return "일정 확인 필요"


def _row_attachments(row: Tag, base_url: str) -> list[dict[str, str]]:
    attachments: list[dict[str, str]] = []
    for anchor in row.select("a[href]"):
        href = str(anchor.get("href") or "").strip()
        label = clean_text(anchor.get_text(" ", strip=True)) or str(anchor.get("title") or "첨부파일")
        if not href or href.startswith("javascript:"):
            continue
        if not any(token in f"{label} {href}".lower() for token in ("pdf", "hwp", "zip", "첨부")):
            continue
        from urllib.parse import urljoin
        attachments.append({"name": label, "url": urljoin(base_url, href)})
    return attachments


def _parse_list_page(html: str, source: dict[str, str]) -> list[Notice]:
    soup = BeautifulSoup(html, "html.parser")
    output: list[Notice] = []

    for row in soup.select("table tbody tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 7:
            continue

        cell_texts = [clean_text(cell.get_text(" ", strip=True)) for cell in cells]
        # GH 공식 목록의 열 순서: 번호, 유형, 공고명, 지역, 첨부, 게시일, 마감일, 상태, ...
        type_text = cell_texts[1] if len(cell_texts) > 1 else source["category"]
        title = _title_from_cell(cells[2]) if len(cells) > 2 else ""
        region_text = cell_texts[3] if len(cell_texts) > 3 else "경기도"
        published = parse_date(cell_texts[5]) if len(cell_texts) > 5 else None
        list_deadline = parse_date(cell_texts[6]) if len(cell_texts) > 6 else None
        official_status = _official_status(cell_texts[7] if len(cell_texts) > 7 else "")

        if len(title) < 8 or not published:
            # 열 구조가 바뀐 경우 날짜와 가장 긴 텍스트를 이용해 보수적으로 복구합니다.
            row_text = clean_text(row.get_text(" ", strip=True))
            dates = all_dates(row_text)
            published = published or (dates[0] if dates else None)
            if not list_deadline and len(dates) >= 2:
                list_deadline = dates[1]
            if len(title) < 8:
                title_candidates = [value for value in cell_texts if len(value) >= 8 and not parse_date(value)]
                title = max(title_candidates, key=len) if title_candidates else ""

        if len(title) < 8:
            continue

        pbanc_no = _extract_pbanc_no(row)
        official_url = source["list_url"]
        if pbanc_no:
            official_url = f'{source["detail_url"]}?{urlencode({"pbancNo": pbanc_no, "previewYn": "N"})}'

        notice_type = infer_notice_type(f"{type_text} {title}")
        if source["category"] == "매입임대" and notice_type == "임대주택":
            notice_type = "매입임대"

        notice = Notice(
            id=stable_id("GH", title, published, official_url),
            agency="GH",
            title=title,
            noticeType=notice_type,
            targetGroups=infer_targets(title),
            regions=infer_regions(f"경기도 {region_text} {title}"),
            publishedAt=published,
            applyEnd=list_deadline,
            status=_status_from_schedule(None, list_deadline, official_status, title),
            officialUrl=official_url,
            attachments=_row_attachments(row, source["list_url"]),
            scheduleSource="GH 청약센터 목록",
            scheduleConfidence="높음" if list_deadline else "확인필요",
        )
        output.append(notice)

    return output


def _fetch_structured_notices() -> list[Notice]:
    merged: dict[tuple[str, str], Notice] = {}
    detail_checked = 0
    detail_failed = 0

    for source in GH_LISTS:
        seen_page_keys: set[tuple[str, str]] = set()

        for page_index in range(1, 41):
            separator = "&" if "?" in source["list_url"] else "?"
            page_url = f'{source["list_url"]}{separator}{urlencode({"pageIndex": page_index})}'
            try:
                html = fetch_url(page_url)
            except Exception as exc:
                if page_index == 1:
                    raise RuntimeError(f"GH {source['category']} 목록 조회 실패: {exc}") from exc
                break

            page_notices = _parse_list_page(html, source)
            if not page_notices:
                break

            page_keys = {(n.title.replace(" ", ""), n.publishedAt or "") for n in page_notices}
            new_page_keys = page_keys - seen_page_keys
            if not new_page_keys:
                # pageIndex가 무시되어 같은 첫 페이지가 반복되는 경우 무한 반복을 막습니다.
                break
            seen_page_keys.update(page_keys)

            for notice in page_notices:
                key = (notice.title.replace(" ", ""), notice.publishedAt or "")
                merged[key] = notice

            if len(page_notices) < 10:
                break
            time.sleep(0.2)

    # 목록에서 마감일이 비어 있거나 최근 공고인 경우만 상세페이지를 확인합니다.
    recent_cutoff = date.today() - timedelta(days=240)
    for notice in merged.values():
        published = date.fromisoformat(notice.publishedAt) if notice.publishedAt else None
        should_check_detail = (
            "pbancNo=" in notice.officialUrl
            and (not notice.applyEnd or (published and published >= recent_cutoff))
        )
        if not should_check_detail:
            continue

        try:
            detail = _parse_detail(fetch_url(notice.officialUrl))
            detail_checked += 1
        except Exception:
            detail_failed += 1
            continue

        detail_has_schedule = any(detail.values())
        if detail.get("applyStart"):
            notice.applyStart = detail["applyStart"]
        if detail.get("applyEnd"):
            notice.applyEnd = detail["applyEnd"]
        notice.documentStart = detail.get("documentStart")
        notice.documentEnd = detail.get("documentEnd")
        notice.winnerAt = detail.get("winnerAt")
        notice.contractStart = detail.get("contractStart")
        notice.contractEnd = detail.get("contractEnd")

        if detail_has_schedule:
            notice.scheduleSource = "GH 청약센터 상세 공급일정"
            notice.scheduleConfidence = "높음" if notice.applyEnd else "확인필요"

        notice.status = _status_from_schedule(
            notice.applyStart,
            notice.applyEnd,
            notice.status,
            notice.title,
        )
        time.sleep(0.12)

    unknown_count = sum(1 for notice in merged.values() if not notice.applyEnd)
    print(
        f"[GH] 전용 청약목록 {len(merged)}건 / 상세일정 확인 {detail_checked}건 "
        f"/ 상세조회 실패 {detail_failed}건 / 마감일 미기재 {unknown_count}건"
    )
    return list(merged.values())


def _match_key(notice: Notice) -> tuple[str, str]:
    compact_title = re.sub(r"[^0-9A-Za-z가-힣]", "", notice.title).lower()
    return compact_title, notice.publishedAt or ""


def _copy_schedule(target: Notice, source: Notice) -> None:
    target.noticeType = source.noticeType
    target.targetGroups = source.targetGroups or target.targetGroups
    target.regions = source.regions or target.regions
    target.applyStart = source.applyStart
    target.applyEnd = source.applyEnd
    target.status = source.status
    target.officialUrl = source.officialUrl or target.officialUrl
    target.attachments = source.attachments or target.attachments
    target.documentStart = source.documentStart
    target.documentEnd = source.documentEnd
    target.winnerAt = source.winnerAt
    target.contractStart = source.contractStart
    target.contractEnd = source.contractEnd
    target.scheduleSource = source.scheduleSource
    target.scheduleConfidence = source.scheduleConfidence


def collect() -> list[Notice]:
    structured = _fetch_structured_notices()
    structured_by_key = {_match_key(notice): notice for notice in structured}
    used: set[tuple[str, str]] = set()
    output: list[Notice] = []

    # 기존 GH 공고 ID가 유지되도록 먼저 기존 방식의 항목을 살리고 일정을 교정합니다.
    try:
        legacy = collect_pages("GH", LEGACY_GH_URLS)
    except Exception:
        legacy = []

    for notice in legacy:
        key = _match_key(notice)
        matched = structured_by_key.get(key)
        if matched:
            _copy_schedule(notice, matched)
            used.add(key)
        elif any(keyword.replace(" ", "") in notice.title.replace(" ", "") for keyword in FOLLOW_UP_KEYWORDS):
            notice.noticeType = "후속공고"
            notice.status = "후속공고"
            notice.applyStart = None
            notice.applyEnd = None
            notice.scheduleSource = "GH 일반 공고 게시판"
            notice.scheduleConfidence = "높음"
        elif notice.applyEnd:
            notice.status = _status_from_schedule(notice.applyStart, notice.applyEnd, notice.status, notice.title)
        else:
            # 당첨자 발표일 등을 신청 마감일로 추측하지 않습니다.
            notice.applyStart = None
            notice.applyEnd = None
            notice.status = "일정 확인 필요"
            notice.scheduleSource = "GH 일반 공고 게시판"
            notice.scheduleConfidence = "확인필요"
        output.append(notice)

    # 기존 수집기에 없던 GH 청약센터 공고도 추가합니다.
    output.extend(notice for notice in structured if _match_key(notice) not in used)

    # 제목+공고일 기준으로 중복 제거하며 기존 ID를 가진 항목을 우선합니다.
    deduped: dict[tuple[str, str], Notice] = {}
    for notice in output:
        key = _match_key(notice)
        if key not in deduped:
            deduped[key] = notice
        elif deduped[key].scheduleConfidence != "높음" and notice.scheduleConfidence == "높음":
            deduped[key] = notice

    return list(deduped.values())
