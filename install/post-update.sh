#!/bin/bash
# Post-update migration — run as root after each OTA update.
# Handles system-level changes that the service user can't do:
#   - service files: refreshes /etc/systemd/system/beo-*.service from repo templates
#   - sudoers: writes the current NOPASSWD entries
#   - daemon-reload: picks up any changed service definitions
#   - pip packages: installs any new Python dependencies
#
# Idempotent — safe to run multiple times.
#
# Called automatically by the OTA updater (input.py) via:
#   sudo <base>/install/post-update.sh
# Can also be run manually after a git pull:
#   sudo ./install/post-update.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"

# SUDO_USER is set by sudo; fall back to the owner of the base directory
SERVICE_USER="${SUDO_USER:-$(stat -c '%U' "$BASE_DIR")}"
SERVICE_HOME=$(getent passwd "$SERVICE_USER" | cut -d: -f6)
SERVICE_UID=$(id -u "$SERVICE_USER")

log() { echo "[post-update] $*"; }

log "Starting (base=$BASE_DIR, user=$SERVICE_USER)"

# ── 1. Refresh installed systemd service files ───────────────────────────────
# OTA rsync only updates ~/beosound5c/services/system/ templates. This step
# re-stamps any already-installed service into /etc/systemd/system/ so that
# changes (e.g. port, capabilities, env vars) take effect on next restart.
TEMPLATE_DIR="$BASE_DIR/services/system"
SYSTEMD_DIR="/etc/systemd/system"
CHANGED=0

for template in "$TEMPLATE_DIR"/beo-*.service; do
    svc="$(basename "$template")"
    target="$SYSTEMD_DIR/$svc"
    [ -f "$target" ] || continue   # don't install new services — that's install.sh's job

    new=$(sed \
        -e "s|__USER__|$SERVICE_USER|g" \
        -e "s|__HOME__|$SERVICE_HOME|g" \
        -e "s|__UID__|$SERVICE_UID|g" \
        "$template")

    if [ "$(cat "$target")" != "$new" ]; then
        echo "$new" > "$target"
        log "Updated $svc"
        CHANGED=$((CHANGED + 1))
    fi
done

[ "$CHANGED" -gt 0 ] && log "$CHANGED service file(s) updated" || log "Service files unchanged"

# ── 2. Sudoers ────────────────────────────────────────────────────────────────
SUDOERS_FILE="/etc/sudoers.d/beosound5c"
POST_UPDATE_PATH="$BASE_DIR/install/post-update.sh"

cat > /tmp/beo-sudoers-new << EOF
# BeoSound 5c — UI kiosk and config management
$SERVICE_USER ALL=(ALL) NOPASSWD: /usr/bin/pkill, /usr/bin/fbi, /usr/bin/plymouth, /sbin/reboot, /usr/sbin/reboot
$SERVICE_USER ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/beosound5c/config.json
$SERVICE_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart beo-*
$SERVICE_USER ALL=(ALL) NOPASSWD: /bin/systemctl stop beo-*
$SERVICE_USER ALL=(ALL) NOPASSWD: /bin/systemctl start beo-*
$SERVICE_USER ALL=(ALL) NOPASSWD: $POST_UPDATE_PATH
EOF

visudo -c -f /tmp/beo-sudoers-new
cp /tmp/beo-sudoers-new "$SUDOERS_FILE"
chmod 440 "$SUDOERS_FILE"
rm /tmp/beo-sudoers-new
log "Sudoers updated"

# ── 3. daemon-reload (picks up any service file changes from step 1) ─────────
systemctl daemon-reload
log "daemon-reload done"

# ── 4. Python packages ───────────────────────────────────────────────────────
REQUIREMENTS="$BASE_DIR/install/requirements.txt"
if [ -f "$REQUIREMENTS" ]; then
    pip3 install -r "$REQUIREMENTS" -q --break-system-packages 2>/dev/null \
        || pip3 install -r "$REQUIREMENTS" -q
    log "pip packages up to date"
fi

log "Done"
