export default {
  async fetch(req) {
    const cors = {
      'access-control-allow-origin': '*',
      'access-control-allow-headers': '*',
      'access-control-allow-methods': 'GET,POST,OPTIONS'
    };
    if (req.method === 'OPTIONS') return new Response(null, { headers: cors });

    const u = new URL(req.url).searchParams.get('u');
    if (!u) return new Response('u required', { status: 400, headers: cors });

    const h = new Headers();
    ['accept', 'content-type', 'x-frontend-id', 'x-frontend-version',
     'x-request-with', 'x-access-right-key', 'x-client-os-type'].forEach(function (k) {
      const v = req.headers.get(k);
      if (v) h.set(k, v);
    });
    h.set('user-agent',
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36');
    h.set('referer', 'https://www.nicovideo.jp/');
    h.set('origin', 'https://www.nicovideo.jp');

    const init = { method: req.method, headers: h, redirect: 'follow' };
    if (req.method !== 'GET' && req.method !== 'HEAD') init.body = await req.text();

    const r = await fetch(u, init);
    const out = new Headers(r.headers);
    Object.keys(cors).forEach(function (k) { out.set(k, cors[k]); });
    return new Response(r.body, { status: r.status, headers: out });
  }
};
