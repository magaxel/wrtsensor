"""Tests for WrtsensorCoordinator._async_update_data and role detection."""

import asyncio
import json
import sys
import time
import types
from contextlib import ExitStack
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

coord_mod = sys.modules["custom_components.wrtsensor.coordinator"]
const_mod = sys.modules["custom_components.wrtsensor.const"]

WrtsensorCoordinator = coord_mod.WrtsensorCoordinator
StateEntry = coord_mod.StateEntry
build_devices = coord_mod.build_devices
apply_configured_host_names = coord_mod.apply_configured_host_names
UpdateFailed = sys.modules["homeassistant.helpers.update_coordinator"].UpdateFailed

_ROOT = Path(__file__).parent.parent
_WRT = _ROOT / "custom_components" / "wrtsensor"


# ── Test infrastructure ────────────────────────────────────────────────────────


class _FakeConfigEntries:
    def __init__(self):
        self.updates: list[dict] = []

    def async_update_entry(self, entry, *, data=None, options=None, version=None):
        self.updates.append({"data": data, "options": options, "version": version})
        if data is not None:
            entry.data = data
        if options is not None:
            entry.options = options
        if version is not None:
            entry.version = version


class _FakeHass:
    def __init__(self):
        self.config_entries = _FakeConfigEntries()
        self.config = types.SimpleNamespace(path=lambda *p: str(Path("/config", *p)))

    async def async_add_executor_job(self, fn, *args):
        return None

    def async_create_task(self, coro):
        coro.close()


class _FakeEntry:
    version = 1
    entry_id = "test-entry"

    def __init__(self, data: dict | None = None, options: dict | None = None):
        self.data = data or {
            "hosts": "192.0.2.1",
            "ssh_key_path": "/tmp/test_key",
            "detected_roles": {"192.0.2.1": "gateway"},
            "disconnect_threshold_s": 120,
        }
        self.options = options or {}


def _hosts_and_roles(gateway_host, ap_hosts, switch_hosts):
    """Build a (hosts CSV, detected_roles cache) pair like the config flow stores."""
    from custom_components.wrtsensor.hosts import parse_host_endpoint

    aps = [h.strip() for h in ap_hosts.split(",") if h.strip()]
    switches = [h.strip() for h in switch_hosts.split(",") if h.strip()]
    parts = ([gateway_host] if gateway_host else []) + aps + switches
    roles: dict[str, str] = {}
    if gateway_host:
        roles[parse_host_endpoint(gateway_host).host] = "gateway"
    for h in aps:
        roles[parse_host_endpoint(h).host] = "ap"
    for h in switches:
        roles[parse_host_endpoint(h).host] = "switch"
    return ",".join(parts), roles


def _make_coordinator(
    *,
    ap_hosts: str = "",
    switch_hosts: str = "",
    gateway_host: str = "192.0.2.1",
    options: dict | None = None,
) -> WrtsensorCoordinator:
    hass = _FakeHass()
    hosts, roles = _hosts_and_roles(gateway_host, ap_hosts, switch_hosts)
    entry = _FakeEntry(
        data={
            "hosts": hosts,
            "ssh_key_path": "/tmp/test_key",
            "detected_roles": roles,
            "disconnect_threshold_s": 120,
        }
    )
    entry.options = options or {}
    c = WrtsensorCoordinator(hass, entry)
    c.hass = hass
    c.update_interval = timedelta(seconds=60)
    return c


_MINIMAL_GW = {
    "leases": [],
    "arp": [],
    "ndp": [],
    "gw_mac": "AA:BB:CC:DD:EE:FF",
    "gw_ip": "192.0.2.1",
    "gw_hostname": "gateway",
    "gw_ip6": "",
    "wan_ip": "1.2.3.4",
    "wan_ip6": "",
    "rx_bytes": None,
    "tx_bytes": None,
    "conntrack": [],
    "hoststat": [],
    "dns": [],
}


def test_compute_device_rates_prefers_port_bytes_over_conntrack():
    c = _make_coordinator()
    mac = "AA:BB:CC:DD:EE:01"
    c._device_bw = {
        "ts": time.time() - 60,
        "wifi": {},
        "wired": {mac: {"ul": 0, "dl": 0}},
        "port": {mac: {"ul": 0, "dl": 0}},
    }
    _rates, accum = c._compute_device_rates(
        {},  # wifi
        {mac: {"ul": 6000, "dl": 6000}},  # conntrack — must be ignored
        {mac: {"ul": 600, "dl": 12000}},  # per-port — preferred
    )
    # Per-port counters win, with switch->device direction swap (ul->tx, dl->rx).
    assert accum[mac]["tx"] == 600
    assert accum[mac]["rx"] == 12000
    assert c._device_bw["port"] == {mac: {"ul": 600, "dl": 12000}}


def test_compute_device_rates_discards_delta_when_port_changes():
    """Recabling a device to a different port must not credit phantom traffic.

    The two ports' lifetime counters are unrelated, so their difference is
    meaningless. A 2 GB gap over a 60 s poll is only ~33 MB/s — well under
    BW_MAX_RATE_BPS — so the rate sanity cap does not catch this.
    """
    c = _make_coordinator()
    mac = "AA:BB:CC:DD:EE:03"
    c._device_bw = {
        "ts": time.time() - 60,
        "wifi": {},
        "wired": {},
        "port": {mac: {"ul": 0, "dl": 0}},
        "port_src": {mac: "192.0.2.21:5"},
    }
    _rates, accum = c._compute_device_rates(
        {},
        {},
        {mac: {"ul": 0, "dl": 2_000_000_000}},  # lan7's lifetime counters
        {mac: "192.0.2.21:7"},  # now on a different port
    )
    assert mac not in accum
    # The new port's counters are still recorded, so the next poll — same port,
    # same source — produces a real delta.
    assert c._device_bw["port_src"] == {mac: "192.0.2.21:7"}


def test_compute_device_rates_accumulates_when_port_unchanged():
    c = _make_coordinator()
    mac = "AA:BB:CC:DD:EE:04"
    c._device_bw = {
        "ts": time.time() - 60,
        "wifi": {},
        "wired": {},
        "port": {mac: {"ul": 100, "dl": 200}},
        "port_src": {mac: "192.0.2.21:5"},
    }
    _rates, accum = c._compute_device_rates(
        {},
        {},
        {mac: {"ul": 700, "dl": 1200}},
        {mac: "192.0.2.21:5"},
    )
    assert accum[mac]["tx"] == 600
    assert accum[mac]["rx"] == 1000


def test_compute_device_rates_prefers_wifi_over_port_bytes():
    c = _make_coordinator()
    mac = "AA:BB:CC:DD:EE:02"
    c._device_bw = {
        "ts": time.time() - 60,
        "wifi": {mac: {"ul": 0, "dl": 0}},
        "wired": {},
        "port": {mac: {"ul": 0, "dl": 0}},
    }
    _rates, accum = c._compute_device_rates(
        {mac: {"ul": 100, "dl": 200}},  # wifi — preferred
        {},
        {mac: {"ul": 9000, "dl": 9000}},  # per-port — ignored for this MAC
    )
    assert accum[mac]["tx"] == 100
    assert accum[mac]["rx"] == 200


def test_compute_device_rates_port_counter_reset_yields_zero_delta():
    c = _make_coordinator()
    mac = "AA:BB:CC:DD:EE:03"
    c._device_bw = {
        "ts": time.time() - 60,
        "wifi": {},
        "wired": {},
        "port": {mac: {"ul": 10_000, "dl": 20_000}},
    }
    _rates, accum = c._compute_device_rates(
        {}, {}, {mac: {"ul": 5, "dl": 10}}
    )  # counters reset (reboot)
    assert accum[mac]["tx"] == 0
    assert accum[mac]["rx"] == 0


def test_coordinator_parses_inline_ports_and_normalizes_identity():
    c = _make_coordinator(
        gateway_host="[2001:db8::1]:2222",
        ap_hosts="192.0.2.22:2200, 2001:db8::23",
    )

    assert c._gateway_host == "2001:db8::1"
    assert c._ap_hosts == ["192.0.2.22", "2001:db8::23"]
    assert c._endpoint_ports == {
        "2001:db8::1": 2222,
        "192.0.2.22": 2200,
        "2001:db8::23": 22,
    }


def test_coordinator_parses_switch_endpoints():
    c = _make_coordinator(switch_hosts="192.0.2.24:2222, 2001:db8::24")
    assert c._switch_hosts == ["192.0.2.24", "2001:db8::24"]
    assert c._endpoint_ports["192.0.2.24"] == 2222
    assert c._endpoint_ports["2001:db8::24"] == 22


