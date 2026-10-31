"""build.sh against the current AeroTizen tree: the guard for merging upstream updates.

The package takes three things from AeroTizen unchanged (service/, js/homewatch.js, config.xml). If an
upstream release moves, renames or re-identifies any of them, these tests fail before a TV gets a broken
package. Builds in a temporary copy, so the real dist/ (which a server may be serving) is never touched.
"""

import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest
import zipfile

ART_HOME = pathlib.Path(__file__).resolve().parents[1]
REPO = ART_HOME.parent


class BuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp())
        shutil.copytree(ART_HOME, cls.tmp / "art-home", ignore=shutil.ignore_patterns("dist", "tests", "art"))
        (cls.tmp / "launcher").symlink_to(REPO / "launcher")
        shutil.copy(REPO / "LICENSE", cls.tmp / "LICENSE")
        config = json.loads((ART_HOME / "config.example.json").read_text())
        config["locale"] = "pt-BR"
        (cls.tmp / "art-home" / "config.json").write_text(json.dumps(config))
        cls.result = subprocess.run(
            ["bash", "build.sh", "config.json"],
            cwd=cls.tmp / "art-home",
            capture_output=True,
            text=True,
            timeout=60,
        )
        cls.package = cls.tmp / "art-home" / "dist" / "package"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_builds(self):
        self.assertEqual(self.result.returncode, 0, self.result.stdout + self.result.stderr)

    def test_every_script_the_page_loads_is_in_the_package(self):
        html = (self.package / "index.html").read_text()
        scripts = re.findall(r'<script src="([^"]+)"', html)
        self.assertIn("js/homewatch.js", scripts)
        for src in scripts:
            if src.startswith("$WEBAPIS"):  # provided by the TV firmware
                continue
            self.assertTrue((self.package / src).is_file(), src)

    def test_keeps_aerotizens_identity_and_service(self):
        xml = (self.package / "config.xml").read_text()
        self.assertIn('<tizen:application id="DclHome001.Home"', xml)  # the native Home helpers look for it
        self.assertIn('<tizen:service id="DclHome001.Watcher"', xml)
        self.assertIn("<name>Art Home</name>", xml)
        self.assertTrue((self.package / re.search(r'<tizen:content src="([^"]+)"', xml)[1]).is_file())
        self.assertTrue((self.package / "LICENSE-AeroTizen.txt").is_file())

    def test_the_bridge_matches_the_service_id(self):
        bridge = (self.package / "aero-bridge.js").read_text()
        self.assertIn("SERVICE_ID: 'DclHome001.Watcher'", bridge)

    def test_settings_carry_only_the_server_and_the_language(self):
        js = (self.package / "settings.js").read_text()
        settings = json.loads(js.removeprefix("window.ART_HOME_SETTINGS = ").rstrip().removesuffix(";"))
        self.assertEqual(settings, {"server": "http://192.168.1.10:8895", "locale": "pt-BR"})

    def test_the_wgt_holds_the_same_files(self):
        with zipfile.ZipFile(self.package.parent / "ArtHome.wgt") as z:
            names = set(z.namelist())
        on_disk = {p.relative_to(self.package).as_posix() for p in self.package.rglob("*") if p.is_file()}
        self.assertEqual(names, on_disk)


if __name__ == "__main__":
    unittest.main()
