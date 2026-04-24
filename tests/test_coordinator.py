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
            "ssh_port": 22,
            "disconnect_threshold_s": 120,
        }
        self.options = options or {}


def _make_coordinator(
    *, ap_hosts: str = "", gateway_host: str = "192.0.2.1"
) -> WrtsensorCoordinator:
    hass = _FakeHass()
    entry = _FakeEntry(
        data={
            "gateway_host": gateway_host,
            "ssh_key_path": "/tmp/test_key",
            "ap_hosts": ap_hosts,
            "ssh_port": 22,
            "disconnect_threshold_s": 120,
        }
    )
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


# ── Migration: v1 → v2 ────────────────────────────────────────────────────────


def test_migrate_v1_adds_ssh_port():
    hass = _FakeHass()
    entry = _FakeEntry(data={"gateway_host": "192.0.2.1"})
    entry.version = 1
    asyncio.run(async_migrate_entry(hass, entry))
    assert const_mod.CONF_SSH_PORT in entry.data
    assert entry.data[const_mod.CONF_SSH_PORT] == const_mod.DEFAULT_SSH_PORT


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


def test_migrate_v1_preserves_existing_ssh_port():
    hass = _FakeHass()
    entry = _FakeEntry(
        data={"gateway_host": "192.0.2.1", const_mod.CONF_SSH_PORT: 2222}
    )
    entry.version = 1
    asyncio.run(async_migrate_entry(hass, entry))
    assert entry.data[const_mod.CONF_SSH_PORT] == 2222  # setdefault, not overwrite


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


def _write_dns_history(path: Path, samples: list[dict[str, int]]) -> None:
    path.write_text("".join(json.dumps(sample) + "\n" for sample in samples))


def _dns_current(hits: int, misses: int) -> dict:
    return {"cache_size": 10000, "hits": hits, "misses": misses, "servers": []}


def test_dns_stats_last_24h_rollup(tmp_path):
    c = _make_coordinator()
    c._dns_history_path = tmp_path / ".netscan_dns_history.jsonl"
    now = 200000
    _write_dns_history(
        c._dns_history_path,
        [{"ts": now - 86400, "hits": 1000, "misses": 500}],
    )

    with patch.object(coord_mod.time, "time", return_value=now):
        result = c._compute_dns_rates(_dns_current(87400, 43700))

    assert result["last_24h"] == {
        "hits": 86400,
        "misses": 43200,
        "hit_pct": 66.7,
        "hits_per_sec": 1.0,
        "misses_per_sec": 0.5,
        "elapsed_s": 86400,
        "label": "last 24h",
    }
    assert result["lifetime"]["hits"] == 87400
    assert result["lifetime"]["misses"] == 43700
    assert result["lifetime"]["hit_pct"] == 66.7


def test_dns_stats_first_sample_has_lifetime_only(tmp_path):
    c = _make_coordinator()
    c._dns_history_path = tmp_path / ".netscan_dns_history.jsonl"

    with patch.object(coord_mod.time, "time", return_value=200000):
        result = c._compute_dns_rates(_dns_current(120, 30))

    assert result["last_24h"] is None
    assert result["lifetime"]["hits"] == 120
    assert result["lifetime"]["misses"] == 30
    assert result["lifetime"]["hit_pct"] == 80.0


def test_dns_stats_empty_history_after_restart_keeps_lifetime(tmp_path):
    c = _make_coordinator()
    c._dns_history_path = tmp_path / ".netscan_dns_history.jsonl"
    c._dns_state = {}

    with patch.object(coord_mod.time, "time", return_value=200000):
        result = c._compute_dns_rates(_dns_current(10000, 2500))

    assert result["last_24h"] is None
    assert result["lifetime"]["hits"] == 10000
    assert result["lifetime"]["misses"] == 2500
    assert result["lifetime"]["hit_pct"] == 80.0


def test_dns_stats_partial_history_without_24h_baseline_has_lifetime_only(tmp_path):
    c = _make_coordinator()
    c._dns_history_path = tmp_path / ".netscan_dns_history.jsonl"
    now = 200000
    _write_dns_history(
        c._dns_history_path,
        [{"ts": now - 10 * 3600, "hits": 1000, "misses": 500}],
    )

    with patch.object(coord_mod.time, "time", return_value=now):
        result = c._compute_dns_rates(_dns_current(4600, 2300))

    assert result["last_24h"] is None
    assert result["lifetime"]["hits"] == 4600
    assert result["lifetime"]["misses"] == 2300


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
        result = c._compute_dns_rates(_dns_current(3610, 1805))

    assert result["last_24h"] == {
        "hits": 3600,
        "misses": 1800,
        "hit_pct": 66.7,
        "hits_per_sec": 0.1,
        "misses_per_sec": 0.05,
        "elapsed_s": 36000,
        "label": "last 10h",
    }


def test_dns_stats_reset_without_clean_interval_has_no_last_24h(tmp_path):
    c = _make_coordinator()
    c._dns_history_path = tmp_path / ".netscan_dns_history.jsonl"
    now = 200000
    _write_dns_history(
        c._dns_history_path,
        [{"ts": now - 3600, "hits": 90000, "misses": 30000}],
    )

    with patch.object(coord_mod.time, "time", return_value=now):
        result = c._compute_dns_rates(_dns_current(10, 5))

    assert result["last_24h"] is None
    assert result["lifetime"]["hits"] == 10
    assert result["lifetime"]["misses"] == 5


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
        c._compute_dns_rates(_dns_current(200, 100))

    retained = [
        json.loads(line) for line in c._dns_history_path.read_text().splitlines()
    ]
    assert [sample["ts"] for sample in retained] == [now - 24 * 3600, now]


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
    assert result["_gw_mac"] == ""
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
