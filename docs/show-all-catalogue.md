# Show all provider catalogues and preferred album editions

Home Media Show all now groups albums by normalized artist/title family independently of the provider version field. Parenthetical album subtitles and edition labels are grouped; different named albums remain separate. Preferred copies rank availability, explicit status, audio quality and library membership in that order. Track recording versions such as live performances and remixes remain distinct; null and missing version metadata are equivalent. Unicode titles retain their letters.

BS5c artist and album item context menus now offer Show all. The source service exposes a read-only `/catalogue?uri=...` endpoint. Artist expansion follows only exact available provider mappings; album expansion requests all album tracks with `in_library_only=false`. Album folders load lazily on entry, avoiding a full provider scan or playback/library mutation. Provider errors are visible; stale responses cannot navigate after a selection or folder change.

Validation: full Android unit suite passed, BS5c Python suite 1173 passed / 14 skipped, JavaScript suite 116 passed. No device playback, library database, HA configuration or live deployment was changed.
