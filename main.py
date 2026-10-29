"""nico-viewer-plus — ニコニコ動画ビューア (Render デプロイ用)"""
import asyncio
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

API = "https://snapshot.search.nicovideo.jp/api/v2/snapshot/video/contents/search"
THUMB = "https://nicovideo.cdn.nimg.jp/thumbnails/{0}/{0}.{1}"
EMBED = "https://embed.nicovideo.jp/watch/{0}"
UA = "nico-viewer-plus/1.0 (personal project)"
JST = timezone(timedelta(hours=9))

FIELDS = ("contentId,title,description,viewCounter,commentCounter,mylistCounter,"
          "likeCounter,lengthSeconds,thumbnailUrl,startTime,tags,genre")

SORTS = {"view": "-viewCounter", "new": "-startTime",
         "comment": "-commentCounter", "like": "-likeCounter"}
SORT_LABELS = {"view": "再生数順", "new": "新着順",
               "comment": "コメント順", "like": "いいね順"}

GENRES = ["一般", "エンタメ", "音楽", "スポーツ", "ゲーム", "動物", "料理", "政治",
          "技術", "科学", "手芸", "旅行", "趣味", "アニメ", "映画", "歴史",
          "ニコニコ動画", "その他"]

TERMS = {"24h": 1, "week": 7, "month": 30}
TERM_LABELS = {"24h": "24時間", "week": "週間", "month": "月間"}

PER_PAGE = 32

_cache: dict[str, tuple[float, object]] = {}
_locks: dict[str, asyncio.Lock] = {}
_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _client
    _client = httpx.AsyncClient(timeout=12.0, headers={"User-Agent": UA},
                                follow_redirects=True)
    yield
    await _client.aclose()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ---------- helpers ----------

def fmt_count(n: int) -> str:
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}億"
    if n >= 10_000:
        return f"{n / 10_000:.1f}万"
    return f"{n:,}"


def fmt_time(sec: int) -> str:
    sec = int(sec or 0)
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_date(iso: str | None) -> str:
    if not iso:
        return "-"
    try:
        dt = datetime.fromisoformat(iso).astimezone(JST)
        return dt.strftime("%Y/%m/%d %H:%M")
    except Exception:
        return iso


templates.env.filters["count"] = fmt_count
templates.env.filters["duration"] = fmt_time
templates.env.filters["date"] = fmt_date


async def cached(key: str, ttl: float, factory):
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        hit = _cache.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        value = await factory()
        _cache[key] = (time.time() + ttl, value)
        if len(_cache) > 800:
            for k, (exp, _) in list(_cache.items()):
                if exp < time.time():
                    _cache.pop(k, None)
        return value


def norm(item: dict) -> dict:
    cid = item.get("contentId") or ""
    return {
        "id": cid,
        "title": item.get("title") or "(無題)",
        "views": item.get("viewCounter") or 0,
        "comments": item.get("commentCounter") or 0,
        "mylists": item.get("mylistCounter") or 0,
        "likes": item.get("likeCounter") or 0,
        "seconds": item.get("lengthSeconds") or 0,
        "start": item.get("startTime"),
        "genre": item.get("genre"),
        "tags": (item.get("tags") or "").split(),
        "thumb": f"/thumb/{cid}",
    }


async def search(keyword: str, *, sort: str = "view", genre: str = "",
                 page: int = 1, targets: str = "title,description,tags",
                 filters: list[tuple[str, str]] | None = None,
                 limit: int = PER_PAGE) -> dict:
    page = max(1, page)
    params = [
        ("q", keyword),
        ("targets", targets),
        ("fields", FIELDS),
        ("_sort", SORTS.get(sort, "-viewCounter")),
        ("_offset", str((page - 1) * limit)),
        ("_limit", str(limit)),
        ("_context", "nico_viewer_plus"),
    ]
    if genre:
        params.append(("filters[genre][0]", genre))
    for k, v in (filters or []):
        params.append((k, v))

    key = "s:" + repr(params)

    async def run():
        r = await _client.get(API, params=params)
        r.raise_for_status()
        return r.json()

    try:
        data = await cached(key, 180, run)
    except Exception:
        return {"items": [], "total": 0, "error": True}

    meta = data.get("meta") or {}
    items = [norm(x) for x in (data.get("data") or []) if x.get("contentId")]
    return {"items": items, "total": meta.get("totalCount") or 0, "error": False}


