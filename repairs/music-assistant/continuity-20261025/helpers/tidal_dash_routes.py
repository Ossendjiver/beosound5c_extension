"""Keep local DASH routes valid longer than MA's cached stream details."""
from __future__ import annotations
import hashlib
import uuid
from aiohttp import web

DASH_DETAILS_TTL = 120
IDLE_BUFFER = 300

class DashManifestRoutes:
    def __init__(self, mass):
        self.mass = mass
        self.namespace = uuid.uuid4().hex
        self.leases = {}

    def register(self, manifest: bytes, duration: float | None) -> str:
        if not manifest or len(manifest) > 262144:
            raise ValueError('Invalid or oversized Tidal DASH manifest')
        digest = hashlib.sha256(self.namespace.encode() + manifest).hexdigest()
        path = '/tidal-dash/' + digest
        # Cached details expire before an unused route can disappear. Serving
        # the manifest extends the lease through playback and subsequent seeks.
        ttl = max(DASH_DETAILS_TTL + IDLE_BUFFER, float(duration or 0) + IDLE_BUFFER)
        if path not in self.leases:
            if len(self.leases) >= 1024:
                raise RuntimeError('Tidal DASH route capacity reached; active routes preserved')
            async def serve(_request):
                renew()
                return web.Response(body=manifest, content_type='application/dash+xml',
                                    headers={'Cache-Control': 'no-cache'})
            self.mass.streams.register_dynamic_route(path, serve, method='GET')
        def renew():
            token = object()
            self.leases[path] = token
            self.mass.call_later(ttl, self._expire, path, token,
                                 task_id='tidal-dash-cleanup-' + digest)
        renew()
        return self.mass.streams.base_url + path

    def _expire(self, path, token):
        # A delayed callback from an earlier lease cannot remove its renewal.
        if self.leases.get(path) is not token:
            return
        try:
            self.mass.streams.unregister_dynamic_route(path, method='GET')
        except RuntimeError:
            pass  # controller shutdown may already have removed the route
        self.leases.pop(path, None)
