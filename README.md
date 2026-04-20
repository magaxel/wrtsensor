# wrtsensor

A lightweight Home Assistant network monitor for a home LAN running OpenWrt. A single Python script SSHes into the router (and any access points) once per minute, collects the complete picture of the network, and pipes it into Home Assistant as a JSON sensor. Lovelace custom cards render device lists, topology maps, event logs, and dnsmasq DNS statistics.

An OpenWrt gateway is required — the scanner pulls DHCP leases, ARP/NDP tables, WAN, and dnsmasq stats from it. Access points are optional: pass zero, one, or many OpenWrt APs as additional arguments to also collect Wi-Fi associations and per-AP host stats.

## What it collects

Per scan (every 60 s) the scanner produces a single JSON object with:

- **Devices** — MAC, IPv4, IPv6, hostname, vendor (OUI lookup), connection type, online status.
- **Wi-Fi metrics** — for associated clients: AP, band, signal, noise, SNR, TX/RX PHY rates, expected throughput, per-station byte counters.
- **Host stats** — CPU%, RAM%, and root disk% for the gateway and each AP.
- **WAN** — IPv4, IPv6, live RX/TX rate, cumulative byte totals.
- **DNS cache** — dnsmasq cache hit/miss counts and rates, per-upstream query counts and latency, weighted average upstream latency.
- **Event log** — append-only JSONL at `/dev/shm/netscan_events.json` with connect/disconnect/ip_change events (30-day retention, sticky disconnect window to prevent flapping).

All of this comes from a single 20 s SSH call to the gateway plus parallel SSH calls to each AP — no redundant shell sensors.

## Requirements

- **Home Assistant** — any install with a `/config` directory (OS/Supervised/Container). Needs Python 3.11+ (built in on HA OS).
- **OpenWrt** — recent release (tested on 22.03+). Stock packages are enough: `busybox`, `ip`, `iwinfo`, `dnsmasq`, `logread`.
- **Network** — SSH from HA host to every OpenWrt box (gateway + APs), port 22 open.

## Installation

There are two installation options. **HACS (Option A)** is the recommended approach — everything is configured via UI and the cards auto-register. The **manual path (Option B)** is available if you prefer direct file control or don't use HACS.

### Option A — HACS (recommended)

1. In Home Assistant, open **HACS → Integrations → ⋮ → Custom repositories** and add:
   ```
   https://github.com/magaxel/wrtsensor
   ```
   Category: **Integration**

2. Install **wrtsensor** from HACS and restart Home Assistant.

3. Go to **Settings → Devices & Services → Add Integration** and search for **wrtsensor**.

4. Enter your gateway IP, SSH key path (default `/config/ssh/id_ed25519`), and optionally any AP IPs (comma-separated).

5. Done — `sensor.wrtsensor_network_scanner` appears immediately, Lovelace cards auto-register. Open **Settings → Options** on the integration to add presence MACs, change scan interval, or update interface names.

> **SSH key prerequisite:** the HA host must be able to SSH into the gateway (and APs) as root with key auth — see step 1 of the manual path below.

---

### Option B — Manual (command_line sensor)

### 1. SSH key — HA → OpenWrt

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

### 2. Deploy the scanner files

Copy this repo's scanner files to `/config/wrtsensor/` on Home Assistant (SMB / Samba share / `scp` / HA file editor — whatever you use):

```
/config/wrtsensor/diagnose.py
/config/wrtsensor/openwrt_collector.sh
```

No need to `chmod +x` — HA runs them via `python3` / `sh -s`. The `oui.db` / `oui.txt` vendor database is **auto-downloaded** from IEEE/Wireshark on first run — no manual setup.

### 3. Wire up the sensors

Add to your `configuration.yaml`:

```yaml
command_line: !include command_line.yaml
template: !include templates.yaml
```

Copy this repo's [`command_line.yaml`](command_line.yaml) and [`templates.yaml`](templates.yaml) to `/config/` and edit the IPs to match your LAN. The command_line sensor runs:

```
python3 /config/wrtsensor/diagnose.py root@<gateway-ip> root@<ap-ip> [root@<ap-ip> ...]
```

Restart Home Assistant (or run **Developer Tools → YAML → Check configuration → Restart**). After ~60 s, `sensor.wrtsensor_network_scanner` should appear with a device count and a big JSON `attributes` blob.

### 4. Install the Lovelace cards

Copy the JS files in [`www/`](www/) to `/config/www/`. Then **Settings → Dashboards → Resources → Add resource** for each:

