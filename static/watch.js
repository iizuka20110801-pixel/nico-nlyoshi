(function () {
  var D = JSON.parse(document.getElementById('vd').textContent);
  var RELAY = D.relay || '';
  var player = document.getElementById('player');
  var logbox = document.getElementById('log');
  var body = document.getElementById('body');
  var KEY = '';

  function el(t, c) { var e = document.createElement(t); if (c) e.className = c; return e; }
  function via(u) { return RELAY ? RELAY + '/p/' + encodeURIComponent(u) : u; }
  function proxyUrl(u) { return '/hls?u=' + encodeURIComponent(u) + (KEY ? '&k=' + encodeURIComponent(KEY) : ''); }
  function num(x) {
    x = +x || 0;
    if (x >= 1e8) return (x / 1e8).toFixed(1) + '億';
    if (x >= 1e4) return (x / 1e4).toFixed(1) + '万';
    return x.toLocaleString();
  }
  function dt(s) {
    if (!s) return '-';
    var d = new Date(s);
    if (isNaN(d)) return String(s);
    var p = function (n) { return ('0' + n).slice(-2); };
    return d.getFullYear() + '/' + p(d.getMonth() + 1) + '/' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
  }

  if (D.thumb) { var po = el('img', 'poster'); po.src = D.thumb; player.appendChild(po); }
  var vid = el('video');
  vid.id = 'vid';
  vid.controls = true;
  vid.preload = 'metadata';
  vid.setAttribute('playsinline', '');
  vid.setAttribute('webkit-playsinline', '');
  if (D.thumb) vid.poster = D.thumb;
  player.appendChild(vid);
  var layer = el('div', 'danmaku-layer');
  layer.id = 'layer';
  player.appendChild(layer);

  var dmbox = el('div', 'dmbox');
  var toggle = el('button', 'chip on');
  toggle.textContent = 'コメント ON';
  var state = el('span', 'note');
  state.textContent = 'プレイヤー準備中…';
  dmbox.appendChild(toggle); dmbox.appendChild(state);
  body.appendChild(dmbox);

  var modes = el('div', 'dmbox');
  var MODES = [['auto', '自動'], ['mp4', 'MP4（確実）'], ['embed', '埋め込み'], ['watch', '視聴ページ'], ['diag', '診断']];
  var btns = {};
  MODES.forEach(function (m) {
    var b = el('button', 'chip');
    b.textContent = m[1];
    b.onclick = function () {
      Object.keys(btns).forEach(function (k) { btns[k].classList.toggle('on', k === m[0]); });
      if (m[0] === 'auto') start(true);
      if (m[0] === 'mp4') useMp4(true);
      if (m[0] === 'embed') useEmbed(true);
      if (m[0] === 'watch') useWatchPage(true);
      if (m[0] === 'diag') showDiag();
    };
    btns[m[0]] = b; modes.appendChild(b);
  });
  btns.auto.classList.add('on');
  body.appendChild(modes);

  var h1 = el('h1', 'vtitle'); h1.textContent = D.title; body.appendChild(h1);
  var sub = el('div', 'vsub');
  [num(D.views) + '回再生', num(D.comments) + 'コメント', num(D.mylists) + 'マイリスト']
    .forEach(function (t) { var s = el('span'); s.textContent = t; sub.appendChild(s); });
  if (D.likes) { var lk = el('span'); lk.textContent = num(D.likes) + 'いいね'; sub.appendChild(lk); }
  var sd = el('span'); sd.textContent = dt(D.start); sub.appendChild(sd);
  body.appendChild(sub);

  if (D.tags && D.tags.length) {
    var tags = el('div', 'tags');
    D.tags.forEach(function (t) { var a = el('a'); a.href = '/search?q=' + encodeURIComponent(t); a.textContent = t; tags.appendChild(a); });
    body.appendChild(tags);
  }
  if (D.related && D.related.length) {
    var h2 = el('h2', 'vtitle'); h2.textContent = '関連動画'; body.appendChild(h2);
    var grid = el('div', 'grid');
    D.related.forEach(function (v) {
      var a = el('a', 'card'); a.href = '/watch/' + v.id;
      var im = el('img'); im.src = v.thumb; im.loading = 'lazy';
      var mt = el('div', 'meta');
      var ti = el('div', 'title'); ti.textContent = v.title;
      var su = el('div', 'sub'); su.textContent = num(v.views) + '回再生';
      mt.appendChild(ti); mt.appendChild(su); a.appendChild(im); a.appendChild(mt); grid.appendChild(a);
    });
    body.appendChild(grid);
  }

  var comments = [], shown = true, mode = null, now = 0, hlsLoaded = false, got = -1, idx = 0;
  function lg(t) { logbox.textContent += t + '\n'; logbox.scrollTop = 1e9; }
  function say(t, bad) { state.textContent = t; state.className = 'note' + (bad ? ' bad' : ''); }

  function scan(n, o) {
    if (n == null) return o;
    if (typeof n === 'string') { if (n.indexOf('.m3u8') >= 0 && n.indexOf('http') === 0) o.push(n); return o; }
    if (Array.isArray(n)) { n.forEach(function (x) { scan(x, o); }); return o; }
    if (typeof n === 'object') Object.values(n).forEach(function (x) { scan(x, o); });
    return o;
  }

  var H = { 'X-Frontend-Id': '6', 'X-Frontend-Version': '0', 'X-Request-With': 'https://www.nicovideo.jp' };
  function track() { return 'AAAAAAAAAA_' + Date.now(); }

  function jget(url, headers, ms) {
    var ac = window.AbortController ? new AbortController() : null;
    var t = setTimeout(function () { if (ac) ac.abort(); }, ms || 9000);
    return fetch(url, { headers: headers || {}, credentials: 'omit', signal: ac ? ac.signal : undefined })
      .then(function (r) { clearTimeout(t); if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); },
            function (e) { clearTimeout(t); throw e; });
  }

  function collect(j) {
    var out = [];
    (((j || {}).data || {}).threads || []).forEach(function (th) {
      (th.comments || []).forEach(function (c) { if (c.body) out.push({ v: (c.vposMs || 0) / 1000, body: c.body, cmd: c.commands || [] }); });
    });
    out.sort(function (a, b) { return a.v - b.v; });
    return out;
  }
  function setComments(list) { comments = list; got = 0; idx = 0; say(list.length ? list.length + '件のコメント' : 'コメントなし'); }

  function commentsFrom(meta) {
    if (meta && meta.comments && meta.comments.length) { setComments(meta.comments); return Promise.resolve(true); }
    var c = (((meta || {}).data || {}).comment) || {};
    var nv = c.nvComment;
    var params = null, threadKey = null, server = 'https://public.nvcomment.nicovideo.jp';
    if (nv && nv.params && nv.threadKey) { params = nv.params; threadKey = nv.threadKey; if (nv.server) server = nv.server; }
    else if (c.layers) {
      var targets = [];
      c.layers.forEach(function (l) { (l.threadIds || []).forEach(function (t) { if (t.id) targets.push({ id: t.id, fork: t.fork || 0 }); }); });
      if (!targets.length) return Promise.resolve(false);
      params = { targets: targets, language: 'ja-jp', videoId: D.id, userId: (c.keys || {}).userKey || '' };
      threadKey = c.threadKey;
      if (c.server && c.server.url) server = c.server.url;
    } else { return Promise.resolve(false); }
    if (!threadKey) return Promise.resolve(false);
    server = server.replace(/\/$/, '');
    return fetch(via(server + '/v1/threads?_frontendId=6'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Frontend-Id': '6', 'X-Frontend-Version': '0' },
      body: JSON.stringify({ params: params, threadKey: threadKey, additionals: {} })
    }).then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (j) { var l = collect(j); if (!l.length) return false; setComments(l); return true; })
      .catch(function (e) { lg('コメント(client): ' + e.message); return false; });
  }

  function commentsFromServer() {
    if (got === 0) return Promise.resolve();
    return jget('/api/comments/' + encodeURIComponent(D.id), {}).then(function (j) {
      var list = (j.comments || []).map(function (c) { return { v: c.v, body: c.body, cmd: c.cmd || [] }; });
      if (list.length) setComments(list); else if (got !== 0) say('コメント取得できません');
    }).catch(function () { if (got !== 0) say('コメント取得できません'); });
  }

  function loadHls() {
    if (hlsLoaded || window.Hls) { hlsLoaded = true; return Promise.resolve(); }
    return new Promise(function (res) {
      var s = document.createElement('script');
      s.src = '/hlsjs';
      s.onload = function () { hlsLoaded = true; res(); };
      s.onerror = function () { hlsLoaded = true; res(); };
      document.head.appendChild(s);
    });
  }

  function nativePlay(url) {
    return new Promise(function (res) {
      var done = false;
      function finish(ok) {
        if (done) return; done = true;
        vid.removeEventListener('loadedmetadata', onOk); vid.removeEventListener('error', onBad); res(ok);
      }
      function onOk() { finish(true); }
      function onBad() { finish(false); }
      vid.addEventListener('loadedmetadata', onOk); vid.addEventListener('error', onBad);
      vid.src = url; vid.load();
      setTimeout(function () { finish(false); }, 8000);
    });
  }

  function hlsPlay(url) {
    return loadHls().then(function () {
      if (!window.Hls || !window.Hls.isSupported()) return false;
      return new Promise(function (res) {
        var done = false;
        function finish(ok) { if (!done) { done = true; res(ok); } }
        var hls = new window.Hls({ maxBufferLength: 20, manifestLoadingTimeOut: 8000 });
        hls.on(window.Hls.Events.MANIFEST_PARSED, function () { finish(true); });
        hls.on(window.Hls.Events.ERROR, function (_, d) { if (d.fatal) { try { hls.destroy(); } catch (e) {} finish(false); } });
        hls.loadSource(url); hls.attachMedia(vid);
        setTimeout(function () { finish(false); }, 10000);
      });
    });
  }

  function play(url) {
    var p = Promise.resolve(false);
    if (vid.canPlayType('application/vnd.apple.mpegurl')) {
      p = nativePlay(url).then(function (ok) {
        if (ok) { mode = 'video'; say('再生中'); vid.play().catch(function () {}); return true; }
        return false;
      });
    }
    return p.then(function (ok) {
      if (ok) return true;
      return hlsPlay(url).then(function (ok2) {
        if (ok2) { mode = 'video'; say('再生中'); vid.play().catch(function () {}); return true; }
        return false;
      });
    });
  }

  function playAny(url) {
    lg('プロキシ再生を試行');
    return play(proxyUrl(url)).then(function (ok) {
      if (ok) return true;
      lg('プロキシ失敗 → リレー直');
      return play(via(url));
    });
  }

  function useMp4(force) {
    if (mode === 'mp4' && !force) return;
    mode = 'mp4';
    layer.innerHTML = '';
    var f = player.querySelector('iframe'); if (f) f.remove();
    if (!vid.parentNode) player.appendChild(vid);
    say('MP4を準備中…初回は1分ほどかかります');
    var x = new XMLHttpRequest();
    x.open('GET', '/mp4/' + encodeURIComponent(D.id));
    x.onload = function () {
      if (x.status >= 400) { say('MP4化に失敗しました', true); lg('mp4: ' + x.responseText.slice(0, 120)); return; }
      vid.src = '/mp4/' + encodeURIComponent(D.id);
      say('MP4で再生中');
      vid.play().catch(function () {});
    };
    x.onerror = function () { say('MP4化に失敗しました', true); };
    x.send();
    commentsFromServer();
  }

  function useWatchPage(force) {
    if (mode === 'watch' && !force) return;
    mode = 'watch'; layer.innerHTML = '';
    var o = player.querySelector('iframe'); if (o) o.remove();
    var fr = el('iframe');
    fr.src = 'https://www.nicovideo.jp/watch/' + D.id;
    fr.allowFullscreen = true; fr.referrerPolicy = 'strict-origin-when-cross-origin';
    player.appendChild(fr); say('視聴ページを表示しています'); commentsFromServer();
  }

  function useEmbed(force) {
    if ((mode === 'iframe' || mode === 'watch') && !force) return;
    mode = 'iframe'; layer.innerHTML = '';
    var o = player.querySelector('iframe'); if (o) o.remove();
    if (vid.parentNode) vid.remove();
    var fr = el('iframe');
    fr.src = D.embed + '?jsapi=1&playerId=1&from=0';
    fr.allow = 'autoplay; fullscreen; encrypted-media';
    fr.allowFullscreen = true; fr.referrerPolicy = 'strict-origin-when-cross-origin';
    player.appendChild(fr); say('埋め込みで再生しています');
    window.addEventListener('message', function (e) {
      if (e.origin !== 'https://embed.nicovideo.jp') return;
      var d = e.data || {};
      if (d.eventName === 'playerMetadataChange' && d.data && typeof d.data.currentTime === 'number') now = d.data.currentTime;
      if (d.eventName === 'error') { lg('embed: error'); useWatchPage(); }
    });
    setTimeout(function () { if (mode === 'iframe') useWatchPage(); }, 6000);
  }

  function showDiag() {
    lg('診断中…');
    jget('/api/diag/' + encodeURIComponent(D.id), {}, 15000).then(function (j) {
      Object.keys(j).forEach(function (k) { lg(k + ': ' + JSON.stringify(j[k]).slice(0, 240)); });
    }).catch(function (e) { lg('診断失敗: ' + e.message); });
  }

  function resetPlayer() {
    comments = []; idx = 0;
    if (mode === 'watch' || mode === 'iframe' || mode === 'mp4') {
      var f = player.querySelector('iframe'); if (f) f.remove();
      if (!vid.parentNode) player.appendChild(vid);
      mode = null;
    }
  }

  function gather() {
    var routes = [
      ['relay', function () {
        return jget(RELAY + '/v/' + encodeURIComponent(D.id), {}, 20000).then(function (j) {
          if (j.comments && j.comments.length) setComments(j.comments);
          if (j.key) KEY = j.key;
          if (!j.url) throw new Error(j.error || 'URLなし');
          return { url: j.url };
        });
      }],
      ['server', function () {
        return jget('/api/brute/' + encodeURIComponent(D.id), {}, 12000).then(function (j) {
          if (j.key) KEY = j.key;
          return j;
        });
      }]
    ];
    if (!RELAY) routes.shift();

    return new Promise(function (resolve) {
      var found = [], left = routes.length, done = false;
      function finish() { if (!done) { done = true; resolve(found.slice()); } }
      routes.forEach(function (r) {
        r[1]().then(function (meta) {
          commentsFrom(meta);
          var u = scan(meta, []);
          if (u.length) found.push(u[0]);
          if (found.length) finish();
        }).catch(function (e) { lg(r[0] + ': ' + e.message); })
          .then(function () { left--; if (!left) finish(); });
      });
      setTimeout(finish, 14000);
    });
  }

  function start(reset) {
    if (reset) { resetPlayer(); commentsFromServer(); }
    say('取得中…');
    gather().then(function (urls) {
      if (!urls.length) { lg('URLが取れません → MP4へ'); useMp4(true); return; }
      var i = 0;
      (function next() {
        if (i >= urls.length) { lg('全URL失敗 → MP4へ'); useMp4(true); return; }
        var u = urls[i++];
        lg('試行 ' + i + ': ' + u.slice(0, 70));
        playAny(u).then(function (ok) { if (!ok) next(); });
      })();
    });
  }

  var lanes = [], LANE_H = 26;
  function spawn(c) {
    var d = el('div', 'dm');
    d.textContent = c.body;
    d.style.fontSize = c.cmd.indexOf('big') >= 0 ? '26px' : (c.cmd.indexOf('small') >= 0 ? '13px' : '19px');
    var col = c.cmd.filter(function (x) { return /^#[0-9a-f]{6}$/i.test(x); })[0];
    if (col) d.style.color = col;
    var h = layer.clientHeight || 1;
    var total = Math.max(1, Math.floor(h / LANE_H));
    var lane = -1;
    for (var i = 0; i < total; i++) { if (!lanes[i] || lanes[i] < performance.now()) { lane = i; break; } }
    if (lane < 0) return;
    var top = lane * LANE_H;
    if (c.cmd.indexOf('shita') >= 0) top = h - (lane + 1) * LANE_H;
    d.style.top = Math.max(0, top) + 'px'; d.style.left = '100%';
    layer.appendChild(d);
    var w = d.offsetWidth || 200;
    var dist = layer.clientWidth + w + 20;
    lanes[lane] = performance.now() + 1400;
    var anim = d.animate([{ transform: 'translateX(0)' }, { transform: 'translateX(-' + dist + 'px)' }],
      { duration: Math.max(4000, dist * 11), easing: 'linear' });
    anim.onfinish = function () { d.remove(); };
  }

  function tick() {
    var t = mode === 'video' ? vid.currentTime : now;
    if (shown && comments.length && t > 0) {
      if (idx > comments.length) idx = 0;
      while (idx < comments.length && comments[idx].v <= t) {
        if (comments[idx].v > t - 1.5) spawn(comments[idx]);
        idx++;
      }
    }
    requestAnimationFrame(tick);
  }

  toggle.addEventListener('click', function () {
    shown = !shown;
    toggle.classList.toggle('on', shown);
    toggle.textContent = shown ? 'コメント ON' : 'コメント OFF';
    if (!shown) layer.innerHTML = '';
  });

  start(false);
  requestAnimationFrame(tick);
})();
