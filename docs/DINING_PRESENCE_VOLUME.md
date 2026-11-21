# Dining volume ceiling while Thomas is away

The installed measured-volume HA adapter retains its configured 70% ceiling
while `person.thomas` is not `home`, including unavailable/unknown presence.
With `BS5C_DINING_HOME_EXEMPT=1`, only Dining targets allow the full 0–100% range
while Thomas is home. Other rooms keep the configured ceiling.

The policy reads presence at most once every two seconds with a one-second
deadline. It changes no hardware volume by itself. The existing policy for a
receiver already above the ceiling is preserved: further upward changes are
blocked, rather than issuing an automatic reduction. The router rescales its UI
and baseline during its existing passive feedback cycle, avoiding a volume jump
when the presence policy changes.

`services/lib/volume_adapters/presence_limit.py` holds the isolated policy.
`tools/apply_dining_presence_limit.py` adds its four hooks to the deployed
measured-volume adapter without overwriting unrelated local changes. It refuses
older incompatible adapters and is idempotent. Back up the adapter before using
the patcher. The deployed opt-in is a dedicated systemd drop-in:

```ini
[Service]
Environment=BS5C_DINING_HOME_EXEMPT=1
```

It is installed as
`/etc/systemd/system/beo-router.service.d/dining-presence-limit.conf`.
The service was restarted and a read-only live HA lookup verified home state:
Dining ceiling 100%, another-room ceiling 70%. No volume commands were sent.
Two async policy tests cover home/away/unknown states, other rooms, caching and
the disabled default.

Rollback (preserves all other adapter customizations from the backup):

```sh
sudo cp -p /var/backups/bs5c-dining-presence-limit/hass-before.py /home/thomas/beosound5c/services/lib/volume_adapters/hass.py
sudo rm /etc/systemd/system/beo-router.service.d/dining-presence-limit.conf
sudo systemctl daemon-reload
sudo systemctl restart beo-router.service
```