def test_assign_roles_no_wan_topology_stays_gatewayless():
    """A live probe with no wan signal must not promote any host to gateway via
    next-hop votes — the entry stays gateway-less (no WAN/DNS/WG entities)."""
    from custom_components.wrtsensor.detect import RoleSignals

    c = _make_coordinator(
        gateway_host="", ap_hosts="192.0.2.22", switch_hosts="192.0.2.24"
    )
    sigs = {
        # both route to a non-configured external router; neither reports wan
        "192.0.2.22": RoleSignals(wan=False, next_hop="192.0.2.99", wifi=2),
        "192.0.2.24": RoleSignals(wan=False, next_hop="192.0.2.99", wifi=0),
    }
    with patch.object(
        coord_mod,
        "probe_role",
        new=AsyncMock(side_effect=lambda host, key, port=22: sigs[host]),
    ):
        asyncio.run(c._assign_roles())

    assert c._gateway_host is None
    assert c._ap_hosts == ["192.0.2.22"]
    assert c._switch_hosts == ["192.0.2.24"]


def test_assign_roles_no_wan_gateway_via_override():
    """An explicit =gateway override still designates a gateway with no wan."""
    from custom_components.wrtsensor.detect import RoleSignals

    c = _make_coordinator(gateway_host="", ap_hosts="192.0.2.22")
    c._role_overrides = {"192.0.2.22": "gateway"}
    sigs = {"192.0.2.22": RoleSignals(wan=False, next_hop="192.0.2.99", wifi=2)}
    with patch.object(
        coord_mod,
        "probe_role",
        new=AsyncMock(side_effect=lambda host, key, port=22: sigs[host]),
    ):
        asyncio.run(c._assign_roles())

    assert c._gateway_host == "192.0.2.22"


def test_update_attaches_switch_port_from_gateway_fdb():
    """A wired lease device learned on a switch port surfaces switch_port."""
    c = _make_coordinator(gateway_host="192.0.2.1")
    gw_data = {
        **_MINIMAL_GW,
        "leases": ["1700000000 11:22:33:44:55:66 192.0.2.50 nas *"],
        "arp": ["192.0.2.50 lladdr 11:22:33:44:55:66 REACHABLE"],
    }
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(c, "_collect_gateway", new=AsyncMock(return_value=gw_data))
        )
        stack.enter_context(
            patch.object(
                c,
                "_collect_wifi",
                new=AsyncMock(
                    return_value=([], [], {"11:22:33:44:55:66": "lan5"}, "", {})
                ),
            )
        )
        stack.enter_context(
            patch.object(c, "_ping_stale", new=AsyncMock(return_value=[]))
        )
        stack.enter_context(
            patch.object(c, "_resolve_hostnames", new=AsyncMock(return_value={}))
        )
        stack.enter_context(patch.object(c, "_detect_wan_events", return_value=[]))
        result = asyncio.run(c._async_update_data())

    assert result["switch_hosts"] == []
    by_mac = {d["mac"]: d for d in result["devices"]}
    assert by_mac["11:22:33:44:55:66"]["switch_port"] == "5"
    assert by_mac["11:22:33:44:55:66"]["switch_host"] == "192.0.2.1"


def test_update_resolves_vendor_for_fdb_only_switch_device():
    """FDB-only devices must be included in OUI lookup before build_devices."""
    c = _make_coordinator(gateway_host="", switch_hosts="192.0.2.24")
    c._oui_db = {"11-22-33": "Acme Devices"}
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(c, "_collect_gateway", new=AsyncMock(return_value={}))
        )
        stack.enter_context(
            patch.object(
                c,
                "_collect_wifi",
                new=AsyncMock(
                    return_value=([], [], {"11:22:33:44:55:66": "lan12"}, "", {})
                ),
            )
        )
        stack.enter_context(patch.object(c, "_detect_wan_events", return_value=[]))
        result = asyncio.run(c._async_update_data())

    by_mac = {d["mac"]: d for d in result["devices"]}
    assert by_mac["11:22:33:44:55:66"]["switch_port"] == "12"
    assert by_mac["11:22:33:44:55:66"]["switch_host"] == "192.0.2.24"
    assert by_mac["11:22:33:44:55:66"]["vendor"] == "Acme Devices"


def test_coordinator_ignores_legacy_ssh_port():
    c = WrtsensorCoordinator(
        _FakeHass(),
        _FakeEntry(
            data={
                "hosts": "192.0.2.1,192.0.2.22",
                "ssh_key_path": "/tmp/test_key",
                "detected_roles": {"192.0.2.1": "gateway", "192.0.2.22": "ap"},
                "ssh_port": 2222,
                "disconnect_threshold_s": 120,
            }
        ),
    )

    assert c._endpoint_ports == {"192.0.2.1": 22, "192.0.2.22": 22}


def test_coordinator_ignores_stale_scan_interval_option():
    c = WrtsensorCoordinator(
        _FakeHass(),
        _FakeEntry(
            options={
                "scan_interval": 300,
                const_mod.CONF_DISCONNECT_THRESHOLD: 180,
            }
        ),
    )

    assert c.update_interval == const_mod.SCAN_INTERVAL
    assert c._disconnect_threshold_miss == 3


def test_coordinator_ignores_legacy_asu_interval_option():
    c = WrtsensorCoordinator(
        _FakeHass(),
        _FakeEntry(
            options={
                "asu_interval_h": 1,
            }
        ),
    )

    assert c._asu_interval_s == const_mod.DEFAULT_ASU_INTERVAL_H * 3600


def test_oui_cache_paths_use_persistent_config_dir(tmp_path):
    hass = _FakeHass()
    hass.config = types.SimpleNamespace(path=lambda *p: str(tmp_path.joinpath(*p)))
    c = WrtsensorCoordinator(hass, _FakeEntry())

    assert c._oui_cache_dir == tmp_path / ".storage" / "wrtsensor"
    assert c._oui_db_path == c._oui_cache_dir / "oui.db"
    assert c._oui_txt_path == c._oui_cache_dir / "oui.txt"
    assert "custom_components" not in str(c._oui_db_path)


def test_load_caches_creates_oui_cache_dir(tmp_path):
    hass = _FakeHass()
    hass.config = types.SimpleNamespace(path=lambda *p: str(tmp_path.joinpath(*p)))
    c = WrtsensorCoordinator(hass, _FakeEntry())
    c._set_state_dir(tmp_path / "state")

    c._load_caches()

    assert c._oui_cache_dir.is_dir()
    assert c._needs_oui_download is True


# ── Device build state carry-over ─────────────────────────────────────────────


def test_build_devices_drops_previous_wifi_ap_when_ap_no_longer_active():
    devices = build_devices(
        leases={
            "11:22:33:44:55:66": {
                "ip": "192.0.2.50",
                "hostname": "phone",
            }
        },
        arp_states={"11:22:33:44:55:66": "REACHABLE"},
        stale=set(),
        wifi=[],
        vendors={},
        gw_mac="",
        gw_ip="",
        gw_hostname="",
        alive_ap_ips=[],
        prev_state={
            "11:22:33:44:55:66": StateEntry(
                mac="11:22:33:44:55:66",
                ip="192.0.2.50",
                hostname="phone",
                connection="wifi",
                ap="Removed AP",
                band="5GHz",
            )
        },
        active_ap_names={"Living Room AP"},
    )

    assert devices[0].connection == "wired"
    assert devices[0].ap == ""
    assert devices[0].band == ""


def test_build_devices_keeps_previous_wifi_ap_when_ap_still_active():
    devices = build_devices(
        leases={
            "11:22:33:44:55:66": {
                "ip": "192.0.2.50",
                "hostname": "phone",
            }
        },
        arp_states={"11:22:33:44:55:66": "REACHABLE"},
        stale=set(),
        wifi=[],
        vendors={},
        gw_mac="",
        gw_ip="",
        gw_hostname="",
        alive_ap_ips=[],
        prev_state={
            "11:22:33:44:55:66": StateEntry(
                mac="11:22:33:44:55:66",
                ip="192.0.2.50",
                hostname="phone",
                connection="wifi",
                ap="Living Room AP",
                band="5GHz",
            )
        },
        active_ap_names={"Living Room AP"},
    )

    assert devices[0].connection == "wifi"
    assert devices[0].ap == "Living Room AP"
    assert devices[0].band == "5GHz"


def test_build_devices_assigns_switch_port_to_wired_lease_device():
    devices = build_devices(
        leases={"11:22:33:44:55:66": {"ip": "192.0.2.50", "hostname": "nas"}},
        arp_states={"11:22:33:44:55:66": "REACHABLE"},
        stale=set(),
        wifi=[],
        vendors={},
        gw_mac="",
        gw_ip="",
        gw_hostname="",
        alive_ap_ips=[],
        switch_ports={"11:22:33:44:55:66": {"port": "5", "host": "sw1"}},
    )
    assert devices[0].connection == "wired"
    assert devices[0].switch_port == "5"
    assert devices[0].switch_host == "sw1"


def test_build_devices_does_not_label_wifi_device_with_switch_port():
    devices = build_devices(
        leases={},
        arp_states={},
        stale=set(),
        wifi=[
            {
                "mac": "AA:BB:CC:DD:EE:01",
                "ap": "AP1",
                "band": "5GHz",
                "essid": "Net",
                "signal": -50,
                "tx_rate": 100.0,
            }
        ],
        vendors={},
        gw_mac="",
        gw_ip="",
        gw_hostname="",
        alive_ap_ips=[],
        switch_ports={"AA:BB:CC:DD:EE:01": {"port": "8", "host": "sw1"}},
    )
    assert devices[0].connection == "wifi"
    assert devices[0].switch_port == ""
    assert devices[0].switch_host == ""


