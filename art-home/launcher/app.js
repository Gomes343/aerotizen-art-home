/* Art Home — the home screen. Everything that changes without reinstalling comes from the server
   (artworks, photos, weather, app row, cameras); without the server it keeps a clock, the bundled
   artwork and the default app row. */
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

  var state = { works: [], worksKey: null, i: 0, photos: [], photosKey: null, slides: [], pi: 0, every: 0, mosaic: false,
                photoDate: true, shown: 0,
                apps: DEFAULT_APPS, focus: 0, rotate_s: 90, frame_after_s: 20, layer: 0, cameras: false, misses: 0 };
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
  function photoDate(iso) {
    var d = String(iso || '').split('-').map(Number);   // "2025-03-12", already in the house's date
    return d.length === 3 && d[1] ? T.photoDate(d[2], T.months[d[1] - 1], d[0]) : '';
  }
  function credit(w) {
    if (w.kind === 'photo') {
      // a mosaic shows its dates from the oldest to the newest: one date, or "first – last"
      var dates = (w.group || [w]).map(function (p) { return p.date; }).filter(Boolean).sort();
      var text = !dates.length ? '' : dates[0] === dates[dates.length - 1] ? photoDate(dates[0])
        : [photoDate(dates[0]), photoDate(dates[dates.length - 1])].filter(Boolean).join(' – ');
      return state.photoDate && text ? '<b>' + esc(text) + '</b>' : '';
    }
    if (!w.title) return '';
    var line2 = [w.artist, w.date].filter(Boolean).join(' · ');
    return '<b>' + esc(w.title) + '</b>' + esc(line2) + (w.museum ? '<br>' + esc(w.museum) : '');
  }

  // a photo has no catalogued colour: average a tiny copy of it for the mat (needs the server's CORS)
  function averageColor(img) {
    try {
      var c = document.createElement('canvas'); c.width = 8; c.height = 8;
      var g = c.getContext('2d'); g.drawImage(img, 0, 0, 8, 8);
      var px = g.getImageData(0, 0, 8, 8).data, sum = [0, 0, 0];
      for (var i = 0; i < px.length; i += 4) { sum[0] += px[i]; sum[1] += px[i + 1]; sum[2] += px[i + 2]; }
      return '#' + sum.map(function (v) { var h = Math.round(v / 64).toString(16); return h.length < 2 ? '0' + h : h; }).join('');
    } catch (e) { return null; }   // no canvas or a tainted one: the default mat colour
  }

  /* ---------- mosaic: photos standing up share the screen (no photo is ever cropped) ---------- */
  // the box of the 1920x1080 screen the mosaic may use; with the UI on screen it steps back like a matted artwork
  var BOX = { w: 1780, h: 960, gap: 36, small: 0.62 };   // small: a lying photo between two standing ones
  function ratio(p) { return (p.width || 4) / (p.height || 3); }
  function standing(p) { return ratio(p) < 1; }
  // [{left, top, width, height}] for the photos of a slide, centred in the screen
  function layout(group) {
    var lying = group.length === 3 && !standing(group[1]) && standing(group[0]) && standing(group[2]);
    var k = group.map(function (p, n) { return lying && n === 1 ? BOX.small : 1; });   // height of each, relative
    var sum = 0;
    group.forEach(function (p, n) { sum += ratio(p) * k[n]; });
    var h = Math.min(BOX.h, (BOX.w - BOX.gap * (group.length - 1)) / sum);
    var x = (1920 - (h * sum + BOX.gap * (group.length - 1))) / 2;
    return group.map(function (p, n) {
      var box = { left: Math.round(x), top: Math.round((1080 - h * k[n]) / 2), width: Math.round(ratio(p) * h * k[n]), height: Math.round(h * k[n]) };
      x += ratio(p) * h * k[n] + BOX.gap;
      return box;
    });
  }
  function mosaicOf(layer) {
    if (!layer.mosaic) { layer.mosaic = document.createElement('div'); layer.mosaic.className = 'mosaic'; layer.appendChild(layer.mosaic); }
    return layer.mosaic;
  }
  function photoUrl(p) { return SERVER + '/photos/' + p.file; }
  // a slide of several photos: loads them all, then shows the ones that loaded (one left = shown alone)
  function showGroup(group) {
    var left = group.length, ok = [], first = null;
    group.forEach(function (p, n) {
      var pre = new Image();
      pre.crossOrigin = 'anonymous';
      pre.onload = function () { ok[n] = p; if (!first) first = pre; done(); };
      pre.onerror = done;
      pre.src = photoUrl(p);
    });
    function done() {
      if (--left) return;
      var got = ok.filter(Boolean);
      if (!got.length) { state.misses += 1; if (state.misses < pool()) nextWork(); return; }
      if (got.length === 1) { show(got[0]); return; }
      state.misses = 0;
      var w = { kind: 'photo', group: got, color: got[0].color || averageColor(first) };
      var next = layers[1 - state.layer], current = layers[state.layer];
      next.className = 'layer with-mosaic';
      next.style.backgroundColor = darken(w.color, 0.55);
      var boxes = layout(got);
      mosaicOf(next).innerHTML = got.map(function (p, n) {
        var b = boxes[n];
        return '<img alt="" src="' + esc(photoUrl(p)) + '" style="left:' + b.left + 'px;top:' + b.top + 'px;width:' + b.width + 'px;height:' + b.height + 'px">';
      }).join('');
      fadeTo(next, current, w);
    }
  }
  function fadeTo(next, current, w) {
    // two animation frames: the layer is painted before the fade starts
    requestAnimationFrame(function () { requestAnimationFrame(function () {
      next.classList.add('shown');
      current.classList.remove('shown');
      state.layer = 1 - state.layer;
      $('credit').innerHTML = credit(w);
    }); });
  }

  function show(w) {
    if (w.slide) { if (w.slide.length > 1) { showGroup(w.slide); return; } w = w.slide[0]; }
    var photo = w.kind === 'photo';
    var url = w.url || (SERVER + (photo ? '/photos/' : '/art/') + w.file);
    var pre = new Image();
    if (photo) pre.crossOrigin = 'anonymous';
    pre.onload = function () {
      state.misses = 0;
      if (photo && !w.color) w.color = averageColor(pre);
      var next = layers[1 - state.layer], current = layers[state.layer];
      var ratio = (w.width || pre.width) / (w.height || pre.height);
      next.className = 'layer' + (Math.abs(ratio - 16 / 9) > 0.14 ? ' matted' : '');
      next.style.backgroundColor = darken(w.color, 0.55);
      next.querySelector('img').src = url;
      fadeTo(next, current, w);
    };
    // A broken image skips to the next one — but with the server down every image fails: after one
    // full lap the current artwork stays until the next rotation, instead of requesting forever.
    pre.onerror = function () {
      if (w === FALLBACK) return;
      state.misses += 1;
      if (state.misses < pool()) nextWork();
    };
    pre.src = url;
  }
  /* ---------- the order of the artworks: shuffled here, so one lap shows each artwork once ---------- */
  // the server sends them in a fixed order; a new order is drawn only when the SET changes or a lap ends
  function workKey(w) { return String(w.id != null ? w.id : w.file); }
  function setKey(list) { return list.map(workKey).sort().join('\n'); }
  // `last` (the artwork on screen) never comes first, so it is never shown twice in a row
  function shuffled(list, last) {
    var a = list.slice();
    for (var k = a.length - 1; k > 0; k--) {
      var j = Math.floor(Math.random() * (k + 1)), t = a[k]; a[k] = a[j]; a[j] = t;
    }
    if (last && a.length > 1 && workKey(a[0]) === workKey(last)) {
      var f = a[0]; a[0] = a[a.length - 1]; a[a.length - 1] = f;
    }
    return a;
  }
  function takeWorks(list) {
    var key = setKey(list);
    if (key === state.worksKey) {   // same artworks: keep the order and the place, refresh the details
      var byKey = {};
      list.forEach(function (w) { byKey[workKey(w)] = w; });
      state.works = state.works.map(function (w) { return byKey[workKey(w)]; });
      return;
    }
    state.works = shuffled(list, state.works[state.i]);
    state.worksKey = key;
    state.i = -1;
  }

  /* ---------- photos: a lap of slides — a lying photo alone, standing ones in a mosaic of three ---------- */
  // The mosaic takes the standing photo, then two more standing ones; half of the time (while lying photos
  // remain) the middle one is a lying photo, a little smaller. Each photo is shown once per lap.
  function slidesOf(list) {
    var rest = shuffled(list), out = [];
    function take(want) {
      for (var n = 0; n < rest.length; n++) if (standing(rest[n]) === want) return rest.splice(n, 1)[0];
      return null;
    }
    while (rest.length) {
      var p = rest.shift();
      if (!state.mosaic || !standing(p)) { out.push([p]); continue; }
      var lie = Math.random() < 0.5 ? take(false) : null;
      var q = take(true), r = lie ? null : take(true);
      if (lie && !q) { rest.unshift(lie); lie = null; }   // no standing partner: the lying photo waits its turn
      out.push(lie ? [p, lie, q] : [p, q, r].filter(Boolean));
    }
    return out;
  }
  function takePhotos(list) {
    var key = setKey(list) + '|' + state.mosaic;
    if (key === state.photosKey) return;
    state.photos = list;
    state.photosKey = key;
    state.slides = list.length ? slidesOf(list) : [];
    state.pi = -1;
  }
  function photosOn() { return state.every > 0 && state.slides.length > 0; }
  function pool() { return state.works.length + (photosOn() ? state.slides.length : 0); }
  // every `every`-th picture is a photo (1 = photos only); without artworks, photos only
  function nextWork() {
    state.shown += 1;
    if (photosOn() && (!state.works.length || state.shown % state.every === 0)) {
      if (state.pi + 1 >= state.slides.length) {   // lap done: new slides, in a new order
        state.slides = slidesOf(state.photos);
        state.pi = -1;
      }
      state.pi += 1;
      show({ slide: state.slides[state.pi] });
      return;
    }
    if (!state.works.length) { show(FALLBACK); return; }
    if (state.i + 1 >= state.works.length) {   // lap done: a new order for the next one
      state.works = shuffled(state.works, state.works[state.i]);
      state.i = -1;
    }
    state.i += 1;
    show(state.works[state.i]);
  }
  function scheduleRotation() {
    clearInterval(timers.rotate);
    timers.rotate = setInterval(function () { state.misses = 0; nextWork(); }, state.rotate_s * 1000);
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
      if (AERO && ON_TV) { AERO.useSamsungHome(); } else { toast(T.previewSamsung); }
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
      var first = !pool();
      if (d.works && d.works.length) takeWorks(d.works);
      state.mosaic = d.photos_mosaic === true;
      if (d.photos) takePhotos(d.photos);
      state.every = d.photos_every || 0;
      state.photoDate = d.photos_date !== false;
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
      if (first && pool()) { state.i = -1; state.pi = -1; state.shown = 0; nextWork(); scheduleRotation(); }
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
