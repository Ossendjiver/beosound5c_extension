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
from sync import sync_once, fetch_history, merge_remote_history, send_event  # noqa: E402
from collections_sync import sync_all  # noqa: E402


def run():
    monitor = xbmc.Monitor()
    own_profile = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
    smarttube_history = xbmcvfs.translatePath("special://profile/addon_data/" + SMARTTUBE + "/history.json")
    ack_path = os.path.join(own_profile, "synced.json")
    last_direct_sample = ("", -1)
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
                # SmartTube's external /play/?video_id= route leaves SmartTube.Item
                # empty. Kodi playback is still observable via VideoId/StreamUrl,
                # so report those sessions directly instead of losing history.
                if active and not home.getProperty("SmartTube.Item"):
                    try:
                        position_ms = max(0, int(player.getTime() * 1000))
                        duration_ms = max(0, int(player.getTotalTime() * 1000))
                        state = (active, position_ms // 1000)
                        if state != last_direct_sample:
                            send_event(host, key, {
                                "video_id": active, "device_id": device,
                                "title": xbmc.getInfoLabel("VideoPlayer.Title")[:300],
                                "channel": xbmc.getInfoLabel("VideoPlayer.Artist")[:150],
                                "position_ms": min(position_ms, duration_ms) if duration_ms else position_ms,
                                "duration_ms": duration_ms,
                                "watched_at_ms": int(__import__("time").time() * 1000),
                                "ended": bool(duration_ms and position_ms >= duration_ms * .9),
                            })
                            last_direct_sample = state
                    except (RuntimeError, ValueError, TypeError):
                        pass
                else:
                    last_direct_sample = ("", -1)
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
