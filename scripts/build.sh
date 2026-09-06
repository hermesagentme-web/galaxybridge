#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python3 -m compileall -q "$project_root/galaxybridge"
python3 -m pytest "$project_root/tests"
test "$(cd "$project_root" && python3 -m galaxybridge.cli buds frame)" = "FC 0B 00 01 43 04 03 04 00 00 0B 02 1E AF CC"
echo "GalaxyBridge build and tests passed."
