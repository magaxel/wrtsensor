"""Tests for WrtsensorCoordinator._async_update_data and config migration."""

import asyncio
import importlib.util
import json
import sys
from contextlib import ExitStack
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

coord_mod = sys.modules["custom_components.wrtsensor.coordinator"]
const_mod = sys.modules["custom_components.wrtsensor.const"]

WrtsensorCoordinator = coord_mod.WrtsensorCoordinator
StateEntry = coord_mod.StateEntry
UpdateFailed = sys.modules["homeassistant.helpers.update_coordinator"].UpdateFailed

_ROOT = Path(__file__).parent.parent
_WRT = _ROOT / "custom_components" / "wrtsensor"

# ── Load __init__.py for migration tests ──────────────────────────────────────

# Extend the config_entries stub so async_update_entry is callable
_ce = sys.modules["homeassistant.config_entries"]
if not hasattr(_ce, "async_update_entry"):
    _ce.async_update_entry = lambda *a, **kw: None

_init_name = "custom_components.wrtsensor.__init_test__"
_spec = importlib.util.spec_from_file_location(_init_name, _WRT / "__init__.py")
_init_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_init_mod.__package__ = "custom_components.wrtsensor"
sys.modules[_init_name] = _init_mod
_spec.loader.exec_module(_init_mod)  # type: ignore[union-attr]

async_migrate_entry = _init_mod.async_migrate_entry


# ── Test infrastructure ────────────────────────────────────────────────────────


class _FakeConfigEntries:
    def __init__(self):
        self.updates: list[dict] = []

    def async_update_entry(self, entry, *, data=None, version=None):
        self.updates.append({"data": data, "version": version})
        if data is not None:
            entry.data = data
        if version is not None:
            entry.version = version


class _FakeHass:
    def __init__(self):
        self.config_entries = _FakeConfigEntries()

    async def async_add_executor_job(self, fn, *args):
        return None

    def async_create_task(self, coro):
        coro.close()


class _FakeEntry:
    version = 2
    entry_id = "test-entry"

    def __init__(self, data: dict | None = None, options: dict | None = None):
        self.data = data or {
            "gateway_host": "192.0.2.1",
            "ssh_key_path": "/tmp/test_key",
            "ap_hosts": "",
            "disconnect_threshold_s": 120,
        }
        self.options = options or {}