def test_build_devices_merges_arp_ip_into_wifi_only_device():
    devices = build_devices(
        leases={},
        arp_states={"AA:BB:CC:DD:EE:01": "REACHABLE"},
        stale=set(),
        wifi=[
            {
                "mac": "AA:BB:CC:DD:EE:01",
                "ap": "AP1",
                "band": "5GHz",
                "essid": "Net",
                "signal": -50,
                "tx_rate": 100.0,
            }
        ],
        vendors={},
        gw_mac="",
        gw_ip="",
        gw_hostname="",
        alive_ap_ips=[],
        arp_ips={"AA:BB:CC:DD:EE:01": "192.0.2.50"},
        arp_hostnames={"AA:BB:CC:DD:EE:01": "phone"},
    )

    assert devices[0].connection == "wifi"
    assert devices[0].ip == "192.0.2.50"
    assert devices[0].hostname == "phone"
    assert len(devices) == 1


def test_build_devices_keeps_previous_ip_for_quiet_wifi_device():
    devices = build_devices(
        leases={},
        arp_states={},
        stale=set(),
        wifi=[
            {
                "mac": "AA:BB:CC:DD:EE:01",
                "ap": "AP1",
                "band": "5GHz",
                "essid": "Net",
                "signal": -50,
                "tx_rate": 100.0,
            }
        ],
        vendors={},
        gw_mac="",
        gw_ip="",
        gw_hostname="",
        alive_ap_ips=[],
        prev_state={
            "AA:BB:CC:DD:EE:01": StateEntry(
                mac="AA:BB:CC:DD:EE:01",
                ip="192.0.2.50",
                ip6="2001:db8::50",
                hostname="phone",
            )
        },
    )

    assert devices[0].connection == "wifi"
    assert devices[0].ip == "192.0.2.50"
    assert devices[0].ip6 == "2001:db8::50"
    assert devices[0].hostname == "phone"


def test_build_devices_creates_fdb_only_device_switch_only_topology():
    # No leases/ARP/wifi (pure L2 switch): the device is known only by its
    # forwarding-DB entry and must still surface with its port.
    devices = build_devices(
        leases={},
        arp_states={},
        stale=set(),
        wifi=[],
        vendors={"AA:BB:CC:DD:EE:01": "Acme"},
        gw_mac="",
        gw_ip="",
        gw_hostname="",
        alive_ap_ips=[],
        switch_ports={"AA:BB:CC:DD:EE:01": {"port": "12", "host": "sw1"}},
    )
    assert len(devices) == 1
    assert devices[0].mac == "AA:BB:CC:DD:EE:01"
    assert devices[0].connection == "wired"
    assert devices[0].switch_port == "12"
    assert devices[0].switch_host == "sw1"
    assert devices[0].vendor == "Acme"
    assert devices[0].online is True


# ── Gateway unreachable ───────────────────────────────────────────────────────


def test_gateway_unreachable_with_prev_state_returns_partial():
    c = _make_coordinator()
    c._prev_state = {
        "11:22:33:44:55:66": StateEntry(
            mac="11:22:33:44:55:66", online=True, ip="192.168.1.10"
        ),
        "22:33:44:55:66:77": StateEntry(mac="22:33:44:55:66:77", online=False),
    }
    with patch.object(c, "_collect_gateway", new=AsyncMock(return_value={})):
        result = asyncio.run(c._async_update_data())
    assert result["partial"] is True
    assert result["device_count"] == 1  # only online device counted
    assert result["host_stats"]["192.0.2.1"]["available"] is False


def test_gateway_unreachable_no_prev_raises_update_failed():
    c = _make_coordinator()
    c._prev_state = {}
    with patch.object(c, "_collect_gateway", new=AsyncMock(return_value={})):
        with pytest.raises(UpdateFailed):
            asyncio.run(c._async_update_data())


def test_gateway_unreachable_returns_scan_duration():
    c = _make_coordinator()
    c._prev_state = {
        "11:22:33:44:55:66": StateEntry(mac="11:22:33:44:55:66", online=True),
    }
    with patch.object(c, "_collect_gateway", new=AsyncMock(return_value={})):
        result = asyncio.run(c._async_update_data())
    assert "scan_duration" in result
    assert result["scan_duration"] >= 0


def test_event_buffer_keeps_most_recent_500():
    c = _make_coordinator()

    c._append_event_buffer(
        [{"ts": f"2026-01-01T00:00:{i:02d}Z", "type": "connect"} for i in range(550)]
    )

    assert c.get_event_count() == 500
    assert c.get_recent_events()[0]["ts"] == "2026-01-01T00:00:50Z"


def _write_dns_history(path: Path, samples: list[dict]) -> None:
    enriched = [
        {**sample, "latency_ms": sample.get("latency_ms", 1.0)} for sample in samples
    ]
    path.write_text("".join(json.dumps(sample) + "\n" for sample in enriched))


def _dns_current(
    hits: int,
    misses: int,
    servers: list[dict] | None = None,
) -> dict:
    return {
        "cache_size": 10000,
        "hits": hits,
        "misses": misses,
        "latency_ms": 1.0,
        "servers": servers or [],
    }


def test_dns_stats_last_24h_rollup(tmp_path):
    c = _make_coordinator()
    c._dns_history_path = tmp_path / ".netscan_dns_history.jsonl"
    now = 200000
    _write_dns_history(
        c._dns_history_path,
        [{"ts": now - 86400, "hits": 1000, "misses": 500}],
    )

    with patch.object(coord_mod.time, "time", return_value=now):
        result = c._compute_dns_rates_sync(_dns_current(87400, 43700))

    assert result["last_24h"] == {
        "hits": 86400,
        "misses": 43200,
        "hit_pct": 66.7,
        "hits_per_sec": 1.0,
        "misses_per_sec": 0.5,
        "elapsed_s": 86400,
        "label": "last 24h",
        "latency_ms": 1.0,
        "servers": [],
    }
    assert "lifetime" not in result


def test_dns_stats_first_sample_has_windowed_periods_only(tmp_path):
    c = _make_coordinator()
    c._dns_history_path = tmp_path / ".netscan_dns_history.jsonl"

    with patch.object(coord_mod.time, "time", return_value=200000):
        result = c._compute_dns_rates_sync(_dns_current(120, 30))

    assert result["last_24h"]["label"] == "just started"
    assert result["last_24h"]["elapsed_s"] == 0
    assert result["last_scan"]["label"] == "just started"
    assert "lifetime" not in result


def test_dns_stats_two_consecutive_samples_add_last_scan(tmp_path):
    c = _make_coordinator()
    c._dns_history_path = tmp_path / ".netscan_dns_history.jsonl"

    with patch.object(coord_mod.time, "time", return_value=200000):
        first = c._compute_dns_rates_sync(_dns_current(10000, 2500))
    with patch.object(coord_mod.time, "time", return_value=200060):
        second = c._compute_dns_rates_sync(_dns_current(10060, 2530))

    assert first["last_scan"]["label"] == "just started"
    assert second["last_scan"]["label"] == "last scan"
    assert second["last_scan"]["hits"] == 60
    assert second["last_scan"]["misses"] == 30
    assert second["last_scan"]["elapsed_s"] == 60
    assert second["last_scan"]["hits_per_sec"] is None
    assert second["last_scan"]["misses_per_sec"] is None


def test_dns_stats_partial_history_uses_clean_window_not_lifetime(tmp_path):
    c = _make_coordinator()
    c._dns_history_path = tmp_path / ".netscan_dns_history.jsonl"
    now = 200000
    _write_dns_history(
        c._dns_history_path,
        [{"ts": now - 10 * 3600, "hits": 1000, "misses": 500}],
    )

    with patch.object(coord_mod.time, "time", return_value=now):
        result = c._compute_dns_rates_sync(_dns_current(4600, 2300))

    assert result["last_24h"]["hits"] == 3600
    assert result["last_24h"]["misses"] == 1800
    assert result["last_24h"]["elapsed_s"] == 10 * 3600
    assert result["last_24h"]["label"] == "collected for 10h"
    assert "lifetime" not in result


def test_dns_stats_8h_window_uses_cutoff_not_segment_start(tmp_path):
    c = _make_coordinator()
    c._dns_history_path = tmp_path / ".netscan_dns_history.jsonl"
    now = 200000
    _write_dns_history(
        c._dns_history_path,
        [
            {"ts": now - 12 * 3600, "hits": 1000, "misses": 500},
            {"ts": now - 8 * 3600, "hits": 2000, "misses": 1000},
        ],
    )

    with patch.object(coord_mod.time, "time", return_value=now):
        result = c._compute_dns_rates_sync(_dns_current(30800, 15400))

    assert result["last_8h"]["elapsed_s"] == 8 * 3600
    assert result["last_8h"]["label"] == "last 8h"
    assert result["last_8h"]["label"] != "collected for 12h"


