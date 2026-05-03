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

_selector = _ensure_module("homeassistant.helpers.selector")
if not hasattr(_selector, "TextSelector"):

    class _NumberSelectorMode:
        BOX = "box"

    class _NumberSelectorConfig:
        def __init__(
            self,
            *,
            min=None,
            max=None,
            step=None,
            mode=None,
            unit_of_measurement=None,
        ):
            self.min = min
            self.max = max
            self.step = step
            self.mode = mode
            self.unit_of_measurement = unit_of_measurement

    class _NumberSelector:
        def __init__(self, config):
            self.config = config

    class _TextSelectorType:
        PASSWORD = "password"

    class _TextSelectorConfig:
        def __init__(self, *, type=None):
            self.type = type

    class _TextSelector:
        def __init__(self, config):
            self.config = config

    _selector.NumberSelector = _NumberSelector  # type: ignore[attr-defined]
    _selector.NumberSelectorConfig = _NumberSelectorConfig  # type: ignore[attr-defined]
    _selector.NumberSelectorMode = _NumberSelectorMode  # type: ignore[attr-defined]
    _selector.TextSelector = _TextSelector  # type: ignore[attr-defined]
    _selector.TextSelectorConfig = _TextSelectorConfig  # type: ignore[attr-defined]
    _selector.TextSelectorType = _TextSelectorType  # type: ignore[attr-defined]

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

    class _RequiredMarker(_Marker):
        pass

    class _OptionalMarker(_Marker):
        pass

    _vol.Schema = lambda d: d  # type: ignore[attr-defined]
    _vol.Required = _RequiredMarker  # type: ignore[attr-defined]
    _vol.Optional = _OptionalMarker  # type: ignore[attr-defined]
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
                    cf.CONF_GATEWAY_HOST: "192.0.2.1",
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


def test_options_schema_has_no_scan_interval():
    entry = types.SimpleNamespace(
        data={
            cf.CONF_GATEWAY_HOST: "192.0.2.1",
            cf.CONF_SSH_KEY_PATH: "/tmp/key",
            cf.CONF_AP_HOSTS: "192.0.2.22",
        },
        options={"scan_interval": 300},
    )
    flow = cf.WrtsensorOptionsFlow(entry)

    result = asyncio.run(flow.async_step_init())

    assert "scan_interval" not in _schema_keys(result)


def test_provision_password_uses_password_selector():
    flow = cf.WrtsensorConfigFlow()

    result = asyncio.run(flow.async_step_provision_key())

    schema = result["data_schema"]
    password_selector = next(
        value
        for marker, value in schema.items()
        if getattr(marker, "key", marker) == "ssh_password"
    )
    assert password_selector.config.type == cf.TextSelectorType.PASSWORD


def test_asu_interval_uses_number_box_selector():
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

    schema = result["data_schema"]
    asu_selector = next(
        value
        for marker, value in schema.items()
        if getattr(marker, "key", marker) == cf.CONF_ASU_INTERVAL_H
    )
    assert asu_selector.config.mode == cf.NumberSelectorMode.BOX
    assert asu_selector.config.min == cf.ASU_INTERVAL_MIN_H
    assert asu_selector.config.max == cf.ASU_INTERVAL_MAX_H
    assert asu_selector.config.step == 1


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
                    cf.CONF_GATEWAY_HOST: "192.0.2.1",
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
    mock = AsyncMock(return_value="cannot_connect")
    with patch.object(cf, "_test_ssh", new=mock):
        result = asyncio.run(
            flow.async_step_init(
                {
                    cf.CONF_GATEWAY_HOST: "192.0.2.1",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                    cf.CONF_AP_HOSTS: "192.0.2.22,192.0.2.23",
                }
            )
        )
    assert result["errors"] == {"base": "setup_failed"}
    assert "192.0.2.23" in result["description_placeholders"]["failures"]


# ── Options flow: gateway + AP editing (replaces former reconfigure flow) ─────


def _options_entry(data=None, options=None):
    return types.SimpleNamespace(
        data=data
        or {
            cf.CONF_GATEWAY_HOST: "192.0.2.1",
            cf.CONF_SSH_KEY_PATH: "/tmp/key",
            cf.CONF_AP_HOSTS: "192.0.2.22",
        },
        options=options or {},
    )


def test_options_schema_includes_gateway():
    """Connection fields are now part of the options form."""
    entry = _options_entry()
    flow = cf.WrtsensorOptionsFlow(entry)

    result = asyncio.run(flow.async_step_init())

    ordered_keys = [getattr(marker, "key", marker) for marker in result["data_schema"]]
    keys = set(ordered_keys)
    assert ordered_keys[0] == cf.CONF_SSH_KEY_PATH
    assert cf.CONF_GATEWAY_HOST in keys
    assert cf.CONF_SSH_KEY_PATH in keys
    assert cf.CONF_AP_HOSTS in keys


