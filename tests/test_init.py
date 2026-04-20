"""Tests for the entity-registry pruner in __init__.py."""

from __future__ import annotations

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
