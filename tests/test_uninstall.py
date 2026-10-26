"""Uninstalling apps without a physical TV: the TV expects app IDs, not package IDs."""

import pytest

from aerotizen import cli, releases

LAUNCHER = f"app_id={releases.APP_ID} app_title=AeroTizen"
OTHER = "app_id=Abcdefghij.Player app_title=Player"


@pytest.fixture
def tv(monkeypatch):
    class TV:
        def __init__(self):
            self.installed = [LAUNCHER, OTHER]
            self.removed = []

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def close(self):
            pass

        def applist(self):
            return "\n".join(self.installed)

        def uninstall(self, app_id):
            self.removed.append(app_id)
            self.installed = [line for line in self.installed if f"app_id={app_id} " not in line]
            return f"app_id[{app_id}] uninstall completed"

    fake = TV()
    monkeypatch.setattr(cli, "require_sdb", lambda host: fake)
    monkeypatch.setattr(cli, "snapshot", lambda host, text: "snapshot.txt")
    return fake


def run(monkeypatch, *arguments):
    monkeypatch.setattr("sys.argv", ["aerotizen", "--tv", "tv", *arguments])
    cli.main()


def test_uninstall_launcher_uses_the_app_id(monkeypatch, tv, capsys):
    run(monkeypatch, "uninstall", "launcher", "--yes")
    assert tv.removed == [releases.APP_ID]
    assert "Uninstalled AeroTizen" in capsys.readouterr().out


def test_uninstall_asks_first_and_mentions_lost_settings(monkeypatch, tv):
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "n")
    run(monkeypatch, "uninstall", "launcher")
    assert tv.removed == []
    assert "settings" in prompts[0]


def test_uninstall_reports_missing_apps_and_refusals(monkeypatch, tv):
    with pytest.raises(SystemExit, match="not installed"):
        run(monkeypatch, "uninstall", "Missing.App", "--yes")
    tv.uninstall = lambda app_id: "was get app info failed"
    with pytest.raises(SystemExit, match=r"still installed\. TV said: was get app info failed"):
        run(monkeypatch, "uninstall", "Abcdefghij.Player", "--yes")


def test_uninstall_rejects_unsafe_ids(monkeypatch, tv):
    with pytest.raises(SystemExit, match="application ID"):
        run(monkeypatch, "uninstall", "bad;id", "--yes")


def test_debloat_removes_packages_by_app_id(monkeypatch, tv):
    run(monkeypatch, "debloat", "--remove", "Abcdefghij", "--yes")
    assert tv.removed == ["Abcdefghij.Player"]