| URL | Type |
|-----|------|
| `/local/network-list-card.js` | JavaScript Module |
| `/local/network-table-card.js` | JavaScript Module |
| `/local/network-topology-card.js` | JavaScript Module |
| `/local/network-events-card.js` | JavaScript Module |
| `/local/dns-stats-card.js` | JavaScript Module |

Append `?v=1` (or any string) to bust the HA companion app's cache when you update a card.

## Card configuration

All cards take `entity: sensor.wrtsensor_network_scanner` (the event card uses `sensor.wrtsensor_event_log` — see below). Drop these snippets into a dashboard via **Raw configuration editor** or the **+ ADD CARD → Manual** editor.

### `network-list-card`

Compact, searchable, sortable device list. Each row expands to show Wi-Fi metrics, byte totals, first-seen, etc.

```yaml
type: custom:network-list-card
entity: sensor.wrtsensor_network_scanner
title: Devices
show_offline: false      # default false — offline devices are hidden
max_height: 560          # px; 0 = fill container in sections/grid layouts
columns:                 # which detail fields to show on expand (optional)
  - ip
  - mac
  - vendor
  - ap
  - signal
  - rx_total
  - tx_total
  - first_seen
```

### `network-table-card`

Wider tabular variant — better for widescreen dashboards.

```yaml
type: custom:network-table-card
entity: sensor.wrtsensor_network_scanner
title: Network
show_offline: false
columns:
  - ip
  - ip6_enabled
  - hostname
  - vendor
  - mac
  - connection
  - ap
  - band
  - tx_rate
  - signal
```

### `network-topology-card`

SVG topology map showing the gateway, APs, and clients.

```yaml
type: custom:network-topology-card
entity: sensor.wrtsensor_network_scanner
title: Network Map
gateway_hostname: gw     # label shown on the gateway node
col_width: 200           # px between AP columns
show_bandwidth: false    # true = draw live throughput labels on each link
show_offline: false
```

### `network-events-card`

Filterable event log. Groups events by date, shows per-day count, live search, type-filter buttons.

```yaml
type: custom:network-events-card
entity: sensor.wrtsensor_event_log
title: Network Events
max_height: 560
show_search: true        # hide search bar by setting false
show_filters: true       # hide type-filter pill row by setting false
shown_types:             # optional — limit to specific event types
  - connect
  - disconnect
  - roam
  - ip_change
```

### `dns-stats-card`

dnsmasq cache statistics.

```yaml
type: custom:dns-stats-card
entity: sensor.wrtsensor_network_scanner
title: DNS Cache
```

## Repository layout

```
diagnose.py                # Standalone scanner (manual/command_line path)
openwrt_collector.sh        # Per-AP Wi-Fi association + host-stat collector
command_line.yaml           # Example command_line sensor definitions (manual path)
templates.yaml              # Example derived template sensors (manual path)
www/                        # Lovelace custom cards (manual path — copy to /config/www/)
  network-list-card.js
  network-table-card.js
  network-topology-card.js
  network-events-card.js
  dns-stats-card.js
custom_components/wrtsensor/ # HACS integration (auto-installs everything)
  __init__.py
  manifest.json
  config_flow.py
  coordinator.py
  sensor.py
  binary_sensor.py
  device_tracker.py
  const.py
  strings.json
  translations/en.json
  openwrt_collector.sh      # bundled copy — auto-deployed to APs via SFTP
  www/                      # bundled cards — served via /wrtsensor_static/
hacs.json
```

## Home Assistant integration

### HACS integration — entities created automatically

| Entity ID | What it is |
|-----------|-----------|
| `sensor.wrtsensor_network_scanner` | Main sensor — device count as state, full JSON blob as attributes. All cards read from this. |
| `sensor.wrtsensor_wan_download` | WAN RX rate in Mbit/s |
| `sensor.wrtsensor_wan_upload` | WAN TX rate in Mbit/s |
| `sensor.wrtsensor_dns_cache_hit` | DNS cache hit % |
| `sensor.wrtsensor_dns_latency` | Weighted upstream DNS latency in ms |
| `sensor.wrtsensor_<ip>_cpu` | CPU % per host, e.g. `sensor.wrtsensor_172_16_42_254_cpu` |
| `sensor.wrtsensor_<ip>_ram` | RAM % per host |
| `sensor.wrtsensor_<ip>_disk` | Disk % per host |
| `binary_sensor.wrtsensor_presence_<mac>` | Online/offline per configured MAC (set in Options) |
| `device_tracker.<hostname>` | home/not_home tracker per discovered device — **disabled by default** |