def test_dns_stats_reset_reports_clean_partial_window(tmp_path):
    c = _make_coordinator()
    c._dns_history_path = tmp_path / ".netscan_dns_history.jsonl"
    now = 200000
    _write_dns_history(
        c._dns_history_path,
        [
            {"ts": now - 12 * 3600, "hits": 90000, "misses": 30000},
            {"ts": now - 10 * 3600, "hits": 10, "misses": 5},
        ],
    )

    with patch.object(coord_mod.time, "time", return_value=now):
        result = c._compute_dns_rates_sync(_dns_current(3610, 1805))

    assert result["last_24h"] == {
        "hits": 3600,
        "misses": 1800,
        "hit_pct": 66.7,
        "hits_per_sec": 0.1,
        "misses_per_sec": 0.05,
        "elapsed_s": 36000,
        "label": "collected for 10h",
        "latency_ms": 1.0,
        "servers": [],
    }


def test_dns_stats_first_post_reset_sample_is_just_started(tmp_path):
    c = _make_coordinator()
    c._dns_history_path = tmp_path / ".netscan_dns_history.jsonl"
    now = 200000
    _write_dns_history(
        c._dns_history_path,
        [{"ts": now - 3600, "hits": 90000, "misses": 30000}],
    )

    with patch.object(coord_mod.time, "time", return_value=now):
        result = c._compute_dns_rates_sync(_dns_current(10, 5))

    assert result["last_24h"]["label"] == "just started"
    assert result["last_24h"]["elapsed_s"] == 0
    assert result["last_scan"]["label"] == "just started"


def test_dns_stats_stale_previous_sample_suppresses_last_scan(tmp_path):
    c = _make_coordinator()
    c._dns_history_path = tmp_path / ".netscan_dns_history.jsonl"
    now = 200000
    _write_dns_history(
        c._dns_history_path,
        [
            {
                "ts": now - coord_mod.DNS_LAST_SCAN_MAX_GAP_S - 1,
                "hits": 100,
                "misses": 50,
            }
        ],
    )

    with patch.object(coord_mod.time, "time", return_value=now):
        result = c._compute_dns_rates_sync(_dns_current(200, 100))

    assert result["last_scan"]["label"] == "just started"
    assert result["last_scan"]["elapsed_s"] == 0


def test_dns_stats_server_deltas_sorted_and_period_scoped(tmp_path):
    c = _make_coordinator()
    c._dns_history_path = tmp_path / ".netscan_dns_history.jsonl"
    now = 200000
    _write_dns_history(
        c._dns_history_path,
        [
            {
                "ts": now - 3600,
                "hits": 1000,
                "misses": 500,
                "servers": [
                    {"addr": "1.1.1.1#53", "queries": 100},
                    {"addr": "8.8.8.8#53", "queries": 200},
                ],
            }
        ],
    )

    with patch.object(coord_mod.time, "time", return_value=now):
        result = c._compute_dns_rates_sync(
            _dns_current(
                4600,
                2300,
                [
                    {"addr": "1.1.1.1#53", "queries": 150, "latency_ms": 20},
                    {"addr": "8.8.8.8#53", "queries": 500, "latency_ms": 25},
                ],
            )
        )

    assert result["last_1h"]["servers"] == [
        {"addr": "8.8.8.8#53", "queries": 300, "latency_ms": 25},
        {"addr": "1.1.1.1#53", "queries": 50, "latency_ms": 20},
    ]


def test_dns_stats_per_server_reset_hides_only_that_row(tmp_path):
    c = _make_coordinator()
    c._dns_history_path = tmp_path / ".netscan_dns_history.jsonl"
    now = 200000
    _write_dns_history(
        c._dns_history_path,
        [
            {
                "ts": now - 3600,
                "hits": 1000,
                "misses": 500,
                "servers": [
                    {"addr": "1.1.1.1#53", "queries": 1000},
                    {"addr": "8.8.8.8#53", "queries": 200},
                ],
            }
        ],
    )

    with patch.object(coord_mod.time, "time", return_value=now):
        result = c._compute_dns_rates_sync(
            _dns_current(
                4600,
                2300,
                [
                    {"addr": "1.1.1.1#53", "queries": 10, "latency_ms": 20},
                    {"addr": "8.8.8.8#53", "queries": 500, "latency_ms": 25},
                ],
            )
        )

    assert result["last_1h"]["servers"] == [
        {"addr": "8.8.8.8#53", "queries": 300, "latency_ms": 25}
    ]


def test_dns_stats_old_history_without_servers_does_not_crash(tmp_path):
    c = _make_coordinator()
    c._dns_history_path = tmp_path / ".netscan_dns_history.jsonl"
    now = 200000
    _write_dns_history(
        c._dns_history_path,
        [{"ts": now - 3600, "hits": 1000, "misses": 500}],
    )

    with patch.object(coord_mod.time, "time", return_value=now):
        result = c._compute_dns_rates_sync(
            _dns_current(4600, 2300, [{"addr": "1.1.1.1#53", "queries": 150}])
        )

    assert result["last_1h"]["hits"] == 3600
    assert result["last_1h"]["servers"] == []


def test_dns_stats_period_servers_use_first_available_server_baseline(tmp_path):
    c = _make_coordinator()
    c._dns_history_path = tmp_path / ".netscan_dns_history.jsonl"
    now = 200000
    _write_dns_history(
        c._dns_history_path,
        [
            {"ts": now - 6 * 3600, "hits": 1000, "misses": 500},
            {
                "ts": now - 3600,
                "hits": 19000,
                "misses": 9500,
                "servers": [
                    {"addr": "1.1.1.1#53", "queries": 100},
                    {"addr": "8.8.8.8#53", "queries": 200},
                ],
            },
        ],
    )

    with patch.object(coord_mod.time, "time", return_value=now):
        result = c._compute_dns_rates_sync(
            _dns_current(
                22600,
                11300,
                [
                    {"addr": "1.1.1.1#53", "queries": 175, "latency_ms": 20},
                    {"addr": "8.8.8.8#53", "queries": 600, "latency_ms": 25},
                ],
            )
        )

    assert result["last_24h"]["label"] == "collected for 6h"
    assert result["last_24h"]["hits"] == 21600
    assert result["last_24h"]["servers"] == [
        {"addr": "8.8.8.8#53", "queries": 400, "latency_ms": 25},
        {"addr": "1.1.1.1#53", "queries": 75, "latency_ms": 20},
    ]


def test_dns_history_prunes_entries_older_than_25h(tmp_path):
    c = _make_coordinator()
    c._dns_history_path = tmp_path / ".netscan_dns_history.jsonl"
    now = 200000
    _write_dns_history(
        c._dns_history_path,
        [
            {"ts": now - 26 * 3600, "hits": 1, "misses": 1},
            {"ts": now - 24 * 3600, "hits": 100, "misses": 50},
        ],
    )

    with patch.object(coord_mod.time, "time", return_value=now):
        result = c._compute_dns_rates_sync(_dns_current(200, 100))

    retained = [
        json.loads(line) for line in c._dns_history_path.read_text().splitlines()
    ]
    assert [sample["ts"] for sample in retained] == [now - 24 * 3600, now]
    assert (
        24 * 3600 <= result["last_24h"]["elapsed_s"] <= coord_mod.DNS_HISTORY_MAX_AGE_S
    )


# ── AP unreachable ────────────────────────────────────────────────────────────


def test_ap_unreachable_result_not_partial():
    """AP SSH failure is handled by gather(return_exceptions=True) — no partial flag."""
    c = _make_coordinator(ap_hosts="192.0.2.22")
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(c, "_collect_gateway", new=AsyncMock(return_value=_MINIMAL_GW))
        )
        stack.enter_context(
            patch.object(
                c,
                "_collect_wifi",
                new=AsyncMock(
                    side_effect=[
                        ([], [], {}, "", {}),  # gateway WiFi OK
                        Exception("SSH timeout"),  # AP WiFi fails
                    ]
                ),
            )
        )
        stack.enter_context(
            patch.object(
                c, "_get_ap_info", new=AsyncMock(return_value=("AP1", "", [], []))
            )
        )
        stack.enter_context(
            patch.object(c, "_ping_stale", new=AsyncMock(return_value=[]))
        )
        stack.enter_context(
            patch.object(c, "_resolve_hostnames", new=AsyncMock(return_value={}))
        )
        stack.enter_context(patch.object(c, "_detect_wan_events", return_value=[]))
        result = asyncio.run(c._async_update_data())
    # _append_events/_save_state are called via hass.async_add_executor_job which
    # is a no-op in FakeHass, so no extra patching needed.
    assert "devices" in result
    assert result["gateway_mac"] == _MINIMAL_GW["gw_mac"]
    assert not result.get("partial")
    assert isinstance(result["devices"], list)


