"""Mnet Plus(리센느 공식 공간)의 일정 가져오기.

Mnet Plus 웹사이트가 내부적으로 쓰는 주소를 그대로 사용합니다.
공식 공개 API가 아니라서 사이트가 바뀌면 고쳐야 할 수 있어요.
"""

from __future__ import annotations

import datetime as dt
import logging

import requests

from .models import Event, KST

log = logging.getLogger(__name__)

SPACE_ID = "rescene-official"
API = f"https://artist.mnetplus.world/svc/stg/{SPACE_ID}/space/api/v1/calendar"
PAGE = f"https://artist.mnetplus.world/main/stg/{SPACE_ID}/schedule"
DEFAULT_DURATION = dt.timedelta(hours=1)
HEADERS = {"User-Agent": "Mozilla/5.0 (rescene-calendar sync)", "Accept": "application/json"}


def _month_start(year: int, month: int) -> dt.datetime:
    return dt.datetime(year, month, 1, tzinfo=KST)


def _add_months(year: int, month: int, n: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) + n
    return total // 12, total % 12 + 1


def _fetch_month(session: requests.Session, year: int, month: int) -> list[dict]:
    start = _month_start(year, month)
    ny, nm = _add_months(year, month, 1)
    end = _month_start(ny, nm) - dt.timedelta(milliseconds=1)
    params = {
        "startAt": start.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "endAt": end.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.999Z"),
        "startAtForAllDay": start.strftime("%Y-%m-%d"),
        "endAtForAllDay": end.strftime("%Y-%m-%d"),
    }
    r = session.get(API, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    if "events" not in data:
        raise RuntimeError(f"Mnet Plus 응답 형식이 예상과 달라요: {str(data)[:200]}")
    return data["events"]


def _parse_utc(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(KST)


def _to_event(raw: dict) -> Event:
    label = (raw.get("label") or {}).get("name", "")
    page_url = f"{PAGE}?eventId={raw['id']}"
    description = "\n".join(
        x for x in [
            f"분류: {label}" if label else "",
            "출처: Mnet Plus 리센느 공식 일정",
            page_url,
        ] if x
    )
    if raw.get("allDay"):
        start = dt.date.fromisoformat(raw["startAtAllDay"])
        last = dt.date.fromisoformat(raw.get("endAtAllDay") or raw["startAtAllDay"])
        end = last + dt.timedelta(days=1)  # 구글 캘린더는 '다음 날'을 끝으로 씀
    else:
        start = _parse_utc(raw["startAt"])
        end = _parse_utc(raw["endAt"]) if raw.get("endAt") else start + DEFAULT_DURATION
    return Event(
        source="mnetplus",
        source_id=raw["id"],
        title=raw["title"],
        description=description,
        start=start,
        end=end,
        url=page_url,
    )


def fetch(months_back: int = 1, months_ahead: int = 6) -> tuple[list[Event], tuple[dt.datetime, dt.datetime]]:
    """일정 목록과 '이 기간은 전부 확인했다'는 기간을 돌려줍니다.

    확인한 기간 안에서 사라진 일정은 취소된 것으로 보고 캘린더에서도 지웁니다.
    """
    today = dt.datetime.now(KST)
    first = _add_months(today.year, today.month, -months_back)
    session = requests.Session()
    events: dict[str, Event] = {}
    for i in range(months_back + months_ahead + 1):
        y, m = _add_months(*first, i)
        for raw in _fetch_month(session, y, m):
            try:
                events[raw["id"]] = _to_event(raw)  # 여러 달에 걸친 일정은 한 번만
            except (KeyError, ValueError) as e:
                log.warning("Mnet Plus 일정 하나를 읽지 못했어요 (%s): %s", e, raw)
    window_start = _month_start(*first)
    window_end = _month_start(*_add_months(*first, months_back + months_ahead + 1))
    log.info("Mnet Plus: 일정 %d개 확인 (%s ~ %s)", len(events), window_start.date(), window_end.date())
    return list(events.values()), (window_start, window_end)
