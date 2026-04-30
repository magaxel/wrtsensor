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

        def _abort_if_unique_id_mismatch(self, *, reason=None):
            pass

        def async_update_reload_and_abort(self, entry, *, data=None, reason):
            entry.data = data if data is not None else entry.data
            return {"type": "abort", "reason": reason, "data": entry.data}

        def async_show_form(
            self,
            *,
            step_id,
            data_schema=None,
            errors=None,
            description_placeholders=None,
        ):
            return {
                "type": "form",
                "step_id": step_id,
                "data_schema": data_schema,
                "errors": errors or {},
                "description_placeholders": description_placeholders or {},
            }

        def async_create_entry(self, *, title, data):
            return {"type": "create_entry", "title": title, "data": data}

    _ce.ConfigFlow = _ConfigFlow  # type: ignore[attr-defined]

if not hasattr(_ce, "OptionsFlow"):

    class _OptionsFlow:
        def async_show_form(
            self,
            *,
            step_id,
            data_schema=None,
            errors=None,
            description_placeholders=None,
        ):
            return {
                "type": "form",
                "step_id": step_id,
                "data_schema": data_schema,
                "errors": errors or {},
                "description_placeholders": description_placeholders or {},
            }

        def async_create_entry(self, *, title, data):
            return {"type": "create_entry", "title": title, "data": data}

    _ce.OptionsFlow = _OptionsFlow  # type: ignore[attr-defined]

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


def _schema_keys(result):
    return {getattr(marker, "key", marker) for marker in result["data_schema"]}


def test_user_schema_has_no_ssh_port():
    flow = cf.WrtsensorConfigFlow()

    result = asyncio.run(flow.async_step_user())

    assert cf.CONF_GATEWAY_HOST in _schema_keys(result)
    assert "ssh_port" not in _schema_keys(result)


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


def test_empty_gateway_probes_all_aps():
    """When no gateway, all APs are SSH-tested (no empty-string probe)."""
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
    probed = [call.args[0] for call in mock.await_args_list]
    assert probed == ["192.0.2.22", "192.0.2.23"]


def test_all_hosts_probed_for_auth():
    """Every host must be SSH-tested, not just the first one."""
    flow = cf.WrtsensorConfigFlow()
    mock = AsyncMock(return_value=None)
    with patch.object(cf, "_test_ssh", new=mock):
        asyncio.run(
            flow.async_step_user(
                {
                    cf.CONF_GATEWAY_HOST: "192.0.2.1",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                    cf.CONF_AP_HOSTS: "192.0.2.22, 192.0.2.23",
                }
            )
        )
    probed = [call.args[0] for call in mock.await_args_list]
    assert probed == ["192.0.2.1", "192.0.2.22", "192.0.2.23"]


def test_inline_ports_probe_bare_hosts_with_parsed_ports():
    flow = cf.WrtsensorConfigFlow()
    mock = AsyncMock(return_value=None)
    with patch.object(cf, "_test_ssh", new=mock):
        asyncio.run(
            flow.async_step_user(
                {
                    cf.CONF_GATEWAY_HOST: "192.0.2.1:2222",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                    cf.CONF_AP_HOSTS: "192.0.2.22, 192.0.2.23:2200",
                }
            )
        )

    assert [(call.args[0], call.args[2]) for call in mock.await_args_list] == [
        ("192.0.2.1", 2222),
        ("192.0.2.22", 22),
        ("192.0.2.23", 2200),
    ]


def test_ipv6_inline_port_requires_brackets_and_probes_port():
    flow = cf.WrtsensorConfigFlow()
    mock = AsyncMock(return_value=None)
    with patch.object(cf, "_test_ssh", new=mock):
        result = asyncio.run(
            flow.async_step_user(
                {
                    cf.CONF_GATEWAY_HOST: "[2001:db8::1]:2222",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                    cf.CONF_AP_HOSTS: "2001:db8::22",
                }
            )
        )

    assert result["type"] == "create_entry"
    assert [(call.args[0], call.args[2]) for call in mock.await_args_list] == [
        ("2001:db8::1", 2222),
        ("2001:db8::22", 22),
    ]


