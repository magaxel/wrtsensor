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
        TEXT = "text"

    class _TextSelectorConfig:
        def __init__(self, *, type=None, multiline=None):
            self.type = type
            self.multiline = multiline

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
            self.default = default
            self.description = description

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


# ── Helpers for the autodetect flow ────────────────────────────────────────────


def _classification(gateway=None, aps=(), switches=()):
    return types.SimpleNamespace(
        gateway=gateway, aps=list(aps), switches=list(switches)
    )


def _detect_ok(gateway=None, aps=(), switches=()):
    """An AsyncMock standing in for cf._detect_roles → (cache, Classification)."""
    cache = {}
    if gateway:
        cache[gateway] = "gateway"
    cache.update({h: "ap" for h in aps})
    cache.update({h: "switch" for h in switches})
    return AsyncMock(return_value=(cache, _classification(gateway, aps, switches)))


def _run_user(flow, hosts, *, ssh=None, detect=None):
    ssh = ssh if ssh is not None else AsyncMock(return_value=None)
    detect = detect if detect is not None else _detect_ok(gateway=None)
    with (
        patch.object(cf, "_test_ssh", new=ssh),
        patch.object(cf, "_detect_roles", new=detect),
    ):
        return asyncio.run(
            flow.async_step_user(
                {cf.CONF_HOSTS: hosts, cf.CONF_SSH_KEY_PATH: "/tmp/key"}
            )
        )


def _stash(flow, hosts):
    pairs = cf.parse_hosts_field(hosts)
    flow._pending_endpoints = [ep for ep, _ in pairs]
    flow._pending_overrides = {ep.host: r for ep, r in pairs if r}
    flow._pending_key = "/tmp/key"
    flow._pending_hosts = hosts


def _options_entry(hosts="192.0.2.1,192.0.2.22", options=None):
    return types.SimpleNamespace(
        data={
            cf.CONF_HOSTS: hosts,
            cf.CONF_SSH_KEY_PATH: "/tmp/key",
            cf.CONF_DETECTED_ROLES: {},
        },
        options=options or {},
    )


# ── User step ──────────────────────────────────────────────────────────────────


def test_user_schema_is_single_hosts_field():
    flow = cf.WrtsensorConfigFlow()
    result = asyncio.run(flow.async_step_user())
    keys = _schema_keys(result)
    assert cf.CONF_HOSTS in keys
    assert cf.CONF_SSH_KEY_PATH in keys
    assert "gateway_host" not in keys
    assert "ap_hosts" not in keys
    assert "switch_hosts" not in keys
    assert "ssh_port" not in keys


def test_empty_hosts_errors():
    flow = cf.WrtsensorConfigFlow()
    result = asyncio.run(
        flow.async_step_user({cf.CONF_HOSTS: "", cf.CONF_SSH_KEY_PATH: "/tmp/key"})
    )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "at_least_one_host"}


def test_user_success_confirms_then_creates_entry():
    flow = cf.WrtsensorConfigFlow()
    detect = _detect_ok(
        gateway="192.0.2.1", aps=["192.0.2.22"], switches=["192.0.2.24"]
    )
    first = _run_user(flow, "192.0.2.1,192.0.2.22,192.0.2.24", detect=detect)
    assert first["type"] == "form"
    assert first["step_id"] == "confirm_detected_roles"
    assert "192.0.2.1" in first["description_placeholders"]["detected"]
    final = asyncio.run(flow.async_step_confirm_detected_roles({}))
    assert final["type"] == "create_entry"
    assert final["data"][cf.CONF_HOSTS] == "192.0.2.1,192.0.2.22,192.0.2.24"
    assert final["data"][cf.CONF_DETECTED_ROLES] == {
        "192.0.2.1": "gateway",
        "192.0.2.22": "ap",
        "192.0.2.24": "switch",
    }
    assert "192.0.2.1" in final["title"]


def test_user_all_hosts_probed():
    flow = cf.WrtsensorConfigFlow()
    ssh = AsyncMock(return_value=None)
    _run_user(
        flow,
        "192.0.2.1, 192.0.2.22, 192.0.2.24",
        ssh=ssh,
        detect=_detect_ok(gateway="192.0.2.1"),
    )
    assert [c.args[0] for c in ssh.await_args_list] == [
        "192.0.2.1",
        "192.0.2.22",
        "192.0.2.24",
    ]


