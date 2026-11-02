# Art Home changelog

AeroTizen keeps its own changelogs (`launcher/CHANGELOG.md`, `CHANGELOG-CLI.md`); this one covers only
Art Home (`art-home/`).

## Unreleased

- **Your own photos from Google Photos, between the artworks** (optional, per TV). Pick them on your phone
  at `/photos` (Google's Photos Picker API); the server downloads them once into its data folder and the
  TV alternates artwork and photo (`photos.every`), matted in the photo's own colour and captioned with
  its date. `photos.py login` signs in once (Desktop-app OAuth client, loopback, PKCE). Standard library
  only, like the rest of the server.
- The Docker example now mounts the whole `server/` folder, plus a writable `data/` for the photos.
- **Fix: a double tap on "Choose photos" opened two choices at Google**, and half an hour later the page
  said "the choice took too long" although the photos had arrived. The job is now reserved before the
  server asks Google, a second tap gets that same job, the button stays disabled while the request runs,
  and a thread of a replaced pick no longer writes over the page. Two downloads of the same photo at once
  no longer put it twice in the catalogue.
- **Fix: an expired or revoked Google sign-in** (`invalid_grant`, every 7 days on a *Testing* project)
  showed "HTTP Error 400: Bad Request" while the page still said signed in. It is now a 409 that says to
  sign in again, and the page shows the sign-in step until a new sign-in is saved. Google failing for a
  moment (5xx) is still a 502 and does not sign you out.
- A turn-off request or an `/api/home` from an address that is not a TV in `tvs` is now logged, once per
  address: a TV whose address changed (no reservation in the router) used to lose its own settings and
  stop being turned off at night without a trace. The decision itself is unchanged.
- **Several TVs on one server.** List them in `tvs` (each with its `ip`); a TV may set its own `apps`,
  `energy` and `art`. A turn-off request now turns off **the TV that asked**, and only a TV listed in
  the config can ask: before, a second TV (or a desktop preview left open at night) would turn off the
  one configured TV. The single `tv` of older configs still works.

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
