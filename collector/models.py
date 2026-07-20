from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(slots=True)
class Notice:
    id: str
    agency: str
    title: str
    noticeType: str = "임대주택"
    targetGroups: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    publishedAt: str | None = None
    applyStart: str | None = None
    applyEnd: str | None = None
    status: str = "공고중"
    officialUrl: str = ""
    attachments: list[dict[str, str]] = field(default_factory=list)

    # 모집 접수와 다른 일정을 별도 필드로 보관합니다.
    # 당첨자 발표일이나 서류 제출일이 신청 마감일로 섞이는 것을 방지합니다.
    documentStart: str | None = None
    documentEnd: str | None = None
    winnerAt: str | None = None
    contractStart: str | None = None
    contractEnd: str | None = None
    scheduleSource: str | None = None
    scheduleConfidence: str | None = None

    firstCollectedAt: str | None = None
    lastCheckedAt: str | None = None
    isNew: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
