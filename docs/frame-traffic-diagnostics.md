# TV-only traffic diagnostics over a Wi-Fi gateway

This opt-in diagnostic temporarily routes one TV's IPv4 Internet traffic through
BS5c. Both devices may remain on the same Wi-Fi network. Existing LAN routing,
Docker chains and audio services are preserved. It is not passive full-LAN
capture: same-LAN unicast traffic need not pass through this gateway.

It depends on the stock YouTube watchdog's `/library/youtube-watchdog` endpoint
(see `youtube-watchdog.md`). The recorder labels an app already present at
capture startup as pre-existing; it only marks a new return after observing a
stable absence. A detection timestamp is not the exact installation time.

## Recorded data and limits

- Linux conntrack NEW/DESTROY events filtered by the TV's original source IP,
  including TCP, UDP/QUIC, peer addresses/ports and available byte counters.
- CoreDNS query names, query types, response codes and timestamps. Only the TV
  IP is accepted. Existing Samsung domain blocks are mirrored for all qtypes.
- YouTube incident markers correlated with the bounded preceding logs.
- No viewing payloads, HTTP URL paths, passwords or TLS decryption.

Logs go only to SSD under `/media/local/cache/frame-traffic/{dns,flow}`. Each
recorder is bounded to 50 MiB and seven days (100 MiB combined), with 5 MiB
parts. Old parts are removed before appending. Disk-write failure does not
alter forwarding. Netlink loss/parse errors are explicitly marked as gaps.

The flow service uses CAP_NET_ADMIN only for conntrack event subscription;
the DNS service needs no extra capabilities. Both have CPU and memory limits
in their unit files, subject to the host's available cgroup controllers.
The forwarding service runs as root to install owned rules; no permanent
firewall save/restore or broad table flush is used.

## IPv6 coverage must be explicit

Prefer disabling IPv6 on the TV during the diagnostic window. If its firmware
has no control, `ipv4_dns_only: true` returns empty AAAA answers only to this
TV. `allow_ipv4_only_capture: true` is an explicit acknowledgement that this
is IPv4/DNS coverage: hard-coded IPv6 or encrypted DNS may bypass it. It does
not disable household IPv6 and must not be described as complete traffic
capture. HTTPS contents remain encrypted, and an endpoint correlated with a
reinstall does not prove its payload contained an app package. A cached or
firmware-provided package can return without a new Internet download.

## Preserve the existing router policy

Source NAT makes forwarded traffic appear to come from BS5c. Before activating,
mirror the TV's router block policy onto BS5c's MAC (or an equivalent isolated
policy) and verify that it has no time, bedtime or unrelated category limits.
Preserve the TV's existing membership and every blocked domain; record only
what was added for rollback. Mirror those domains in the dedicated CoreDNS
config too. Do not turn off blocks just to provoke a download. No router
administration credentials are installed on BS5c by this feature.

## Installation: explicit opt-in, no activation by a normal update

1. Copy the three `tools/frame_traffic/*.py` files to the SSD directory
   `/media/local/cache/frame-traffic/app`. Install a pinned official CoreDNS
   ARM64 binary at `.../bin/coredns`, verifying its published SHA256 before
   extraction. Version 1.15.0 was validated during development.
2. Copy `config/frame-traffic.example.json` to
   `/etc/beosound5c/frame-traffic.json`; set the real TV MAC/IP, BS5c IP,
   interface, subnet, upstream DNS and block list. Confirm both IPs remain
   stable. This file contains no authentication credentials.
3. Generate the Corefile using `plan.corefile(config)` and save it at
   `/etc/beosound5c/frame-traffic.Corefile`. This binds a TV-only listener on
   the configured BS5c address, port 1053 by default. The owned DNS redirect
   exposes ordinary port 53 only to the TV IP and MAC.
4. Install the units with `__USER__` replaced by the service account. Create
   the SSD `dns` and `flow` directories writable by that account. Start the
   DNS and flow units; verify the listener, event socket and logs. Their
   conditions prevent startup when the private configuration is absent.
5. Confirm the mirrored router policy. Set `enabled` and
   `router_policy_mirrored` true, and either `tv_ipv6_disabled` true or the
   explicit IPv4-only combination above. Keep the real coverage visible in
   deployment notes. Create `.../enabled.flag` only after approval.
6. Enable/start `beo-frame-traffic-network.service`. Its guarded helper creates
   only the three `BS5C_FRAME_DIAG_*` chains and links them ahead of Docker's
   default-drop forwarding policy. Existing Docker rules are not rewritten.
   Original sysctls and full rule ownership are saved on SSD before mutation.
7. Only after readiness, manually retain the TV's IP/subnet and change its
   gateway and DNS to BS5c. Run the TV's Internet test. Verify DNS logs and
   gateway counters in both directions before claiming active capture.

BS5c becomes a dependency of the TV's Internet connection during this test.
The network unit reconstructs only owned rules after a reboot; restarting a
collector does not silently remove forwarding. `ensure` is idempotent and
refuses changed configuration until explicit rollback. Missing SSD storage
fails startup rather than writing capture data to the SD card.

## Rollback

First restore the TV's original gateway/DNS and IPv6 setting. Remove the SSD
`enabled.flag` and disable the network unit. Run `activate.py CONFIG --action
rollback` to remove only the recorded owned jumps/chains and restore sysctls
only when their values still match this setup. Stop the diagnostic units and
remove only the BS5c MAC/profile membership added for this test. Preserve the
bounded incident evidence. No audio service restart is required.

## Validation

Offline tests cover strict address/MAC input, rule scope, disabled defaults,
owned rollback, crash-safe ownership recording, NAT-original tuples, UDP and
byte counters, rejection of other source IPs, malformed netlink input and
bounded logs. A checksum-verified CoreDNS 1.15.0 binary was tested on loopback:
six blocked names and their subdomains, TCP DNS blocking, AAAA NODATA,
per-qtype Samsung blocking, allowed upstream resolution and non-TV refusal.
On-device checks verified unit startup, the underlying SSD (including systemd
bind mounts), conntrack subscription and idempotent gateway activation.

References: [Wireshark capture placement](https://wiki.wireshark.org/CaptureSetup/Ethernet),
[CoreDNS ACL](https://coredns.io/plugins/acl/),
[template](https://coredns.io/plugins/template/) and
[query logging](https://coredns.io/plugins/log/).
