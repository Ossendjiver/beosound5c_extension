# Phone-mediated iPod sync

Home Media Settings → iPod sync: enter the scoped transfer token shown on the BS5c's own System → Services → IPOD SYNC screen, choose the iPod's USB drive root, then Sync iPod. The phone must stay on the home Wi-Fi network with the iPod attached via USB OTG. Save uses the app's encrypted settings store. This route never uses HA/MA remote access or cellular fallback.

The private phone bridge on port 8790 exposes identity, predicted offline selection, acknowledged listening history and opaque selected-file downloads. It accepts only RFC1918/loopback clients with a separate token; it exposes neither arbitrary filesystem paths nor provider credentials, and issues no player/queue commands. Pairing token retrieval is device-local only with origin checking.

Deploy `beo-ipod-phone.service` with the existing secrets environment. Before enabling, create root-private `/etc/beosound5c/ipod-phone.json` with a random token of at least 16 characters (for example `secrets.token_urlsafe(12)`) and mode 0600. Preserve it on subsequent deployments. An existing dock pairing is reused. Otherwise the first verified phone sync pairs the USB volume UUID, and dock sync uses this same history identity thereafter.

Listening receipts use the same recording/start/device hash as dock sync and are uploaded before selecting or copying anything. Modern Rockbox playback and rotated logs are retained, merged and matched only through the shared HomeMedia manifest. Unmatched logs are preserved. Legacy .scrobbler.log import remains available through dock sync.

The foreground data-sync service binds sockets and DNS to Wi-Fi, disables redirects and retries, and stops on USB/Wi-Fi failure. Cancel is available in Settings and the notification. Every transfer checks byte count and SHA-256, closes/flushed writes before renaming, checkpoints the manifest per completed recording and keeps the previous manifest across SAF renames. Retry retains completed tracks. Existing music is never pruned by phone sync; a 1 GiB reserve is preserved. Available local recordings include the authenticated Tidal offline cache; provider-only tracks are skipped, never downloaded by the phone.

Android cannot automatically eject USB storage through the public SAF API. When the sync says Complete, use Android's USB eject action before unplugging. Some phone/device combinations require a powered OTG adapter and support for USB mass storage. Physical OTG testing remains necessary before release.

Device prompts are deliberately separate from phone requests: music prompts select BS3's LINK source and an explicitly discovered native Link MA queue. News prompts use the configured Lounge Mini morning-news script. Yoga keeps its selected video room. These defaults can be overridden through `library.prompt_music_player_id` and `library.prompt_music_entity`.
