"""Tests for WrtsensorNetworkScannerSensor.extra_state_attributes allowlist."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_WRT = _ROOT / "custom_components" / "wrtsensor"


def _stub(name: str) -> types.ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    mod = types.ModuleType(name.split(".")[-1])
    mod.__path__ = []  # type: ignore[attr-defined]
    sys.modules[name] = mod
    return mod


# ── Extra stubs needed by sensor.py ──────────────────────────────────────────

_sensor_mod = _stub("homeassistant.components.sensor")
_sensor_mod.SensorEntity = object  # type: ignore[attr-defined]
_sensor_mod.SensorStateClass = types.SimpleNamespace(MEASUREMENT="measurement")  # type: ignore[attr-defined]

_entity_mod = _stub("homeassistant.helpers.entity")


class _DeviceInfo:
    def __init__(self, **kwargs: object) -> None:
        self.__dict__.update(kwargs)


_entity_mod.DeviceInfo = _DeviceInfo  # type: ignore[attr-defined]

_ep_mod = _stub("homeassistant.helpers.entity_platform")
_ep_mod.AddEntitiesCallback = object  # type: ignore[attr-defined]

_core_mod = _stub("homeassistant.core")
_core_mod.callback = lambda fn: fn  # type: ignore[attr-defined]

# CoordinatorEntity stub — stores coordinator so self.coordinator works
_uc = sys.modules["homeassistant.helpers.update_coordinator"]


class _CoordinatorEntity:
    def __init__(self, coordinator: object, *args: object, **kwargs: object) -> None:
        self.coordinator = coordinator

    def __class_getitem__(cls, item: object) -> type:
        return cls


_uc.CoordinatorEntity = _CoordinatorEntity  # type: ignore[attr-defined]

# ── Load sensor.py ────────────────────────────────────────────────────────────

_sensor_name = "custom_components.wrtsensor.sensor"
if _sensor_name not in sys.modules:
    _spec = importlib.util.spec_from_file_location(_sensor_name, _WRT / "sensor.py")
    _mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
    _mod.__package__ = "custom_components.wrtsensor"
    sys.modules[_sensor_name] = _mod
    _spec.loader.exec_module(_mod)  # type: ignore[union-attr]

WrtsensorNetworkScannerSensor = sys.modules[_sensor_name].WrtsensorNetworkScannerSensor


# ── Helpers ───────────────────────────────────────────────────────────────────


class _FakeCoordinator:
    def __init__(self, data: dict | None) -> None:
        self.data = data
        self._gateway_host = None
        self._listeners: list[object] = []

    def async_add_listener(self, listener):
        self._listeners.append(listener)

        def _remove():
            self._listeners.remove(listener)

        return _remove

    def fire_update(self) -> None:
        for listener in list(self._listeners):
            listener()


class _FakeEntry:
    entry_id = "test-entry"
    title = "My Router"

    def __init__(self) -> None:
        self._unloaders: list[object] = []

    def async_on_unload(self, unloader) -> None:
        self._unloaders.append(unloader)


def _make_sensor(data: dict | None) -> WrtsensorNetworkScannerSensor:
    sensor = WrtsensorNetworkScannerSensor(_FakeCoordinator(data), _FakeEntry())
    return sensor


# ── Tests ─────────────────────────────────────────────────────────────────────

_ALL_COMPAT_KEYS = (
    "devices",
    "device_count",
    "wan_ip",
    "wan_ip6",
    "wan_rx_rate",
    "wan_tx_rate",
    "host_stats",
    "dns_stats",
    "scan_duration",
    "partial",
)


def test_network_scanner_attributes_allowlist():
    """Internal keys added to coordinator.data must not leak into attributes."""
    data = {k: f"val_{k}" for k in _ALL_COMPAT_KEYS}
    data["_internal_key"] = "should_not_appear"
    data["future_key"] = "also_should_not_appear"

    attrs = _make_sensor(data).extra_state_attributes

    assert set(attrs.keys()) == set(_ALL_COMPAT_KEYS)
    assert "_internal_key" not in attrs
    assert "future_key" not in attrs
    for k in _ALL_COMPAT_KEYS:
        assert attrs[k] == f"val_{k}"


def test_network_scanner_attributes_partial_data():
    """Keys absent from coordinator.data must be absent from attributes (not None)."""
    data = {
        "device_count": 3,
        "wan_ip": "1.2.3.4",
        "partial": True,
    }

    attrs = _make_sensor(data).extra_state_attributes

    assert attrs == {"device_count": 3, "wan_ip": "1.2.3.4", "partial": True}
    assert "dns_stats" not in attrs
    assert "host_stats" not in attrs
    assert "devices" not in attrs


def test_network_scanner_attributes_no_data():
    """Empty coordinator.data returns an empty dict, not an error."""
    assert _make_sensor(None).extra_state_attributes == {}
    assert _make_sensor({}).extra_state_attributes == {}


def test_network_scanner_unique_id_is_entry_scoped():
    sensor_a = WrtsensorNetworkScannerSensor(_FakeCoordinator({}), _FakeEntry())
    entry_b = _FakeEntry()
    entry_b.entry_id = "other-entry"
    entry_b.title = "Guest Router"
    sensor_b = WrtsensorNetworkScannerSensor(_FakeCoordinator({}), entry_b)

    assert sensor_a._attr_unique_id == "test-entry_network_scanner"
    assert sensor_b._attr_unique_id == "other-entry_network_scanner"
    assert sensor_a._attr_unique_id != sensor_b._attr_unique_id


async def _setup_platform_with_hosts(
    host_stats: dict,
) -> tuple[list[object], _FakeCoordinator]:
    added: list[object] = []
    coordinator = _FakeCoordinator({"host_stats": host_stats})
    entry = _FakeEntry()
    hass = types.SimpleNamespace(data={"wrtsensor": {entry.entry_id: coordinator}})

    def _add_entities(new_entities):
        added.extend(list(new_entities))

    await sys.modules[_sensor_name].async_setup_entry(hass, entry, _add_entities)
    return added, coordinator


def test_async_setup_entry_adds_initial_host_sensors():
    added, _ = asyncio.run(_setup_platform_with_hosts({"192.0.2.1": {"cpu": 1.0}}))

    host_entities = [e for e in added if getattr(e, "_hostname", None) == "192.0.2.1"]
    assert len(host_entities) == 3
    assert {type(e).__name__ for e in host_entities} == {
        "WrtsensorHostCPUSensor",
        "WrtsensorHostRAMSensor",
        "WrtsensorHostDiskSensor",
    }


def test_async_setup_entry_adds_new_host_sensors_on_update():
    added, coordinator = asyncio.run(_setup_platform_with_hosts({}))

    coordinator.data = {"host_stats": {"192.0.2.22": {"cpu": 2.0}}}
    coordinator.fire_update()

    host_entities = [e for e in added if getattr(e, "_hostname", None) == "192.0.2.22"]
    assert len(host_entities) == 3


def test_async_setup_entry_does_not_duplicate_host_sensors():
    added, coordinator = asyncio.run(_setup_platform_with_hosts({}))

    coordinator.data = {"host_stats": {"192.0.2.22": {"cpu": 2.0}}}
    coordinator.fire_update()
    coordinator.fire_update()

    host_entities = [e for e in added if getattr(e, "_hostname", None) == "192.0.2.22"]
    assert len(host_entities) == 3
