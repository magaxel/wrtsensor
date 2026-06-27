# Card & dashboard reference

Full configuration for the bundled Lovelace cards and the dashboard snippets for
host metrics and firmware updates. See the [README](../README.md) for install and
the short card overview.

Each wrtsensor config entry creates its own scanner entity. Use the entity picker in
the card editor, or replace the example IDs below with the entity IDs created for
your entry. Drop these snippets into a dashboard via **Raw configuration editor** or
the **+ ADD CARD → Manual** editor.

## `network-table-card`

Wide tabular device list — better for widescreen dashboards.

```yaml
type: custom:network-table-card
entity: sensor.my_router_network_scanner
title: Network
show_offline: false
show_unknown: true       # false = hide devices with no confirmed AP/switch path
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

The `ap` column is displayed as **Port/AP**. It shows the AP name for Wi-Fi clients
and `Port <number>` for wired clients learned from a configured switch. Devices
with no current Wi-Fi station match and no switch-port attribution show as
**Unknown** unless `show_unknown: false` is set. Existing dashboards that still list
`switch_port` are mapped to `ap` automatically by the card.

## `network-topology-card`

SVG topology map showing the gateway, switches, APs, and clients. Configured APs are
shown even when the AP itself is not present in DHCP/ARP device discovery.
When only one AP is configured, Wi-Fi clients with an unmatched AP hostname are
attached to that AP so AP-to-client links still render if host metrics are disabled
or temporarily unavailable. Devices without a current Wi-Fi station match and
without switch-port attribution connect directly to the router when a gateway is
configured. In gateway-less topologies they render under an **Unknown** node
unless `show_unknown: false` is set. The integration exposes `host_names`,
`ap_names`, and `switch_names` so configured infrastructure IPs can be matched to
OpenWrt hostnames reported by the collector, including switch-only maps.
Configured OpenWrt gateway, AP, and switch nodes prefer the hostname configured
on that device over DHCP or DNS names seen elsewhere.

```yaml
type: custom:network-topology-card
entity: sensor.my_router_network_scanner
title: Network Map
gateway_label: gw        # fallback label shown when gateway device is missing
column_width: 200        # px between AP columns
show_offline: false
show_unknown: true            # false = hide devices with no confirmed AP/switch path
show_hostnames: true          # show device hostnames; false leaves only selected address rows
show_ipv4: true               # show IPv4 address rows and public IPv4 beside the gateway
show_ipv6: false              # show IPv6 address rows and public IPv6 beside the gateway
sort_wireless_by_signal: false   # true = order wireless clients by signal (strongest under the AP) instead of hostname
show_wireguard_peers: false      # true = draw WireGuard peers above Internet
show_offline_wireguard: true     # include offline WireGuard peers dimmed
wireguard_entity: null           # optional sensor override if auto-detect is wrong
```

WireGuard peers are auto-detected from a sibling `sensor.*_wireguard` entity on the
same wrtsensor device. If auto-detect cannot choose a sensor, set `wireguard_entity`
explicitly. The editor shows the WireGuard controls when a sensor is discoverable or
an override is configured; if the sensor reports `available: false`, peers appear
after the next successful WireGuard scan.

## `network-events-card`

Filterable event log. Groups events by date, shows per-day count, live search,
type-filter buttons.

```yaml
type: custom:network-events-card
entity: sensor.my_router_network_scanner
title: Network Events
max_height: 560
show_search: true        # hide search bar by setting false
show_filters: true       # hide type-filter pill row by setting false
shown_types:             # optional — limit to specific event types; [] shows none
  - connect
  - disconnect
  - roam
  - ip_change
