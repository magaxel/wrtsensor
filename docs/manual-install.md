# Manual installation (command_line sensor)

> This is an alternative to the recommended [HACS install](../README.md#installation). Use it if you prefer direct file control or don't run HACS. Everything in this document is **not** needed when using HACS.

## Table of contents

- [1. SSH key — HA → OpenWrt](#1-ssh-key--ha--openwrt)
- [2. Deploy the scanner files](#2-deploy-the-scanner-files)
- [3. Wire up the sensors](#3-wire-up-the-sensors)
- [4. Install the Lovelace cards](#4-install-the-lovelace-cards)
- [`command_line.yaml`](#command_lineyaml)
- [`templates.yaml` — derived sensors](#templatesyaml--derived-sensors)
- [Troubleshooting](#troubleshooting)

## 1. SSH key — HA → OpenWrt

On the HA host (Terminal add-on or SSH into the HA OS):

```bash
mkdir -p /config/ssh
ssh-keygen -t ed25519 -f /config/ssh/id_ed25519 -N ""
cat /config/ssh/id_ed25519.pub
```

Copy the printed public key into `/etc/dropbear/authorized_keys` on **each** OpenWrt box (gateway + every AP). Verify:

```bash
ssh -i /config/ssh/id_ed25519 root@<gateway-ip> uptime
```

Should return the OpenWrt uptime without a password prompt.

## 2. Deploy the scanner files

Copy this repo's scanner files to `/config/wrtsensor/` on Home Assistant (SMB / Samba share / `scp` / HA file editor — whatever you use):

```
/config/wrtsensor/diagnose.py
/config/wrtsensor/openwrt_collector.sh
```

No need to `chmod +x` — HA runs them via `python3` / `sh -s`. The `oui.db` / `oui.txt` vendor database is **auto-downloaded** from IEEE/Wireshark on first run — no manual setup.

## 3. Wire up the sensors

Add to your `configuration.yaml`:

```yaml
command_line: !include command_line.yaml
template: !include templates.yaml
```

Copy this repo's [`command_line.yaml`](../command_line.yaml) and [`templates.yaml`](../templates.yaml) to `/config/` and edit the IPs to match your LAN. The command_line sensor runs:

```
python3 /config/wrtsensor/diagnose.py root@<gateway-ip> root@<ap-ip> [root@<ap-ip> ...]
```

Restart Home Assistant (or run **Developer Tools → YAML → Check configuration → Restart**). After ~60 s, `sensor.wrtsensor_network_scanner` should appear with a device count and a big JSON `attributes` blob.

## 4. Install the Lovelace cards

Copy the JS files in [`www/`](../www/) to `/config/www/`. Then **Settings → Dashboards → Resources → Add resource** for each:

| URL | Type |
|-----|------|
| `/local/network-list-card.js` | JavaScript Module |
| `/local/network-table-card.js` | JavaScript Module |
| `/local/network-topology-card.js` | JavaScript Module |
| `/local/network-events-card.js` | JavaScript Module |
| `/local/dns-stats-card.js` | JavaScript Module |

Append `?v=1` (or any string) to bust the HA companion app's cache when you update a card.

## `command_line.yaml`

Defines the `sensor.wrtsensor_network_scanner` command_line sensor that runs the scanner every 60 s (45 s timeout). The `json_attributes` list is an **explicit allowlist** — any new top-level JSON key emitted by the scanner must be added here or Home Assistant silently drops it. Also defines `sensor.wrtsensor_event_log` which tails the JSONL event file (capped at 500 entries per poll). This file-backed event log is specific to the manual install path; the HACS integration keeps recent events in memory only.

## `templates.yaml` — derived sensors

Template sensors that unpack values out of `sensor.wrtsensor_network_scanner`'s attributes. Not needed when using the HACS integration (which provides dedicated sensor entities instead).

- **Host metrics** per host — CPU%, RAM%, disk% for the gateway and each AP (e.g. `sensor.openwrtgw_used_cpu`, `sensor.kallaren_ap_used_ram`).
- **WAN** — `sensor.wan_download_mbit`, `sensor.wan_upload_mbit`.
- **DNS cache** — `sensor.dns_cache_hit_pct`, `sensor.dns_cache_hits_per_sec`, `sensor.dns_cache_misses_per_sec`, `sensor.dns_upstream_latency_ms`. `diagnose.py` also writes `.netscan_dns_history.jsonl` so `dns-stats-card` can show the same last-24h view on manual installs.
- **Device presence** — binary sensors matching specific MACs (e.g. family phones) with `device_class: presence` for person/automation use.

Adding a new per-host template follows the pattern:

```yaml
- unique_id: <uuid>
  name: <hostname>_used_cpu
  unit_of_measurement: "%"
  state: >-
    {% set h = state_attr('sensor.wrtsensor_network_scanner', 'host_stats') %}
    {{ h.get('<ip>', {}).get('cpu') if h else none }}
```

## Troubleshooting

**Sensor stays `unavailable` after restart**

Run the scanner by hand on the HA host to see the raw error:

```bash
python3 /config/wrtsensor/diagnose.py root@<gateway-ip> root@<ap-ip>
```

Common causes:

- **SSH key rejected** — permissions wrong (`chmod 600 /config/ssh/id_ed25519`) or public key not in OpenWrt's `authorized_keys`.
- **Wrong interface names** — defaults are `br-lan` (bridge) and `eth0` (WAN). Edit constants at the top of `diagnose.py` if yours differ.
- **`openwrt_collector.sh not found`** — the helper script must live next to `diagnose.py` in `/config/wrtsensor/`.
- **`command_timeout` exceeded** — bump `command_timeout` in `command_line.yaml` (default 45 s); a slow router + many devices can push past that.

**A new top-level JSON key is silently missing in `sensor.wrtsensor_network_scanner`'s attributes**

`json_attributes` in `command_line.yaml` is an allowlist. Add the key there and restart.

**Event card shows only a handful of events**

The command_line sensor runs `tail -500 /dev/shm/netscan_events.json`. If you need more, raise the limit there. The underlying event log keeps 30 days.
