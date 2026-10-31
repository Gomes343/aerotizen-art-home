"""Photos from Google Photos — picked by you, downloaded once, served by this server. Standard library only.

Google lets an app read only the photos a person picks in Google's own picker (the Photos Picker API);
since 2025 no app can read a library or follow an album. So: you pick on your phone, this server
downloads those photos once (fitted inside 2560 x 1440) and keeps them. The TV never talks to Google, and
the photos keep showing with the internet down.

One-time setup:
  1. Google Cloud console → enable "Google Photos Picker API" → create an OAuth client of type
     "Desktop app" → download its JSON to <data>/google-client.json (data: ART_HOME_DATA, default
     server/data).
  2. Sign in once:  python3 photos.py login     (Docker: docker exec -it art-home python photos.py login)
  3. Turn photos on in config.json ("photos": {"enabled": true}) and open http://SERVER:8895/photos on
     your phone → "Choose photos".

    python3 photos.py login | status | logout
"""

import base64
import calendar
import hashlib
import http.server
import json
import os
import pathlib
import random
import re
import secrets
import select
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
DATA = pathlib.Path(os.environ.get("ART_HOME_DATA", HERE / "data"))
SCOPE = "https://www.googleapis.com/auth/photospicker.mediaitems.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
PICKER_API = "https://photospicker.googleapis.com/v1"
LOGIN_PORT = 8796
FIT = "=w2560-h1440"  # Google resizes to fit inside this box, keeping the aspect ratio
THUMB = "=w480-h480"
MIN_POLL_S = 1.0  # never poll Google faster than this, whatever the session suggests
TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} photos: {msg}", flush=True)


def _client_file():
    return DATA / "google-client.json"


def _token_file():
    return DATA / "google-token.json"


def _photos_dir():
    return DATA / "photos"


class NotConnected(Exception):
    """No Google client file or no sign-in yet: the page shows the setup steps."""


# ---------------------------------------------------------------- OAuth (Desktop-app client, loopback)
def client():
    try:
        raw = json.loads(_client_file().read_text())
    except (OSError, ValueError) as e:
        raise NotConnected(f"{_client_file()} is missing or not JSON") from e
    c = raw.get("installed") or raw.get("web") or raw
    if not c.get("client_id") or not c.get("client_secret"):
        raise NotConnected(f"{_client_file()} has no client_id/client_secret")
    return c


def _post_form(url, fields):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode(), method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _write_private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def login_url(redirect, verifier, state):
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    q = {
        "client_id": client()["client_id"],
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(q)}"


def exchange(code, redirect, verifier):
    """The code from Google's redirect → a refresh token, saved (mode 600) in the data folder."""
    c = client()
    t = _post_form(
        TOKEN_URL,
        {
            "code": code,
            "client_id": c["client_id"],
            "client_secret": c["client_secret"],
            "redirect_uri": redirect,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        },
    )
    if not t.get("refresh_token"):
        raise RuntimeError("Google answered without a refresh token")
    _write_private(_token_file(), json.dumps({"refresh_token": t["refresh_token"], "scope": t.get("scope")}))
    with _access_lock:
        _access.update(token=t.get("access_token"), until=time.time() + int(t.get("expires_in", 0)) - 60)


_access = {"token": None, "until": 0.0}
_access_lock = threading.Lock()


def connected():
    try:
        client()
    except NotConnected:
        return False
    return _token_file().exists()


def access_token(force=False):
    with _access_lock:
        if not force and _access["token"] and time.time() < _access["until"]:
            return _access["token"]
        c = client()
        try:
            refresh = json.loads(_token_file().read_text())["refresh_token"]
        except (OSError, ValueError, KeyError) as e:
            raise NotConnected("not signed in to Google Photos (python3 photos.py login)") from e
        t = _post_form(
            TOKEN_URL,
            {
                "refresh_token": refresh,
                "client_id": c["client_id"],
                "client_secret": c["client_secret"],
                "grant_type": "refresh_token",
            },
        )
        _access.update(token=t["access_token"], until=time.time() + int(t.get("expires_in", 3600)) - 60)
        return _access["token"]


def logout():
    """Forgets the sign-in here and asks Google to revoke it. The downloaded photos stay."""
    try:
        refresh = json.loads(_token_file().read_text())["refresh_token"]
    except (OSError, ValueError, KeyError):
        return False
    try:
        _post_form(REVOKE_URL, {"token": refresh})
    except (urllib.error.URLError, OSError, ValueError) as e:
        log(f"revoke at Google failed ({e.__class__.__name__}); forgetting the sign-in here anyway")
    _token_file().unlink(missing_ok=True)
    with _access_lock:
        _access.update(token=None, until=0.0)
    return True


