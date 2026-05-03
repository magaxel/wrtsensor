"""Tests for the entity-registry pruner in __init__.py."""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
import types
from pathlib import Path
from unittest.mock import patch

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
    def __init__(
        self,
        entity_id: str,
        unique_id: str,
        config_entry_id: str,
        device_id: str | None = None,
    ):
        self.entity_id = entity_id
        self.unique_id = unique_id
        self.config_entry_id = config_entry_id
        self.device_id = device_id


class _Registry:
    def __init__(self) -> None:
        self._entries: dict[str, _RegEntry] = {}

    def add(
        self,
        entity_id: str,
        unique_id: str,
        config_entry_id: str,
        device_id: str | None = None,
    ) -> None:
        self._entries[entity_id] = _RegEntry(
            entity_id, unique_id, config_entry_id, device_id
        )

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


# device_registry stub with cascade-into-entity-registry on remove
_dr = _stub("homeassistant.helpers.device_registry")


class _DeviceEntry:
    def __init__(
        self, device_id: str, identifiers: set[tuple[str, str]], config_entry_id: str
    ):
        self.id = device_id
        self.identifiers = identifiers
        self.config_entry_id = config_entry_id


class _DeviceRegistry:
    def __init__(self) -> None:
        self._devices: dict[str, _DeviceEntry] = {}

    def add(
        self, device_id: str, identifiers: set[tuple[str, str]], config_entry_id: str
    ) -> _DeviceEntry:
        dev = _DeviceEntry(device_id, identifiers, config_entry_id)
        self._devices[device_id] = dev
        return dev

    def async_get(self, device_id: str):
        return self._devices.get(device_id)

    def async_remove_device(self, device_id: str) -> None:
        self._devices.pop(device_id, None)
        # Cascade: HA removes every entity_registry entry whose device_id
        # points at the removed device.
        for entity_id, reg_entry in list(_singleton_registry._entries.items()):
            if reg_entry.device_id == device_id:
                _singleton_registry._entries.pop(entity_id, None)


_singleton_dev_registry = _DeviceRegistry()


def _dr_async_get(_hass):  # noqa: D401
    return _singleton_dev_registry


def _dr_async_entries_for_config_entry(reg, entry_id):
    return [d for d in reg._devices.values() if d.config_entry_id == entry_id]


_dr.async_get = _dr_async_get  # type: ignore[attr-defined]
_dr.async_entries_for_config_entry = _dr_async_entries_for_config_entry  # type: ignore[attr-defined]


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

_prune_devices = _init_mod._prune_orphaned_host_devices
_prune_wg = _init_mod._prune_wireguard_entities
_prune_wan = _init_mod._prune_wan_bandwidth_entities
_prune_dns = _init_mod._prune_dns_entities
_remove_legacy = _init_mod._remove_legacy_event_log_entity
_ws_recent_events = _init_mod._ws_recent_events
_async_remove_entry = _init_mod.async_remove_entry
_DOMAIN = _init_mod.DOMAIN


def _reset_registry():
    _singleton_registry._entries.clear()
    _singleton_dev_registry._devices.clear()


def _make_entry(entry_id="test-entry"):
    return types.SimpleNamespace(entry_id=entry_id)


class _FakeConfigEntries:
    def __init__(self, entries=None) -> None:
        self._entries = entries or []

    def async_entries(self, domain):
        assert domain == "wrtsensor"
        return self._entries


class _FakeRemoveHass:
    def __init__(self, entries=None) -> None:
        self.config_entries = _FakeConfigEntries(entries)
        self.executor_calls = 0

    async def async_add_executor_job(self, fn, *args):
        self.executor_calls += 1
        return fn(*args)


def _set_cleanup_state_dirs(monkeypatch, tmp_path):
    ha_dir = tmp_path / "dev_shm"
    local_dir = tmp_path / "tmp_netscan"
    monkeypatch.setattr(_init_mod, "STATE_DIR_HA", str(ha_dir))
    monkeypatch.setattr(_init_mod, "STATE_DIR_LOCAL", str(local_dir))
    return ha_dir, local_dir


