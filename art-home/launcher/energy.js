/* Energy saving driven by idleness. It only counts while OUR screen is in front (inside another app it
   is frozen) and never with the cameras open. Steps, all undone by any key:
     night (window in config)        → frame mode gets darker;
     idle for `dim_after_min`          → the artwork slowly dims to `dim_level`;
     night and idle for `off_after_min` → ASKS the server to turn the TV off (the server checks and decides).
   And ARRIVAL: this page only loads from scratch when the TV powers on (Home reuses the running
   instance), so it starts in frame mode at low brightness — whoever walks in sees the artwork and the
   camera pop-ups without the TV lighting up fully. Any key brings full brightness.
   On local-dimming panels (Mini-LED / Neo QLED, OLED) a darker picture really draws less power. */
(function () {
  'use strict';

  var SERVER = (window.ART_HOME_SETTINGS && window.ART_HOME_SETTINGS.server) || window.location.origin;
  var cfg = { dim_after_min: 30, dim_level: 0.45, night: '23-7', night_level: 0.7,
              off_after_min: 120, turn_off: false, arrival: true, arrival_level: 0.5 };
  var lastKey = Date.now(), asked = false, arriving = false;
  var $ = function (id) { return document.getElementById(id); };

  function inWindow(win, h) {   // "23-7" crosses midnight
    var p = String(win || '').split('-').map(Number);
    if (p.length !== 2 || isNaN(p[0]) || isNaN(p[1])) return false;
    return p[0] <= p[1] ? (h >= p[0] && h < p[1]) : (h >= p[0] || h < p[1]);
  }

  function level(value, fast) {
    var d = $('dimmer');
    d.style.transition = fast ? 'opacity 0.4s ease' : 'opacity 60s linear';
    d.style.opacity = String(Math.max(0, Math.min(0.9, 1 - value)));
  }

  function evaluate() {
    if (document.hidden) return;
    if (window.Cameras && window.Cameras.isOpen()) { lastKey = Date.now(); level(1, true); return; }
    var idle = (Date.now() - lastKey) / 60000;
    var night = inWindow(cfg.night, new Date().getHours());
    var frame = document.body.classList.contains('frame');
    var v = arriving ? cfg.arrival_level : 1;
    if (frame && night) v = Math.min(v, cfg.night_level);
    if (idle >= cfg.dim_after_min) v = Math.min(v, cfg.dim_level);
    level(v, false);
    if (cfg.turn_off && night && idle >= cfg.off_after_min && !asked) {
      asked = true;
      var x = new XMLHttpRequest();
      x.open('POST', SERVER + '/api/turn-off?idle_min=' + Math.round(idle));
      x.timeout = 8000;
      x.send();
    }
  }

  function touched() { lastKey = Date.now(); asked = false; arriving = false; level(1, true); }
  document.addEventListener('keydown', touched, true);
  // coming back to the front brings light — except on arrival: the TV shows the page right after
  // loading it (prelaunch), and that isn't anyone asking for light
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) return;
    if (arriving) { lastKey = Date.now(); level(cfg.arrival_level, true); } else touched();
  });

  window.Energy = {
    configure: function (c) { if (c) for (var k in c) if (Object.prototype.hasOwnProperty.call(c, k)) cfg[k] = c[k]; },
    _simulateIdle: function (min) { lastKey = Date.now() - min * 60000; evaluate(); }   // preview only
  };
  setInterval(evaluate, 30000);

  // arrival: first load only (TV powering on). The server config arrives later; the default applies.
  if (cfg.arrival) {
    arriving = true;
    document.body.classList.add('frame');
    level(cfg.arrival_level, true);
  }
})();