# ---------------------------------------------------------------- Picker API
def _request(method, url, body=None, raw=False):
    """One authorized call; a 401 refreshes the access token once."""
    for attempt in (0, 1):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {access_token(force=attempt == 1)}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                if raw:
                    return r.headers.get("Content-Type", "").split(";")[0].strip(), r.read()
                answer = r.read()
                return json.loads(answer) if answer else {}
        except urllib.error.HTTPError as e:
            if e.code != 401 or attempt:
                raise
            e.close()
    raise AssertionError("unreachable")


def _seconds(duration, default):
    """Google durations come as "5s" or "1.5s"."""
    m = re.fullmatch(r"([\d.]+)s", str(duration or ""))
    return float(m.group(1)) if m else default


# ---------------------------------------------------------------- the library on disk
_catalog_lock = threading.Lock()


def catalog():
    try:
        return json.loads((_photos_dir() / "catalog.json").read_text())
    except (OSError, ValueError):
        return []


def _save_catalog(items):
    d = _photos_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "catalog.json.tmp"
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1))
    os.replace(tmp, d / "catalog.json")


def items():
    """What the TV gets: photos whose file exists, shuffled, with only the fields it uses."""
    keep = ("id", "file", "date", "width", "height")
    found = [{**{k: p.get(k) for k in keep}, "kind": "photo"} for p in catalog()]
    found = [p for p in found if p["file"] and (_photos_dir() / p["file"]).is_file()]
    random.shuffle(found)
    return found


def delete(photo_id):
    with _catalog_lock:
        current = catalog()
        gone = [p for p in current if p.get("id") == photo_id]
        if not gone:
            return False
        for p in gone:
            (_photos_dir() / p["file"]).unlink(missing_ok=True)
            (_photos_dir() / "thumbs" / p["file"]).unlink(missing_ok=True)
        _save_catalog([p for p in current if p.get("id") != photo_id])
    return True


def _name(media_id, ext):
    return hashlib.sha256(media_id.encode()).hexdigest()[:20] + ext


