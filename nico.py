"""ニコニコ動画 API クライアント（検索・メタ・コメント・HLS）"""
import asyncio
import html as html_lib
import json
import os
import re
import time
from datetime import timedelta, timezone
from urllib.parse import quote

import httpx

API = "https://snapshot.search.nicovideo.jp/api/v2/snapshot/video/contents/search"
WATCH = "https://www.nicovideo.jp/watch/{0}"
EMBED = "https://embed.nicovideo.jp/watch/{0}"
NVAPI = "https://nvapi.nicovideo.jp/v1/watch/{0}"
HLS = "https://nvapi.nicovideo.jp/v1/watch/{0}/access-rights/hls"
GUEST = "https://www.nicovideo.jp/api/watch/v3_guest/{0}"
KEYAPI = "https://nvapi.nicovideo.jp/v1/comment/keys/thread"
NVCOMMENT = "https://public.nvcomment.nicovideo.jp"
RELAY = os.environ.get("NICO_RELAY", "").strip()
UA = "nico-viewer-plus/1.0 (personal project)"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
JST = timezone(timedelta(hours=9))

FIELDS = ("contentId,title,description,viewCounter,commentCounter,mylistCounter,"
          "likeCounter,lengthSeconds,thumbnailUrl,startTime,tags,genre")

SORTS = {"view": "-viewCounter", "new": "-startTime",
         "comment": "-commentCounter", "like": "-likeCounter"}

GENRES = ["一般", "エンタメ", "音楽", "スポーツ", "ゲーム", "動物", "料理", "政治",
          "技術", "科学", "手芸", "旅行", "趣味", "アニメ", "映画", "歴史",
          "ニコニコ動画", "その他"]

TERMS = {"24h": 1, "week": 7, "month": 30}

NICO_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept-Language": "ja-JP,ja;q=0.9",
    "Accept": "*/*",
    "X-Frontend-Id": "6",
    "X-Frontend-Version": "0",
    "X-Request-With": "https://www.nicovideo.jp",
    "Origin": "https://www.nicovideo.jp",
    "Referer": "https://www.nicovideo.jp/",
}

_client = httpx.AsyncClient(timeout=12.0, headers={"User-Agent": UA},
                            follow_redirects=True)
_browser = httpx.AsyncClient(timeout=10.0, headers=NICO_HEADERS,
                             follow_redirects=True)

_cache = {}
_locks = {}
_warm_at = 0.0
_warm_lock = asyncio.Lock()


def _via(url):
    return RELAY + "?u=" + quote(url, safe="") if RELAY else url


async def close():
    await _client.aclose()
    await _browser.aclose()


async def warmup():
    global _warm_at
    if time.time() - _warm_at < 1800:
        return
    async with _warm_lock:
        if time.time() - _warm_at < 1800:
            return
        try:
            await _browser.get(_via("https://www.nicovideo.jp/"), timeout=8)
            _warm_at = time.time()
        except Exception:
            pass


def thumb_url(url):
    if not url:
        return ""
    return "/thumb?u=" + quote(url, safe="")


async def cached(key, ttl, factory):
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


def norm(item):
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


def build_from_nv(video_id, video, tags):
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


def scan_m3u8(node, out):
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


async def search(keyword, sort="view", genre="", page=1,
                 targets="title,description,tags", filters=None, limit=32):
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


async def fetch_one(video_id):
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
        data = await cached("v:" + video_id, 600, run)
    except Exception:
        return None
    for row in data.get("data") or []:
        if row.get("contentId") == video_id:
            return norm(row)
    return None


async def guest_watch(video_id):
    async def run():
        await warmup()
        r = await _browser.get(
            _via(GUEST.format(video_id)),
            params={"actionTrackId": "AAAAAAAAAA_" + str(int(time.time() * 1000)),
                    "t": str(int(time.time() * 1000))},
            headers={"X-Client-Os-Type": "others"})
        if r.status_code != 200:
            return None
        return (r.json() or {}).get("data")

    try:
        return await cached("g:" + video_id, 300, run)
    except Exception:
        return None


