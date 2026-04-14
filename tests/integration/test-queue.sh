#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# Router-Owns-Queue Integration Tests
#
# Deploys code changes + test scripts to the device and runs them.
# Default target: office (local player).
#
# Usage:
#   ./tests/integration/test-queue.sh
#   HOST=beosound5c.local ./tests/integration/test-queue.sh
# ─────────────────────────────────────────────────────────────────

HOST="${HOST:-beosound5c.local}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "═══════════════════════════════════════════════════"
echo " Queue Tests — Device: $HOST"
echo "═══════════════════════════════════════════════════"

# Check connectivity
if ! ssh -o ConnectTimeout=3 "$HOST" "true" 2>/dev/null; then
    echo "ERROR: Cannot connect to $HOST"
    exit 2
fi

# Deploy latest code to target device only
echo ""
echo "── Deploying latest code ──"
cd "$PROJECT_DIR"
BEOSOUND5C_HOSTS="$HOST" ./deploy.sh beo-router beo-player-local beo-source-spotify beo-source-plex beo-source-radio beo-source-usb
echo ""

# Wait for services to restart
echo "Waiting for services to restart..."
sleep 5

# Copy test files
echo "Copying test files..."
scp -o ConnectTimeout=5 -q \
    "$SCRIPT_DIR/test-queue-helpers.py" "$HOST:/tmp/qhelpers.py"
scp -o ConnectTimeout=5 -q \
    "$SCRIPT_DIR/test-queue.py" "$HOST:/tmp/test-queue.py"

echo ""

# Run tests
ssh -o ConnectTimeout=5 "$HOST" "python3 /tmp/test-queue.py"
exit $?
