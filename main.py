"""nico-viewer-plus — ニコニコ動画ビューア"""
import html as html_lib
import json
import re
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote, unquote, urljoin

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import nico

PER_PAGE = 32
SORT_LABELS = {"view": "再生数順", "new": "新着順",
               "comment": "コメント順", "like": "いいね順"}
TERM_LABELS = {"24h": "24時間", "week": "週間", "month": "月間"}

SHELL = Path("static/watch_shell.html").read_text(encoding="utf-8")


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await nico.close()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.exception_handler(Exception)
async def on_error(request: Request, exc: Exception):
    traceback.print_exc()
    return HTMLResponse(
        "<h1>500</h1><pre>" + html_lib.escape(repr(exc)) + "</pre>",
        status_code=500)


def fmt_count(n):
    n = int(n or 0)
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}億"
    if n >= 10_000:
        return f"{n / 10_000:.1f}万"
    return f"{n:,}"


def fmt_time(sec):
    sec = int(sec or 0)
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_date(iso):
    if not iso:
        return "-"
    try:
        return datetime.fromisoformat(iso).astimezone(nico.JST).strftime("%Y/%m/%d %H:%M")
    except Exception:
        return str(iso)


templates.env.filters["count"] = fmt_count
templates.env.filters["duration"] = fmt_time
templates.env.filters["date"] = fmt_date


def remember(request, video):
    try:
        hist = json.loads(unquote(request.cookies.get("history", "[]")))
    except Exception:
        hist = []
    hist = [h for h in hist if h.get("id") != video["id"]]
    hist.insert(0, {"id": video["id"], "title": (video["title"] or "")[:50]})
    return quote(json.dumps(hist[:12], ensure_ascii=False), safe="")