def test_invalid_inline_port_errors():
    flow = cf.WrtsensorConfigFlow()

    result = asyncio.run(
        flow.async_step_user(
            {
                cf.CONF_GATEWAY_HOST: "192.0.2.1:nope",
                cf.CONF_SSH_KEY_PATH: "/tmp/key",
                cf.CONF_AP_HOSTS: "",
            }
        )
    )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_host"}


def test_one_ap_auth_failed_triggers_provision():
    """Gateway OK, one AP returns auth_failed → provision step runs."""
    flow = cf.WrtsensorConfigFlow()
    # gateway OK, AP1 auth_failed, AP2 OK
    mock = AsyncMock(side_effect=[None, "auth_failed", None])
    with patch.object(cf, "_test_ssh", new=mock):
        result = asyncio.run(
            flow.async_step_user(
                {
                    cf.CONF_GATEWAY_HOST: "192.0.2.1",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                    cf.CONF_AP_HOSTS: "192.0.2.22, 192.0.2.23",
                }
            )
        )
    assert result["type"] == "form"
    assert result["step_id"] == "provision_key"


def test_gateway_cannot_connect_no_provision():
    """Non-auth error on any host is surfaced directly, provision not triggered."""
    flow = cf.WrtsensorConfigFlow()
    mock = AsyncMock(side_effect=["cannot_connect", None])
    with patch.object(cf, "_test_ssh", new=mock):
        result = asyncio.run(
            flow.async_step_user(
                {
                    cf.CONF_GATEWAY_HOST: "192.0.2.1",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                    cf.CONF_AP_HOSTS: "192.0.2.22",
                }
            )
        )
    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "setup_failed"}
    assert "192.0.2.1" in result["description_placeholders"]["failures"]
    assert "cannot connect" in result["description_placeholders"]["failures"]


def test_provision_step_provisions_all_hosts():
    """provision_key must loop over gateway + all APs, not just first host."""
    flow = cf.WrtsensorConfigFlow()
    flow._pending = {
        cf.CONF_GATEWAY_HOST: "192.0.2.1:2222",
        cf.CONF_SSH_KEY_PATH: "/tmp/key",
        cf.CONF_AP_HOSTS: "192.0.2.22, 192.0.2.23:2200",
    }
    prov = AsyncMock(return_value=None)
    post = AsyncMock(return_value=None)
    with (
        patch.object(cf, "_provision_ssh_key", new=prov),
        patch.object(cf, "_test_ssh", new=post),
    ):
        result = asyncio.run(
            flow.async_step_provision_key({"ssh_user": "root", "ssh_password": "pw"})
        )
    provisioned = [call.args[0] for call in prov.await_args_list]
    assert provisioned == ["192.0.2.1", "192.0.2.22", "192.0.2.23"]
    provisioned_ports = [call.args[1] for call in prov.await_args_list]
    assert provisioned_ports == [2222, 22, 2200]
    tested = [call.args[0] for call in post.await_args_list]
    assert tested == ["192.0.2.1", "192.0.2.22", "192.0.2.23"]
    tested_ports = [call.args[2] for call in post.await_args_list]
    assert tested_ports == [2222, 22, 2200]
    assert result["type"] == "create_entry"


def test_provision_fails_on_one_host_reports_error():
    flow = cf.WrtsensorConfigFlow()
    flow._pending = {
        cf.CONF_GATEWAY_HOST: "192.0.2.1",
        cf.CONF_SSH_KEY_PATH: "/tmp/key",
        cf.CONF_AP_HOSTS: "192.0.2.22",
    }
    # gateway provision OK, AP provision fails
    prov = AsyncMock(side_effect=[None, "provision_auth_failed"])
    with (
        patch.object(cf, "_provision_ssh_key", new=prov),
        patch.object(cf, "_test_ssh", new=AsyncMock(return_value=None)),
    ):
        result = asyncio.run(
            flow.async_step_provision_key({"ssh_user": "root", "ssh_password": "pw"})
        )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "provision_failed"}
    assert "192.0.2.22" in result["description_placeholders"]["failures"]


