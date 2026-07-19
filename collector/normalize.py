from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from .models import Notice

DATE_RE = re.compile(r"(20\d{2})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})(?:일)?")
SPACE_RE = re.compile(r"\s+")

RENTAL_KEYWORDS = (
    "임대", "행복주택", "국민임대", "영구임대", "매입임대", "전세임대", "장기전세",
    "입주자", "예비입주자", "청년주택", "협동조합주택", "공공주택"
)
EXCLUDE_KEYWORDS = (
    "사업자 모집", "민간사업자", "용지", "상가", "매각", "매입 공고", "건축설계", "공사입찰",
    "채용", "공지사항", "분양주택", "토지", "보상", "공모"
)

REGION_NAMES = [
    "서울특별시", "서울", "경기도", "경기", "인천광역시", "인천",
    "고양시", "고양", "화성시", "화성", "수원시", "수원", "성남시", "성남",
    "용인시", "용인", "안양시", "안양", "부천시", "부천", "남양주시", "남양주",
    "김포시", "김포", "광명시", "광명", "시흥시", "시흥", "파주시", "파주",
    "의정부시", "의정부", "양주시", "양주", "하남시", "하남", "광주시", "광주",
    "평택시", "평택", "오산시", "오산", "군포시", "군포", "의왕시", "의왕",
    "이천시", "이천", "안성시", "안성", "구리시", "구리", "포천시", "포천",
    "동두천시", "동두천", "가평군", "가평", "양평군", "양평", "연천군", "연천",
    "은평구", "강서구", "마포구", "서대문구", "종로구", "중구", "용산구",
    "성동구", "광진구", "동대문구", "중랑구", "성북구", "강북구", "도봉구",
    "노원구", "양천구", "구로구", "금천구", "영등포구", "동작구", "관악구",
    "서초구", "강남구", "송파구", "강동구"
]

TYPE_PATTERNS = [
    ("행복주택", "행복주택"), ("국민임대", "국민임대"), ("영구임대", "영구임대"),
    ("장기전세", "장기전세"), ("전세임대", "전세임대"), ("매입임대", "매입임대"),
    ("청년안심주택", "청년안심주택"), ("청년주택", "청년주택"),
    ("협동조합", "협동조합주택"), ("통합공공임대", "통합공공임대"),
    ("공공임대", "공공임대"), ("도시형생활주택", "도시형생활주택")
]
TARGET_PATTERNS = [
    ("예비신혼", "예비신혼부부"), ("신혼", "신혼부부"), ("신생아", "신생아가구"),
    ("청년", "청년"), ("대학생", "대학생"), ("고령자", "고령자"),
    ("한부모", "한부모가족"), ("다자녀", "다자녀"), ("주거취약", "주거취약계층")
]


def clean_text(value: str) -> str:
    return SPACE_RE.sub(" ", value or "").strip()


def parse_date(value: str) -> str | None:
    match = DATE_RE.search(value or "")
    if not match:
        return None
    try:
        return date(*map(int, match.groups())).isoformat()
    except ValueError:
        return None


def all_dates(value: str) -> list[str]:
    found: list[str] = []
    for match in DATE_RE.finditer(value or ""):
        try:
            parsed = date(*map(int, match.groups())).isoformat()
        except ValueError:
            continue
        if parsed not in found:
            found.append(parsed)
    return found


def infer_notice_type(text: str) -> str:
    for needle, label in TYPE_PATTERNS:
        if needle in text:
            return label
    return "임대주택"


def infer_targets(text: str) -> list[str]:
    return [label for needle, label in TARGET_PATTERNS if needle in text]


def infer_regions(text: str) -> list[str]:
    hits: list[str] = []
    aliases = {"서울": "서울특별시", "경기": "경기도", "인천": "인천광역시"}
    for region in REGION_NAMES:
        if region in text:
            normalized = aliases.get(region, region)
            if normalized not in hits:
                hits.append(normalized)
    return hits[:4]


def infer_status(text: str, published: str | None, deadline: str | None) -> str:
    compact = text.replace(" ", "")
    if "접수중" in compact or "모집중" in compact:
        return "접수중"
    if "접수예정" in compact or "모집예정" in compact:
        return "접수예정"
    if "마감" in compact or "종료" in compact:
        return "마감"
    if deadline:
        try:
            if datetime.fromisoformat(deadline).date() < date.today():
                return "마감"
        except ValueError:
            pass
    return "공고중"


def stable_id(agency: str, title: str, published: str | None, url: str) -> str:
    raw = "|".join([agency, clean_text(title).lower(), published or "", url or ""])
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:14]
    return f"{agency}-{digest}"


def title_from_element(element: Tag) -> str:
    link_texts = [clean_text(a.get_text(" ", strip=True)) for a in element.select("a")]
    link_texts = [text for text in link_texts if len(text) >= 8 and text not in {"바로가기", "상세보기", "첨부파일"}]
    if link_texts:
        return max(link_texts, key=len)

    text = clean_text(element.get_text(" ", strip=True))
    text = DATE_RE.sub(" ", text)
    text = re.sub(r"\b\d{1,4}\b", " ", text)
    return clean_text(text)[:180]


def link_from_element(element: Tag, base_url: str) -> str:
    for anchor in element.select("a[href]"):
        href = anchor.get("href", "").strip()
        if not href or href.startswith("javascript:") or href == "#":
            continue
        return urljoin(base_url, href)
    return base_url


def is_relevant(title: str, text: str) -> bool:
    joined = f"{title} {text}"
    if any(keyword in joined for keyword in EXCLUDE_KEYWORDS):
        return False
    return any(keyword in joined for keyword in RENTAL_KEYWORDS)


def parse_notice_elements(html: str, agency: str, base_url: str) -> list[Notice]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[Tag] = list(soup.select("table tbody tr"))
    if not candidates:
        candidates = list(soup.select("ul li, ol li"))

    notices: list[Notice] = []
    seen: set[str] = set()
    for element in candidates:
        text = clean_text(element.get_text(" ", strip=True))
        if len(text) < 18:
            continue
        title = title_from_element(element)
        if len(title) < 8 or not is_relevant(title, text):
            continue
        dates = all_dates(text)
        published = dates[0] if dates else None
        deadline = dates[-1] if len(dates) >= 2 else None
        url = link_from_element(element, base_url)
        notice_id = stable_id(agency, title, published, url)
        if notice_id in seen:
            continue
        seen.add(notice_id)
        notices.append(Notice(
            id=notice_id,
            agency=agency,
            title=title,
            noticeType=infer_notice_type(text),
            targetGroups=infer_targets(text),
            regions=infer_regions(text),
            publishedAt=published,
            applyEnd=deadline,
            status=infer_status(text, published, deadline),
            officialUrl=url,
        ))
    return notices
