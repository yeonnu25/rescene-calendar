"""리센느 일정을 구글 캘린더로 동기화합니다.

실행: python -m rescene_sync

필요한 환경 변수 (GitHub에서는 Secrets에 넣어요):
  GOOGLE_SERVICE_ACCOUNT_JSON  서비스 계정 키 파일(JSON)의 내용 전체
  GOOGLE_CALENDAR_ID           일정을 넣을 캘린더의 ID
  YOUTUBE_API_KEY              유튜브 API 키 (없으면 유튜브는 건너뜀)
  GEMINI_API_KEY               Gemini API 키 (없으면 공지·게시물 AI 읽기는 건너뜀)

구글 설정이 없으면 '연습 모드'로 돌아서, 캘린더는 건드리지 않고
무엇을 할지 화면에만 보여줍니다.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import sys

from . import announcements, mnetplus, youtube
from .models import KST, Event

log = logging.getLogger("rescene_sync")


def _event_start(item: dict) -> dt.datetime | None:
    s = item.get("start", {})
    if "dateTime" in s:
        return dt.datetime.fromisoformat(s["dateTime"])
    if "date" in s:
        return dt.datetime.fromisoformat(s["date"]).replace(tzinfo=KST)
    return None


def _fmt(e: Event) -> str:
    when = e.start.strftime("%Y-%m-%d %H:%M") if not e.all_day else f"{e.start} (하루 종일)"
    return f"{when}  {e.title}"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # 1) 각 출처에서 일정 모으기. 실패한 출처는 '지우기'를 하지 않아요 (안전장치).
    wanted: dict[str, Event] = {}
    windows: dict[str, tuple[dt.datetime, dt.datetime]] = {}
    failed = False

    try:
        events, window = mnetplus.fetch()
        wanted.update({e.key: e for e in events})
        if events:  # 0개면 뭔가 이상한 것일 수 있으니 지우기 안 함
            windows["mnetplus"] = window
    except Exception:
        log.exception("Mnet Plus 일정을 가져오지 못했어요")
        failed = True

    yt_key = os.environ.get("YOUTUBE_API_KEY")
    if yt_key:
        try:
            wanted.update({e.key: e for e in youtube.fetch(yt_key)})
        except Exception:
            log.exception("유튜브 정보를 가져오지 못했어요")
            failed = True
    else:
        log.info("YOUTUBE_API_KEY가 없어서 유튜브는 건너뛰어요")

    gemini_key = os.environ.get("GEMINI_API_KEY")
    if gemini_key:
        try:
            ai_events, problem = announcements.fetch(gemini_key, list(wanted.values()))
            wanted.update({e.key: e for e in ai_events})
            failed = failed or problem
            # AI 일정은 최근 60일 ~ 앞으로의 일정 중에서 AI가 더는 뽑지 않은 것만 지워요
            # (AI 기록은 180일 동안 남아 있어서, 이 기간의 일정은 기록과 비교할 수 있어요)
            if announcements.CACHE_PATH.exists():
                recent = (dt.datetime.now(KST) - dt.timedelta(days=60), dt.datetime(9999, 1, 1, tzinfo=KST))
                windows["mnet-notice"] = windows["yt-post"] = recent
        except Exception:
            log.exception("공지·게시물 AI 읽기에 실패했어요")
            failed = True
    else:
        log.info("GEMINI_API_KEY가 없어서 공지·게시물 AI 읽기는 건너뛰어요")

    # 2) 구글 캘린더 설정이 없으면 연습 모드
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    cal_id = os.environ.get("GOOGLE_CALENDAR_ID")
    if not (sa_json and cal_id):
        log.info("\n[연습 모드] 구글 캘린더 설정이 없어서 목록만 보여줄게요:\n")
        for e in sorted(wanted.values(), key=lambda e: str(e.start)):
            log.info("  %s", _fmt(e))
        log.info("\n총 %d개", len(wanted))
        return 1 if failed else 0

    from .gcal import GoogleCalendar
    cal = GoogleCalendar(sa_json, cal_id)

    # 3) 캘린더와 비교해서 추가 / 수정 / 삭제
    existing = {}
    for item in cal.managed_events():
        p = item.get("extendedProperties", {}).get("private", {})
        existing[f"{p.get('source')}:{p.get('sourceId')}"] = item

    counts = {"added": 0, "updated": 0, "deleted": 0, "skipped": 0}
    for key, e in wanted.items():
        body = e.to_google()
        cur = existing.get(key)
        if cur is None:
            result = cal.insert(e.calendar_id(), body)
            counts[result] += 1
            if result != "skipped":
                log.info("추가: %s", _fmt(e))
        elif cur.get("extendedProperties", {}).get("private", {}).get("fingerprint") != \
                body["extendedProperties"]["private"]["fingerprint"]:
            cal.update(cur["id"], body)
            counts["updated"] += 1
            log.info("수정: %s", _fmt(e))

    # 넣지 않기로 한 채널(원이 개인 채널)의 영상 일정 정리
    for key, item in list(existing.items()):
        if key.startswith("youtube:") and key not in wanted \
                and youtube.EXCLUDED_MARK in item.get("description", ""):
            cal.delete(item["id"])
            del existing[key]
            counts["deleted"] += 1
            log.info("삭제(제외한 채널): %s", item.get("summary"))

    # 확인한 기간 안에 있었는데 출처에서 사라진 일정 = 취소됨 → 삭제
    for key, item in existing.items():
        source = key.split(":", 1)[0]
        if key in wanted or source not in windows:
            continue
        start = _event_start(item)
        lo, hi = windows[source]
        if start and lo <= start < hi:
            cal.delete(item["id"])
            counts["deleted"] += 1
            log.info("삭제(취소된 일정): %s", item.get("summary"))

    log.info("완료: 추가 %(added)d, 수정 %(updated)d, 삭제 %(deleted)d, "
             "내가 지운 일정이라 건너뜀 %(skipped)d", counts)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
