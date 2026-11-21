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

The worker searches **existing MA library tracks**. It requires unique exact
normalized title and artist, an identical version, and compatible duration when
known. It rejects potentially truncated search results. It does not fabricate
URIs or add unknown music to the library. Unresolved recordings stay in this
external history and are retried hourly up to 24 times.

A real resolved track uses `music/mark_played`, `fully_played=false`, observed
`seconds_played`, `is_playing=false`, `user_initiated=false`. This records a
partial playlog entry without falsely incrementing full-track play counts.
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
They did not add fictitious production listens or MA plays. The first genuinely
confirmed completed listen is still needed for full live-path verification.

Rollback affects only this new service and preserves its database:

```sh
sudo systemctl disable --now beo-external-listens.service
```

Clear the HA receiver URL to stop new delivery; pending events remain in HA's
private outbox. Do not remove or replace existing MASS service files.
