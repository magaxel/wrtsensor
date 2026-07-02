# wrtsensor

> 🤖 **100% vibe coded.** Every line of this project was written by an LLM.
> Still under heavy development — config schema and entity IDs may change without
> notice; pin a commit or tag if you need stability.

A lightweight Home Assistant network monitor for a home LAN running OpenWrt. One
Python integration SSHes into the router (and any APs/switches) once per minute,
collects the full picture of the network, and exposes it to Home Assistant as
sensors, binary sensors, and device trackers. Bundled Lovelace cards render device
lists, topology maps, event logs, and dnsmasq DNS stats.

You give it a single comma-separated list of OpenWrt device IPs; it **autodetects each
device's role** — gateway, access point, or managed switch — on every reload, so you
never sort them yourself. Detection is hardware-independent: the gateway is the box with
the internet uplink (an `up` `wan` interface, corroborated by every other box routing its
default traffic through it), access points are non-gateway boxes with Wi-Fi, and switches
are the wired-only rest. The gateway's LAN bridge and WAN interface are autodetected too.
Any mix works; each role is optional on its own, but at least one host must be configured:

- **Gateway + APs** — the full picture: DHCP leases, ARP/NDP, WAN, dnsmasq stats,
  Wi-Fi associations, per-host stats.
- **Gateway only** — one box that routes *and* does Wi-Fi; detected as the gateway and
  counts as gateway *and* AP for Wi-Fi collection.
- **APs only** — OpenWrt APs behind a non-OpenWrt router. With no OpenWrt gateway in the
  list, none is detected as gateway; devices come from each AP's `ip neigh` on `br-lan`;
  WAN/DNS/conntrack sensors are not created.
- **Switches** — OpenWrt managed switches (e.g. a Zyxel GS1900). Each switch's bridge
  forwarding table (`bridge fdb`) reports **which switch port** a wired device is on.

If autodetection ever picks the wrong role, append `=gateway`, `=ap`, or `=switch` to that
IP (e.g. `192.0.2.5=switch`). Switch ports show in the `network-table-card` **Port/AP**
column and as the `switch_port` attribute on each device; `switch_host` identifies which
detected switch reported the access port.

## What it collects

Per scan (every 60 s) the integration produces one JSON object. Every block can be
toggled in **Settings → Devices & Services → wrtsensor → Configure** — disabling a
feature drops both its SSH-side commands and its HA entities. Initial setup creates
the full default set; a "DNS-only" or "WireGuard-only" install is just a matter of
toggling the rest off after first run.

- **Network hosts** *(default on)* — MAC, IPv4/IPv6, hostname, vendor (OUI), connection
  type, switch port, online status, plus per-client device_trackers and presence
  binary sensors. Required by the topology, table, and events cards.
- **Wi-Fi metrics** — AP, band, signal, noise, SNR, TX/RX PHY rates, expected
  throughput, per-station byte counters. Tied to Network hosts.
- **Host stats** *(default on)* — CPU%, RAM%, root disk%, hardware model, board name
  per configured host.
- **WAN bandwidth** *(default on)* — gateway WAN RX/TX rate and byte totals.
- **DNS cache** *(default on)* — dnsmasq hit/miss counts over 24h/8h/1h/last-scan,
  per-window upstream query counts and latency. Powers `dns-stats-card`.
- **WireGuard** *(default off)* — per-peer endpoint, allowed IPs, last-handshake age,
  transfer counters, live throughput, online state. Private/preshared keys are never
  read.
- **Firmware updates** *(default off)* — one HA `update` entity per host backed by
  `owut`, reporting installed vs. latest OpenWrt version with a LuCI upgrade link.
- **Event log** — most recent 500 events in memory (cleared on restart/reload). Tied
  to Network hosts.

All enabled blocks come from a single SSH call to the gateway plus parallel calls to
each AP and switch — no redundant shell sensors.

## Requirements