def test_options_connection_fields_are_required_to_allow_clearing():
    """Optional text fields with defaults can restore the old value when blank."""
    entry = _options_entry()
    flow = cf.WrtsensorOptionsFlow(entry)

    result = asyncio.run(flow.async_step_init())
    schema = result["data_schema"]
    markers = {getattr(marker, "key", marker): marker for marker in schema}

    assert isinstance(markers[cf.CONF_GATEWAY_HOST], cf.vol.Required)
    assert isinstance(markers[cf.CONF_AP_HOSTS], cf.vol.Required)


def test_options_flow_changes_gateway():
    """Submitting a new gateway via options writes it to the saved data."""
    entry = _options_entry()
    flow = cf.WrtsensorOptionsFlow(entry)
    with patch.object(cf, "_test_ssh", new=AsyncMock(return_value=None)):
        result = asyncio.run(
            flow.async_step_init(
                {
                    cf.CONF_GATEWAY_HOST: "192.0.2.99",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                    cf.CONF_AP_HOSTS: "192.0.2.22",
                }
            )
        )
    assert result["type"] == "create_entry"
    assert entry.data[cf.CONF_GATEWAY_HOST] == "192.0.2.99"
    assert result["data"][cf.CONF_GATEWAY_HOST] == "192.0.2.99"


def test_options_flow_can_remove_gateway():
    """Clearing the gateway is allowed when at least one AP remains."""
    entry = _options_entry()
    flow = cf.WrtsensorOptionsFlow(entry)
    with patch.object(
        cf,
        "_test_ssh",
        new=AsyncMock(side_effect=AssertionError("unchanged AP must not be probed")),
    ):
        result = asyncio.run(
            flow.async_step_init(
                {
                    cf.CONF_GATEWAY_HOST: "",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                    cf.CONF_AP_HOSTS: "192.0.2.22",
                }
            )
        )
    assert result["type"] == "create_entry"
    assert entry.data[cf.CONF_GATEWAY_HOST] == ""
    assert result["data"][cf.CONF_GATEWAY_HOST] == ""


def test_options_flow_remove_gateway_ignores_existing_ap_probe_failure():
    """A transient failure on an unchanged AP must not block gateway removal."""
    entry = _options_entry()
    flow = cf.WrtsensorOptionsFlow(entry)
    with patch.object(
        cf,
        "_test_ssh",
        new=AsyncMock(side_effect=AssertionError("unchanged AP must not be probed")),
    ):
        result = asyncio.run(
            flow.async_step_init(
                {
                    cf.CONF_GATEWAY_HOST: "",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                    cf.CONF_AP_HOSTS: "192.0.2.22",
                }
            )
        )

    assert result["type"] == "create_entry"
    assert result["data"][cf.CONF_GATEWAY_HOST] == ""


def test_options_flow_probes_added_ap():
    entry = _options_entry()
    flow = cf.WrtsensorOptionsFlow(entry)
    probe = AsyncMock(return_value=None)
    with patch.object(cf, "_test_ssh", new=probe):
        result = asyncio.run(
            flow.async_step_init(
                {
                    cf.CONF_GATEWAY_HOST: "192.0.2.1",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                    cf.CONF_AP_HOSTS: "192.0.2.22,192.0.2.23",
                }
            )
        )

    assert result["type"] == "create_entry"
    probe.assert_awaited_once_with("192.0.2.23", "/tmp/key", 22)


def test_options_flow_can_add_gateway_to_aps_only():
    entry = _options_entry(
        data={
            cf.CONF_GATEWAY_HOST: "",
            cf.CONF_SSH_KEY_PATH: "/tmp/key",
            cf.CONF_AP_HOSTS: "192.0.2.22",
        }
    )
    flow = cf.WrtsensorOptionsFlow(entry)
    with patch.object(cf, "_test_ssh", new=AsyncMock(return_value=None)):
        result = asyncio.run(
            flow.async_step_init(
                {
                    cf.CONF_GATEWAY_HOST: "192.0.2.1",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                    cf.CONF_AP_HOSTS: "192.0.2.22",
                }
            )
        )
    assert result["type"] == "create_entry"
    assert entry.data[cf.CONF_GATEWAY_HOST] == "192.0.2.1"
    assert result["data"][cf.CONF_GATEWAY_HOST] == "192.0.2.1"


def test_options_flow_normalizes_ap_hosts():
    """AP host CSV is normalized: whitespace stripped, joined with commas."""
    entry = _options_entry()
    flow = cf.WrtsensorOptionsFlow(entry)
    with patch.object(cf, "_test_ssh", new=AsyncMock(return_value=None)):
        result = asyncio.run(
            flow.async_step_init(
                {
                    cf.CONF_GATEWAY_HOST: "  192.0.2.1  ",
                    cf.CONF_SSH_KEY_PATH: "  /tmp/key  ",
                    cf.CONF_AP_HOSTS: "192.0.2.22 ,   192.0.2.23",
                }
            )
        )
    assert entry.data[cf.CONF_GATEWAY_HOST] == "192.0.2.1"
    assert entry.data[cf.CONF_SSH_KEY_PATH] == "/tmp/key"
    assert entry.data[cf.CONF_AP_HOSTS] == "192.0.2.22,192.0.2.23"
    assert result["data"][cf.CONF_GATEWAY_HOST] == "192.0.2.1"
    assert result["data"][cf.CONF_SSH_KEY_PATH] == "/tmp/key"
    assert result["data"][cf.CONF_AP_HOSTS] == "192.0.2.22,192.0.2.23"


