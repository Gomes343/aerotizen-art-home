#!/usr/bin/env bash
# Art Home tests: the server (Python stdlib), the launcher in a fake TV (node --test) and the package
# build against the current AeroTizen tree. No network, no TV.   ./test.sh
set -euo pipefail
cd "$(dirname "$0")"
python3 -m unittest discover -s tests -t tests
node --test tests/*.test.cjs
