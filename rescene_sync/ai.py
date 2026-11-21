"""Gemini에게 글을 읽히고, 날짜·시간이 있는 일정만 뽑아 받기."""

from __future__ import annotations

import base64
import datetime as dt
import json
import logging
import os
import time

import requests

from .posts import Post

log = logging.getLogger(__name__)

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# 앞의 모델이 없어졌거나 막히면 다음 모델로 넘어가요 (모두 무료 등급이 있는 모델).
# 날짜 뽑기는 가벼운 일이라 빠르고 덜 붐비는 lite 모델을 먼저 써요.
MODELS = [m for m in [os.environ.get("GEMINI_MODEL"),
                      "gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-3.8-flash"] if m]
MAX_IMAGES = 3
MAX_IMAGE_BYTES = 4_000_000

INSTRUCTIONS = """\
당신은 K-pop 그룹 리센느(RESCENE)의 팬 캘린더를 관리하는 비서예요.
아래 글(소속사 공지 또는 유튜브 채널 게시물)을 읽고, 팬이 캘린더에 적어 둘 만한
'날짜가 정해진 일정'만 뽑아 주세요.

넣을 것:
- 방송 출연, 음원·앨범 발매, 영상 업로드·프리미어·라이브, 공연, 팬사인회, 팬미팅, 쇼케이스
- 예약 판매·응모·투표·이벤트의 시작이나 마감 (마감은 제목 끝에 "마감"을 붙이기)
- 이모티콘·굿즈 출시처럼 날짜가 정해진 출시

넣지 말 것:
- 날짜가 없거나 "추후 공지", "이번 달 중" 처럼 흐릿한 것
- 이미 지난 일을 알리는 글(예: "OUT NOW", "~ 달성")의 그 지난 일
- 법적 대응, 팬 에티켓, 고객센터 휴무 같은 운영 안내
- 아래 '이미 캘린더에 있는 일정'과 같은 일정 (날짜와 내용이 같으면 같은 일정)

규칙:
- 연도가 없으면 글이 올라온 날짜 기준으로 가장 자연스러운 연도를 고르세요.
- 시간은 한국 시간(KST) 24시간제 "HH:MM". 시간이 없으면 time은 null.
- "11:59PM"은 "23:59", "오후 7시"는 "19:00".
- 기간(예: 11/1 ~ 11/10)이면 date에 시작일, end_date에 종료일. 종료 시각이 있으면 end_time.
- 시작일 없이 "~ 11월 10일 23:59"처럼 마감만 있으면 date와 time에 마감 시각을 넣고 제목에 "마감".
- 제목은 한국어로 짧게(30자 이내), 무엇인지 바로 알 수 있게. 그룹 이름은 빼도 돼요.
- 일정이 없으면 빈 목록을 돌려주세요. 확실하지 않으면 넣지 마세요.
"""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "events": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "title": {"type": "STRING"},
                    "date": {"type": "STRING", "description": "YYYY-MM-DD"},
                    "time": {"type": "STRING", "nullable": True, "description": "HH:MM (KST) 또는 null"},
                    "end_date": {"type": "STRING", "nullable": True, "description": "YYYY-MM-DD 또는 null"},
                    "end_time": {"type": "STRING", "nullable": True, "description": "HH:MM (KST) 또는 null"},
                },
                "required": ["title", "date"],
            },
        }
    },
    "required": ["events"],
}


class AIError(RuntimeError):
    pass


def _image_part(session: requests.Session, url: str) -> dict | None:
    try:
        r = session.get(url, timeout=30)
        r.raise_for_status()
    except requests.RequestException:
        log.warning("  이미지를 받지 못해서 건너뛰어요: %s", url[:80])
        return None
    mime = r.headers.get("Content-Type", "image/jpeg").split(";")[0]
    if not mime.startswith("image/") or len(r.content) > MAX_IMAGE_BYTES:
        return None
    return {"inline_data": {"mime_type": mime, "data": base64.b64encode(r.content).decode()}}


def extract(post: Post, known: list[str], api_key: str, today: dt.date) -> list[dict]:
    session = requests.Session()
    context = (
        f"오늘 날짜: {today.isoformat()}\n"
        f"글이 올라온 날짜(대략): {post.published.date().isoformat()}\n"
        f"출처: {post.where}\n\n"
        "이미 캘린더에 있는 일정:\n" + ("\n".join(known) if known else "(없음)") + "\n\n"
        f"--- 글 제목 ---\n{post.title}\n\n--- 글 본문 ---\n{post.text or '(본문 없음, 이미지 참고)'}"
    )
    parts: list[dict] = [{"text": context}]
    # 본문이 짧으면 내용이 이미지에 들어 있는 경우가 많아서 이미지도 같이 보여줘요
    if len(post.text) < 300:
        for url in post.images[:MAX_IMAGES]:
            part = _image_part(session, url)
            if part:
                parts.append(part)

    body = {
        "system_instruction": {"parts": [{"text": INSTRUCTIONS}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseMimeType": "application/json", "responseSchema": SCHEMA},
    }
    last = None
    for model in MODELS:
        r = None
        for attempt in range(3):
            started = time.monotonic()
            try:
                r = session.post(API.format(model=model), json=body,
                                 headers={"x-goog-api-key": api_key}, timeout=(10, 90))
            except requests.RequestException as e:  # 시간 초과 등 → 같은 모델은 그만, 다음 모델로
                last = f"{model}: {type(e).__name__}"
                log.warning("  %s 응답 없음 (%.0f초) → 다음 모델 시도", model, time.monotonic() - started)
                r = None
                break
            log.info("  %s 응답 %d (%.0f초)", model, r.status_code, time.monotonic() - started)
            if r.status_code == 429 or r.status_code >= 500:  # 잠깐 너무 많이 썼거나 서버 문제
                last = f"{model}: {r.status_code} {r.text[:200]}"
                time.sleep(20 * (attempt + 1))
                r = None
                continue
            break
        if r is None:
            continue  # 이 모델은 실패 → 다음 모델
        if r.status_code in (400, 403, 404) and "API key" not in r.text and "PERMISSION" not in r.text:
            last = f"{model}: {r.status_code} {r.text[:200]}"
            log.warning("  %s 사용 불가: %s", model, r.text[:200])
            continue  # 모델 이름이 없어졌거나 이 모델을 못 씀 → 다음 모델
        if r.status_code >= 400:
            raise AIError(f"Gemini 요청 실패 ({r.status_code}): {r.text[:300]}")
        data = r.json()
        try:
            text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"]
                           if not p.get("thought"))
            return json.loads(text).get("events", [])
        except (KeyError, IndexError, ValueError) as e:
            raise AIError(f"Gemini 답변을 이해하지 못했어요: {str(data)[:300]}") from e
    raise AIError(f"쓸 수 있는 Gemini 모델이 없어요. 마지막 오류: {last}")
