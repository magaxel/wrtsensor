"""Tests for the entity-registry pruner in __init__.py."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_WRT = _ROOT / "custom_components" / "wrtsensor"


# ── Extra stubs for __init__.py imports ──────────────────────────────────────


def _stub(name: str) -> types.ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    mod = types.ModuleType(name)
    mod.__path__ = []  # type: ignore[attr-defined]
    sys.modules[name] = mod
    return mod


# entity_registry stub with minimal in-memory registry
_er = _stub("homeassistant.helpers.entity_registry")


class _RegEntry:
    def __init__(self, entity_id: str, unique_id: str, config_entry_id: str):
        self.entity_id = entity_id
        self.unique_id = unique_id
        self.config_entry_id = config_entry_id


class _Registry:
    def __init__(self) -> None:
        self._entries: dict[str, _RegEntry] = {}

    def add(self, entity_id: str, unique_id: str, config_entry_id: str) -> None:
        self._entries[entity_id] = _RegEntry(entity_id, unique_id, config_entry_id)

    def async_get(self, entity_id: str):
        return self._entries.get(entity_id)

    def async_remove(self, entity_id: str) -> None:
        self._entries.pop(entity_id, None)


_singleton_registry = _Registry()


def _async_get(_hass):  # noqa: D401
    return _singleton_registry


def _async_entries_for_config_entry(reg, entry_id):
    return [e for e in reg._entries.values() if e.config_entry_id == entry_id]


_er.async_get = _async_get  # type: ignore[attr-defined]
_er.async_entries_for_config_entry = _async_entries_for_config_entry  # type: ignore[attr-defined]


# Load __init__.py once
_init_name = "custom_components.wrtsensor.__init_prune_test__"
if _init_name not in sys.modules:
    _spec = importlib.util.spec_from_file_location(_init_name, _WRT / "__init__.py")
    _init_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
    _init_mod.__package__ = "custom_components.wrtsensor"
    sys.modules[_init_name] = _init_mod
    _spec.loader.exec_module(_init_mod)  # type: ignore[union-attr]
else:
    _init_mod = sys.modules[_init_name]

_prune = _init_mod._prune_orphaned_host_entities
_prune_wg = _init_mod._prune_wireguard_entities
_remove_legacy = _init_mod._remove_legacy_event_log_entity
_ws_recent_events = _init_mod._ws_recent_events


def _reset_registry():
    _singleton_registry._entries.clear()


def _make_entry(entry_id="test-entry"):
    return types.SimpleNamespace(entry_id=entry_id)


def _make_coordinator(host_stats):
    return types.SimpleNamespace(data={"host_stats": host_stats})


def test_prune_removes_stale_host_entity():
    _reset_registry()
    entry = _make_entry()
    # One live host, one stale ghost — all three sensor types for each.
    for host, kind in [
        ("live", "cpu"),
        ("live", "ram"),
        ("live", "disk"),
        ("ghost", "cpu"),
        ("ghost", "ram"),
        ("ghost", "disk"),
    ]:
        _singleton_registry.add(
            entity_id=f"sensor.wrtsensor_{host}_{kind}",
            unique_id=f"{entry.entry_id}_host_{host}_{kind}",
            config_entry_id=entry.entry_id,
        )
    coordinator = _make_coordinator({"live": {"cpu": 1.0}})

    _prune(hass=None, entry=entry, coordinator=coordinator)

    remaining = set(_singleton_registry._entries.keys())
    assert remaining == {
        "sensor.wrtsensor_live_cpu",
        "sensor.wrtsensor_live_ram",
        "sensor.wrtsensor_live_disk",
    }


def test_prune_skips_when_host_stats_empty():
    """Empty scan must not nuke every host entity — could be a cold start."""
    _reset_registry()
    entry = _make_entry()
    _singleton_registry.add(
        entity_id="sensor.wrtsensor_live_cpu",
        unique_id=f"{entry.entry_id}_host_live_cpu",
        config_entry_id=entry.entry_id,
    )
    coordinator = _make_coordinator({})

    _prune(hass=None, entry=entry, coordinator=coordinator)

    assert "sensor.wrtsensor_live_cpu" in _singleton_registry._entries


def test_prune_ignores_non_host_entities():
    """Entities whose unique_id doesn't match the host_ prefix are left alone."""
    _reset_registry()
    entry = _make_entry()
    _singleton_registry.add(
        entity_id="sensor.wrtsensor_wan_download",
        unique_id=f"{entry.entry_id}_wan_download",
        config_entry_id=entry.entry_id,
    )
    _singleton_registry.add(
        entity_id="sensor.wrtsensor_ghost_cpu",
        unique_id=f"{entry.entry_id}_host_ghost_cpu",
        config_entry_id=entry.entry_id,
    )
    coordinator = _make_coordinator({"live": {"cpu": 1.0}})

    _prune(hass=None, entry=entry, coordinator=coordinator)

    assert "sensor.wrtsensor_wan_download" in _singleton_registry._entries
    assert "sensor.wrtsensor_ghost_cpu" not in _singleton_registry._entries


