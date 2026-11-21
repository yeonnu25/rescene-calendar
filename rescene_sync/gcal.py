"""구글 캘린더에 일정 넣기 / 고치기 / 지우기 (서비스 계정 사용)."""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import quote, unquote

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
API = "https://www.googleapis.com/calendar/v3"


def _extract_calendar_id(raw: str) -> str:
    """캘린더 ID를 정리합니다.

    공백·줄바꿈·따옴표를 지우고, 실수로 캘린더 '주소(URL)'를 넣었으면
    (공개 URL, iCal 주소 등) 그 안에 들어 있는 캘린더 ID를 꺼냅니다.
    """
    value = raw.strip().strip("'\"").strip()
    if "://" in value or "%40" in value:
        m = re.search(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]+)", unquote(value))
        if m:
            log.info("캘린더 ID 자리에 주소(URL)가 들어 있어서, 그 안의 캘린더 ID를 꺼내 썼어요")
            return m.group(1)
    return value


class GoogleCalendar:
    def __init__(self, service_account_json: str, calendar_id: str):
        info = json.loads(service_account_json)
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        self.session = AuthorizedSession(creds)
        self.sa_email = info.get("client_email", "?")
        # 복사·붙여넣기 할 때 섞여 들어오는 공백, 줄바꿈, 따옴표 제거
        self.calendar_id = _extract_calendar_id(calendar_id)
        self.base = f"{API}/calendars/{quote(self.calendar_id, safe='')}/events"

    def describe_problem(self) -> str:
        """404일 때 비밀 값을 드러내지 않고 캘린더 ID의 '모양'만 알려줍니다."""
        cid = self.calendar_id
        domain = cid.split("@", 1)[1] if "@" in cid else "(없음)"
        return (
            "구글 캘린더가 이 캘린더를 찾지 못했어요. 점검 결과:\n"
            f"  - 서비스 계정: {self.sa_email}\n"
            f"  - 캘린더 ID 길이: {len(cid)}자\n"
            f"  - 캘린더 ID의 @ 뒷부분: {domain}  (보통 group.calendar.google.com 이어야 해요)\n"
            f"  - 중간에 공백 포함: {'예' if any(c.isspace() for c in cid) else '아니오'}\n"
            "  → 캘린더 ID가 맞다면, 그 캘린더의 공유 목록에 위 서비스 계정이 있는지 확인하세요."
        )

    def _check(self, r, what: str):
        if r.status_code == 404 and what == "목록 조회":
            raise RuntimeError(self.describe_problem())
        if r.status_code >= 400:
            raise RuntimeError(f"구글 캘린더 {what} 실패 ({r.status_code}): {r.text[:500]}")
        return r

    def managed_events(self) -> list[dict]:
        """이 프로그램이 만든 일정만 가져옵니다 (내가 직접 만든 일정은 건드리지 않음)."""
        items, token = [], None
        while True:
            params = {
                "privateExtendedProperty": "managedBy=rescene-calendar",
                "maxResults": 2500,
                "showDeleted": "false",
            }
            if token:
                params["pageToken"] = token
            data = self._check(self.session.get(self.base, params=params, timeout=30), "목록 조회").json()
            items.extend(data.get("items", []))
            token = data.get("nextPageToken")
            if not token:
                return items

    def insert(self, event_id: str, body: dict) -> str:
        r = self.session.post(self.base, json={**body, "id": event_id}, timeout=30)
        if r.status_code != 409:
            self._check(r, "추가")
            return "added"
        # 같은 ID가 이미 있음 → 사용자가 캘린더에서 직접 지운 일정이면 존중하고 다시 넣지 않음
        existing = self._check(self.session.get(f"{self.base}/{event_id}", timeout=30), "조회").json()
        if existing.get("status") == "cancelled":
            return "skipped"
        self.update(event_id, body)
        return "updated"

    def update(self, event_id: str, body: dict) -> None:
        self._check(self.session.put(f"{self.base}/{event_id}", json=body, timeout=30), "수정")

    def delete(self, event_id: str) -> None:
        r = self.session.delete(f"{self.base}/{event_id}", timeout=30)
        if r.status_code not in (404, 410):
            self._check(r, "삭제")