def test_ap_unreachable_gateway_device_still_present():
    """Gateway device (gw_mac) appears even when all APs are unreachable."""
    c = _make_coordinator(ap_hosts="192.0.2.22")
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(c, "_collect_gateway", new=AsyncMock(return_value=_MINIMAL_GW))
        )
        stack.enter_context(
            patch.object(
                c,
                "_collect_wifi",
                new=AsyncMock(
                    side_effect=[
                        ([], [], {}, "", {}),
                        Exception("SSH timeout"),
                    ]
                ),
            )
        )
        stack.enter_context(
            patch.object(
                c, "_get_ap_info", new=AsyncMock(return_value=("AP1", "", [], []))
            )
        )
        stack.enter_context(
            patch.object(c, "_ping_stale", new=AsyncMock(return_value=[]))
        )
        stack.enter_context(
            patch.object(c, "_resolve_hostnames", new=AsyncMock(return_value={}))
        )
        stack.enter_context(patch.object(c, "_detect_wan_events", return_value=[]))
        result = asyncio.run(c._async_update_data())

    macs = {d["mac"] for d in result["devices"]}
    assert _MINIMAL_GW["gw_mac"] in macs


def test_ap_unreachable_marked_unavailable_in_host_stats():
    """An AP whose SSH probe fails still gets a host_stats entry, flagged down —
    the topology map and table card key off this instead of dropping the host."""
    c = _make_coordinator(ap_hosts="192.0.2.22")
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(c, "_collect_gateway", new=AsyncMock(return_value=_MINIMAL_GW))
        )
        stack.enter_context(
            patch.object(
                c,
                "_collect_wifi",
                new=AsyncMock(
                    side_effect=[
                        ([], [], {}, "", {}),  # gateway WiFi OK
                        Exception("SSH timeout"),  # AP WiFi fails
                    ]
                ),
            )
        )
        stack.enter_context(
            patch.object(
                c, "_get_ap_info", new=AsyncMock(return_value=("AP1", "", [], []))
            )
        )
        stack.enter_context(
            patch.object(c, "_ping_stale", new=AsyncMock(return_value=[]))
        )
        stack.enter_context(
            patch.object(c, "_resolve_hostnames", new=AsyncMock(return_value={}))
        )
        stack.enter_context(patch.object(c, "_detect_wan_events", return_value=[]))
        result = asyncio.run(c._async_update_data())

    assert result["host_stats"]["192.0.2.22"]["available"] is False
    assert result["host_stats"]["192.0.2.22"]["cpu"] is None


# ── APs-only mode (no gateway configured) ────────────────────────────────────


def test_aps_only_init_gateway_host_none():
    c = _make_coordinator(ap_hosts="192.0.2.22", gateway_host="")
    assert c._gateway_host is None


def test_aps_only_collect_gateway_returns_empty():
    c = _make_coordinator(ap_hosts="192.0.2.22", gateway_host="")
    result = asyncio.run(c._collect_gateway())
    assert result == {}


def test_aps_only_update_builds_from_ap_neigh():
    c = _make_coordinator(ap_hosts="192.0.2.22", gateway_host="")
    ap_arp = [
        "192.0.2.50 lladdr aa:bb:cc:dd:ee:01 REACHABLE",
        "192.0.2.51 lladdr aa:bb:cc:dd:ee:02 STALE",
    ]
    ap_ndp = ["2001:db8::10 lladdr aa:bb:cc:dd:ee:01 router REACHABLE"]
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(c, "_collect_gateway", new=AsyncMock(return_value={}))
        )
        stack.enter_context(
            patch.object(
                c, "_collect_wifi", new=AsyncMock(return_value=([], [], {}, "", {}))
            )
        )
        stack.enter_context(
            patch.object(
                c,
                "_get_ap_info",
                new=AsyncMock(return_value=("ap1", "2001:db8::22", ap_arp, ap_ndp)),
            )
        )
        result = asyncio.run(c._async_update_data())
    assert result["wan_ip"] == ""
    assert result["dns_stats"] is None
    assert result["gateway_mac"] == ""
    macs = {d["mac"] for d in result["devices"]}
    assert "AA:BB:CC:DD:EE:01" in macs
    assert "AA:BB:CC:DD:EE:02" in macs


def test_aps_only_no_prev_no_raise():
    """APs-only mode must not trigger the 'Gateway unreachable' UpdateFailed path."""
    c = _make_coordinator(ap_hosts="192.0.2.22", gateway_host="")
    c._prev_state = {}
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(
                c, "_collect_wifi", new=AsyncMock(return_value=([], [], {}, "", {}))
            )
        )
        stack.enter_context(
            patch.object(
                c, "_get_ap_info", new=AsyncMock(return_value=("ap1", "", [], []))
            )
        )
        result = asyncio.run(c._async_update_data())
    assert not result.get("partial")


def test_host_metrics_disabled_omits_host_stats_from_update():
    c = _make_coordinator(
        ap_hosts="192.0.2.22",
        options={const_mod.CONF_ENABLE_HOST_METRICS: False},
    )
    gw_data = {
        **_MINIMAL_GW,
        "hoststat": [
            "cpu  10 0 10 80",
            "1000 500",
            "25",
        ],
    }
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(c, "_collect_gateway", new=AsyncMock(return_value=gw_data))
        )
        stack.enter_context(
            patch.object(
                c,
                "_collect_wifi",
                new=AsyncMock(
                    return_value=([], ["cpu  10 0 10 80", "1000 500", "25"], {}, "", {})
                ),
            )
        )
        stack.enter_context(
            patch.object(
                c, "_get_ap_info", new=AsyncMock(return_value=("AP1", "", [], []))
            )
        )
        stack.enter_context(
            patch.object(c, "_ping_stale", new=AsyncMock(return_value=[]))
        )
        stack.enter_context(
            patch.object(c, "_resolve_hostnames", new=AsyncMock(return_value={}))
        )
        stack.enter_context(patch.object(c, "_detect_wan_events", return_value=[]))
        compute = stack.enter_context(patch.object(c, "_compute_host_stats"))
        result = asyncio.run(c._async_update_data())

    assert "host_stats" not in result
    compute.assert_not_called()


def test_collect_wifi_passes_no_host_metrics_flag_when_disabled():
    c = _make_coordinator(options={const_mod.CONF_ENABLE_HOST_METRICS: False})
    with patch.object(c, "_ssh_run", new=AsyncMock(return_value="")) as ssh_run:
        result = asyncio.run(c._collect_wifi("192.0.2.22", "AP1"))

    assert result == ([], [], {}, "", {})
    assert (
        ssh_run.await_args.args[1] == "sh /tmp/wrtsensor_collector.sh --no-host-metrics"
    )


def test_collect_wifi_caches_hostname_from_board_metadata():
    c = _make_coordinator(gateway_host="", switch_hosts="192.0.2.24")
    out = (
        'BOARD|{"model":"Zyxel GS1900","board_name":"zyxel,gs1900",'
        '"hostname":"switch1"}\n'
        "STAT|cpu  10 0 10 80|1000|500|25\n"
        "FDB|AA:BB:CC:DD:EE:01|lan4\n"
    )
    with patch.object(c, "_ssh_run", new=AsyncMock(return_value=out)):
        entries, hoststat, fdb, self_mac, port_bytes = asyncio.run(
            c._collect_wifi("192.0.2.24", "192.0.2.24")
        )

    assert entries == []
    assert hoststat == ["cpu  10 0 10 80", "1000 500", "25"]
    assert fdb == {"AA:BB:CC:DD:EE:01": "lan4"}
    assert self_mac == ""
    assert port_bytes == {}
    assert c._host_names["192.0.2.24"] == "switch1"
    assert c._host_models["192.0.2.24"] == ("Zyxel GS1900", "zyxel,gs1900")


def test_switch_only_update_exposes_switch_name_from_board_metadata():
    c = _make_coordinator(gateway_host="", switch_hosts="192.0.2.24")

    async def collect_wifi(host, ap_name):
        c._host_names[host] = "switch1"
        c._host_models[host] = ("Zyxel GS1900", "zyxel,gs1900")
        return (
            [],
            ["cpu  10 0 10 80", "1000 500", "25"],
            {"AA:BB:CC:DD:EE:01": "lan4"},
            "",
            {},
        )

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(c, "_collect_wifi", new=AsyncMock(side_effect=collect_wifi))
        )
        stack.enter_context(
            patch.object(c, "_resolve_hostnames", new=AsyncMock(return_value={}))
        )
        result = asyncio.run(c._async_update_data())

    assert result["switch_hosts"] == ["192.0.2.24"]
    assert result["host_names"] == {"192.0.2.24": "switch1"}
    assert result["switch_names"] == {"192.0.2.24": "switch1"}
    assert result["host_stats"]["192.0.2.24"]["hostname"] == "switch1"


