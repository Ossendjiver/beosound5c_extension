# Shared Playing queue context menu

The Showing fallback previously verified a shared MA queue but explicitly exposed only Show Information. Its generic overlay also ignored the existing goto capability. This made the restored queue overlay appear to have lost the richer MASS menu.

The verified shared queue now returns track URIs, durations, stable item IDs, the actual queue ID, the current item ID and explicit capabilities. Its menu restores Play Now, Play Next, Play Radio from here, Show Information, Go to Artist/Album/Genre and Remove. Artist and Album use the existing music library navigation. Genre retains the existing honest unavailable message; a genre browser is not implemented by this change. Information uses the existing item_info endpoint, with a queue-metadata fallback.

Mutations use the dedicated `/playing_queue/action` endpoint. Each operation rebinds the configured shared player to the displayed title/artist, checks its actual queue and current queue-item ID, and resolves the selected stable ID without a positional fallback. Changed or missing items are rejected. A numeric UI position is never used. No source registration, target selection, queue transfer or room power command is sent. Existing source-owned queues keep their source-specific capabilities.

Radio uses the local adaptive radio session, with the selected seed confirmed first and an expected-item guard checked inside the mix-session lock. It does not invoke MA endless radio. The existing current song is retained when already selected and playing. Buffered queue edit restrictions remain those of the existing MA action handlers.

Queue refresh preserves selection by stable item ID; a removed selection exits its stale context menu. Mood-wheel queues use the same verified shared path, including real MASS presets in custom mode. Old/read-only servers still expose only their supported controls.

Validation: 73 focused Python tests, 11 JS tests and two offline Chromium walkthroughs passed. Live access was read-only. These changes are staged only: no HA or BS5c service restart, script reload, UI reload or live queue mutation was performed.