Device tracker entities are named after the device hostname (e.g. `device_tracker.my_phone`) and are disabled by default. HA's scanner entity base class does this to avoid flooding the registry when dozens of devices are discovered. To use a tracker for Person presence, go to **Settings → Entities**, enable "Show disabled entities", find the device, and enable it. The entity can then be added to a Person.

`sensor.wrtsensor_event_log` is a separate `command_line` sensor defined in `command_line.yaml` that tails the JSONL event log at `/dev/shm/netscan_events.json`. It is not part of the integration but must be kept alongside it.

### `command_line.yaml` (manual path)

Defines the `sensor.wrtsensor_network_scanner` command_line sensor that runs the scanner every 60 s (45 s timeout). The `json_attributes` list is an **explicit allowlist** — any new top-level JSON key emitted by the scanner must be added here or Home Assistant silently drops it. Also defines `sensor.wrtsensor_event_log` which tails the JSONL event file (capped at 500 entries per poll).

### `templates.yaml` — derived sensors (manual path)

Template sensors that unpack values out of `sensor.wrtsensor_network_scanner`'s attributes. Not needed when using the HACS integration (which provides dedicated sensor entities instead).

- **Host metrics** per host — CPU%, RAM%, disk% for the gateway and each AP (e.g. `sensor.openwrtgw_used_cpu`, `sensor.kallaren_ap_used_ram`).
- **WAN** — `sensor.wan_download_mbit`, `sensor.wan_upload_mbit`.
- **DNS cache** — `sensor.dns_cache_hit_pct`, `sensor.dns_cache_hits_per_sec`, `sensor.dns_cache_misses_per_sec`, `sensor.dns_upstream_latency_ms`.
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

## Network assumptions

The scanner expects an OpenWrt gateway reachable over SSH (key-based auth). Any number of OpenWrt access points can be added by appending them to the scanner's argument list — the reference deployment below uses three but one or none will also work.

| Role | Address (example) |
|------|-------------------|
| Gateway — **required** | `192.0.2.1` |
| AP — optional | `192.0.2.10` |
| AP — optional (more as needed) | `192.0.2.11`, `192.0.2.12`, … |
| LAN bridge | `br-lan` |
| WAN interface | `eth0` |

Replace the example addresses with whatever your LAN uses. The script takes the gateway as its first argument and any number of APs as subsequent arguments.

SSH uses key-based auth (`id_ed25519`) with connection multiplexing (`ControlMaster=auto / ControlPersist=60`) for low overhead.

## Recorder exclusion

`sensor.wrtsensor_network_scanner` and `sensor.wrtsensor_event_log` carry large JSON blobs as attributes and will bloat the HA database if recorded. Exclude them in `configuration.yaml`:

```yaml
recorder:
  exclude:
    entities:
      - sensor.wrtsensor_network_scanner
      - sensor.wrtsensor_event_log
```

## Logbook exclusion

The same two sensors generate a logbook entry every scan (every 60 s) because their state or attributes change constantly. Exclude them to keep the logbook readable:

```yaml
logbook:
  exclude:
    entities:
      - sensor.wrtsensor_network_scanner
      - sensor.wrtsensor_event_log
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

**Card shows "configuration error"**

- Resource URL not registered, or the `type:` in the dashboard doesn't match the card's registered name (e.g. `custom:network-table-card`).
- Companion-app JS cache — bump the `?v=` query on the resource URL.

**Event card shows only a handful of events**

The command_line sensor runs `tail -500 /dev/shm/netscan_events.json`. If you need more, raise the limit there. The underlying event log keeps 30 days.

## Development

```bash
# Lint
ruff check diagnose.py
ruff format diagnose.py
shellcheck openwrt_collector.sh
biome check www/*.js
yamllint command_line.yaml templates.yaml
yamlfmt -dry command_line.yaml templates.yaml

# Run the scanner locally (replace with your gateway + any APs)
python3 diagnose.py \
  root@<gateway-ip> root@<ap-ip> [root@<ap-ip> ...]

# Inspect the live event log
cat /dev/shm/netscan_events.json
```

The scanner writes state under `/dev/shm` on HA (or `/tmp/netscan` locally): previous-scan device state, MAC vendor cache, DNS cache cache, CPU delta state, and the event log.

## License

MIT — see [LICENSE](LICENSE). Use it, fork it, sell it; just keep the copyright notice.