def test_prune_handles_missing_data():
    """None coordinator.data is allowed and treated as empty."""
    _reset_registry()
    entry = _make_entry()
    _singleton_registry.add(
        entity_id="sensor.wrtsensor_live_cpu",
        unique_id=f"{entry.entry_id}_host_live_cpu",
        config_entry_id=entry.entry_id,
    )
    coordinator = types.SimpleNamespace(data=None)

    _prune(hass=None, entry=entry, coordinator=coordinator)

    assert "sensor.wrtsensor_live_cpu" in _singleton_registry._entries


def test_remove_legacy_event_log_entity():
    _reset_registry()
    entry = _make_entry()
    _singleton_registry.add(
        entity_id="sensor.wrtsensor_event_log",
        unique_id=f"{entry.entry_id}_event_log",
        config_entry_id=entry.entry_id,
    )

    _remove_legacy(hass=None, entry=entry)

    assert "sensor.wrtsensor_event_log" not in _singleton_registry._entries


def test_ws_recent_events_returns_buffer():
    _reset_registry()
    entry = _make_entry()
    _singleton_registry.add(
        entity_id="sensor.wrtsensor_network_scanner",
        unique_id=f"{entry.entry_id}_network_scanner",
        config_entry_id=entry.entry_id,
    )

    sent = {}

    class _Conn:
        def send_result(self, msg_id, payload):
            sent["result"] = (msg_id, payload)

        def send_error(self, msg_id, code, message):
            sent["error"] = (msg_id, code, message)

    hass = types.SimpleNamespace(
        data={
            "wrtsensor": {
                entry.entry_id: types.SimpleNamespace(
                    get_recent_events=lambda: [{"type": "connect"}],
                    get_event_count=lambda: 1,
                    get_event_buffer_size=lambda: 500,
                )
            }
        }
    )

    asyncio.run(
        _ws_recent_events(
            hass,
            _Conn(),
            {"id": 7, "entity_id": "sensor.wrtsensor_network_scanner"},
        )
    )

    assert sent["result"] == (
        7,
        {"events": [{"type": "connect"}], "count": 1, "buffer_size": 500},
    )


def test_ws_recent_events_rejects_non_scanner_entity():
    _reset_registry()
    entry = _make_entry()
    _singleton_registry.add(
        entity_id="sensor.wrtsensor_wan_download",
        unique_id=f"{entry.entry_id}_wan_download",
        config_entry_id=entry.entry_id,
    )

    sent = {}

    class _Conn:
        def send_result(self, msg_id, payload):
            sent["result"] = (msg_id, payload)

        def send_error(self, msg_id, code, message):
            sent["error"] = (msg_id, code, message)

    hass = types.SimpleNamespace(data={"wrtsensor": {}})

    asyncio.run(
        _ws_recent_events(
            hass,
            _Conn(),
            {"id": 9, "entity_id": "sensor.wrtsensor_wan_download"},
        )
    )

    assert sent["error"][0] == 9
    assert sent["error"][1] == "invalid_entity"


def test_ws_recent_events_rejects_unknown_entity():
    _reset_registry()
    sent = {}

    class _Conn:
        def send_result(self, msg_id, payload):
            sent["result"] = (msg_id, payload)

        def send_error(self, msg_id, code, message):
            sent["error"] = (msg_id, code, message)

    hass = types.SimpleNamespace(data={"wrtsensor": {}})

    asyncio.run(
        _ws_recent_events(
            hass,
            _Conn(),
            {"id": 11, "entity_id": "sensor.missing_network_scanner"},
        )
    )

    assert sent["error"][0] == 11
    assert sent["error"][1] == "invalid_entity"


def test_setup_entry_does_not_touch_lovelace_storage():
    calls: list[str] = []

    class _FakeCoordinator:
        def __init__(self, hass, entry):
            self.data = {"host_stats": {}}
            self._enable_wireguard = False

        async def async_setup(self):
            return None

        async def async_config_entry_first_refresh(self):
            return None

    class _FakeConfigEntries:
        async def async_forward_entry_setups(self, entry, platforms):
            calls.append(f"forward:{','.join(platforms)}")

    class _FakeHass:
        def __init__(self) -> None:
            self.data = {}
            self.config_entries = _FakeConfigEntries()

        async def async_add_executor_job(self, fn, *args):
            calls.append(getattr(fn, "__name__", "executor"))
            return None

    class _FakeEntry:
        entry_id = "entry-1"

        def add_update_listener(self, listener):
            return listener

        def async_on_unload(self, callback):
            calls.append("unload")

    async def _fake_register_static_path(hass):
        calls.append("static")

    original_coordinator = _init_mod.WrtsensorCoordinator
    original_register = _init_mod._register_static_path
    original_prune = _init_mod._prune_orphaned_host_entities
    original_prune_wg = _init_mod._prune_wireguard_entities
    try:
        _init_mod.WrtsensorCoordinator = _FakeCoordinator
        _init_mod._register_static_path = _fake_register_static_path
        _init_mod._prune_orphaned_host_entities = lambda hass, entry, coordinator: (
            calls.append("prune")
        )
        _init_mod._prune_wireguard_entities = lambda hass, entry, coordinator: (
            calls.append("prune_wg")
        )

        asyncio.run(_init_mod.async_setup_entry(_FakeHass(), _FakeEntry()))
    finally:
        _init_mod.WrtsensorCoordinator = original_coordinator
        _init_mod._register_static_path = original_register
        _init_mod._prune_orphaned_host_entities = original_prune
        _init_mod._prune_wireguard_entities = original_prune_wg

    assert "static" in calls
    assert "prune" in calls
    assert all(call != "_register_lovelace_resources" for call in calls)


