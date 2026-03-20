"""
Spotify token management — the ONE place for token refresh.

Two interfaces:
  - get_access_token()  — sync, for scripts (fetch.py)
  - SpotifyAuth         — async, for the long-running service (service.py)

Both use pkce.refresh_access_token() and tokens.load/save_tokens() internally.
"""

import asyncio
import json
import logging
import os
import sys
import time
import urllib.error

# Sibling imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pkce import refresh_access_token
from tokens import load_tokens, save_tokens, delete_tokens

log = logging.getLogger('beo-source-spotify')


def get_access_token(config_client_id=None):
    """Get a Spotify access token (sync). For standalone fetch.py runs.

    Uses the PKCE token store. Handles refresh token rotation automatically.
    The service passes --access-token instead; this exists for manual/cron use.
    """
    tokens = load_tokens()
    if not tokens or not tokens.get('client_id') or not tokens.get('refresh_token'):
        raise ValueError(
            "No Spotify credentials found. Use the /setup page to connect."
        )

    client_id = tokens['client_id']
    if config_client_id and client_id != config_client_id:
        delete_tokens()
        raise ValueError(
            f"Spotify client_id changed ({client_id[:8]}... → {config_client_id[:8]}...). "
            "Stale token cleared — re-authenticate via /setup."
        )
    refresh_token = tokens['refresh_token']

    result = refresh_access_token(client_id, refresh_token)

    # Persist rotated refresh token if provided
    new_rt = result.get('refresh_token')
    if new_rt and new_rt != refresh_token:
        save_tokens(client_id, new_rt)

    return result['access_token']


class SpotifyAuth:
    """Manages Spotify access tokens with automatic refresh (async).

    For use by the long-running Spotify service. Adds in-memory caching
    and revocation detection on top of the shared pkce/tokens modules.
    """

    def __init__(self):
        self._access_token = None
        self._token_expiry = 0
        self._client_id = None
        self._refresh_token = None
        self.revoked = False
        self._refresh_lock = asyncio.Lock()

    def load(self, config_client_id=None):
        """Load credentials from token store. Returns True if valid credentials found.

        If config_client_id is provided and differs from the stored client_id,
        the stale token is cleared — the user switched Spotify apps.
        """
        tokens = load_tokens()
        if tokens and tokens.get('client_id') and tokens.get('refresh_token'):
            stored_id = tokens['client_id']
            if config_client_id and stored_id != config_client_id:
                log.warning("Spotify client_id changed (%s... → %s...) — "
                            "clearing stale token, re-auth required",
                            stored_id[:8], config_client_id[:8])
                delete_tokens()
                return False
            self._client_id = stored_id
            self._refresh_token = tokens['refresh_token']
            log.info("Spotify credentials loaded (client_id: %s...)", self._client_id[:8])
            return True
        if tokens is not None:
            log.info("Token file exists but incomplete — waiting for setup")
        else:
            log.warning("No Spotify tokens found — use the /setup page to connect")
        return False

    def set_credentials(self, client_id, refresh_token, access_token=None, expires_in=3600):
        """Set credentials directly (used after OAuth callback)."""
        self._client_id = client_id
        self._refresh_token = refresh_token
        self._access_token = access_token
        self._token_expiry = time.monotonic() + expires_in - 300 if access_token else 0
        self.revoked = False

    def clear(self):
        """Clear all credentials (used on logout)."""
        self._client_id = None
        self._refresh_token = None
        self._access_token = None
        self._token_expiry = 0

    async def get_token(self):
        """Get a valid access token, refreshing if needed."""
        if self._access_token and time.monotonic() < self._token_expiry:
            return self._access_token
        return await self._refresh()

    async def _refresh(self):
        """Refresh the access token via PKCE.

        Uses a lock to prevent concurrent refreshes — PKCE token rotation
        invalidates the old refresh token, so a second in-flight refresh
        with the stale token would get 400 invalid_grant.
        """
        async with self._refresh_lock:
            # Re-check after acquiring lock — another task may have refreshed
            if self._access_token and time.monotonic() < self._token_expiry:
                return self._access_token

            if not self._client_id or not self._refresh_token:
                raise RuntimeError("No Spotify credentials")

            loop = asyncio.get_running_loop()

            try:
                result = await loop.run_in_executor(
                    None, refresh_access_token, self._client_id, self._refresh_token)
            except urllib.error.HTTPError as e:
                if e.code == 400:
                    self._mark_revoked(e)
                raise

            # Persist rotated refresh token to disk FIRST — if the process is
            # killed before we finish, the disk still has the valid token.
            new_rt = result.get('refresh_token')
            if new_rt and new_rt != self._refresh_token:
                await loop.run_in_executor(
                    None, save_tokens, self._client_id, new_rt)
                self._refresh_token = new_rt
                log.info("Refresh token rotated and saved")

            self._access_token = result['access_token']
            self._token_expiry = time.monotonic() + result.get('expires_in', 3600) - 300

            if self.revoked:
                self.revoked = False
                log.info("Token revocation cleared — refresh succeeded")
            log.info("Access token refreshed (expires in %ds)", result.get('expires_in', 0))
            return self._access_token

    def _mark_revoked(self, exc):
        """Flag that the refresh token has been revoked by Spotify."""
        try:
            body = json.loads(exc.read().decode())
            error = body.get('error', '')
        except Exception:
            error = ''
        if error == 'invalid_grant':
            self.revoked = True
            log.error("Spotify refresh token revoked — re-authentication required")
        else:
            log.warning("Token refresh failed (400): %s", error)

    async def start_keepalive(self, interval=2700):
        """Proactively refresh token every `interval` seconds (default 45min)
        to prevent Spotify's PKCE refresh token from expiring."""
        self.stop_keepalive()
        self._keepalive_task = asyncio.create_task(self._keepalive_loop(interval))

    async def _keepalive_loop(self, interval):
        try:
            while True:
                await asyncio.sleep(interval)
                if self.revoked or not self.is_configured:
                    break
                try:
                    await self.get_token()
                    log.info("Keepalive: token refreshed")
                except Exception as e:
                    log.warning("Keepalive refresh failed: %s", e)
        except asyncio.CancelledError:
            return

    def stop_keepalive(self):
        task = getattr(self, '_keepalive_task', None)
        if task:
            task.cancel()
            self._keepalive_task = None

    @property
    def is_configured(self):
        return bool(self._client_id and self._refresh_token)
