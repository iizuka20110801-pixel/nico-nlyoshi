"""nico-viewer-plus — ニコニコ動画ビューア (Render デプロイ用)"""
import asyncio
import html as html_lib
import json
import re
import time
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, unquote, urljoin

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

API = "https://snapshot.search.nicovideo.jp/api/v2/snapshot/video/contents/search"
WATCH = "https://www.nicovideo.jp/watch/{0}"
EMBED = "https://embed.nicovideo.jp/watch/{0}"
NVAPI = "https://nvapi.nicovideo.jp/v1/watch/{0}"
HLS = "https://nvapi.nicovideo.jp/v1/watch/{0}/access-rights/hls"
GUEST = "https://www.nicovideo.jp/api/watch/v3_guest/{0}"
NVCOMMENT = "https://public.nvcomment.nicovideo.jp"
UA = "nico-viewer-plus/1.0 (personal project)"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
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
_browser: httpx.AsyncClient | None = None

NICO_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept-Language": "ja-JP,ja;q=0.9",
    "X-Frontend-Id": "6",
    "X-Frontend-Version": "0",
    "X-Request-With": "https://www.nicovideo.jp",
    "Origin": "https://www.nicovideo.jp",
    "Referer": "https://www.nicovideo.jp/",
}

WATCH_SHELL = """<!doctype html>
<html lang="ja"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ - ニコ動ビューア</title>
<style>
:root{--bg:#0b0b0e;--fg:#e8e8ef;--dim:#8a8a99;--panel:#17171d;--line:#2a2a33;--acc:#e4007f}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
 font-family:system-ui,-apple-system,"Hiragino Sans","Noto Sans JP",sans-serif}
a{color:inherit;text-decoration:none}
header{display:flex;gap:16px;align-items:center;padding:12px 16px;border-bottom:1px solid var(--line)}
header .brand{font-weight:800;color:var(--acc)}
header .sp{flex:1}
main{max-width:1000px;margin:0 auto;padding:16px}
.player{position:relative;width:100%;padding-top:56.25%;height:0;background:#000;
 border-radius:10px;overflow:hidden;border:1px solid var(--line)}
.player .poster{position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover;z-index:0}
.player video{position:absolute;top:0;left:0;width:100%;height:100%;background:#000;z-index:1}
.player iframe{position:absolute;top:0;left:0;width:100%;height:100%;border:0;z-index:1}
.danmaku-layer{position:absolute;top:0;left:0;width:100%;height:100%;overflow:hidden;
 pointer-events:none;z-index:3}
.dm{position:absolute;white-space:nowrap;font-weight:800;color:#fff;line-height:1.2;
 text-shadow:0 0 3px #000,0 0 3px #000,0 0 3px #000,1px 1px 2px #000;will-change:transform}
.dmbox{display:flex;gap:10px;align-items:center;margin:10px 0 0;flex-wrap:wrap}
.dmbox .chip{cursor:pointer;background:var(--panel);border:1px solid var(--line);
 border-radius:999px;padding:5px 12px;font-size:12px;color:var(--fg)}
.note{color:var(--dim);font-size:13px}
.note.bad{color:#f88}
#log{white-space:pre-wrap;font:11px/1.5 ui-monospace,monospace;color:var(--dim);
 background:var(--panel);border:1px solid var(--line);border-radius:8px;
 padding:8px;margin:10px 0 0;max-height:120px;overflow:auto}
.vtitle{font-size:18px;margin:14px 0 6px}
.vsub{display:flex;gap:14px;flex-wrap:wrap;color:var(--dim);font-size:13px}
.tags{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}
.tags a{background:var(--panel);border:1px solid var(--line);border-radius:999px;
 padding:4px 10px;font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
.card img{width:100%;aspect-ratio:16/9;object-fit:cover;display:block}
.card .meta{padding:8px}
.card .title{font-size:13px;line-height:1.4;max-height:3.6em;overflow:hidden}
.card .sub{color:var(--dim);font-size:11px;margin-top:4px}
</style>
</head><body>
<header><a class="brand" href="/">ニコ動ビューア</a><a href="/ranking">ランキング</a>
<a href="/history">履歴</a><span class="sp"></span></header>
<main>
<div class="player" id="player"></div>
<pre id="log"></pre>
<div id="body"></div>
</main>
<script type="application/json" id="vd">__DATA__</script>
<script src="/static/watch.js"></script>
</body></html>
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _client, _browser
    _client = httpx.AsyncClient(timeout=12.0, headers={"User-Agent": UA},
                                follow_redirects=True)
    _browser = httpx.AsyncClient(timeout=15.0, headers=NICO_HEADERS,
                                 follow_redirects=True)
    yield
    await _client.aclose()
    await _browser.aclose()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.exception_handler(Exception)
async def on_error(request: Request, exc: Exception):
    traceback.print_exc()
    return HTMLResponse(
        "<h1>500</h1><pre>" + html_lib.escape(repr(exc)) + "</pre>",
        status_code=500)


# ---------- helpers ----------

def fmt_count(n) -> str:
    n = int(n or 0)
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}億"
    if n >= 10_000:
        return f"{n / 10_000:.1f}万"
    return f"{n:,}"


def fmt_time(sec) -> str:
    sec = int(sec or 0)
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_date(iso) -> str:
    if not iso:
        return "-"
    try:
        return datetime.fromisoformat(iso).astimezone(JST).strftime("%Y/%m/%d %H:%M")
    except Exception:
        return str(iso)


templates.env.filters["count"] = fmt_count
templates.env.filters["duration"] = fmt_time
templates.env.filters["date"] = fmt_date


def thumb_url(url: str | None) -> str:
    if not url:
        return ""
    return "/thumb?u=" + quote(url, safe="")


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
        if len(_cache) > 900:
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
        "thumb": thumb_url(item.get("thumbnailUrl")),
    }


def build_from_nv(video_id: str, video: dict, tags: list[str]) -> dict:
    thumb = video.get("thumbnail") or {}
    return {
        "id": video_id,
        "title": video.get("title") or "(無題)",
        "views": video.get("viewCount") or 0,
        "comments": video.get("commentCount") or 0,
        "mylists": video.get("mylistCount") or 0,
        "likes": 0,
        "seconds": video.get("lengthSeconds") or 0,
        "start": video.get("registeredAt"),
        "genre": None,
        "tags": tags[:12],
        "thumb": thumb_url(thumb.get("player") or thumb.get("largeUrl")),
    }


def scan_m3u8(node, out: list[str]) -> list[str]:
    if node is None:
        return out
    if isinstance(node, str):
        if ".m3u8" in node and node.startswith("http"):
            out.append(node)
        return out
    if isinstance(node, list):
        for x in node:
            scan_m3u8(x, out)
        return out
    if isinstance(node, dict):
        for x in node.values():
            scan_m3u8(x, out)
    return out


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

    async def run():
        r = await _client.get(API, params=params)
        r.raise_for_status()
        return r.json()

    try:
        data = await cached("s:" + repr(params), 180, run)
    except Exception:
        return {"videos": [], "total": 0, "error": True}

    meta = data.get("meta") or {}
    videos = [norm(x) for x in (data.get("data") or []) if x.get("contentId")]
    return {"videos": videos, "total": meta.get("totalCount") or 0, "error": False}


async def fetch_one(video_id: str) -> dict | None:
    async def run():
        r = await _client.get(API, params={
            "q": "", "targets": "title", "fields": FIELDS,
            "filters[contentId][0]": video_id,
            "_sort": "-startTime", "_limit": "1",
            "_context": "nico_viewer_plus",
        })
        r.raise_for_status()
        return r.json()

    try:
        data = await cached(f"v:{video_id}", 600, run)
    except Exception:
        return None
    for row in data.get("data") or []:
        if row.get("contentId") == video_id:
            return norm(row)
    return None


async def nv_watch(video_id: str) -> dict | None:
    async def run():
        r = await _browser.get(
            NVAPI.format(video_id),
            params={"actionTrackId": "AAAAAAAAAA_" + str(int(time.time() * 1000))})
        if r.status_code != 200:
            return None
        return (r.json() or {}).get("data")

    try:
        return await cached(f"n:{video_id}", 300, run)
    except Exception:
        return None


async def guest_watch(video_id: str) -> dict | None:
    track = "AAAAAAAAAA_" + str(int(time.time() * 1000))

    async def run():
        r = await _browser.get(GUEST.format(video_id),
                               params={"actionTrackId": track,
                                       "t": str(int(time.time() * 1000))})
        if r.status_code != 200:
            return None
        return (r.json() or {}).get("data")

    try:
        return await cached(f"g:{video_id}", 300, run)
    except Exception:
        return None


async def watch_data(video_id: str) -> dict | None:
    async def run():
        r = await _browser.get(WATCH.format(video_id))
        if r.status_code != 200:
            return None
        m = re.search(r'id="js-initial-watch-data"[^>]*data-api-data="([^"]*)"', r.text)
        if m:
            try:
                return json.loads(html_lib.unescape(m.group(1)))
            except Exception:
                pass
        m = re.search(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
        if m:
            try:
                blob = json.loads(m.group(1))
                return (((blob.get("props") or {}).get("pageProps") or {})
                        .get("serverResponse") or blob)
            except Exception:
                pass
        return None

    try:
        return await cached(f"w:{video_id}", 300, run)
    except Exception:
        return None


async def fetch_meta(video_id: str) -> dict | None:
    got = await fetch_one(video_id)
    if got:
        return got

    for source in (nv_watch, guest_watch, watch_data):
        api = await source(video_id)
        if not api or not api.get("video"):
            continue
        tags = [t.get("name") for t in ((api.get("tag") or {}).get("items") or [])
                if t.get("name")]
        return build_from_nv(video_id, api["video"], tags)
    return None


def nvcomment_of(api: dict | None) -> dict:
    return (((api or {}).get("comment") or {}).get("nvComment") or {})


async def fetch_comments(video_id: str) -> list[dict]:
    nvc = {}
    for source in (nv_watch, guest_watch, watch_data):
        api = await source(video_id)
        nvc = nvcomment_of(api)
        if nvc.get("params") and nvc.get("threadKey"):
            break

    params = nvc.get("params")
    thread_key = nvc.get("threadKey")
    if not params or not thread_key:
        return []

    server = (nvc.get("server") or NVCOMMENT).rstrip("/")

    async def run():
        r = await _browser.post(
            f"{server}/v1/threads?_frontendId=6",
            json={"params": params, "threadKey": thread_key, "additionals": {}},
            headers={"Content-Type": "application/json"})
        if r.status_code != 200:
            return []
        out = []
        for thread in ((r.json().get("data") or {}).get("threads") or []):
            for c in thread.get("comments") or []:
                out.append({
                    "v": (c.get("vposMs") or 0) / 1000.0,
                    "body": c.get("body") or "",
                    "cmd": c.get("commands") or [],
                })
        out.sort(key=lambda x: x["v"])
        return out

    try:
        return await cached(f"c:{video_id}", 300, run)
    except Exception:
        return []


async def fetch_stream(video_id: str) -> dict:
    urls: list[str] = []
    comments: dict = {}

    for source in (guest_watch, nv_watch):
        api = await source(video_id)
        if not api:
            continue
        nvc = nvcomment_of(api)
        if nvc.get("params") and nvc.get("threadKey") and not comments:
            comments = {"params": nvc["params"],
                        "threadKey": nvc["threadKey"],
                        "server": (nvc.get("server") or NVCOMMENT).rstrip("/")}
        scan_m3u8(api, urls)
        if urls:
            break

    if not urls:
        api = await nv_watch(video_id)
        domand = ((api or {}).get("media") or {}).get("domand") or {}
        key = domand.get("accessRightKey")
        videos = sorted(domand.get("videos") or [],
                        key=lambda v: v.get("height") or 0)
        audios = sorted(domand.get("audios") or [],
                        key=lambda a: a.get("bitRate") or 0)
        if key and videos:
            under = [v for v in videos if (v.get("height") or 0) <= 720]
            video = (under or videos)[-1]
            audio = audios[-1] if audios else None
            outputs = [[video["id"], audio["id"]]] if audio else [[video["id"]]]
            track = ((api.get("client") or {}).get("watchTrackId")
                     or "AAAAAAAAAA_" + str(int(time.time() * 1000)))
            try:
                r = await _browser.post(
                    HLS.format(video_id), params={"actionTrackId": track},
                    json={"outputs": outputs},
                    headers={"Content-Type": "application/json",
                             "X-Access-Right-Key": key})
                if r.status_code in (200, 201):
                    url = ((r.json() or {}).get("data") or {}).get("contentUrl")
                    if url:
                        urls.append(url)
            except Exception:
                pass

    if not comments:
        nvc = nvcomment_of(await watch_data(video_id))
        if nvc.get("params") and nvc.get("threadKey"):
            comments = {"params": nvc["params"],
                        "threadKey": nvc["threadKey"],
                        "server": (nvc.get("server") or NVCOMMENT).rstrip("/")}

    return {"url": urls[0] if urls else None, "comments": comments or None}


def remember(request: Request, video: dict) -> str:
    try:
        hist = json.loads(unquote(request.cookies.get("history", "[]")))
    except Exception:
        hist = []
    hist = [h for h in hist if h.get("id") != video["id"]]
    hist.insert(0, {"id": video["id"], "title": (video["title"] or "")[:50]})
    return quote(json.dumps(hist[:12], ensure_ascii=False), safe="")


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
    return templates.TemplateResponse("index.html", ctx(request, hot=hot["videos"]))


@app.get("/search", response_class=HTMLResponse)
async def search_page(request: Request,
                      q: str = Query("", max_length=120),
                      sort: str = Query("view"),
                      genre: str = Query(""),
                      page: int = Query(1, ge=1, le=200)):
    keyword = q.strip()
    result = await search(keyword, sort=sort, genre=genre, page=page) if keyword \
        else {"videos": [], "total": 0, "error": False}
    return templates.TemplateResponse("search.html", ctx(
        request, q=keyword, sort=sort, genre=genre, page=page, result=result,
        pages=min(200, (result["total"] + PER_PAGE - 1) // PER_PAGE)))


@app.get("/ranking", response_class=HTMLResponse)
async def ranking_page(request: Request,
                       genre: str = Query(""),
                       term: str = Query("24h")):
    days = TERMS.get(term, 1)
    since = (datetime.now(JST) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S+09:00")
    result = await search("", sort="view", genre=genre, limit=50,
                          filters=[("filters[startTime][gte]", since)])
    return templates.TemplateResponse("ranking.html", ctx(
        request, genre=genre, term=term, videos=result["videos"]))


@app.get("/watch/{video_id}", response_class=HTMLResponse)
async def watch_page(request: Request, video_id: str):
    video_id = re.sub(r"[^A-Za-z0-9_]", "", video_id)
    video = await fetch_meta(video_id)
    if not video:
        return templates.TemplateResponse("search.html", ctx(
            request, q=video_id, sort="view", genre="", page=1,
            result={"videos": [], "total": 0, "error": True}, pages=0,
            missing=video_id), status_code=404)

    related = {"videos": []}
    if video["tags"]:
        related = await search(video["tags"][0], targets="tagsExact", limit=12)

    data = {
        "id": video["id"],
        "title": video["title"],
        "embed": EMBED.format(video_id),
        "thumb": video["thumb"],
        "views": video["views"],
        "comments": video["comments"],
        "mylists": video["mylists"],
        "likes": video["likes"],
        "sta