def _local_date(create_time):
    """ "2025-03-12T22:10:00Z" → the date where this server is ("2025-03-12"), or None."""
    try:
        t = time.strptime(re.sub(r"\.\d+", "", create_time), "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None
    return time.strftime("%Y-%m-%d", time.localtime(calendar.timegm(t)))


# ---------------------------------------------------------------- one picking job at a time
class Picker:
    """Create a session → the phone opens its pickerUri → poll until the person is done → download
    every picked PHOTO (videos are skipped) → delete the session, as Google asks."""

    def __init__(self):
        self.lock = threading.Lock()
        self.job = {"state": "idle"}
        self.thread = None

    def status(self):
        with self.lock:
            return dict(self.job)

    def _set(self, **kw):
        with self.lock:
            self.job.update(kw)

    def start(self, max_items=0):
        with self.lock:
            if self.job.get("state") == "waiting" and time.time() < self.job.get("expires", 0):
                return dict(self.job)  # the phone may simply open the same link again
            if self.job.get("state") == "downloading":
                return dict(self.job)
        body = {"pickingConfig": {"maxItemCount": str(int(max_items))}} if max_items else {}
        session = _request("POST", f"{PICKER_API}/sessions", body=body)
        with self.lock:
            self.job = {
                "state": "waiting",
                "picker_uri": session["pickerUri"].rstrip("/") + "/autoclose",
                "session": session["id"],
                "picked": 0,
                "saved": 0,
                "skipped": 0,
                "failed": 0,
                "expires": time.time() + _seconds(session.get("pollingConfig", {}).get("timeoutIn"), 1800),
            }
            job = dict(self.job)
        self.thread = threading.Thread(target=self._run, args=(session,), name="photos-pick", daemon=True)
        self.thread.start()
        return job

    def _run(self, session):
        sid = session["id"]
        try:
            if not self._wait(session):
                return
            self._set(state="downloading")
            for item in self._picked(sid):
                self._download(item)
            self._set(state="done", finished=time.time())
            job = self.status()
            log(f"{job['saved']} new, {job['skipped']} skipped, {job['failed']} failed")
        except Exception as e:
            self._set(state="error", error=f"{e.__class__.__name__}: {e}")
            log(f"picking failed: {e.__class__.__name__}: {e}")
        finally:
            try:
                _request("DELETE", f"{PICKER_API}/sessions/{sid}")
            except Exception as e:
                log(f"could not delete session ({e.__class__.__name__}); Google expires it anyway")

    def _wait(self, session):
        sid = session["id"]
        interval = _seconds(session.get("pollingConfig", {}).get("pollInterval"), 5)
        while time.time() < self.status().get("expires", 0):
            s = _request("GET", f"{PICKER_API}/sessions/{sid}")
            if s.get("mediaItemsSet"):
                return True
            interval = _seconds(s.get("pollingConfig", {}).get("pollInterval"), interval)
            time.sleep(max(MIN_POLL_S, interval))
        self._set(state="expired")
        return False

    def _picked(self, sid):
        token = ""
        while True:
            q = {"sessionId": sid, "pageSize": 100, **({"pageToken": token} if token else {})}
            page = _request("GET", f"{PICKER_API}/mediaItems?{urllib.parse.urlencode(q)}")
            batch = page.get("mediaItems", [])
            self._set(picked=self.status()["picked"] + len(batch))
            yield from batch
            token = page.get("nextPageToken")
            if not token:
                return

    def _download(self, item):
        media_id, f = item.get("id"), item.get("mediaFile") or {}
        if item.get("type") != "PHOTO" or not media_id or not f.get("baseUrl"):
            self._set(skipped=self.status()["skipped"] + 1)
            return
        if any(p.get("id") == media_id for p in catalog()):
            self._set(skipped=self.status()["skipped"] + 1)
            return
        try:
            kind, body = _request("GET", f["baseUrl"] + FIT, raw=True)
            ext = TYPES.get(kind)
            if not ext or not body:
                raise ValueError(f"unexpected content type {kind!r}")
            _, thumb = _request("GET", f["baseUrl"] + THUMB, raw=True)
        except Exception as e:
            self._set(failed=self.status()["failed"] + 1)
            log(f"download failed ({e.__class__.__name__})")
            return
        name = _name(media_id, ext)
        (_photos_dir() / "thumbs").mkdir(parents=True, exist_ok=True)
        (_photos_dir() / name).write_bytes(body)
        (_photos_dir() / "thumbs" / name).write_bytes(thumb)
        meta = f.get("mediaFileMetadata") or {}
        entry = {
            "id": media_id,
            "file": name,
            "date": _local_date(item.get("createTime")),
            "width": meta.get("width"),
            "height": meta.get("height"),
            "added": time.strftime("%Y-%m-%d"),
        }
        with _catalog_lock:
            _save_catalog([*catalog(), entry])
        self._set(saved=self.status()["saved"] + 1)


PICKER = Picker()


def status():
    """For the phone page: what is set up, how many photos, and the current picking job."""
    try:
        client()
        has_client = True
    except NotConnected:
        has_client = False
    job = PICKER.status()
    job.pop("session", None)
    return {
        "client": has_client,
        "signed_in": _token_file().exists(),
        "count": len(catalog()),
        "photos": sorted(catalog(), key=lambda p: p.get("added") or "", reverse=True),
        "job": job,
    }


# ---------------------------------------------------------------- CLI: the one-time sign-in
def _login():
    redirect = f"http://127.0.0.1:{LOGIN_PORT}/"
    verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(16)
    got = {}

    class Catch(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            got.update(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query))
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Art Home: signed in. You can close this tab.")

        def log_message(self, *_):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", LOGIN_PORT), Catch)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("Open this address in a browser and sign in with the Google account that has the photos:\n")
    print(login_url(redirect, verifier, state), "\n")
    print(f"If that browser is on another computer, the last page will fail to load ({redirect}…).")
    print(
        "That is expected: copy the whole address from its address bar and paste it here.\n> ",
        end="",
        flush=True,
    )
    while "code" not in got:
        ready, _, _ = select.select([sys.stdin], [], [], 0.5)
        if ready:
            line = sys.stdin.readline().strip()
            got.update(urllib.parse.parse_qs(urllib.parse.urlsplit(line).query))
    srv.shutdown()
    if got.get("state", [""])[0] != state:
        sys.exit("refused: the address is not from this sign-in (state does not match)")
    exchange(got["code"][0], redirect, verifier)
    print(f"\nSigned in. Token saved in {_token_file()} (mode 600). Now open /photos on your phone.")


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "login":
        _login()
    elif cmd == "logout":
        print("signed out" if logout() else "was not signed in")
    elif cmd == "status":
        s = status()
        client_file = "ok" if s["client"] else "missing"
        print(f"client file: {client_file} · signed in: {s['signed_in']} · photos: {s['count']}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
