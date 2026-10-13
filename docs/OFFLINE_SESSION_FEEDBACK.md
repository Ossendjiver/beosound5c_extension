# Saved session feedback

Home Media saves session thumbs and their reasons to a private durable outbox before sending. It uses the existing remote Home Assistant bridge when configured and the local BS5c endpoint when reachable. Reports survive an app restart and reboot; an Android network-constrained periodic job retries them, as does the running app. Android can defer background jobs, so reconnection does not guarantee immediate delivery.

The report includes the original observed queue session, song ID, event ID and timestamp. BS5c retains bounded session/song snapshots for up to 30 days, deduplicates retries and rejects unobserved songs. A delayed report cannot rate the new current song or steer a different mix. Closed-session reports are archived; expired session reports are acknowledged without changing a live mix. Recent votes in a still-active mix request an upcoming-track refresh without restarting the playing song. This remains session feedback, not a permanent favourite/dislike.

Remote delivery requires Home Media Bridge 0.1.5 or newer. Without it, reports remain saved for local Wi-Fi delivery. Initial feedback requires a previously observed session/song binding; the app does not invent a session while fully disconnected.