```

## `dns-stats-card`

dnsmasq cache statistics. The main hit/miss bar defaults to the last 24 hours. If
wrtsensor does not have enough history for the selected period yet, the card shows
the available clean collection window instead, such as `collected for 3h`. Upstream
server rows use the first available per-server counter baseline inside that same
clean window, so they can appear even when older hit/miss history predates server
tracking.

The "Upstream latency" value and the per-upstream `· N ms` next to each server
follow the configured `period:` — they are means of the per-sample dnsmasq latency
reports across the window. `last_scan` shows the latest sample only.

Requires the **Collect DNS stats** option (on by default). When the option is off,
or when no gateway is configured, the card displays a short message pointing back at
the integration options instead of a chart.

```yaml
type: custom:dns-stats-card
entity: sensor.my_router_network_scanner
title: DNS Cache
period: last_24h     # last_24h, last_8h, last_1h, or last_scan
show_ipv6: false     # hide IPv6 upstream rows by default
max_servers: 8
```

DNS period values are exposed under `dns_stats.last_24h`, `dns_stats.last_8h`,
`dns_stats.last_1h`, and `dns_stats.last_scan`. Per-period upstream latency and
servers live under each window, e.g. `dns_stats.last_1h.latency_ms` and
`dns_stats.last_24h.servers`. The `sensor.<entry>_dns_latency` entity reports the
`last_scan` window. `last_scan` rates are intentionally hidden and display as `—`
because one-scan rates are noisy.

## `wireguard-card`

Per-peer WireGuard tunnel status. Requires the **Show WireGuard connections** option
(off by default; turn it on under **Settings → Devices & Services → wrtsensor →
Configure**). The card reads from the `sensor.<entry>_wireguard` entity that the
integration creates when the option is on. Each peer renders as a collapsible row
showing a green/grey status dot and its name (from the UCI `option description` if
set, else the first 8 chars of the public key); tapping or pressing Enter expands the
row to show endpoint, allowed IPs, last-handshake age, RX/TX totals, and live rate.
Stale peers (no handshake within the configured idle timeout, default 180 s) are
greyed out.

Set `max_peers` (default `0` = unlimited) to cap how many peers are shown at once.
When the total peer count across all interfaces exceeds the limit, prev/next arrows
appear at the bottom of the card and you can also swipe left/right on touch devices
to page. Pagination is global across interfaces — when an interface's peers cross a
page boundary they are split between pages, so the page boundaries follow the global
peer order rather than per-interface counts.

```yaml
type: custom:wireguard-card
entity: sensor.my_router_wireguard
max_peers: 0
```

Each peer also gets its own `device_tracker` entity with a stable `unique_id` of
`<entry_id>_wgpeer_<sha1[:16]>`. State is `home` when the peer's last handshake is
within the idle timeout, `not_home` otherwise — useful for presence automations
driven by VPN state. HA picks the slugified entity_id; pick the entity from the
picker rather than guessing the slug.

Security: wrtsensor never reads WireGuard private or preshared keys. Collection uses
`wg show <iface> <subcommand>` queries (`public-key`, `listen-port`, `peers`,
`endpoints`, `allowed-ips`, `latest-handshakes`, `transfer`, `persistent-keepalive`)
and an awk-filtered `uci -q show network` that drops every option name except
`description`, `public_key`, `allowed_ips`, `endpoint_host`, `endpoint_port`.
`wg show all dump`, `wg-quick`, `cat /etc/config/network`, and `cat
/etc/wireguard/*` are never executed.

## Firmware updates (Attended Sysupgrade)

One HA `update` entity per OpenWrt host, surfaced through HA's standard **Settings →
Updates** dashboard and the built-in update tile — no extra Lovelace card needed.
Off by default; turn on **Check for OpenWrt firmware updates** under **Settings →
Devices & Services → wrtsensor → Configure**.

Each host needs the `owut` tool. It is optional on OpenWrt 24.10 and included by
default on OpenWrt 25.12 images for devices with larger flash storage, but smaller
devices may still need it installed manually:

```sh
# OpenWrt 24.10
opkg update && opkg install owut

# OpenWrt 25.12 / main
apk -U add owut
```

When enabled, a background task per HA instance probes one host at a time on a slow
rotation (default every 6 h, configurable from 1 h to 24 h via the **Firmware check
interval** option). The probe runs `owut check` on the device, which round-trips to
`https://sysupgrade.openwrt.org` and takes 5–20 s — it does not block the 60 s scan
tick.

Each entity exposes:

- `installed_version` and `latest_version` — normalised OpenWrt version strings
  including revision (e.g. `24.10.1 r28597`). The build hash suffix (`-6df6e6c8a4`)
  is dropped so two builds of the same revision compare equal, but the revision
  number is kept so same-release revision upgrades surface as available.
- `release_url` and the `luci_url` attribute — both point at
  `http://<host>/cgi-bin/luci/admin/system/attendedsysupgrade/overview` on that
  device. Click the link in the entity's More Info dialog to perform the actual
  upgrade through LuCI; LuCI auto-redirects through its login page if you are not
  authenticated.
- `release_summary` — the headline status from `owut`, e.g. `It is safe to proceed
  with an upgrade` or `no changes, upgrade not necessary`.
- Attributes: `tool` (`owut` / `none` / `unknown`), `error` (set when owut is missing
  or the ASU server returned errors), `installed_version_raw` (the full `OpenWrt
  24.10.1 r28597-...` string).

Hosts without `owut` installed stay unavailable until the package is added; a single
INFO log per host describes the install command. The integration never installs
packages on the device.

## `history-graph` — CPU

Per-host CPU % over time using HA's built-in graph card. Requires the **Collect host
metrics** option (on by default). Host metric entities use the suggested ID pattern
`sensor.wrtsensor_<ip_with_underscores>_cpu`.

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

## `history-graph` — RAM

Same pattern as CPU, with `_ram` sensors. Requires the **Collect host metrics** option.

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

## `entities` — Storage

Disk use per host as a static list. Requires the **Collect host metrics** option.
Host metric entities use the suggested ID pattern
`sensor.wrtsensor_<ip_with_underscores>_disk`.

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

Without a gateway, only the configured AP and switch infrastructure rows appear.
