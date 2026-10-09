"""Read-only playback guard for proactive music prompts."""
import datetime

class Guard:
    def __init__(self):
        self.states = {}
        self.paused = {}

    def observe(self, key, state, now, changed=None):
        state = str(state or '').lower()
        if state == 'paused':
            try:
                stamp = datetime.datetime.fromisoformat(str(changed).replace('Z', '+00:00')).timestamp()
            except (ValueError, TypeError):
                stamp = now
            if self.states.get(key) != 'paused':
                self.paused[key] = min(now, stamp)
        else:
            self.paused.pop(key, None)
        self.states[key] = state

    def blocked(self, now):
        return any(state in ('playing', 'buffering') or
                   state == 'paused' and now - self.paused.get(key, now) < 1800
                   for key, state in self.states.items())
