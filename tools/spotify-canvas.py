#!/usr/bin/env python3
"""
Fetch Spotify Canvas video URL for a track.

Usage:
    python3 tools/spotify-canvas.py <track-url-or-uri>
    SP_DC="..." python3 tools/spotify-canvas.py spotify:track:7eGuPhpdS8sBjPJNuAShUX

The SP_DC cookie can be set via:
  - SP_DC env var (for this command)
  - SPOTIFY_SP_DC env var (used by the service)
  - /etc/beosound5c/secrets.env (SPOTIFY_SP_DC=...)
"""

import asyncio
import os
import sys

# Add services/lib to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "lib"))

import logging
logging.basicConfig(level=logging.INFO, format="%(message)s")

from spotify_canvas import SpotifyCanvasClient


def load_sp_dc():
    """Load sp_dc from env or secrets.env."""
    sp_dc = os.environ.get("SP_DC") or os.environ.get("SPOTIFY_SP_DC")
    if sp_dc:
        return sp_dc
    # Try secrets.env
    for path in ["/etc/beosound5c/secrets.env",
                 os.path.join(os.path.dirname(__file__), "..", "config", "secrets.env")]:
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("SPOTIFY_SP_DC="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val:
                            return val
        except FileNotFoundError:
            pass
    return None


def parse_track_arg(arg):
    """Convert URL or URI to spotify:track:xxx format."""
    if arg.startswith("spotify:track:"):
        return arg
    if "open.spotify.com/track/" in arg:
        track_id = arg.split("/track/")[1].split("?")[0]
        return f"spotify:track:{track_id}"
    # Assume bare track ID
    if len(arg) == 22 and arg.isalnum():
        return f"spotify:track:{arg}"
    return arg


async def main():
    if len(sys.argv) < 2:
        print(__doc__.strip())
        sys.exit(1)

    track_uri = parse_track_arg(sys.argv[1])
    sp_dc = load_sp_dc()

    if not sp_dc:
        print("No sp_dc cookie found.")
        print("Set SP_DC env var or add SPOTIFY_SP_DC to /etc/beosound5c/secrets.env")
        print("\nTo get sp_dc: open.spotify.com → DevTools → Application → Cookies → sp_dc")
        sys.exit(1)

    print(f"Track: {track_uri}")
    print(f"sp_dc: {sp_dc[:20]}...")

    client = SpotifyCanvasClient(sp_dc=sp_dc)
    url = await client.get_canvas_url(track_uri)

    if url:
        print(f"\nCanvas URL: {url}")
    else:
        print("\nNo canvas available for this track.")


if __name__ == "__main__":
    asyncio.run(main())