def _make_coordinator(
    *,
    ap_hosts: str = "",
    gateway_host: str = "192.0.2.1",
    options: dict | None = None,
) -> WrtsensorCoordinator:
    hass = _FakeHass()
    entry = _FakeEntry(
        data={
            "gateway_host": gateway_host,
            "ssh_key_path": "/tmp/test_key",
            "ap_hosts": ap_hosts,
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


def test_coordinator_ignores_legacy_ssh_port():
    c = WrtsensorCoordinator(
        _FakeHass(),
        _FakeEntry(
            data={
                "gateway_host": "192.0.2.1",
                "ssh_key_path": "/tmp/test_key",
                "ap_hosts": "192.0.2.22",
                "ssh_port": 2222,
                "disconnect_threshold_s": 120,
            }
        ),
    )

    assert c._endpoint_ports == {"192.0.2.1": 22, "192.0.2.22": 22}


# ── Migration: v1 → v2 ────────────────────────────────────────────────────────


def test_migrate_v1_does_not_add_ssh_port():
    hass = _FakeHass()
    entry = _FakeEntry(data={"gateway_host": "192.0.2.1"})
    entry.version = 1
    asyncio.run(async_migrate_entry(hass, entry))
    assert const_mod.CONF_SSH_PORT not in entry.data


def test_migrate_v1_adds_disconnect_threshold():
    hass = _FakeHass()
    entry = _FakeEntry(data={"gateway_host": "192.0.2.1"})
    entry.version = 1
    asyncio.run(async_migrate_entry(hass, entry))
    assert const_mod.CONF_DISCONNECT_THRESHOLD in entry.data
    assert (
        entry.data[const_mod.CONF_DISCONNECT_THRESHOLD]
        == const_mod.DEFAULT_DISCONNECT_THRESHOLD
    )


def test_migrate_v1_leaves_existing_ssh_port_untouched():
    hass = _FakeHass()
    entry = _FakeEntry(
        data={"gateway_host": "192.0.2.1", const_mod.CONF_SSH_PORT: 2222}
    )
    entry.version = 1
    asyncio.run(async_migrate_entry(hass, entry))
    assert entry.data[const_mod.CONF_SSH_PORT] == 2222


def test_migrate_v2_is_noop():
    hass = _FakeHass()
    entry = _FakeEntry(data={"gateway_host": "192.0.2.1"})
    entry.version = 2
    asyncio.run(async_migrate_entry(hass, entry))
    assert len(hass.config_entries.updates) == 0


def test_migrate_returns_true():
    hass = _FakeHass()
    entry = _FakeEntry(data={"gateway_host": "192.0.2.1"})
    entry.version = 1
    result = asyncio.run(async_migrate_entry(hass, entry))
    assert result is True


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
                        ([], []),  # gateway WiFi OK
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
                        ([], []),
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
            patch.object(c, "_collect_wifi", new=AsyncMock(return_value=([], [])))
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
            patch.object(c, "_collect_wifi", new=AsyncMock(return_value=([], [])))
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
                new=AsyncMock(return_value=([], ["cpu  10 0 10 80", "1000 500", "25"])),
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

    assert result == ([], [])
    assert (
        ssh_run.await_args.args[1] == "sh /tmp/wrtsensor_collector.sh --no-host-metrics"
    )


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


def _ok_info(installed: str = "24.10.1") -> dict:
    return {
        "tool": "owut",
        "installed_version": installed,
        "installed_version_raw": f"OpenWrt {installed} r28597-aaaa",
        "latest_version": installed,
        "summary": "no changes, upgrade not necessary",
        "error": None,
    }


def test_asu_probe_once_no_op_when_all_fresh():
    c = _make_asu_coordinator()
    now = 1000.0
    for h in c._asu_hosts():
        c._asu_cache[h] = {"info": _ok_info(), "ts": now}
    fake_get = AsyncMock(return_value=_ok_info())
    set_data = AsyncMock()
    with (
        patch("time.time", return_value=now),
        patch.object(c, "_get_asu_info", fake_get),
        patch.object(c, "async_set_updated_data", set_data),
    ):
        did = asyncio.run(c._asu_probe_once())
    assert did is False
    assert fake_get.await_count == 0
    assert set_data.await_count == 0


def test_asu_probe_once_picks_first_due_host_and_emits():
    c = _make_asu_coordinator()
    now = 1_000_000.0
    # First host stale, others fresh.
    hosts = c._asu_hosts()
    c._asu_cache[hosts[0]] = {"info": _ok_info(), "ts": now - 99_999}
    c._asu_cache[hosts[1]] = {"info": _ok_info(), "ts": now}
    c._asu_cache[hosts[2]] = {"info": _ok_info(), "ts": now}

    new_info = _ok_info("24.10.2")
    fake_get = AsyncMock(return_value=new_info)
    captured: list[dict] = []
    set_data = lambda data: captured.append(data)  # noqa: E731

    with (
        patch("time.time", return_value=now),
        patch.object(c, "_get_asu_info", fake_get),
        patch.object(c, "async_set_updated_data", side_effect=set_data),
    ):
        did = asyncio.run(c._asu_probe_once())

    assert did is True
    fake_get.assert_awaited_once_with(hosts[0])
    assert c._asu_cache[hosts[0]]["info"] == new_info
    assert captured and "asu" in captured[0]
    assert captured[0]["asu"][hosts[0]]["latest_version"] == "24.10.2"


def test_asu_probe_once_advances_one_host_per_call():
    c = _make_asu_coordinator()
    now = 1_000_000.0
    hosts = c._asu_hosts()
    # All due.
    fake_get = AsyncMock(return_value=_ok_info())
    # async_set_updated_data is a sync method on the real coordinator; use a
    # plain MagicMock so the mock call returns a regular value, not a coroutine.
    from unittest.mock import MagicMock

    set_data = MagicMock()
    times = iter([now, now + 1, now + 2, now + 3, now + 4, now + 5])

    def _t():
        return next(times)

    with (
        patch("time.time", side_effect=_t),
        patch.object(c, "_get_asu_info", fake_get),
        patch.object(c, "async_set_updated_data", set_data),
    ):
        # Three hosts, three iterations: each picks a different host.
        for _ in range(3):
            asyncio.run(c._asu_probe_once())
    probed = [call.args[0] for call in fake_get.await_args_list]
    assert sorted(probed) == sorted(hosts)


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
