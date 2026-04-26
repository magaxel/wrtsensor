"""Pure parsing functions for wrtsensor data sources.

All functions are stateless and have no HA or SSH dependencies, making them
straightforward to unit-test without a running Home Assistant instance.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from typing import Any

_IP_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")


def _valid_ipv4(ip: str) -> bool:
    if not _IP_RE.match(ip):
        return False
    return all(0 <= int(o) <= 255 for o in ip.split("."))


def _is_random_mac(mac: str) -> bool:
    """Return True if MAC is locally administered (random/private).

    Bit 1 of the first octet being set indicates a locally administered address,
    which modern OSes use for MAC randomisation (e.g. 2E:xx, 4A:xx, 72:xx).
    """
    try:
        return bool(int(mac.split(":")[0], 16) & 0x02)
    except (ValueError, IndexError):
        return False


def parse_leases(lines: list[str]) -> dict[str, dict[str, str]]:
    result = {}
    for line in lines:
        parts = line.split()
        if len(parts) >= 4:
            mac = parts[1].upper()
            ip = parts[2]
            hostname = "" if parts[3] == "*" else parts[3]
            result[mac] = {"ip": ip, "hostname": hostname}
    return result


def parse_arp(lines: list[str]) -> tuple[dict[str, str], set[str], dict[str, str]]:
    states: dict[str, str] = {}
    stale: set[str] = set()
    arp_ips: dict[str, str] = {}
    for line in lines:
        tokens = line.split()
        if not tokens:
            continue
        ip = tokens[0]
        mac = None
        state = tokens[-1]
        for i, t in enumerate(tokens):
            if t == "lladdr" and i + 1 < len(tokens):
                mac = tokens[i + 1].upper()
                break
        if mac and state != "FAILED":
            states[mac] = state
            if _valid_ipv4(ip):
                arp_ips[mac] = ip
            if state == "STALE":
                stale.add(mac)
    return states, stale, arp_ips


def parse_ndp(lines: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in lines:
        tokens = line.split()
        if len(tokens) < 4:
            continue
        addr = tokens[0]
        mac = None
        for i, t in enumerate(tokens):
            if t == "lladdr" and i + 1 < len(tokens):
                mac = tokens[i + 1].upper()
                break
        state = tokens[-1] if tokens else ""
        if not mac or state == "FAILED":
            continue
        try:
            ip6_obj = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if not (ip6_obj.is_link_local or ip6_obj.is_loopback or ip6_obj.is_multicast):
            existing = result.get(mac)
            if existing is None:
                result[mac] = addr
            elif not ip6_obj.is_private:
                result[mac] = addr
    return result


def parse_hoststat(lines: list[str]) -> dict[str, Any] | None:
    if len(lines) < 2:
        return None
    cpu_parts = lines[0].split()
    if len(cpu_parts) < 5 or cpu_parts[0] != "cpu":
        return None
    try:
        user, nice, system, idle = (int(cpu_parts[i]) for i in range(1, 5))
    except ValueError:
        return None
    mem_parts = lines[1].split()
    try:
        mem_total = int(mem_parts[0])
        mem_avail = int(mem_parts[1]) if len(mem_parts) > 1 else 0
    except (ValueError, IndexError):
        return None
    disk = None
    if len(lines) >= 3 and lines[2].strip():
        try:
            disk = int(lines[2].strip())
        except ValueError:
            disk = None
    return {
        "busy": user + nice + system,
        "idle": idle,
        "mem_total": mem_total,
        "mem_avail": mem_avail,
        "disk": disk,
    }


def parse_wifi_output(out: str, ap_name: str) -> tuple[list[dict[str, Any]], list[str]]:
    entries: list[dict[str, Any]] = []
    hoststat: list[str] = []
    for line in out.splitlines():
        if line.startswith("STAT|"):
            stat_parts = line.split("|")
            if len(stat_parts) >= 4:
                hoststat = [stat_parts[1], f"{stat_parts[2]} {stat_parts[3]}"]
                if len(stat_parts) >= 5:
                    hoststat.append(stat_parts[4])
            continue
        parts = line.split("|")
        if len(parts) < 3:
            continue
        mac = parts[0].strip().upper()
        if not mac:
            continue

        def _int(idx: int) -> int | None:
            try:
                return int(parts[idx]) if len(parts) > idx and parts[idx] else None
            except ValueError:
                return None

        def _float(idx: int) -> float | None:
            try:
                return float(parts[idx]) if len(parts) > idx and parts[idx] else None
            except ValueError:
                return None

        entries.append(
            {
                "mac": mac,
                "ap": ap_name,
                "band": parts[1] if len(parts) > 1 else "",
                "essid": parts[2] if len(parts) > 2 else "",
                "signal": _int(3),
                "tx_rate": _float(4),
                "channel": _int(5),
                "sta_ul_bytes": _int(6) or 0,
                "sta_dl_bytes": _int(7) or 0,
                "noise": _int(8),
                "snr": _int(9),
                "rx_rate": _float(10),
                "exp_tput": _float(11),
            }
        )
    return entries, hoststat


def parse_board_model(out: str) -> tuple[str, str]:
    """Extract (model, board_name) from collector output containing a BOARD| line."""
    for line in out.splitlines():
        if line.startswith("BOARD|"):
            try:
                board = json.loads(line[6:])
                return board.get("model", ""), board.get("board_name", "")
            except (ValueError, KeyError):
                return "", ""
    return "", ""


def parse_dns_stats(lines: list[str]) -> dict[str, Any] | None:
    last_qf_idx = -1
    for i, line in enumerate(lines):
        if re.search(r"queries forwarded \d+,\s*queries answered locally \d+", line):
            last_qf_idx = i
    if last_qf_idx < 0:
        return None
    cache_size = hits = misses = None
    lat_weighted_sum = 0.0
    lat_weight = 0
    servers: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        m = re.search(r"cache size (\d+)", line)
        if m:
            cache_size = int(m.group(1))
        m = re.search(
            r"queries forwarded (\d+),\s*queries answered locally (\d+)", line
        )
        if m:
            misses = int(m.group(1))
            hits = int(m.group(2))
        if i <= last_qf_idx:
            continue
        m = re.search(
            r"server (\S+): queries sent (\d+)(?:.*?avg\. latency (\d+)ms)?", line
        )
        if m:
            addr = m.group(1)
            q = int(m.group(2))
            lat = int(m.group(3)) if m.group(3) else None
            servers.append({"addr": addr, "queries": q, "latency_ms": lat})
            if lat is not None and q > 0:
                lat_weighted_sum += q * lat
                lat_weight += q
    if hits is None or misses is None:
        return None
    result: dict[str, Any] = {
        "cache_size": cache_size or 0,
        "hits": hits,
        "misses": misses,
        "servers": servers,
    }
    if lat_weight > 0:
        result["latency_ms"] = round(lat_weighted_sum / lat_weight, 1)
    return result


_WG_UCI_ALLOWED_OPTIONS = (
    "description",
    "public_key",
    "allowed_ips",
    "endpoint_host",
    "endpoint_port",
)


def wg_peer_id(host: str, iface: str, public_key: str) -> str:
    """Stable opaque peer id used for device_tracker unique_id.

    Hashed so registry IDs and attributes never embed the raw pubkey.
    """
    digest = hashlib.sha1(f"{host}|{iface}|{public_key}".encode()).hexdigest()
    return digest[:16]


def _split_kv_line(line: str) -> tuple[str, str] | None:
    parts = line.split(None, 1)
    if len(parts) != 2:
        return None
    return parts[0], parts[1].strip()


def parse_wg_show_sections(
    host: str, sections: dict[str, list[str]], stale_threshold_s: int, now: float
) -> list[dict[str, Any]]:
    """Stitch the per-subcommand `wg show` outputs into structured interfaces.

    Expected sections (produced by the SSH command in coordinator._get_wireguard_info):

      WG_INTERFACES                     -> one iface name per line
      WG_IFACE <iface>                  -> 2 lines: <public-key>, <listen-port>
      WG_PEERS <iface>                  -> one pubkey per line
      WG_ENDPOINTS <iface>              -> "<pubkey>\\t<endpoint|(none)>"
      WG_ALLOWED_IPS <iface>            -> "<pubkey>\\t<csv-or-(none)>"
      WG_HANDSHAKES <iface>             -> "<pubkey>\\t<epoch>"
      WG_TRANSFER <iface>               -> "<pubkey>\\t<rx>\\t<tx>"
      WG_KEEPALIVE <iface>              -> "<pubkey>\\t<seconds|off>"

    None of these subcommands ever emit private or preshared keys, so this parser
    has no secret-stripping logic — by design, secrets are blocked at the source.
    """
    iface_names = [ln.strip() for ln in sections.get("WG_INTERFACES", []) if ln.strip()]
    interfaces: list[dict[str, Any]] = []
    for iface in iface_names:
        meta = [
            ln.strip() for ln in sections.get(f"WG_IFACE {iface}", []) if ln.strip()
        ]
        if len(meta) < 2:
            continue
        public_key = meta[0]
        listen_port_raw = meta[1]
        try:
            listen_port = int(listen_port_raw)
        except ValueError:
            listen_port = None

        peer_keys = [
            ln.strip() for ln in sections.get(f"WG_PEERS {iface}", []) if ln.strip()
        ]

        endpoints: dict[str, str | None] = {}
        for raw in sections.get(f"WG_ENDPOINTS {iface}", []):
            kv = _split_kv_line(raw)
            if kv is None:
                continue
            pk, val = kv
            endpoints[pk] = None if val == "(none)" else val

        allowed_map: dict[str, list[str]] = {}
        for raw in sections.get(f"WG_ALLOWED_IPS {iface}", []):
            kv = _split_kv_line(raw)
            if kv is None:
                continue
            pk, val = kv
            if val == "(none)":
                allowed_map[pk] = []
            else:
                allowed_map[pk] = [v.strip() for v in val.split(",") if v.strip()]

        handshakes: dict[str, int] = {}
        for raw in sections.get(f"WG_HANDSHAKES {iface}", []):
            kv = _split_kv_line(raw)
            if kv is None:
                continue
            pk, val = kv
            try:
                handshakes[pk] = int(val.strip())
            except ValueError:
                continue

        transfer: dict[str, tuple[int, int]] = {}
        for raw in sections.get(f"WG_TRANSFER {iface}", []):
            cols = raw.split()
            if len(cols) != 3:
                continue
            pk, rx_s, tx_s = cols
            try:
                transfer[pk] = (int(rx_s), int(tx_s))
            except ValueError:
                continue

        keepalive: dict[str, int | None] = {}
        for raw in sections.get(f"WG_KEEPALIVE {iface}", []):
            kv = _split_kv_line(raw)
            if kv is None:
                continue
            pk, val = kv
            if val == "off":
                keepalive[pk] = None
            else:
                try:
                    keepalive[pk] = int(val)
                except ValueError:
                    keepalive[pk] = None

        peers: list[dict[str, Any]] = []
        for pk in peer_keys:
            last_hs = handshakes.get(pk, 0)
            rx_b, tx_b = transfer.get(pk, (0, 0))
            online = last_hs > 0 and (now - last_hs) <= stale_threshold_s
            peers.append(
                {
                    "id": wg_peer_id(host, iface, pk),
                    "public_key": pk,
                    "endpoint": endpoints.get(pk),
                    "allowed_ips": allowed_map.get(pk, []),
                    "last_handshake": last_hs,
                    "rx_bytes": rx_b,
                    "tx_bytes": tx_b,
                    "persistent_keepalive_s": keepalive.get(pk),
                    "online": online,
                }
            )

        interfaces.append(
            {
                "host": host,
                "name": iface,
                "public_key": public_key,
                "listen_port": listen_port,
                "peers": peers,
            }
        )
    return interfaces


def parse_wg_uci(lines: list[str]) -> dict[str, dict[str, str]]:
    """Parse awk-filtered `uci -q show network` lines into pubkey -> metadata.

    Format per line: `network.<section>.<option>=<value>` (value may be quoted).
    The awk filter in the SSH command already restricts options to the allowlist,
    but we apply the same allowlist here as defence in depth so unfiltered input
    (e.g. a future code change) cannot leak forbidden keys.
    """
    by_section: dict[str, dict[str, str]] = {}
    for raw in lines:
        line = raw.strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key_parts = key.split(".")
        if len(key_parts) != 3:
            continue
        _, section, option = key_parts
        if option not in _WG_UCI_ALLOWED_OPTIONS:
            continue
        v = value.strip()
        if len(v) >= 2 and v[0] == "'" and v[-1] == "'":
            v = v[1:-1]
        elif len(v) >= 2 and v[0] == '"' and v[-1] == '"':
            v = v[1:-1]
        by_section.setdefault(section, {})[option] = v

    by_pubkey: dict[str, dict[str, str]] = {}
    for fields in by_section.values():
        pk = fields.get("public_key")
        if not pk:
            continue
        entry: dict[str, str] = {}
        if "description" in fields:
            entry["name"] = fields["description"]
        by_pubkey[pk] = entry
    return by_pubkey


def parse_conntrack(lines: list[str]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for line in lines:
        if not line.startswith("ipv4"):
            continue
        srcs = re.findall(r"src=(\S+)", line)
        bytes_vals = re.findall(r"bytes=(\d+)", line)
        if len(srcs) < 1 or len(bytes_vals) < 2:
            continue
        orig_src = srcs[0]
        if not _valid_ipv4(orig_src):
            continue
        try:
            if not ipaddress.ip_address(orig_src).is_private:
                continue
        except ValueError:
            continue
        entry = result.setdefault(orig_src, {"ul": 0, "dl": 0})
        entry["ul"] += int(bytes_vals[0])
        entry["dl"] += int(bytes_vals[1])
    return result
