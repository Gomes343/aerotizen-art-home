"""Builds the artwork library: CC0 paintings from the Cleveland Museum of Art Open Access API.

Only works the museum marks CC0. Downloads the "print" image (~3400 px), resizes it to at most 2560 px
wide, measures its average colour (the mat colour on screen) and writes `art/catalog.json`.
Idempotent: works already downloaded are not downloaded again. Needs Pillow.

    python3 curate_art.py               # fill up to 60 works
    python3 curate_art.py --target 120
"""
import argparse
import io
import json
import os
import pathlib
import time
import urllib.parse
import urllib.request

from PIL import Image

HERE = pathlib.Path(__file__).resolve().parent
ART = pathlib.Path(os.environ.get("ART_HOME_ART", HERE / "art"))
CATALOG = ART / "catalog.json"
API = "https://openaccess-api.clevelandart.org/api/artworks/"
UA = {"User-Agent": "aerotizen-art-home (home TV art frame)"}

# For a living room: landscapes, sea, gardens, towns. No martyrdom, no battles.
SEARCHES = ["landscape", "seascape", "harbor", "garden", "river", "mountains", "sunset",
            "flowers", "village", "winter", "forest", "lake", "beach", "meadow"]
AVOID = ("crucifix", "martyr", "saint", "virgin", "christ", "madonna", "death", "battle",
         "massacre", "dead", "lamentation", "entombment", "judith", "holofernes", "skull")
MAX_WIDTH = 2560
RATIO = (1.25, 2.4)   # landscape only; the TV is 1.78


def fetch(url, attempts=8, timeout=60):
    """Flaky connections happen: retry with growing waits."""
    for i in range(attempts):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001 — any network failure is a new attempt
            if i == attempts - 1:
                raise
            print(f"  network: {e.__class__.__name__}, retrying ({i + 1})", flush=True)
            time.sleep(min(5 * (i + 1), 30))


def candidates(search, limit=100):
    q = urllib.parse.urlencode({"cc0": 1, "has_image": 1, "type": "Painting", "q": search, "limit": limit,
                                "fields": "id,title,creators,creation_date,images,share_license_status"})
    for a in json.loads(fetch(f"{API}?{q}")).get("data", []):
        if a.get("share_license_status") != "CC0":
            continue
        img = (a.get("images") or {}).get("print") or {}
        try:
            w, h = int(img["width"]), int(img["height"])
        except (KeyError, TypeError, ValueError):
            continue
        title = a.get("title") or ""
        if not (RATIO[0] <= w / h <= RATIO[1]) or w < 1800 or any(p in title.lower() for p in AVOID):
            continue
        creator = (a.get("creators") or [{}])[0].get("description") or "Unknown artist"
        yield {
            "id": f"cma-{a['id']}", "title": title, "artist": creator.split(" (")[0],
            "artist_full": creator, "date": a.get("creation_date") or "",
            "museum": "Cleveland Museum of Art", "license": "CC0", "source": img["url"],
            "page": f"https://clevelandart.org/art/collection/search?search={a['id']}",
        }


def process(work):
    dest = ART / f"{work['id']}.jpg"
    if not dest.exists():
        im = Image.open(io.BytesIO(fetch(work["source"], timeout=120))).convert("RGB")
        if im.width > MAX_WIDTH:
            im = im.resize((MAX_WIDTH, round(im.height * MAX_WIDTH / im.width)), Image.LANCZOS)
        tmp = dest.with_suffix(".tmp")
        im.save(tmp, "JPEG", quality=86, optimize=True, progressive=True)
        tmp.replace(dest)
    im = Image.open(dest)
    r, g, b = im.resize((1, 1), Image.BOX).getpixel((0, 0))
    work.update(file=dest.name, width=im.width, height=im.height, color=f"#{r:02x}{g:02x}{b:02x}")
    return work


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=60)
    target = ap.parse_args().target
    ART.mkdir(parents=True, exist_ok=True)
    catalog = json.loads(CATALOG.read_text()) if CATALOG.exists() else []
    seen = {w["id"] for w in catalog}
    per_search = max(3, target // len(SEARCHES) + 2)
    for search in SEARCHES:
        if len(catalog) >= target:
            break
        added = 0
        print(f"[{search}]", flush=True)
        for work in candidates(search):
            if work["id"] in seen or added >= per_search or len(catalog) >= target:
                continue
            try:
                catalog.append(process(work))
            except Exception as e:  # noqa: BLE001 — one bad work doesn't stop the run
                print(f"  skipped {work['id']}: {e}", flush=True)
                continue
            seen.add(work["id"])
            added += 1
            print(f"  + {work['title'][:60]} — {work['artist']}", flush=True)
            CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=1))
    print(f"catalog: {len(catalog)} works in {CATALOG}")


if __name__ == "__main__":
    main()