def test_user_inline_ports_probed_with_parsed_ports():
    flow = cf.WrtsensorConfigFlow()
    ssh = AsyncMock(return_value=None)
    _run_user(
        flow,
        "192.0.2.1:2222, 192.0.2.23:2200",
        ssh=ssh,
        detect=_detect_ok(gateway="192.0.2.1"),
    )
    assert [(c.args[0], c.args[2]) for c in ssh.await_args_list] == [
        ("192.0.2.1", 2222),
        ("192.0.2.23", 2200),
    ]


def test_user_invalid_host_errors():
    flow = cf.WrtsensorConfigFlow()
    result = asyncio.run(
        flow.async_step_user(
            {cf.CONF_HOSTS: "192.0.2.1:nope", cf.CONF_SSH_KEY_PATH: "/tmp/key"}
        )
    )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_host"}


def test_user_duplicate_host_errors():
    flow = cf.WrtsensorConfigFlow()
    result = asyncio.run(
        flow.async_step_user(
            {
                cf.CONF_HOSTS: "192.0.2.5,192.0.2.5=switch",
                cf.CONF_SSH_KEY_PATH: "/tmp/key",
            }
        )
    )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_host"}


def test_user_role_override_passed_to_detection():
    flow = cf.WrtsensorConfigFlow()
    detect = _detect_ok(gateway="192.0.2.1", switches=["192.0.2.22"])
    with (
        patch.object(cf, "_test_ssh", new=AsyncMock(return_value=None)),
        patch.object(cf, "_detect_roles", new=detect),
    ):
        asyncio.run(
            flow.async_step_user(
                {
                    cf.CONF_HOSTS: "192.0.2.1,192.0.2.22=switch",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                }
            )
        )
    assert detect.await_args.args[1] == {"192.0.2.22": "switch"}


def test_user_cannot_connect_surfaces_setup_failed():
    flow = cf.WrtsensorConfigFlow()
    ssh = AsyncMock(side_effect=["cannot_connect", None])
    with patch.object(cf, "_test_ssh", new=ssh):
        result = asyncio.run(
            flow.async_step_user(
                {
                    cf.CONF_HOSTS: "192.0.2.1,192.0.2.22",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                }
            )
        )
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "setup_failed"}
    assert "192.0.2.1" in result["description_placeholders"]["failures"]


def test_user_auth_failed_routes_to_provision():
    flow = cf.WrtsensorConfigFlow()
    ssh = AsyncMock(side_effect=[None, "auth_failed"])
    with patch.object(cf, "_test_ssh", new=ssh):
        result = asyncio.run(
            flow.async_step_user(
                {
                    cf.CONF_HOSTS: "192.0.2.1,192.0.2.22",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                }
            )
        )
    assert result["step_id"] == "provision_key"


def test_two_hosts_fail_both_listed():
    flow = cf.WrtsensorConfigFlow()
    ssh = AsyncMock(side_effect=["no_response", "cannot_connect"])
    with patch.object(cf, "_test_ssh", new=ssh):
        result = asyncio.run(
            flow.async_step_user(
                {
                    cf.CONF_HOSTS: "192.0.2.1,192.0.2.22",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                }
            )
        )
    failures = result["description_placeholders"]["failures"]
    assert "192.0.2.1" in failures and "192.0.2.22" in failures


# ── Provision step ─────────────────────────────────────────────────────────────


def test_provision_provisions_all_hosts_then_confirms():
    flow = cf.WrtsensorConfigFlow()
    _stash(flow, "192.0.2.1:2222,192.0.2.22,192.0.2.24:2200")
    prov = AsyncMock(return_value=None)
    post = AsyncMock(return_value=None)
    with (
        patch.object(cf, "_provision_ssh_key", new=prov),
        patch.object(cf, "_test_ssh", new=post),
        patch.object(cf, "_detect_roles", new=_detect_ok(gateway="192.0.2.1")),
    ):
        result = asyncio.run(
            flow.async_step_provision_key({"ssh_user": "root", "ssh_password": "pw"})
        )
    assert [(c.args[0], c.args[1]) for c in prov.await_args_list] == [
        ("192.0.2.1", 2222),
        ("192.0.2.22", 22),
        ("192.0.2.24", 2200),
    ]
    assert result["step_id"] == "confirm_detected_roles"


