// A small stand-in for the TV's browser: just enough DOM, timers, Image and XMLHttpRequest to run the
// launcher scripts in a node:vm context. Time only moves when a test calls `advance(ms)`.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const LAUNCHER = path.join(__dirname, '../launcher');
const UPSTREAM = path.join(__dirname, '../../launcher');

class ClassList {
  constructor(el) { this.el = el; }
  get set() { return new Set(this.el.className.split(/\s+/).filter(Boolean)); }
  write(s) { this.el.className = [...s].join(' '); }
  add(...c) { const s = this.set; c.forEach((x) => s.add(x)); this.write(s); }
  remove(...c) { const s = this.set; c.forEach((x) => s.delete(x)); this.write(s); }
  contains(c) { return this.set.has(c); }
  toggle(c, on) {
    const want = on === undefined ? !this.contains(c) : on;
    if (want) this.add(c); else this.remove(c);
    return want;
  }
}

class Element {
  constructor(tag, id) {
    this.tagName = tag; this.id = id; this.className = ''; this.hidden = false; this.style = {};
    this.textContent = ''; this.children = []; this.attributes = {}; this._html = ''; this.src = '';
    this.classList = new ClassList(this);
  }
  set innerHTML(v) { this._html = v; this.children = []; this._q = {}; }
  get innerHTML() { return this._html; }
  appendChild(c) { this.children.push(c); return c; }
  // enough for the launcher: tiles and layers look up their <img> and the "no signal" line,
  // which the markup creates `hidden`
  querySelector(sel) {
    this._q = this._q || {};
    if (!this._q[sel]) { this._q[sel] = new Element(sel, ''); this._q[sel].hidden = sel === '.cam-down'; }
    return this._q[sel];
  }
  setAttribute(k, v) { this.attributes[k] = v; }
  removeAttribute(k) { delete this.attributes[k]; if (k === 'src') this.src = ''; }
  load() {}
  play() { return Promise.resolve(); }
}

const IDS = ['layer-a', 'layer-b', 'veil', 'dimmer', 'ui', 'clock', 'date', 'weather', 'weather-temp',
  'weather-text', 'apps', 'credit', 'cameras', 'cam-title', 'cam-grid', 'cam-hint', 'cam-full', 'cam-video',
  'cam-full-name', 'cam-full-note', 'cam-full-hint', 'alert', 'alert-photo', 'alert-title', 'alert-time', 'toast'];

/**
 * opts.server(method, url) → { status, body } | null (null = network error)
 * opts.images(url) → true (loads) | false (fails); default: everything loads
 * opts.tizen: put a `tizen` object in the window (the scripts then behave as on the TV)
 * opts.now: starting epoch ms (default 2026-01-01 12:00 local)
 */
function boot(scripts, opts = {}) {
  let now = opts.now ?? new Date(2026, 0, 1, 12, 0, 0).getTime();
  let timers = []; let nextId = 1;
  const requests = []; const imageLoads = [];
  const listeners = {};
  const elements = Object.fromEntries(IDS.map((id) => [id, new Element('div', id)]));
  elements['cameras'].hidden = true; elements['cam-full'].hidden = true;
  elements['weather'].hidden = true; elements['toast'].hidden = true;

  const RealDate = Date;
  class FakeDate extends RealDate {
    constructor(...a) { if (a.length) super(...a); else super(now); }
    static now() { return now; }
  }

  function addTimer(fn, ms, repeat) {
    const id = nextId++;
    timers.push({ id, at: now + Math.max(0, ms || 0), fn, repeat: repeat ? Math.max(1, ms) : 0 });
    return id;
  }
  function clear(id) { timers = timers.filter((t) => t.id !== id); }
  function advance(ms) {
    const end = now + ms;
    for (let runs = 0; ; runs++) {
      if (runs > 200000) throw new Error('runaway timers: something reschedules itself with no delay');
      timers.sort((a, b) => a.at - b.at || a.id - b.id);
      const t = timers[0];
      if (!t || t.at > end) break;
      now = t.at;
      if (t.repeat) t.at += t.repeat; else clear(t.id);
      t.fn();
    }
    now = end;
  }

  class FakeImage {
    set src(url) {
      this._src = url; imageLoads.push(url);
      const ok = opts.images ? opts.images(url) : true;
      addTimer(() => { if (ok) { this.width = 1600; this.height = 900; if (this.onload) this.onload(); } else if (this.onerror) this.onerror(); }, 0);
    }
    get src() { return this._src; }
  }

  class FakeXHR {
    open(method, url) { this.method = method; this.url = url; }
    send() {
      requests.push({ method: this.method, url: this.url });
      const answer = opts.server ? opts.server(this.method, this.url) : { status: 404, body: '' };
      addTimer(() => {
        if (!answer) { if (this.onerror) this.onerror(); return; }
        this.status = answer.status; this.responseText = typeof answer.body === 'string' ? answer.body : JSON.stringify(answer.body);
        if (this.onload) this.onload();
      }, 1);
    }
  }

  const document = {
    hidden: false,
    body: new Element('body', 'body'),
    getElementById: (id) => elements[id] || null,
    createElement: (tag) => new Element(tag, ''),
    addEventListener: (type, fn, capture) => { (listeners[type] = listeners[type] || []).push({ fn, capture: !!capture }); }
  };

  const window = {
    location: { origin: 'http://server' },
    ART_HOME_SETTINGS: opts.settings || { server: 'http://server', locale: opts.locale || 'en' }
  };
  if (opts.tizen) window.tizen = opts.tizen;
  const context = vm.createContext({
    window, document, Date: FakeDate, Image: FakeImage, XMLHttpRequest: FakeXHR, JSON, Math, String, Object,
    Array, Number, isNaN, parseInt, encodeURIComponent, Promise,
    setTimeout: (fn, ms) => addTimer(fn, ms, false), clearTimeout: clear,
    setInterval: (fn, ms) => addTimer(fn, ms, true), clearInterval: clear,
    requestAnimationFrame: (fn) => addTimer(fn, 16, false)
  });
  // the scripts read globals (tizen, T, webapis) the way a browser exposes window properties
  vm.runInContext('Object.defineProperty(this, "self", { get: () => window });', context);
  for (const k of Object.keys(window)) context[k] = window[k];

  function load(file) {
    const full = file.startsWith('upstream:') ? path.join(UPSTREAM, file.slice(9)) : path.join(LAUNCHER, file);
    vm.runInContext(fs.readFileSync(full, 'utf8'), context, { filename: full });
    for (const k of Object.keys(window)) context[k] = window[k];   // what the script just published
  }
  scripts.forEach(load);

  function key(keyCode) {
    let stopped = false;
    const ev = { keyCode, preventDefault() { ev.prevented = true; }, stopImmediatePropagation() { stopped = true; } };
    const all = (listeners.keydown || []);
    for (const l of all.filter((x) => x.capture).concat(all.filter((x) => !x.capture))) {
      if (stopped) break;
      l.fn(ev);
    }
    return ev;
  }
  function setHidden(hidden) {
    document.hidden = hidden;
    (listeners.visibilitychange || []).forEach((l) => l.fn({}));
  }

  return { window, document, elements, requests, imageLoads, advance, key, setHidden, load, now: () => now };
}

module.exports = { boot };