def test_host_topology_resolves_ap_behind_switch():
    """AP whose self-MAC is learned on the switch's FDB resolves its parent
    to the switch — the topology map keys off this to nest the AP under the
    switch instead of drawing it flat under the gateway."""
    c = _make_coordinator(ap_hosts="192.0.2.22", switch_hosts="192.0.2.21")
    ap_self_mac = "AA:BB:CC:DD:EE:01"

    async def collect_wifi(host, ap_name):
        if host == "192.0.2.21":  # switch: sees the AP's own MAC on lan4,
            # alongside a client MAC relayed through the same uplink port.
            return (
                [],
                [],
                {ap_self_mac: "lan4", "11:11:11:11:11:11": "lan4"},
                "",
                {},
            )
        if host == "192.0.2.22":  # AP: reports its own MAC, no FDB of its own
            return ([], [], {}, ap_self_mac, {})
        return ([], [], {}, "", {})  # gateway

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(c, "_collect_gateway", new=AsyncMock(return_value=_MINIMAL_GW))
        )
        stack.enter_context(
            patch.object(c, "_collect_wifi", new=AsyncMock(side_effect=collect_wifi))
        )
        stack.enter_context(
            patch.object(
                c, "_get_ap_info", new=AsyncMock(return_value=("AP1", "", [], []))
            )
        )
        stack.enter_context(
            patch.object(c, "_ping_stale", new=AsyncMock(return_value=[]))
        )
        stack.enter_context(
            patch.object(c, "_resolve_hostnames", new=AsyncMock(return_value={}))
        )
        stack.enter_context(patch.object(c, "_detect_wan_events", return_value=[]))
        result = asyncio.run(c._async_update_data())

    assert result["host_topology"]["192.0.2.22"] == {
        "parent_host": "192.0.2.21",
        "parent_port": "4",
    }
    assert result["host_topology"]["192.0.2.21"] == {
        "parent_host": None,
        "parent_port": None,
    }


def test_host_topology_omits_parent_for_older_collector_script():
    """An AP still running an older collector script (no SELFMAC| line) still
    gets a host_topology entry — None/None, not omitted — so the topology
    map falls back to attaching it under the gateway, same as today."""
    c = _make_coordinator(ap_hosts="192.0.2.22", switch_hosts="192.0.2.21")

    async def collect_wifi(host, ap_name):
        if host == "192.0.2.21":
            return ([], [], {"11:11:11:11:11:11": "lan4"}, "", {})
        return ([], [], {}, "", {})  # AP self_mac empty — pre-update collector

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(c, "_collect_gateway", new=AsyncMock(return_value=_MINIMAL_GW))
        )
        stack.enter_context(
            patch.object(c, "_collect_wifi", new=AsyncMock(side_effect=collect_wifi))
        )
        stack.enter_context(
            patch.object(
                c, "_get_ap_info", new=AsyncMock(return_value=("AP1", "", [], []))
            )
        )
        stack.enter_context(
            patch.object(c, "_ping_stale", new=AsyncMock(return_value=[]))
        )
        stack.enter_context(
            patch.object(c, "_resolve_hostnames", new=AsyncMock(return_value={}))
        )
        stack.enter_context(patch.object(c, "_detect_wan_events", return_value=[]))
        result = asyncio.run(c._async_update_data())

    assert result["host_topology"]["192.0.2.22"] == {
        "parent_host": None,
        "parent_port": None,
    }


def test_host_topology_populated_with_host_metrics_disabled():
    """Topology derives from fdb_by_host (collected under network_hosts), not
    host_metrics — a user who disables CPU/RAM polling should still get it."""
    c = _make_coordinator(
        ap_hosts="192.0.2.22",
        switch_hosts="192.0.2.21",
        options={const_mod.CONF_ENABLE_HOST_METRICS: False},
    )
    ap_self_mac = "AA:BB:CC:DD:EE:01"

    async def collect_wifi(host, ap_name):
        if host == "192.0.2.21":
            return ([], [], {ap_self_mac: "lan4", "11:11:11:11:11:11": "lan4"}, "", {})
        if host == "192.0.2.22":
            return ([], [], {}, ap_self_mac, {})
        return ([], [], {}, "", {})

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(c, "_collect_gateway", new=AsyncMock(return_value=_MINIMAL_GW))
        )
        stack.enter_context(
            patch.object(c, "_collect_wifi", new=AsyncMock(side_effect=collect_wifi))
        )
        stack.enter_context(
            patch.object(
                c, "_get_ap_info", new=AsyncMock(return_value=("AP1", "", [], []))
            )
        )
        stack.enter_context(
            patch.object(c, "_ping_stale", new=AsyncMock(return_value=[]))
        )
        stack.enter_context(
            patch.object(c, "_resolve_hostnames", new=AsyncMock(return_value={}))
        )
        stack.enter_context(patch.object(c, "_detect_wan_events", return_value=[]))
        result = asyncio.run(c._async_update_data())

    assert "host_stats" not in result
    assert result["host_topology"]["192.0.2.22"] == {
        "parent_host": "192.0.2.21",
        "parent_port": "4",
    }


def test_configured_openwrt_hostnames_override_device_hostnames():
    devices = [
        coord_mod.Device(
            mac="AA:00:00:00:00:01", ip="192.0.2.1", hostname="router-dhcp"
        ),
        coord_mod.Device(mac="AA:00:00:00:00:02", ip="192.0.2.22", hostname="ap-dns"),
        coord_mod.Device(mac="AA:00:00:00:00:03", ip="192.0.2.24", hostname="switch"),
        coord_mod.Device(mac="AA:00:00:00:00:04", ip="192.0.2.50", hostname="client"),
    ]

    apply_configured_host_names(
        devices,
        {
            "192.0.2.1": "Gateway",
            "192.0.2.22": "LivingRoomAP",
            "192.0.2.24": "CoreSwitch",
        },
    )

    assert [d.hostname for d in devices] == [
        "Gateway",
        "LivingRoomAP",
        "CoreSwitch",
        "client",
    ]


def test_dns_only_update_skips_ap_info_and_wifi_collection():
    c = _make_coordinator(
        ap_hosts="192.0.2.22",
        options={
            const_mod.CONF_ENABLE_NETWORK_HOSTS: False,
            const_mod.CONF_ENABLE_WAN_BANDWIDTH: False,
            const_mod.CONF_ENABLE_DNS_STATS: True,
            const_mod.CONF_ENABLE_HOST_METRICS: False,
            const_mod.CONF_ENABLE_WIREGUARD: False,
        },
    )
    collect_wifi = AsyncMock(return_value=([], [], {}, "", {}))
    get_ap_info = AsyncMock(return_value=("AP1", "", [], []))
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(c, "_collect_gateway", new=AsyncMock(return_value=_MINIMAL_GW))
        )
        stack.enter_context(patch.object(c, "_collect_wifi", new=collect_wifi))
        stack.enter_context(patch.object(c, "_get_ap_info", new=get_ap_info))
        result = asyncio.run(c._async_update_data())

    collect_wifi.assert_not_called()
    get_ap_info.assert_not_called()
    assert set(result) == {"scan_duration", "partial", "dns_stats"}
    assert result["dns_stats"] is None


def test_host_metrics_with_network_hosts_disabled_runs_collector_without_devices():
    c = _make_coordinator(
        ap_hosts="192.0.2.22",
        options={
            const_mod.CONF_ENABLE_NETWORK_HOSTS: False,
            const_mod.CONF_ENABLE_WAN_BANDWIDTH: False,
            const_mod.CONF_ENABLE_DNS_STATS: False,
            const_mod.CONF_ENABLE_HOST_METRICS: True,
            const_mod.CONF_ENABLE_WIREGUARD: False,
        },
    )
    gw_data = {
        **_MINIMAL_GW,
        "hoststat": [
            "cpu  10 0 10 80",
            "1000 500",
            "25",
        ],
    }
    wifi_entries = [
        {
            "mac": "AA:BB:CC:DD:EE:01",
            "sta_ul_bytes": 1,
            "sta_dl_bytes": 2,
        }
    ]
    collect_wifi = AsyncMock(
        side_effect=[
            (wifi_entries, ["cpu  10 0 10 80", "1000 500", "25"], {}, "", {}),
            (wifi_entries, ["cpu  10 0 10 80", "1000 500", "25"], {}, "", {}),
        ]
    )
    get_ap_info = AsyncMock(return_value=("AP1", "2001:db8::22", [], []))
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(c, "_collect_gateway", new=AsyncMock(return_value=gw_data))
        )
        stack.enter_context(patch.object(c, "_collect_wifi", new=collect_wifi))
        stack.enter_context(patch.object(c, "_get_ap_info", new=get_ap_info))
        result = asyncio.run(c._async_update_data())

    assert collect_wifi.await_count == 2
    get_ap_info.assert_awaited_once()
    assert "host_stats" in result
    assert "devices" not in result
    assert "device_count" not in result
    assert "wan_ip" not in result


# ── WireGuard ────────────────────────────────────────────────────────────────


def test_wg_command_contains_no_secret_subcommands():
    """The remote SSH command must never request private/preshared keys."""
    cmd = WrtsensorCoordinator._build_wireguard_command()
    forbidden = (
        "dump",
        "private-key",
        "preshared",
        "export network",
        "cat /etc/",
    )
    for token in forbidden:
        assert token not in cmd, f"forbidden token {token!r} in WG command"


def test_wg_command_uses_safe_subcommands():
    cmd = WrtsensorCoordinator._build_wireguard_command()
    for safe in (
        "---WG_PROBE---",
        "wg show interfaces",
        "public-key",
        "listen-port",
        "peers",
        "endpoints",
        "allowed-ips",
        "latest-handshakes",
        "transfer",
        "persistent-keepalive",
    ):
        assert safe in cmd