def _write_cleanup_files(*state_dirs: Path) -> None:
    for state_dir in state_dirs:
        state_dir.mkdir(parents=True, exist_ok=True)
        for basename in _init_mod.STATE_FILE_BASENAMES:
            (state_dir / basename).write_text("state")
        (state_dir / "netscan_events.json").write_text("{}\n")
        (state_dir / "oui.db").write_text("cached oui")
        (state_dir / "oui.txt").write_text("raw oui")


def _make_coordinator(
    *,
    gateway_host: str | None = "10.0.0.1",
    ap_hosts: tuple[str, ...] = ("10.0.0.22", "10.0.0.23"),
    enable_host_metrics: bool = True,
    enable_wireguard: bool = False,
    enable_wan_bandwidth: bool = True,
    enable_dns_stats: bool = True,
    data: dict | None = None,
):
    return types.SimpleNamespace(
        _gateway_host=gateway_host,
        _ap_hosts=list(ap_hosts),
        _enable_host_metrics=enable_host_metrics,
        _enable_wireguard=enable_wireguard,
        _enable_wan_bandwidth=enable_wan_bandwidth,
        _enable_dns_stats=enable_dns_stats,
        data=data if data is not None else {"host_stats": {}},
    )


def _add_host_device_with_children(entry, host: str) -> str:
    """Register a per-host device + its cpu/ram/disk + firmware entities.

    Returns the synthetic device_id so tests can assert removal.
    """
    device_id = f"dev_{host}"
    _singleton_dev_registry.add(
        device_id,
        identifiers={(_DOMAIN, f"{entry.entry_id}_{host}")},
        config_entry_id=entry.entry_id,
    )
    for metric in ("cpu", "ram", "disk"):
        _singleton_registry.add(
            entity_id=f"sensor.wrtsensor_{host}_{metric}",
            unique_id=f"{entry.entry_id}_host_metric_{host}_{metric}",
            config_entry_id=entry.entry_id,
            device_id=device_id,
        )
    _singleton_registry.add(
        entity_id=f"update.wrtsensor_{host}_firmware",
        unique_id=f"{entry.entry_id}_host_{host}_firmware",
        config_entry_id=entry.entry_id,
        device_id=device_id,
    )
    return device_id


def test_remove_entry_deletes_runtime_state_for_last_entry(monkeypatch, tmp_path):
    ha_dir, local_dir = _set_cleanup_state_dirs(monkeypatch, tmp_path)
    _write_cleanup_files(ha_dir, local_dir)
    hass = _FakeRemoveHass(entries=[])

    asyncio.run(_async_remove_entry(hass, _make_entry()))

    assert hass.executor_calls == 1
    for state_dir in (ha_dir, local_dir):
        for basename in _init_mod.STATE_FILE_BASENAMES:
            assert not (state_dir / basename).exists()
        # HACS keeps events in memory; the JSONL file is owned by the manual path.
        assert (state_dir / "netscan_events.json").exists()
        # OUI artifacts are intentionally retained for faster re-adds.
        assert (state_dir / "oui.db").exists()
        assert (state_dir / "oui.txt").exists()


def test_remove_entry_keeps_global_state_when_other_entry_exists(monkeypatch, tmp_path):
    ha_dir, local_dir = _set_cleanup_state_dirs(monkeypatch, tmp_path)
    _write_cleanup_files(ha_dir, local_dir)
    hass = _FakeRemoveHass(entries=[_make_entry("other-entry")])

    asyncio.run(_async_remove_entry(hass, _make_entry()))

    assert hass.executor_calls == 0
    for state_dir in (ha_dir, local_dir):
        for basename in _init_mod.STATE_FILE_BASENAMES:
            assert (state_dir / basename).exists()


def test_remove_entry_ignores_missing_runtime_state(monkeypatch, tmp_path):
    _set_cleanup_state_dirs(monkeypatch, tmp_path)
    hass = _FakeRemoveHass(entries=[])

    asyncio.run(_async_remove_entry(hass, _make_entry()))

    assert hass.executor_calls == 1


