# wrtsensor

> ⚠️ **Under heavy development.** Config schema, entity IDs, and behaviour may change without notice. Pin to a specific commit or tag if you need stability.

A lightweight Home Assistant network monitor for a home LAN running OpenWrt. A single Python integration SSHes into the router (and any access points) once per minute, collects the complete picture of the network, and exposes it to Home Assistant as sensors, binary sensors, and device trackers. Lovelace custom cards render device lists, topology maps, event logs, and dnsmasq DNS statistics.

Three topologies are supported:

- **Gateway + APs** — the full picture: DHCP leases, ARP/NDP, WAN, dnsmasq stats, Wi-Fi associations, per-host stats.
- **Gateway only** — one OpenWrt box that routes and does Wi-Fi. Enter its IP in the gateway field; it counts as both gateway *and* AP for Wi-Fi collection.
- **APs only** — OpenWrt access points behind a non-OpenWrt router. Leave gateway empty; devices are discovered from each AP's `ip neigh` on `br-lan`. WAN, DNS, and conntrack-bandwidth sensors are not created in this mode.

At least one host (gateway or AP) must be configured.

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
- **Host stats** — CPU%, RAM%, root disk%, hardware model, and board name for the gateway and each AP. The hardware model (from `ubus call system board`) is shown in the HA device registry for each host's sensor cluster.
- **WAN** — IPv4, IPv6, live RX/TX rate, cumulative byte totals.
- **DNS cache** — dnsmasq cache hit/miss counts and rates, per-upstream query counts and latency, weighted average upstream latency.
- **Event log** — HACS integration keeps the most recent 500 events in memory only (cleared on HA restart/reload). The manual `command_line` install keeps its separate JSONL event file.

All of this comes from a single 20 s SSH call to the gateway plus parallel SSH calls to each AP — no redundant shell sensors.

## Requirements

