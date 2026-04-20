"""Pure parsing functions for wrtsensor data sources.

All functions are stateless and have no HA or SSH dependencies, making them
straightforward to unit-test without a running Home Assistant instance.
"""

from __future__ import annotations

import ipaddress
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
