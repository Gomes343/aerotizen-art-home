"""Art Home server — artworks, weather, app row, cameras and detection alerts for the TV, over the LAN.

The TV package asks `/api/home` and loads images from `/art/`. If this server is down, the TV keeps a
clock, the bundled artwork and its last app row. Python standard library only.

    ART_HOME_CONFIG=config.json HA_TOKEN=... python3 server.py
    GET  /api/home              → works, weather, apps, cameras, energy, alerts settings
    GET  /api/alerts?since=<n>  → new Frigate detections for the pop-up (since=-1: none of the past)
    GET  /art/<file>.jpg
    GET  /app/...               → the built TV package, to preview it in a desktop browser
    GET  /demo/<file>.jpg       → placeholder camera frames (demo mode, for screenshots)
    GET  /healthz
    POST /api/turn-off?idle_min=<n>[&dry_run=1] → the TV ASKS; this server checks and decides
"""
import http.server
import json
import mimetypes
import os
import pathlib
import random
import threading
import time
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
CONFIG = pathlib.Path(os.environ.get("ART_HOME_CONFIG", HERE / "config.json"))
ART = pathlib.Path(os.environ.get("ART_HOME_ART", HERE / "art"))
APP = pathlib.Path(os.environ.get("ART_HOME_APP", HERE.parent / "dist" / "package"))
DEMO = HERE / "demo"
HA_TOKEN = os.environ.get("HA_TOKEN")
WEATHER_TTL = 600

CONDITIONS = {
    "en": {"clear-night": "Clear", "cloudy": "Cloudy", "exceptional": "Unusual weather", "fog": "Fog",
           "hail": "Hail", "lightning": "Lightning", "lightning-rainy": "Thunderstorm",
           "partlycloudy": "Partly cloudy", "pouring": "Heavy rain", "rainy": "Rain", "snowy": "Snow",
           "snowy-rainy": "Sleet", "sunny": "Sunny", "windy": "Windy", "windy-variant": "Windy"},
    "pt-BR": {"clear-night": "Céu limpo", "cloudy": "Nublado", "exceptional": "Tempo incomum", "fog": "Neblina",
              "hail": "Granizo", "lightning": "Raios", "lightning-rainy": "Tempestade",
              "partlycloudy": "Parcialmente nublado", "pouring": "Chuva forte", "rainy": "Chuva", "snowy": "Neve",
              "snowy-rainy": "Chuva e neve", "sunny": "Ensolarado", "windy": "Vento", "windy-variant": "Vento"},
}


