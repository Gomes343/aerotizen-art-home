/* Detection pop-up: asks the Art Home server (which reads Frigate) every 2 s and shows a card in the
   top-right corner with the event photo. It appears above everything that is OURS (home, frame mode,
   cameras); inside another app the TV freezes this page — Tizen web apps can't draw over other apps.
   RED button while the card is on screen = that camera live. */
(function () {
  'use strict';

  var T = window.T;
  var SERVER = (window.ART_HOME_SETTINGS && window.ART_HOME_SETTINGS.server) || window.location.origin;
  var ON_TV = typeof window.tizen !== 'undefined';
  var READ_MS = 2000, RED = 403;
  var showMs = 15000, seq = -1, queue = [], current = null, hideTimer = null;
  var $ = function (id) { return document.getElementById(id); };

  function two(n) { return (n < 10 ? '0' : '') + n; }
  function hhmmss(ts) { var d = new Date(ts * 1000); return two(d.getHours()) + ':' + two(d.getMinutes()) + ':' + two(d.getSeconds()); }
  function cameraName(id) {
    var c = window.Cameras && window.Cameras.list().filter(function (x) { return x.frigate === id; })[0];
    return c ? c.name : id;
  }

  function show(a) {
    current = a;
    var photo = $('alert-photo');
    photo.onerror = function () { photo.onerror = null; photo.src = a.thumbnail; };   // snapshot not ready yet
    photo.src = a.photo;
    $('alert-title').textContent = (a.who || T.labels[a.label] || a.label) + ' · ' + cameraName(a.camera);
    $('alert-time').textContent = hhmmss(a.start) + (ON_TV ? '   ' + T.redOpensLive : '');
    $('alert').classList.add('shown');
    clearTimeout(hideTimer);
    hideTimer = setTimeout(hide, showMs);
  }
  function hide() {
    $('alert').classList.remove('shown');
    current = null;
    if (queue.length) setTimeout(function () { show(queue.shift()); }, 600);
  }

  function read() {
    if (document.hidden) { setTimeout(read, READ_MS); return; }   // frozen while not in front
    var x = new XMLHttpRequest();
    x.open('GET', SERVER + '/api/alerts?since=' + seq);
    x.timeout = 4000;
    x.onload = function () {
      try {
        var d = JSON.parse(x.responseText);
        (d.alerts || []).forEach(function (a) { if (current) queue.push(a); else show(a); });
        queue = queue.slice(-5);
        seq = d.seq;
      } catch (e) { /* bad answer: try again next time */ }
      setTimeout(read, READ_MS);
    };
    x.onerror = x.ontimeout = function () { setTimeout(read, READ_MS * 5); };
    x.send();
  }

  // The red key only reaches the app if it is registered.
  try { if (ON_TV) tizen.tvinputdevice.registerKey('ColorF0Red'); } catch (e) { /* no red key: the card alone */ }
  document.addEventListener('keydown', function (ev) {
    if (ev.keyCode !== RED || !current || !window.Cameras) return;
    ev.preventDefault(); ev.stopImmediatePropagation();
    var cam = current.camera;
    hide();
    window.Cameras.openLive(cam);
  }, true);   // capture phase: the red key doesn't reach the rest of the screen

  window.Alerts = {
    configure: function (c) { if (c && c.show_s) showMs = c.show_s * 1000; },
    simulate: function (a) { show(a); }   // preview/screenshots only
  };
  read();
})();
