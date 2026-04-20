# wrtsensor

> ⚠️ **Under heavy development.** Config schema, entity IDs, and behaviour may change without notice. Pin to a specific commit or tag if you need stability.

A lightweight Home Assistant network monitor for a home LAN running OpenWrt. A single Python integration SSHes into the router (and any access points) once per minute, collects the complete picture of the network, and exposes it to Home Assistant as sensors, binary sensors, and device trackers. Lovelace custom cards render device lists, topology maps, event logs, and dnsmasq DNS statistics.

An OpenWrt gateway is required — the scanner pulls DHCP leases, ARP/NDP tables, WAN, and dnsmasq stats from it. Access points are optional: zero, one, or many OpenWrt APs can be added to also collect Wi-Fi associations and per-AP host stats.

## Table of contents

- [What it collects](#what-it-collects)
- [Requirements](#requirements)
- [Installation](#installation)
- [Card configuration](#card-configuration)
- [Entities](#entities)
- [Network assumptions](#network-assumptions)
- [Recorder & logbook exclusion](#recorder--logbook-exclusion)
- [Troubleshooting](#troubleshooting)
- [Additional docs](#additional-docs)
- [License](#license)

## What it collects

Per scan (every 60 s) the integration produces a single JSON object with:

- **Devices** — MAC, IPv4, IPv6, hostname, vendor (OUI lookup), connection type, online status.
- **Wi-Fi metrics** — for associated clients: AP, band, signal, noise, SNR, TX/RX PHY rates, expected throughput, per-station byte counters.
- **Host stats** — CPU%, RAM%, and root disk% for the gateway and each AP.
- **WAN** — IPv4, IPv6, live RX/TX rate, cumulative byte totals.
- **DNS cache** — dnsmasq cache hit/miss counts and rates, per-upstream query counts and latency, weighted average upstream latency.
- **Event log** — append-only JSONL at `/dev/shm/netscan_events.json` with connect/disconnect/ip_change events (30-day retention, sticky disconnect window to prevent flapping).

All of this comes from a single 20 s SSH call to the gateway plus parallel SSH calls to each AP — no redundant shell sensors.

## Requirements

- **Home Assistant** — Python 3.11+ (built in on HA OS).
- **HACS** — install [HACS](https://hacs.xyz/) first; it's how wrtsensor is distributed.
- **OpenWrt** — recent release (tested on 22.03+). Stock packages are enough: `busybox`, `ip`, `iwinfo`, `dnsmasq`, `logread`.
- **Network** — SSH from HA host to every OpenWrt box (gateway + APs). Port 22 by default; any port works (configurable in the integration's Options).

## Installation

1. In Home Assistant, open **HACS → Integrations → ⋮ → Custom repositories** and add:
   ```
   https://github.com/magaxel/wrtsensor
   ```
   Category: **Integration**

2. Install **wrtsensor** from HACS and restart Home Assistant.

3. Go to **Settings → Devices & Services → Add Integration** and search for **wrtsensor**.

4. Enter your gateway IP, SSH key path (default `/config/ssh/id_ed25519`), and optionally any AP IPs (comma-separated).

5. Done — `sensor.wrtsensor_network_scanner` appears immediately, Lovelace cards auto-register. Open **Settings → Options** on the integration to add presence MACs, change scan interval, or update interface names.

> If key authentication isn't set up yet, the config flow will ask for a password once and provision the public key into `/etc/dropbear/authorized_keys` on each OpenWrt box for you. The password is never stored.

Prefer not to use HACS? See [docs/manual-install.md](docs/manual-install.md) for the `command_line` sensor path.

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

## Entities

| Entity ID | What it is |
|-----------|-----------|
| `sensor.wrtsensor_network_scanner` | Main sensor — device count as state, full JSON blob as attributes. All cards read from this. |
| `sensor.wrtsensor_wan_download` | WAN RX rate in Mbit/s |
| `sensor.wrtsensor_wan_upload` | WAN TX rate in Mbit/s |
| `sensor.wrtsensor_dns_cache_hit` | DNS cache hit % |
| `sensor.wrtsensor_dns_latency` | Weighted upstream DNS latency in ms |
| `sensor.wrtsensor_<ip>_cpu` | CPU % per host, e.g. `sensor.wrtsensor_192_0_2_1_cpu` |
| `sensor.wrtsensor_<ip>_ram` | RAM % per host |
| `sensor.wrtsensor_<ip>_disk` | Disk % per host |
| `binary_sensor.wrtsensor_presence_<mac>` | Online/offline per configured MAC (set in Options) |
| `device_tracker.<hostname>` | home/not_home tracker per discovered device — **disabled by default** |

Device tracker entities are named after the device hostname (e.g. `device_tracker.my_phone`) and are disabled by default. HA's scanner entity base class does this to avoid flooding the registry when dozens of devices are discovered. To use a tracker for Person presence, go to **Settings → Entities**, enable "Show disabled entities", find the device, and enable it. The entity can then be added to a Person.

## Network assumptions

The integration expects an OpenWrt gateway reachable over SSH (key-based auth). Any number of OpenWrt access points can be added — the reference deployment uses three but one or none will also work.

| Role | Address (example) |
|------|-------------------|
| Gateway — **required** | `192.0.2.1` |
| AP — optional | `192.0.2.10` |
| AP — optional (more as needed) | `192.0.2.11`, `192.0.2.12`, … |
| LAN bridge | `br-lan` |
| WAN interface | `eth0` |

Replace the example addresses with whatever your LAN uses. The HACS integration multiplexes connections with asyncssh in-process; the standalone `diagnose.py` script uses `ControlMaster=auto / ControlPersist=60`.

## Recorder & logbook exclusion

`sensor.wrtsensor_network_scanner` and `sensor.wrtsensor_event_log` carry large JSON blobs as attributes and change every scan (every 60 s). Exclude them in `configuration.yaml` to keep the database and logbook trim:

```yaml
recorder:
  exclude:
    entities:
      - sensor.wrtsensor_network_scanner
      - sensor.wrtsensor_event_log

logbook:
  exclude:
    entities:
      - sensor.wrtsensor_network_scanner
      - sensor.wrtsensor_event_log
```

## Troubleshooting

**Integration shows "Failed to connect"** — SSH key path is wrong or unreadable by HA, or the key isn't in OpenWrt's `authorized_keys`. The config flow offers a password-provisioning step; otherwise set the key up manually (see [manual install — SSH key](docs/manual-install.md#1-ssh-key--ha--openwrt)).

**All APs in log as "unreachable"** — APs must be reachable from HA over SSH with the same key as the gateway, on the SSH port configured for the integration (default 22; change in the integration's Options). Check with `ssh -i /config/ssh/id_ed25519 -p <port> root@<ap-ip> uptime` on the HA host.

**Card shows "configuration error"** — the resource URL isn't registered, or the `type:` in the dashboard doesn't match the card's registered name (e.g. `custom:network-table-card`). For HACS installs, cards are auto-served under `/wrtsensor_static/`. Bump the `?v=` query on the resource URL to bust the companion app's JS cache after updates.

**A device tracker won't show up** — trackers are disabled by default. Enable via **Settings → Entities → Show disabled entities**.

For manual-install specific troubleshooting (diagnose.py, `command_line.yaml`, `json_attributes` allowlist) see [docs/manual-install.md#troubleshooting](docs/manual-install.md#troubleshooting).

## Additional docs

- [Manual installation](docs/manual-install.md) — command_line sensor path for users without HACS.
- [Development](docs/development.md) — lint/test commands, repository layout, CI.

## License

MIT — see [LICENSE](LICENSE). Use it, fork it, sell it; just keep the copyright notice.
