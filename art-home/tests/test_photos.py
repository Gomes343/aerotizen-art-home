"""Photos from Google Photos (server/photos.py): sign-in, picking, downloading, the library on disk.

No network and no Google account: a fake Google (token endpoint + Photos Picker API + image hosts) runs
on 127.0.0.1 and the module's endpoints are pointed at it.
"""

import http.server
import json
import os
import pathlib
import shutil
import stat
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "server"))
import photos

JPEG = b"\xff\xd8\xff\xe0fake-jpeg\xff\xd9"


class FakeGoogle:
    """Answers like Google. `items` are the picked media items; `polls_until_done` how many session
    reads say "still picking"; `stale` makes the first Picker call answer 401."""

    def __init__(self):
        self.items = []
        self.polls_until_done = 1
        self.stale = False
        self.calls = []
        self.deleted = []
        self.media_type = "image/jpeg"
        self.revoked = []
        fake = self

        class H(http.server.BaseHTTPRequestHandler):
            def _send(self, status, body=b"", kind="application/json"):
                if isinstance(body, dict):
                    body = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self):
                auth = self.headers.get("Authorization", "")
                if fake.stale:
                    fake.stale = False
                    return False
                return auth.startswith("Bearer AT")

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n).decode()
                fake.calls.append(("POST", self.path, self.headers.get("Authorization")))
                if self.path == "/token":
                    form = urllib.parse.parse_qs(raw)
                    if form["grant_type"] == ["authorization_code"]:
                        ok = form.get("code_verifier") and form["code"] == ["CODE"]
                        return self._send(
                            200 if ok else 400,
                            {"access_token": "AT0", "expires_in": 3600, "refresh_token": "RT"} if ok else {},
                        )
                    return self._send(200, {"access_token": f"AT{len(fake.calls)}", "expires_in": 3600})
                if self.path == "/revoke":
                    fake.revoked.append(raw)
                    return self._send(200, {})
                if self.path == "/v1/sessions":
                    if not self._authorized():
                        return self._send(401, {})
                    fake.session_body = json.loads(raw or "{}")
                    return self._send(
                        200,
                        {
                            "id": "S1",
                            "pickerUri": "https://photos.example/pick/S1",
                            "pollingConfig": {"pollInterval": "0.01s", "timeoutIn": "5s"},
                            "mediaItemsSet": False,
                        },
                    )
                self._send(404)

            def do_GET(self):
                fake.calls.append(("GET", self.path, self.headers.get("Authorization")))
                if not self._authorized():
                    return self._send(401, {})
                if self.path == "/v1/sessions/S1":
                    fake.polls_until_done -= 1
                    return self._send(
                        200,
                        {
                            "id": "S1",
                            "mediaItemsSet": fake.polls_until_done < 0,
                            "pollingConfig": {"pollInterval": "0.01s"},
                        },
                    )
                if self.path.startswith("/v1/mediaItems?"):
                    q = urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    assert q["sessionId"] == ["S1"]
                    if not q.get("pageToken"):
                        return self._send(200, {"mediaItems": fake.items[:2], "nextPageToken": "P2"})
                    return self._send(200, {"mediaItems": fake.items[2:]})
                if self.path.startswith("/media/"):
                    return self._send(200, JPEG, fake.media_type)
                self._send(404)

            def do_DELETE(self):
                fake.calls.append(("DELETE", self.path, self.headers.get("Authorization")))
                fake.deleted.append(self.path)
                self._send(200, {})

            def log_message(self, *_):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def stop(self):
        self.srv.shutdown()


def media(mid, kind="PHOTO", when="2025-03-12T23:30:00Z"):
    return {
        "id": mid,
        "type": kind,
        "createTime": when,
        "mediaFile": {
            "baseUrl": f"BASE/media/{mid}",
            "mimeType": "image/jpeg",
            "filename": f"{mid}.jpg",
            "mediaFileMetadata": {"width": 4000, "height": 3000},
        },
    }


class PhotosTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.google = FakeGoogle()
        self.addCleanup(self.google.stop)
        g = self.google.url
        for name, value in {
            "DATA": self.tmp,
            "TOKEN_URL": f"{g}/token",
            "REVOKE_URL": f"{g}/revoke",
            "PICKER_API": f"{g}/v1",
            "MIN_POLL_S": 0.0,
            "log": lambda msg: None,
        }.items():
            p = mock.patch.object(photos, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(photos, "_access", {"token": None, "until": 0.0})
        p.start()
        self.addCleanup(p.stop)
        self.picker = photos.Picker()
        self.addCleanup(self.stop_picker)  # runs before the fake Google stops
        os.environ["TZ"] = "America/Sao_Paulo"
        time.tzset()
        self.addCleanup(time.tzset)
        self.addCleanup(os.environ.pop, "TZ", None)

    def stop_picker(self):
        self.picker._set(expires=0)
        if self.picker.thread:
            self.picker.thread.join(5)

    def sign_in(self):
        (self.tmp / "google-client.json").write_text(
            json.dumps({"installed": {"client_id": "C", "client_secret": "S"}})
        )
        photos.exchange("CODE", "http://127.0.0.1:8796/", "verifier")

    def pick(self, items):
        self.google.items = [
            {
                **m,
                "mediaFile": {
                    **m["mediaFile"],
                    "baseUrl": m["mediaFile"]["baseUrl"].replace("BASE", self.google.url),
                },
            }
            for m in items
        ]
        job = self.picker.start()
        for _ in range(400):
            if self.picker.status()["state"] not in ("waiting", "downloading"):
                break
            time.sleep(0.01)
        return job, self.picker.status()

    # ---- sign-in
    def test_sign_in_keeps_only_the_refresh_token_and_only_for_this_user(self):
        self.sign_in()
        f = self.tmp / "google-token.json"
        self.assertEqual(json.loads(f.read_text())["refresh_token"], "RT")
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        self.assertTrue(photos.connected())

    def test_without_a_client_file_it_is_not_connected_and_says_so(self):
        self.assertFalse(photos.connected())
        with self.assertRaises(photos.NotConnected):
            photos.access_token()
        s = photos.status()
        self.assertEqual((s["client"], s["signed_in"], s["count"]), (False, False, 0))

    def test_the_login_address_asks_only_the_picker_scope_offline_with_pkce(self):
        (self.tmp / "google-client.json").write_text(
            json.dumps({"installed": {"client_id": "C", "client_secret": "S"}})
        )
        q = urllib.parse.parse_qs(
            urllib.parse.urlsplit(photos.login_url("http://127.0.0.1:8796/", "v", "st")).query
        )
        self.assertEqual(q["scope"], [photos.SCOPE])
        self.assertEqual(
            (q["access_type"], q["code_challenge_method"], q["state"]), (["offline"], ["S256"], ["st"])
        )

    # ---- picking
    def test_picking_downloads_the_photos_skips_videos_and_deletes_the_session(self):
        self.sign_in()
        job, end = self.pick([media("A"), media("V", kind="VIDEO"), media("B")])
        self.assertTrue(job["picker_uri"].endswith("/pick/S1/autoclose"))
        self.assertEqual(
            (end["state"], end["picked"], end["saved"], end["skipped"], end["failed"]), ("done", 3, 2, 1, 0)
        )
        self.assertEqual(self.google.deleted, ["/v1/sessions/S1"])
        cat = photos.catalog()
        self.assertEqual([p["id"] for p in cat], ["A", "B"])
        # 23:30 UTC on the 12th is still the 12th in São Paulo (20:30)
        self.assertEqual(cat[0]["date"], "2025-03-12")
        self.assertEqual((cat[0]["width"], cat[0]["height"]), (4000, 3000))
        for p in cat:
            self.assertEqual((self.tmp / "photos" / p["file"]).read_bytes(), JPEG)
            self.assertTrue((self.tmp / "photos" / "thumbs" / p["file"]).is_file())
        downloads = [c for c in self.google.calls if c[1].startswith("/media/")]
        self.assertEqual({c[1].split("=", 1)[1] for c in downloads}, {"w2560-h1440", "w480-h480"})
        self.assertTrue(all(c[2] and c[2].startswith("Bearer ") for c in downloads))

    def test_picking_the_same_photo_again_does_not_download_it_twice(self):
        self.sign_in()
        self.pick([media("A")])
        self.stop_picker()
        self.picker = photos.Picker()
        self.google.polls_until_done = 0
        _, end = self.pick([media("A"), media("C")])
        self.assertEqual((end["saved"], end["skipped"]), (1, 1))
        self.assertEqual(sorted(p["id"] for p in photos.catalog()), ["A", "C"])

    def test_a_stale_access_token_is_refreshed_once(self):
        self.sign_in()
        self.google.stale = True
        job = self.picker.start()
        self.assertEqual(job["state"], "waiting")
        self.assertEqual(sum(1 for c in self.google.calls if c[1] == "/token"), 2)  # sign-in + one refresh

    def test_max_pick_goes_to_google(self):
        self.sign_in()
        self.picker.start(max_items=50)
        self.assertEqual(self.google.session_body, {"pickingConfig": {"maxItemCount": "50"}})

    def test_while_waiting_the_same_link_is_given_again(self):
        self.sign_in()
        self.google.polls_until_done = 10**6
        first = self.picker.start()
        again = self.picker.start()
        self.assertEqual(first["picker_uri"], again["picker_uri"])
        self.assertEqual(sum(1 for c in self.google.calls if c[1] == "/v1/sessions"), 1)

    def test_a_choice_that_takes_too_long_expires_and_still_deletes_the_session(self):
        self.sign_in()
        self.google.polls_until_done = 10**6
        self.picker.start()
        self.picker._set(expires=time.time() + 0.1)
        for _ in range(100):
            if self.picker.status()["state"] == "expired":
                break
            time.sleep(0.01)
        time.sleep(0.05)
        self.assertEqual(self.picker.status()["state"], "expired")
        self.assertEqual(self.google.deleted, ["/v1/sessions/S1"])

    def test_an_unexpected_file_type_counts_as_failed_and_saves_nothing(self):
        self.sign_in()
        self.google.media_type = "text/html"
        _, end = self.pick([media("A")])
        self.assertEqual((end["state"], end["saved"], end["failed"]), ("done", 0, 1))
        self.assertEqual(photos.catalog(), [])

    # ---- the library
    def test_items_are_only_existing_files_marked_as_photos(self):
        self.sign_in()
        self.pick([media("A"), media("B")])
        gone = photos.catalog()[1]["file"]
        (self.tmp / "photos" / gone).unlink()
        got = photos.items()
        self.assertEqual([(p["id"], p["kind"]) for p in got], [("A", "photo")])
        self.assertEqual(set(got[0]), {"id", "file", "date", "width", "height", "kind"})

    def test_delete_removes_the_photo_and_its_thumbnail(self):
        self.sign_in()
        self.pick([media("A"), media("B")])
        f = photos.catalog()[0]["file"]
        self.assertTrue(photos.delete("A"))
        self.assertFalse((self.tmp / "photos" / f).exists())
        self.assertFalse((self.tmp / "photos" / "thumbs" / f).exists())
        self.assertEqual([p["id"] for p in photos.catalog()], ["B"])
        self.assertFalse(photos.delete("nope"))

    def test_logout_revokes_at_google_and_forgets_here_but_keeps_the_photos(self):
        self.sign_in()
        self.pick([media("A")])
        self.assertTrue(photos.logout())
        self.assertEqual(self.google.revoked, ["token=RT"])
        self.assertFalse((self.tmp / "google-token.json").exists())
        self.assertEqual(len(photos.items()), 1)
        self.assertFalse(photos.logout())

    def test_logout_with_google_unreachable_still_forgets_the_sign_in(self):
        self.sign_in()
        with mock.patch.object(photos, "REVOKE_URL", "http://127.0.0.1:9/revoke"):
            self.assertTrue(photos.logout())
        self.assertFalse((self.tmp / "google-token.json").exists())

    def test_dates(self):
        self.assertEqual(photos._local_date("2025-03-13T01:30:00.123Z"), "2025-03-12")  # 22:30 the day before
        self.assertIsNone(photos._local_date("yesterday"))
        self.assertIsNone(photos._local_date(None))


if __name__ == "__main__":
    unittest.main()