async def fetch_one(video_id: str) -> dict | None:
    async def run():
        r = await _client.get(API, params={
            "q": video_id, "targets": "title", "fields": FIELDS,
            "_sort": "-startTime", "_limit": "1",
            "_context": "nico_viewer_plus",
        })
        r.raise_for_status()
        return r.json()

    data = await cached(f"v:{video_id}", 900, run)
    for row in data.get("data") or []:
        if row.get("contentId") == video_id:
            return norm(row)
    return None


def remember(request: Request, video: dict) -> str:
    import json
    try:
        hist = json.loads(request.cookies.get("history", "[]"))
    except Exception:
        hist = []
    hist = [h for h in hist if h.get("id") != video["id"]]
    hist.insert(0, {"id": video["id"], "title": video["title"]})
    return json.dumps(hist[:24], ensure_ascii=False)


def ctx(request: Request, **kw) -> dict:
    return {"request": request, "GENRES": GENRES, "SORTS": SORTS,
            "SORT_LABELS": SORT_LABELS, "TERMS": TERMS,
            "TERM_LABELS": TERM_LABELS, **kw}


# ---------- routes ----------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    hot = await search("", sort="view", filters=[
        ("filters[startTime][gte]",
         (datetime.now(JST) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S+09:00"))
    ], limit=24)
    return templates.TemplateResponse("index.html", ctx(request, hot=hot["items"]))


@app.get("/search", response_class=HTMLResponse)
async def search_page(request: Request,
                      q: str = Query("", max_length=120),
                      sort: str = Query("view"),
                      genre: str = Query(""),
                      page: int = Query(1, ge=1, le=200)):
    keyword = q.strip()
    result = await search(keyword, sort=sort, genre=genre, page=page) if keyword \
        else {"items": [], "total": 0, "error": False}
    return templates.TemplateResponse("search.html", ctx(
        request, q=keyword, sort=sort, genre=genre, page=page,
        result=result, pages=min(200, (result["total"] + PER_PAGE - 1) // PER_PAGE)))


@app.get("/ranking", response_class=HTMLResponse)
async def ranking_page(request: Request,
                       genre: str = Query(""),
                       term: str = Query("24h")):
    days = TERMS.get(term, 1)
    since = (datetime.now(JST) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S+09:00")
    result = await search("", sort="view", genre=genre, limit=50,
                          filters=[("filters[startTime][gte]", since)])
    return templates.TemplateResponse("ranking.html", ctx(
        request, genre=genre, term=term, items=result["items"]))


@app.get("/watch/{video_id}", response_class=HTMLResponse)
async def watch_page(request: Request, video_id: str):
    video_id = re.sub(r"[^A-Za-z0-9_]", "", video_id)
    video = await fetch_one(video_id)
    if not video:
        return templates.TemplateResponse("search.html", ctx(
            request, q=video_id, sort="view", genre="", page=1,
            result={"items": [], "total": 0, "error": True}, pages=0), status_code=404)

    related = {"items": []}
    if video["tags"]:
        related = await search(video["tags"][0], targets="tagsExact", limit=12)

    resp = templates.TemplateResponse("watch.html", ctx(
        request, video=video, embed=EMBED.format(video_id),
        related=[x for x in related["items"] if x["id"] != video_id][:12]))
    resp.set_cookie("history", remember(request, video), max_age=60 * 60 * 24 * 90,
                    httponly=True, samesite="lax")
    return resp


@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    import json
    try:
        hist = json.loads(request.cookies.get("history", "[]"))
    except Exception:
        hist = []
    return templates.TemplateResponse("search.html", ctx(
        request, q="", sort="view", genre="", page=1,
        result={"items": [], "total": 0, "error": False}, pages=0, history=hist))


@app.get("/history/clear")
async def clear_history():
    resp = RedirectResponse("/history", status_code=303)
    resp.delete_cookie("history")
    return resp


@app.get("/thumb/{video_id}")
async def thumb(video_id: str):
    cid = re.sub(r"[^A-Za-z0-9_]", "", video_id)

    async def run():
        for size in ("M", "L", "S"):
            try:
                r = await _client.get(THUMB.format(cid, size))
                if r.status_code == 200 and r.content:
                    return r.content
            except Exception:
                continue
        return b""

    body = await cached(f"t:{cid}", 60 * 60 * 12, run)
    if not body:
        return Response(status_code=404)
    return Response(body, media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=43200"})


@app.get("/healthz")
async def healthz():
    return {"ok": True}
