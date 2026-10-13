# Default music continuation

BS5c's library service now watches all available MA queues, including Home Media's phone queue. By default an ordinary playing music queue becomes a persistent local song-radio session when it has seven or fewer upcoming tracks. It appends recommendations after the existing album/playlist, preserving its tracks, order and queue-item IDs. It uses the current song as the root, and the existing queue tail as the transition reference. Existing explicit radio/mood sessions keep their selected policy.

News, podcasts, spoken audio, radio streams, video, paused/stopped queues, repeat mode and active MA sleep timers are excluded from automatic adoption. Queue continuation never issues play/resume/seek/transfer commands. An ended or user-stopped queue is not restarted. It works without the app being open, over either local or remote MA playback, while BS5c can reach MA.

The monitor refills early, retries transient failures, and isolates in-flight checks per queue. Saved mix intent survives phone disconnects and service restarts. Missing metadata and provider-to-library URI resolution do not silently cancel sessions; an unrelated manual replacement does. Exact length, recording/version deduplication, recent skips, genre/mood compatibility and discovery distribution policies remain in force. If the library has no eligible playable tracks, the service keeps retrying rather than inserting unrelated material; it cannot guarantee uninterrupted music in that case.

`library.continue_music=false` disables adoption of ordinary queues. Explicit mixes remain available. Optional `library.continuation_rooms` maps MA queue IDs to recommendation room contexts; otherwise the phone uses `phone`, and shared targets use `shared`. Choosing Stop Mix opts that queue out until its queue head changes or a new mix is explicitly started. Opt-outs are persisted.

App v0.7.62 already contains the required client behaviour; no new APK is needed for this backend update. The previously ended phone session has already been removed by the old backend; start music again to use the repaired/default continuation.
