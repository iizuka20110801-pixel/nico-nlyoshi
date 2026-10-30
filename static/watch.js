(function () {
  var D = JSON.parse(document.getElementById('vd').textContent);
  var player = document.getElementById('player');
  var logbox = document.getElementById('log');
  var body = document.getElementById('body');

  function el(tag, cls) { var e = document.createElement(tag); if (cls) e.className = cls; return e; }
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
    return d.getFullYear() + '/' + p(d.getMonth() + 1) + '/' + p(d.getDate()) +
      ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
  }

  // --- プレイヤー DOM ---
  if (D.thumb) {
    var poster = el('img', 'poster');
    poster.src = D.thumb;
    player.appendChild(poster);
  }
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

  // --- 操作系 ---
  var dmbox = el('div', 'dmbox');
  var toggle = el('button', 'chip on');
  toggle.textContent = 'コメント ON';
  var state = el('span', 'note');
  state.textContent = 'プレイヤー準備中…';
  var l1 = el('a', 'chip');
  l1.href = D.embed; l1.target = '_blank'; l1.rel = 'noopener';
  l1.textContent = '埋め込みで開く';
  var l2 = el('a', 'chip');
  l2.href = 'https://www.nicovideo.jp/watch/' + D.id;
  l2.target = '_blank'; l2.rel = 'noopener';
  l2.textContent = 'ニコニコ動画で開く';
  dmbox.appendChild(toggle); dmbox.appendChild(state);
  dmbox.appendChild(l1); dmbox.appendChild(l2);
  body.appendChild(dmbox);

  var h1 = el('h1', 'vtitle');
  h1.textContent = D.title;
  body.appendChild(h1);

  var sub = el('div', 'vsub');
  [num(D.views) + '回再生', num(D.comments) + 'コメント', num(D.mylists) + 'マイリスト']
    .forEach(function (t) { var s = el('span'); s.textContent = t; sub.appendChild(s); });
  if (D.likes) { var lk = el('span'); lk.textContent = num(D.likes) + 'いいね'; sub.appendChild(lk); }
  var sd = el('span'); sd.textContent = dt(D.start); sub.appendChild(sd);
  body.appendChild(sub);

  if (D.tags && D.tags.length) {
    var tags = el('div', 'tags');
    D.tags.forEach(function (t) {
      var a = el('a'); a.href = '/search?q=' + encodeURIComponent(t); a.textContent = t;
      tags.appendChild(a);
    });
    body.appendChild(tags);
  }

  if (D.related && D.related.length) {
    var h2 = el('h2', 'vtitle');
    h2.textContent = '関連動画';
    body.appendChild(h2);
    var grid = el('div', 'grid');
    D.related.forEach(function (v) {
      var a = el('a', 'card');
      a.href = '/watch/' + v.id;
      var im = el('img'); im.src = v.thumb; im.loading = 'lazy';
      var meta = el('div', 'meta');
      var ti = el('div', 'title'); ti.textContent = v.title;
      var su = el('div', 'sub'); su.textContent = num(v.views) + '回再生';
      meta.appendChild(ti); meta.appendChild(su);
      a.appendChild(im); a.appendChild(meta);
      grid.appendChild(a);
    });
    body.appendChild(grid);
  }

  // --- ロジック ---
  var comments = [], shown = true, mode = null, now = 0, hlsLoaded = false, got = -1;

  function lg(t) { logbox.textContent += t + '\n'; logbox.scrollTop = 1e9; }
  function say(t, bad) { state.textContent = t; state.className = 'note' + (bad ? ' bad' : ''); }

  function scan(node, out) {
    if (node == null) return out;
    if (typeof node === 'string') {
      if (node.indexOf('.m3u8') >= 0 && node.indexOf('http') === 0) out.push(node);
      return out;
    }
    if (Array.isArray(node)) { node.forEach(function (x) { scan(x, out); }); return out; }
    if (typeof node === 'object') Object.values(node).forEach(function (x) { scan(x, out); });
    return out;
  }

  var H = {
    'X-Frontend-Id': '6',
    'X-Frontend-Version': '0',
    'X-Request-With': 'https://www.nicovideo.jp'
  };

  function track() { return 'AAAAAAAAAA_' + Date.now(); }

  function jget(url, headers) {
    return fetch(url, { headers: headers || {}, credentials: 'omit' }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  function guest(headers) {
    return jget('https://www.nicovideo.jp/api/watch/v3_guest/' + D.id +
      '?actionTrackId=' + track() + '&t=' + Date.now(), headers);
  }

  function nvapi() {
    var trackId = track();
    return jget('https://nvapi.nicovideo.jp/v1/watch/' + D.id +
      '?actionTrackId=' + trackId, H).then(function (meta) {
      var data = meta.data || {};
      var domand = (data.media || {}).domand || {};
      var key = domand.accessRightKey;
      var videos = (domand.videos || []).slice().sort(function (a, b) {
        return (a.height || 0) - (b.height || 0);
      });
      var audios = (domand.audios || []).slice().sort(function (a, b) {
        return (a.bitRate || 0) - (b.bitRate || 0);
      });
      if (!key || !videos.length) throw new Error('no access-right');
      var under = videos.filter(function (v) { return (v.height || 0) <= 720; });
      var pool = under.length ? under : videos;
      var v = pool[pool.length - 1];
      var a = audios[audios.length - 1];
      var outputs = a ? [[v.id, a.id]] : [[v.id]];
      return fetch('https://nvapi.nicovideo.jp/v1/watch/' + D.id +
        '/access-rights/hls?actionTrackId=' + ((data.client || {}).watchTrackId || trackId), {
        method: 'POST',
        headers: Object.assign({ 'Content-Type': 'application/json', 'X-Access-Right-Key': key }, H),
        body: JSON.stringify({ outputs: outputs })
      }).then(function (r) {
        if (!r.ok) throw new Error('hls HTTP ' + r.status);
        return r.json();
      }).then(function (j) {
        var url = (j.data || {}).contentUrl;
        if (!url) throw new Error('no contentUrl');
        return { data: { media: { movie: { session: { urls: [{ url: url }] } } }, comment: data.comment } };
      });
    });
  }

  function collect(j) {
    var out = [];
    (((j || {}).data || {}).threads || []).forEach(function (th) {
      (th.comments || []).forEach(function (c) {
        if (c.body) out.push({ v: (c.vposMs || 0) / 1000, body: c.body, cmd: c.commands || [] });
      });
    });
    out.sort(function (a, b) { return a.v - b.v; });
    return out;
  }

  function setComments(list) {
    comments = list;
    got = 0;
    say(list.length ? list.length + '件のコメント' : 'コメントなし');
  }

  function commentsFrom(meta) {
    var nv = (((meta || {}).data || {}).comment || {}).nvComment;
    if (!nv || !nv.params || !nv.threadKey) return Promise.resolve(false);
    var base = (nv.server || 'https://public.nvcomment.nicovideo.jp').replace(/\/$/, '');
    return fetch(base + '/v1/threads?_frontendId=6', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Frontend-Id': '6', 'X-Frontend-Version': '0' },
      body: JSON.stringify({ params: nv.params, threadKey: nv.threadKey, additionals: {} })
    }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).then(function (j) {
      var list = collect(j);
      if (!list.length) return false;
      setComments(list);
      return true;
    }).catch(function (e) {
      lg('コメント(client): ' + e.message);
      return false;
    });
  }

  function commentsFromServer() {
    if (got === 0) return Promise.resolve();
    return jget('/api/comments/' + encodeURIComponent(D.id), {}).then(function (j) {
      var list = (j.comments || []).map(function (c) {
        return { v: c.v, body: c.body, cmd: c.cmd || [] };
      });
      if (list.length) setComments(list);
      else if (got !== 0) say('コメント取得できません');
    }).catch(function () {
      if (got !== 0) say('コメント取得できません');
    });
  }

  function loadHls() {
    if (hlsLoaded || window.Hls) { hlsLoaded = true; return Promise.resolve(); }
    return new Promise(function (res) {
      var s = document.createElement('script');
      s.src = 'https://cdn.jsdelivr.net/npm/hls.js@1/dist/hls.min.js';
      s.onload = function () { hlsLoaded = true; res(); };
      s.onerror = function () { hlsLoaded = true; res(); };
      document.head.appendChild(s);
    });
  }

  function nativePlay(url) {
    return new Promise(function (res) {
      var done = false;
      function finish(ok) {
        if (done) return;
        done = true;
        vid.removeEventListener('loadedmetadata', onOk);
        vid.removeEventListener('error', onBad);
        res(ok);
      }
      function onOk() { finish(true); }
      function onBad() { finish(false); }
      vid.addEventListener('loadedmetadata', onOk);
      vid.addEventListener('error', onBad);
      vid.src = url;
      vid.load();
      setTimeout(function () { finish(false); }, 9000);
    });
  }

  function hlsPlay(url) {
    return loadHls().then(function () {
      if (!window.Hls || !window.Hls.isSupported()) return false;
      return new Promise(function (res) {
        var done = false;
        function finish(ok) { if (!done) { done = true; res(ok); } }
        var hls = new window.Hls({ maxBufferLength: 30 });
        hls.on(window.Hls.Events.MANIFEST_PARSED, function () { finish(true); });
        hls.on(window.Hls.Events.ERROR, function (_, d) {
          if (d.fatal) { try { hls.destroy(); } catch (e) {} finish(false); }
        });
        hls.loadSource(url);
        hls.attachMedia(vid);
        setTimeout(function () { finish(false); }, 12000);
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

  function useWatchPage() {
    if (mode === 'watch') return;
    mode = 'watch';
    layer.innerHTML = '';
    var old = player.querySelector('iframe');
    if (old) old.remove();
    var frame = el('iframe');
    frame.src = 'https://www.nicovideo.jp/watch/' + D.id;
    frame.allowFullscreen = true;
    frame.referrerPolicy = 'strict-origin-when-cross-origin';
    player.appendChild(frame);
    say('視聴ページを表示しています');
    commentsFromServer();
  }

  function useEmbed() {
    if (mode === 'iframe' || mode === 'watch') return;
    mode = 'iframe';
    layer.innerHTML = '';
    if (vid.parentNode) vid.remove();
    var frame = el('iframe');
    frame.src = D.embed + '?jsapi=1&playerId=1&from=0';
    frame.allow = 'autoplay; fullscreen; encrypted-media';
    frame.allowFullscreen = true;
    frame.referrerPolicy = 'strict-origin-when-cross-origin';
    player.appendChild(frame);
    say('埋め込みで再生しています');
    window.addEventListener('message', function (e) {
      if (e.origin !== 'https://embed.nicovideo.jp') return;
      var d = e.data || {};
      if (d.eventName === 'playerMetadataChange' && d.data &&
          typeof d.data.currentTime === 'number') now = d.data.currentTime;
      if (d.eventName === 'error') { lg('embed: error'); useWatchPage(); }
    });
    setTimeout(function () {
      if (mode === 'iframe') useWatchPage();
    }, 7000);
  }

  var routes = [
    ['guest(no-hdr)', function () { return guest({}); }],
    ['guest(hdr)', function () { return guest(H); }],
    ['nvapi', nvapi],
    ['server', function () { return jget('/api/brute/' + encodeURIComponent(D.id), {}); }]
  ];

  function boot() {
    commentsFromServer();
    var chain = Promise.resolve(false);
    routes.forEach(function (route) {
      chain = chain.then(function (finished) {
        if (finished) return true;
        var name = route[0];
        return route[1]().then(function (meta) {
          var urls = scan(meta, []);
          return commentsFrom(meta).then(function () {
            if (!urls.length) { lg(name + ': URLなし'); return false; }
            lg(name + ': ' + urls[0].slice(0, 90));
            return play(urls[0]).then(function (ok) {
              if (ok) return true;
              lg(name + ': 直リンク失敗 → プロキシ');
              return play('/hls?u=' + encodeURIComponent(urls[0])).then(function (ok2) {
                if (ok2) return true;
                lg(name + ': プロキシも失敗');
                return false;
              });
            });
          });
        }).catch(function (e) {
          lg(name + ': ' + e.message);
          return false;
        });
      });
    });
    chain.then(function (finished) {
      if (!finished) { lg('全ルート失敗 → iframe'); useEmbed(); }
    });
  }

  var lanes = [];
  var LANE_H = 26;

  function spawn(c) {
    var big = c.cmd.indexOf('big') >= 0;
    var small = c.cmd.indexOf('small') >= 0;
    var d = el('div', 'dm');
    d.textContent = c.body;
    d.style.fontSize = big ? '26px' : (small ? '13px' : '19px');
    var col = c.cmd.filter(function (x) { return /^#[0-9a-f]{6}$/i.test(x); })[0];
    if (col) d.style.color = col;
    var h = layer.clientHeight || 1;
    var total = Math.max(1, Math.floor(h / LANE_H));
    var lane = -1;
    for (var i = 0; i < total; i++) {
      if (!lanes[i] || lanes[i] < performance.now()) { lane = i; break; }
    }
    if (lane < 0) return;
    var top = lane * LANE_H;
    if (c.cmd.indexOf('shita') >= 0) top = h - (lane + 1) * LANE_H;
    d.style.top = Math.max(0, top) + 'px';
    d.style.left = '100%';
    layer.appendChild(d);
    var w = d.offsetWidth || 200;
    var dist = layer.clientWidth + w + 20;
    var dur = Math.max(4000, dist * 11);
    lanes[lane] = performance.now() + 1400;
    var anim = d.animate(
      [{ transform: 'translateX(0)' }, { transform: 'translateX(-' + dist + 'px)' }],
      { duration: dur, easing: 'linear' });
    anim.onfinish = function () { d.remove(); };
  }

  var idx = 0;
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

  boot();
  requestAnimationFrame(tick);
})();