async def nv_watch(video_id):
    async def run():
        await warmup()
        r = await _browser.get(
            _via(NVAPI.format(video_id)),
            params={"actionTrackId": "AAAAAAAAAA_" + str(int(time.time() * 1000))},
            headers={"X-Client-Os-Type": "others"})
        if r.status_code != 200:
            return None
        return (r.json() or {}).get("data")

    try:
        return await cached("n:" + video_id, 300, run)
    except Exception:
        return None


async def watch_data(video_id):
    async def run():
        r = await _browser.get(_via(WATCH.format(video_id)))
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
        return await cached("w:" + video_id, 300, run)
    except Exception:
        return None


async def html_probe(video_id):
    async def run():
        try:
            r = await _browser.get(_via(WATCH.format(video_id)))
        except Exception:
            return None
        text = r.text or ""
        urls = [u.replace("\\u0026", "&") for u in
                re.findall(r'https://[^"\\\s<>]+?\.m3u8[^"\\\s<>]*', text)]
        key = None
        km = re.search(r'"accessRightKey"\s*:\s*"([^"]+)"', text)
        if km:
            key = km.group(1)
        track = None
        tm = re.search(r'"watchTrackId"\s*:\s*"([^"]+)"', text)
        if tm:
            track = tm.group(1)
        quality = re.findall(r'"(video-[a-z0-9\-]+)"', text)
        audio = re.findall(r'"(audio-[a-z0-9\-]+)"', text)

        if not urls and key and track and quality:
            outputs = [[quality[-1], audio[-1]]] if audio else [[quality[-1]]]
            try:
                hr = await _browser.post(
                    _via(HLS.format(video_id)), params={"actionTrackId": track},
                    json={"outputs": outputs},
                    headers={"Content-Type": "application/json",
                             "X-Access-Right-Key": key})
                if hr.status_code in (200, 201):
                    u = ((hr.json() or {}).get("data") or {}).get("contentUrl")
                    if u:
                        urls.append(u)
            except Exception:
                pass
        return {"url": urls[0] if urls else None, "text": text}

    try:
        return await cached("h:" + video_id, 240, run)
    except Exception:
        return None


async def fetch_meta(video_id):
    got = await fetch_one(video_id)
    if got:
        return got
    for source in (guest_watch, nv_watch, watch_data):
        api = await source(video_id)
        if not api or not api.get("video"):
            continue
        tags = [t.get("name") for t in ((api.get("tag") or {}).get("items") or [])
                if t.get("name")]
        return build_from_nv(video_id, api["video"], tags)
    return None


def nvcomment_of(api):
    return (((api or {}).get("comment") or {}).get("nvComment") or {})


def comment_pack(api, video_id):
    c = (api or {}).get("comment") or {}
    targets = []
    for layer in c.get("layers") or []:
        for t in layer.get("threadIds") or []:
            if t.get("id"):
                targets.append({"id": t["id"], "fork": t.get("fork") or 0})
    if not targets:
        return None
    return {
        "threadKey": c.get("threadKey") or "",
        "params": {"targets": targets, "language": "ja-jp",
                   "videoId": video_id,
                   "userId": (c.get("keys") or {}).get("userKey") or ""},
        "server": ((c.get("server") or {}).get("url") or NVCOMMENT).rstrip("/"),
    }


async def thread_key(video_id, given):
    if given:
        return given
    try:
        r = await _browser.get(_via(KEYAPI), params={"videoId": video_id})
        if r.status_code == 200:
            return ((r.json() or {}).get("data") or {}).get("threadKey")
    except Exception:
        pass
    return None


