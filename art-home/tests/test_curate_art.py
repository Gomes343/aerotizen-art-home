"""curate_art.py: only CC0 works enter the catalog, and the catalog is written atomically.

No network, `fetch` is replaced by a fake museum:
    python3 -m unittest discover -s art-home/tests -t art-home/tests
"""

import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "server"))

try:
    from PIL import Image

    import curate_art
except ImportError:  # Pillow is needed only to build the library, not to serve it
    curate_art = None


def artwork(aid, license_status):
    return {
        "id": aid,
        "title": f"Landscape {aid}",
        "creators": [{"description": "Some Painter (Dutch, 1600-1650)"}],
        "creation_date": "1630",
        "share_license_status": license_status,
        "images": {"print": {"url": f"https://img/{aid}.jpg", "width": 3000, "height": 2000}},
    }


@unittest.skipIf(curate_art is None, "Pillow not installed")
class CurateTest(unittest.TestCase):
    def setUp(self):
        self.art = pathlib.Path(tempfile.mkdtemp())
        buf = io.BytesIO()
        Image.new("RGB", (30, 20), (200, 100, 50)).save(buf, "JPEG")
        self.jpeg = buf.getvalue()
        museum = {"data": [artwork(1, "CC0"), artwork(2, "Copyrighted")]}

        def fetch(url, attempts=8, timeout=60):
            return json.dumps(museum).encode() if url.startswith(curate_art.API) else self.jpeg

        for p in (
            mock.patch.object(curate_art, "ART", self.art),
            mock.patch.object(curate_art, "CATALOG", self.art / "catalog.json"),
            mock.patch.object(curate_art, "SEARCHES", ["landscape"]),
            mock.patch.object(curate_art, "fetch", fetch),
            mock.patch("builtins.print", lambda *a, **k: None),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_only_cc0_enters_and_the_catalog_is_written_whole(self):
        curate_art.main(["--target", "5"])
        catalog = json.loads((self.art / "catalog.json").read_text())
        self.assertEqual([w["id"] for w in catalog], ["cma-1"])
        self.assertEqual((catalog[0]["license"], catalog[0]["file"]), ("CC0", "cma-1.jpg"))
        self.assertTrue((self.art / "cma-1.jpg").is_file())
        self.assertFalse((self.art / "cma-2.jpg").exists())
        left = sorted(p.name for p in self.art.iterdir())
        self.assertEqual(left, ["catalog.json", "cma-1.jpg"])  # no .tmp left behind

    def test_a_broken_catalog_stops_with_what_to_do(self):
        (self.art / "catalog.json").write_text('[{"id": "cma-1", "fi')
        with self.assertRaises(SystemExit) as e:
            curate_art.main(["--target", "5"])
        self.assertIn("is broken", str(e.exception))
        self.assertIn("fix or remove it", str(e.exception))
        self.assertEqual((self.art / "catalog.json").read_text(), '[{"id": "cma-1", "fi')  # untouched


if __name__ == "__main__":
    unittest.main()
