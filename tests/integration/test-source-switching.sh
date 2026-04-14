#!/bin/bash
# ─────────────────────────────────────────────────────────────────────
# Source Switching Integration Test (wrapper)
#
# Copies the test script to the target device and runs it.
# Tests rapid source switching, metadata correctness, and audio backend.
#
# Usage:
#   ./tests/integration/test-source-switching.sh
#   HOST=beosound5c.local ./tests/integration/test-source-switching.sh
#   ./tests/integration/test-source-switching.sh --volume 15
#   ./tests/integration/test-source-switching.sh --json
#
# Prerequisites:
#   - beo-router + beo-player-* running on device
#   - At least 2 sources registered and available
#   - For Spotify: valid credentials (auto-skipped if needs_reauth)
# ─────────────────────────────────────────────────────────────────────

HOST="${HOST:-beosound5c.local}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REMOTE_SCRIPT="/tmp/test-source-switching.py"

echo "═══════════════════════════════════════════════════"
echo " Device: $HOST"
echo "═══════════════════════════════════════════════════"

# Check connectivity
if ! ssh -o ConnectTimeout=3 "$HOST" "true" 2>/dev/null; then
    echo "ERROR: Cannot connect to $HOST"
    exit 2
fi

# Copy and run
scp -o ConnectTimeout=5 -q "$SCRIPT_DIR/test-source-switching.py" "$HOST:$REMOTE_SCRIPT"
ssh -o ConnectTimeout=5 "$HOST" "python3 $REMOTE_SCRIPT $*"
exit $?
