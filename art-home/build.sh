#!/usr/bin/env bash
# Builds the Art Home TV package: our screen + AeroTizen's service and homewatch client, unchanged.
#
#   ./build.sh [path/to/config.json]      → dist/package/ (folder) and dist/ArtHome.wgt
#
# The package keeps AeroTizen's identity (DclHome001 / DclHome001.Home / DclHome001.Watcher) on
# purpose: the native helpers that assign the Home button (shipped prebuilt and checksum-verified in
# the AeroTizen CLI) look for exactly that id. Renaming would mean rebuilding them (.NET 6).
# Install with `aerotizen install dist/package` (signs + installs), or any Tizen signing tool.
set -euo pipefail
cd "$(dirname "$0")"

CONFIG=${1:-${ART_HOME_CONFIG:-config.json}}
[ -f "$CONFIG" ] || { echo "config not found: $CONFIG (copy config.example.json)"; exit 1; }
UP=../launcher          # AeroTizen's launcher (upstream)
OUT=dist/package

# empty instead of delete: a server container may bind-mount this folder for previews
mkdir -p "$OUT" && find "$OUT" -mindepth 1 -delete && mkdir -p "$OUT/js"
cp -r "$UP/service" "$OUT/service"
cp "$UP/js/homewatch.js" "$OUT/js/homewatch.js"
cp launcher/* "$OUT/"
cp ../LICENSE "$OUT/LICENSE-AeroTizen.txt"
sed -e 's#<name>AeroTizen</name>#<name>Art Home</name>#' "$UP/config.xml" > "$OUT/config.xml"
grep -q 'id="DclHome001.Home"' "$OUT/config.xml" || { echo "!! upstream config.xml changed the app id"; exit 1; }

# the only per-house values the TV needs: where the server is, and the language
python3 - "$CONFIG" "$OUT/settings.js" <<'EOF'
import json, sys
c = json.load(open(sys.argv[1]))
s = {"server": c.get("server", {}).get("url", ""), "locale": c.get("locale", "en")}
open(sys.argv[2], "w").write("window.ART_HOME_SETTINGS = " + json.dumps(s) + ";\n")
EOF

rm -f dist/ArtHome.wgt
(cd "$OUT" && python3 -c "
import os, zipfile
with zipfile.ZipFile('../ArtHome.wgt', 'w', zipfile.ZIP_DEFLATED) as z:
    for root, _, files in os.walk('.'):
        for f in sorted(files):
            p = os.path.join(root, f)[2:]
            z.write(p, p)
")
echo "built: $OUT/ and dist/ArtHome.wgt ($(du -h dist/ArtHome.wgt | cut -f1))"
