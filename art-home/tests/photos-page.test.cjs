// The phone page that picks photos (server/photos.html), its script run in node:vm with a tiny DOM and a
// fetch the test answers by hand:  node --test art-home/tests/
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const HTML = fs.readFileSync(path.join(__dirname, '../server/photos.html'), 'utf8');
const SCRIPT = HTML.match(/<script>([\s\S]*?)<\/script>/)[1];

function el(id) {
  return { id, hidden: false, disabled: false, className: '', textContent: '', innerHTML: '', children: [],
    firstChild: { textContent: '' }, appendChild(c) { this.children.push(c); return c; }, setAttribute() {} };
}

function boot() {
  const els = {};
  const requests = []; // { method, path, answer(status, body) }
  const windows = [];
  const document = {
    documentElement: {},
    getElementById: (id) => (els[id] = els[id] || el(id)),
    createElement: (tag) => el(tag)
  };
  const fetch = (p, opts) => new Promise((resolve) => {
    requests.push({ method: opts.method, path: p, answer(status, body) {
      resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });
    } });
  });
  const window = {
    open() {
      const w = { location: {}, closed: false, close() { this.closed = true; } };
      windows.push(w);
      return w;
    }
  };
  vm.runInNewContext(SCRIPT, { document, fetch, window, setTimeout: () => 0, clearTimeout() {}, confirm: () => true });
  return { els, requests, windows };
}

const settle = () => new Promise((r) => setImmediate(r));
const READY = { client: true, signed_in: true, count: 0, photos: [], job: { state: 'idle' }, locale: 'en' };
const posts = (page) => page.requests.filter((r) => r.method === 'POST');

test('a double tap asks the server once and opens one tab', async () => {
  const page = boot();
  page.requests[0].answer(200, READY);
  await settle();
  page.els.pick.onclick();
  page.els.pick.onclick(); // the second tap of a double tap, before the server answers
  assert.equal(posts(page).length, 1);
  assert.equal(page.windows.length, 1);
  posts(page)[0].answer(200, { state: 'waiting', picker_uri: 'https://photos.example/pick/S1/autoclose' });
  await settle(); await settle();
  assert.equal(page.windows[0].location.href, 'https://photos.example/pick/S1/autoclose');
  assert.equal(page.els.pick.disabled, false);
});

test('while the choice is still being opened (no link yet) the new tab is closed, never sent to "undefined"', async () => {
  const page = boot();
  page.requests[0].answer(200, READY);
  await settle();
  page.els.pick.onclick();
  posts(page)[0].answer(200, { state: 'starting', picked: 0, saved: 0, skipped: 0, failed: 0 });
  await settle(); await settle();
  assert.equal(page.windows[0].closed, true);
  assert.equal(page.windows[0].location.href, undefined);
});

test('a failed request closes the tab and frees the button', async () => {
  const page = boot();
  page.requests[0].answer(200, READY);
  await settle();
  page.els.pick.onclick();
  posts(page)[0].answer(409, { error: 'Google refused the sign-in' });
  await settle(); await settle();
  assert.equal(page.windows[0].closed, true);
  assert.equal(page.els.pick.disabled, false);
  assert.match(page.els.job.textContent, /Google refused the sign-in/);
});
