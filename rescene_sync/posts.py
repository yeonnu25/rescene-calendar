"""AI가 읽을 '글' 모으기: Mnet Plus Notice 게시판 + 유튜브 커뮤니티 게시물."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field

import requests

from .models import KST

log = logging.getLogger(__name__)

SPACE_ID = "rescene-official"
MNET_API = f"https://artist.mnetplus.world/svc/stg/{SPACE_ID}/content/api/v1/boardcontents"
MNET_NOTICE_BOARD = "67a5c573036dc37e1e6d248c"  # 커뮤니티 > Notice (소속사 공지)
MNET_POST_PAGE = f"https://artist.mnetplus.world/main/stg/{SPACE_ID}/community/board/{MNET_NOTICE_BOARD}/post/"

YT_CHANNELS = {
    "RESCENE_official": "RESCENE 공식 유튜브",
    "helloiamwoninicetomeetyou": "원이 개인 채널",
}

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


@dataclass
class Post:
    source: str            # mnet-notice / yt-post
    id: str
    where: str             # 사람이 읽을 출처 이름
    title: str
    text: str
    published: dt.datetime
    url: str
    images: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.id}"

    @property
    def version(self) -> str:
        """글 내용이 바뀌었는지 알아보는 지문. 바뀌면 AI가 다시 읽어요."""
        raw = json.dumps([self.title, self.text, self.images[:3]], ensure_ascii=False)
        return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _utc(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(KST)


# ---------------------------------------------------------------- Mnet Plus

def mnet_notices(since: dt.datetime) -> list[Post]:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    r = s.get(MNET_API, params={"boardId": MNET_NOTICE_BOARD, "size": 30}, timeout=30)
    r.raise_for_status()
    items = r.json()["boardContents"]["items"]
    posts = []
    for it in items:
        published = _utc(it["createdAt"])
        if published < since:
            continue
        # 목록에는 본문이 500자까지만 있어서, 글마다 전체 내용을 다시 받아요
        d = s.get(f"{MNET_API}/{it['id']}", timeout=30)
        d.raise_for_status()
        c = d.json()["boardContent"]
        posts.append(Post(
            source="mnet-notice",
            id=c["id"],
            where="Mnet Plus 리센느 공지",
            title=(c.get("title") or "").strip(),
            text=(c.get("body") or "").strip(),
            published=published,
            url=MNET_POST_PAGE + c["id"],
            images=list(c.get("images") or []),
        ))
    log.info("Mnet Plus 공지: 최근 글 %d개", len(posts))
    return posts


# ---------------------------------------------------------------- YouTube 커뮤니티

_REL = re.compile(r"(\d+)\s*(초|분|시간|일|주|개월|년)")
_UNIT = {"초": 1 / 86400, "분": 1 / 1440, "시간": 1 / 24, "일": 1, "주": 7, "개월": 30, "년": 365}


def _relative(text: str, now: dt.datetime) -> dt.datetime:
    """'3일 전', '1개월 전(수정됨)' 같은 표시를 대략적인 날짜로 바꿔요."""
    m = _REL.search(text or "")
    if not m:
        return now
    return now - dt.timedelta(days=int(m.group(1)) * _UNIT[m.group(2)])


def _find(obj, key: str, out: list):
    if isinstance(obj, dict):
        if key in obj:
            out.append(obj[key])
        for v in obj.values():
            _find(v, key, out)
    elif isinstance(obj, list):
        for v in obj:
            _find(v, key, out)
    return out


def _runs_text(o: dict | None) -> str:
    return "".join(r.get("text", "") for r in (o or {}).get("runs", []))


def _post_images(p: dict) -> list[str]:
    urls = []
    for img in _find(p.get("backstageAttachment", {}), "backstageImageRenderer", []):
        thumbs = img.get("image", {}).get("thumbnails", [])
        if thumbs:
            urls.append(thumbs[-1]["url"])
    return urls


def _headline(text: str) -> str:
    """게시물 첫머리에서 제목 삼을 줄 고르기 ('[🗳]' 처럼 기호뿐인 줄은 건너뜀)."""
    for line in text.splitlines():
        if len(re.findall(r"\w", line)) >= 4:
            return line.strip()[:80]
    return text.strip()[:80]


def youtube_posts(since: dt.datetime) -> list[Post]:
    """채널의 '게시물' 탭 웹페이지를 읽어요 (공식 API가 없어서)."""
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    s.cookies.set("SOCS", "CAI", domain=".youtube.com")  # 쿠키 동의 화면 건너뛰기
    now = dt.datetime.now(KST)
    posts = []
    for handle, name in YT_CHANNELS.items():
        r = s.get(f"https://www.youtube.com/@{handle}/posts", params={"hl": "ko", "gl": "KR"}, timeout=30)
        r.raise_for_status()
        m = re.search(r"var ytInitialData = (\{.*?\});</script>", r.text, re.S)
        if not m:
            raise RuntimeError(f"유튜브 {name} 게시물 페이지 모양이 바뀌었어요")
        found = _find(json.loads(m.group(1)), "backstagePostRenderer", [])
        count = 0
        for p in found:
            published = _relative(_runs_text(p.get("publishedTimeText")), now)
            if published < since:
                continue
            text = _runs_text(p.get("contentText")).strip()
            posts.append(Post(
                source="yt-post",
                id=p["postId"],
                where=f"유튜브 {name} 게시물",
                title=_headline(text),
                text=text,
                published=published,
                url=f"https://www.youtube.com/post/{p['postId']}",
                images=_post_images(p),
            ))
            count += 1
        log.info("유튜브 %s 게시물: 최근 글 %d개", name, count)
    return posts