- **Home Assistant** — Python 3.11+ (built in on HA OS).
- **HACS** — install [HACS](https://hacs.xyz/) first; it's how wrtsensor is distributed.
- **OpenWrt** — tested on 25.12.2. Older releases may work but are untested. Stock packages are enough: `busybox`, `ip`, `iwinfo`, `dnsmasq`, `logread`.
- **Network** — SSH from HA host to every OpenWrt box (gateway + APs). Port 22 by default; any port works (configurable in the integration's Options).

## Installation

1. In Home Assistant, open **HACS → Integrations → ⋮ → Custom repositories** and add:
   ```
   https://github.com/magaxel/wrtsensor
   ```
   Category: **Integration**

2. Install **wrtsensor** from HACS and restart Home Assistant.

3. Go to **Settings → Devices & Services → Add Integration** and search for **wrtsensor**.

4. Enter your gateway IP (optional), SSH key path (default `/config/ssh/id_ed25519`), and any AP IPs (comma-separated). At least one of gateway or APs must be set — leaving gateway empty enables APs-only mode.

5. Done — wrtsensor creates one scanner sensor per config entry, plus dedicated WAN/DNS/host sensors where applicable. Their exact entity IDs depend on the entry title, so pick them from the entity picker in the UI rather than assuming a fixed `sensor.wrtsensor_*` name.

6. Add the Lovelace resources manually in **Settings → Dashboards → Resources**:

   - `/wrtsensor_static/network-list-card.js?v=1.0.0` — JavaScript Module
   - `/wrtsensor_static/network-table-card.js?v=1.0.0` — JavaScript Module
   - `/wrtsensor_static/network-topology-card.js?v=1.0.0` — JavaScript Module
   - `/wrtsensor_static/network-events-card.js?v=1.0.0` — JavaScript Module
   - `/wrtsensor_static/dns-stats-card.js?v=1.0.0` — JavaScript Module

> If key authentication isn't set up yet, the config flow will ask for a password once and provision the public key into `/etc/dropbear/authorized_keys` on each OpenWrt box for you. The password is never stored.

Prefer not to use HACS? See [docs/manual-install.md](docs/manual-install.md) for the `command_line` sensor path.

### Changing gateway or APs

Open the integration in **Settings → Devices & Services**, hit the triple-dot menu → **Reconfigure** to change gateway, APs, SSH key path, or SSH port in place. Every host is re-probed; if authentication fails, the flow prompts for a password and re-provisions the key, same as initial setup. Removing a host prunes its CPU/RAM/Disk sensors automatically on the next reload. The public SSH key stays in `/etc/dropbear/authorized_keys` on the removed device — delete it manually there if you no longer trust the host.

## Card configuration

Each wrtsensor config entry creates its own scanner entity. Use the entity picker in the card editor, or replace the example IDs below with the entity IDs created for your entry. Drop these snippets into a dashboard via **Raw configuration editor** or the **+ ADD CARD → Manual** editor.

### `network-list-card`

Compact, searchable, sortable device list. Each row expands to show Wi-Fi metrics, byte totals, first-seen, etc.

```yaml
type: custom:network-list-card
entity: sensor.my_router_network_scanner
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
entity: sensor.my_router_network_scanner
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
entity: sensor.my_router_network_scanner
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
entity: sensor.my_router_network_scanner
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
entity: sensor.my_router_network_scanner
title: DNS Cache
```

### `history-graph` — CPU

Per-host CPU % over time using HA's built-in graph card. Entity IDs follow `sensor.wrtsensor_<ip_with_underscores>_cpu`.

```yaml
type: history-graph
title: CPU
hours_to_show: 8
min_y_axis: 0            # lock axis to 0–100 so hosts are comparable
max_y_axis: 100
entities:
  - entity: sensor.wrtsensor_192_0_2_1_cpu
    name: Gateway
  - entity: sensor.wrtsensor_192_0_2_22_cpu
    name: AP1
  - entity: sensor.wrtsensor_192_0_2_23_cpu
    name: AP2
```

### `history-graph` — RAM

Same pattern as CPU, with `_ram` sensors.

```yaml
type: history-graph
title: RAM
hours_to_show: 8
min_y_axis: 0
max_y_axis: 100
entities:
  - entity: sensor.wrtsensor_192_0_2_1_ram
    name: Gateway
  - entity: sensor.wrtsensor_192_0_2_22_ram
    name: AP1
  - entity: sensor.wrtsensor_192_0_2_23_ram
    name: AP2
```

### `entities` — Storage

Disk use per host as a static list. Entity IDs follow `sensor.wrtsensor_<ip_with_underscores>_disk`.

```yaml
type: entities
title: Storage
entities:
  - entity: sensor.wrtsensor_192_0_2_1_disk
    icon: mdi:harddisk
    name: Gateway - Root
  - entity: sensor.wrtsensor_192_0_2_22_disk
    icon: mdi:harddisk
    name: AP1 - Root
```

In APs-only mode, only the AP rows appear.

## Entities

| Entity ID | What it is |
|-----------|-----------|
| `sensor.<entry>_network_scanner` | Main sensor for one config entry — device count as state, full JSON blob as attributes. |
| `sensor.<entry>_wan_download` | WAN RX rate in Mbit/s |
| `sensor.<entry>_wan_upload` | WAN TX rate in Mbit/s |
| `sensor.<entry>_dns_cache_hit` | DNS cache hit % |
| `sensor.<entry>_dns_latency` | Weighted upstream DNS latency in ms |
| `sensor.<host>_cpu` | CPU % per host, e.g. `sensor.192_0_2_1_cpu` |
| `sensor.<host>_ram` | RAM % per host |
| `sensor.<host>_disk` | Disk % per host |
| `binary_sensor.<entry>_presence_<mac>` | Online/offline per configured MAC (set in Options) |
| `device_tracker.<hostname>` | home/not_home tracker per discovered device — **disabled by default** |

Device tracker entities are named after the device hostname (e.g. `device_tracker.my_phone`) and are disabled by default. HA's scanner entity base class does this to avoid flooding the registry when dozens of devices are discovered. To use a tracker for Person presence, go to **Settings → Entities**, enable "Show disabled entities", find the device, and enable it. The entity can then be added to a Person.

## Network assumptions

The integration expects at least one OpenWrt host reachable over SSH (key-based auth). Gateway and APs are both optional on their own — but at least one must be set.

| Role | Address (example) |
|------|-------------------|
| Gateway — optional | `192.0.2.1` |
| AP — optional | `192.0.2.10` |
| AP — optional (more as needed) | `192.0.2.11`, `192.0.2.12`, … |
| LAN bridge | `br-lan` |
| WAN interface | `eth0` |

A single OpenWrt box that routes *and* does Wi-Fi counts as a gateway — enter its IP in the gateway field and leave the AP list empty.

WAN bandwidth, DNS cache, and conntrack-derived per-device bandwidth are only collected when a gateway is configured. In APs-only mode, devices are discovered via each AP's own `ip -4/-6 neigh show dev br-lan` output; DHCP hostnames are not available since the non-OpenWrt router holds them.

Replace the example addresses with whatever your LAN uses. The HACS integration multiplexes connections with asyncssh in-process; the standalone `diagnose.py` script uses `ControlMaster=auto / ControlPersist=60`.

## Recorder & logbook exclusion

The per-entry `*_network_scanner` entity carries a large JSON blob as attributes and changes every scan (every 60 s). Exclude the specific entity created for your entry in `configuration.yaml` to keep the database and logbook trim:

```yaml
recorder:
  exclude:
    entities:
      - sensor.my_router_network_scanner

logbook:
  exclude:
    entities:
      - sensor.my_router_network_scanner
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
