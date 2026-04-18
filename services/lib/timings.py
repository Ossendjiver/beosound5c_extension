# SPDX-License-Identifier: GPL-3.0-or-later

# Seconds since the last BS5c command before a player event is attributed to
# external control (e.g. Sonos/BluOS app, Spotify Connect, auto-advance).
# Used consistently across all player monitors and the router's external-start
# detection — change here to tune system-wide.
USER_ACTION_HORIZON: float = 3.0
