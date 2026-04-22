"""Tests for WrtsensorNetworkScannerSensor.extra_state_attributes allowlist."""

from __future__ import annotations

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


class _FakeEntry:
    entry_id = "test-entry"


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


# ── WrtsensorEventLogSensor ───────────────────────────────────────────────────

WrtsensorEventLogSensor = sys.modules[_sensor_name].WrtsensorEventLogSensor


def _make_event_log_sensor(data: dict | None) -> WrtsensorEventLogSensor:
    return WrtsensorEventLogSensor(_FakeCoordinator(data), _FakeEntry())


def test_event_log_sensor_state():
    """State equals event_count from coordinator data."""
    sensor = _make_event_log_sensor({"event_count": 42, "events": []})
    assert sensor.state == 42


def test_event_log_sensor_attributes():
    """Attributes expose events list and event_count."""
    events = [
        {"ts": "2026-01-01T00:00:00Z", "type": "join", "mac": "aa:bb:cc:dd:ee:ff"}
    ]
    sensor = _make_event_log_sensor({"event_count": 1, "events": events})
    attrs = sensor.extra_state_attributes
    assert attrs["event_count"] == 1
    assert attrs["events"] == events


def test_event_log_sensor_no_data():
    """Returns zero state and empty events when coordinator has no data."""
    sensor = _make_event_log_sensor(None)
    assert sensor.state == 0
    assert sensor.extra_state_attributes == {"events": [], "event_count": 0}