def test_provision_post_test_fails_on_one_host():
    """If provisioning reports success but one host still rejects the key afterwards."""
    flow = cf.WrtsensorConfigFlow()
    flow._pending = {
        cf.CONF_GATEWAY_HOST: "192.0.2.1",
        cf.CONF_SSH_KEY_PATH: "/tmp/key",
        cf.CONF_AP_HOSTS: "192.0.2.22",
    }
    prov = AsyncMock(return_value=None)
    post = AsyncMock(side_effect=[None, "auth_failed"])
    with (
        patch.object(cf, "_provision_ssh_key", new=prov),
        patch.object(cf, "_test_ssh", new=post),
    ):
        result = asyncio.run(
            flow.async_step_provision_key({"ssh_user": "root", "ssh_password": "pw"})
        )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "auth_failed_after_provision"}
    assert "192.0.2.22" in result["description_placeholders"]["failures"]


def test_two_hosts_fail_both_listed():
    """Both failing hosts must appear in the failures placeholder."""
    flow = cf.WrtsensorConfigFlow()
    mock = AsyncMock(side_effect=["no_response", "cannot_connect"])
    with patch.object(cf, "_test_ssh", new=mock):
        result = asyncio.run(
            flow.async_step_user(
                {
                    cf.CONF_GATEWAY_HOST: "192.0.2.1",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                    cf.CONF_AP_HOSTS: "192.0.2.22",
                }
            )
        )
    assert result["errors"] == {"base": "setup_failed"}
    failures = result["description_placeholders"]["failures"]
    assert "192.0.2.1" in failures
    assert "192.0.2.22" in failures
    assert "no response" in failures
    assert "cannot connect" in failures


def test_cannot_connect_names_specific_ap():
    """Only the failing AP is named, not the working ones."""
    flow = cf.WrtsensorConfigFlow()
    mock = AsyncMock(side_effect=[None, "cannot_connect", None])
    with patch.object(cf, "_test_ssh", new=mock):
        result = asyncio.run(
            flow.async_step_user(
                {
                    cf.CONF_GATEWAY_HOST: "192.0.2.1",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                    cf.CONF_AP_HOSTS: "192.0.2.22, 192.0.2.23",
                }
            )
        )
    failures = result["description_placeholders"]["failures"]
    assert "192.0.2.22" in failures
    assert "192.0.2.1" not in failures
    assert "192.0.2.23" not in failures


def test_provision_failure_multiple_hosts():
    """Multiple provision failures — all hosts listed."""
    flow = cf.WrtsensorConfigFlow()
    flow._pending = {
        cf.CONF_GATEWAY_HOST: "192.0.2.1",
        cf.CONF_SSH_KEY_PATH: "/tmp/key",
        cf.CONF_AP_HOSTS: "192.0.2.22",
    }
    prov = AsyncMock(side_effect=["provision_auth_failed", "provision_cannot_connect"])
    with (
        patch.object(cf, "_provision_ssh_key", new=prov),
        patch.object(cf, "_test_ssh", new=AsyncMock(return_value=None)),
    ):
        result = asyncio.run(
            flow.async_step_provision_key({"ssh_user": "root", "ssh_password": "pw"})
        )
    assert result["errors"] == {"base": "provision_failed"}
    failures = result["description_placeholders"]["failures"]
    assert "192.0.2.1" in failures
    assert "192.0.2.22" in failures


def test_options_flow_accepts_wireguard_toggle():
    """The WG toggle and stale-threshold round-trip through the options flow."""
    entry = types.SimpleNamespace(
        data={
            cf.CONF_GATEWAY_HOST: "192.0.2.1",
            cf.CONF_SSH_KEY_PATH: "/tmp/key",
            cf.CONF_AP_HOSTS: "192.0.2.22",
        },
        options={},
    )
    flow = cf.WrtsensorOptionsFlow(entry)
    with patch.object(cf, "_test_ssh", new=AsyncMock(return_value=None)):
        result = asyncio.run(
            flow.async_step_init(
                {
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                    cf.CONF_AP_HOSTS: "192.0.2.22",
                    cf.CONF_ENABLE_WIREGUARD: True,
                    cf.CONF_WG_STALE_THRESHOLD: 240,
                }
            )
        )
    assert result["type"] == "create_entry"
    assert result["data"][cf.CONF_ENABLE_WIREGUARD] is True
    assert result["data"][cf.CONF_WG_STALE_THRESHOLD] == 240