def test_remove_entry_logs_unlink_failure_and_continues(monkeypatch, tmp_path, caplog):
    ha_dir, local_dir = _set_cleanup_state_dirs(monkeypatch, tmp_path)
    _write_cleanup_files(ha_dir, local_dir)
    failed = ha_dir / ".netscan_mac_vendors"
    original_unlink = Path.unlink

    def _unlink(path, *args, **kwargs):
        if path == failed:
            raise OSError("permission denied")
        return original_unlink(path, *args, **kwargs)

    caplog.set_level(logging.WARNING)
    hass = _FakeRemoveHass(entries=[])
    with patch.object(Path, "unlink", _unlink):
        asyncio.run(_async_remove_entry(hass, _make_entry()))

    assert failed.exists()
    assert not (ha_dir / ".netscan_prev_state.json").exists()
    assert not (ha_dir / ".netscan_dns_cache").exists()
    assert not (ha_dir / ".netscan_dns_history.jsonl").exists()
    for basename in _init_mod.STATE_FILE_BASENAMES:
        assert not (local_dir / basename).exists()
    assert (ha_dir / "oui.db").exists()
    assert (ha_dir / "oui.txt").exists()
    assert (local_dir / "oui.db").exists()
    assert (local_dir / "oui.txt").exists()
    assert "Failed to remove wrtsensor state file" in caplog.text
    assert str(failed) in caplog.text


# ── _host_from_device_identifier ─────────────────────────────────────────────


def test_host_identifier_parser_accepts_per_host_shape():
    parse = _init_mod._host_from_device_identifier
    assert parse("entry-1", (_DOMAIN, "entry-1_10.0.0.22")) == "10.0.0.22"


def test_host_identifier_parser_rejects_hub_identifier():
    """Identifier (DOMAIN, entry_id) is the hub device — must not be matched."""
    parse = _init_mod._host_from_device_identifier
    assert parse("entry-1", (_DOMAIN, "entry-1")) is None


def test_host_identifier_parser_rejects_other_domain():
    parse = _init_mod._host_from_device_identifier
    assert parse("entry-1", ("other_domain", "entry-1_10.0.0.22")) is None


# ── _prune_orphaned_host_devices ─────────────────────────────────────────────


def test_prune_devices_removes_gateway_when_cleared():
    """Clearing the gateway must remove its device + child entities (cascade)."""
    _reset_registry()
    entry = _make_entry()
    gw_dev_id = _add_host_device_with_children(entry, "10.0.0.1")
    ap1_dev_id = _add_host_device_with_children(entry, "10.0.0.22")
    ap2_dev_id = _add_host_device_with_children(entry, "10.0.0.23")

    coordinator = _make_coordinator(
        gateway_host=None, ap_hosts=("10.0.0.22", "10.0.0.23")
    )
    _prune_devices(hass=None, entry=entry, coordinator=coordinator)

    # Gateway device gone, APs intact
    assert _singleton_dev_registry.async_get(gw_dev_id) is None
    assert _singleton_dev_registry.async_get(ap1_dev_id) is not None
    assert _singleton_dev_registry.async_get(ap2_dev_id) is not None

    # Cascade: gateway's child entities gone, AP child entities intact
    for metric in ("cpu", "ram", "disk"):
        assert (
            _singleton_registry.async_get(f"sensor.wrtsensor_10.0.0.1_{metric}") is None
        )
        assert (
            _singleton_registry.async_get(f"sensor.wrtsensor_10.0.0.22_{metric}")
            is not None
        )
    assert _singleton_registry.async_get("update.wrtsensor_10.0.0.1_firmware") is None
    assert (
        _singleton_registry.async_get("update.wrtsensor_10.0.0.22_firmware") is not None
    )


