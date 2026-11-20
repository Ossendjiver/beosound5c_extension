"""Kodi service: poll SmartTube history and forward confirmed updates to BS5c."""
import os
import sys

import xbmc
import xbmcaddon
import xbmcvfs

ADDON = xbmcaddon.Addon()
SMARTTUBE = "plugin.video.smarttube"
ROOT = xbmcvfs.translatePath(ADDON.getAddonInfo("path"))
sys.path.insert(0, os.path.join(ROOT, "resources", "lib"))
from sync import sync_once  # noqa: E402


def run():
    monitor = xbmc.Monitor()
    own_profile = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
    smarttube_history = xbmcvfs.translatePath("special://profile/addon_data/" + SMARTTUBE + "/history.json")
    ack_path = os.path.join(own_profile, "synced.json")
    while not monitor.abortRequested():
        if ADDON.getSettingBool("enabled"):
            try:
                number = sync_once(
                    smarttube_history, ack_path,
                    ADDON.getSettingString("device_id").strip(),
                    ADDON.getSettingString("host").strip(),
                    ADDON.getSettingString("history_key"),
                )
                if number:
                    xbmc.log("[BS5c YouTube] Forwarded %d SmartTube records" % number, xbmc.LOGINFO)
            except Exception as exc:
                # Never emit the key, request headers, or video metadata to Kodi logs.
                xbmc.log("[BS5c YouTube] Sync unavailable (%s); will retry" % type(exc).__name__, xbmc.LOGWARNING)
        if monitor.waitForAbort(30):
            break


if __name__ == "__main__":
    run()