def test_wg_disabled_omits_key_from_result():
    c = _make_coordinator()
    c._enable_wireguard = False
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(c, "_collect_gateway", new=AsyncMock(return_value=_MINIMAL_GW))
        )
        stack.enter_context(
            patch.object(
                c,
                "_collect_wireguard",
                new=AsyncMock(side_effect=AssertionError("must not be called")),
            )
        )
        result = asyncio.run(c._async_update_data())
    assert "wireguard" not in result


def test_wg_enabled_without_gateway_omits_key_and_skips_collection():
    c = _make_coordinator(gateway_host="", ap_hosts="192.0.2.22")
    c._enable_wireguard = True
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(c, "_collect_gateway", new=AsyncMock(return_value={}))
        )
        stack.enter_context(
            patch.object(
                c,
                "_collect_wireguard",
                new=AsyncMock(side_effect=AssertionError("must not be called")),
            )
        )
        result = asyncio.run(c._async_update_data())

    assert "wireguard" not in result


def test_wg_enabled_no_hosts_have_wg_returns_unavailable():
    c = _make_coordinator()
    c._enable_wireguard = True
    c._wg_stale_threshold_s = 180
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(c, "_collect_gateway", new=AsyncMock(return_value=_MINIMAL_GW))
        )
        # Mock the SSH layer rather than the higher-level method so
        # _collect_wireguard's own logic exercises.
        stack.enter_context(
            patch.object(
                c,
                "_ssh_run",
                new=AsyncMock(return_value="---WG_PROBE---\nok\n"),
            )
        )
        result = asyncio.run(c._async_update_data())
    assert result["wireguard"] == {
        "available": False,
        "stale_threshold_s": 180,
        "interfaces": [],
    }


def test_wg_probe_failure_returns_none_for_partial_wg_scan():
    c = _make_coordinator()
    c._enable_wireguard = True
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(c, "_collect_gateway", new=AsyncMock(return_value=_MINIMAL_GW))
        )
        # Empty stdout means the WG probe itself did not complete; do not treat
        # that as "no WireGuard installed".
        stack.enter_context(patch.object(c, "_ssh_run", new=AsyncMock(return_value="")))
        result = asyncio.run(c._async_update_data())
    assert result["wireguard"] is None


def test_wg_enabled_with_peers_populates_result():
    """End-to-end: parse the fixture into a structured result via _collect_wireguard."""
    c = _make_coordinator()
    c._enable_wireguard = True
    c._wg_stale_threshold_s = 180

    fixture_text = (
        Path(__file__).parent / "fixtures" / "wg_show_sections.txt"
    ).read_text()

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(c, "_collect_gateway", new=AsyncMock(return_value=_MINIMAL_GW))
        )
        stack.enter_context(
            patch.object(c, "_ssh_run", new=AsyncMock(return_value=fixture_text))
        )
        result = asyncio.run(c._async_update_data())

    wg = result["wireguard"]
    assert wg["available"] is True
    assert len(wg["interfaces"]) == 1
    iface = wg["interfaces"][0]
    assert iface["name"] == "Wireguard"
    assert len(iface["peers"]) == 3


def test_wg_partial_scan_emits_wireguard_none_not_zero():
    """Cached/partial-scan path must emit wireguard=None so entities go
    unavailable rather than reporting 0 peers + flipping presence to not_home."""
    c = _make_coordinator()
    c._enable_wireguard = True
    c._prev_state = {
        "11:22:33:44:55:66": StateEntry(mac="11:22:33:44:55:66", online=True),
    }
    with patch.object(c, "_collect_gateway", new=AsyncMock(return_value={})):
        result = asyncio.run(c._async_update_data())
    assert result["partial"] is True
    assert "wireguard" in result
    assert result["wireguard"] is None


def test_wg_compute_rates_first_sample_is_none():
    c = _make_coordinator()
    interfaces = [
        {
            "host": "h",
            "name": "wg0",
            "peers": [
                {
                    "id": "abc",
                    "public_key": "PK",
                    "rx_bytes": 1_000_000,
                    "tx_bytes": 500_000,
                }
            ],
        }
    ]
    c._compute_wg_rates(interfaces)
    [peer] = interfaces[0]["peers"]
    assert peer["rx_Bps"] is None
    assert peer["tx_Bps"] is None


def test_wg_compute_rates_counter_reset_yields_none():
    c = _make_coordinator()
    interfaces = [
        {
            "host": "h",
            "name": "wg0",
            "peers": [
                {
                    "id": "abc",
                    "public_key": "PK",
                    "rx_bytes": 1_000_000,
                    "tx_bytes": 500_000,
                }
            ],
        }
    ]
    c._compute_wg_rates(interfaces)  # establish baseline
    # Backdate the baseline so elapsed > BW_MIN_ELAPSED_S
    c._wg_bw_state["abc"]["ts"] -= 60
    interfaces[0]["peers"][0]["rx_bytes"] = 1  # counter went backward
    interfaces[0]["peers"][0]["tx_bytes"] = 1
    c._compute_wg_rates(interfaces)
    [peer] = interfaces[0]["peers"]
    assert peer["rx_Bps"] is None
    assert peer["tx_Bps"] is None


def test_wg_compute_rates_normal_delta():
    c = _make_coordinator()
    interfaces = [
        {
            "host": "h",
            "name": "wg0",
            "peers": [
                {
                    "id": "abc",
                    "public_key": "PK",
                    "rx_bytes": 1_000_000,
                    "tx_bytes": 500_000,
                }
            ],
        }
    ]
    c._compute_wg_rates(interfaces)
    c._wg_bw_state["abc"]["ts"] -= 60  # 60s ago
    interfaces[0]["peers"][0]["rx_bytes"] = 1_600_000  # +600k over ~60s = ~10 KB/s
    interfaces[0]["peers"][0]["tx_bytes"] = 560_000  # +60k over ~60s = ~1 KB/s
    c._compute_wg_rates(interfaces)
    [peer] = interfaces[0]["peers"]
    # int truncation + sub-second elapsed jitter — tolerate 1-byte rounding
    assert peer["rx_Bps"] is not None and 9_990 <= peer["rx_Bps"] <= 10_010
    assert peer["tx_Bps"] is not None and 990 <= peer["tx_Bps"] <= 1_010


def test_wg_compute_rates_drops_baseline_for_disappeared_peer():
    c = _make_coordinator()
    interfaces = [
        {
            "host": "h",
            "name": "wg0",
            "peers": [
                {
                    "id": "abc",
                    "public_key": "PK",
                    "rx_bytes": 1_000_000,
                    "tx_bytes": 500_000,
                }
            ],
        }
    ]
    c._compute_wg_rates(interfaces)
    assert "abc" in c._wg_bw_state
    c._compute_wg_rates([{"host": "h", "name": "wg0", "peers": []}])
    assert "abc" not in c._wg_bw_state


# ── Attended Sysupgrade background loop ───────────────────────────────────────


def _make_asu_coordinator(
    *, ap_hosts: str = "192.0.2.10,192.0.2.11"
) -> WrtsensorCoordinator:
    c = _make_coordinator(ap_hosts=ap_hosts)
    c._enable_asu = True
    c._asu_interval_s = 21600
    return c


def _ok_info(installed: str = "24.10.1 r28597") -> dict:
    return {
        "tool": "owut",
        "installed_version": installed,
        "installed_version_raw": f"OpenWrt {installed}-aaaa",
        "latest_version": installed,
        "summary": "no changes, upgrade not necessary",
        "error": None,
    }


def test_asu_command_uses_owut_check_and_openwrt_release():
    command = WrtsensorCoordinator._build_asu_command()
    assert "owut check" in command
    assert "owut --quiet check" not in command
    assert "OPENWRT_RELEASE" in command


def test_asu_probe_once_no_op_when_all_fresh():
    c = _make_asu_coordinator()
    now = 1000.0
    for h in c._asu_hosts():
        c._asu_cache[h] = {"info": _ok_info(), "ts": now}
    fake_get = AsyncMock(return_value=_ok_info())
    notify = MagicMock()
    with (
        patch("time.time", return_value=now),
        patch.object(c, "_get_asu_info", fake_get),
        patch.object(c, "async_update_listeners", notify),
    ):
        did = asyncio.run(c._asu_probe_once())
    assert did is False
    assert fake_get.await_count == 0
    assert notify.call_count == 0


def test_asu_probe_once_picks_first_due_host_and_emits():
    c = _make_asu_coordinator()
    # Pre-populate self.data so the probe can graft onto it (real flow:
    # first scan tick runs before _asu_loop wakes after its 5 s delay).
    c.data = {"devices": []}
    now = 1_000_000.0
    # First host stale, others fresh.
    hosts = c._asu_hosts()
    c._asu_cache[hosts[0]] = {"info": _ok_info(), "ts": now - 99_999}
    c._asu_cache[hosts[1]] = {"info": _ok_info(), "ts": now}
    c._asu_cache[hosts[2]] = {"info": _ok_info(), "ts": now}

    new_info = _ok_info("24.10.2 r28739")
    fake_get = AsyncMock(return_value=new_info)
    notify = MagicMock()

    with (
        patch("time.time", return_value=now),
        patch.object(c, "_get_asu_info", fake_get),
        patch.object(c, "async_update_listeners", notify),
    ):
        did = asyncio.run(c._asu_probe_once())

    assert did is True
    fake_get.assert_awaited_once_with(hosts[0])
    assert c._asu_cache[hosts[0]]["info"] == new_info
    # Mutated in place — does NOT reset the main coordinator's poll timer
    # or last_update_success, unlike async_set_updated_data.
    assert c.data["asu"][hosts[0]]["latest_version"] == new_info["latest_version"]
    notify.assert_called_once()


