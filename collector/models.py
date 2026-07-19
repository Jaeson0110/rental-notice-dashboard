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
    firstCollectedAt: str | None = None
    lastCheckedAt: str | None = None
    isNew: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