def test_options_schema_has_no_ssh_port():
    entry = types.SimpleNamespace(
        data={
            cf.CONF_GATEWAY_HOST: "192.0.2.1",
            cf.CONF_SSH_KEY_PATH: "/tmp/key",
            cf.CONF_AP_HOSTS: "192.0.2.22",
        },
        options={},
    )
    flow = cf.WrtsensorOptionsFlow(entry)

    result = asyncio.run(flow.async_step_init())

    assert "ssh_port" not in _schema_keys(result)


def test_options_flow_accepts_host_metrics_toggle():
    """The host metrics toggle round-trips through the options flow."""
    entry = types.SimpleNamespace(
        data={
            cf.CONF_GATEWAY_HOST: "192.0.2.1",
            cf.CONF_SSH_KEY_PATH: "/tmp/key",
            cf.CONF_AP_HOSTS: "192.0.2.22",
        },
        options={},
    )
    flow = cf.WrtsensorOptionsFlow(entry)
    with patch.object(cf, "_test_ssh", new=AsyncMock(return_value=None)):
        result = asyncio.run(
            flow.async_step_init(
                {
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                    cf.CONF_AP_HOSTS: "192.0.2.22",
                    cf.CONF_ENABLE_HOST_METRICS: False,
                }
            )
        )
    assert result["type"] == "create_entry"
    assert result["data"][cf.CONF_ENABLE_HOST_METRICS] is False


def test_options_flow_failure_names_host():
    """Options-flow reconfiguration surfaces the failing host."""
    # Build a minimal fake entry; we only need .data and .options.
    entry = types.SimpleNamespace(
        data={
            cf.CONF_GATEWAY_HOST: "192.0.2.1",
            cf.CONF_SSH_KEY_PATH: "/tmp/key",
            cf.CONF_AP_HOSTS: "192.0.2.22",
        },
        options={},
    )
    flow = cf.WrtsensorOptionsFlow(entry)
    mock = AsyncMock(side_effect=[None, "cannot_connect"])
    with patch.object(cf, "_test_ssh", new=mock):
        result = asyncio.run(
            flow.async_step_init(
                {
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                    cf.CONF_AP_HOSTS: "192.0.2.22",
                }
            )
        )
    assert result["errors"] == {"base": "setup_failed"}
    assert "192.0.2.22" in result["description_placeholders"]["failures"]


# ── Reconfigure flow ──────────────────────────────────────────────────────────


def _reconfigure_flow(entry):
    flow = cf.WrtsensorConfigFlow()
    flow.context = {"entry_id": "test-entry"}
    flow.hass = types.SimpleNamespace(
        config_entries=types.SimpleNamespace(async_get_entry=lambda eid: entry)
    )
    return flow


def _make_entry(data):
    return types.SimpleNamespace(data=data, entry_id="test-entry")


def test_reconfigure_schema_has_no_ssh_port():
    entry = _make_entry(
        {
            cf.CONF_GATEWAY_HOST: "192.0.2.1",
            cf.CONF_SSH_KEY_PATH: "/tmp/key",
            "ssh_port": 2222,
            cf.CONF_AP_HOSTS: "192.0.2.22",
        }
    )
    flow = _reconfigure_flow(entry)

    result = asyncio.run(flow.async_step_reconfigure())

    assert "ssh_port" not in _schema_keys(result)


def test_reconfigure_changes_gateway():
    entry = _make_entry(
        {
            cf.CONF_GATEWAY_HOST: "192.0.2.1",
            cf.CONF_SSH_KEY_PATH: "/tmp/key",
            "ssh_port": 2222,
            cf.CONF_AP_HOSTS: "192.0.2.22",
        }
    )
    flow = _reconfigure_flow(entry)
    with patch.object(cf, "_test_ssh", new=AsyncMock(return_value=None)):
        result = asyncio.run(
            flow.async_step_reconfigure(
                {
                    cf.CONF_GATEWAY_HOST: "192.0.2.99",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                    cf.CONF_AP_HOSTS: "192.0.2.22",
                }
            )
        )
    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    assert "ssh_port" not in result["data"]
    assert result["data"][cf.CONF_GATEWAY_HOST] == "192.0.2.99"
    assert "ssh_port" not in result["data"]


