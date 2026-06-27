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

from .const import FDB_UPLINK_MAC_THRESHOLD

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
        if line.startswith("FDB|"):
            # Forwarding-DB lines are handled by parse_fdb; never a wifi station.
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


def parse_fdb(out: str) -> dict[str, str]:
    """Map MAC -> bridge port netdev from collector ``FDB|`` lines.

    Format per line is ``FDB|<MAC>|<port_netdev>`` (e.g. ``FDB|AA:..|lan5``).
    Last writer wins if a MAC appears on multiple ports of a single host.
    """
    result: dict[str, str] = {}
    for line in out.splitlines():
        if not line.startswith("FDB|"):
            continue
        parts = line.split("|")
        if len(parts) >= 3:
            mac = parts[1].strip().upper()
            port = parts[2].strip()
            if mac and port:
                result[mac] = port
    return result


def _port_number(port: str) -> str:
    """Display label for a bridge port: trailing digits of the netdev name.

    ``lan5`` -> ``5``, ``lan24`` -> ``24``; falls back to the raw name when
    there are no trailing digits.
    """
    m = re.search(r"(\d+)$", port)
    return m.group(1) if m else port


def resolve_switch_ports(
    fdb_by_host: dict[str, dict[str, str]],
    switch_hosts: set[str] | None = None,
    uplink_threshold: int = FDB_UPLINK_MAC_THRESHOLD,
) -> dict[str, dict[str, str]]:
    """Resolve each MAC to the access port it is physically connected to.

    A MAC is learned both on its real access port and on the uplink/trunk ports
    of every other OpenWrt device. Ports carrying more than ``uplink_threshold``
    MACs are treated as uplinks and ignored. Among the remaining candidates the
    port with the fewest MACs (the most specific access port) wins, preferring a
    designated switch host on ties.

    Returns ``mac -> {"port": display_port, "host": switch_host}``.
    """
    switch_hosts = switch_hosts or set()
    port_macs: dict[tuple[str, str], set[str]] = {}
    for host, fdb in fdb_by_host.items():
        for mac, port in fdb.items():
            port_macs.setdefault((host, port), set()).add(mac)
    # Per MAC, collect candidate access ports as sortable tuples:
    # (mac_count_on_port, switch_preference, host, port). min() then picks the
    # smallest port (fewest MACs), preferring designated switch hosts on ties.
    candidates: dict[str, list[tuple[int, int, str, str]]] = {}
    for (host, port), macs in port_macs.items():
        if len(macs) > uplink_threshold:
            continue
        for mac in macs:
            candidates.setdefault(mac, []).append(
                (len(macs), 0 if host in switch_hosts else 1, host, port)
            )
    result: dict[str, dict[str, str]] = {}
    for mac, cands in candidates.items():
        _, _, host, port = min(cands)
        result[mac] = {"port": _port_number(port), "host": host}
    return result


def parse_board_info(out: str) -> dict[str, str]:
    """Extract board metadata from collector output containing a BOARD| line."""
    board_lines: list[str] = []
    collecting = False

    def _parse_board(lines: list[str]) -> dict[str, str] | None:
        try:
            board = json.loads("\n".join(lines))
        except ValueError:
            return None
        if not isinstance(board, dict):
            return {}
        return {
            "model": board.get("model", ""),
            "board_name": board.get("board_name", ""),
            "hostname": board.get("hostname", ""),
        }

    for line in out.splitlines():
        if line.startswith("BOARD|"):
            board_lines = [line[6:]]
            collecting = True
            board = _parse_board(board_lines)
            if board is not None:
                return board
            continue
        if not collecting:
            continue
        if line.startswith(("STAT|", "FDB|")):
            return {}
        board_lines.append(line)
        board = _parse_board(board_lines)
        if board is not None:
            return board
    return {}


def parse_board_model(out: str) -> tuple[str, str]:
    """Extract (model, board_name) from collector output containing a BOARD| line."""
    board = parse_board_info(out)
    return board.get("model", ""), board.get("board_name", "")


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


# ── Attended Sysupgrade (owut) ────────────────────────────────────────────────

_OWUT_VERSION_RE = re.compile(
    r"(?:OpenWrt\s+)?(\d+)\.(\d+)(?:\.(\d+))?(?:[\s\-_]+r(\d+))?"
)
# owut check final-line markers (lower-cased before match)
_OWUT_UP_TO_DATE = "no changes, upgrade not necessary"
_OWUT_SAFE = "it is safe to proceed with an upgrade"
_OWUT_DOWNGRADE = "there are downgrades, upgrade carefully"
_OWUT_ERRORS = "checks reveal errors, do not upgrade"
# Lines from `owut check` that surface a candidate target version.
# Real samples: "ASU build OpenWrt 24.10.2 r28739-..."
#               "Available: 24.10.2"
_OWUT_AVAILABLE_RE = re.compile(
    r"(?:available|asu build|target|upgrade to|version-to)\s*[:=]?\s*"
    r"(?:openwrt\s+)?(\d+\.\d+(?:\.\d+)?(?:[\s\-_]+r\d+)?)",
    re.IGNORECASE,
)


