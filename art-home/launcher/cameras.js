/* Cameras on the TV: a grid with each camera's latest frame (Frigate keeps it in memory, 20–40 ms per
   image, nothing is decoded again) and, with OK, live video full screen through go2rtc (plain MP4 into
   the <video> tag). The TV CPU is weak: the grid swaps <img> one at a time, no video; video only one,
   full screen. URLs and the camera list come from the server (config.json → cameras). */
(function () {
  'use strict';

  var T = window.T;
  var COLUMNS = 3, REFRESH_MS = 1500, VIDEO_WAIT_MS = 8000;   // not playing by then → light (_sub) stream
  var cfg = { frigate_url: '', go2rtc_url: '', list: [] };
  var focus = 0, open = false, full = -1, timer = null, videoWait = null;
  var $ = function (id) { return document.getElementById(id); };

  function frameUrl(c) {
    if (c.snapshot_url) return c.snapshot_url + (c.snapshot_url.indexOf('?') < 0 ? '?' : '&') + 't=' + Date.now();
    return cfg.frigate_url + '/api/' + encodeURIComponent(c.frigate) + '/latest.jpg?h=360&quality=70&t=' + Date.now();
  }
  function esc(s) { return String(s || '').replace(/[&<>"]/g, function (x) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[x]; }); }

  function draw() {
    var g = $('cam-grid');
    $('cam-title').textContent = T.cameras;
    $('cam-hint').textContent = T.camHint;
    g.innerHTML = '';
    cfg.list.forEach(function (c, i) {
      var el = document.createElement('div');
      el.className = 'cam' + (i === focus ? ' focus' : '');
      el.innerHTML = '<img alt=""><div class="cam-label">' + esc(c.name) + '</div><div class="cam-down" hidden>' + esc(T.noSignal) + '</div>';
      g.appendChild(el);
      el.errors = 0;
      refreshOne(i);
    });
  }

  // Swap the image only after the new one loaded: the tile never blinks white.
  function refreshOne(i) {
    var el = $('cam-grid').children[i], c = cfg.list[i];
    if (!el || el.loading) return;
    el.loading = true;
    var pre = new Image();
    pre.onload = function () {
      el.querySelector('img').src = pre.src;
      el.querySelector('.cam-down').hidden = true;
      el.errors = 0; el.loading = false;
    };
    pre.onerror = function () {
      el.errors += 1; el.loading = false;
      if (el.errors >= 3) el.querySelector('.cam-down').hidden = false;
    };
    pre.src = frameUrl(c);
  }
  function loop() {
    clearInterval(timer);
    timer = setInterval(function () {
      if (full >= 0) return;
      for (var i = 0; i < cfg.list.length; i++) refreshOne(i);
    }, REFRESH_MS);
  }
  function focusOn(i) {
    if (i < 0 || i >= cfg.list.length) return;
    focus = i;
    var els = $('cam-grid').children;
    for (var k = 0; k < els.length; k++) els[k].classList.toggle('focus', k === focus);
  }

  /* ---------- full screen ---------- */
  function play(source) {
    var v = $('cam-video');
    clearTimeout(videoWait);
    v.src = cfg.go2rtc_url + '/api/stream.mp4?src=' + encodeURIComponent(source);
    if (v.play) v.play().catch(function () { /* muted autoplay: the real error arrives as an event */ });
  }
  function openFull(i) {
    full = i;
    var c = cfg.list[i], v = $('cam-video');
    $('cam-full').hidden = false;
    $('cam-full-name').textContent = c.name;
    $('cam-full-note').textContent = T.loading;
    $('cam-full-hint').textContent = T.camFullHint;
    v.usingSub = false;
    v.onplaying = function () { clearTimeout(videoWait); $('cam-full-note').textContent = v.usingSub ? T.lowQuality : ''; };
    v.onerror = function () { fallBackToSub(); };
    play(c.stream);
    videoWait = setTimeout(fallBackToSub, VIDEO_WAIT_MS);
  }
  function fallBackToSub() {
    var v = $('cam-video'), c = cfg.list[full];
    if (full < 0 || v.usingSub || !c.sub) { $('cam-full-note').textContent = T.noVideo; return; }
    v.usingSub = true;
    play(c.sub);
    videoWait = setTimeout(function () { $('cam-full-note').textContent = T.noVideo; }, VIDEO_WAIT_MS);
  }
  function closeFull() {
    var v = $('cam-video');
    clearTimeout(videoWait);
    v.onplaying = v.onerror = null;
    v.removeAttribute('src'); v.load();   // releases the go2rtc connection
    $('cam-full').hidden = true;
    full = -1;
  }

  /* ---------- API for app.js and alerts.js ---------- */
  window.Cameras = {
    configure: function (c) {
      cfg = { frigate_url: c.frigate_url || '', go2rtc_url: c.go2rtc_url || '', list: c.list || [] };
      focus = Math.min(focus, Math.max(0, cfg.list.length - 1));
      if (open) draw();
    },
    list: function () { return cfg.list; },
    isOpen: function () { return open; },
    open: function () {
      open = true;
      document.body.classList.add('in-cameras');
      $('cameras').hidden = false;
      draw(); loop();
    },
    close: function () {
      if (full >= 0) closeFull();
      clearInterval(timer);
      open = false;
      $('cameras').hidden = true;
      document.body.classList.remove('in-cameras');
    },
    // from the detection pop-up: straight to that camera, full screen
    openLive: function (frigateName) {
      var i = -1;
      for (var k = 0; k < cfg.list.length; k++) if (cfg.list[k].frigate === frigateName) i = k;
      if (i < 0) return;
      if (!open) window.Cameras.open();
      if (full >= 0) closeFull();
      focusOn(i); openFull(i);
    },
    // returns true when the key was used
    key: function (k) {
      var BACK = (k === 10009 || k === 27 || k === 8);
      if (full >= 0) {
        if (BACK || k === 13) { closeFull(); return true; }
        if (k === 37 || k === 39) {   // ←/→ switches camera without leaving full screen
          var n = (full + (k === 39 ? 1 : -1) + cfg.list.length) % cfg.list.length;
          closeFull(); focusOn(n); openFull(n); return true;
        }
        return true;
      }
      if (BACK) { window.Cameras.close(); return true; }
      if (k === 37) focusOn(focus - 1);
      else if (k === 39) focusOn(focus + 1);
      else if (k === 38) focusOn(focus - COLUMNS);
      else if (k === 40) focusOn(focus + COLUMNS);
      else if (k === 13) openFull(focus);
      else return false;
      return true;
    }
  };
})();