def test_options_flow_mixed_failure_skips_provision():
    """auth_failed + cannot_connect must surface setup_failed, not provision."""
    entry = _options_entry()
    flow = cf.WrtsensorOptionsFlow(entry)
    mock = AsyncMock(side_effect=["auth_failed", "cannot_connect"])
    with patch.object(cf, "_test_ssh", new=mock):
        result = asyncio.run(
            flow.async_step_init(
                {
                    cf.CONF_GATEWAY_HOST: "192.0.2.99",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                    cf.CONF_AP_HOSTS: "192.0.2.22,192.0.2.23",
                }
            )
        )
    assert result["type"] == "form"
    assert result["step_id"] == "init"
    assert result["errors"] == {"base": "setup_failed"}
    assert "192.0.2.23" in result["description_placeholders"]["failures"]


def test_options_flow_all_auth_failed_routes_to_provision():
    """Every host failing only with auth_failed → provision step."""
    entry = _options_entry()
    flow = cf.WrtsensorOptionsFlow(entry)
    with patch.object(cf, "_test_ssh", new=AsyncMock(return_value="auth_failed")):
        result = asyncio.run(
            flow.async_step_init(
                {
                    cf.CONF_GATEWAY_HOST: "192.0.2.99",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                    cf.CONF_AP_HOSTS: "192.0.2.22",
                }
            )
        )
    assert result["type"] == "form"
    assert result["step_id"] == "provision_key"


def test_options_flow_provision_preserves_unrelated_options():
    """Options like CONF_DISCONNECT_THRESHOLD must survive the provision hop."""
    entry = _options_entry()
    flow = cf.WrtsensorOptionsFlow(entry)
    with patch.object(cf, "_test_ssh", new=AsyncMock(return_value="auth_failed")):
        asyncio.run(
            flow.async_step_init(
                {
                    cf.CONF_GATEWAY_HOST: "192.0.2.99",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                    cf.CONF_AP_HOSTS: "192.0.2.22",
                    cf.CONF_DISCONNECT_THRESHOLD: 600,
                    cf.CONF_ENABLE_WIREGUARD: True,
                }
            )
        )
    with (
        patch.object(cf, "_provision_ssh_key", new=AsyncMock(return_value=None)),
        patch.object(cf, "_test_ssh", new=AsyncMock(return_value=None)),
    ):
        result = asyncio.run(
            flow.async_step_provision_key({"ssh_user": "root", "ssh_password": "pw"})
        )
    assert result["type"] == "create_entry"
    assert entry.data[cf.CONF_GATEWAY_HOST] == "192.0.2.99"
    assert result["data"][cf.CONF_GATEWAY_HOST] == "192.0.2.99"
    assert result["data"][cf.CONF_DISCONNECT_THRESHOLD] == 600
    assert result["data"][cf.CONF_ENABLE_WIREGUARD] is True


def test_options_flow_provision_failure_names_host():
    entry = _options_entry()
    flow = cf.WrtsensorOptionsFlow(entry)
    flow._pending = {
        cf.CONF_GATEWAY_HOST: "192.0.2.1",
        cf.CONF_SSH_KEY_PATH: "/tmp/key",
        cf.CONF_AP_HOSTS: "192.0.2.22",
    }
    prov = AsyncMock(side_effect=[None, "provision_auth_failed"])
    with (
        patch.object(cf, "_provision_ssh_key", new=prov),
        patch.object(cf, "_test_ssh", new=AsyncMock(return_value=None)),
    ):
        result = asyncio.run(
            flow.async_step_provision_key({"ssh_user": "root", "ssh_password": "pw"})
        )
    assert result["type"] == "form"
    assert result["step_id"] == "provision_key"
    assert result["errors"] == {"base": "provision_failed"}
    assert "192.0.2.22" in result["description_placeholders"]["failures"]


def test_user_step_normalizes_ap_hosts():
    """Initial setup also writes canonical CSV for ap_hosts."""
    flow = cf.WrtsensorConfigFlow()
    with patch.object(cf, "_test_ssh", new=AsyncMock(return_value=None)):
        result = asyncio.run(
            flow.async_step_user(
                {
                    cf.CONF_GATEWAY_HOST: "192.0.2.1",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                    cf.CONF_AP_HOSTS: "192.0.2.22 , 192.0.2.23",
                }
            )
        )
    assert result["type"] == "create_entry"
    assert result["data"][cf.CONF_AP_HOSTS] == "192.0.2.22,192.0.2.23"


def test_reconfigure_step_is_gone():
    """The reconfigure flow has been collapsed into options; method removed."""
    assert not hasattr(cf.WrtsensorConfigFlow, "async_step_reconfigure")
