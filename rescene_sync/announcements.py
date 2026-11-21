"""공지·게시물 → (AI) → 캘린더 일정.

AI가 이미 읽은 글은 state/ai_cache.json에 기록해 둬요.
- 매시간 돌아도 새 글이나 내용이 바뀐 글만 AI에게 보내요 (무료 한도 절약).
- 같은 글은 늘 같은 결과라서 캘린더 일정이 들쭉날쭉하지 않아요.
이 파일은 GitHub Actions가 실행 후 저장소에 자동으로 저장해요.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
import time
from pathlib import Path

from . import ai, posts
from .models import KST, Event

log = logging.getLogger(__name__)

CACHE_PATH = Path(__file__).resolve().parent.parent / "state" / "ai_cache.json"
LOOKBACK_DAYS = 45        # 이보다 오래된 글은 읽지 않아요
MAX_AI_CALLS = 15         # 한 번 실행에 AI 호출 최대 횟수 (나머지는 다음 시간에)
AI_TIME_BUDGET = 8 * 60   # 한 번 실행에 AI 읽기에 쓰는 최대 시간(초)
FORGET_AFTER_DAYS = 180   # 이보다 오래된 글의 기록은 지워요 (캘린더의 지난 일정은 그대로 남아요)
DEFAULT_DURATION = dt.timedelta(hours=1)
LABEL = {"mnet-notice": "[공지]", "yt-post": "[유튜브 공지]"}

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


def load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                          encoding="utf-8")


def _to_event(entry: dict, raw: dict) -> Event | None:
    """AI 답변 한 줄을 캘린더 일정으로. 형식이 이상하면 버려요."""
    title = (raw.get("title") or "").strip()
    date = (raw.get("date") or "").strip()
    if not title or not _DATE.match(date):
        return None
    t, end_date, end_t = raw.get("time"), raw.get("end_date"), raw.get("end_time")
    t = t if t and _TIME.match(t) else None
    end_t = end_t if end_t and _TIME.match(end_t) else None
    end_date = end_date if end_date and _DATE.match(end_date) else None
    try:
        day = dt.date.fromisoformat(date)
        last_day = dt.date.fromisoformat(end_date) if end_date else day
    except ValueError:
        return None
    if last_day < day or (last_day - day).days > 120:
        last_day = day
    # 글이 올라오기 전 일정은 AI의 착각일 가능성이 높아서 버려요
    if day < dt.date.fromisoformat(entry["published"]) - dt.timedelta(days=1):
        return None

    if t:
        start = dt.datetime.combine(day, dt.time.fromisoformat(t), KST)
        if end_t:
            end = dt.datetime.combine(last_day, dt.time.fromisoformat(end_t), KST)
            if end <= start:
                end = start + DEFAULT_DURATION
        elif title.endswith("마감"):
            end = start  # 마감은 '그 시각'만 표시
        else:
            end = start + DEFAULT_DURATION
    else:
        start, end = day, last_day + dt.timedelta(days=1)

    ident = hashlib.sha1(f"{date}|{t}|{title}".encode()).hexdigest()[:10]
    return Event(
        source=entry["source"],
        source_id=f"{entry['id']}:{ident}",
        title=f"{LABEL.get(entry['source'], '[공지]')} {title}",
        description=(
            "AI가 아래 글을 읽고 뽑은 일정이에요. 정확한 내용은 원문에서 확인해 주세요.\n\n"
            f"출처: {entry['where']}\n글 제목: {entry['title']}\n{entry['url']}"
        ),
        start=start,
        end=end,
        url=entry["url"],
    )


def _known_lines(known: list[Event], around: dt.date) -> list[str]:
    lo, hi = around - dt.timedelta(days=7), around + dt.timedelta(days=120)
    lines = []
    for e in sorted(known, key=lambda e: str(e.start)):
        day = e.start.date() if isinstance(e.start, dt.datetime) else e.start
        if lo <= day <= hi:
            when = e.start.strftime("%Y-%m-%d %H:%M") if isinstance(e.start, dt.datetime) else day.isoformat()
            lines.append(f"- {when} {e.title}")
    return lines[:80]


def fetch(api_key: str, known: list[Event]) -> tuple[list[Event], bool]:
    """돌려주는 값: (일정 목록, 문제가 있었는지)"""
    now = dt.datetime.now(KST)
    since = now - dt.timedelta(days=LOOKBACK_DAYS)
    cache = load_cache()
    problem = False

    found: list[posts.Post] = []
    for name, getter in (("Mnet Plus 공지", posts.mnet_notices), ("유튜브 게시물", posts.youtube_posts)):
        try:
            found.extend(getter(since))
        except Exception:
            log.exception("%s을 가져오지 못했어요", name)
            problem = True

    calls = ai_errors = 0
    t0 = time.monotonic()
    for post in sorted(found, key=lambda p: p.published, reverse=True):
        entry = cache.get(post.key)
        if entry and entry.get("version") == post.version:
            continue  # 이미 읽은 글
        if calls >= MAX_AI_CALLS or time.monotonic() - t0 > AI_TIME_BUDGET:
            log.info("이번 실행의 AI 읽기 한도에 닿아서 나머지는 다음에 읽어요")
            break
        if calls:
            time.sleep(4)  # 무료 등급 분당 한도를 넘지 않게 천천히
        calls += 1
        published = entry["published"] if entry else post.published.date().isoformat()
        try:
            raw = ai.extract(post, _known_lines(known, post.published.date()), api_key, now.date())
        except ai.AIError as e:
            log.warning("AI가 '%s' 글을 읽지 못했어요: %s", post.title[:40], e)
            ai_errors += 1
            continue
        cache[post.key] = {
            "source": post.source, "id": post.id, "where": post.where, "title": post.title,
            "url": post.url, "published": published, "version": post.version,
            "checked": now.date().isoformat(), "events": raw,
        }
        save_cache(cache)  # 중간에 멈춰도 읽은 만큼은 남게
        log.info("AI가 읽음: %s → 일정 %d개", post.title[:50], len(raw))

    if calls and ai_errors == calls:
        problem = True  # 전부 실패 = 키나 한도 문제일 가능성 → 실행을 실패로 표시해 이메일로 알림

    # 오래된 기록 정리
    cutoff = (now - dt.timedelta(days=FORGET_AFTER_DAYS)).date().isoformat()
    for k in [k for k, v in cache.items() if v.get("published", "") < cutoff]:
        del cache[k]
    save_cache(cache)

    # 같은 공지가 여러 곳에 올라와도 일정은 하나만 (먼저 올라온 글 기준)
    events, seen = [], set()
    for entry in sorted(cache.values(), key=lambda v: (v.get("published", ""), v["id"])):
        for raw in entry.get("events", []):
            e = _to_event(entry, raw)
            same = (str(e.start), e.title.split(" ", 1)[-1].replace(" ", "")) if e else None
            if e and same not in seen:
                seen.add(same)
                events.append(e)
    return events, problem