def test_asu_probe_once_does_not_call_async_set_updated_data():
    """Regression guard: the loop must not reset the main coordinator's state."""
    c = _make_asu_coordinator()
    c.data = {"devices": []}
    hosts = c._asu_hosts()
    fake_get = AsyncMock(return_value=_ok_info())
    set_data = MagicMock()
    with (
        patch.object(c, "_get_asu_info", fake_get),
        patch.object(c, "async_set_updated_data", set_data),
    ):
        # Cache is empty → first host is due.
        asyncio.run(c._asu_probe_once())
    fake_get.assert_awaited_once_with(hosts[0])
    set_data.assert_not_called()


def test_asu_probe_once_advances_one_host_per_call():
    c = _make_asu_coordinator()
    c.data = {"devices": []}
    now = 1_000_000.0
    hosts = c._asu_hosts()
    # All due.
    fake_get = AsyncMock(return_value=_ok_info())
    notify = MagicMock()
    times = iter([now, now + 1, now + 2, now + 3, now + 4, now + 5])

    def _t():
        return next(times)

    with (
        patch("time.time", side_effect=_t),
        patch.object(c, "_get_asu_info", fake_get),
        patch.object(c, "async_update_listeners", notify),
    ):
        # Three hosts, three iterations: each picks a different host.
        for _ in range(3):
            asyncio.run(c._asu_probe_once())
    probed = [call.args[0] for call in fake_get.await_args_list]
    assert sorted(probed) == sorted(hosts)
    assert notify.call_count == 3


def test_async_update_data_emits_asu_from_cache_when_no_probe_ran():
    """Hot path must always re-emit data['asu'] from cache when enabled."""
    c = _make_asu_coordinator()
    c._asu_cache["192.0.2.10"] = {"info": _ok_info(), "ts": 1.0}
    c._prev_state = {
        "11:22:33:44:55:66": StateEntry(mac="11:22:33:44:55:66", online=True),
    }
    with patch.object(c, "_collect_gateway", new=AsyncMock(return_value={})):
        result = asyncio.run(c._async_update_data())
    assert "asu" in result
    assert result["asu"] == {"192.0.2.10": _ok_info()}


def test_async_update_data_omits_asu_when_disabled():
    c = _make_coordinator()
    assert c._enable_asu is False
    c._prev_state = {
        "11:22:33:44:55:66": StateEntry(mac="11:22:33:44:55:66", online=True),
    }
    with patch.object(c, "_collect_gateway", new=AsyncMock(return_value={})):
        result = asyncio.run(c._async_update_data())
    assert "asu" not in result


def test_async_shutdown_cancels_task_idempotently():
    c = _make_asu_coordinator()

    async def _run():
        async def _never():
            await asyncio.sleep(60)

        c._asu_task = asyncio.create_task(_never())
        # Yield once so the task is actually scheduled before shutdown cancels it.
        await asyncio.sleep(0)
        await c.async_shutdown()
        # Second call is a no-op: task already None.
        await c.async_shutdown()

    asyncio.run(_run())
    assert c._asu_task is None
    assert c._asu_cache == {}
    assert c._asu_missing_tool_logged == set()


def test_get_asu_info_logs_owut_missing_once_per_host():
    c = _make_asu_coordinator()
    fake_run = AsyncMock(return_value="---ASU_TOOL---\nnone\n")
    with patch.object(c, "_ssh_run", fake_run):
        info1 = asyncio.run(c._get_asu_info("192.0.2.10"))
        info2 = asyncio.run(c._get_asu_info("192.0.2.10"))
    assert info1["tool"] == "none"
    assert info2["tool"] == "none"
    assert "192.0.2.10" in c._asu_missing_tool_logged
    # Probe should run twice; logging dedup is internal to the set.
    assert fake_run.await_count == 2


def test_get_asu_info_swallows_ssh_errors():
    c = _make_asu_coordinator()
    fake_run = AsyncMock(side_effect=OSError("connection refused"))
    with patch.object(c, "_ssh_run", fake_run):
        info = asyncio.run(c._get_asu_info("192.0.2.10"))
    assert info["tool"] == "unknown"
    assert "connection refused" in (info["error"] or "")
    assert info["installed_version"] is None


def test_gateway_alone_on_switch_port_keeps_wan_totals():
    """The gateway is the only MAC learned on the switch's uplink port.

    Every routed frame carries the gateway's rewritten source MAC, so that port
    looks exactly like a lone access port and would otherwise attribute the
    whole LAN's byte counters to the gateway — overwriting its WAN totals with
    LAN-side numbers, upload and download swapped. Observed live: the gateway
    alone on the switch's lan9.
    """
    gw_mac = _MINIMAL_GW["gw_mac"]
    c = _make_coordinator(switch_hosts="192.0.2.21")
    c._device_bw = {
        "ts": time.time() - 60,
        "wifi": {},
        "wired": {},
        "port": {gw_mac: {"ul": 0, "dl": 0}},
        "port_src": {gw_mac: "192.0.2.21:9"},
    }

    async def collect_wifi(host, ap_name):
        if host == "192.0.2.21":  # switch: gateway alone on lan9
            # A plausible one-minute delta (10 MB/s up, 5 MB/s down) — under
            # BW_MAX_RATE_BPS, so the rate sanity cap does not mask this.
            return (
                [],
                [],
                {gw_mac: "lan9"},
                "B8:EC:A3:B4:4F:5B",
                {"lan9": {"rx": 600_000_000, "tx": 300_000_000, "speed": 1000}},
            )
        return ([], [], {}, "FC:EC:DA:43:95:A2", {})  # gateway reports its own MAC

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(c, "_collect_gateway", new=AsyncMock(return_value=_MINIMAL_GW))
        )
        stack.enter_context(
            patch.object(c, "_collect_wifi", new=AsyncMock(side_effect=collect_wifi))
        )
        stack.enter_context(
            patch.object(
                c, "_get_ap_info", new=AsyncMock(return_value=("SW", "", [], []))
            )
        )
        stack.enter_context(
            patch.object(c, "_ping_stale", new=AsyncMock(return_value=[]))
        )
        stack.enter_context(
            patch.object(c, "_resolve_hostnames", new=AsyncMock(return_value={}))
        )
        stack.enter_context(patch.object(c, "_detect_wan_events", return_value=[]))
        result = asyncio.run(c._async_update_data())

    gw_row = next(d for d in result["devices"] if d["mac"] == gw_mac)
    # The WAN totals injected for the gateway (0 here — the fixture reports no
    # WAN byte counters) must survive; the switch-port numbers must not appear,
    # least of all with upload and download transposed.
    assert (gw_row["rx_total"], gw_row["tx_total"]) == (0, 0)


def test_host_topology_falls_back_to_last_known_parent():
    """One failed SSH probe must not visibly collapse the topology map.

    The switch's FDB is what places every AP behind it; without caching, a
    single timeout reparents them all to the gateway for that poll and the map
    reshuffles, then snaps back on the next one.
    """
    c = _make_coordinator(ap_hosts="192.0.2.22", switch_hosts="192.0.2.21")
    ap_self_mac = "AA:BB:CC:DD:EE:01"
    switch_reachable = True

    async def collect_wifi(host, ap_name):
        if host == "192.0.2.21":
            if not switch_reachable:
                raise OSError("ssh timeout")
            return ([], [], {ap_self_mac: "lan4"}, "B8:EC:A3:B4:4F:5B", {})
        if host == "192.0.2.22":
            return ([], [], {}, ap_self_mac, {})
        return ([], [], {}, "FC:EC:DA:43:95:A2", {})

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(c, "_collect_gateway", new=AsyncMock(return_value=_MINIMAL_GW))
        )
        stack.enter_context(
            patch.object(c, "_collect_wifi", new=AsyncMock(side_effect=collect_wifi))
        )
        stack.enter_context(
            patch.object(
                c, "_get_ap_info", new=AsyncMock(return_value=("AP1", "", [], []))
            )
        )
        stack.enter_context(
            patch.object(c, "_ping_stale", new=AsyncMock(return_value=[]))
        )
        stack.enter_context(
            patch.object(c, "_resolve_hostnames", new=AsyncMock(return_value={}))
        )
        stack.enter_context(patch.object(c, "_detect_wan_events", return_value=[]))

        first = asyncio.run(c._async_update_data())
        assert first["host_topology"]["192.0.2.22"] == {
            "parent_host": "192.0.2.21",
            "parent_port": "4",
        }

        switch_reachable = False
        second = asyncio.run(c._async_update_data())

    assert second["host_topology"]["192.0.2.22"] == {
        "parent_host": "192.0.2.21",
        "parent_port": "4",
    }