- **Home Assistant** — Python 3.11+ (built in on HA OS).
- **HACS** — install [HACS](https://hacs.xyz/) first; it's how wrtsensor is distributed.
- **OpenWrt** — tested on 25.12.2. Stock packages are enough: `busybox`, `ip`,
  `iwinfo`, `dnsmasq`, `logread`.
- **Network** — SSH (key-based) from HA to every OpenWrt box. Port 22 by default; add
  the port inline for custom ports, e.g. `192.0.2.1:2222` or `[2001:db8::1]:2222`.

## Installation

1. In Home Assistant, open **HACS → Integrations → ⋮ → Custom repositories** and add
   `https://github.com/magaxel/wrtsensor` with category **Integration**.

2. Install **wrtsensor** from HACS and restart Home Assistant.

3. Go to **Settings → Devices & Services → Add Integration** and search for **wrtsensor**.

4. Enter a comma-separated list of every OpenWrt device IP in the single **OpenWrt
   device IPs** field, plus the SSH key path (default `/config/ssh/id_ed25519`). Roles
   are detected automatically; the next screen shows the detected roles to confirm.
   Append `:port` for a custom SSH port and `=gateway`/`=ap`/`=switch` to override a
   detected role. At least one host must be set; with no OpenWrt gateway in the list the
   integration runs in APs-only / switch-only mode.

5. wrtsensor creates one scanner sensor per config entry, plus dedicated
   WAN/DNS/host sensors where applicable. Entity IDs depend on the entry title, so
   pick them from the entity picker rather than assuming a fixed name.

6. Add the Lovelace resources in **Settings → Dashboards → Resources** (all
   JavaScript Modules):

   - `/wrtsensor_static/network-table-card.js?v=2.4.0`
   - `/wrtsensor_static/network-topology-card.js?v=1.2.1`
   - `/wrtsensor_static/network-events-card.js?v=1.1.2`
   - `/wrtsensor_static/dns-stats-card.js?v=3.0.1`
   - `/wrtsensor_static/wireguard-card.js?v=1.1.0` *(only if WireGuard is enabled)*

> If key auth isn't set up yet, the config flow asks for a password once and
> provisions the public key into `/etc/dropbear/authorized_keys` on each box. The
> password is never stored.

Prefer not to use HACS? See [docs/manual-install.md](docs/manual-install.md) for the
`command_line` sensor path.

**Changing hosts:** open **Configure** to edit the single **OpenWrt device IPs** field
(plus the SSH key or any option) in place. Roles are re-detected on save; append
`=gateway`/`=ap`/`=switch` to an IP to override a detected role. Every host is
re-probed; removing a host prunes its sensors on the next reload. The public key is
left in `authorized_keys` on a removed device — delete it there manually if you no
longer trust the host.

**Removing the entry:** deleting the last wrtsensor entry unloads its entities and
clears the runtime cache in `/dev/shm` and `/tmp/netscan`. The OUI vendor database is
kept in the HA config dir so re-adding doesn't re-download it.

## Cards

Bundled Lovelace cards — full YAML examples and options in
[docs/cards.md](docs/cards.md):

- **`network-table-card`** — wide tabular device list with filterable columns.
  Infra rows (gateway/AP/switch) whose SSH probe failed this cycle are dimmed
  and filtered like any other offline device, keyed off `host_stats.available`.
- **`network-topology-card`** — SVG map of the real physical hierarchy: router
  on top, switch(es) beneath it, APs beneath whichever switch they're actually
  plugged into (or the router directly), and clients attached to whichever
  node they're actually on. Auto-detected each poll from `bridge fdb show`
  data already collected from every host — no manual wiring config. An infra
  node whose SSH probe failed this cycle renders dimmed/offline, same as an
  offline client — even if it's still visible over ARP.
- **`network-events-card`** — filterable connect/disconnect/roam/ip_change log.
- **`dns-stats-card`** — dnsmasq cache hit/miss and upstream latency.
- **`wireguard-card`** — per-peer WireGuard tunnel status.
- **Host metrics & firmware** — `history-graph` (CPU/RAM) and `entities` (storage)
  snippets, plus the built-in `update` tiles for firmware. See
  [docs/cards.md](docs/cards.md).

## Entities

Each toggle in Options owns a set of entities — turn the toggle off and they're
removed from the registry on reload.

| Entity ID | Owning option | What it is |
|-----------|---------------|-----------|
| `sensor.<entry>_network_scanner` | Track LAN/Wi-Fi clients | Device count as state; `devices` (with `switch_port`/`switch_host`), `wan_ip`, `wan_ip6`, `gateway_mac`, `host_names`, `ap_hosts`, `ap_names`, `switch_hosts`, `switch_names`, `host_stats`, `host_topology`, `partial`, `scan_duration` as attributes. Powers the topology, table, and events cards. |
| `device_tracker.<hostname>` | Track LAN/Wi-Fi clients | home/not_home per discovered device — **disabled by default** |
| `binary_sensor.<entry>_presence_<mac>` | Track LAN/Wi-Fi clients | Online/offline per configured MAC |
| `sensor.<entry>_wan_download` / `_wan_upload` | Collect WAN bandwidth | WAN RX / TX rate in Mbit/s |
| `sensor.<entry>_dns_cache_hit_pct` | Collect DNS stats | DNS cache hit %; `dns_stats` blob powers `dns-stats-card` |
| `sensor.<entry>_dns_latency` | Collect DNS stats | Weighted upstream DNS latency in ms |
| `sensor.wrtsensor_<host>_cpu` / `_ram` / `_disk` | Collect host metrics | CPU / RAM / disk % per host |
| `sensor.<entry>_wireguard` | Show WireGuard connections | Live peer count; `wireguard` blob powers `wireguard-card` |
| `device_tracker.<entry>_wgpeer_<id>` | Show WireGuard connections | home/not_home per WG peer |
| `update.<entry>_<host>_firmware` | Check for OpenWrt firmware updates | Per-host firmware update backed by `owut check` |

Device trackers are named after the device hostname (e.g. `device_tracker.my_phone`)
and are disabled by default. To use one for Person presence, enable it via **Settings
→ Entities → Show disabled entities**.

## Network assumptions

At least one OpenWrt host must be reachable over SSH (key-based auth). Roles are
autodetected, so you just list IPs. A single box that routes *and* does Wi-Fi is
detected as the gateway and also collects its own Wi-Fi.

| Field | Value (example) |
|------|-------------------|
| OpenWrt device IPs | `192.0.2.1,192.0.2.10,192.0.2.11,192.0.2.24` |
| Optional role override | `192.0.2.24=switch` |
| LAN bridge | autodetected (e.g. `br-lan`) |
| WAN interface | autodetected (e.g. `eth0`) |

The LAN bridge and WAN interface are autodetected from the gateway; leave the matching
options blank unless you need to override them. WAN bandwidth, DNS cache, and
conntrack-derived per-device bandwidth are only collected when a gateway is detected. In APs-only mode, devices are discovered via
each AP's `ip -4/-6 neigh show dev br-lan`; DHCP hostnames are unavailable since the
non-OpenWrt router holds them. Configured OpenWrt gateway, AP, and switch devices
use their own `ubus call system board` hostname when available, including
switch-only topologies.

## Recorder & logbook exclusion

The `*_network_scanner` entity carries a large JSON blob and changes every 60 s.
Exclude it to keep the database and logbook trim:

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

**"Failed to connect"** — SSH key path is wrong/unreadable by HA, or the key isn't in
OpenWrt's `authorized_keys`. Use the config flow's password-provisioning step, or set
the key up manually (see [manual install — SSH key](docs/manual-install.md#1-ssh-key--ha--openwrt)).

**APs logged as "unreachable"** — APs must be reachable from HA over SSH with the same
key. For custom ports enter `192.0.2.10:2222` or `[2001:db8::10]:2222`. Test with
`ssh -i /config/ssh/id_ed25519 -p <port> root@<ap-ip> uptime`.

**Card missing / "Custom element doesn't exist"** — the card's JS resource isn't
loaded. Add each card URL under **Settings → Dashboards → Resources**, and bump the
`?v=` query to bust the app's JS cache after updates.

For manual-install specifics see
[docs/manual-install.md#troubleshooting](docs/manual-install.md#troubleshooting).

## Additional docs

- [Cards & dashboards](docs/cards.md) — full YAML for every bundled card.
- [Manual installation](docs/manual-install.md) — command_line sensor path without HACS.
- [Development](docs/development.md) — lint/test commands, repository layout, CI.

## License

MIT — see [LICENSE](LICENSE). Use it, fork it, sell it; just keep the copyright notice.
