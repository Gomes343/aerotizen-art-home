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
import urllib.error
import urllib.parse
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "server"))
import photos

JPEG = b"\xff\xd8\xff\xe0fake-jpeg\xff\xd9"


class FakeGoogle:
    """Answers like Google. `items` are the picked media items; `polls_until_done` how many session
    reads say "still picking" (`never_done`: sessions that never finish); `stale` makes the first
    Picker call answer 401; `refresh_status` is what the token endpoint answers a refresh with;
    `session_delay`/`media_delay` slow those answers down; `poll_failures`/`page_failures` are answers
    given, in order, to the next session reads / picked-item pages instead of the real one (an HTTP status,
    or 0 to drop the connection without answering). Sessions are S1, S2, …"""

    def __init__(self):
        self.items = []
        self.polls_until_done = 1
        self.never_done = set()
        self.poll_interval = "0.01s"
        self.timeout_in = "5s"
        self.stale = False
        self.calls = []
        self.deleted = []
        self.media_type = "image/jpeg"
        self.revoked = []
        self.sessions = 0
        self.session_status = 200
        self.session_delay = 0.0
        self.media_delay = 0.0
        self.refresh_status = 200
        self.poll_failures = []
        self.page_failures = []
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

            def _fail(self, failures):
                """Answers with the next planned failure, if any (0: the connection drops)."""
                if not failures:
                    return False
                status = failures.pop(0)
                if status:
                    self._send(status, {"error": {"code": status}})
                else:
                    self.close_connection = True
                return True

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
                    if fake.refresh_status == 200:
                        return self._send(200, {"access_token": f"AT{len(fake.calls)}", "expires_in": 3600})
                    error = "invalid_grant" if fake.refresh_status == 400 else "backend_error"
                    return self._send(fake.refresh_status, {"error": error})
                if self.path == "/revoke":
                    fake.revoked.append(raw)
                    return self._send(200, {})
                if self.path == "/v1/sessions":
                    if not self._authorized():
                        return self._send(401, {})
                    fake.session_body = json.loads(raw or "{}")
                    time.sleep(fake.session_delay)
                    if fake.session_status != 200:
                        return self._send(fake.session_status, {})
                    fake.sessions += 1
                    sid = f"S{fake.sessions}"
                    return self._send(
                        200,
                        {
                            "id": sid,
                            "pickerUri": f"https://photos.example/pick/{sid}",
                            "pollingConfig": {
                                "pollInterval": fake.poll_interval,
                                "timeoutIn": fake.timeout_in,
                            },
                            "mediaItemsSet": False,
                        },
                    )
                self._send(404)

            def do_GET(self):
                fake.calls.append(("GET", self.path, self.headers.get("Authorization")))
                if not self._authorized():
                    return self._send(401, {})
                if self.path.startswith("/v1/sessions/S"):
                    if self._fail(fake.poll_failures):
                        return None
                    sid = self.path.rsplit("/", 1)[1]
                    if sid not in fake.never_done:
                        fake.polls_until_done -= 1
                    return self._send(
                        200,
                        {
                            "id": sid,
                            "mediaItemsSet": sid not in fake.never_done and fake.polls_until_done < 0,
                            "pollingConfig": {"pollInterval": fake.poll_interval},
                        },
                    )
                if self.path.startswith("/v1/mediaItems?"):
                    q = urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    assert q["sessionId"][0].startswith("S")
                    if self._fail(fake.page_failures):
                        return None
                    if not q.get("pageToken"):
                        return self._send(200, {"mediaItems": fake.items[:2], "nextPageToken": "P2"})
                    return self._send(200, {"mediaItems": fake.items[2:]})
                if self.path.startswith("/media/"):
                    time.sleep(fake.media_delay)
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
            "PAGE_RETRY_S": 0.01,
            "log": lambda msg: None,
        }.items():
            p = mock.patch.object(photos, name, value)
            p.start()
            self.addCleanup(p.stop)
        for name, value in {"_access": {"token": None, "until": 0.0}, "_refused": {"sign_in": None}}.items():
            p = mock.patch.object(photos, name, value, create=True)
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

    def hosted(self, m):
        url = m["mediaFile"]["baseUrl"].replace("BASE", self.google.url)
        return {**m, "mediaFile": {**m["mediaFile"], "baseUrl": url}}

    def pick(self, items):
        self.google.items = [self.hosted(m) for m in items]
        job = self.picker.start()
        self.settle()
        if self.picker.status()["state"] in ("done", "error", "expired") and self.picker.thread:
            self.picker.thread.join(5)  # the session is deleted after the state is set: wait for it
        return job, self.picker.status()

    def settle(self, states=("starting", "waiting", "downloading")):
        for _ in range(400):
            if self.picker.status()["state"] not in states:
                break
            time.sleep(0.01)

    def sessions_created(self):
        return sum(1 for c in self.google.calls if c[:2] == ("POST", "/v1/sessions"))

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

    def test_two_taps_at_once_open_one_choice_at_google(self):
        # the second tap arrives while the first is still asking Google for a session
        self.sign_in()
        self.google.polls_until_done = 10**6
        self.google.session_delay = 0.3
        got = []
        taps = [threading.Thread(target=lambda: got.append(self.picker.start())) for _ in range(2)]
        for t in taps:
            t.start()
            time.sleep(0.05)
        for t in taps:
            t.join(5)
        self.assertEqual(self.sessions_created(), 1)
        self.assertEqual(sorted(j["state"] for j in got), ["starting", "waiting"])  # no link yet for the 2nd
        self.assertEqual(self.picker.status()["picker_uri"], "https://photos.example/pick/S1/autoclose")

    def test_a_pick_replaced_after_running_out_never_writes_over_the_new_one(self):
        # A runs out while its thread sleeps between two polls; a new tap opens B, which is picked
        # and downloaded. A's thread then wakes up: the page must keep saying B is done, not "expired".
        self.sign_in()
        self.google.never_done = {"S1"}
        self.google.poll_interval = "0.3s"
        self.google.timeout_in = "1s"
        self.google.items = [self.hosted(media("A"))]
        self.picker.start()
        time.sleep(0.05)  # A has polled once and sleeps 0.3 s
        self.picker._set(expires=time.time())
        b = self.picker.start()
        self.assertTrue(b["picker_uri"].endswith("/pick/S2/autoclose"))
        self.settle()
        self.assertEqual(self.picker.status()["state"], "done")
        time.sleep(1.5)  # past B's own deadline: A's thread has long woken up
        end = self.picker.status()
        self.assertEqual((end["state"], end["saved"], end["picker_uri"]), ("done", 1, b["picker_uri"]))
        self.assertEqual(sorted(self.google.deleted), ["/v1/sessions/S1", "/v1/sessions/S2"])

    def test_a_failed_start_frees_the_button(self):
        self.sign_in()
        self.google.session_status = 500
        with self.assertRaises(urllib.error.HTTPError):
            self.picker.start()
        self.assertEqual(self.picker.status()["state"], "idle")
        self.google.session_status = 200
        self.google.polls_until_done = 10**6
        self.assertEqual(self.picker.start()["state"], "waiting")

    # ---- the network failing for a moment while the person picks
    def deletes_before_the_end(self):
        """DELETE calls that came before the last call to Google: the session thrown away mid-pick."""
        return [c for c in self.google.calls[:-1] if c[0] == "DELETE"]

    def test_a_blip_while_waiting_for_the_choice_does_not_throw_it_away(self):
        self.sign_in()
        self.google.polls_until_done = 2
        self.google.poll_failures = [503, 0, 503]  # Google busy, the connection dropped, busy again
        lines = []
        with mock.patch.object(photos, "log", lines.append):
            _, end = self.pick([media("A"), media("B")])
        self.assertEqual((end["state"], end["saved"], end["failed"]), ("done", 2, 0))
        self.assertEqual(sorted(p["id"] for p in photos.catalog()), ["A", "B"])
        self.assertEqual(self.google.deleted, ["/v1/sessions/S1"])
        self.assertEqual(self.deletes_before_the_end(), [])
        self.assertEqual(self.google.calls[-1][:2], ("DELETE", "/v1/sessions/S1"))
        # one line for the outage (three failed polls in a row), not one per poll
        self.assertEqual(sum("trying again" in line for line in lines), 1, lines)

    def test_google_refusing_the_wait_still_ends_the_pick_and_deletes_the_session(self):
        # control: a 403 is not a blip
        self.sign_in()
        self.google.polls_until_done = 2
        self.google.poll_failures = [403]
        _, end = self.pick([media("A")])
        self.assertEqual(end["state"], "error")
        self.assertIn("403", end["error"])
        self.assertEqual(self.google.deleted, ["/v1/sessions/S1"])
        self.assertEqual(photos.catalog(), [])
        self.assertEqual(sum(1 for c in self.google.calls if c[1] == "/v1/sessions/S1" and c[0] == "GET"), 1)

    def test_a_blip_reading_the_picked_items_is_tried_again(self):
        self.sign_in()
        self.google.page_failures = [503]
        _, end = self.pick([media("A"), media("B"), media("C")])
        self.assertEqual((end["state"], end["picked"], end["saved"], end["failed"]), ("done", 3, 3, 0))
        self.assertEqual(sorted(p["id"] for p in photos.catalog()), ["A", "B", "C"])
        self.assertEqual(self.deletes_before_the_end(), [])
        pages = [c[1] for c in self.google.calls if c[1].startswith("/v1/mediaItems?")]
        first_pages = [c for c in pages if "pageToken" not in c]
        self.assertEqual(len(first_pages), 2)  # the 503, then the answer

    def test_a_page_of_picked_items_still_refused_after_the_limit_ends_the_pick(self):
        self.sign_in()
        self.google.page_failures = [503] * 1000
        with mock.patch.object(photos, "PAGE_GIVE_UP_S", 0.05):
            _, end = self.pick([media("A")])
        self.assertEqual(end["state"], "error")
        self.assertEqual(photos.catalog(), [])
        self.assertEqual(self.google.deleted, ["/v1/sessions/S1"])

    def test_the_same_photo_saved_twice_at_once_is_catalogued_once(self):
        self.sign_in()
        self.google.media_delay = 0.2
        item = self.hosted(media("A"))
        both = [threading.Thread(target=self.picker._download, args=(item,)) for _ in range(2)]
        for t in both:
            t.start()
        for t in both:
            t.join(5)
        self.assertEqual([p["id"] for p in photos.catalog()], ["A"])

    # ---- a sign-in Google no longer accepts
    def expire_access(self):
        photos._access.update(token=None, until=0.0)

    def test_a_refused_sign_in_says_sign_in_again_and_stops_saying_signed_in(self):
        self.sign_in()
        self.expire_access()
        self.google.refresh_status = 400  # invalid_grant: a "Testing" project after 7 days, or revoked
        with self.assertRaises(photos.NotConnected) as e:
            self.picker.start()
        self.assertIn("invalid_grant", str(e.exception))
        self.assertIn("photos.py login", str(e.exception))
        self.assertIsNone(e.exception.__cause__)  # nothing of the token request rides along
        self.assertEqual(self.picker.status()["state"], "idle")
        s = photos.status()
        self.assertEqual((s["client"], s["signed_in"]), (True, False))
        self.assertFalse(photos.connected())
        # signing in again (photos.py login, another process) brings the page back
        self.google.refresh_status = 200
        (self.tmp / "google-token.json").write_text(json.dumps({"refresh_token": "RT2"}))
        self.assertTrue(photos.status()["signed_in"])

    def test_google_failing_for_a_moment_is_not_a_sign_out(self):
        self.sign_in()
        self.expire_access()
        self.google.refresh_status = 503
        with self.assertRaises(urllib.error.HTTPError):
            self.picker.start()
        self.assertTrue(photos.status()["signed_in"])

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
