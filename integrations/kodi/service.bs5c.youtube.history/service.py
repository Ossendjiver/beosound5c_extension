"""Kodi service: poll SmartTube history and forward confirmed updates to BS5c."""
import os
import sys

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON = xbmcaddon.Addon()
SMARTTUBE = "plugin.video.smarttube"
ROOT = xbmcvfs.translatePath(ADDON.getAddonInfo("path"))
sys.path.insert(0, os.path.join(ROOT, "resources", "lib"))
from sync import sync_once, fetch_history, merge_remote_history  # noqa: E402
from collections_sync import sync_all  # noqa: E402


def run():
    monitor = xbmc.Monitor()
    own_profile = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
    smarttube_history = xbmcvfs.translatePath("special://profile/addon_data/" + SMARTTUBE + "/history.json")
    ack_path = os.path.join(own_profile, "synced.json")
    smarttube_profile = os.path.dirname(smarttube_history)
    while not monitor.abortRequested():
        if ADDON.getSettingBool("enabled"):
            try:
                device = ADDON.getSettingString("device_id").strip()
                host = ADDON.getSettingString("host").strip()
                key = ADDON.getSettingString("history_key")
                number = sync_once(smarttube_history, ack_path, device, host, key)
                home = xbmcgui.Window(10000)
                player = xbmc.Player()
                active = (home.getProperty("SmartTube.VideoId")
                          if player.isPlayingVideo() and
                          player.getPlayingFile() == home.getProperty("SmartTube.StreamUrl") else "")
                imported = merge_remote_history(smarttube_history, ack_path,
                                                fetch_history(host, key), active_id=active)
                collections = sync_all(smarttube_profile, own_profile, device, host, key)
                if number or imported or any(v["changes"] for v in collections.values()):
                    xbmc.log("[BS5c YouTube] Synced history and collections", xbmc.LOGINFO)
                if any(v["conflicts"] for v in collections.values()):
                    xbmc.log("[BS5c YouTube] Conflicting collection edits retained locally", xbmc.LOGWARNING)
            except Exception as exc:
                # Never emit the key, request headers, or video metadata to Kodi logs.
                xbmc.log("[BS5c YouTube] Sync unavailable (%s); will retry" % type(exc).__name__, xbmc.LOGWARNING)
        if monitor.waitForAbort(30):
            break


if __name__ == "__main__":
    run()
