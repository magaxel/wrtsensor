"""Tests for wrtsensor config flow validation (optional gateway)."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

_ROOT = Path(__file__).parent.parent
_WRT = _ROOT / "custom_components" / "wrtsensor"


# ── Additional HA stubs needed by config_flow ──────────────────────────────────


def _ensure_module(name: str) -> types.ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    mod = types.ModuleType(name)
    mod.__path__ = []  # type: ignore[attr-defined]
    sys.modules[name] = mod
    return mod


_ce = _ensure_module("homeassistant.config_entries")
if not hasattr(_ce, "ConfigFlow"):

    class _ConfigFlow:
        def __init_subclass__(cls, **kw):
            super().__init_subclass__()

        async def async_set_unique_id(self, uid):
            self._unique_id = uid

        def _abort_if_unique_id_configured(self):
            pass

        def async_show_form(self, *, step_id, data_schema=None, errors=None):
            return {"type": "form", "step_id": step_id, "errors": errors or {}}

        def async_create_entry(self, *, title, data):
            return {"type": "create_entry", "title": title, "data": data}

    _ce.ConfigFlow = _ConfigFlow  # type: ignore[attr-defined]

if not hasattr(_ce, "OptionsFlow"):
    _ce.OptionsFlow = type("OptionsFlow", (), {})  # type: ignore[attr-defined]

_core = _ensure_module("homeassistant.core")
if not hasattr(_core, "callback"):
    _core.callback = lambda fn: fn  # type: ignore[attr-defined]

_def = _ensure_module("homeassistant.data_entry_flow")
if not hasattr(_def, "FlowResult"):
    _def.FlowResult = dict  # type: ignore[attr-defined]

# voluptuous stub — config_flow only uses it to declare schemas we never validate
_vol = _ensure_module("voluptuous")
if not hasattr(_vol, "Schema"):

    class _Marker:
        def __init__(self, key, default=None, description=None):
            self.key = key

        def __hash__(self):
            return hash(self.key)

        def __eq__(self, other):
            return isinstance(other, _Marker) and self.key == other.key

    _vol.Schema = lambda d: d  # type: ignore[attr-defined]
    _vol.Required = _Marker  # type: ignore[attr-defined]
    _vol.Optional = _Marker  # type: ignore[attr-defined]
    _vol.All = lambda *a, **kw: object  # type: ignore[attr-defined]
    _vol.Coerce = lambda t: t  # type: ignore[attr-defined]
    _vol.Range = lambda **kw: object  # type: ignore[attr-defined]


# ── Load config_flow with package context ─────────────────────────────────────


def _load_config_flow():
    name = "custom_components.wrtsensor.config_flow"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _WRT / "config_flow.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    mod.__package__ = "custom_components.wrtsensor"
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


cf = _load_config_flow()


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_empty_gateway_and_empty_aps_errors():
    flow = cf.WrtsensorConfigFlow()
    result = asyncio.run(
        flow.async_step_user(
            {
                cf.CONF_GATEWAY_HOST: "",
                cf.CONF_SSH_KEY_PATH: "/tmp/key",
                cf.CONF_AP_HOSTS: "",
            }
        )
    )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "at_least_one_host"}


def test_empty_gateway_with_ap_creates_entry():
    flow = cf.WrtsensorConfigFlow()
    with patch.object(cf, "_test_ssh", new=AsyncMock(return_value=None)):
        result = asyncio.run(
            flow.async_step_user(
                {
                    cf.CONF_GATEWAY_HOST: "",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                    cf.CONF_AP_HOSTS: "192.0.2.22",
                }
            )
        )
    assert result["type"] == "create_entry"
    assert result["data"][cf.CONF_GATEWAY_HOST] == ""
    assert result["data"][cf.CONF_AP_HOSTS] == "192.0.2.22"
    assert "192.0.2.22" in result["title"]


def test_gateway_only_creates_entry():
    flow = cf.WrtsensorConfigFlow()
    with patch.object(cf, "_test_ssh", new=AsyncMock(return_value=None)):
        result = asyncio.run(
            flow.async_step_user(
                {
                    cf.CONF_GATEWAY_HOST: "192.0.2.1",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                    cf.CONF_AP_HOSTS: "",
                }
            )
        )
    assert result["type"] == "create_entry"
    assert result["data"][cf.CONF_GATEWAY_HOST] == "192.0.2.1"


def test_empty_gateway_tests_first_ap():
    """When no gateway, SSH test must target first AP, not empty string."""
    flow = cf.WrtsensorConfigFlow()
    mock = AsyncMock(return_value=None)
    with patch.object(cf, "_test_ssh", new=mock):
        asyncio.run(
            flow.async_step_user(
                {
                    cf.CONF_GATEWAY_HOST: "",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                    cf.CONF_AP_HOSTS: "192.0.2.22, 192.0.2.23",
                }
            )
        )
    assert mock.await_args.args[0] == "192.0.2.22"
