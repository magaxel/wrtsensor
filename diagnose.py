#!/usr/bin/env python3
"""wrtsensor.py — OpenWrt network scanner for Home Assistant.

Collects device state from gateway + APs, detects events, outputs JSON.

Usage:
    wrtsensor.py <gw_user@gw_ip> [ap_user@ap_ip] ...
"""

from __future__ import annotations

import concurrent.futures
import ipaddress
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


# ------------------------- Configuration -------------------------
# Auto-detect environment: use /config/ssh on HA, otherwise fall back
# to paths relative to the script for local development. Env vars
# override auto-detection if set.
def _default_paths() -> tuple[Path, Path, Path]:
    """Returns (ssh_key, config_dir, state_dir)."""
    ha_ssh = Path("/config/ssh")
    if ha_ssh.exists() and os.access(ha_ssh, os.W_OK):
        # Running on Home Assistant — SSH keys stay in /config/ssh,
        # everything else lives in /config/wrtsensor.
        ha_monitor = Path("/config/wrtsensor")
        ha_monitor.mkdir(exist_ok=True)
        return (ha_ssh / "id_ed25519", ha_monitor, Path("/dev/shm"))
    # Running locally — use script directory for config, /tmp for state
    script_dir = Path(__file__).resolve().parent
    local_state = Path("/tmp/netscan")
    local_state.mkdir(parents=True, exist_ok=True)
    return (Path.home() / ".ssh" / "id_ed25519", script_dir, local_state)


_ssh_key, _config_dir, _state_dir = _default_paths()

SSH_KEY = os.environ.get("NETSCAN_SSH_KEY", str(_ssh_key))
STATE_DIR = Path(os.environ.get("NETSCAN_STATE_DIR", str(_state_dir)))
CONFIG_DIR = Path(os.environ.get("NETSCAN_CONFIG_DIR", str(_config_dir)))
SSH_TIMEOUT = int(os.environ.get("NETSCAN_SSH_TIMEOUT", "8"))

# Strict IPv4 validation — used to guard shell-interpolated IPs
_IP_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
_HOST_ARG_RE = re.compile(
    r"^[a-zA-Z0-9_.-]+@\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d{1,5})?$"
)


def _valid_ip(ip: str) -> bool:
    """Return True for well-formed IPv4 or IPv6 addresses (no shell metacharacters)."""
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False


def _valid_ipv4(ip: str) -> bool:
    """Return True only for well-formed IPv4 addresses (safe to shell-interpolate)."""
    if not _IP_RE.match(ip):
        return False
    return all(0 <= int(o) <= 255 for o in ip.split("."))


def _parse_host_arg(arg: str) -> tuple[str, int]:
    """Parse 'user@ip' or 'user@ip:port' → (user@ip, port)."""
    user, _, rest = arg.partition("@")
    if ":" in rest:
        ip, _, port_str = rest.partition(":")
        return f"{user}@{ip}", int(port_str)
    return arg, 22


def _valid_host_arg(arg: str) -> bool:
    """Return True for user@ip or user@ip:port arguments that are safe to pass to SSH."""
    if not _HOST_ARG_RE.match(arg):
        return False
    ip = arg.split("@")[-1].split(":")[0]
    return _valid_ip(ip)


KNOWN_HOSTS = STATE_DIR / ".netscan_known_hosts"
VENDOR_CACHE = STATE_DIR / ".netscan_mac_vendors"
DNS_CACHE = STATE_DIR / ".netscan_dns_cache"
BW_STATE = STATE_DIR / ".netscan_bw_state"
CPU_STATE = STATE_DIR / ".netscan_cpu_state"
DNS_STATE = STATE_DIR / ".netscan_dns_state"
DNS_HISTORY = STATE_DIR / ".netscan_dns_history.jsonl"
PREV_STATE = STATE_DIR / ".netscan_prev_state.json"
EVENT_LOG = STATE_DIR / "netscan_events.json"
OUI_DB = CONFIG_DIR / "oui.db"
OUI_TXT = CONFIG_DIR / "oui.txt"

EVENT_RETENTION_DAYS = 30
DNS_HISTORY_MAX_AGE_S = 25 * 60 * 60
DNS_WINDOW_S = 24 * 60 * 60
MAX_EVENT_LINES = 10_000
DISCONNECT_MISS_THRESHOLD = 3
STATE_MAX_AGE_DAYS = 7

LAN_IFACE = os.environ.get("NETSCAN_LAN_IFACE", "br-lan")
DHCP_LEASES = os.environ.get("NETSCAN_DHCP_LEASES", "/tmp/dhcp.leases")
SSH_CONNECT_TIMEOUT = int(os.environ.get("NETSCAN_SSH_CONNECT_TIMEOUT", "5"))
SSH_CONTROL_PERSIST = int(os.environ.get("NETSCAN_SSH_CONTROL_PERSIST", "60"))
BW_MAX_AGE_S = int(os.environ.get("NETSCAN_BW_MAX_AGE_S", "600"))
BW_MIN_ELAPSED_S = int(os.environ.get("NETSCAN_BW_MIN_ELAPSED_S", "10"))
BW_MAX_RATE_BPS = int(os.environ.get("NETSCAN_BW_MAX_RATE_BPS", "125000000"))  # 1 Gbps
DEVICE_BW_STATE = STATE_DIR / ".netscan_device_bw"
WAN_EVENT_STATE = STATE_DIR / ".netscan_wan_state"
DEVICE_BW_ACCUM = STATE_DIR / ".netscan_device_bw_accum"

# ------------------------- Data types -------------------------


@dataclass
class Device:
    mac: str
    ip: str = ""
    ip6: str = ""
    hostname: str = ""
    vendor: str = ""
    connection: str = "wired"  # "wired" | "wifi"
    ap: str = ""
    band: str = ""
    channel: int | None = None
    essid: str = ""
    signal: int | None = None
    noise: int | None = None
    snr: int | None = None
    tx_rate: float | None = None
    rx_rate: float | None = None
    exp_tput: float | None = None
    rx_bps: int | None = None
    tx_bps: int | None = None
    rx_total: int | None = None
    tx_total: int | None = None
    bw_since: int | None = None
    first_seen: float = 0.0
    online: bool = False


@dataclass
class StateEntry:
    mac: str
    ap: str = ""
    band: str = ""
    channel: int | None = None
    essid: str = ""
    signal: int | None = None
    online: bool = False
    hostname: str = ""
    vendor: str = ""
    ip: str = ""
    ip6: str = ""
    connection: str = "wired"
    miss: int = 0
    first_seen: float = 0.0  # Unix timestamp when device first came online (session)
    last_seen: float = 0.0  # Unix timestamp when device was last seen online
    rx_total: int | None = None
    tx_total: int | None = None


# ------------------------- SSH helper -------------------------