def test_provision_failure_names_host():
    flow = cf.WrtsensorConfigFlow()
    _stash(flow, "192.0.2.1,192.0.2.22")
    prov = AsyncMock(side_effect=[None, "provision_auth_failed"])
    with (
        patch.object(cf, "_provision_ssh_key", new=prov),
        patch.object(cf, "_test_ssh", new=AsyncMock(return_value=None)),
    ):
        result = asyncio.run(
            flow.async_step_provision_key({"ssh_user": "root", "ssh_password": "pw"})
        )
    assert result["errors"] == {"base": "provision_failed"}
    assert "192.0.2.22" in result["description_placeholders"]["failures"]


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


# ── Options flow ───────────────────────────────────────────────────────────────


def test_options_schema_is_single_hosts_field_no_legacy():
    flow = cf.WrtsensorOptionsFlow(_options_entry())
    result = asyncio.run(flow.async_step_init())
    keys = _schema_keys(result)
    assert cf.CONF_HOSTS in keys
    assert "gateway_host" not in keys
    assert "ap_hosts" not in keys
    assert "switch_hosts" not in keys
    assert "ssh_port" not in keys
    assert "scan_interval" not in keys
    assert "asu_interval_h" not in keys


def test_options_edit_stores_connection_in_data_only():
    entry = _options_entry()
    flow = cf.WrtsensorOptionsFlow(entry)
    detect = _detect_ok(gateway="192.0.2.1", aps=["192.0.2.22"])
    with (
        patch.object(cf, "_test_ssh", new=AsyncMock(return_value=None)),
        patch.object(cf, "_detect_roles", new=detect),
    ):
        result = asyncio.run(
            flow.async_step_init(
                {
                    cf.CONF_HOSTS: "192.0.2.1,192.0.2.22",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                    cf.CONF_ENABLE_WIREGUARD: True,
                }
            )
        )
    assert result["type"] == "create_entry"
    # toggles -> options (returned data becomes entry.options)
    assert result["data"][cf.CONF_ENABLE_WIREGUARD] is True
    assert cf.CONF_HOSTS not in result["data"]
    assert cf.CONF_SSH_KEY_PATH not in result["data"]
    # connection -> entry.data
    assert entry.data[cf.CONF_HOSTS] == "192.0.2.1,192.0.2.22"
    assert entry.data[cf.CONF_DETECTED_ROLES] == {
        "192.0.2.1": "gateway",
        "192.0.2.22": "ap",
    }


def test_options_toggle_roundtrips():
    entry = _options_entry()
    flow = cf.WrtsensorOptionsFlow(entry)
    with (
        patch.object(cf, "_test_ssh", new=AsyncMock(return_value=None)),
        patch.object(
            cf, "_detect_roles", new=_detect_ok(gateway="192.0.2.1", aps=["192.0.2.22"])
        ),
    ):
        result = asyncio.run(
            flow.async_step_init(
                {
                    cf.CONF_HOSTS: "192.0.2.1,192.0.2.22",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                    cf.CONF_ENABLE_HOST_METRICS: False,
                }
            )
        )
    assert result["data"][cf.CONF_ENABLE_HOST_METRICS] is False


def test_options_iface_defaults_render_blank_for_autodetect():
    entry = _options_entry(
        options={cf.CONF_LAN_IFACE: "br-lan", cf.CONF_WAN_IFACE: "eth0"}
    )
    flow = cf.WrtsensorOptionsFlow(entry)
    result = asyncio.run(flow.async_step_init())
    sugg = {}
    for marker in result["data_schema"]:
        key = getattr(marker, "key", marker)
        desc = getattr(marker, "description", None)
        if isinstance(desc, dict):
            sugg[key] = desc.get("suggested_value")
    assert sugg.get(cf.CONF_LAN_IFACE) == ""
    assert sugg.get(cf.CONF_WAN_IFACE) == ""


def test_options_unchanged_hosts_skip_probe():
    entry = _options_entry(hosts="192.0.2.1,192.0.2.22")
    flow = cf.WrtsensorOptionsFlow(entry)
    ssh = AsyncMock(return_value=None)
    with (
        patch.object(cf, "_test_ssh", new=ssh),
        patch.object(
            cf, "_detect_roles", new=_detect_ok(gateway="192.0.2.1", aps=["192.0.2.22"])
        ),
    ):
        asyncio.run(
            flow.async_step_init(
                {
                    cf.CONF_HOSTS: "192.0.2.1,192.0.2.22",
                    cf.CONF_SSH_KEY_PATH: "/tmp/key",
                }
            )
        )
    assert ssh.await_count == 0
