"""Tests for the wrtsensor update entity and ASU registry pruner."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_WRT = _ROOT / "custom_components" / "wrtsensor"


# ── Stub homeassistant.components.update so update.py imports cleanly ─────────
def _stub(name: str) -> types.ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    mod = types.ModuleType(name)
    mod.__path__ = []  # type: ignore[attr-defined]
    sys.modules[name] = mod
    return mod


_update_pkg = _stub("homeassistant.components.update")


class _UpdateEntity:
    """Minimal stub matching the surface our subclass touches."""


class _UpdateDeviceClass:
    FIRMWARE = "firmware"


_update_pkg.UpdateEntity = _UpdateEntity  # type: ignore[attr-defined]
_update_pkg.UpdateDeviceClass = _UpdateDeviceClass  # type: ignore[attr-defined]
sys.modules["homeassistant.components"].update = _update_pkg  # type: ignore[attr-defined]

# entity / entity_platform stubs (only what update.py imports)
_helpers = sys.modules["homeassistant.helpers"]
_entity = _stub("homeassistant.helpers.entity")
if not hasattr(_entity, "DeviceInfo"):
    _entity.DeviceInfo = dict  # type: ignore[attr-defined]
_helpers.entity = _entity  # type: ignore[attr-defined]
_ep = _stub("homeassistant.helpers.entity_platform")
if not hasattr(_ep, "AddEntitiesCallback"):
    _ep.AddEntitiesCallback = object  # type: ignore[attr-defined]
_helpers.entity_platform = _ep  # type: ignore[attr-defined]

# CoordinatorEntity stub (already partly there from conftest)
_uc = sys.modules["homeassistant.helpers.update_coordinator"]
if not hasattr(_uc, "CoordinatorEntity"):

    class _CoordinatorEntity:
        def __init__(self, coordinator) -> None:
            self.coordinator = coordinator

        def __class_getitem__(cls, item):
            return cls

    _uc.CoordinatorEntity = _CoordinatorEntity  # type: ignore[attr-defined]


# Load update.py with full package context.
_upd_name = "custom_components.wrtsensor.update"
if _upd_name not in sys.modules:
    _spec = importlib.util.spec_from_file_location(_upd_name, _WRT / "update.py")
    _mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
    _mod.__package__ = "custom_components.wrtsensor"
    sys.modules[_upd_name] = _mod
    _spec.loader.exec_module(_mod)  # type: ignore[union-attr]

WrtsensorHostUpdate = sys.modules[_upd_name].WrtsensorHostUpdate
async_setup_entry = sys.modules[_upd_name].async_setup_entry


# Pull the ASU pruner out of __init__.py via test_init.py's renamed module.
import tests.test_init as test_init  # noqa: E402

_init_mod = test_init._init_mod
_prune_asu = _init_mod._prune_asu_entities


# ── Test helpers ──────────────────────────────────────────────────────────────


class _FakeEntry:
    entry_id = "test-entry"


class _FakeCoordinator:
    def __init__(
        self,
        *,
        asu: dict | None = None,
        host_stats: dict | None = None,
        last_update_success: bool = True,
        gateway_host: str = "192.0.2.1",
        ap_hosts: tuple[str, ...] = ("192.0.2.10", "192.0.2.11"),
        enable_asu: bool = True,
    ) -> None:
        self.data = {
            "asu": asu or {},
            "host_stats": host_stats or {},
        }
        self.last_update_success = last_update_success
        self._gateway_host = gateway_host
        self._ap_hosts = list(ap_hosts)
        self._enable_asu = enable_asu


def _ok_info(installed: str = "24.10.1 r28597", latest: str | None = None) -> dict:
    return {
        "tool": "owut",
        "installed_version": installed,
        "installed_version_raw": f"OpenWrt {installed}-aaaa",
        "latest_version": latest if latest is not None else installed,
        "summary": "no changes, upgrade not necessary",
        "error": None,
    }


# ── Entity property tests ─────────────────────────────────────────────────────


def test_release_url_uses_host_address():
    coord = _FakeCoordinator(asu={"192.0.2.22": _ok_info()})
    e = WrtsensorHostUpdate(coord, _FakeEntry(), "192.0.2.22")
    assert (
        e.release_url
        == "http://192.0.2.22/cgi-bin/luci/admin/system/attendedsysupgrade/overview"
    )


def test_unique_id_pattern():
    e = WrtsensorHostUpdate(_FakeCoordinator(), _FakeEntry(), "192.0.2.22")
    assert e._attr_unique_id == "test-entry_host_192.0.2.22_firmware"


def test_unavailable_when_host_not_in_cache():
    coord = _FakeCoordinator(asu={})  # no host yet
    e = WrtsensorHostUpdate(coord, _FakeEntry(), "192.0.2.22")
    assert e.available is False
    assert e.installed_version is None
    assert e.latest_version is None


def test_available_for_owut_with_no_error():
    coord = _FakeCoordinator(asu={"192.0.2.22": _ok_info()})
    e = WrtsensorHostUpdate(coord, _FakeEntry(), "192.0.2.22")
    assert e.available is True
    assert e.installed_version == "24.10.1 r28597"
    assert e.latest_version == "24.10.1 r28597"


def test_unavailable_when_tool_missing():
    info = {
        "tool": "none",
        "installed_version": None,
        "installed_version_raw": None,
        "latest_version": None,
        "summary": None,
        "error": "owut not installed",
    }
    coord = _FakeCoordinator(asu={"192.0.2.22": info})
    e = WrtsensorHostUpdate(coord, _FakeEntry(), "192.0.2.22")
    assert e.available is False


def test_unavailable_when_coordinator_last_update_failed():
    coord = _FakeCoordinator(asu={"192.0.2.22": _ok_info()}, last_update_success=False)
    e = WrtsensorHostUpdate(coord, _FakeEntry(), "192.0.2.22")
    assert e.available is False


def test_extra_state_attributes_include_luci_url():
    info = _ok_info()
    info["error"] = None
    coord = _FakeCoordinator(asu={"192.0.2.22": info})
    e = WrtsensorHostUpdate(coord, _FakeEntry(), "192.0.2.22")
    attrs = e.extra_state_attributes
    assert (
        attrs["luci_url"]
        == "http://192.0.2.22/cgi-bin/luci/admin/system/attendedsysupgrade/overview"
    )
    assert attrs["tool"] == "owut"
    assert attrs["error"] is None


def test_version_is_newer_uses_normalised_tuple():
    e = WrtsensorHostUpdate(_FakeCoordinator(), _FakeEntry(), "h")
    # Build-hash-only differences must not flap an up-to-date device.
    assert (
        e.version_is_newer("OpenWrt 24.10.1 r28597-aaaa", "OpenWrt 24.10.1 r28597-bbbb")
        is False
    )
    assert e.version_is_newer("24.10.2", "24.10.1") is True
    assert e.version_is_newer("24.10.0", "24.10.1") is False
    # Same release, newer revision must surface as an upgrade.
    assert e.version_is_newer("24.10.1 r28600", "24.10.1 r28597") is True
    assert e.version_is_newer("24.10.1 r28597", "24.10.1 r28600") is False


# ── async_setup_entry: eager creation, opt-in gating ──────────────────────────


class _FakeHass:
    def __init__(self, coordinator):
        self.data = {"wrtsensor": {"test-entry": coordinator}}


def _collect_added(coordinator):
    added: list = []

    def add_entities(iterable):
        added.extend(iterable)

    import asyncio

    asyncio.run(async_setup_entry(_FakeHass(coordinator), _FakeEntry(), add_entities))
    return added


def test_setup_creates_one_entity_per_host_before_first_probe():
    coord = _FakeCoordinator(asu={})  # cache empty — first probe not yet done
    added = _collect_added(coord)
    assert len(added) == 3
    hostnames = sorted(e._hostname for e in added)
    assert hostnames == ["192.0.2.1", "192.0.2.10", "192.0.2.11"]
    # All start unavailable until cache fills.
    assert all(e.available is False for e in added)


def test_setup_skips_entities_when_option_disabled():
    coord = _FakeCoordinator(enable_asu=False)
    added = _collect_added(coord)
    assert added == []


def test_setup_skips_when_no_hosts_configured():
    coord = _FakeCoordinator(gateway_host=None, ap_hosts=())
    added = _collect_added(coord)
    assert added == []


# ── Pruner ────────────────────────────────────────────────────────────────────


def test_prune_removes_only_firmware_unique_id_pattern():
    test_init._reset_registry()
    entry = _FakeEntry()
    reg = test_init._singleton_registry
    # Add three matching entities + one unrelated update entity.
    for host in ("192.0.2.1", "192.0.2.10", "192.0.2.11"):
        reg.add(
            entity_id=f"update.wrtsensor_{host.replace('.', '_')}_firmware",
            unique_id=f"{entry.entry_id}_host_{host}_firmware",
            config_entry_id=entry.entry_id,
        )
    reg.add(
        entity_id="update.unrelated",
        unique_id=f"{entry.entry_id}_some_other_update",
        config_entry_id=entry.entry_id,
    )
    coord = _FakeCoordinator(enable_asu=False)
    _prune_asu(None, entry, coord)
    remaining = list(reg._entries.keys())
    assert remaining == ["update.unrelated"]


def test_prune_no_op_when_option_enabled():
    test_init._reset_registry()
    entry = _FakeEntry()
    reg = test_init._singleton_registry
    reg.add(
        entity_id="update.wrtsensor_gw_firmware",
        unique_id=f"{entry.entry_id}_host_192.0.2.1_firmware",
        config_entry_id=entry.entry_id,
    )
    coord = _FakeCoordinator(enable_asu=True)
    _prune_asu(None, entry, coord)
    assert "update.wrtsensor_gw_firmware" in reg._entries
