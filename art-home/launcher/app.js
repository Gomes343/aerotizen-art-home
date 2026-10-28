/* Art Home — the home screen. Everything that changes without reinstalling comes from the server
   (artworks, weather, app row, cameras); without the server it keeps a clock, the bundled artwork and
   the default app row. */
(function () {
  'use strict';

  var ON_TV = typeof window.tizen !== 'undefined';
  var SETTINGS = window.ART_HOME_SETTINGS || {};
  var SERVER = SETTINGS.server || window.location.origin;
  var T = window.T;
  var DEFAULT_APPS = [];
  var CAMERAS = { name: T.cameras, id: '__cameras', color: '#0e7c66', glyph: '◉' };   // our own screen (cameras.js)
  var SAMSUNG = { name: T.samsung, id: '__samsung', color: '#1428a0', glyph: 'S' };   // leaves for Samsung Home
  var AERO = window.AeroTizen && window.AeroTizen.homewatch;   // Home service (AeroTizen)
  var FALLBACK = { title: '', artist: '', date: '', museum: '', url: 'fallback.jpg', width: 16, height: 9, color: '#1d2230' };

  var state = { works: [], i: 0, apps: DEFAULT_APPS, focus: 0, rotate_s: 90, frame_after_s: 20, layer: 0, cameras: false };
  var timers = { rotate: null, frame: null };
  var $ = function (id) { return document.getElementById(id); };
  var layers = [$('layer-a'), $('layer-b')];

  /* ---------- clock ---------- */
  function two(n) { return (n < 10 ? '0' : '') + n; }
  function clock() {
    var d = new Date();
    $('clock').textContent = two(d.getHours()) + ':' + two(d.getMinutes());
    $('date').textContent = T.date(T.days[d.getDay()], d.getDate(), T.months[d.getMonth()]);
  }

  /* ---------- artwork ---------- */
  function darken(hex, f) {
    var n = parseInt((hex || '#1d2230').slice(1), 16);
    return 'rgb(' + Math.round(((n >> 16) & 255) * f) + ',' + Math.round(((n >> 8) & 255) * f) + ',' + Math.round((n & 255) * f) + ')';
  }
  function esc(s) { return String(s || '').replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function credit(w) {
    if (!w.title) return '';
    var line2 = [w.artist, w.date].filter(Boolean).join(' · ');
    return '<b>' + esc(w.title) + '</b>' + esc(line2) + (w.museum ? '<br>' + esc(w.museum) : '');
  }

  function show(w) {
    var url = w.url || (SERVER + '/art/' + w.file);
    var pre = new Image();
    pre.onload = function () {
      var next = layers[1 - state.layer], current = layers[state.layer];
      var ratio = (w.width || pre.width) / (w.height || pre.height);
      next.className = 'layer' + (Math.abs(ratio - 16 / 9) > 0.14 ? ' matted' : '');
      next.style.backgroundColor = darken(w.color, 0.55);
      next.querySelector('img').src = url;
      // two animation frames: the layer is painted before the fade starts
      requestAnimationFrame(function () { requestAnimationFrame(function () {
        next.classList.add('shown');
        current.classList.remove('shown');
        state.layer = 1 - state.layer;
        $('credit').innerHTML = credit(w);
      }); });
    };
    pre.onerror = function () { if (w !== FALLBACK) nextWork(); };
    pre.src = url;
  }
  function nextWork() {
    if (!state.works.length) { show(FALLBACK); return; }
    state.i = (state.i + 1) % state.works.length;
    show(state.works[state.i]);
  }
  function scheduleRotation() {
    clearInterval(timers.rotate);
    timers.rotate = setInterval(nextWork, state.rotate_s * 1000);
  }

  /* ---------- app row ---------- */
  function items() { return state.apps.concat(state.cameras ? [CAMERAS, SAMSUNG] : [SAMSUNG]); }
  function drawApps() {
    var nav = $('apps');
    nav.innerHTML = '';
    items().forEach(function (a, i) {
      var el = document.createElement('div');
      el.className = 'app' + (i === state.focus ? ' focus' : '');
      el.innerHTML = '<div class="badge" style="background:' + esc(a.color) + '">' + esc(a.glyph || a.name.charAt(0)) + '</div>' +
                     '<div class="name">' + esc(a.name) + '</div>';
      nav.appendChild(el);
    });
  }
  function focusOn(i) {
    state.focus = (i + items().length) % items().length;
    var els = $('apps').children;
    for (var k = 0; k < els.length; k++) els[k].classList.toggle('focus', k === state.focus);
  }
  function open(a) {
    if (a.id === CAMERAS.id) { window.Cameras.open(); return; }
    if (a.id === SAMSUNG.id) {
      if (AERO) { AERO.useSamsungHome(); } else { toast(T.previewSamsung); }
      return;
    }
    if (!ON_TV) { toast(T.previewOpen + a.name); return; }
    if (AERO) { AERO.watch(a.id); }   // Home pressed inside that app comes back here
    try {
      tizen.application.launch(a.id, null, function () { toast(T.couldNotOpen + a.name); });
    } catch (e) { toast(T.couldNotOpen + a.name); }
  }
  function toast(text) {
    var el = $('toast');
    el.textContent = text; el.hidden = false;
    clearTimeout(toast.t);
    toast.t = setTimeout(function () { el.hidden = true; }, 3500);
  }

  /* ---------- frame mode: no remote input for a while, only the artwork remains ---------- */
  function wake() {
    var asleep = document.body.classList.contains('frame');
    document.body.classList.remove('frame');
    clearTimeout(timers.frame);
    timers.frame = setTimeout(function () {
      if (!(window.Cameras && window.Cameras.isOpen())) document.body.classList.add('frame');
    }, state.frame_after_s * 1000);
    return asleep;
  }

  document.addEventListener('keydown', function (ev) {
    var k = ev.keyCode;
    if (window.Cameras && window.Cameras.isOpen()) {
      if (window.Cameras.key(k)) ev.preventDefault();
      if (!window.Cameras.isOpen()) wake();   // back home: the frame-mode timer restarts
      return;
    }
    if (wake()) { ev.preventDefault(); return; }   // the first key only wakes the screen
    if (k === 37) focusOn(state.focus - 1);
    else if (k === 39) focusOn(state.focus + 1);
    else if (k === 13) open(items()[state.focus]);
    else if (k === 10009 || k === 27 || k === 8) document.body.classList.add('frame');
    else return;
    ev.preventDefault();
  });

  /* ---------- server ---------- */
  function load() {
    var x = new XMLHttpRequest();
    x.open('GET', SERVER + '/api/home');
    x.timeout = 8000;
    x.onload = function () {
      if (x.status !== 200) return failed();
      var d; try { d = JSON.parse(x.responseText); } catch (e) { return failed(); }
      var first = !state.works.length;
      if (d.works && d.works.length) state.works = d.works;
      if (d.cameras && d.cameras.list && d.cameras.list.length && window.Cameras) {
        window.Cameras.configure(d.cameras);
        state.cameras = true;
      }
      if (d.apps) { state.apps = d.apps; }
      state.focus = Math.min(state.focus, items().length - 1);
      drawApps();
      if (d.energy && window.Energy) window.Energy.configure(d.energy);
      if (d.alerts && window.Alerts) window.Alerts.configure(d.alerts);
      if (d.rotate_s) state.rotate_s = d.rotate_s;
      if (d.frame_after_s) state.frame_after_s = d.frame_after_s;
      if (d.weather && d.weather.temperature != null) {
        $('weather-temp').textContent = Math.round(d.weather.temperature) + '°';
        $('weather-text').textContent = d.weather.text;
        $('weather').hidden = false;
      } else { $('weather').hidden = true; }
      if (first && state.works.length) { state.i = 0; show(state.works[0]); scheduleRotation(); }
      setTimeout(load, 10 * 60 * 1000);
    };
    x.onerror = x.ontimeout = failed;
    x.send();
  }
  function failed() { setTimeout(load, 60 * 1000); }

  /* ---------- Home service: start it and report which apps exist (tells Home apart from "open app") ---------- */
  function homeService() {
    if (!AERO || !ON_TV) return;
    AERO.start();
    try {
      tizen.application.getAppsInfo(function (list) {
        AERO.reportApps(list.map(function (x) { return x.id; }));
      }, function () { /* without the list the service keeps what it knows */ });
    } catch (e) { /* idem */ }
  }

  /* ---------- TV: the screen saver stays off while the artwork is on screen ---------- */
  function noScreenSaver() {
    try {
      if (window.webapis && webapis.appcommon) {
        webapis.appcommon.setScreenSaver(webapis.appcommon.AppCommonScreenSaverState.SCREEN_SAVER_OFF);
      }
    } catch (e) { /* without the API the TV decides */ }
  }

  window.ArtHome = { toast: toast, next: nextWork };   // next: used by the preview/screenshots
  clock(); setInterval(clock, 10 * 1000);
  drawApps();
  show(FALLBACK);
  wake();
  noScreenSaver();
  homeService();
  load();
})();
