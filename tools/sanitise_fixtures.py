#!/usr/bin/env python3
"""Sanitise captured OpenWrt fixtures in place.

Scans every text file under a version directory, collects identifying data,
and substitutes with stable deterministic placeholders:

- MACs        → XX:00:00:00:00:YY   (first octet preserved so LAA/UAA bits still test)
- IPv4        → 192.0.2.x           (RFC 5737 doc range; gateways end at .1)
- IPv6 GUA    → 2001:db8::<seq>     (RFC 3849 doc range)
- IPv6 LL     → fe80::<seq>         (link-local preserved as link-local)
- SSIDs       → NetA, NetB, …       (parsed from `ESSID: "…"` lines)
- Router hostnames → directory name (`gateway`, `ap1`, `ap2`, …)
- Client hostnames → host-<N>       (per-unique from dhcp.leases + dnsmasq DHCPACK)

Run after ./tools/capture_fixtures.sh before committing. Idempotent: a second
run against already-sanitised output produces no diff.

Usage: python3 tools/sanitise_fixtures.py tests/fixtures/openwrt/<version>
"""

import ipaddress
import json
import re
import sys
from pathlib import Path

MAC_RE = re.compile(r"\b([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})\b")
IPV4_RE = re.compile(r"\b((?:\d{1,3}\.){3}\d{1,3})\b")
IPV6_RE = re.compile(r"\b([0-9a-fA-F:]{2,}:[0-9a-fA-F]{1,4})\b")
ESSID_RE = re.compile(r'ESSID:\s*"([^"]*)"')
DHCPACK_RE = re.compile(r"DHCPACK\(\S+\)\s+\S+\s+\S+\s+(\S+)")

SKIP_MAC = {"00:00:00:00:00:00", "FF:FF:FF:FF:FF:FF"}
SKIP_IPV4_PREFIXES = ("0.", "127.", "255.", "224.", "169.254.", "192.0.2.")


def _already_fake_mac(mac: str) -> bool:
    parts = mac.split(":")
    return parts[1:5] == ["00", "00", "00", "00"]


def _already_fake_ipv4(ip: str) -> bool:
    return ip.startswith(SKIP_IPV4_PREFIXES)


def _already_fake_ipv6(addr: ipaddress.IPv6Address) -> bool:
    s = addr.exploded
    if s.startswith("2001:0db8"):
        return True
    # fe80::<small-seq> is our link-local fake form
    if addr.is_link_local and int(addr) - int(ipaddress.IPv6Address("fe80::")) < 4096:
        return True
    return False


def _read(path: Path) -> str:
    try:
        return path.read_text()
    except UnicodeDecodeError:
        return ""


def collect_macs_ipv4(files: list[Path]) -> tuple[set[str], set[str]]:
    macs: set[str] = set()
    ips: set[str] = set()
    for f in files:
        text = _read(f)
        for m in MAC_RE.findall(text):
            mac = m.upper()
            if mac in SKIP_MAC or _already_fake_mac(mac):
                continue
            macs.add(mac)
        for ip in IPV4_RE.findall(text):
            if _already_fake_ipv4(ip):
                continue
            parts = ip.split(".")
            if any(int(p) > 255 for p in parts):
                continue
            ips.add(ip)
    return macs, ips


def collect_ipv6(files: list[Path]) -> set[str]:
    ips: set[str] = set()
    for f in files:
        text = _read(f)
        for raw in IPV6_RE.findall(text):
            try:
                addr = ipaddress.IPv6Address(raw)
            except ValueError:
                continue
            if (
                addr.is_loopback
                or addr.is_unspecified
                or addr.is_multicast
                or _already_fake_ipv6(addr)
            ):
                continue
            ips.add(addr.compressed)
    return ips


def collect_ssids(files: list[Path]) -> set[str]:
    ssids: set[str] = set()
    for f in files:
        if "iwinfo" not in f.name:
            continue
        for essid in ESSID_RE.findall(_read(f)):
            if essid and not essid.startswith("Net"):
                ssids.add(essid)
    return ssids


def collect_router_hostnames(version_dir: Path) -> dict[str, str]:
    """Real router hostname → role dir name (gateway, ap1, ap2, ...)."""
    mapping: dict[str, str] = {}
    for hfile in version_dir.glob("*/hostname.txt"):
        role = hfile.parent.name
        real = _read(hfile).strip()
        if real and real != role:
            mapping[real] = role
    return mapping


def collect_client_hostnames(version_dir: Path) -> set[str]:
    hosts: set[str] = set()
    leases = version_dir / "gateway" / "dhcp.leases"
    if leases.is_file():
        for line in _read(leases).splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[3] != "*":
                hosts.add(parts[3])
    log = version_dir / "gateway" / "logread-dnsmasq.txt"
    if log.is_file():
        for line in _read(log).splitlines():
            m = DHCPACK_RE.search(line)
            if m and m.group(1) != "*":
                hosts.add(m.group(1))
    # Exclude already-sanitised names and router role names (ap1/gateway/...) —
    # those are post-sanitise router hostnames that leak into DHCPACK log lines.
    role_names = {d.name for d in version_dir.iterdir() if d.is_dir()}
    return {h for h in hosts if not h.startswith("host-") and h not in role_names}


