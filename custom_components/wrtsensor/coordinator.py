"""WrtsensorCoordinator — async DataUpdateCoordinator wrapping all data collection.

Parsing functions are ported verbatim from wrtsensor.py; only the SSH transport
and delta-state handling differ (asyncssh + in-memory state instead of files).
"""

from __future__ import annotations

import asyncio
from collections import deque
import ipaddress
import json
import logging
import os
import random
import re
import time
import urllib.request
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import asyncssh
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .parser import (
    _is_random_mac,
    _valid_ipv4,
    parse_arp,
    parse_board_model,
    parse_conntrack,
    parse_dns_stats,
    parse_hoststat,
    parse_leases,
    parse_ndp,
    parse_wifi_output,
)
from .const import (
    BW_MAX_AGE_S,
    BW_MAX_RATE_BPS,
    BW_MIN_ELAPSED_S,
    COLLECTOR_REMOTE_PATH,
    COLLECTOR_SCRIPT_NAME,
    CONF_AP_HOSTS,
    CONF_GATEWAY_HOST,
    CONF_DISCONNECT_THRESHOLD,
    CONF_LAN_IFACE,
    CONF_SCAN_INTERVAL,
    CONF_SSH_KEY_PATH,
    CONF_SSH_PORT,
    CONF_WAN_IFACE,
    DEFAULT_DHCP_LEASES,
    DEFAULT_DISCONNECT_THRESHOLD,
    DEFAULT_LAN_IFACE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SSH_KEY,
    DEFAULT_SSH_PORT,
    DEFAULT_WAN_IFACE,
    DISCONNECT_MISS_THRESHOLD,
    DOMAIN,
    STATE_DIR_HA,
    STATE_DIR_LOCAL,
    STATE_MAX_AGE_DAYS,
)

_LOGGER = logging.getLogger(__name__)
DNS_HISTORY_MAX_AGE_S = 25 * 60 * 60
DNS_WINDOW_S = 24 * 60 * 60


# ── Data types ────────────────────────────────────────────────────────────────


@dataclass
class Device:
    mac: str
    ip: str = ""
    ip6: str = ""
    hostname: str = ""
    vendor: str = ""
    connection: str = "wired"
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
    first_seen: float = 0.0
    last_seen: float = 0.0
    rx_total: int | None = None
    tx_total: int | None = None
    last_event_ts: dict[str, float] = field(default_factory=dict)


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
    prev_state: dict[str, StateEntry] | None = None,
    rates: dict[str, dict[str, int | None]] | None = None,
) -> list[Device]:
    ndp = dict(ndp or {})
    if gw_mac and gw_ip:
        leases[gw_mac] = {"ip": gw_ip, "hostname": gw_hostname}
        if gw_ip6:
            ndp[gw_mac] = gw_ip6
    force_online: set[str] = set()
    if gw_mac:
        force_online.add(gw_mac)
    wifi_by_mac: dict[str, dict] = {}
    for w in wifi:
        mac = w["mac"]
        if mac not in wifi_by_mac or (w.get("signal") or -999) > (
            wifi_by_mac[mac].get("signal") or -999
        ):
            wifi_by_mac[mac] = w
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