def test_reconfigure_adds_gateway_to_aps_only():
    entry = _make_entry(
        {
            cf.CONF_GATEWAY_HOST: "",
            cf.CONF_SSH_KEY_PATH: "/tmp/key",
            cf.CONF_AP_HOSTS: "192.0.2.22",
        }
    )
    flow = _reconfigure_flow(entry)
    with patch.object(cf, "_test_ssh", new=AsyncMock(return_value=None)):
        result = asyncio.run(
            flow.async_step_reconfigure(
                {
                    cf.CONF_GATEWAY_HOST: "192.0.2.1",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                    cf.CONF_AP_HOSTS: "192.0.2.22",
                }
            )
        )
    assert result["reason"] == "reconfigure_successful"
    assert result["data"][cf.CONF_GATEWAY_HOST] == "192.0.2.1"


def test_reconfigure_removes_gateway():
    entry = _make_entry(
        {
            cf.CONF_GATEWAY_HOST: "192.0.2.1",
            cf.CONF_SSH_KEY_PATH: "/tmp/key",
            "ssh_port": 2222,
            cf.CONF_AP_HOSTS: "192.0.2.22",
        }
    )
    flow = _reconfigure_flow(entry)
    with patch.object(cf, "_test_ssh", new=AsyncMock(return_value=None)):
        result = asyncio.run(
            flow.async_step_reconfigure(
                {
                    cf.CONF_GATEWAY_HOST: "",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                    cf.CONF_AP_HOSTS: "192.0.2.22",
                }
            )
        )
    assert result["reason"] == "reconfigure_successful"
    assert result["data"][cf.CONF_GATEWAY_HOST] == ""


def test_reconfigure_auth_failed_runs_provision():
    entry = _make_entry(
        {
            cf.CONF_GATEWAY_HOST: "192.0.2.1",
            cf.CONF_SSH_KEY_PATH: "/tmp/key",
            cf.CONF_AP_HOSTS: "192.0.2.22",
        }
    )
    flow = _reconfigure_flow(entry)
    with patch.object(cf, "_test_ssh", new=AsyncMock(return_value="auth_failed")):
        result = asyncio.run(
            flow.async_step_reconfigure(
                {
                    cf.CONF_GATEWAY_HOST: "192.0.2.1",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                    cf.CONF_AP_HOSTS: "192.0.2.22",
                }
            )
        )
    assert result["type"] == "form"
    assert result["step_id"] == "provision_key"
    assert flow._is_reconfigure is True


def test_reconfigure_probe_failure_names_host():
    entry = _make_entry(
        {
            cf.CONF_GATEWAY_HOST: "192.0.2.1",
            cf.CONF_SSH_KEY_PATH: "/tmp/key",
            cf.CONF_AP_HOSTS: "192.0.2.22",
        }
    )
    flow = _reconfigure_flow(entry)
    with patch.object(
        cf, "_test_ssh", new=AsyncMock(side_effect=[None, "cannot_connect"])
    ):
        result = asyncio.run(
            flow.async_step_reconfigure(
                {
                    cf.CONF_GATEWAY_HOST: "192.0.2.1",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                    cf.CONF_AP_HOSTS: "192.0.2.22",
                }
            )
        )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "setup_failed"}
    assert "192.0.2.22" in result["description_placeholders"]["failures"]


def test_reconfigure_provision_completes_via_update_reload_and_abort():
    """After provisioning succeeds inside reconfigure, completion path is the
    update-reload-abort helper, not async_create_entry."""
    entry = _make_entry(
        {
            cf.CONF_GATEWAY_HOST: "192.0.2.1",
            cf.CONF_SSH_KEY_PATH: "/tmp/key",
            cf.CONF_AP_HOSTS: "192.0.2.22",
        }
    )
    flow = _reconfigure_flow(entry)
    flow._is_reconfigure = True
    flow._pending = {
        cf.CONF_GATEWAY_HOST: "192.0.2.1",
        cf.CONF_SSH_KEY_PATH: "/tmp/key",
        cf.CONF_AP_HOSTS: "192.0.2.22",
    }
    with (
        patch.object(cf, "_provision_ssh_key", new=AsyncMock(return_value=None)),
        patch.object(cf, "_test_ssh", new=AsyncMock(return_value=None)),
    ):
        result = asyncio.run(
            flow.async_step_provision_key({"ssh_user": "root", "ssh_password": "pw"})
        )
    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    assert "ssh_port" not in result["data"]