def ctx(request, **kw):
    return {"request": request, "GENRES": nico.GENRES, "SORTS": nico.SORTS,
            "SORT_LABELS": SORT_LABELS, "TERMS": nico.TERMS,
            "TERM_LABELS": TERM_LABELS, **kw}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    hot = await nico.search("", sort="view", filters=[
        ("filters[startTime][gte]",
         (datetime.now(nico.JST) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S+09:00"))
    ], limit=24)
    return templates.TemplateResponse("index.html", ctx(request, hot=hot["videos"]))


@app.get("/search", response_class=HTMLResponse)
async def search_page(request: Request,
                      q: str = Query("", max_length=120),
                      sort: str = Query("view"),
                      genre: str = Query(""),
                      page: int = Query(1, ge=1, le=200)):
    keyword = q.strip()
    result = await nico.search(keyword, sort=sort, genre=genre, page=page) if keyword \
        else {"videos": [], "total": 0, "error": False}
    return templates.TemplateResponse("search.html", ctx(
        request, q=keyword, sort=sort, genre=genre, page=page, result=result,
        pages=min(200, (result["total"] + PER_PAGE - 1) // PER_PAGE)))


@app.get("/ranking", response_class=HTMLResponse)
async def ranking_page(request: Request,
                       genre: str = Query(""),
                       term: str = Query("24h")):
    days = nico.TERMS.get(term, 1)
    since = (datetime.now(nico.JST) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S+09:00")
    result = await nico.search("", sort="view", genre=genre, limit=50,
                               filters=[("filters[startTime][gte]", since)])
    return templates.TemplateResponse("ranking.html", ctx(
        request, genre=genre, term=term, videos=result["videos"]))


@app.get("/watch/{video_id}", response_class=HTMLResponse)
async def watch_page(request: Request, video_id: str):
    video_id = re.sub(r"[^A-Za-z0-9_]", "", video_id)
    video = await nico.fetch_meta(video_id)
    if not video:
        return templates.TemplateResponse("search.html", ctx(
            request, q=video_id, sort="view", genre="", page=1,
            result={"videos": [], "total": 0, "error": True}, pages=0,
            missing=video_id), status_code=404)

    related = {"videos": []}
    if video["tags"]:
        related = await nico.search(video["tags"][0], targets="tagsExact", limit=12)

    data = {
        "id": video["id"],
        "title": video["title"],
        "embed": nico.EMBED.format(video_id),
        "thumb": video["thumb"],
        "views": video["views"],
        "comments": video["comments"],
        "mylists": video["mylists"],
        "likes": video["likes"],
        "start": video["start"],
        "tags": video["tags"],
        "related": [{"id": x["id"], "title": x["title"], "thumb": x["thumb"],
                     "views": x["views"]}
                    for x in related["videos"] if x["id"] != video_id][:12],
    }
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = SHELL.replace("__TITLE__", html_lib.escape(video["title"])) \
                .replace("__DATA__", payload)

    resp = HTMLResponse(page)
    resp.set_cookie("history", remember(request, video), max_age=60 * 60 * 24 * 90,
                    httponly=True, samesite="lax")
    return resp


@app.get("/api/stream/{video_id}")
async def api_stream(video_id: str):
    return JSONResponse(await nico.fetch_stream(re.sub(r"[^A-Za-z0-9_]", "", video_id)))


@app.get("/api/comments/{video_id}")
async def api_comments(video_id: str):
    cid = re.sub(r"[^A-Za-z0-9_]", "", video_id)
    return JSONResponse({"comments": await nico.fetch_comments(cid)})


@app.get("/api/brute/{video_id}")
async def api_brute(video_id: str):
    cid = re.sub(r"[^A-Za-z0-9_]", "", video_id)
    data = await nico.cached("b:" + cid, 240, lambda: nico.fetch_stream(cid))
    return JSONResponse(data or {"url": None, "comments": None})


@app.get("/hls")
async def hls_proxy(u: str = Query(..., max_length=2000)):
    if not u.startswith("http"):
        return Response(status_code=404)
    try:
        r = await nico._browser.get(u, headers={"Referer": "https://www.nicovideo.jp/"})
    except Exception:
        return Response(status_code=502)
    if r.status_code != 200:
        return Response(status_code=r.status_code)

    ctype = r.headers.get("content-type", "")
    if "mpegurl" in ctype or u.split("?")[0].endswith(".m3u8"):
        lines = []
        for line in r.text.splitlines():
            if not line or line.startswith("#"):
                if 'URI="' in line:
                    pre, _, rest = line.partition('URI="')
                    inner, _, tail = rest.partition('"')
                    lines.append(
                        f'{pre}URI="/hls?u={quote(urljoin(u, inner), safe="")}"{tail}')
                else:
                    lines.append(line)
                continue
            lines.append("/hls?u=" + quote(urljoin(u, line), safe=""))
        return Response("\n".join(lines),
                        media_type="application/vnd.apple.mpegurl",
                        headers={"Cache-Control": "no-store"})

    return Response(content=r.content, media_type=ctype or "video/mp2t",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    try:
        hist = json.loads(unquote(request.cookies.get("history", "[]")))
    except Exception:
        hist = []
    return templates.TemplateResponse("search.html", ctx(
        request, q="", sort="view", genre="", page=1,
        result={"videos": [], "total": 0, "error": False}, pages=0, history=hist))


@app.get("/history/clear")
async def clear_history():
    resp = RedirectResponse("/history", status_code=303)
    resp.delete_cookie("history")
    return resp


@app.get("/thumb")
async def thumb(u: str = Query("", max_length=400)):
    if not u.startswith("https://") or ".nimg.jp/" not in u:
        return Response(status_code=404)

    async def run():
        try:
            r = await nico._client.get(u)
            if r.status_code == 200:
                return r.content
        except Exception:
            pass
        return None

    data = await nico.cached("t:" + u, 43200, run)
    if data:
        return Response(content=data, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=43200"})
    return Response(status_code=404)


@app.get("/healthz")
async def healthz():
    return {"ok": True}
