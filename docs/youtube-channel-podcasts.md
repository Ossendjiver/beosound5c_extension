# YouTube channels as Music Assistant podcasts

YouTube song/video search results with an uploader URL offer **Save channel as podcast**. The bridge resolves a public channel to a canonical feed; BS5c checks existing MA podcastfeed configurations before creating a subscription. Saving a podcast is intercepted before room targeting or playback, so it does not activate a source, modify a queue, or play media.

Requires NewPipe bridge 0.1.2, its configured MA-reachable public URL, and an MA admin connection. Up to 60 recent finite Videos-tab uploads become audio episodes; public feeds update on MA's normal sync schedule. This is not a full channel archive. Provider creation is an admin operation, separate from normal music library saves.

Nine focused Python tests passed, including duplicate detection and canonical channel validation. Browser JavaScript syntax passed. Bridge RSS passed its own tests and MA's podcastparser dependency. Source prepared on 6 October 2026; podcast bridge/backend/menu updates are not deployed yet, and Home Media has not been rebuilt.
