"""Art Home server: alerts rules, the turn-off decision, weather cache, artworks and the HTTP surface.

Standard library only:  python3 -m unittest discover -s art-home/tests -t art-home/tests
No network: Frigate and Home Assistant are replaced by fakes; the HTTP tests bind 127.0.0.1 on a free port.
"""

import http.client
import io
import json
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from typing import ClassVar
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "server"))
import server


def frigate_answer(events):
    """A stand-in for urlopen(): returns `events` as Frigate's /api/events JSON."""

    def fake(url, timeout=None):
        assert "/api/events?" in url
        return io.BytesIO(json.dumps(events).encode())

    return fake


def event(eid, camera="front", label="person", score=0.9, start=1000.0, sub_label=None):
    return {
        "id": eid,
        "camera": camera,
        "label": label,
        "start_time": start,
        "data": {"top_score": score},
        "sub_label": sub_label,
    }


CFG = {
    "cameras": {"frigate_url": "http://frigate", "list": [{"frigate": "front"}, {"frigate": "yard"}]},
    "alerts": {"labels": ["person", "car"], "min_score": 0.7, "interval_s": 60},
}


class TempDirs(unittest.TestCase):
    """Points the server at an empty config, art folder and package folder for each test."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        (self.tmp / "art").mkdir()
        (self.tmp / "package").mkdir()
        patches = [
            mock.patch.object(server, "CONFIG", self.tmp / "config.json"),
            mock.patch.object(server, "ART", self.tmp / "art"),
            mock.patch.object(server, "APP", self.tmp / "package"),
            mock.patch.object(server, "log", lambda msg: None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def write_config(self, cfg):
        (self.tmp / "config.json").write_text(json.dumps(cfg))


class AlertsTest(unittest.TestCase):
    def setUp(self):
        self.alerts = server.Alerts()
        p = mock.patch.object(server, "log", lambda msg: None)
        p.start()
        self.addCleanup(p.stop)

    def step(self, events, cfg=CFG):
        with mock.patch.object(server.urllib.request, "urlopen", frigate_answer(events)):
            self.alerts.step(cfg)

    def test_opening_the_tv_gets_only_the_current_number_never_the_past(self):
        self.step([event("a")])
        self.assertEqual(self.alerts.since(-1), {"seq": 1, "alerts": []})
        self.assertEqual([a["id"] for a in self.alerts.since(0)["alerts"]], ["a"])
        self.assertEqual(self.alerts.since(1)["alerts"], [])

    def test_filters_camera_label_and_score(self):
        self.step(
            [
                event("other-cam", camera="indoor"),
                event("other-label", label="bird"),
                event("too-low", score=0.5),
                event("ok", camera="yard", label="car"),
            ]
        )
        self.assertEqual([a["id"] for a in self.alerts.since(0)["alerts"]], ["ok"])

    def test_alerts_cameras_overrides_the_camera_list(self):
        cfg = {**CFG, "alerts": {**CFG["alerts"], "cameras": ["yard"]}}
        self.step([event("front"), event("yard", camera="yard")], cfg)
        self.assertEqual([a["id"] for a in self.alerts.since(0)["alerts"]], ["yard"])

    def test_a_low_score_event_is_announced_once_its_score_rises(self):
        self.step([event("e", score=0.5)])
        self.assertEqual(self.alerts.seq, 0)
        self.step([event("e", score=0.8)])
        self.assertEqual(self.alerts.seq, 1)

    def test_the_same_event_is_announced_once(self):
        self.step([event("e")])
        self.step([event("e")])
        self.assertEqual(self.alerts.seq, 1)

    def test_one_card_per_camera_and_label_per_interval(self):
        self.step([event("a", start=1), event("b", start=2), event("c", label="car", start=3)])
        self.assertEqual([a["id"] for a in self.alerts.since(0)["alerts"]], ["a", "c"])
        self.alerts.last_per_cam[("front", "person")] -= 61  # a minute later
        self.step([event("d", start=4)])
        self.assertEqual(self.alerts.since(2)["alerts"][0]["id"], "d")

    def test_the_card_carries_photo_thumbnail_and_who(self):
        self.step([event("e", sub_label="Alice"), event("f", camera="yard", sub_label=["Bob", 0.9])])
        a, b = self.alerts.since(0)["alerts"]
        self.assertEqual(a["who"], "Alice")
        self.assertIsNone(b["who"])  # Frigate sends [name, score] while it is still unsure
        self.assertEqual(a["photo"], "http://frigate/api/events/e/snapshot.jpg?crop=1&h=400")
        self.assertEqual(a["thumbnail"], "http://frigate/api/events/e/thumbnail.jpg")

    def test_keeps_only_the_last_30(self):
        for i in range(40):
            self.alerts.last_per_cam.clear()
            self.step([event(f"e{i}", start=i)])
        items = self.alerts.since(0)["alerts"]
        self.assertEqual(len(items), 30)
        self.assertEqual(items[-1]["seq"], 40)

    def test_frigate_down_is_reported_once_and_cleared_when_back(self):
        logged = []

        def down(url, timeout=None):
            raise OSError("refused")

        with (
            mock.patch.object(server, "log", logged.append),
            mock.patch.object(server.urllib.request, "urlopen", down),
        ):
            self.alerts.step(CFG)
            self.alerts.step(CFG)
        self.assertEqual(self.alerts.error, "Frigate unreachable (OSError)")
        self.assertEqual(len(logged), 1)
        with mock.patch.object(server, "log", logged.append):
            self.step([])
        self.assertIsNone(self.alerts.error)
        self.assertEqual(logged[-1], "alerts: Frigate is back")

    def test_without_frigate_url_nothing_is_asked(self):
        with mock.patch.object(server.urllib.request, "urlopen", side_effect=AssertionError):
            self.alerts.step({"cameras": {}})


class NightWindowTest(unittest.TestCase):
    def test_window_inside_one_day_and_across_midnight(self):
        self.assertTrue(server.in_window("8-18", 8))
        self.assertFalse(server.in_window("8-18", 18))
        self.assertTrue(server.in_window("23-7", 23))
        self.assertTrue(server.in_window("23-7", 3))
        self.assertFalse(server.in_window("23-7", 7))
        self.assertFalse(server.in_window("23-7", 12))

    def test_a_broken_window_is_never_night(self):
        for win in ("", "23", "a-b", None, "1-2-3"):
            self.assertFalse(server.in_window(win, 0), win)


class TurnOffTest(TempDirs):
    ON: ClassVar = {
        "energy": {"turn_off": True, "night": "23-7"},
        "tv": {"ha_media_player": "media_player.tv"},
    }

    def setUp(self):
        super().setUp()
        p = mock.patch.object(server, "_last_off", {})
        p.start()
        self.addCleanup(p.stop)
        self.calls = []
        p = mock.patch.object(
            server, "ha_call", lambda cfg, method, path, body=None: self.calls.append((method, path, body))
        )
        p.start()
        self.addCleanup(p.stop)

    def at(self, hour):
        return mock.patch.object(
            server.time, "localtime", lambda *a: time.struct_time((2026, 1, 1, hour, 0, 0, 3, 1, 0))
        )

    def test_disabled_by_default(self):
        self.write_config({"tv": {"ha_media_player": "media_player.tv"}})
        with self.at(2):
            self.assertFalse(server.turn_off(200)["turned_off"])
        self.assertEqual(self.calls, [])

    def test_outside_the_night_window_it_refuses(self):
        self.write_config(self.ON)
        with self.at(22):
            r = server.turn_off(200)
        self.assertEqual(r["reason"], "outside the night window")
        self.assertEqual(self.calls, [])

    def test_dry_run_checks_everything_and_does_not_turn_off(self):
        self.write_config(self.ON)
        with self.at(2):
            r = server.turn_off(200, dry_run=True)
        self.assertEqual(r["reason"], "dry run: would turn off now")
        self.assertEqual(self.calls, [])
        self.assertEqual(server._last_off, {})  # a dry run doesn't use up the 10 min

    def test_turns_off_once_per_10_minutes(self):
        self.write_config(self.ON)
        with self.at(2):
            self.assertTrue(server.turn_off(200)["turned_off"])
            self.assertFalse(server.turn_off(201)["turned_off"])
        self.assertEqual(
            self.calls, [("POST", "/api/services/media_player/turn_off", {"entity_id": "media_player.tv"})]
        )

    def test_without_the_media_player_it_says_so(self):
        self.write_config({"energy": self.ON["energy"], "tv": {"name": "Living room"}})
        with self.at(2):
            self.assertEqual(server.turn_off(200)["reason"], "ha_media_player is not set for this TV")

    TWO: ClassVar = {
        "energy": {"turn_off": True, "night": "23-7"},
        "tvs": [
            {"ip": "10.0.0.5", "ha_media_player": "media_player.living"},
            {"ip": "10.0.0.4", "ha_media_player": "media_player.bedroom", "energy": {"turn_off": False}},
            {"ip": "10.0.0.6", "ha_media_player": "media_player.office"},
        ],
    }

    def off(self, entity):
        return ("POST", "/api/services/media_player/turn_off", {"entity_id": entity})

    def test_with_several_tvs_it_turns_off_the_one_that_asked(self):
        self.write_config(self.TWO)
        with self.at(2):
            self.assertTrue(server.turn_off(200, client_ip="10.0.0.6")["turned_off"])
            self.assertTrue(server.turn_off(200, client_ip="10.0.0.5")["turned_off"])  # its own 10 min
        self.assertEqual(self.calls, [self.off("media_player.office"), self.off("media_player.living")])

    def test_a_tv_can_switch_turning_off_for_itself(self):
        self.write_config(self.TWO)
        with self.at(2):
            r = server.turn_off(200, client_ip="10.0.0.4")
        self.assertEqual(r["reason"], "energy.turn_off is disabled")
        self.assertEqual(self.calls, [])

    def test_a_request_not_from_a_tv_never_turns_one_off(self):
        # a desktop preview left open at night asks too
        for cfg in (self.TWO, {**self.ON, "tv": {"ip": "10.0.0.5", "ha_media_player": "media_player.tv"}}):
            self.write_config(cfg)
            with self.at(2):
                r = server.turn_off(200, client_ip="10.0.0.99")
            self.assertEqual(r["reason"], "10.0.0.99 is not a TV in the config")
        self.assertEqual(self.calls, [])


class PerTvTest(unittest.TestCase):
    CFG: ClassVar = {
        "apps": ["shared"],
        "energy": {"turn_off": True},
        "cameras": {"list": [1]},
        "tvs": [{"ip": "10.0.0.4", "apps": ["bedroom"], "energy": {"turn_off": False}, "cameras": "no"}],
    }

    def test_a_tv_sees_its_own_apps_and_energy_and_never_other_keys(self):
        cfg = server.for_tv(self.CFG, "10.0.0.4")
        self.assertEqual((cfg["apps"], cfg["energy"]), (["bedroom"], {"turn_off": False}))
        self.assertEqual(cfg["cameras"], {"list": [1]})

    def test_anyone_else_sees_the_shared_config(self):
        self.assertEqual(server.for_tv(self.CFG, "10.0.0.9")["apps"], ["shared"])

    def test_a_single_tv_without_ip_answers_for_any_client(self):
        cfg = {"tv": {"ha_media_player": "m"}}
        self.assertEqual(server.tv_for(cfg, "1.2.3.4"), {"ha_media_player": "m"})
        self.assertIsNone(server.tv_for({}, "1.2.3.4"))
        self.assertIsNone(server.tv_for({"tvs": []}, "1.2.3.4"))


class WeatherTest(unittest.TestCase):
    CFG: ClassVar = {
        "locale": "pt-BR",
        "home_assistant": {"url": "http://ha", "weather_entity": "weather.home"},
    }

    def setUp(self):
        server._weather.update(at=0.0, data=None)
        p = mock.patch.object(server, "log", lambda msg: None)
        p.start()
        self.addCleanup(p.stop)

    def test_translated_and_cached_for_ten_minutes(self):
        answer = {"state": "rainy", "attributes": {"temperature": 18.4}}
        with mock.patch.object(server, "ha_call", return_value=answer) as call:
            self.assertEqual(
                server.weather(self.CFG), {"state": "rainy", "text": "Chuva", "temperature": 18.4}
            )
            server.weather(self.CFG)
        self.assertEqual(call.call_count, 1)

    def test_home_assistant_down_keeps_the_last_good_value(self):
        server._weather.update(at=0.0, data={"state": "sunny", "text": "Ensolarado", "temperature": 30})
        with mock.patch.object(server, "ha_call", side_effect=OSError):
            self.assertEqual(server.weather(self.CFG)["state"], "sunny")

    def test_unavailable_entity_keeps_the_last_good_value(self):
        with mock.patch.object(server, "ha_call", return_value={"state": "unavailable"}):
            self.assertIsNone(server.weather(self.CFG))

    def test_without_an_entity_there_is_no_weather(self):
        self.assertIsNone(server.weather({}))


class WorksTest(TempDirs):
    def test_only_catalogued_files_that_exist_with_public_fields(self):
        (self.tmp / "art" / "a.jpg").write_bytes(b"x")
        catalog = [{"id": 1, "file": "a.jpg", "title": "A", "secret": "no"}, {"id": 2, "file": "missing.jpg"}]
        (self.tmp / "art" / "catalog.json").write_text(json.dumps(catalog))
        (w,) = server.works()
        self.assertEqual(w["id"], 1)
        self.assertNotIn("secret", w)

    def test_no_catalog_means_no_works(self):
        self.assertEqual(server.works(), [])


class HttpTest(TempDirs):
    def setUp(self):
        super().setUp()
        (self.tmp / "art" / "a.jpg").write_bytes(b"jpeg")
        (self.tmp / "art" / "catalog.json").write_text(json.dumps([{"id": 1, "file": "a.jpg"}]))
        (self.tmp / "package" / "index.html").write_text("<html>")
        self.write_config({"demo": True, "energy": {"turn_off": False}, "apps": [{"name": "X", "id": "x"}]})
        self.httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def request(self, method, path):
        c = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        c.request(method, path)
        r = c.getresponse()
        body = r.read()
        c.close()
        return r, body

    def test_healthz_counts_works(self):
        r, body = self.request("GET", "/healthz")
        self.assertEqual(r.status, 200)
        self.assertEqual(json.loads(body)["works"], 1)

    def test_home_in_demo_mode(self):
        r, body = self.request("GET", "/api/home")
        d = json.loads(body)
        self.assertEqual(r.getheader("Cache-Control"), "no-store")
        self.assertEqual(len(d["works"]), 1)
        self.assertEqual(d["apps"], [{"name": "X", "id": "x"}])
        self.assertEqual(len(d["cameras"]["list"]), 5)
        self.assertTrue(all(c["snapshot_url"].startswith("/demo/") for c in d["cameras"]["list"]))
        self.assertEqual(d["weather"]["text"], "Partly cloudy")
        self.assertEqual((d["rotate_s"], d["frame_after_s"]), (90, 20))

    def test_alerts_with_a_bad_since_is_treated_as_opening(self):
        _, body = self.request("GET", "/api/alerts?since=abc")
        self.assertEqual(json.loads(body)["alerts"], [])

    def test_art_is_cached_and_cannot_leave_its_folder(self):
        r, body = self.request("GET", "/art/a.jpg")
        self.assertEqual((r.status, body), (200, b"jpeg"))
        self.assertIn("max-age", r.getheader("Cache-Control"))
        for path in (
            "/art/../config.json",
            "/art/%2e%2e/config.json",
            "/app/../config.json",
            "/art/nope.jpg",
        ):
            self.assertEqual(self.request("GET", path)[0].status, 404, path)

    def test_root_redirects_to_the_preview(self):
        r, _ = self.request("GET", "/")
        self.assertEqual((r.status, r.getheader("Location")), (302, "/app/index.html"))
        self.assertEqual(self.request("GET", "/app/index.html")[0].status, 200)

    def test_home_answers_with_the_asking_tvs_own_apps(self):
        self.write_config(
            {"demo": True, "apps": [{"id": "x"}], "tvs": [{"ip": "127.0.0.1", "apps": [{"id": "y"}]}]}
        )
        self.assertEqual(json.loads(self.request("GET", "/api/home")[1])["apps"], [{"id": "y"}])

    def test_turn_off_answers_the_decision(self):
        r, body = self.request("POST", "/api/turn-off?idle_min=130&dry_run=1")
        self.assertEqual((r.status, json.loads(body)["turned_off"]), (200, False))

    def test_turn_off_with_home_assistant_down_is_a_502(self):
        with mock.patch.object(server, "turn_off", side_effect=OSError):
            r, body = self.request("POST", "/api/turn-off?idle_min=130")
        self.assertEqual((r.status, json.loads(body)["reason"]), (502, "OSError"))

    def test_unknown_paths_are_404(self):
        self.assertEqual(self.request("GET", "/nope")[0].status, 404)
        self.assertEqual(self.request("POST", "/api/home")[0].status, 404)


if __name__ == "__main__":
    unittest.main()
