"""Persistent provider cooldowns; never bypass a server Retry-After."""
import json
import random
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from .ipod_sync import atomic_json

class Cooldown(Exception):
    def __init__(self, retry_at):
        self.retry_at = retry_at
        super().__init__('Tidal cooldown')

class RateLimit:
    def __init__(self, path, spacing=2.0):
        self.path = Path(path)
        self.spacing = spacing
        self.last_request = None

    def state(self):
        try:
            value = json.loads(self.path.read_text())
            return value if isinstance(value, dict) else {}
        except FileNotFoundError:
            return {}

    def ready(self):
        until = float(self.state().get('retry_at', 0))
        if until > time.time():
            raise Cooldown(until)

    def before_request(self):
        self.ready()
        if self.last_request is not None:
            delay = self.spacing - (time.monotonic() - self.last_request)
            if delay > 0:
                time.sleep(delay)
        self.ready()
        self.last_request = time.monotonic()

    def limited(self, headers):
        now = time.time()
        state = self.state()
        attempts = min(int(state.get('attempts', 0)) + 1, 10)
        header = headers.get('Retry-After') if headers else None
        delay = None
        try:
            delay = float(header)
        except (TypeError, ValueError):
            try:
                date = parsedate_to_datetime(header)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                delay = date.timestamp() - now
            except (TypeError, ValueError, OverflowError):
                pass
        if delay is None or not (0 <= delay < float('inf')):
            delay = min(300 * 2 ** (attempts - 1), 21600)
        until = max(now + max(1, delay) + random.uniform(0, min(60, max(1, delay) * .05)), float(state.get('retry_at', 0)))
        atomic_json(self.path, {'retry_at': until, 'attempts': attempts, 'updated': now})
        raise Cooldown(until)

    def completed(self):
        atomic_json(self.path, {'retry_at': 0, 'attempts': 0, 'updated': time.time()})