def make_event(
    event_type: str, ts: str, mac: str, d: Any, **extra: Any
) -> dict[str, Any]:
    ev: dict[str, Any] = {
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


def device_to_state(
    d: Device, miss: int = 0, first_seen: float = 0.0, last_seen: float = 0.0
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


def _can_emit(
    event_type: str, prev_entry: StateEntry | None, min_interval_s: float, now: float
) -> bool:
    """Return True if enough time has passed since the last emission of this event type."""
    if min_interval_s <= 0 or prev_entry is None:
        return True
    return (now - prev_entry.last_event_ts.get(event_type, 0.0)) >= min_interval_s


def _carry_ts(
    entry: StateEntry, prev: StateEntry | None, emitted: dict[str, float]
) -> None:
    """Copy last_event_ts from prev into entry, then overlay any newly emitted timestamps."""
    if prev:
        entry.last_event_ts = {**prev.last_event_ts, **emitted}
    elif emitted:
        entry.last_event_ts = dict(emitted)


def detect_events(
    prev: dict[str, StateEntry],
    devices: list[Device],
    ts: str,
    ap_macs: set[str] | None = None,
    min_interval_s: float = 0.0,
    disconnect_miss_threshold: int = DISCONNECT_MISS_THRESHOLD,
) -> tuple[list[dict], dict[str, StateEntry]]:
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
        raw_online = d.online
        emitted: dict[str, float] = {}
        if raw_online:
            if prev_entry is None:
                evt = "ap_online" if is_ap else "new_device"
                events.append(make_event(evt, ts, mac, d))
                emitted[evt] = now
                new_state[mac] = device_to_state(
                    d, miss=0, first_seen=now, last_seen=now
                )
            elif not prev_entry.online and prev_entry.miss >= disconnect_miss_threshold:
                evt = "ap_online" if is_ap else "connect"
                if _can_emit(evt, prev_entry, min_interval_s, now):
                    events.append(make_event(evt, ts, mac, d))
                    emitted[evt] = now
                new_state[mac] = device_to_state(
                    d, miss=0, first_seen=prev_entry.first_seen or now, last_seen=now
                )
            else:
                if prev_entry.online:
                    if d.ap and prev_entry.ap and d.ap != prev_entry.ap:
                        extra: dict[str, Any] = {"from_ap": prev_entry.ap}
                        if prev_entry.signal is not None:
                            extra["from_signal"] = prev_entry.signal
                        if _can_emit("roam", prev_entry, min_interval_s, now):
                            events.append(make_event("roam", ts, mac, d, **extra))
                            emitted["roam"] = now
                    elif (
                        d.band
                        and prev_entry.band
                        and d.band != prev_entry.band
                        and d.ap == prev_entry.ap
                    ):
                        if _can_emit("band_change", prev_entry, min_interval_s, now):
                            events.append(
                                make_event(
                                    "band_change", ts, mac, d, from_band=prev_entry.band
                                )
                            )
                            emitted["band_change"] = now
                    if (
                        d.hostname
                        and prev_entry.hostname
                        and d.hostname != prev_entry.hostname
                    ):
                        if _can_emit(
                            "hostname_change", prev_entry, min_interval_s, now
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
                            emitted["hostname_change"] = now
                fs = prev_entry.first_seen if prev_entry.first_seen else now
                new_state[mac] = device_to_state(
                    d, miss=0, first_seen=fs, last_seen=now
                )
        else:
            miss = (prev_entry.miss + 1) if prev_entry else 1
            was_online = prev_entry.online if prev_entry else False
            prev_last_seen = prev_entry.last_seen if prev_entry else 0.0
            if was_online and miss >= disconnect_miss_threshold:
                assert prev_entry is not None
                extra2: dict[str, Any] = {}
                if prev_entry.first_seen:
                    extra2["duration"] = int(now - prev_entry.first_seen)
                evt2 = "ap_offline" if is_ap else "disconnect"
                if _can_emit(evt2, prev_entry, min_interval_s, now):
                    events.append(make_event(evt2, ts, mac, prev_entry, **extra2))
                    emitted[evt2] = now
                entry = device_to_state(
                    d, miss=miss, first_seen=0.0, last_seen=prev_last_seen
                )
                entry.online = False
                new_state[mac] = entry
            else:
                entry = device_to_state(
                    d,
                    miss=miss,
                    first_seen=prev_entry.first_seen if prev_entry else 0.0,
                    last_seen=prev_last_seen,
                )
                entry.online = was_online and miss < disconnect_miss_threshold
                if prev_entry and entry.online:
                    entry.ap = prev_entry.ap
                    entry.band = prev_entry.band
                    entry.channel = prev_entry.channel
                    entry.signal = prev_entry.signal
                    entry.essid = prev_entry.essid
                    entry.connection = prev_entry.connection
                new_state[mac] = entry
        if prev_entry and d.ip and prev_entry.ip and d.ip != prev_entry.ip:
            if _can_emit("ip_change", prev_entry, min_interval_s, now):
                events.append(
                    make_event("ip_change", ts, mac, d, from_ip=prev_entry.ip)
                )
                emitted["ip_change"] = now
        if prev_entry and d.ip6 and prev_entry.ip6 and d.ip6 != prev_entry.ip6:
            if _can_emit("ip6_change", prev_entry, min_interval_s, now):
                events.append(
                    make_event("ip6_change", ts, mac, d, from_ip6=prev_entry.ip6)
                )
                emitted["ip6_change"] = now
        _carry_ts(new_state[mac], prev_entry, emitted)
    for mac, prev_entry in prev.items():
        if mac in seen:
            continue
        new_miss = prev_entry.miss + 1
        emitted = {}
        if prev_entry.online and new_miss >= disconnect_miss_threshold:
            extra3: dict[str, Any] = {}
            if prev_entry.first_seen:
                extra3["duration"] = int(now - prev_entry.first_seen)
            evt3 = "ap_offline" if mac in _ap_macs else "disconnect"
            if _can_emit(evt3, prev_entry, min_interval_s, now):
                events.append(make_event(evt3, ts, mac, prev_entry, **extra3))
                emitted[evt3] = now
            entry2 = StateEntry(**asdict(prev_entry))
            entry2.online = False
            entry2.miss = new_miss
            entry2.first_seen = 0.0
            _carry_ts(entry2, prev_entry, emitted)
            new_state[mac] = entry2
        elif new_miss < disconnect_miss_threshold + 10:
            entry2 = StateEntry(**asdict(prev_entry))
            entry2.miss = new_miss
            entry2.online = prev_entry.online and new_miss < disconnect_miss_threshold
            _carry_ts(entry2, prev_entry, emitted)
            new_state[mac] = entry2
    return events, new_state


def remap_random_macs(
    devices: list[Device],
    prev_state: dict[str, StateEntry],
    miss_threshold: int,
) -> list[Device]:
    """Remap locally-administered (random) MACs to their canonical MAC via hostname.

    When a device rotates its MAC, a matching hostname in prev_state lets us
    restore identity continuity so detect_events sees the same MAC it always did,
    preventing false new_device events and entity proliferation.
    """
    count: dict[str, int] = {}
    h2mac: dict[str, str] = {}
    for mac, entry in prev_state.items():
        if not entry.hostname:
            continue
        count[entry.hostname] = count.get(entry.hostname, 0) + 1
        if entry.online or entry.miss < miss_threshold:
            h2mac[entry.hostname] = mac
    # Only use unambiguous hostnames (exactly one device with that name)
    h2mac = {h: m for h, m in h2mac.items() if count.get(h, 0) == 1}

    current_macs = {d.mac for d in devices}
    used: set[str] = set()
    result: list[Device] = []
    for d in devices:
        if _is_random_mac(d.mac) and d.hostname and d.hostname in h2mac:
            canonical = h2mac[d.hostname]
            if (
                canonical != d.mac
                and canonical not in current_macs
                and canonical not in used
            ):
                d = replace(d, mac=canonical)
                used.add(canonical)
        result.append(d)
    return result


def prune_old_state(state: dict[str, StateEntry]) -> dict[str, StateEntry]:
    cutoff = time.time() - STATE_MAX_AGE_DAYS * 86400
    return {mac: e for mac, e in state.items() if e.online or e.last_seen >= cutoff}


def lookup_vendors(
    macs: set[str], cache: dict[str, str], oui_db: dict[str, str]
) -> None:
    for mac in macs:
        oui = mac[:8].replace(":", "-")
        if mac in cache:
            if not cache[mac] and oui in oui_db:
                cache[mac] = oui_db[oui]
            continue
        cache[mac] = oui_db.get(oui, "")


def _atomic_write(path: Path, text: str) -> None:
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


# ── Coordinator ────────────────────────────────────────────────────────────────


class WrtsensorCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    EVENT_BUFFER_SIZE = 500

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        data = {**entry.data, **entry.options}
        self._gateway_host: str | None = data.get(CONF_GATEWAY_HOST, "") or None
        self._ssh_key = data.get(CONF_SSH_KEY_PATH, DEFAULT_SSH_KEY)
        self._ssh_port: int = int(data.get(CONF_SSH_PORT, DEFAULT_SSH_PORT))
        raw_aps = data.get(CONF_AP_HOSTS, "")
        self._ap_hosts: list[str] = [h.strip() for h in raw_aps.split(",") if h.strip()]
        self._lan_iface = data.get(CONF_LAN_IFACE, DEFAULT_LAN_IFACE)
        self._wan_iface = data.get(CONF_WAN_IFACE, DEFAULT_WAN_IFACE)
        self._disconnect_threshold_s = int(
            data.get(CONF_DISCONNECT_THRESHOLD, DEFAULT_DISCONNECT_THRESHOLD)
        )

        # State dir: /dev/shm on HA, /tmp/netscan locally
        state_dir = (
            Path(STATE_DIR_HA) if Path(STATE_DIR_HA).exists() else Path(STATE_DIR_LOCAL)
        )
        state_dir.mkdir(parents=True, exist_ok=True)
        self._state_dir = state_dir

        # File paths (persistent across HA restarts)
        self._prev_state_path = state_dir / ".netscan_prev_state.json"
        self._vendor_cache_path = state_dir / ".netscan_mac_vendors"
        self._dns_cache_path = state_dir / ".netscan_dns_cache"
        self._dns_history_path = state_dir / ".netscan_dns_history.jsonl"

        # Integration package dir (for collector script + OUI db)
        self._pkg_dir = Path(__file__).resolve().parent
        self._oui_db_path = self._pkg_dir / "oui.db"
        self._oui_txt_path = self._pkg_dir / "oui.txt"

        # In-memory delta state (replaces the file-per-delta approach)
        self._bw_state: dict[str, Any] = {}
        self._cpu_state: dict[str, dict[str, int]] = {}
        self._dns_state: dict[str, Any] = {}
        self._device_bw: dict[str, Any] = {}
        self._device_bw_accum: dict[str, dict[str, int]] = {}
        self._wan_event_state: dict[str, str] = {}
        self._prev_state: dict[str, StateEntry] = {}
        self._host_models: dict[str, tuple[str, str]] = {}  # ip → (model, board_name)
        self._event_buffer: deque[dict[str, Any]] = deque(maxlen=self.EVENT_BUFFER_SIZE)

        # Cached file data (warm across scans)
        self._vendor_cache: dict[str, str] = {}
        self._dns_cache: dict[str, str] = {}
        self._oui_db: dict[str, str] = {}
        self._collector_script: str = ""

        scan_interval = timedelta(
            seconds=int(data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        )
        self._disconnect_threshold_miss = max(
            1, int(self._disconnect_threshold_s / scan_interval.total_seconds())
        )
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=scan_interval)

    # ── Setup ──────────────────────────────────────────────────────────────────

    async def async_setup(self) -> None:
        """Load caches and deploy collector script. Called once from async_setup_entry."""
        await self.hass.async_add_executor_job(self._load_caches)
        await self._deploy_collector()

    def _load_caches(self) -> None:
        self._vendor_cache = load_kv_cache(self._vendor_cache_path)
        self._dns_cache = load_kv_cache(self._dns_cache_path)
        self._oui_db = self._load_oui_db()
        if self._prev_state_path.exists():
            try:
                raw = json.loads(self._prev_state_path.read_text())
                self._prev_state = {mac: StateEntry(**d) for mac, d in raw.items()}
            except Exception:
                self._prev_state = {}
        script_path = self._pkg_dir / COLLECTOR_SCRIPT_NAME
        if script_path.exists():
            self._collector_script = script_path.read_text()

    def _load_oui_db(self) -> dict[str, str]:
        if not self._oui_db_path.exists() and self._oui_txt_path.exists():
            db: dict[str, str] = {}
            for line in self._oui_txt_path.read_text(errors="ignore").splitlines():
                if "(hex)" in line:
                    parts = line.split("(hex)", 1)
                    if len(parts) == 2:
                        oui = parts[0].strip().upper()
                        vendor = parts[1].strip()
                        if oui:
                            db[oui] = vendor
            _atomic_write(
                self._oui_db_path, "\n".join(f"{k}|{v}" for k, v in db.items())
            )
            return db
        if not self._oui_db_path.exists():
            self.hass.async_create_task(self._download_oui_db())
            return {}
        db = {}
        for line in self._oui_db_path.read_text().splitlines():
            if "|" in line:
                k, _, v = line.partition("|")
                db[k.strip().upper()] = v.strip()
        return db

    async def _download_oui_db(self) -> None:
        urls = [
            "https://standards-oui.ieee.org/oui/oui.txt",
            "https://www.wireshark.org/download/automated/data/manuf",
        ]
        for url in urls:
            try:
                _LOGGER.info("Downloading OUI database from %s", url)
                req = urllib.request.Request(
                    url, headers={"User-Agent": "Mozilla/5.0 (wrtsensor)"}
                )
                data = await self.hass.async_add_executor_job(
                    lambda r=req: urllib.request.urlopen(r, timeout=40).read(
                        50 * 1024 * 1024 + 1
                    )
                )
                if len(data) > 50 * 1024 * 1024:
                    raise ValueError("OUI response exceeds 50 MB limit")
                await self.hass.async_add_executor_job(
                    self._oui_txt_path.write_bytes, data
                )
                self._oui_db = await self.hass.async_add_executor_job(self._load_oui_db)
                _LOGGER.info("OUI database loaded (%d entries)", len(self._oui_db))
                return
            except Exception as e:
                _LOGGER.warning("OUI download from %s failed: %s", url, e)
        _LOGGER.warning(
            "All OUI sources failed; vendor lookup unavailable until next restart"
        )

    async def _deploy_collector(self) -> None:
        """SFTP openwrt_collector.sh to /tmp/ on each AP if missing or outdated."""
        if not self._collector_script:
            _LOGGER.warning("openwrt_collector.sh not found in integration package")
            return
        hosts = self._ap_hosts[:]
        # Also deploy to gateway (it runs the script for gateway-side WiFi)
        all_hosts = ([self._gateway_host] if self._gateway_host else []) + hosts
        await asyncio.gather(
            *[self._deploy_to_host(h) for h in all_hosts], return_exceptions=True
        )

    async def _deploy_to_host(self, host: str) -> None:
        await asyncio.sleep(random.uniform(0, 2))
        try:
            async with asyncssh.connect(
                host,
                port=self._ssh_port,
                username="root",
                client_keys=[self._ssh_key],
                known_hosts=None,
                connect_timeout=8,
                keepalive_interval=15,
                keepalive_count_max=3,
            ) as conn:
                result = await conn.run(
                    f"cat {COLLECTOR_REMOTE_PATH} 2>/dev/null", check=False
                )
                # Bytes-exact comparison, no CRLF normalization
                if result.stdout.encode() == self._collector_script.encode():
                    return
                # Write via stdin pipe — dropbear has no SFTP subsystem
                await conn.run(
                    f"cat > {COLLECTOR_REMOTE_PATH}",
                    input=self._collector_script,
                    check=True,
                )
                await conn.run(f"chmod +x {COLLECTOR_REMOTE_PATH}", check=False)
                _LOGGER.debug("[%s] Deployed collector script", host)
        except (
            asyncssh.Error,
            asyncio.TimeoutError,
            OSError,
        ) as e:
            _LOGGER.warning("[%s] Failed to deploy collector: %s", host, e)

    # ── SSH helpers ────────────────────────────────────────────────────────────

    async def _ssh_run(self, host: str, command: str, timeout: int = 20) -> str:
        try:
            async with asyncio.timeout(timeout):
                async with asyncssh.connect(
                    host,
                    port=self._ssh_port,
                    username="root",
                    client_keys=[self._ssh_key],
                    known_hosts=None,
                    connect_timeout=5,
                    keepalive_interval=15,
                    keepalive_count_max=3,
                ) as conn:
                    result = await conn.run(command)
                    return result.stdout or ""
        except (
            asyncssh.Error,
            asyncio.TimeoutError,
            OSError,
        ) as e:
            _LOGGER.warning("[%s] SSH failed: %s", host, e)
            return ""

    # ── Data collection ────────────────────────────────────────────────────────

    async def _collect_gateway(self) -> dict[str, Any]:
        if not self._gateway_host:
            return {}
        li = self._lan_iface
        wi = self._wan_iface
        dhcp = DEFAULT_DHCP_LEASES
        cmd = (
            f"echo '---LEASES---'; cat {dhcp} 2>/dev/null; "
            f"echo '---ARP---'; ip -4 neigh show dev {li} 2>/dev/null; "
            "echo '---NDP---'; "
            f"{{ ping6 -c2 -W1 -I {li} ff02::1 2>/dev/null || ping -6 -c2 -W1 -I {li} ff02::1 2>/dev/null; }} >/dev/null; "
            f"ip -6 neigh show dev {li} 2>/dev/null | grep -v '^fe80'; "
            "echo '---GW---'; "
            f"ip addr show {li} 2>/dev/null | grep 'link/ether' | awk '{{print $2}}'; "
            f"ip addr show {li} 2>/dev/null | grep ' inet ' | awk '{{split($2,a,\"/\"); print a[1]}}'; "
            "cat /proc/sys/kernel/hostname; "
            f"ip addr show {li} 2>/dev/null | grep ' inet6 ' | grep -v ' fe80' | awk '{{split($2,a,\"/\"); print a[1]}}' | head -1; "
            "echo '---WAN---'; "
            f"ip addr show {wi} 2>/dev/null | grep ' inet ' | awk '{{split($2,a,\"/\"); print a[1]}}'; "
            f"ip -6 route 2>/dev/null | awk '/default/ {{for(i=1;i<=NF;i++) if($i==\"dev\") {{print $(i+1); exit}}}}' | xargs -I{{}} ip addr show {{}} 2>/dev/null | grep ' inet6 ' | grep -v ' fe80' | awk '{{split($2,a,\"/\"); print a[1]}}' | head -1; "
            "echo '---BW---'; "
            f"cat /sys/class/net/{wi}/statistics/rx_bytes 2>/dev/null; "
            f"cat /sys/class/net/{wi}/statistics/tx_bytes 2>/dev/null; "
            "echo '---HOSTSTAT---'; "
            "grep '^cpu ' /proc/stat 2>/dev/null; "
            "awk '/^MemTotal:/ {t=$2} /^MemAvailable:/ {a=$2} END{print t, a}' /proc/meminfo 2>/dev/null; "
            'df / 2>/dev/null | awk \'NR==2 {gsub("%","",$5); print $5+0}\'; '
            "echo '---DNS---'; "
            "kill -USR1 $(pidof dnsmasq) 2>/dev/null; sleep 1; "
            "logread -l 60 2>/dev/null | grep 'dnsmasq\\[' | grep -E 'cache size|queries forwarded|avg\\. latency' | tail -20; "
            "echo '---CONNTRACK---'; cat /proc/net/nf_conntrack 2>/dev/null; "
            "echo '---BOARD---'; ubus call system board 2>/dev/null"
        )
        out = await self._ssh_run(self._gateway_host, cmd, timeout=25)
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
        hoststat_info = [ln for ln in sections.get("HOSTSTAT", []) if ln.strip()]
        dns_info = [ln for ln in sections.get("DNS", []) if ln.strip()]
        return {
            "leases": [ln for ln in sections.get("LEASES", []) if ln.strip()],
            "arp": [ln for ln in sections.get("ARP", []) if ln.strip()],
            "ndp": [ln for ln in sections.get("NDP", []) if ln.strip()],
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
            "conntrack": [ln for ln in sections.get("CONNTRACK", []) if ln.strip()],
            "hoststat": hoststat_info,
            "dns": dns_info,
            "gw_board": sections.get("BOARD", [""])[0].strip(),
        }

    async def _get_ap_info(self, host: str) -> tuple[str, str, list[str], list[str]]:
        li = self._lan_iface
        cmd = (
            "echo '---HOST---'; cat /proc/sys/kernel/hostname; "
            f"echo '---IP6---'; ip addr show {li} 2>/dev/null | grep ' inet6 ' | grep -v ' fe80' | awk '{{split($2,a,\"/\"); print a[1]}}'; "
            f"echo '---ARP---'; ip -4 neigh show dev {li} 2>/dev/null; "
            "echo '---NDP---'; "
            f"ip -6 neigh show dev {li} 2>/dev/null | grep -v '^fe80'"
        )
        out = await self._ssh_run(host, cmd, timeout=10)
        sections: dict[str, list[str]] = {}
        current: str | None = None
        for line in out.splitlines():
            m = re.match(r"^---([A-Z0-9]+)---$", line)
            if m:
                current = m.group(1)
                sections[current] = []
            elif current:
                sections[current].append(line)
        host_lines = [ln.strip() for ln in sections.get("HOST", []) if ln.strip()]
        hostname = host_lines[0] if host_lines else host
        ip6 = ""
        for raw in sections.get("IP6", []):
            addr = raw.strip()
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
                break
            if not ip6:
                ip6 = addr
        arp_lines = [ln for ln in sections.get("ARP", []) if ln.strip()]
        ndp_lines = [ln for ln in sections.get("NDP", []) if ln.strip()]
        return hostname or host, ip6, arp_lines, ndp_lines

    async def _collect_wifi(
        self, host: str, ap_name: str
    ) -> tuple[list[dict[str, Any]], list[str]]:
        out = await self._ssh_run(host, f"sh {COLLECTOR_REMOTE_PATH}", timeout=12)
        model, board_name = parse_board_model(out)
        if model:
            self._host_models[host] = (model, board_name)
        return parse_wifi_output(out, ap_name)

    async def _ping_stale(self, ips: list[str]) -> list[str]:
        safe = [ip for ip in ips if _valid_ipv4(ip)]
        if not safe:
            return []
        ping_cmd = " ".join(f"ping -c1 -W1 {ip} >/dev/null 2>&1 &" for ip in safe)
        li = self._lan_iface
        full = f"{ping_cmd} wait; ip -4 neigh show dev {li} 2>/dev/null"
        out = await self._ssh_run(self._gateway_host, full, timeout=12)
        return [ln for ln in out.splitlines() if ln.strip()]

    async def _resolve_hostnames(self, ips: list[str]) -> dict[str, str]:
        need = [ip for ip in ips if ip not in self._dns_cache and _valid_ipv4(ip)]
        if not need:
            return {ip: self._dns_cache[ip] for ip in ips if self._dns_cache.get(ip)}
        cmd_parts = [
            f"echo \"{ip}|$(nslookup {ip} 2>/dev/null | grep 'name = ' | sed 's/.*name = //;s/\\.$//' | head -1)\""
            for ip in need
        ]
        out = await self._ssh_run(self._gateway_host, "; ".join(cmd_parts), timeout=10)
        for line in out.splitlines():
            if "|" in line:
                ip, _, hostname = line.partition("|")
                self._dns_cache[ip] = hostname
        return {ip: self._dns_cache.get(ip, "") for ip in ips}

    # ── Delta computations (in-memory state) ──────────────────────────────────

    def _compute_wan_rates(
        self, rx: int | None, tx: int | None
    ) -> tuple[int | None, int | None, int, int, int]:
        if rx is None or tx is None:
            return None, None, 0, 0, int(time.time())
        now = int(time.time())
        rx_rate = tx_rate = None
        rx_total = tx_total = 0
        since = now
        p = self._bw_state
        if p:
            prev_ts, prev_rx, prev_tx = p.get("ts", 0), p.get("rx", 0), p.get("tx", 0)
            elapsed = now - prev_ts
            if 0 < elapsed < BW_MAX_AGE_S:
                rx_rate = max(0, (rx - prev_rx) // elapsed) if rx >= prev_rx else None
                tx_rate = max(0, (tx - prev_tx) // elapsed) if tx >= prev_tx else None
            rx_total = p.get("rx_total", 0)
            tx_total = p.get("tx_total", 0)
            since = p.get("since", now)
            if rx >= prev_rx:
                rx_total += rx - prev_rx
            if tx >= prev_tx:
                tx_total += tx - prev_tx
        self._bw_state = {
            "ts": now,
            "rx": rx,
            "tx": tx,
            "rx_total": rx_total,
            "tx_total": tx_total,
            "since": since,
        }
        return rx_rate, tx_rate, rx_total, tx_total, since

    def _compute_host_stats(
        self, host_key: str, current: dict[str, int] | None
    ) -> dict[str, float | None] | None:
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
        cpu = None
        p = self._cpu_state.get(host_key)
        if p:
            d_busy = current["busy"] - p.get("busy", 0)
            d_idle = current["idle"] - p.get("idle", 0)
            total = d_busy + d_idle
            if total > 0 and d_busy >= 0 and d_idle >= 0:
                cpu = round(100.0 * d_busy / total, 1)
        self._cpu_state[host_key] = {"busy": current["busy"], "idle": current["idle"]}
        return {"cpu": cpu, "ram": ram, "disk": current.get("disk")}

    def _compute_dns_rates(
        self, current: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if current is None:
            return None
        now = int(time.time())
        hits_rate = misses_rate = None
        p = self._dns_state
        if p:
            elapsed = now - int(p.get("ts", 0))
            if 0 < elapsed < BW_MAX_AGE_S:
                d_hits = current["hits"] - p.get("hits", 0)
                d_misses = current["misses"] - p.get("misses", 0)
                if d_hits >= 0 and d_misses >= 0:
                    hits_rate = round(d_hits / elapsed, 2)
                    misses_rate = round(d_misses / elapsed, 2)
        self._dns_state = {
            "ts": now,
            "hits": current["hits"],
            "misses": current["misses"],
        }
        history = self._append_dns_history(now, current)
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
            "last_24h": self._compute_dns_window(history, now),
            "lifetime": lifetime,
            "latency_ms": current.get("latency_ms"),
            "servers": current.get("servers", []),
        }

    def _load_dns_history(self) -> list[dict[str, int]]:
        if not self._dns_history_path.exists():
            return []
        history: list[dict[str, int]] = []
        for line in self._dns_history_path.read_text().splitlines():
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

    def _append_dns_history(
        self, now: int, current: dict[str, Any]
    ) -> list[dict[str, int]]:
        cutoff = now - DNS_HISTORY_MAX_AGE_S
        sample = {
            "ts": now,
            "hits": int(current["hits"]),
            "misses": int(current["misses"]),
        }
        history = [entry for entry in self._load_dns_history() if entry["ts"] >= cutoff]
        history.append(sample)
        text = "".join(
            json.dumps(entry, separators=(",", ":")) + "\n" for entry in history
        )
        _atomic_write(self._dns_history_path, text)
        return history

    def _compute_dns_window(
        self, history: list[dict[str, int]], now: int
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
            # Use the latest clean sample at or before the 24h boundary.
            if sample["ts"] <= cutoff:
                baseline = sample
            else:
                break
        if baseline is None:
            return None
        return _dns_rollup(baseline, current, label="last 24h")

    def _compute_device_rates(
        self,
        wifi_bytes: dict[str, dict[str, int]],
        wired_bytes: dict[str, dict[str, int]],
    ) -> tuple[dict[str, dict[str, int | None]], dict[str, dict[str, int]]]:
        now = time.time()
        p = self._device_bw
        prev_ts = float(p.get("ts", 0.0))
        prev_wifi = p.get("wifi", {})
        prev_wired = p.get("wired", {})
        elapsed = now - prev_ts
        rates: dict[str, dict[str, int | None]] = {}
        deltas: dict[str, dict[str, int]] = {}
        if BW_MIN_ELAPSED_S <= elapsed < BW_MAX_AGE_S:
            for mac, curr in wifi_bytes.items():
                prev_m = prev_wifi.get(mac)
                if not prev_m:
                    continue
                d_ul = curr["ul"] - prev_m.get("ul", 0)
                d_dl = curr["dl"] - prev_m.get("dl", 0)
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
                    continue
                prev_m = prev_wired.get(mac)
                if not prev_m:
                    continue
                d_ul = curr["ul"] - prev_m.get("ul", 0)
                d_dl = curr["dl"] - prev_m.get("dl", 0)
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
        self._device_bw = {"ts": now, "wifi": wifi_bytes, "wired": wired_bytes}
        accum = self._device_bw_accum
        for mac, delta in deltas.items():
            if mac not in accum:
                accum[mac] = {"rx": 0, "tx": 0, "since": int(now)}
            accum[mac]["rx"] = accum[mac].get("rx", 0) + delta["rx"]
            accum[mac]["tx"] = accum[mac].get("tx", 0) + delta["tx"]
        return rates, accum

    def _detect_wan_events(
        self, wan_ip: str, wan_ip6: str, gw_mac: str, gw_hostname: str, ts: str
    ) -> list[dict]:
        prev = self._wan_event_state
        events: list[dict] = []
        prev_ip = prev.get("wan_ip", "")
        prev_ip6 = prev.get("wan_ip6", "")
        first_run = not prev

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
        self._wan_event_state = {"wan_ip": wan_ip, "wan_ip6": wan_ip6}
        return events

    def _save_state(self, state: dict[str, StateEntry]) -> None:
        self._prev_state = state
        _atomic_write(
            self._prev_state_path,
            json.dumps({mac: asdict(e) for mac, e in state.items()}),
        )

    def _append_event_buffer(self, events: list[dict[str, Any]]) -> None:
        if events:
            self._event_buffer.extend(events)

    def get_recent_events(self) -> list[dict[str, Any]]:
        return list(self._event_buffer)

    def get_event_count(self) -> int:
        return len(self._event_buffer)

    def get_event_buffer_size(self) -> int:
        return self.EVENT_BUFFER_SIZE

    # ── Main update ───────────────────────────────────────────────────────────

    async def _async_update_data(self) -> dict[str, Any]:
        scan_start = time.time()

        # Collect gateway data
        gw_data = await self._collect_gateway()
        gateway_absent = self._gateway_host is None
        if not gw_data and not gateway_absent:
            # Gateway configured but unreachable — serve cached device list
            if self._prev_state:
                cached_devices = [
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
                    for e in self._prev_state.values()
                ]
                return {
                    "device_count": sum(1 for d in cached_devices if d.online),
                    "scan_duration": round(time.time() - scan_start, 2),
                    "wan_ip": "",
                    "wan_ip6": "",
                    "gateway_mac": "",
                    "wan_rx_rate": None,
                    "wan_tx_rate": None,
                    "host_stats": {},
                    "dns_stats": None,
                    "devices": [asdict(d) for d in cached_devices],
                    "partial": True,
                }
            raise UpdateFailed("Gateway unreachable")

        if gw_data:
            leases = parse_leases(gw_data["leases"])
            arp_states, stale, arp_ips = parse_arp(gw_data["arp"])
            ndp = parse_ndp(gw_data["ndp"])
            gw_board_json = gw_data.get("gw_board", "")
            if gw_board_json and self._gateway_host:
                gw_model, gw_board_name = parse_board_model("BOARD|" + gw_board_json)
                if gw_model:
                    self._host_models[self._gateway_host] = (gw_model, gw_board_name)
        else:
            leases = {}
            arp_states = {}
            stale = set()
            arp_ips = {}
            ndp = {}

        # Ping stale devices
        stale_ips = []
        for mac in stale:
            ip = leases.get(mac, {}).get("ip") or arp_ips.get(mac, "")
            if ip and (
                self._prev_state.get(mac) is None
                or self._prev_state[mac].miss < DISCONNECT_MISS_THRESHOLD
            ):
                stale_ips.append(ip)
        if stale_ips and gw_data:
            refreshed = await self._ping_stale(stale_ips)
            if refreshed:
                arp_states, stale, arp_ips = parse_arp(refreshed)

        # Resolve missing hostnames
        lease_macs = set(leases.keys())
        arp_only_macs = {mac for mac in arp_ips if mac not in lease_macs}
        missing_ips = [
            info["ip"]
            for info in leases.values()
            if not info["hostname"] and info["ip"]
        ] + [
            arp_ips[mac] for mac in arp_only_macs if arp_ips[mac] not in self._dns_cache
        ]
        if missing_ips and gw_data:
            resolved = await self._resolve_hostnames(missing_ips)
            for mac, info in leases.items():
                if not info["hostname"] and info["ip"] in resolved:
                    info["hostname"] = resolved[info["ip"]]
            await self.hass.async_add_executor_job(
                save_kv_cache, self._dns_cache_path, self._dns_cache
            )
        arp_hostnames = {
            mac: self._dns_cache.get(arp_ips[mac], "") for mac in arp_only_macs
        }

        # Collect WiFi from gateway + all APs in parallel
        name_map: dict[str, str] = {}
        ap_ip6_map: dict[str, str] = {}
        if self._ap_hosts:
            ap_info_results = await asyncio.gather(
                *[self._get_ap_info(h) for h in self._ap_hosts], return_exceptions=True
            )
            for host, result in zip(self._ap_hosts, ap_info_results):
                if isinstance(result, Exception):
                    name_map[host] = host
                    continue
                hostname, ip6, ap_arp_lines, ap_ndp_lines = result
                name_map[host] = hostname or host
                if ip6:
                    ap_ip6_map[host] = ip6
                # Gateway-less mode: union per-AP neigh tables into master maps.
                # Prefer REACHABLE over STALE when same MAC seen on multiple APs.
                if gateway_absent:
                    ap_states, ap_stale, ap_arp_ips = parse_arp(ap_arp_lines)
                    for mac, state in ap_states.items():
                        if mac not in arp_states or (
                            arp_states[mac] != "REACHABLE" and state == "REACHABLE"
                        ):
                            arp_states[mac] = state
                    stale |= ap_stale - {
                        m for m, s in arp_states.items() if s == "REACHABLE"
                    }
                    for mac, ip in ap_arp_ips.items():
                        arp_ips.setdefault(mac, ip)
                    for mac, ip6_addr in parse_ndp(ap_ndp_lines).items():
                        ndp.setdefault(mac, ip6_addr)

        wifi_tasks: list = []
        if self._gateway_host:
            wifi_tasks.append(self._collect_wifi(self._gateway_host, "Gateway"))
        for host in self._ap_hosts:
            wifi_tasks.append(self._collect_wifi(host, name_map.get(host, host)))
        wifi_results = await asyncio.gather(*wifi_tasks, return_exceptions=True)

        all_wifi: list[dict] = []
        alive_ap_ips: list[str] = []
        ap_hoststats: dict[str, list[str]] = {}

        idx = 0
        if self._gateway_host:
            gw_wifi_result = wifi_results[idx]
            idx += 1
            if not isinstance(gw_wifi_result, Exception):
                entries, hoststat = gw_wifi_result
                all_wifi.extend(entries)
                if hoststat:
                    ap_hoststats[self._gateway_host] = hoststat

        for host in self._ap_hosts:
            result = wifi_results[idx]
            idx += 1
            if isinstance(result, Exception):
                continue
            entries, hoststat = result
            all_wifi.extend(entries)
            ip = host
            if hoststat:
                ap_hoststats[ip] = hoststat
            if entries or hoststat:
                alive_ap_ips.append(ip)

        # Inject AP IPv6 into NDP
        for ap_host, ap_ip6 in ap_ip6_map.items():
            matched = False
            for mac, info in leases.items():
                if info["ip"] == ap_host:
                    ndp[mac] = ap_ip6
                    matched = True
                    break
            if not matched:
                for mac, ip in arp_ips.items():
                    if ip == ap_host:
                        ndp[mac] = ap_ip6
                        break

        # Vendor lookup
        all_macs = (
            set(leases.keys()) | {w["mac"] for w in all_wifi} | set(arp_ips.keys())
        )
        lookup_vendors(all_macs, self._vendor_cache, self._oui_db)
        await self.hass.async_add_executor_job(
            save_kv_cache, self._vendor_cache_path, self._vendor_cache
        )

        # WAN bandwidth
        if gw_data:
            rx_rate, tx_rate, wan_rx_total, wan_tx_total, wan_bw_since = (
                self._compute_wan_rates(gw_data["rx_bytes"], gw_data["tx_bytes"])
            )
        else:
            rx_rate = tx_rate = None
            wan_rx_total = wan_tx_total = 0
            wan_bw_since = int(time.time())

        # Per-device bandwidth
        wifi_bytes: dict[str, dict[str, int]] = {}
        wifi_signals: dict[str, int] = {}
        for w in all_wifi:
            mac = w["mac"]
            sig = w.get("signal") or -999
            if mac not in wifi_bytes or sig > wifi_signals.get(mac, -999):
                wifi_bytes[mac] = {
                    "ul": w.get("sta_ul_bytes", 0),
                    "dl": w.get("sta_dl_bytes", 0),
                }
                wifi_signals[mac] = sig
        ip_to_mac = {info["ip"]: mac for mac, info in leases.items() if info.get("ip")}
        ip_to_mac.update({ip: mac for mac, ip in arp_ips.items()})
        wired_bytes: dict[str, dict[str, int]] = {}
        conntrack_lines = gw_data.get("conntrack", []) if gw_data else []
        for ip, bw in parse_conntrack(conntrack_lines).items():
            wmac = ip_to_mac.get(ip)
            if wmac and wmac not in wifi_bytes:
                wired_bytes[wmac] = bw
        device_rates, device_accum = self._compute_device_rates(wifi_bytes, wired_bytes)

        # Build device list
        gw_mac = gw_data.get("gw_mac", "") if gw_data else ""
        gw_ip = gw_data.get("gw_ip", "") if gw_data else ""
        gw_hostname = gw_data.get("gw_hostname", "") if gw_data else ""
        gw_ip6 = gw_data.get("gw_ip6", "") if gw_data else ""
        devices = build_devices(
            leases,
            arp_states,
            stale,
            all_wifi,
            self._vendor_cache,
            gw_mac,
            gw_ip,
            gw_hostname,
            alive_ap_ips,
            ndp=ndp,
            gw_ip6=gw_ip6,
            arp_ips=arp_ips,
            arp_hostnames=arp_hostnames,
            prev_state=self._prev_state,
            rates=device_rates,
        )

        # Restore identity for devices that rotated their random MAC
        devices = remap_random_macs(
            devices, self._prev_state, self._disconnect_threshold_miss
        )

        # Aggregate AP bandwidth; inject WAN onto gateway
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
            elif gw_mac and d.mac == gw_mac:
                d.rx_bps = rx_rate
                d.tx_bps = tx_rate
                d.rx_total = wan_rx_total
                d.tx_total = wan_tx_total
                d.bw_since = wan_bw_since

        # Apply accumulated totals
        for d in devices:
            if d.mac in device_accum:
                d.rx_total = device_accum[d.mac].get("rx")
                d.tx_total = device_accum[d.mac].get("tx")
                d.bw_since = device_accum[d.mac].get("since")

        # Event detection
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        ap_ips_set = set(self._ap_hosts)
        ap_macs = {mac for mac, info in leases.items() if info.get("ip") in ap_ips_set}
        events, new_state = detect_events(
            self._prev_state,
            devices,
            ts,
            ap_macs=ap_macs,
            min_interval_s=self.update_interval.total_seconds() * 2,
            disconnect_miss_threshold=self._disconnect_threshold_miss,
        )
        if gw_data:
            wan_events = self._detect_wan_events(
                gw_data.get("wan_ip", ""),
                gw_data.get("wan_ip6", "") or gw_data.get("gw_ip6", ""),
                gw_data.get("gw_mac", ""),
                gw_data.get("gw_hostname", "gw"),
                ts,
            )
        else:
            wan_events = []
        all_events = wan_events + events
        new_state = prune_old_state(new_state)

        # Carry first_seen back into devices
        for d in devices:
            if d.mac in new_state and new_state[d.mac].first_seen:
                d.first_seen = new_state[d.mac].first_seen
            if d.bw_since and (not d.first_seen or d.bw_since < d.first_seen):
                d.first_seen = float(d.bw_since)

        await self.hass.async_add_executor_job(self._save_state, new_state)
        self._append_event_buffer(all_events)

        # Host stats
        host_stats: dict[str, dict[str, Any]] = {}
        if self._gateway_host and gw_data:
            gw_stats = self._compute_host_stats(
                self._gateway_host, parse_hoststat(gw_data.get("hoststat", []))
            )
            if gw_stats:
                gw_model_info = self._host_models.get(self._gateway_host, ("", ""))
                host_stats[self._gateway_host] = {
                    "hostname": gw_data.get("gw_hostname", "gateway"),
                    "model": gw_model_info[0],
                    "board_name": gw_model_info[1],
                    **gw_stats,
                }
        for host in self._ap_hosts:
            stats = self._compute_host_stats(
                host, parse_hoststat(ap_hoststats.get(host, []))
            )
            if stats:
                ap_model_info = self._host_models.get(host, ("", ""))
                host_stats[host] = {
                    "hostname": next(
                        (d.hostname for d in devices if d.ip == host and d.hostname),
                        host,
                    ),
                    "model": ap_model_info[0],
                    "board_name": ap_model_info[1],
                    **stats,
                }

        # DNS stats
        if gw_data:
            dns_stats = self._compute_dns_rates(parse_dns_stats(gw_data.get("dns", [])))
        else:
            dns_stats = None

        return {
            "device_count": sum(1 for d in devices if d.online),
            "scan_duration": round(time.time() - scan_start, 2),
            "wan_ip": gw_data.get("wan_ip", "") if gw_data else "",
            "wan_ip6": (gw_data.get("wan_ip6") or gw_data.get("gw_ip6", ""))
            if gw_data
            else "",
            "gateway_mac": gw_mac,
            "wan_rx_rate": rx_rate,
            "wan_tx_rate": tx_rate,
            "host_stats": host_stats,
            "dns_stats": dns_stats,
            "devices": [asdict(d) for d in devices],
            "partial": False,
            # Pass through for entity platform use
            "_gw_mac": gw_mac,
            "_gw_hostname": gw_hostname,
            "_ap_hosts": self._ap_hosts,
        }
