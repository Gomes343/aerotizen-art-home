"""Restart a TV app with its web inspector for ``aerotizen inspect``."""

import time
import urllib.request

from aerotizen.sdb import Sdb, SdbError

ATTEMPTS = 4


def stop(host, app_id):
    try:
        with urllib.request.urlopen(
            urllib.request.Request(f"http://{host}:8001/api/v2/applications/{app_id}", method="DELETE"),
            timeout=5,
        ):
            pass
    except OSError:
        pass


def run(host, app_id, local_port=9222):
    # Debug launch only works when the app isn't running, and the launcher's Home watcher
    # reopens it within a second of closing. So connect first, then stop and launch back to
    # back, and try again if the watcher won the race.
    with Sdb(host, timeout=60).connect() as connection:
        for attempt in range(ATTEMPTS):
            stop(host, app_id)
            try:
                port = connection.debug_launch(app_id)
                break
            except SdbError:
                if attempt == ATTEMPTS - 1:
                    raise
                time.sleep(0.5)
        print(f"inspector: TV port {port} -> http://localhost:{local_port}/json", flush=True)
        connection.forward(local_port, port)