def test_prune_devices_removes_ap_when_dropped_from_csv():
    _reset_registry()
    entry = _make_entry()
    gw_dev_id = _add_host_device_with_children(entry, "10.0.0.1")
    kept_dev_id = _add_host_device_with_children(entry, "10.0.0.22")
    dropped_dev_id = _add_host_device_with_children(entry, "10.0.0.23")

    coordinator = _make_coordinator(gateway_host="10.0.0.1", ap_hosts=("10.0.0.22",))
    _prune_devices(hass=None, entry=entry, coordinator=coordinator)

    assert _singleton_dev_registry.async_get(gw_dev_id) is not None
    assert _singleton_dev_registry.async_get(kept_dev_id) is not None
    assert _singleton_dev_registry.async_get(dropped_dev_id) is None
    for metric in ("cpu", "ram", "disk"):
        assert (
            _singleton_registry.async_get(f"sensor.wrtsensor_10.0.0.23_{metric}")
            is None
        )
    assert _singleton_registry.async_get("update.wrtsensor_10.0.0.23_firmware") is None


def test_prune_devices_leaves_hub_device_alone():
    """The hub-level device (identifier (DOMAIN, entry_id)) must never be pruned."""
    _reset_registry()
    entry = _make_entry()
    _singleton_dev_registry.add(
        "hub_dev",
        identifiers={(_DOMAIN, entry.entry_id)},
        config_entry_id=entry.entry_id,
    )
    _add_host_device_with_children(entry, "10.0.0.22")

    coordinator = _make_coordinator(gateway_host=None, ap_hosts=("10.0.0.22",))
    _prune_devices(hass=None, entry=entry, coordinator=coordinator)

    assert _singleton_dev_registry.async_get("hub_dev") is not None


def test_prune_devices_host_metrics_disabled_keeps_devices_and_firmware():
    """When host metrics are disabled, cpu/ram/disk entities go but device stays.

    Drives the host-metrics-disabled branch directly, asserting it does NOT
    delete the device or its firmware update entity (those belong to ASU).
    """
    _reset_registry()
    entry = _make_entry()
    dev_id = _add_host_device_with_children(entry, "10.0.0.22")

    coordinator = _make_coordinator(
        gateway_host="10.0.0.1",
        ap_hosts=("10.0.0.22",),
        enable_host_metrics=False,
    )
    _prune_devices(hass=None, entry=entry, coordinator=coordinator)

    # Device + firmware survive
    assert _singleton_dev_registry.async_get(dev_id) is not None
    assert (
        _singleton_registry.async_get("update.wrtsensor_10.0.0.22_firmware") is not None
    )
    # cpu/ram/disk for the still-configured host are pruned
    for metric in ("cpu", "ram", "disk"):
        assert (
            _singleton_registry.async_get(f"sensor.wrtsensor_10.0.0.22_{metric}")
            is None
        )


def test_prune_devices_removes_legacy_metric_entities_for_configured_hosts():
    """Legacy _host_<host>_<metric> IDs are superseded by _host_metric_* IDs."""
    _reset_registry()
    entry = _make_entry()
    dev_id = _add_host_device_with_children(entry, "10.0.0.22")
    _singleton_registry.add(
        "sensor.legacy_10_0_0_22_cpu",
        f"{entry.entry_id}_host_10.0.0.22_cpu",
        entry.entry_id,
    )
    coordinator = _make_coordinator(
        gateway_host=None,
        ap_hosts=("10.0.0.22",),
        enable_host_metrics=True,
    )

    _prune_devices(hass=None, entry=entry, coordinator=coordinator)

    assert _singleton_dev_registry.async_get(dev_id) is not None
    assert _singleton_registry.async_get("sensor.legacy_10_0_0_22_cpu") is None
    assert _singleton_registry.async_get("sensor.wrtsensor_10.0.0.22_cpu") is not None


# ── Gateway-only feature pruners fire on no-gateway ──────────────────────────


def test_prune_wan_removes_when_gateway_cleared():
    _reset_registry()
    entry = _make_entry()
    _singleton_registry.add(
        "sensor.wrtsensor_wan_download",
        f"{entry.entry_id}_wan_download",
        entry.entry_id,
    )
    _singleton_registry.add(
        "sensor.wrtsensor_wan_upload",
        f"{entry.entry_id}_wan_upload",
        entry.entry_id,
    )
    coordinator = _make_coordinator(gateway_host=None, enable_wan_bandwidth=True)

    _prune_wan(None, entry, coordinator)

    assert _singleton_registry.async_get("sensor.wrtsensor_wan_download") is None
    assert _singleton_registry.async_get("sensor.wrtsensor_wan_upload") is None