def ssh_run(host: str, command: str, timeout: int = SSH_TIMEOUT) -> str:
    """Run a command on a remote host via SSH. Returns stdout, empty on failure."""
    host_addr, port = _parse_host_arg(host)
    # Use TOFU known_hosts when available; fall back to accept-on-first-use prompt
    # suppression only when the file hasn't been created yet.
    if KNOWN_HOSTS.exists():
        host_checking = [
            "-o",
            f"UserKnownHostsFile={KNOWN_HOSTS}",
            "-o",
            "StrictHostKeyChecking=yes",
        ]
    else:
        host_checking = [
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
        ]
    # Control socket lives in STATE_DIR (not world-writable /tmp).
    # Unix domain sockets have a ~108-char path limit; keep NETSCAN_STATE_DIR short.
    ctl_path = str(STATE_DIR / "ssh_mux_%h_%p_%r")
    port_args = ["-p", str(port)] if port != 22 else []
    try:
        result = subprocess.run(
            [
                "/usr/bin/ssh",
                "-i",
                SSH_KEY,
                *host_checking,
                *port_args,
                "-o",
                f"ConnectTimeout={SSH_CONNECT_TIMEOUT}",
                "-o",
                "ControlMaster=auto",
                "-o",
                f"ControlPersist={SSH_CONTROL_PERSIST}",
                "-o",
                f"ControlPath={ctl_path}",
                "-q",
                host_addr,
                command,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stdout
    except subprocess.TimeoutExpired:
        return ""
    except Exception:
        return ""


def _ensure_known_hosts(hosts: list[str]) -> None:
    """Add any host keys missing from known_hosts (TOFU per host).

    IPs are stored unhashed so membership is checked with a plain Python regex —
    no subprocess forks on the common hot path (all hosts already known).
    Non-standard ports are stored as [ip]:port per ssh-keyscan convention.
    """
    existing = KNOWN_HOSTS.read_text() if KNOWN_HOSTS.exists() else ""
    missing: list[tuple[str, int]] = []
    for h in hosts:
        addr, port = _parse_host_arg(h)
        ip = addr.split("@")[-1]
        if port == 22:
            pattern = rf"^{re.escape(ip)}[, ]"
        else:
            pattern = rf"^\[{re.escape(ip)}\]:{port}[ ,]"
        if not re.search(pattern, existing, re.MULTILINE):
            missing.append((ip, port))

    if not missing:
        return

    new_keys: list[str] = []
    for ip, port in missing:
        port_args = ["-p", str(port)] if port != 22 else []
        try:
            result = subprocess.run(
                ["/usr/bin/ssh-keyscan", "-T", "5", *port_args, ip],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.stdout:
                new_keys.append(result.stdout)
        except Exception as e:
            print(
                f"ssh-keyscan failed for {ip}:{port}: {e}; continuing without strict host checking",
                file=sys.stderr,
            )

    if new_keys:
        with KNOWN_HOSTS.open("a") as f:
            f.write("".join(new_keys))
        KNOWN_HOSTS.chmod(0o600)
        labels = [f"{ip}:{port}" if port != 22 else ip for ip, port in missing]
        print(f"Added keys for {labels} to {KNOWN_HOSTS}", file=sys.stderr)


def emit_error(msg: str) -> None:
    print(
        json.dumps(
            {
                "device_count": 0,
                "wan_ip": "",
                "wan_ip6": "",
                "wan_rx_rate": None,
                "wan_tx_rate": None,
                "devices": [],
                "error": msg,
            }
        )
    )
    sys.exit(1)


# ------------------------- Cache helpers -------------------------


def _atomic_write(path: Path, text: str) -> None:
    """Write text to path atomically via a sibling .tmp file."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def load_kv_cache(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    result = {}
    for line in path.read_text().splitlines():
        if "|" in line:
            k, _, v = line.partition("|")
            result[k.upper() if ":" in k else k] = v
    return result


def save_kv_cache(path: Path, cache: dict[str, str]) -> None:
    _atomic_write(path, "\n".join(f"{k}|{v}" for k, v in cache.items()) + "\n")


def load_oui_db() -> dict[str, str]:
    """Load IEEE OUI database. Download/build index if needed."""
    # Build index from source if index missing but source exists
    if not OUI_DB.exists() and OUI_TXT.exists():
        db = {}
        for line in OUI_TXT.read_text(errors="ignore").splitlines():
            if "(hex)" in line:
                parts = line.split("(hex)", 1)
                if len(parts) == 2:
                    oui = parts[0].strip().upper()
                    vendor = parts[1].strip()
                    if oui:
                        db[oui] = vendor
        OUI_DB.parent.mkdir(parents=True, exist_ok=True)
        OUI_DB.write_text("\n".join(f"{k}|{v}" for k, v in db.items()))
        return db

    # If neither exists, download and build
    if not OUI_DB.exists() and not OUI_TXT.exists():
        urls = [
            "https://standards-oui.ieee.org/oui/oui.txt",
            "https://www.wireshark.org/download/automated/data/manuf",
        ]
        OUI_TXT.parent.mkdir(parents=True, exist_ok=True)
        for url in urls:
            try:
                print(
                    f"OUI database missing; downloading from {url}...", file=sys.stderr
                )
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (netscan)"},
                )
                with urllib.request.urlopen(req, timeout=40) as response:
                    data = response.read(50 * 1024 * 1024 + 1)
                    if len(data) > 50 * 1024 * 1024:
                        raise ValueError("OUI response exceeds 50 MB limit")
                    OUI_TXT.write_bytes(data)
                print(f"Downloaded to {OUI_TXT}", file=sys.stderr)
                return load_oui_db()
            except Exception as e:
                print(f"Download from {url} failed: {e}", file=sys.stderr)
        print("All OUI sources failed; continuing without vendor data", file=sys.stderr)
        return {}

    # Load pre-built index
    if not OUI_DB.exists():
        return {}
    db = {}
    for line in OUI_DB.read_text().splitlines():
        if "|" in line:
            k, _, v = line.partition("|")
            db[k.strip().upper()] = v.strip()
    return db


# ------------------------- Data collection -------------------------


def collect_gateway(gw_host: str) -> dict[str, Any]:
    """Single SSH call to gateway: leases, ARP, gateway info, WAN IP, bandwidth."""
    cmd = (
        f"echo '---LEASES---'; cat {DHCP_LEASES} 2>/dev/null; "
        f"echo '---ARP---'; ip -4 neigh show dev {LAN_IFACE} 2>/dev/null; "
        "echo '---NDP---'; "
        f"{{ ping6 -c2 -W1 -I {LAN_IFACE} ff02::1 2>/dev/null || ping -6 -c2 -W1 -I {LAN_IFACE} ff02::1 2>/dev/null; }} >/dev/null; "
        f"ip -6 neigh show dev {LAN_IFACE} 2>/dev/null | grep -v '^fe80'; "
        "echo '---GW---'; "
        f"ip addr show {LAN_IFACE} 2>/dev/null | grep 'link/ether' | awk '{{print $2}}'; "
        f"ip addr show {LAN_IFACE} 2>/dev/null | grep ' inet ' | awk '{{split($2,a,\"/\"); print a[1]}}'; "
        "cat /proc/sys/kernel/hostname; "
        f"ip addr show {LAN_IFACE} 2>/dev/null | grep ' inet6 ' | grep -v ' fe80' | awk '{{split($2,a,\"/\"); print a[1]}}' | head -1; "
        "echo '---WAN---'; "
        "wan=$(ip route 2>/dev/null | awk '/default/ {print $5; exit}'); "
        "ip addr show \"$wan\" 2>/dev/null | grep ' inet ' | awk '{split($2,a,\"/\"); print a[1]}'; "
        "wan6=$(ip -6 route 2>/dev/null | awk '/default/ {for(i=1;i<=NF;i++) if($i==\"dev\") {print $(i+1); exit}}'); "
        "ip addr show \"$wan6\" 2>/dev/null | grep ' inet6 ' | grep -v ' fe80' | awk '{split($2,a,\"/\"); print a[1]}' | head -1; "
        "echo '---BW---'; "
        'cat /sys/class/net/"$wan"/statistics/rx_bytes 2>/dev/null; '
        'cat /sys/class/net/"$wan"/statistics/tx_bytes 2>/dev/null; '
        "echo '---HOSTSTAT---'; "
        "grep '^cpu ' /proc/stat 2>/dev/null; "
        "awk '/^MemTotal:/ {t=$2} /^MemAvailable:/ {a=$2} END{print t, a}' /proc/meminfo 2>/dev/null; "
        'df / 2>/dev/null | awk \'NR==2 {gsub("%","",$5); print $5+0}\'; '
        "echo '---DNS---'; "
        "kill -USR1 $(pidof dnsmasq) 2>/dev/null; sleep 0.3; "
        "logread -l 60 2>/dev/null | grep 'dnsmasq\\[' "
        "| grep -E 'cache size|queries forwarded|avg\\. latency' | tail -20; "
        "echo '---CONNTRACK---'; cat /proc/net/nf_conntrack 2>/dev/null"
    )
    out = ssh_run(gw_host, cmd, timeout=20)
    if not out or "---LEASES---" not in out:
        return {}

    sections: dict[str, list[str]] = {}
    current = None
    for line in out.splitlines():
        m = re.match(r"^---([A-Z]+)---$", line)
        if m:
            current = m.group(1)
            sections[current] = []
        elif current:
            sections[current].append(line)

    gw_info = sections.get("GW", [])
    wan_info = sections.get("WAN", [])
    bw_info = sections.get("BW", [])
    hoststat_info = [line for line in sections.get("HOSTSTAT", []) if line.strip()]
    dns_info = [line for line in sections.get("DNS", []) if line.strip()]

    return {
        "leases": [line for line in sections.get("LEASES", []) if line.strip()],
        "arp": [line for line in sections.get("ARP", []) if line.strip()],
        "ndp": [line for line in sections.get("NDP", []) if line.strip()],
        "gw_mac": gw_info[0].strip().upper() if gw_info else "",
        "gw_ip": gw_info[1].strip() if len(gw_info) > 1 else "",
        "gw_hostname": gw_info[2].strip() if len(gw_info) > 2 else "gateway",
        "gw_ip6": gw_info[3].strip() if len(gw_info) > 3 else "",
        "wan_ip": wan_info[0].strip() if wan_info else "",
        "wan_ip6": wan_info[1].strip() if len(wan_info) > 1 else "",
        "rx_bytes": int(bw_info[0]) if bw_info and bw_info[0].isdigit() else None,
        "tx_bytes": int(bw_info[1])
        if len(bw_info) > 1 and bw_info[1].isdigit()
        else None,
        "conntrack": [line for line in sections.get("CONNTRACK", []) if line.strip()],
        "hoststat": hoststat_info,
        "dns": dns_info,
    }


def parse_leases(lines: list[str]) -> dict[str, dict[str, str]]:
    """Returns {MAC: {ip, hostname}}"""
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
    """Returns ({MAC: state}, {stale MACs}, {MAC: ip})"""
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
    """Parse `ip -6 neigh show` output. Returns {MAC: ipv6_addr}.

    Accepts global unicast AND ULA (fd__:/fc__:) addresses — both are used on
    home LANs.  Skips link-local (fe80::) which are filtered at collection time,
    plus loopback, multicast, and FAILED entries.

    OpenWrt's busybox `ip` omits the `dev <iface>` column when filtering by
    device, so lines are 4 tokens: <addr> lladdr <mac> <state>.
    Standard iproute2 outputs 6 tokens: <addr> dev <iface> lladdr <mac> <state>.
    Both formats are handled by searching for the `lladdr` keyword.
    """
    result: dict[str, str] = {}
    for line in lines:
        tokens = line.split()
        # Need at least: addr, lladdr, mac, state
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
            # Prefer public global over ULA — don't overwrite a global with a private one
            existing = result.get(mac)
            if existing is None:
                result[mac] = addr
            elif not ip6_obj.is_private:
                # New address is global (public) — always prefer it
                result[mac] = addr
    return result


def collect_wifi(
    host: str, ap_name: str, script: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """Get WiFi associations from an OpenWrt host. Returns (entries, hoststat_lines)."""
    out = ssh_run(host, script)
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
        try:
            signal = int(parts[3]) if len(parts) > 3 and parts[3] else None
        except ValueError:
            signal = None
        try:
            tx = float(parts[4]) if len(parts) > 4 and parts[4] else None
        except ValueError:
            tx = None
        try:
            channel = int(parts[5]) if len(parts) > 5 and parts[5] else None
        except ValueError:
            channel = None
        try:
            sta_ul = int(parts[6]) if len(parts) > 6 and parts[6] else 0
        except ValueError:
            sta_ul = 0
        try:
            sta_dl = int(parts[7]) if len(parts) > 7 and parts[7] else 0
        except ValueError:
            sta_dl = 0
        try:
            noise = int(parts[8]) if len(parts) > 8 and parts[8] else None
        except ValueError:
            noise = None
        try:
            snr = int(parts[9]) if len(parts) > 9 and parts[9] else None
        except ValueError:
            snr = None
        try:
            rx_rate = float(parts[10]) if len(parts) > 10 and parts[10] else None
        except ValueError:
            rx_rate = None
        try:
            exp_tput = float(parts[11]) if len(parts) > 11 and parts[11] else None
        except ValueError:
            exp_tput = None
        entries.append(
            {
                "mac": mac,
                "ap": ap_name,
                "band": parts[1] if len(parts) > 1 else "",
                "essid": parts[2] if len(parts) > 2 else "",
                "signal": signal,
                "tx_rate": tx,
                "channel": channel,
                "sta_ul_bytes": sta_ul,
                "sta_dl_bytes": sta_dl,
                "noise": noise,
                "snr": snr,
                "rx_rate": rx_rate,
                "exp_tput": exp_tput,
            }
        )
    return entries, hoststat


def get_ap_info(host: str) -> tuple[str, str]:
    """Returns (hostname, best_ip6) for an AP. ip6 is '' if unavailable."""
    out = ssh_run(
        host,
        "cat /proc/sys/kernel/hostname; "
        f"ip addr show {LAN_IFACE} 2>/dev/null | grep ' inet6 ' | grep -v ' fe80' "
        "| awk '{split($2,a,\"/\"); print a[1]}'",
    ).strip()
    lines = out.splitlines()
    hostname = lines[0].strip() if lines else host.split("@")[-1]
    # Prefer global public over ULA, skip link-local/loopback
    ip6 = ""
    for line in lines[1:]:
        addr = line.strip()
        if not addr:
            continue
        try:
            obj = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if obj.is_link_local or obj.is_loopback or obj.is_multicast:
            continue
        if not obj.is_private:
            ip6 = addr
            break  # global public — best choice, stop immediately
        if not ip6:
            ip6 = addr  # ULA — keep as fallback
    return hostname or host.split("@")[-1], ip6


# ------------------------- Host CPU / RAM -------------------------


def parse_hoststat(lines: list[str]) -> dict[str, int] | None:
    """Parse a HOSTSTAT section (cpu /proc/stat line + 'mem_total mem_avail' + optional disk%).
    Returns {busy, idle, mem_total, mem_avail, disk} or None if malformed."""
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


def compute_host_stats(
    host_key: str, current: dict[str, int] | None
) -> dict[str, float | None] | None:
    """Compute CPU% (delta since last scan) and RAM% for one host.
    `current` comes from parse_hoststat. Returns {cpu, ram} with None for cpu on first run."""
    if current is None:
        return None
    ram = None
    if current["mem_total"] > 0:
        ram = round(
            100.0
            * (current["mem_total"] - current["mem_avail"])
            / current["mem_total"],
            1,
        )
    prev: dict[str, dict[str, int]] = {}
    if CPU_STATE.exists():
        try:
            prev = json.loads(CPU_STATE.read_text())
        except Exception:
            pass
    cpu = None
    p = prev.get(host_key)
    if p:
        d_busy = current["busy"] - p.get("busy", 0)
        d_idle = current["idle"] - p.get("idle", 0)
        total = d_busy + d_idle
        if total > 0 and d_busy >= 0 and d_idle >= 0:
            cpu = round(100.0 * d_busy / total, 1)
    prev[host_key] = {"busy": current["busy"], "idle": current["idle"]}
    _atomic_write(CPU_STATE, json.dumps(prev))
    return {"cpu": cpu, "ram": ram, "disk": current.get("disk")}


# ------------------------- dnsmasq DNS cache stats -------------------------


def parse_dns_stats(lines: list[str]) -> dict[str, Any] | None:
    """Extract cache_size, hits, misses, weighted avg upstream latency, and per-server list.
    Line examples:
      '... dnsmasq[1]: cache size 1000, 0/1754 cache insertions re-used unexpired cache entries.'
      '... dnsmasq[1]: queries forwarded 370224, queries answered locally 346136'
      '... dnsmasq[1]: server 1.1.1.3#53: queries sent 214592, ..., avg. latency 19ms'
    Latency is a weighted avg of dnsmasq's per-upstream EMA latencies, weighted by queries_sent.
    """
    # Each SIGUSR1 dump prints: cache size → queries forwarded → server lines.
    # logread may contain multiple dumps; keep only lines after the last
    # 'queries forwarded' (which starts the newest dump's server block).
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


def _dns_window_label(elapsed_s: int) -> str:
    if elapsed_s >= DNS_WINDOW_S - 5 * 60:
        return "last 24h"
    if elapsed_s >= 2 * 60 * 60:
        return f"last {round(elapsed_s / 3600)}h"
    if elapsed_s >= 60 * 60:
        return "last 1h"
    if elapsed_s >= 2 * 60:
        return f"last {round(elapsed_s / 60)}m"
    return "last scan"


def _dns_rollup(
    baseline: dict[str, int], current: dict[str, int], *, label: str | None = None
) -> dict[str, Any] | None:
    elapsed_s = int(current["ts"]) - int(baseline["ts"])
    if elapsed_s <= 0:
        return None
    hits = int(current["hits"]) - int(baseline["hits"])
    misses = int(current["misses"]) - int(baseline["misses"])
    if hits < 0 or misses < 0:
        return None
    total = hits + misses
    return {
        "hits": hits,
        "misses": misses,
        "hit_pct": round(100.0 * hits / total, 1) if total else None,
        "hits_per_sec": round(hits / elapsed_s, 2),
        "misses_per_sec": round(misses / elapsed_s, 2),
        "elapsed_s": elapsed_s,
        "label": label or _dns_window_label(elapsed_s),
    }


def _load_dns_history() -> list[dict[str, int]]:
    if not DNS_HISTORY.exists():
        return []
    history: list[dict[str, int]] = []
    for line in DNS_HISTORY.read_text().splitlines():
        try:
            raw = json.loads(line)
            sample = {
                "ts": int(raw["ts"]),
                "hits": int(raw["hits"]),
                "misses": int(raw["misses"]),
            }
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            continue
        history.append(sample)
    return sorted(history, key=lambda sample: sample["ts"])


def _append_dns_history(now: int, current: dict[str, Any]) -> list[dict[str, int]]:
    cutoff = now - DNS_HISTORY_MAX_AGE_S
    sample = {
        "ts": now,
        "hits": int(current["hits"]),
        "misses": int(current["misses"]),
    }
    history = [entry for entry in _load_dns_history() if entry["ts"] >= cutoff]
    history.append(sample)
    text = "".join(json.dumps(entry, separators=(",", ":")) + "\n" for entry in history)
    _atomic_write(DNS_HISTORY, text)
    return history


def _compute_dns_window(
    history: list[dict[str, int]], now: int
) -> dict[str, Any] | None:
    if len(history) < 2:
        return None

    cutoff = now - DNS_WINDOW_S
    segment_start = 0
    reset_in_window = False
    for idx in range(1, len(history)):
        prev = history[idx - 1]
        cur = history[idx]
        if cur["hits"] < prev["hits"] or cur["misses"] < prev["misses"]:
            segment_start = idx
            if cur["ts"] >= cutoff:
                reset_in_window = True

    current = history[-1]
    segment = history[segment_start:]
    if len(segment) < 2:
        return None

    if reset_in_window:
        return _dns_rollup(segment[0], current)

    baseline = None
    for sample in segment:
        if sample["ts"] <= cutoff:
            baseline = sample
        else:
            break
    if baseline is None:
        return None
    return _dns_rollup(baseline, current, label="last 24h")


def compute_dns_rates(current: dict[str, int | float] | None) -> dict[str, Any] | None:
    """Return DNS lifetime stats plus backend-computed last-24h rollups."""
    if current is None:
        return None
    now = int(time.time())
    hits_rate = misses_rate = None
    if DNS_STATE.exists():
        try:
            prev = json.loads(DNS_STATE.read_text())
            elapsed = now - int(prev.get("ts", 0))
            if 0 < elapsed < BW_MAX_AGE_S:
                d_hits = current["hits"] - prev.get("hits", 0)
                d_misses = current["misses"] - prev.get("misses", 0)
                if d_hits >= 0 and d_misses >= 0:
                    hits_rate = round(d_hits / elapsed, 2)
                    misses_rate = round(d_misses / elapsed, 2)
        except Exception:
            pass
    _atomic_write(
        DNS_STATE,
        json.dumps({"ts": now, "hits": current["hits"], "misses": current["misses"]}),
    )
    history = _append_dns_history(now, current)
    life_total = int(current["hits"]) + int(current["misses"])
    lifetime = {
        "hits": int(current["hits"]),
        "misses": int(current["misses"]),
        "hit_pct": round(100.0 * int(current["hits"]) / life_total, 1)
        if life_total
        else None,
        "hits_per_sec": hits_rate,
        "misses_per_sec": misses_rate,
        "elapsed_s": None,
        "label": "lifetime",
    }
    return {
        "cache_size": current["cache_size"],
        "last_24h": _compute_dns_window(history, now),
        "lifetime": lifetime,
        "latency_ms": current.get("latency_ms"),
        "avg_latency_ms": current.get("latency_ms"),
        "servers": current.get("servers", []),
    }


# ------------------------- WAN bandwidth -------------------------


def compute_wan_rates(
    rx: int | None, tx: int | None
) -> tuple[int | None, int | None, int | None, int | None, int | None]:
    """Returns (rx_rate, tx_rate, rx_total, tx_total, since) — rates in bytes/s,
    totals in bytes since first-seen timestamp."""
    if rx is None or tx is None:
        return None, None, None, None, None
    now = int(time.time())
    rx_rate = tx_rate = None
    rx_total = tx_total = 0
    since = now
    if BW_STATE.exists():
        try:
            prev = BW_STATE.read_text().split()
            if len(prev) >= 3:
                prev_ts, prev_rx, prev_tx = int(prev[0]), int(prev[1]), int(prev[2])
                elapsed = now - prev_ts
                if 0 < elapsed < BW_MAX_AGE_S:
                    rx_rate = (
                        max(0, (rx - prev_rx) // elapsed) if rx >= prev_rx else None
                    )
                    tx_rate = (
                        max(0, (tx - prev_tx) // elapsed) if tx >= prev_tx else None
                    )
            if len(prev) >= 6:
                rx_total, tx_total, since = int(prev[3]), int(prev[4]), int(prev[5])
                if rx >= prev_rx:
                    rx_total += rx - prev_rx
                if tx >= prev_tx:
                    tx_total += tx - prev_tx
        except (ValueError, IndexError):
            pass
    _atomic_write(BW_STATE, f"{now} {rx} {tx} {rx_total} {tx_total} {since}\n")
    return rx_rate, tx_rate, rx_total, tx_total, since


# ------------------------- Per-device bandwidth -------------------------


def parse_conntrack(lines: list[str]) -> dict[str, dict[str, int]]:
    """Aggregate conntrack bytes per originating LAN IP.
    Returns {ip: {ul: upload_bytes, dl: download_bytes}} from device perspective.
    Only IPv4 entries where the originator is a private (LAN) address."""
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
        entry["ul"] += int(bytes_vals[0])  # original direction: device → WAN
        entry["dl"] += int(bytes_vals[1])  # reply direction: WAN → device

    return result


def compute_device_rates(
    wifi_bytes: dict[str, dict[str, int]],
    wired_bytes: dict[str, dict[str, int]],
) -> tuple[dict[str, dict[str, int | None]], dict[str, dict[str, int]]]:
    """Compute per-device bandwidth rates in bytes/s and accumulated totals.
    wifi_bytes:  {mac: {ul, dl}} — from AP station dump (ul=AP rx, dl=AP tx)
    wired_bytes: {mac: {ul, dl}} — from conntrack, already IP→MAC resolved
    Returns (rates, accum) where:
      rates: {mac: {rx_bps, tx_bps}} — current rate, bytes/s
      accum: {mac: {rx, tx, since}} — cumulative bytes since first seen"""
    now = time.time()
    prev: dict = {}
    if DEVICE_BW_STATE.exists():
        try:
            prev = json.loads(DEVICE_BW_STATE.read_text())
        except Exception:
            pass

    prev_ts = float(prev.get("ts", 0.0))
    prev_wifi = prev.get("wifi", {})
    prev_wired = prev.get("wired", {})
    elapsed = now - prev_ts

    rates: dict[str, dict[str, int | None]] = {}
    deltas: dict[str, dict[str, int]] = {}  # valid byte deltas for accumulator

    if BW_MIN_ELAPSED_S <= elapsed < BW_MAX_AGE_S:
        for mac, curr in wifi_bytes.items():
            p = prev_wifi.get(mac)
            if not p:
                continue
            d_ul = curr["ul"] - p.get("ul", 0)
            d_dl = curr["dl"] - p.get("dl", 0)
            rx = max(0, int(d_dl / elapsed))
            tx = max(0, int(d_ul / elapsed))
            rates[mac] = {
                "rx_bps": rx if rx <= BW_MAX_RATE_BPS else None,
                "tx_bps": tx if tx <= BW_MAX_RATE_BPS else None,
            }
            deltas[mac] = {
                "rx": max(0, d_dl) if rx <= BW_MAX_RATE_BPS else 0,
                "tx": max(0, d_ul) if tx <= BW_MAX_RATE_BPS else 0,
            }
        for mac, curr in wired_bytes.items():
            if mac in rates:
                continue  # WiFi station dump takes priority
            p = prev_wired.get(mac)
            if not p:
                continue
            d_ul = curr["ul"] - p.get("ul", 0)
            d_dl = curr["dl"] - p.get("dl", 0)
            rx = max(0, int(d_dl / elapsed))
            tx = max(0, int(d_ul / elapsed))
            rates[mac] = {
                "rx_bps": rx if rx <= BW_MAX_RATE_BPS else None,
                "tx_bps": tx if tx <= BW_MAX_RATE_BPS else None,
            }
            deltas[mac] = {
                "rx": max(0, d_dl) if rx <= BW_MAX_RATE_BPS else 0,
                "tx": max(0, d_ul) if tx <= BW_MAX_RATE_BPS else 0,
            }

    # Update accumulator
    accum: dict[str, dict[str, int]] = {}
    if DEVICE_BW_ACCUM.exists():
        try:
            accum = json.loads(DEVICE_BW_ACCUM.read_text())
        except Exception:
            pass
    for mac, delta in deltas.items():
        if mac not in accum:
            accum[mac] = {"rx": 0, "tx": 0, "since": int(now)}
        accum[mac]["rx"] = accum[mac].get("rx", 0) + delta["rx"]
        accum[mac]["tx"] = accum[mac].get("tx", 0) + delta["tx"]

    _atomic_write(
        DEVICE_BW_STATE,
        json.dumps({"ts": now, "wifi": wifi_bytes, "wired": wired_bytes}),
    )
    _atomic_write(DEVICE_BW_ACCUM, json.dumps(accum))
    return rates, accum


# ------------------------- Vendor lookup -------------------------


def lookup_vendors(
    macs: set[str], cache: dict[str, str], oui_db: dict[str, str]
) -> None:
    """Populate vendor cache for any missing MACs. Re-resolves empty cache entries if oui_db is now available."""
    for mac in macs:
        oui = mac[:8].replace(":", "-")
        # If MAC is in cache with empty vendor but OUI db now has it, update
        if mac in cache:
            if not cache[mac] and oui in oui_db:
                cache[mac] = oui_db[oui]
            continue
        # New MAC — populate from OUI db
        cache[mac] = oui_db.get(oui, "")


# ------------------------- Reverse DNS -------------------------


def resolve_hostnames(
    gw_host: str, ips: list[str], cache: dict[str, str]
) -> dict[str, str]:
    """Resolve missing hostnames via nslookup on the gateway (cached)."""
    need = [ip for ip in ips if ip not in cache and _valid_ipv4(ip)]
    if not need:
        return {ip: cache[ip] for ip in ips if cache.get(ip)}

    cmd_parts = []
    for ip in need:
        cmd_parts.append(
            f"echo \"{ip}|$(nslookup {ip} 2>/dev/null | grep 'name = ' | "
            f"sed 's/.*name = //;s/\\.$//' | head -1)\""
        )
    out = ssh_run(gw_host, "; ".join(cmd_parts))
    for line in out.splitlines():
        if "|" in line:
            ip, _, hostname = line.partition("|")
            cache[ip] = hostname
    return {ip: cache.get(ip, "") for ip in ips}


# ------------------------- Ping stale devices -------------------------


def ping_stale(gw_host: str, ips: list[str]) -> list[str]:
    """Ping stale IPs in parallel on the gateway, return refreshed ARP."""
    safe = [ip for ip in ips if _valid_ipv4(ip)]
    if not safe:
        return []
    ips = safe
    ping_cmd = " ".join(f"ping -c1 -W1 {ip} >/dev/null 2>&1 &" for ip in ips)
    full = f"{ping_cmd} wait; ip -4 neigh show dev {LAN_IFACE} 2>/dev/null"
    return [line for line in ssh_run(gw_host, full).splitlines() if line.strip()]


# ------------------------- Collect WiFi from all APs in parallel -------------------------


def collect_all_wifi(
    gw_host: str, ap_hosts: list[str], script: str
) -> tuple[list[dict], list[str], dict[str, str], dict[str, list[str]]]:
    """Collect WiFi from gateway + all APs in parallel.
    Returns (wifi_entries, alive_ap_ips, ap_ip6_map, ap_hoststats) where:
      ap_hoststats is {ap_ip: [cpu_line, 'mem_total mem_avail']}."""
    all_wifi: list[dict] = []
    alive_ap_ips: list[str] = []
    ap_ip6_map: dict[str, str] = {}
    ap_hoststats: dict[str, list[str]] = {}

    # Phase 1: resolve AP hostnames + IPv6 in parallel
    name_map: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(len(ap_hosts), 1)) as ex:
        futs = {ex.submit(get_ap_info, h): h for h in ap_hosts}
        for fut in concurrent.futures.as_completed(futs):
            host = futs[fut]
            try:
                hostname, ip6 = fut.result()
                name_map[host] = hostname or host.split("@")[-1]
                ap_ip = host.split("@")[-1]
                if ip6:
                    ap_ip6_map[ap_ip] = ip6
            except Exception:
                name_map[host] = host.split("@")[-1]

    # Phase 2: collect WiFi in parallel (gateway + all APs)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(ap_hosts) + 1) as ex:
        wifi_futs: dict[
            concurrent.futures.Future[list[dict[str, Any]]], tuple[str, str]
        ] = {ex.submit(collect_wifi, gw_host, "Gateway", script): ("Gateway", gw_host)}
        for host in ap_hosts:
            wifi_futs[ex.submit(collect_wifi, host, name_map[host], script)] = (
                name_map[host],
                host,
            )

        for wfut in concurrent.futures.as_completed(wifi_futs):
            ap_name, host = wifi_futs[wfut]
            try:
                entries, hoststat = wfut.result()
                all_wifi.extend(entries)
                ip = host.split("@")[-1]
                if hoststat:
                    ap_hoststats[ip] = hoststat
                # An AP is considered alive if it returned hoststat (means SSH worked),
                # even if no clients are currently associated.
                if host != gw_host and (entries or hoststat):
                    alive_ap_ips.append(ip)
            except Exception:
                pass

    return all_wifi, alive_ap_ips, ap_ip6_map, ap_hoststats


# ------------------------- Build device list -------------------------


def build_devices(
    leases: dict[str, dict[str, str]],
    arp_states: dict[str, str],
    stale: set[str],
    wifi: list[dict],
    vendors: dict[str, str],
    gw_mac: str,
    gw_ip: str,
    gw_hostname: str,
    alive_ap_ips: list[str],
    ndp: dict[str, str] | None = None,
    gw_ip6: str = "",
    arp_ips: dict[str, str] | None = None,
    arp_hostnames: dict[str, str] | None = None,
    prev_state: dict[str, "StateEntry"] | None = None,
    rates: dict[str, dict[str, int | None]] | None = None,
) -> list[Device]:
    """Merge all data sources into a unified device list."""
    # Inject gateway as a lease
    ndp = dict(ndp or {})
    if gw_mac and gw_ip:
        leases[gw_mac] = {"ip": gw_ip, "hostname": gw_hostname}
        if gw_ip6:
            ndp[gw_mac] = gw_ip6

    # Build force_online set (gateway + alive APs)
    force_online: set[str] = set()
    if gw_mac:
        force_online.add(gw_mac)

    # WiFi lookups by MAC (keep strongest signal)
    wifi_by_mac: dict[str, dict] = {}
    for w in wifi:
        mac = w["mac"]
        if mac not in wifi_by_mac or (w.get("signal") or -999) > (
            wifi_by_mac[mac].get("signal") or -999
        ):
            wifi_by_mac[mac] = w

    # Mark alive AP MACs as force_online via lease IP matching
    for ap_ip in alive_ap_ips:
        for mac, info in leases.items():
            if info["ip"] == ap_ip:
                force_online.add(mac)

    devices: list[Device] = []
    seen: set[str] = set()

    for mac, info in leases.items():
        online = False
        wifi_entry = wifi_by_mac.get(mac)

        if wifi_entry:
            online = True
        elif mac in force_online:
            online = True
        elif mac in arp_states and mac not in stale:
            online = True

        d = Device(
            mac=mac,
            ip=info["ip"],
            ip6=ndp.get(mac, ""),
            hostname=info["hostname"],
            vendor=vendors.get(mac, ""),
            online=online,
        )
        if wifi_entry:
            d.connection = "wifi"
            d.ap = wifi_entry["ap"]
            d.band = wifi_entry["band"]
            d.channel = wifi_entry.get("channel")
            d.essid = wifi_entry["essid"]
            d.signal = wifi_entry["signal"]
            d.noise = wifi_entry.get("noise")
            d.snr = wifi_entry.get("snr")
            d.tx_rate = wifi_entry["tx_rate"]
            d.rx_rate = wifi_entry.get("rx_rate")
            d.exp_tput = wifi_entry.get("exp_tput")
        elif (
            prev_state
            and mac in prev_state
            and (prev_state[mac].connection == "wifi" or prev_state[mac].ap)
        ):
            # Offline WiFi device — restore last-known connection type so it
            # doesn't incorrectly show as wired while disconnected.
            # Fall back on ap being non-empty in case connection field was
            # saved as "wired" by an older state file.
            p = prev_state[mac]
            d.connection = "wifi"
            d.ap = p.ap
            d.band = p.band
            d.channel = p.channel
            d.essid = p.essid
        if rates and mac in rates:
            d.rx_bps = rates[mac].get("rx_bps")
            d.tx_bps = rates[mac].get("tx_bps")
        devices.append(d)
        seen.add(mac)

    # WiFi-only devices with no DHCP lease
    for mac, w in wifi_by_mac.items():
        if mac in seen:
            continue
        r = rates.get(mac) if rates else None
        devices.append(
            Device(
                mac=mac,
                ip6=ndp.get(mac, ""),
                vendor=vendors.get(mac, ""),
                connection="wifi",
                ap=w["ap"],
                band=w["band"],
                channel=w.get("channel"),
                essid=w["essid"],
                signal=w["signal"],
                noise=w.get("noise"),
                snr=w.get("snr"),
                tx_rate=w["tx_rate"],
                rx_rate=w.get("rx_rate"),
                exp_tput=w.get("exp_tput"),
                rx_bps=r.get("rx_bps") if r else None,
                tx_bps=r.get("tx_bps") if r else None,
                online=True,
            )
        )
        seen.add(mac)

    # ARP-only devices (no DHCP lease, not WiFi) — e.g. macvtap Docker containers
    if arp_ips:
        _arp_hostnames = arp_hostnames or {}
        for mac, ip in arp_ips.items():
            if mac in seen:
                continue
            r = rates.get(mac) if rates else None
            online = mac not in stale
            devices.append(
                Device(
                    mac=mac,
                    ip=ip,
                    ip6=ndp.get(mac, ""),
                    hostname=_arp_hostnames.get(mac, ""),
                    vendor=vendors.get(mac, ""),
                    rx_bps=r.get("rx_bps") if r else None,
                    tx_bps=r.get("tx_bps") if r else None,
                    online=online,
                )
            )

    return devices


# ------------------------- Event detection -------------------------


def load_prev_state() -> dict[str, StateEntry]:
    if not PREV_STATE.exists():
        return {}
    try:
        raw = json.loads(PREV_STATE.read_text())
        return {mac: StateEntry(**data) for mac, data in raw.items()}
    except Exception:
        return {}


def save_state(state: dict[str, StateEntry]) -> None:
    _atomic_write(PREV_STATE, json.dumps({mac: asdict(e) for mac, e in state.items()}))


def device_to_state(
    d: Device,
    miss: int = 0,
    first_seen: float = 0.0,
    last_seen: float = 0.0,
) -> StateEntry:
    return StateEntry(
        mac=d.mac,
        ap=d.ap,
        band=d.band,
        channel=d.channel,
        essid=d.essid,
        signal=d.signal,
        online=d.online,
        hostname=d.hostname,
        vendor=d.vendor,
        ip=d.ip,
        ip6=d.ip6,
        connection=d.connection,
        miss=miss,
        first_seen=first_seen,
        last_seen=last_seen,
        rx_total=d.rx_total,
        tx_total=d.tx_total,
    )


def make_event(
    event_type: str, ts: str, mac: str, d: Any, **extra: Any
) -> dict[str, Any]:
    ev = {
        "ts": ts,
        "type": event_type,
        "mac": mac,
        "hostname": d.hostname,
        "vendor": d.vendor,
        "ip": d.ip,
        "ip6": getattr(d, "ip6", ""),
        "connection": getattr(d, "connection", "wired"),
        "ap": d.ap,
        "band": d.band,
        "essid": d.essid,
    }
    if d.signal is not None:
        ev["signal"] = d.signal
    if getattr(d, "channel", None) is not None:
        ev["channel"] = d.channel
    if getattr(d, "rx_total", None) is not None:
        ev["rx_total"] = d.rx_total
    if getattr(d, "tx_total", None) is not None:
        ev["tx_total"] = d.tx_total
    ev.update(extra)
    return ev


def detect_events(
    prev: dict[str, StateEntry],
    devices: list[Device],
    ts: str,
    ap_macs: set[str] | None = None,
) -> tuple[list[dict], dict[str, StateEntry]]:
    """Compare current devices with previous state. Returns (events, new_state).
    ap_macs: set of MAC addresses that are access points — use ap_online/ap_offline events."""
    _ap_macs = ap_macs or set()
    events: list[dict] = []
    new_state: dict[str, StateEntry] = {}
    seen: set[str] = set()
    now = time.time()

    for d in devices:
        mac = d.mac
        seen.add(mac)
        prev_entry = prev.get(mac)
        is_ap = mac in _ap_macs

        # Use raw online state for event detection; stickiness only affects state file
        raw_online = d.online

        if raw_online:
            if prev_entry is None:
                # First time seen — APs emit ap_online, clients emit new_device
                evt = "ap_online" if is_ap else "new_device"
                events.append(make_event(evt, ts, mac, d))
                new_state[mac] = device_to_state(
                    d, miss=0, first_seen=now, last_seen=now
                )
            elif not prev_entry.online and prev_entry.miss >= DISCONNECT_MISS_THRESHOLD:
                evt = "ap_online" if is_ap else "connect"
                events.append(make_event(evt, ts, mac, d))
                new_state[mac] = device_to_state(
                    d, miss=0, first_seen=prev_entry.first_seen or now, last_seen=now
                )
            else:
                # Was online (or in sticky window)
                if prev_entry.online:
                    if d.ap and prev_entry.ap and d.ap != prev_entry.ap:
                        extra: dict[str, Any] = {"from_ap": prev_entry.ap}
                        if prev_entry.signal is not None:
                            extra["from_signal"] = prev_entry.signal
                        events.append(make_event("roam", ts, mac, d, **extra))
                    elif (
                        d.band
                        and prev_entry.band
                        and d.band != prev_entry.band
                        and d.ap == prev_entry.ap
                    ):
                        events.append(
                            make_event(
                                "band_change", ts, mac, d, from_band=prev_entry.band
                            )
                        )
                    if (
                        d.hostname
                        and prev_entry.hostname
                        and d.hostname != prev_entry.hostname
                    ):
                        events.append(
                            make_event(
                                "hostname_change",
                                ts,
                                mac,
                                d,
                                from_hostname=prev_entry.hostname,
                            )
                        )
                fs = prev_entry.first_seen if prev_entry.first_seen else now
                new_state[mac] = device_to_state(
                    d, miss=0, first_seen=fs, last_seen=now
                )
        else:
            # Currently offline — increment miss counter
            miss = (prev_entry.miss + 1) if prev_entry else 1
            was_online = prev_entry.online if prev_entry else False
            prev_last_seen = prev_entry.last_seen if prev_entry else 0.0
            if was_online and miss >= DISCONNECT_MISS_THRESHOLD:
                # Fire disconnect using previous state's AP/band/channel info
                assert prev_entry is not None  # was_online implies prev_entry exists
                extra = {}
                if prev_entry.first_seen:
                    extra["duration"] = int(now - prev_entry.first_seen)
                evt = "ap_offline" if is_ap else "disconnect"
                events.append(make_event(evt, ts, mac, prev_entry, **extra))
                entry = device_to_state(
                    d, miss=miss, first_seen=0.0, last_seen=prev_last_seen
                )
                entry.online = False
                new_state[mac] = entry
            else:
                # Below threshold: stay sticky-online in state file
                entry = device_to_state(
                    d,
                    miss=miss,
                    first_seen=prev_entry.first_seen if prev_entry else 0.0,
                    last_seen=prev_last_seen,
                )
                entry.online = was_online and miss < DISCONNECT_MISS_THRESHOLD
                # Preserve prev AP/band/channel/connection while sticky
                if prev_entry and entry.online:
                    entry.ap = prev_entry.ap
                    entry.band = prev_entry.band
                    entry.channel = prev_entry.channel
                    entry.signal = prev_entry.signal
                    entry.essid = prev_entry.essid
                    entry.connection = prev_entry.connection
                new_state[mac] = entry

        # IP change detection
        if prev_entry and d.ip and prev_entry.ip and d.ip != prev_entry.ip:
            events.append(make_event("ip_change", ts, mac, d, from_ip=prev_entry.ip))
        if prev_entry and d.ip6 and prev_entry.ip6 and d.ip6 != prev_entry.ip6:
            events.append(make_event("ip6_change", ts, mac, d, from_ip6=prev_entry.ip6))

    # Devices missing entirely from current scan
    for mac, prev_entry in prev.items():
        if mac in seen:
            continue
        new_miss = prev_entry.miss + 1
        if prev_entry.online and new_miss >= DISCONNECT_MISS_THRESHOLD:
            extra = {}
            if prev_entry.first_seen:
                extra["duration"] = int(now - prev_entry.first_seen)
            evt = "ap_offline" if mac in _ap_macs else "disconnect"
            events.append(make_event(evt, ts, mac, prev_entry, **extra))
            entry = StateEntry(**asdict(prev_entry))
            entry.online = False
            entry.miss = new_miss
            entry.first_seen = 0.0
            new_state[mac] = entry
        elif new_miss < DISCONNECT_MISS_THRESHOLD + 10:
            entry = StateEntry(**asdict(prev_entry))
            entry.miss = new_miss
            entry.online = prev_entry.online and new_miss < DISCONNECT_MISS_THRESHOLD
            new_state[mac] = entry

    return events, new_state


# ------------------------- Event log management -------------------------


def append_events(events: list[dict]) -> None:
    if not events:
        return
    with EVENT_LOG.open("a") as f:
        f.write("".join(json.dumps(ev) + "\n" for ev in events))


def prune_events() -> None:
    """Prune events older than retention period and enforce line cap.

    Time-based pruning runs once per hour at :00.
    Line cap is enforced on every call as a backstop against flapping devices.
    """
    if not EVENT_LOG.exists():
        return
    cutoff = (
        (datetime.now(timezone.utc) - timedelta(days=EVENT_RETENTION_DAYS))
        .isoformat()
        .replace("+00:00", "Z")
    )
    lines = [ln for ln in EVENT_LOG.read_text().splitlines() if ln.strip()]
    before = len(lines)

    # Time-based pruning — run once per hour
    if datetime.now().minute == 0:
        kept = []
        for line in lines:
            try:
                if json.loads(line).get("ts", "") >= cutoff:
                    kept.append(line)
            except json.JSONDecodeError:
                continue
        lines = kept

    # Line cap — always enforced; keep the most recent MAX_EVENT_LINES entries
    if len(lines) > MAX_EVENT_LINES:
        lines = lines[-MAX_EVENT_LINES:]

    if len(lines) != before:
        _atomic_write(EVENT_LOG, "\n".join(lines) + ("\n" if lines else ""))


def detect_wan_events(
    wan_ip: str, wan_ip6: str, gw_mac: str, gw_hostname: str, ts: str
) -> list[dict]:
    """Compare current WAN state to previous. Returns list of WAN events."""
    prev: dict = {}
    if WAN_EVENT_STATE.exists():
        try:
            prev = json.loads(WAN_EVENT_STATE.read_text())
        except Exception:
            pass

    events: list[dict] = []
    prev_ip = prev.get("wan_ip", "")
    prev_ip6 = prev.get("wan_ip6", "")
    first_run = not prev

    # Stub device-like object for make_event
    class _WAN:
        hostname = gw_hostname
        vendor = ""
        ip = wan_ip
        ip6 = wan_ip6
        ap = ""
        band = ""
        essid = ""
        connection = "wired"
        signal = None
        channel = None

    if not first_run:
        if wan_ip and not prev_ip:
            events.append(make_event("wan_online", ts, gw_mac, _WAN()))
        elif prev_ip and not wan_ip:
            events.append(make_event("wan_offline", ts, gw_mac, _WAN()))
        elif wan_ip and prev_ip and wan_ip != prev_ip:
            events.append(
                make_event("wan_ip_change", ts, gw_mac, _WAN(), from_ip=prev_ip)
            )
        if wan_ip6 and prev_ip6 and wan_ip6 != prev_ip6:
            events.append(
                make_event("wan_ip6_change", ts, gw_mac, _WAN(), from_ip6=prev_ip6)
            )

    _atomic_write(
        WAN_EVENT_STATE,
        json.dumps({"wan_ip": wan_ip, "wan_ip6": wan_ip6}),
    )
    return events


def prune_old_state(state: dict[str, StateEntry]) -> dict[str, StateEntry]:
    """Drop offline entries not seen online in STATE_MAX_AGE_DAYS days.

    Prevents unbounded growth from randomised MACs and other one-time devices.
    Online entries are always kept regardless of age.
    """
    cutoff = time.time() - STATE_MAX_AGE_DAYS * 86400
    return {
        mac: entry
        for mac, entry in state.items()
        if entry.online or entry.last_seen >= cutoff
    }


# ------------------------- Main -------------------------


def main() -> None:
    scan_start = time.time()

    if len(sys.argv) < 2:
        emit_error(f"usage: {sys.argv[0]} <gw_user@gw_ip> [ap_user@ap_ip] ...")

    gw_host = sys.argv[1]
    ap_hosts = sys.argv[2:]

    for arg in [gw_host, *ap_hosts]:
        if not _valid_host_arg(arg):
            emit_error(f"invalid host argument {arg!r} — expected user@<ipv4>")

    _ensure_known_hosts([gw_host, *ap_hosts])

    # Load wifi collection script from CONFIG_DIR
    wifi_script_path = CONFIG_DIR / "openwrt_collector.sh"
    if not wifi_script_path.exists():
        emit_error(f"openwrt_collector.sh not found in {CONFIG_DIR}")
    wifi_script = wifi_script_path.read_text()

    # Load previous state early — used for stale-ping filtering and graceful degradation
    prev_state = load_prev_state()

    # Collect gateway data (leases, ARP, bandwidth, WAN)
    gw_data = collect_gateway(gw_host)
    if not gw_data:
        # Gateway unreachable — serve cached device list rather than failing hard.
        # State and event log are left untouched (no new data to write).
        if prev_state:
            cached = [
                Device(
                    mac=e.mac,
                    ip=e.ip,
                    ip6=e.ip6,
                    hostname=e.hostname,
                    vendor=e.vendor,
                    connection=e.connection,
                    ap=e.ap,
                    band=e.band,
                    channel=e.channel,
                    essid=e.essid,
                    signal=e.signal,
                    online=e.online,
                )
                for e in prev_state.values()
            ]
            print(
                json.dumps(
                    {
                        "device_count": sum(1 for d in cached if d.online),
                        "scan_duration": round(time.time() - scan_start, 2),
                        "wan_ip": "",
                        "wan_ip6": "",
                        "wan_rx_rate": None,
                        "wan_tx_rate": None,
                        "partial": True,
                        "devices": [asdict(d) for d in cached],
                    }
                )
            )
            sys.exit(0)
        emit_error("gateway unreachable")

    leases = parse_leases(gw_data["leases"])
    arp_states, stale, arp_ips = parse_arp(gw_data["arp"])
    ndp = parse_ndp(gw_data["ndp"])

    # Ping STALE devices — skip those already confirmed offline to save latency
    # Include ARP-only devices (no lease) in the stale-ping candidate list
    stale_ips = []
    for mac in stale:
        ip = leases.get(mac, {}).get("ip") or arp_ips.get(mac, "")
        if ip and (
            prev_state.get(mac) is None
            or prev_state[mac].miss < DISCONNECT_MISS_THRESHOLD
        ):
            stale_ips.append(ip)
    if stale_ips:
        refreshed_arp = ping_stale(gw_host, stale_ips)
        if refreshed_arp:
            arp_states, stale, arp_ips = parse_arp(refreshed_arp)

    # Resolve missing hostnames via reverse DNS
    dns_cache = load_kv_cache(DNS_CACHE)
    lease_macs_in_leases = set(leases.keys())
    arp_only_macs = {mac for mac in arp_ips if mac not in lease_macs_in_leases}
    missing_ips = [
        info["ip"] for info in leases.values() if not info["hostname"] and info["ip"]
    ] + [arp_ips[mac] for mac in arp_only_macs if arp_ips[mac] not in dns_cache]
    if missing_ips:
        resolved = resolve_hostnames(gw_host, missing_ips, dns_cache)
        for mac, info in leases.items():
            if not info["hostname"] and info["ip"] in resolved:
                info["hostname"] = resolved[info["ip"]]
        save_kv_cache(DNS_CACHE, dns_cache)

    # Build hostname map for ARP-only devices from DNS cache
    arp_hostnames = {mac: dns_cache.get(arp_ips[mac], "") for mac in arp_only_macs}

    # Collect WiFi from all APs in parallel
    wifi, alive_aps, ap_ip6_map, ap_hoststats = collect_all_wifi(
        gw_host, ap_hosts, wifi_script
    )

    # Inject AP IPv6 addresses into ndp (APs never appear in gateway's NDP with global addresses)
    for ap_ip, ap_ip6 in ap_ip6_map.items():
        for mac, info in leases.items():
            if info["ip"] == ap_ip:
                ndp[mac] = ap_ip6
                break

    # Vendor lookup
    vendor_cache = load_kv_cache(VENDOR_CACHE)
    oui_db = load_oui_db()
    all_macs = set(leases.keys()) | {w["mac"] for w in wifi} | set(arp_ips.keys())
    lookup_vendors(all_macs, vendor_cache, oui_db)
    save_kv_cache(VENDOR_CACHE, vendor_cache)

    # WAN bandwidth rates
    rx_rate, tx_rate, wan_rx_total, wan_tx_total, wan_bw_since = compute_wan_rates(
        gw_data["rx_bytes"], gw_data["tx_bytes"]
    )

    # Per-device bandwidth rates
    # WiFi: aggregate station byte counters by MAC (strongest-signal AP wins)
    wifi_bytes: dict[str, dict[str, int]] = {}
    wifi_signals: dict[str, int] = {}
    for w in wifi:
        mac = w["mac"]
        sig = w.get("signal") or -999
        if mac not in wifi_bytes or sig > wifi_signals.get(mac, -999):
            wifi_bytes[mac] = {
                "ul": w.get("sta_ul_bytes", 0),
                "dl": w.get("sta_dl_bytes", 0),
            }
            wifi_signals[mac] = sig
    # Wired: conntrack bytes per IP → MAC
    ip_to_mac = {info["ip"]: mac for mac, info in leases.items() if info.get("ip")}
    ip_to_mac.update({ip: mac for mac, ip in arp_ips.items()})
    wired_bytes: dict[str, dict[str, int]] = {}
    for ip, bw in parse_conntrack(gw_data.get("conntrack", [])).items():
        wmac = ip_to_mac.get(ip)
        if wmac and wmac not in wifi_bytes:  # WiFi station dump takes priority
            wired_bytes[wmac] = bw
    device_rates, device_accum = compute_device_rates(wifi_bytes, wired_bytes)

    # Build unified device list
    devices = build_devices(
        leases,
        arp_states,
        stale,
        wifi,
        vendor_cache,
        gw_data["gw_mac"],
        gw_data["gw_ip"],
        gw_data["gw_hostname"],
        alive_aps,
        ndp=ndp,
        gw_ip6=gw_data["gw_ip6"],
        arp_ips=arp_ips,
        arp_hostnames=arp_hostnames,
        prev_state=prev_state,
        rates=device_rates,
    )

    # Aggregate client rates onto AP devices; inject WAN rates onto gateway device
    ap_rx: dict[str, int] = {}
    ap_tx: dict[str, int] = {}
    for d in devices:
        if d.connection == "wifi" and d.ap and d.online:
            if d.rx_bps is not None:
                ap_rx[d.ap] = ap_rx.get(d.ap, 0) + d.rx_bps
            if d.tx_bps is not None:
                ap_tx[d.ap] = ap_tx.get(d.ap, 0) + d.tx_bps
    for d in devices:
        if d.hostname and d.hostname in ap_rx:
            d.rx_bps = ap_rx[d.hostname]
            d.tx_bps = ap_tx.get(d.hostname, 0)
        elif d.mac == gw_data["gw_mac"]:
            d.rx_bps = rx_rate
            d.tx_bps = tx_rate
            d.rx_total = wan_rx_total
            d.tx_total = wan_tx_total
            d.bw_since = wan_bw_since

    # Apply accumulated totals to all devices
    for d in devices:
        if d.mac in device_accum:
            d.rx_total = device_accum[d.mac].get("rx")
            d.tx_total = device_accum[d.mac].get("tx")
            d.bw_since = device_accum[d.mac].get("since")

    # Event detection
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Build AP MAC set for infrastructure event labelling
    ap_ips = {h.split("@")[1] for h in ap_hosts}
    ap_macs = {mac for mac, info in leases.items() if info.get("ip") in ap_ips}
    events, new_state = detect_events(prev_state, devices, ts, ap_macs=ap_macs)
    wan_events = detect_wan_events(
        gw_data.get("wan_ip", ""),
        gw_data.get("wan_ip6", "") or gw_data.get("gw_ip6", ""),
        gw_data.get("gw_mac", ""),
        gw_data.get("gw_hostname", "gw"),
        ts,
    )
    events = wan_events + events
    new_state = prune_old_state(new_state)
    # Carry first_seen from state back into Device for JSON output.
    # Use whichever of first_seen / bw_since is older — bw_since is never
    # reset on reconnect so it acts as a floor for the true discovery time.
    for d in devices:
        if d.mac in new_state and new_state[d.mac].first_seen:
            d.first_seen = new_state[d.mac].first_seen
        if d.bw_since and (not d.first_seen or d.bw_since < d.first_seen):
            d.first_seen = float(d.bw_since)
    save_state(new_state)
    append_events(events)
    prune_events()

    # Host CPU/RAM stats (gateway + APs)
    host_stats: dict[str, dict[str, Any]] = {}
    gw_ip_key = gw_host.split("@")[-1]
    gw_stats = compute_host_stats(
        gw_ip_key, parse_hoststat(gw_data.get("hoststat", []))
    )
    if gw_stats:
        host_stats[gw_ip_key] = {
            "hostname": gw_data.get("gw_hostname", "gateway"),
            **gw_stats,
        }
    for ap_host in ap_hosts:
        ap_ip = ap_host.split("@")[-1]
        stats = compute_host_stats(ap_ip, parse_hoststat(ap_hoststats.get(ap_ip, [])))
        if stats:
            host_stats[ap_ip] = {
                "hostname": next(
                    (d.hostname for d in devices if d.ip == ap_ip and d.hostname),
                    ap_ip,
                ),
                **stats,
            }

    # dnsmasq DNS cache stats
    dns_stats = compute_dns_rates(parse_dns_stats(gw_data.get("dns", [])))

    # Output JSON for HA sensor
    output = {
        "device_count": sum(1 for d in devices if d.online),
        "scan_duration": round(time.time() - scan_start, 2),
        "wan_ip": gw_data["wan_ip"],
        "wan_ip6": gw_data["wan_ip6"] or gw_data["gw_ip6"],
        "gateway_mac": gw_data["gw_mac"],
        "wan_rx_rate": rx_rate,
        "wan_tx_rate": tx_rate,
        "host_stats": host_stats,
        "dns_stats": dns_stats,
        "devices": [
            {
                "mac": d.mac,
                "ip": d.ip,
                "ip6": d.ip6,
                "hostname": d.hostname,
                "vendor": d.vendor,
                "connection": d.connection,
                "ap": d.ap,
                "band": d.band,
                "channel": d.channel,
                "essid": d.essid,
                "signal": d.signal,
                "noise": d.noise,
                "snr": d.snr,
                "tx_rate": d.tx_rate,
                "rx_rate": d.rx_rate,
                "exp_tput": d.exp_tput,
                "rx_bps": d.rx_bps,
                "tx_bps": d.tx_bps,
                "rx_total": d.rx_total,
                "tx_total": d.tx_total,
                "bw_since": d.bw_since,
                "first_seen": int(d.first_seen) if d.first_seen else None,
                "online": d.online,
            }
            for d in devices
        ],
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
