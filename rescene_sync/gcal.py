"""구글 캘린더에 일정 넣기 / 고치기 / 지우기 (서비스 계정 사용)."""

from __future__ import annotations

import json
import logging
from urllib.parse import quote

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
API = "https://www.googleapis.com/calendar/v3"


class GoogleCalendar:
    def __init__(self, service_account_json: str, calendar_id: str):
        info = json.loads(service_account_json)
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        self.session = AuthorizedSession(creds)
        self.base = f"{API}/calendars/{quote(calendar_id, safe='')}/events"

    def _check(self, r, what: str):
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