def _normalise_owut_version(raw: str) -> str | None:
    """Return a normalised version like '24.10.1 r28597'.

    The build hash suffix (``-6df6e6c8a4``) is dropped so two builds of the same
    revision compare equal, but the OpenWrt revision number is **kept** so
    same-release revision upgrades (24.10.1 r28597 → r28600) flip the entity
    state instead of silently going unnoticed.
    """
    if not raw:
        return None
    m = _OWUT_VERSION_RE.search(raw)
    if not m:
        return None
    parts = [m.group(1), m.group(2)]
    if m.group(3):
        parts.append(m.group(3))
    base = ".".join(parts)
    revision = m.group(4)
    if revision:
        return f"{base} r{revision}"
    return base


def _parse_owut_version_tuple(raw: str) -> tuple[int, int, int, int] | None:
    """Return (major, minor, patch, revision) or None if unparsable."""
    if not raw:
        return None
    m = _OWUT_VERSION_RE.search(raw)
    if not m:
        return None
    major = int(m.group(1))
    minor = int(m.group(2))
    patch = int(m.group(3) or 0)
    revision = int(m.group(4) or 0)
    return (major, minor, patch, revision)


def asu_version_is_newer(latest: str, installed: str) -> bool:
    """Tuple-aware comparison so OpenWrt build hashes don't trigger forever-update."""
    lt = _parse_owut_version_tuple(latest or "")
    it = _parse_owut_version_tuple(installed or "")
    if lt is None or it is None:
        # Fall back to string equality semantics: any difference is "newer".
        return (latest or "") != (installed or "")
    return lt > it


def parse_asu_sections(out: str) -> dict[str, list[str]]:
    """Split the SSH stdout from _build_asu_command into ``---ASU_*---`` sections."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in out.splitlines():
        m = re.match(r"^---(ASU_[A-Z_]+)---$", line)
        if m:
            current = m.group(1)
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return sections


def _asu_blank(tool: str = "owut", error: str | None = None) -> dict[str, Any]:
    return {
        "tool": tool,
        "installed_version": None,
        "installed_version_raw": None,
        "latest_version": None,
        "summary": None,
        "error": error,
    }


def parse_asu_output(sections: dict[str, list[str]]) -> dict[str, Any]:
    """Parse `owut check` output captured by _build_asu_command.

    Returns a dict with the canonical shape used by the update entity:
    ``tool``, ``installed_version`` (normalised), ``installed_version_raw``,
    ``latest_version`` (normalised, equal to installed when up-to-date),
    ``summary``, and ``error``.

    All error paths populate ``error`` and leave at most one of the version
    fields populated so the entity can decide availability deterministically.
    """
    tool_lines = sections.get("ASU_TOOL", [])
    tool = (tool_lines[0].strip() if tool_lines else "").lower() or "unknown"
    if tool == "none":
        return _asu_blank(tool="none", error="owut not installed")
    if tool != "owut":
        return _asu_blank(tool="unknown", error=f"unknown ASU tool: {tool!r}")

    version_lines = sections.get("ASU_VERSION", [])
    installed_raw = next((ln.strip() for ln in version_lines if ln.strip()), None)
    installed_norm = _normalise_owut_version(installed_raw or "")

    output_lines = sections.get("ASU_OUTPUT", [])
    body = [ln for ln in output_lines if not ln.startswith("exit=")]
    text_lower = "\n".join(body).lower()

    summary = next((ln.strip() for ln in reversed(body) if ln.strip()), None)

    if _OWUT_UP_TO_DATE in text_lower:
        return {
            "tool": "owut",
            "installed_version": installed_norm,
            "installed_version_raw": installed_raw,
            "latest_version": installed_norm,
            "summary": summary,
            "error": None,
        }

    if _OWUT_ERRORS in text_lower:
        # ASU service / DNS / WAN failure — keep entity "up-to-date" rather
        # than blink on transient failures, but expose the diagnostic.
        err_line = next(
            (ln.strip() for ln in reversed(body) if ln.strip()),
            "ASU server reported errors",
        )
        return {
            "tool": "owut",
            "installed_version": installed_norm,
            "installed_version_raw": installed_raw,
            "latest_version": installed_norm,
            "summary": summary,
            "error": err_line,
        }

    if _OWUT_SAFE in text_lower or _OWUT_DOWNGRADE in text_lower:
        target_norm: str | None = None
        for line in body:
            m = _OWUT_AVAILABLE_RE.search(line)
            if m:
                target_norm = _normalise_owut_version(m.group(1))
                if target_norm:
                    break
        if not target_norm:
            return _asu_blank(
                tool="owut",
                error="owut output unrecognised: target version not found",
            )
        return {
            "tool": "owut",
            "installed_version": installed_norm,
            "installed_version_raw": installed_raw,
            "latest_version": target_norm,
            "summary": summary,
            "error": None,
        }

    # Unknown shape — likely owut returned an error before the markers we
    # know about. Carry the captured output as the error so users can debug.
    return _asu_blank(
        tool="owut",
        error=summary or "owut returned unrecognised output",
    )


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