def config():
    return json.loads(CONFIG.read_text()) if CONFIG.exists() else {}


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def ha_call(cfg, method, path, body=None):
    url = cfg.get("home_assistant", {}).get("url")
    if not url or not HA_TOKEN:
        raise RuntimeError("Home Assistant url or HA_TOKEN missing")
    req = urllib.request.Request(f"{url}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.load(r)


# ---------------------------------------------------------------- weather (from a Home Assistant entity)
_weather = {"at": 0.0, "data": None}
_weather_lock = threading.Lock()


def weather(cfg):
    """Reads the weather entity; cached 10 min. Failure keeps the last good value (or None: the TV hides it)."""
    entity = cfg.get("home_assistant", {}).get("weather_entity")
    if not entity:
        return None
    with _weather_lock:
        if time.time() - _weather["at"] < WEATHER_TTL:
            return _weather["data"]
        _weather["at"] = time.time()
        try:
            s = ha_call(cfg, "GET", f"/api/states/{entity}")
            if s.get("state") not in (None, "unavailable", "unknown"):
                texts = CONDITIONS.get(cfg.get("locale", "en"), CONDITIONS["en"])
                _weather["data"] = {"state": s["state"], "text": texts.get(s["state"], s["state"]),
                                    "temperature": s.get("attributes", {}).get("temperature")}
        except Exception as e:  # noqa: BLE001 — without HA the TV just doesn't show the weather
            log(f"weather: Home Assistant unreachable ({e.__class__.__name__}); keeping the last value")
        return _weather["data"]


# ---------------------------------------------------------------- artworks
def works():
    cat = ART / "catalog.json"
    items = json.loads(cat.read_text()) if cat.exists() else []
    items = [w for w in items if (ART / w.get("file", "")).exists()]
    random.shuffle(items)
    keep = ("id", "title", "artist", "date", "museum", "file", "width", "height", "color")
    return [{k: w.get(k) for k in keep} for w in items]


def cameras(cfg):
    c = cfg.get("cameras", {})
    if cfg.get("demo"):
        names = ["Front door", "Garden", "Backyard", "Driveway", "Side gate"]
        return {"frigate_url": "", "go2rtc_url": "",
                "list": [{"name": n, "frigate": f"demo{i}", "stream": "", "sub": "",
                          "snapshot_url": f"/demo/cam{i}.jpg"} for i, n in enumerate(names, 1)]}
    return {"frigate_url": c.get("frigate_url", ""), "go2rtc_url": c.get("go2rtc_url", ""),
            "list": c.get("list", [])}


# ---------------------------------------------------------------- detection alerts (Frigate events)
class Alerts:
    """Reads Frigate's /api/events with TV rules from config → alerts. The TV asks by sequence number;
    when it opens it gets only the current number, never the past."""

    def _fail(self, msg):
        if msg != self.error:   # log each new failure once, not every 2 s
            log(f"alerts: {msg}")
        self.error = msg

    def __init__(self):
        self.seq = 0
        self.items = []          # last 30, each with "seq"
        self.seen = {}           # event id → when seen
        self.last_per_cam = {}   # (camera, label) → when announced (minimum interval)
        self.error = None
        self.lock = threading.Lock()

    def step(self, cfg):
        a = cfg.get("alerts", {})
        frigate = cfg.get("cameras", {}).get("frigate_url")
        if not frigate:
            return
        cams = set(a.get("cameras") or [c.get("frigate") for c in cfg.get("cameras", {}).get("list", [])])
        labels = set(a.get("labels", ["person", "car", "dog", "cat"]))
        minimum = float(a.get("min_score", 0.7))
        interval = float(a.get("interval_s", 60))
        now = time.time()
        q = urllib.parse.urlencode({"after": int(now - 90), "limit": 25, "include_thumbnails": 0})
        try:
            with urllib.request.urlopen(f"{frigate}/api/events?{q}", timeout=5) as r:
                events = json.load(r)
            if self.error:
                log("alerts: Frigate is back")
            self.error = None
        except Exception as e:  # noqa: BLE001 — Frigate down: the TV just gets no pop-ups
            self._fail(f"Frigate unreachable ({e.__class__.__name__})")
            return
        for e in sorted(events, key=lambda x: x.get("start_time") or 0):
            eid = e.get("id")
            if not eid or eid in self.seen:
                continue
            d = e.get("data") or {}
            score = max(d.get("top_score") or 0, d.get("score") or 0, e.get("top_score") or 0)
            if e.get("camera") not in cams or e.get("label") not in labels or score < minimum:
                continue   # not marked as seen: the score may rise while the event runs
            self.seen[eid] = now
            key = (e["camera"], e["label"])
            if now - self.last_per_cam.get(key, 0) < interval:
                continue
            self.last_per_cam[key] = now
            with self.lock:
                self.seq += 1
                self.items.append({
                    "seq": self.seq, "id": eid, "camera": e["camera"], "label": e["label"],
                    "who": e.get("sub_label") if isinstance(e.get("sub_label"), str) else None,
                    "score": round(score, 2), "start": e.get("start_time"),
                    "photo": f"{frigate}/api/events/{eid}/snapshot.jpg?crop=1&h=400",
                    "thumbnail": f"{frigate}/api/events/{eid}/thumbnail.jpg",
                })
                self.items = self.items[-30:]
            log(f"alert: {e['label']} on {e['camera']} ({score:.2f})")
        for eid, when in list(self.seen.items()):
            if now - when > 600:
                del self.seen[eid]

    def since(self, n):
        with self.lock:
            if n < 0:
                return {"seq": self.seq, "alerts": []}
            return {"seq": self.seq, "alerts": [x for x in self.items if x["seq"] > n]}

    def run(self):
        while True:
            cfg = config()
            if cfg.get("alerts", {}).get("enabled", True) and not cfg.get("demo"):
                try:
                    self.step(cfg)
                except Exception as e:  # noqa: BLE001 — the loop never dies
                    self._fail(f"{e.__class__.__name__}: {e}")
            time.sleep(cfg.get("alerts", {}).get("poll_s", 2))


ALERTS = Alerts()


# ---------------------------------------------------------------- turning the TV off (energy saving)
_last_off = 0.0


def in_window(win, h):
    try:
        a, b = (int(x) for x in str(win).split("-"))
    except ValueError:
        return False
    return a <= h < b if a <= b else (h >= a or h < b)


def turn_off(idle_min, dry_run=False):
    """The TV only asks; this decides: option on, night window (SERVER clock — run it in your timezone),
    at most once per 10 min. `dry_run` checks everything and does NOT turn off: test only with it."""
    global _last_off
    cfg = config()
    e = cfg.get("energy", {})
    if not e.get("turn_off", False):
        return {"turned_off": False, "reason": "energy.turn_off is disabled"}
    if not in_window(e.get("night", "23-7"), time.localtime().tm_hour):
        return {"turned_off": False, "reason": "outside the night window", "local_time": time.strftime("%H:%M")}
    if time.time() - _last_off < 600:
        return {"turned_off": False, "reason": "already asked less than 10 min ago"}
    entity = cfg.get("tv", {}).get("ha_media_player")
    if not entity:
        return {"turned_off": False, "reason": "tv.ha_media_player is not set"}
    if dry_run:
        return {"turned_off": False, "reason": "dry run: would turn off now", "local_time": time.strftime("%H:%M")}
    _last_off = time.time()
    ha_call(cfg, "POST", "/api/services/media_player/turn_off", {"entity_id": entity})
    log(f"energy: TV turned off (idle {idle_min} min at night)")
    return {"turned_off": True}


# ---------------------------------------------------------------- HTTP
class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, base, rel, cache):
        target = (base / rel).resolve()
        if base.resolve() not in target.parents or not target.is_file():
            self.send_error(404)
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _query(self):
        return urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        cfg = config()
        if path == "/healthz":
            n = len(works())
            self._json({"ok": n > 0, "works": n, "alerts": {"seq": ALERTS.seq, "error": ALERTS.error}})
        elif path == "/api/home":
            demo_weather = {"state": "partlycloudy", "text": "Partly cloudy", "temperature": 21}
            self._json({"works": works(), "weather": demo_weather if cfg.get("demo") else weather(cfg),
                        "apps": cfg.get("apps", []),
                        "cameras": cameras(cfg), "energy": cfg.get("energy", {}), "alerts": cfg.get("alerts", {}),
                        "rotate_s": cfg.get("art", {}).get("rotate_s", 90),
                        "frame_after_s": cfg.get("art", {}).get("frame_after_s", 20)})
        elif path == "/api/alerts":
            try:
                n = int(self._query().get("since", ["-1"])[0])
            except ValueError:
                n = -1
            self._json(ALERTS.since(n))
        elif path.startswith("/art/"):
            self._file(ART, path[len("/art/"):], "public, max-age=604800")
        elif path.startswith("/demo/"):
            self._file(DEMO, path[len("/demo/"):], "no-store")
        elif path in ("/", "/app"):
            self.send_response(302)
            self.send_header("Location", "/app/index.html")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path.startswith("/app/"):
            self._file(APP, path[len("/app/"):], "no-store")
        else:
            self.send_error(404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/turn-off":
            q = self._query()
            try:
                self._json(turn_off(q.get("idle_min", ["?"])[0], dry_run=q.get("dry_run") == ["1"]))
            except Exception as e:  # noqa: BLE001 — HA down: the TV stays on and says why
                self._json({"turned_off": False, "reason": e.__class__.__name__}, 502)
            return
        self.send_error(404)

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    port = int(config().get("server", {}).get("port", 8895))
    log(f"art-home server on 0.0.0.0:{port} (config {CONFIG}, art {ART})")
    threading.Thread(target=ALERTS.run, name="alerts", daemon=True).start()
    http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