async def fetch_comments(video_id):
    pack = None
    for source in (guest_watch, nv_watch):
        pack = comment_pack(await source(video_id), video_id)
        if pack:
            break
    if not pack:
        pack = comment_pack(await watch_data(video_id), video_id)
    if not pack:
        return []

    key = await thread_key(video_id, pack["threadKey"])
    if not key:
        return []
    server = pack["server"]

    async def run():
        r = await _browser.post(
            _via(server + "/v1/threads?_frontendId=6"),
            json={"params": pack["params"], "threadKey": key, "additionals": {}},
            headers={"Content-Type": "application/json"})
        if r.status_code != 200:
            return []
        out = []
        for thread in ((r.json().get("data") or {}).get("threads") or []):
            for c in thread.get("comments") or []:
                out.append({"v": (c.get("vposMs") or 0) / 1000.0,
                            "body": c.get("body") or "",
                            "cmd": c.get("commands") or []})
        out.sort(key=lambda x: x["v"])
        return out

    try:
        return await cached("c:" + video_id, 300, run)
    except Exception:
        return []


async def fetch_stream(video_id):
    api = await guest_watch(video_id) or await nv_watch(video_id)
    urls = []
    pack = comment_pack(api, video_id) if api else None

    if api:
        domand = ((api.get("media") or {}).get("domand")) or {}
        key = domand.get("accessRightKey")
        videos = sorted(domand.get("videos") or [], key=lambda v: v.get("height") or 0)
        audios = sorted(domand.get("audios") or [], key=lambda a: a.get("bitRate") or 0)
        if key and videos:
            under = [v for v in videos if (v.get("height") or 0) <= 720]
            video = (under or videos)[-1]
            audio = audios[-1] if audios else None
            outputs = [[video["id"], audio["id"]]] if audio else [[video["id"]]]
            track = ((api.get("client") or {}).get("watchTrackId")
                     or "AAAAAAAAAA_" + str(int(time.time() * 1000)))
            try:
                r = await _browser.post(
                    _via(HLS.format(video_id)), params={"actionTrackId": track},
                    json={"outputs": outputs},
                    headers={"Content-Type": "application/json",
                             "X-Access-Right-Key": key})
                if r.status_code in (200, 201):
                    u = ((r.json() or {}).get("data") or {}).get("contentUrl")
                    if u:
                        urls.append(u)
            except Exception:
                pass
        scan_m3u8(api, urls)

    if not urls:
        probe = await html_probe(video_id)
        if probe and probe.get("url"):
            urls.append(probe["url"])

    return {"url": urls[0] if urls else None,
            "comments": pack if pack and pack.get("threadKey") else None}


async def diag(video_id):
    out = {"relay": RELAY or "(なし)"}
    try:
        r = await _browser.get(_via("https://www.nicovideo.jp/"), timeout=8)
        out["warmup"] = [r.status_code, len(_browser.cookies)]
    except Exception as e:
        out["warmup"] = ["ERR", repr(e)[:140]]

    track = "AAAAAAAAAA_" + str(int(time.time() * 1000))
    for name, url, params in (
        ("guest", GUEST.format(video_id), {"actionTrackId": track}),
        ("nvapi", NVAPI.format(video_id), {"actionTrackId": track}),
        ("watch", WATCH.format(video_id), None),
    ):
        try:
            r = await _browser.get(_via(url), params=params)
            out[name] = [r.status_code, r.text[:200].replace("\n", " ")]
        except Exception as e:
            out[name] = ["ERR", repr(e)[:140]]
    return out


async def raw_probe(video_id):
    await warmup()
    try:
        r = await _browser.get(_via(GUEST.format(video_id)), params={
            "actionTrackId": "AAAAAAAAAA_" + str(int(time.time() * 1000)),
            "t": str(int(time.time() * 1000))},
            headers={"X-Client-Os-Type": "others"})
    except Exception as e:
        return {"err": repr(e)[:200]}
    out = {"status": r.status_code, "relay": RELAY or "(なし)"}
    try:
        j = r.json()
    except Exception:
        out["text"] = r.text[:400]
        return out
    d = j.get("data") or {}
    out["okReason"] = d.get("okReason")
    domand = ((d.get("media") or {}).get("domand")) or {}
    out["domand"] = list(domand.keys()) if isinstance(domand, dict) else domand
    out["videos"] = len(domand.get("videos") or [])
    out["audios"] = len(domand.get("audios") or [])
    out["key"] = (domand.get("accessRightKey") or "")[:20]
    out["comment_keys"] = list((d.get("comment") or {}).keys())
    return out