# ── WireGuard registry pruning ────────────────────────────────────────────────


def _make_wg_coordinator(*, enable: bool, data):
    c = types.SimpleNamespace()
    c._enable_wireguard = enable
    c.data = data
    return c


def test_wg_prune_removes_orphan_peer_when_enabled():
    _reset_registry()
    entry = _make_entry()
    _singleton_registry.add(
        "device_tracker.wg_alice", "test-entry_wgpeer_aaa", "test-entry"
    )
    _singleton_registry.add(
        "device_tracker.wg_bob", "test-entry_wgpeer_bbb", "test-entry"
    )
    _singleton_registry.add("sensor.wireguard", "test-entry_wireguard", "test-entry")

    coordinator = _make_wg_coordinator(
        enable=True,
        data={
            "wireguard": {
                "available": True,
                "interfaces": [
                    {"peers": [{"id": "aaa"}]},  # only aaa is live
                ],
            }
        },
    )
    _prune_wg(None, entry, coordinator)

    assert _singleton_registry.async_get("device_tracker.wg_alice") is not None
    assert _singleton_registry.async_get("device_tracker.wg_bob") is None
    # WG sensor stays — option still on
    assert _singleton_registry.async_get("sensor.wireguard") is not None


def test_wg_prune_removes_all_when_option_disabled():
    _reset_registry()
    entry = _make_entry()
    _singleton_registry.add(
        "device_tracker.wg_alice", "test-entry_wgpeer_aaa", "test-entry"
    )
    _singleton_registry.add(
        "device_tracker.wg_bob", "test-entry_wgpeer_bbb", "test-entry"
    )
    _singleton_registry.add("sensor.wireguard", "test-entry_wireguard", "test-entry")
    _singleton_registry.add("sensor.unrelated", "test-entry_dns_hit_pct", "test-entry")

    coordinator = _make_wg_coordinator(enable=False, data=None)
    _prune_wg(None, entry, coordinator)

    assert _singleton_registry.async_get("device_tracker.wg_alice") is None
    assert _singleton_registry.async_get("device_tracker.wg_bob") is None
    assert _singleton_registry.async_get("sensor.wireguard") is None
    # Unrelated entries untouched
    assert _singleton_registry.async_get("sensor.unrelated") is not None


def test_wg_prune_skips_on_partial_scan():
    """If the option is on but data['wireguard'] is None (partial scan), keep all trackers."""
    _reset_registry()
    entry = _make_entry()
    _singleton_registry.add(
        "device_tracker.wg_alice", "test-entry_wgpeer_aaa", "test-entry"
    )

    coordinator = _make_wg_coordinator(enable=True, data={"wireguard": None})
    _prune_wg(None, entry, coordinator)

    assert _singleton_registry.async_get("device_tracker.wg_alice") is not None


def test_wg_prune_removes_all_peer_trackers_on_clean_no_wg():
    """Successful scan that returns no interfaces (WG uninstalled, option still on)
    must prune all peer trackers — but keep the WG sensor so it can recover when
    WG comes back."""
    _reset_registry()
    entry = _make_entry()
    _singleton_registry.add(
        "device_tracker.wg_alice", "test-entry_wgpeer_aaa", "test-entry"
    )
    _singleton_registry.add(
        "device_tracker.wg_bob", "test-entry_wgpeer_bbb", "test-entry"
    )
    _singleton_registry.add("sensor.wireguard", "test-entry_wireguard", "test-entry")

    coordinator = _make_wg_coordinator(
        enable=True,
        data={
            "wireguard": {
                "available": False,
                "stale_threshold_s": 180,
                "interfaces": [],
            }
        },
    )
    _prune_wg(None, entry, coordinator)

    assert _singleton_registry.async_get("device_tracker.wg_alice") is None
    assert _singleton_registry.async_get("device_tracker.wg_bob") is None
    assert _singleton_registry.async_get("sensor.wireguard") is not None


def test_wg_prune_skips_when_no_data():
    """Cold start (coordinator.data is None): leave registry alone."""
    _reset_registry()
    entry = _make_entry()
    _singleton_registry.add(
        "device_tracker.wg_alice", "test-entry_wgpeer_aaa", "test-entry"
    )

    coordinator = _make_wg_coordinator(enable=True, data=None)
    _prune_wg(None, entry, coordinator)

    assert _singleton_registry.async_get("device_tracker.wg_alice") is not None
