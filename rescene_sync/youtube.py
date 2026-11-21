"""유튜브 채널의 예정된 라이브와 프리미어 가져오기 (YouTube Data API v3).

할당량 절약을 위해 '검색'(1회 100단위)은 쓰지 않고,
업로드 목록(1단위)과 영상 정보(1단위)만 씁니다. 채널당 1회 2단위예요.
"""

from __future__ import annotations

import datetime as dt
import logging

import requests

from .models import Event, KST

log = logging.getLogger(__name__)

API = "https://www.googleapis.com/youtube/v3"
DEFAULT_DURATION = dt.timedelta(hours=1)

CHANNELS = {
    "UCtKtCiaWRz-d3EZn2xd1mdA": "RESCENE 공식 유튜브",
    "UCWpY0eSJtyO-qNAPbKFRSSg": "원이 개인 채널 (helloiamwoninicetomeetyou)",
}


def _parse(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(KST)


def _channel_events(session: requests.Session, api_key: str, channel_id: str, name: str) -> list[Event]:
    uploads = "UU" + channel_id[2:]  # 채널의 '업로드' 재생목록
    r = session.get(f"{API}/playlistItems", params={
        "part": "contentDetails", "playlistId": uploads, "maxResults": 25, "key": api_key,
    }, timeout=30)
    r.raise_for_status()
    ids = [it["contentDetails"]["videoId"] for it in r.json().get("items", [])]
    if not ids:
        return []
    r = session.get(f"{API}/videos", params={
        "part": "snippet,liveStreamingDetails", "id": ",".join(ids), "key": api_key,
    }, timeout=30)
    r.raise_for_status()

    events = []
    for v in r.json().get("items", []):
        live = v.get("liveStreamingDetails") or {}
        scheduled = live.get("scheduledStartTime")
        if not scheduled:
            continue  # 예정 시간이 없는 일반 업로드 영상은 일정이 아님
        start = _parse(live.get("actualStartTime") or scheduled)
        if live.get("actualEndTime"):
            end = max(_parse(live["actualEndTime"]), start + dt.timedelta(minutes=15))
        else:
            end = start + DEFAULT_DURATION
        url = f"https://www.youtube.com/watch?v={v['id']}"
        events.append(Event(
            source="youtube",
            source_id=v["id"],
            title=f"[YouTube] {v['snippet']['title']}",
            description=f"출처: {name}\n{url}",
            start=start,
            end=end,
            url=url,
        ))
    return events


def fetch(api_key: str) -> list[Event]:
    session = requests.Session()
    events: list[Event] = []
    for cid, name in CHANNELS.items():
        found = _channel_events(session, api_key, cid, name)
        log.info("YouTube %s: 예정/라이브 영상 %d개", name, len(found))
        events.extend(found)
    return events
