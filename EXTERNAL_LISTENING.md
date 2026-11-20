# External listening integration (HA-first)

**Active listening host for the current rollout: Home Assistant.** BS5c is the eventual recognition/capture host, but should begin now as a durable **external listening history receiver**. The current core listener, router and Music Assistant queue code must remain untouched until hardware tests pass.

## Staged implementation

- `services/lib/external_listens.py` stores confirmed AUX/CD/radio listens in a separate SQLite database (`/media/local/cache/bs5c_external_listens.sqlite3`). It does not infer or manufacture Music Assistant URIs.
- `services/library.py` registers new routes. No source switch, queue change or playback interruption occurs when ingesting a listen.
- The endpoint `POST /library/external/listens` requires `Authorization: Bearer <BS5C_EXTERNAL_LISTEN_TOKEN>` from a configured, private environment variable. If unset, writes are disabled. The Home Assistant add-on must be given this token locally, **not in GitHub**.
- `GET /library/external/recent?limit=50` reports durable history even while Music Assistant is unavailable.
- `POST /library/external/favorites` stores an external favourite locally. It does not falsely mark an unrecognised provider item as favourite.
- `POST /library/external/resolve` accepts only an **explicitly validated real Music Assistant URI**. Resolving a provider item does not itself cause MA playback, favourite or play-log mutation.

## Required ingestion record

```json
{
  "event_id":"ha-dining-20261121-00000000001",
  "external":true,
  "room":"dining", "source":"AUX",
  "title":"Example Track", "artist":"Example Artist", "album":"",
  "first_observed_at":1795200000,
  "confirmed_at":1795200012,
  "ended_at":1795200200,
  "listened_seconds":180,
  "observations":2, "providers":["shazam"],
  "confirmation_method":"time_separated_audio"
}
```

Two independent *audio windows* are mandatory; two providers recognizing the same exact short window are supporting evidence, not a replacement for the second window. Event IDs are unique and idempotent across repeated delivery. Old events retain their original timestamps; no backdating from arrival time.

## Music Assistant handling (not implemented)

Music Assistant offers `music/mark_played` for a real resolved MediaItem and `music/favorites/add_item` with a real URI. **Do not send unresolved off-network tracks as fake media items.** Even a correct name match can pick the wrong recording/live version. Local external history is the source of truth; MA may only be enriched once identity is validated. Recent MA playlog is not necessarily an immutable multi-event listening journal.

## Future Song Radio / Mood Mix

Home Media may read the external history and local favourite state now. For a manually resolved MA URI, the existing `/library/mix` service can start Song Radio in a user-selected queue, without controlling the physical AUX source. Unresolved recordings need a separate metadata-only seed resolver and safe fallback to a normal Mood Mix, which is explicitly **not** wired yet. Do not create fake provider URIs or force a playback source change.

## Live safety checks still required

- On HA, verify selected source (Dining AUX/CD/radio) before submitting a completed listen; confirm two time-separated recognitions and realistic seconds.
- Configure shared token on both ends (HA outbox and BS5c server); never print it to logs.
- Test duplicates, network outage/reconnect, ordering, mixed radio talk, gapless playback, misrecognition, long gaps and BS5c restarts.
- External favourite and song radio Android UI are **not wired yet**; only the client methods/contract are staged.
- Run `python -m unittest services.tests.test_external_listens` if local test discovery and dependencies permit; no live BS5c service or Music Assistant has been altered.

Project tracker: Google Drive → Home Assistant → Audio identifier → Audio Identifier — Implementation & Test Tracker.
