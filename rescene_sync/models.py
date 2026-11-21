from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


@dataclass
class Event:
    source: str          # 어디서 왔는지 (mnetplus, youtube)
    source_id: str       # 그곳에서의 고유 번호
    title: str
    description: str
    start: dt.date | dt.datetime   # date면 하루 종일 일정
    end: dt.date | dt.datetime     # 하루 종일 일정이면 '끝나는 다음 날'
    url: str = ""

    @property
    def key(self) -> str:
        return f"{self.source}:{self.source_id}"

    @property
    def all_day(self) -> bool:
        return not isinstance(self.start, dt.datetime)

    def calendar_id(self) -> str:
        # 구글 캘린더 일정 ID는 0-9, a-v 문자만 허용 → 16진수 해시 사용.
        # 같은 일정은 항상 같은 ID가 되어 중복으로 들어가지 않아요.
        return "rsc" + hashlib.sha1(self.key.encode()).hexdigest()

    def _time(self, value: dt.date | dt.datetime) -> dict:
        if isinstance(value, dt.datetime):
            return {"dateTime": value.astimezone(KST).isoformat(), "timeZone": "Asia/Seoul"}
        return {"date": value.isoformat()}

    def to_google(self) -> dict:
        body = {
            "summary": self.title,
            "description": self.description,
            "start": self._time(self.start),
            "end": self._time(self.end),
        }
        if self.url:
            body["source"] = {"title": self.source, "url": self.url}
        fingerprint = hashlib.sha1(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        body["extendedProperties"] = {
            "private": {
                "managedBy": "rescene-calendar",
                "source": self.source,
                "sourceId": self.source_id,
                "fingerprint": fingerprint,
            }
        }
        return body