def test_prune_wan_keeps_when_gateway_and_toggle_on():
    _reset_registry()
    entry = _make_entry()
    _singleton_registry.add(
        "sensor.wrtsensor_wan_download",
        f"{entry.entry_id}_wan_download",
        entry.entry_id,
    )
    coordinator = _make_coordinator(gateway_host="10.0.0.1", enable_wan_bandwidth=True)

    _prune_wan(None, entry, coordinator)

    assert _singleton_registry.async_get("sensor.wrtsensor_wan_download") is not None


def test_prune_dns_removes_when_gateway_cleared():
    _reset_registry()
    entry = _make_entry()
    _singleton_registry.add(
        "sensor.wrtsensor_dns_hit_pct",
        f"{entry.entry_id}_dns_hit_pct",
        entry.entry_id,
    )
    _singleton_registry.add(
        "sensor.wrtsensor_dns_latency",
        f"{entry.entry_id}_dns_latency",
        entry.entry_id,
    )
    coordinator = _make_coordinator(gateway_host=None, enable_dns_stats=True)

    _prune_dns(None, entry, coordinator)

    assert _singleton_registry.async_get("sensor.wrtsensor_dns_hit_pct") is None
    assert _singleton_registry.async_get("sensor.wrtsensor_dns_latency") is None


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
            self._gateway_host = "10.0.0.1"
            self._ap_hosts = []
            self._enable_network_hosts = True
            self._enable_wan_bandwidth = True
            self._enable_dns_stats = True
            self._enable_wireguard = False
            self._enable_asu = False
            self._enable_host_metrics = True

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
    original_prune = _init_mod._prune_orphaned_host_devices
    original_prune_wg = _init_mod._prune_wireguard_entities
    try:
        _init_mod.WrtsensorCoordinator = _FakeCoordinator
        _init_mod._register_static_path = _fake_register_static_path
        _init_mod._prune_orphaned_host_devices = lambda hass, entry, coordinator: (
            calls.append("prune")
        )
        _init_mod._prune_wireguard_entities = lambda hass, entry, coordinator: (
            calls.append("prune_wg")
        )

        asyncio.run(_init_mod.async_setup_entry(_FakeHass(), _FakeEntry()))
    finally:
        _init_mod.WrtsensorCoordinator = original_coordinator
        _init_mod._register_static_path = original_register
        _init_mod._prune_orphaned_host_devices = original_prune
        _init_mod._prune_wireguard_entities = original_prune_wg

    assert "static" in calls
    assert "prune" in calls
    assert calls.index("prune") < calls.index(
        "forward:sensor,binary_sensor,device_tracker,update"
    )
    assert all(call != "_register_lovelace_resources" for call in calls)


# ── WireGuard registry pruning ────────────────────────────────────────────────


def _make_wg_coordinator(*, enable: bool, data, gateway_host: str | None = "10.0.0.1"):
    c = types.SimpleNamespace()
    c._enable_wireguard = enable
    c._gateway_host = gateway_host
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


def test_wg_prune_removes_all_when_gateway_cleared():
    """No gateway → WG must be removed even with toggle on (sensor cannot work)."""
    _reset_registry()
    entry = _make_entry()
    _singleton_registry.add(
        "device_tracker.wg_alice", "test-entry_wgpeer_aaa", "test-entry"
    )
    _singleton_registry.add("sensor.wireguard", "test-entry_wireguard", "test-entry")

    coordinator = _make_wg_coordinator(enable=True, data=None, gateway_host=None)
    _prune_wg(None, entry, coordinator)

    assert _singleton_registry.async_get("device_tracker.wg_alice") is None
    assert _singleton_registry.async_get("sensor.wireguard") is None


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
