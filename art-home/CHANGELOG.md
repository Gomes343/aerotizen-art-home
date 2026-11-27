# Art Home changelog

AeroTizen keeps its own changelogs (`launcher/CHANGELOG.md`, `CHANGELOG-CLI.md`); this one covers only
Art Home (`art-home/`).

## Unreleased

## 0.3.0 — 2026-11-27

Your own photos on the TV, several TVs on one server, and a switch to choose what each one shows.

### Upgrading from 0.2.0

- **Rebuild and reinstall the TV package** (`./build.sh`, then install it as before): the shuffle, the
  photos and the mosaic live in the launcher. The server works with the old package, but without them.
- **Docker:** mount the whole `server/` folder (not only `server.py`) and a writable `data/` folder for
  the photos, the Google sign-in and the switch — see `docker-compose.example.yml`.
- **Several TVs:** move the single `tv` into a `tvs` list, each with its `ip` (the old `tv` still works).
  Give each TV a fixed address in the router: a TV is known by its address.
- Photos are off until you turn them on per TV and sign in once (`photos.py login`); see the README.

### New

- **Your own photos from Google Photos, between the artworks** (optional, per TV). Pick them on your phone
  at `/photos` (Google's Photos Picker API); the server downloads them once into its data folder and the
  TV alternates artwork and photo (`photos.every`), matted in the photo's own colour and captioned with
  its date. `photos.py login` signs in once (Desktop-app OAuth client, loopback, PKCE). Standard library
  only, like the rest of the server.
- **A Photos | Artworks switch per TV**, on the same phone page. Turning photos into the only thing a TV
  shows (or back to artworks only) meant editing `every` in the config. Now it is one tap; the choice lives
  in `data/display.json` and wins over the config (`mode=auto` gives the mix back). The TV follows on its
  next refresh, within 10 minutes; a TV switched to photos with none downloaded yet keeps the artworks.
- **Photos standing up share the screen** (`photos.mosaic`, on by default). On a TV lying down, a portrait
  photo alone left two thirds of the screen empty. Now a standing photo comes with two more: three standing
  side by side, or — half of the time, while lying photos remain — a lying one a little smaller between two
  standing ones. Each photo is whole (no cropping), each still shows once per lap, and the caption gives
  the dates from the oldest to the newest. A lying photo is shown alone, as before.
- **Several TVs on one server.** List them in `tvs` (each with its `ip`); a TV may set its own `apps`,
  `energy`, `art` and `photos`. A turn-off request now turns off **the TV that asked**, and only a TV
  listed in the config can ask: before, a second TV (or a desktop preview left open at night) would turn
  off the one configured TV.
- A turn-off request or an `/api/home` from an address that is not a TV in `tvs` is now logged, once per
  address: a TV whose address changed used to lose its own settings and stop being turned off at night
  without a trace. The decision itself is unchanged.

### Fixes

- **The same artwork could come back within half an hour.** The server shuffled the artworks on every
  `/api/home` and the TV, asking again every 10 minutes, swapped in the new order but kept its place in
  it. The server now sends them in catalogue order and the TV shuffles: a new order only when the set of
  artworks changes (compared by id, not by order) or a lap ends, so one lap shows every artwork once, and
  the one on screen is never shown twice in a row. Needs the new TV package.
- **A broken `catalog.json` took the whole `/api/home` down** (cameras pop-up, night turn-off, app row)
  and `/healthz` with it. An unreadable catalogue now means no artworks, logged once per new error and
  reported in `/healthz` as `art_error`; entries without a `file` are left out. `curate_art.py` writes the
  catalogue atomically and stops with a clear message if it finds it broken.
- **A restart in the middle of a pick lost it.** The pick lived only in memory: restarting the server
  while it downloaded (600 photos take a while) left the rest of the choice behind. The pick in progress
  is now kept in `data/pick.json` (mode 600) and resumed when the server starts: the photos already here
  are skipped, the rest arrive, and the session is deleted.
- **A few seconds without internet while you pick on your phone threw the choice away.** One failed poll
  (network down, timeout, a dropped connection, Google answering 5xx or 429) ended the pick and deleted the
  session at Google. The server now tries again while the choice lasts, and a page of picked items is
  tried again for up to 10 minutes; a refused sign-in and any other 4xx still end the pick. One log line
  per outage, not one per poll.
- **An expired or revoked Google sign-in** (`invalid_grant`, every 7 days on a *Testing* project) showed
  "HTTP Error 400: Bad Request" while the page still said signed in. It is now a 409 that says to sign in
  again, and the page shows the sign-in step until a new sign-in is saved. Google failing for a moment
  (5xx) is still a 502 and does not sign you out.
- **A double tap on "Choose photos" opened two choices at Google**, and half an hour later the page said
  "the choice took too long" although the photos had arrived. The job is now reserved before the server
  asks Google, a second tap gets that same job, and the button stays disabled while the request runs. Two
  downloads of the same photo at once no longer put it twice in the catalogue. The new switch has the same
  one-tap guard.

### Tests

- 93 server tests and 40 launcher and phone-page tests (`./test.sh`, no network and no TV), up from the
  0.2.0 suite: photos, picking and its recovery, several TVs, the switch, the mosaic and the shuffle.

## 0.2.0 — 2026-10-31

- **Up to date with AeroTizen launcher 2.5.0 and CLI 2.5.0.** The Home service and `homewatch.js` that
  ship in the package are unchanged upstream since 2.4.0; the new CLI checks the TV runtime before
  running the native Home helpers, so use ≥ 2.5.0 for `home apply/restore`.
- **Fix: no request storm when the server goes away.** With every artwork failing to load, the screen
  used to skip to the next one immediately, forever. Now it tries one lap and waits for the next
  rotation.
- **Fix:** in the desktop preview, the Samsung tile shows Art Home's own (translated) message instead of
  AeroTizen's English one.
- **Tests** (`./test.sh`, no network and no TV): the server's alert rules, turn-off decision, weather
  cache and HTTP surface; the launcher in a fake TV (navigation, frame mode, failing images, pop-ups,
  cameras, energy saving, the AeroTizen bridge); and the package build against the current AeroTizen
  tree, which fails if an upstream release moves or renames a file Art Home relies on.
- Python code follows the repository's `ruff` rules.

## 0.1.0 — 2026-10-27

First release: public-domain art library (Cleveland Museum of Art, CC0), matted artworks, frame mode,
cameras (Frigate grid + go2rtc live), detection pop-ups, energy saving, arrival mode, Home Assistant
weather, English and Brazilian Portuguese.
