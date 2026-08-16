#!/usr/bin/env bash
# runtests_live.sh — run live integration tests against real Fluxer instances.
#
# Requires ReaperFiles-AutoTest/reaper_config.yaml with valid tokens.
# Default: runs only live-marked tests.  Pass any pytest args to override.
#
# Customise message counts via environment variables:
#   LIVE_COUNT_A=500   LIVE_COUNT_B=100   LIVE_COUNT_RESUME=50
#
# Usage:
#   ./runtests_live.sh                     # all live tests (default counts)
#   LIVE_COUNT_A=50 ./runtests_live.sh     # custom count
#   ./runtests_live.sh -k "resume"         # specific live test - add even more messages to a previous test.
#   ./runtests_live.sh -s --tb=long        # verbose

set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d venv ]; then
    echo "ERROR: venv not found"
    exit 1
fi

source venv/bin/activate

echo "=== Live Integration Tests ==="
echo "Config dir: $(ls -d ReaperFiles-AutoTest 2>/dev/null || echo 'NOT FOUND')"
echo "Counts:  A=${LIVE_COUNT_A:-300}  B=${LIVE_COUNT_B:-400}  RESUME=${LIVE_COUNT_RESUME:-200}"
echo ""

python -m pytest tests/ -m live -v "${@}"
