# Verified external listening receiver

`services/external_listens.py` is a standalone standard-library service. It
does not change the existing MASS source, router, or playback controls. Home
Assistant remains the audio capture and recognition host.

Install `deploy/beo-external-listens.service`, adapting its User, Group and
ExecStart for another host. It reads the existing root-only
`/etc/beosound5c/secrets.env` for `MASS_TOKEN` and a new root-only
`/etc/beosound5c/external-listens.env` for `EXTERNAL_LISTENS_TOKEN`. Use a randomly
generated token of at least 32 characters, also configured in HA. Never commit
these files. The service defaults to port 8798 and local MA HTTP API on 8095.
Optional `EXTERNAL_LISTENS_BIND`, `EXTERNAL_LISTENS_PORT`, and
`EXTERNAL_LISTENS_MA_URL` override those defaults. Use a trusted LAN or TLS proxy.

```sh
sudo install -m 644 services/external_listens.py /home/thomas/beosound5c/services/
sudo install -m 644 deploy/beo-external-listens.service /etc/systemd/system/
# Create the separate token env file securely before starting the unit.
sudo chmod 600 /etc/beosound5c/external-listens.env
sudo systemctl daemon-reload
sudo systemctl enable --now beo-external-listens.service
systemctl is-active beo-external-listens.service
python3 -m unittest discover -s tests -p test_external_listens.py -v
```

POST `/library/external/listens` with bearer authentication. It accepts only
completed `external_listen` events with two observations, the confirmation
method `time_separated_audio`, finite ordered timestamps and plausible observed
seconds. Predictions are rejected. A stable event ID makes retries idempotent;
different payloads under one ID are rejected. Responses acknowledge accepted
or duplicate event IDs only after the SQLite transaction commits.

State is `/var/lib/beosound5c-external-listens/listens.sqlite3` under systemd.
No raw audio, credentials or event bodies are logged. Keep this database private.
There is a 100,000-event cap; full storage returns an error so HA retains its
pending event. Back it up locally and arrange retention before reaching the cap.

The worker searches existing MA library tracks, then the user's configured
provider catalogues when the recording is absent from the library. It uses
actual returned provider items/URIs. It requires matching title and artist,
compatible version and duration, and a compatible album for catalogue matches.
Shared explicit ISRCs can disambiguate duplicate catalogue copies. Unknown or
conflicting versions remain unresolved; no fabricated URI or library entry is
created. Retries back off through one, two and four minutes, then hourly, up to
24 attempts. Recognition does not require an MA library entry.

A real resolved track uses `music/mark_played`, normally `fully_played=false`, observed
`seconds_played`, `is_playing=false`, `user_initiated=false`. This records a
partial playlog entry without falsely incrementing full-track play counts.
Completed-play credit requires a start estimate and at least 90% of catalogue
duration observed, with a minimum 30 seconds. Partial entries remain partial;
MA's default recent-track query excludes them.

An album context is recorded once per enabled session and four-hour period using the same semantics
as MA's native queue: playing its tracks can put an album in Recently played
without marking any other album tracks as heard. Context writes have separate
persistent idempotency/uncertainty states. User-confirmed manual backfills are
annotated separately and do not forge acoustic observations.
The installed MA 2.10.5 implementation was inspected and its authenticated
HTTP library-search endpoint tested. Its playlog is a per-item view, not the
complete historical ledger; this SQLite inbox preserves distinct events.

Before mark_played, status becomes `uncertain`. Successful response changes it
to `ma_recorded`. A timeout/crash stays uncertain and is never automatically
reissued because MA has no event-ID deduplication argument. Reconcile such rows
against MA manually. This favors avoiding duplicates over guaranteed delivery
through an ambiguous downstream failure.

Receiver unit tests and isolated HTTP tests passed on BS5c. Synthetic HTTP
fixtures used a separate temporary database and stubbed downstream search.
They did not add fictitious production listens or MA plays. A real user-confirmed
session was subsequently backfilled: all confirmed tracks and its album context
were verified through that user's MA recent-items API. The manual annotations
and listening history remain private on the host. Ten receiver tests and three
separate Dining presence-policy/patcher tests pass.

Rollback affects only this new service and preserves its database:

```sh
sudo systemctl disable --now beo-external-listens.service
```

Clear the HA receiver URL to stop new delivery; pending events remain in HA's
private outbox. Do not remove or replace existing MASS service files.