def build_mac_map(macs: set[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    per_prefix: dict[str, int] = {}
    for mac in sorted(macs):
        prefix = mac[:2]
        per_prefix[prefix] = per_prefix.get(prefix, 0) + 1
        out[mac] = f"{prefix}:00:00:00:00:{per_prefix[prefix]:02X}"
    return out


def build_ipv4_map(ips: set[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    seq = 10
    for ip in sorted(ips, key=lambda s: [int(x) for x in s.split(".")]):
        if ip.endswith((".1", ".254")):
            out[ip] = "192.0.2.1"
        else:
            out[ip] = f"192.0.2.{seq}"
            seq += 1
    return out


def build_ipv6_map(ips: set[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    gua_seq = 10
    ll_seq = 10
    for raw in sorted(ips):
        addr = ipaddress.IPv6Address(raw)
        if addr.is_link_local:
            out[raw] = f"fe80::{ll_seq:x}"
            ll_seq += 1
        else:
            out[raw] = f"2001:db8::{gua_seq:x}"
            gua_seq += 1
    return out


def build_ssid_map(ssids: set[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for i, essid in enumerate(sorted(ssids)):
        out[essid] = f"Net{chr(ord('A') + i)}"
    return out


def build_client_hostname_map(hosts: set[str]) -> dict[str, str]:
    return {h: f"host-{i}" for i, h in enumerate(sorted(hosts), start=1)}


def _replace_word(text: str, real: str, fake: str) -> str:
    return re.sub(rf"(?<![A-Za-z0-9_-]){re.escape(real)}(?![A-Za-z0-9_-])", fake, text)


def _replace_all_ipv6_forms(text: str, real: str, fake: str) -> str:
    """IPv6 can be written compressed/exploded; replace the compressed form only.
    capture_fixtures pulls raw `ip` output which uses compression consistently."""
    try:
        addr = ipaddress.IPv6Address(real)
    except ValueError:
        return text
    forms = {real, addr.compressed, addr.exploded}
    for form in forms:
        text = text.replace(form, fake)
    return text


def apply(
    files: list[Path],
    mac_map: dict[str, str],
    ipv4_map: dict[str, str],
    ipv6_map: dict[str, str],
    ssid_map: dict[str, str],
    router_map: dict[str, str],
    client_map: dict[str, str],
) -> int:
    # Longest-first to avoid partial replacements.
    mac_items = sorted(mac_map.items(), key=lambda kv: -len(kv[0]))
    ipv4_items = sorted(ipv4_map.items(), key=lambda kv: -len(kv[0]))
    ipv6_items = sorted(ipv6_map.items(), key=lambda kv: -len(kv[0]))
    ssid_items = sorted(ssid_map.items(), key=lambda kv: -len(kv[0]))
    router_items = sorted(router_map.items(), key=lambda kv: -len(kv[0]))
    client_items = sorted(client_map.items(), key=lambda kv: -len(kv[0]))

    changes = 0
    for f in files:
        text = _read(f)
        if not text:
            continue
        new = text
        for real, fake in mac_items:
            new = re.sub(re.escape(real), fake, new, flags=re.IGNORECASE)
        for real, fake in ipv4_items:
            new = new.replace(real, fake)
        for real, fake in ipv6_items:
            new = _replace_all_ipv6_forms(new, real, fake)
        for real, fake in ssid_items:
            new = _replace_word(new, real, fake)
        for real, fake in router_items:
            new = _replace_word(new, real, fake)
        for real, fake in client_items:
            new = _replace_word(new, real, fake)
        if new != text:
            f.write_text(new)
            changes += 1
    return changes


def main(version_dir: Path) -> None:
    if not version_dir.is_dir():
        sys.exit(f"not a directory: {version_dir}")

    files = [
        p for p in version_dir.rglob("*") if p.is_file() and not p.name.startswith(".")
    ]

    macs, ipv4 = collect_macs_ipv4(files)
    ipv6 = collect_ipv6(files)
    ssids = collect_ssids(files)
    router_hosts = collect_router_hostnames(version_dir)
    client_hosts = collect_client_hostnames(version_dir)

    mac_map = build_mac_map(macs)
    ipv4_map = build_ipv4_map(ipv4)
    ipv6_map = build_ipv6_map(ipv6)
    ssid_map = build_ssid_map(ssids)
    client_map = build_client_hostname_map(client_hosts)

    total = sum(
        len(m)
        for m in (mac_map, ipv4_map, ipv6_map, ssid_map, router_hosts, client_map)
    )
    if total == 0:
        print("nothing to sanitise — fixtures already clean.")
        return

    print(
        f"found {len(mac_map)} MAC(s), {len(ipv4_map)} IPv4, "
        f"{len(ipv6_map)} IPv6, {len(ssid_map)} SSID(s), "
        f"{len(router_hosts)} router hostname(s), "
        f"{len(client_map)} client hostname(s)"
    )
    changed = apply(
        files, mac_map, ipv4_map, ipv6_map, ssid_map, router_hosts, client_map
    )
    print(f"rewrote {changed} file(s)")

    mapping = {
        "macs": mac_map,
        "ipv4": ipv4_map,
        "ipv6": ipv6_map,
        "ssids": ssid_map,
        "router_hostnames": router_hosts,
        "client_hostnames": client_map,
    }
    (version_dir / ".sanitise-map.json").write_text(
        json.dumps(mapping, indent=2, sort_keys=True)
    )
    print(f"mapping saved → {version_dir / '.sanitise-map.json'} (gitignored)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} tests/fixtures/openwrt/<version>")
    main(Path(sys.argv[1]))
