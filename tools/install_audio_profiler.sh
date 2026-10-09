#!/bin/bash
# Install only the optional profiling timer; the runtime/models live on SSD.
set -euo pipefail
if [[ $EUID -ne 0 ]]; then
    echo 'Run with sudo after copying the profile and preparing the SSD runtime.' >&2
    exit 1
fi
PROFILE_USER="${SUDO_USER:?Use sudo from the BS5c account}"
PROFILE_HOME="$(getent passwd "$PROFILE_USER" | cut -d: -f6)"
PROFILE_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ "$PROFILE_REPO" == "$PROFILE_HOME/beosound5c" ]] || { echo 'Install from the BS5c checkout.' >&2; exit 1; }
mountpoint -q /media/local || { echo '/media/local must be mounted on SSD storage.' >&2; exit 1; }
for file in /media/local/cache/audio-analysis/venv/bin/python /media/local/cache/audio-analysis/models/msd-musicnn-1.onnx /media/local/cache/audio-analysis/models/emomusic-msd-musicnn-2.onnx; do
    [[ -f "$file" ]] || { echo "Missing runtime/model: $file" >&2; exit 1; }
done
for unit in beo-audio-profile.service beo-audio-profile.timer; do
    sed -e "s|__USER__|$PROFILE_USER|g" -e "s|__HOME__|$PROFILE_HOME|g" \
        "$PROFILE_REPO/services/system/$unit" > "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable --now beo-audio-profile.timer
systemctl list-timers beo-audio-profile.timer --no-pager
