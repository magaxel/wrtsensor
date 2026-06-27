from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import asyncssh
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_DETECTED_ROLES,
    CONF_DISCONNECT_THRESHOLD,
    CONF_ENABLE_ASU,
    CONF_ENABLE_DNS_STATS,
    CONF_ENABLE_HOST_METRICS,
    CONF_ENABLE_NETWORK_HOSTS,
    CONF_ENABLE_WAN_BANDWIDTH,
    CONF_ENABLE_WIREGUARD,
    CONF_HOSTS,
    CONF_LAN_IFACE,
    CONF_PRESENCE_MACS,
    CONF_SSH_KEY_PATH,
    CONF_WAN_IFACE,
    CONF_WG_STALE_THRESHOLD,
    DEFAULT_DISCONNECT_THRESHOLD,
    DEFAULT_ENABLE_ASU,
    DEFAULT_ENABLE_DNS_STATS,
    DEFAULT_ENABLE_HOST_METRICS,
    DEFAULT_ENABLE_NETWORK_HOSTS,
    DEFAULT_ENABLE_WAN_BANDWIDTH,
    DEFAULT_ENABLE_WIREGUARD,
    DEFAULT_LAN_IFACE,
    DEFAULT_SSH_KEY,
    DEFAULT_WAN_IFACE,
    DEFAULT_WG_STALE_THRESHOLD,
    DOMAIN,
    ROLE_AP,
    ROLE_GATEWAY,
    ROLE_SWITCH,
)
from .detect import Classification, RoleSignals, classify, probe_role
from .hosts import HostEndpoint, HostEndpointError, parse_hosts_field

_LOGGER = logging.getLogger(__name__)

# Connection state lives in entry.data; everything else (toggles) in entry.options.
_CONNECTION_KEYS = (CONF_HOSTS, CONF_SSH_KEY_PATH, CONF_DETECTED_ROLES)

_ERROR_LABELS = {
    "cannot_connect": "cannot connect",
    "no_response": "no response",
    "ssh_key_not_found": "SSH key file not found",
    "auth_failed": "SSH authentication failed",
    "auth_failed_after_provision": "still rejects key after provisioning",
    "invalid_host": "invalid host",
    "provision_auth_failed": "password authentication failed",
    "provision_cannot_connect": "cannot connect for provisioning",
    "pub_key_unreadable": "public key unreadable",
}


def _format_failures(pairs: list[tuple[str, str]]) -> str:
    return ", ".join(f"{h} — {_ERROR_LABELS.get(k, k)}" for h, k in pairs)


def _canonical_hosts(pairs: list[tuple[HostEndpoint, str | None]]) -> str:
    """Canonical CSV for storage: original endpoint spec + optional =role suffix."""
    return ",".join(ep.raw + (f"={role}" if role else "") for ep, role in pairs)


def _roles_summary(classification: Classification) -> str:
    lines: list[str] = []
    if classification.gateway:
        lines.append(f"{classification.gateway} → gateway")
    lines += [f"{h} → access point" for h in classification.aps]
    lines += [f"{h} → switch" for h in classification.switches]
    return "\n".join(lines) if lines else "(none)"


def _entry_title(classification: Classification, endpoints: list[HostEndpoint]) -> str:
    if classification.gateway:
        return f"wrtsensor ({classification.gateway})"
    return f"wrtsensor ({endpoints[0].host})"


@dataclass
class ProbeResult:
    errors: dict[str, str] = field(default_factory=dict)
    placeholders: dict[str, str] = field(default_factory=dict)
    needs_provision: bool = False
    hosts_raw: str = ""
    ssh_key_path: str = ""
    pairs: list[tuple[HostEndpoint, str | None]] = field(default_factory=list)

    @property
    def endpoints(self) -> list[HostEndpoint]:
        return [ep for ep, _ in self.pairs]

    @property
    def overrides(self) -> dict[str, str]:
        return {ep.host: role for ep, role in self.pairs if role}


def _parse_pairs(result: ProbeResult) -> bool:
    """Parse hosts_raw into result.pairs. Returns False (and sets errors) on failure."""
    if not result.hosts_raw.strip():
        result.errors["base"] = "at_least_one_host"
        return False
    try:
        result.pairs = parse_hosts_field(result.hosts_raw)
    except HostEndpointError as err:
        result.errors["base"] = "invalid_host"
        result.placeholders["failures"] = str(err) or result.hosts_raw
        return False
    if not result.pairs:
        result.errors["base"] = "at_least_one_host"
        return False
    return True


async def _probe_hosts(user_input: dict[str, Any]) -> ProbeResult:
    """Parse + SSH-probe all configured hosts.

    Routes to provisioning only when *every* failure is auth_failed; any
    non-auth failure short-circuits to setup_failed so a dead host isn't
    misreported as a provisioning failure later.
    """
    result = ProbeResult(
        hosts_raw=user_input.get(CONF_HOSTS, ""),
        ssh_key_path=user_input[CONF_SSH_KEY_PATH].strip(),
    )
    if not _parse_pairs(result):
        return result

    endpoints = result.endpoints
    test_results = await asyncio.gather(
        *[_test_ssh(ep.host, result.ssh_key_path, ep.port) for ep in endpoints]
    )
    _route_failures(result, list(zip(endpoints, test_results)))
    return result


async def _probe_options_hosts(
    user_input: dict[str, Any], current: dict[str, Any]
) -> ProbeResult:
    """Validate options-flow host edits without blocking removals on old hosts."""
    result = ProbeResult(
        hosts_raw=user_input.get(CONF_HOSTS, ""),
        ssh_key_path=user_input[CONF_SSH_KEY_PATH].strip(),
    )
    if not _parse_pairs(result):
        return result

    endpoints = result.endpoints
    current_key = (current.get(CONF_SSH_KEY_PATH, DEFAULT_SSH_KEY) or "").strip()
    if result.ssh_key_path != current_key:
        endpoints_to_probe = endpoints
    else:
        try:
            current_ids = {
                (ep.host, ep.port)
                for ep, _ in parse_hosts_field(current.get(CONF_HOSTS, "") or "")
            }
        except HostEndpointError:
            current_ids = set()
        endpoints_to_probe = [
            ep for ep in endpoints if (ep.host, ep.port) not in current_ids
        ]

    if not endpoints_to_probe:
        return result

    test_results = await asyncio.gather(
        *[_test_ssh(ep.host, result.ssh_key_path, ep.port) for ep in endpoints_to_probe]
    )
    _route_failures(result, list(zip(endpoints_to_probe, test_results)))
    return result


def _route_failures(
    result: ProbeResult, tested: list[tuple[HostEndpoint, str | None]]
) -> None:
    failures = [(ep, r) for ep, r in tested if r]
    non_auth = [(ep, r) for ep, r in failures if r != "auth_failed"]
    auth_failed = [(ep, r) for ep, r in failures if r == "auth_failed"]
    if non_auth:
        result.errors["base"] = "setup_failed"
        result.placeholders["failures"] = _format_failures(
            [(ep.raw, r) for ep, r in non_auth]
        )
    elif auth_failed:
        result.needs_provision = True


async def _detect_roles(
    endpoints: list[HostEndpoint],
    overrides: dict[str, str],
    ssh_key_path: str,
    cached: dict[str, str] | None = None,
) -> tuple[dict[str, str], Classification]:
    """Probe every endpoint in parallel and classify. Returns (cache, result)."""
    probe_results = await asyncio.gather(
        *[probe_role(ep.host, ssh_key_path, ep.port) for ep in endpoints]
    )
    signals: dict[str, RoleSignals | None] = {
        ep.host: sig for ep, sig in zip(endpoints, probe_results)
    }
    classification = classify(signals, overrides, cached or {})
    cache: dict[str, str] = {}
    if classification.gateway:
        cache[classification.gateway] = ROLE_GATEWAY
    cache.update({h: ROLE_AP for h in classification.aps})
    cache.update({h: ROLE_SWITCH for h in classification.switches})
    return cache, classification


def _connection_data(
    hosts_raw: str, ssh_key_path: str, detected_roles: dict[str, str]
) -> dict[str, Any]:
    return {
        CONF_HOSTS: hosts_raw,
        CONF_SSH_KEY_PATH: ssh_key_path,
        CONF_DETECTED_ROLES: detected_roles,
    }


async def _test_ssh(host: str, ssh_key_path: str, ssh_port: int = 22) -> str | None:
    """Return None on success or an error key string."""
    key_exists = await asyncio.to_thread(lambda: Path(ssh_key_path).exists())
    if not key_exists:
        return "ssh_key_not_found"
    try:
        async with asyncio.timeout(10):
            async with asyncssh.connect(
                host,
                port=ssh_port,
                username="root",
                client_keys=[ssh_key_path],
                known_hosts=None,
            ) as conn:
                result = await conn.run("uname -n", check=True)
                if not result.stdout.strip():
                    return "no_response"
    except asyncssh.PermissionDenied:
        return "auth_failed"
    except (TimeoutError, asyncio.TimeoutError):
        return "cannot_connect"
    except Exception:  # noqa: BLE001
        return "cannot_connect"
    return None


async def _provision_ssh_key(
    host: str, port: int, username: str, password: str, key_path: str
) -> str | None:
    """Add the public key to /etc/dropbear/authorized_keys via password auth.

    Returns None on success or an error key string.
    """
    try:
        priv_key = await asyncio.to_thread(asyncssh.read_private_key, key_path)
        pub_key_str = priv_key.export_public_key("openssh").decode().strip()
    except Exception:  # noqa: BLE001
        return "pub_key_unreadable"
    try:
        async with asyncio.timeout(15):
            async with asyncssh.connect(
                host,
                port=port,
                username=username,
                password=password,
                known_hosts=None,
                connect_timeout=10,
            ) as conn:
                result = await conn.run(
                    "cat /etc/dropbear/authorized_keys 2>/dev/null || echo ''"
                )
                if pub_key_str not in (result.stdout or ""):
                    await conn.run(
                        "mkdir -p /etc/dropbear; "
                        "cat >> /etc/dropbear/authorized_keys; "
                        "chmod 600 /etc/dropbear/authorized_keys",
                        input=pub_key_str + "\n",
                    )
    except asyncssh.PermissionDenied:
        return "provision_auth_failed"
    except (asyncssh.Error, asyncio.TimeoutError, OSError):
        return "provision_cannot_connect"
    return None


async def _run_provision(
    endpoints: list[HostEndpoint], key_path: str, ssh_user: str, ssh_password: str
) -> tuple[dict[str, str], dict[str, str]]:
    """Push the public key to every host, then re-probe with the key.

    Returns (errors, placeholders). Empty errors == success.
    """
    errors: dict[str, str] = {}
    placeholders: dict[str, str] = {}

    prov_results = await asyncio.gather(
        *[
            _provision_ssh_key(ep.host, ep.port, ssh_user, ssh_password, key_path)
            for ep in endpoints
        ]
    )
    prov_failures = [(ep.raw, r) for ep, r in zip(endpoints, prov_results) if r]
    if prov_failures:
        errors["base"] = "provision_failed"
        placeholders["failures"] = _format_failures(prov_failures)
        return errors, placeholders

    post_results = await asyncio.gather(
        *[_test_ssh(ep.host, key_path, ep.port) for ep in endpoints]
    )
    post_failures = [(ep.raw, r) for ep, r in zip(endpoints, post_results) if r]
    if post_failures:
        errors["base"] = "auth_failed_after_provision"
        placeholders["failures"] = _format_failures(post_failures)

    return errors, placeholders


_PROVISION_SCHEMA = vol.Schema(
    {
        vol.Required("ssh_user", default="root"): str,
        vol.Required("ssh_password"): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


def _hosts_field() -> TextSelector:
    return TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT, multiline=True))


def _iface_suggestion(value: str | None, default: str) -> str:
    """Show only a real override in the field; default/blank renders empty so the
    field reads as 'autodetect'."""
    value = (value or "").strip()
    return value if value and value != default else ""


class WrtsensorConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._pending_endpoints: list[HostEndpoint] = []
        self._pending_overrides: dict[str, str] = {}
        self._pending_key: str = ""
        self._pending_hosts: str = ""
        self._pending_data: dict[str, Any] = {}
        self._pending_title: str = ""
        self._pending_summary: str = ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}

        if user_input is not None:
            probe = await _probe_hosts(user_input)
            if probe.needs_provision:
                await self.async_set_unique_id(probe.endpoints[0].host)
                self._abort_if_unique_id_configured()
                self._stash_pending(probe)
                return await self.async_step_provision_key()
            if not probe.errors:
                await self.async_set_unique_id(probe.endpoints[0].host)
                self._abort_if_unique_id_configured()
                self._stash_pending(probe)
                cache, classification = await _detect_roles(
                    probe.endpoints, probe.overrides, probe.ssh_key_path
                )
                self._finalize_pending(probe, cache, classification)
                return await self.async_step_confirm_detected_roles()
            errors = probe.errors
            placeholders = probe.placeholders

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_HOSTS,
                    description={"suggested_value": user_input.get(CONF_HOSTS, "")}
                    if user_input
                    else None,
                ): _hosts_field(),
                vol.Required(
                    CONF_SSH_KEY_PATH,
                    default=DEFAULT_SSH_KEY,
                ): str,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders=placeholders or None,
        )

    def _stash_pending(self, probe: ProbeResult) -> None:
        self._pending_endpoints = probe.endpoints
        self._pending_overrides = probe.overrides
        self._pending_key = probe.ssh_key_path
        self._pending_hosts = _canonical_hosts(probe.pairs)

    def _finalize_pending(
        self,
        probe: ProbeResult,
        cache: dict[str, str],
        classification: Classification,
    ) -> None:
        self._pending_data = _connection_data(
            self._pending_hosts, self._pending_key, cache
        )
        self._pending_title = _entry_title(classification, probe.endpoints)
        self._pending_summary = _roles_summary(classification)

    async def async_step_provision_key(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}

        if user_input is not None:
            errors, placeholders = await _run_provision(
                self._pending_endpoints,
                self._pending_key,
                user_input["ssh_user"].strip(),
                user_input["ssh_password"],
            )
            if not errors:
                cache, classification = await _detect_roles(
                    self._pending_endpoints, self._pending_overrides, self._pending_key
                )
                self._pending_data = _connection_data(
                    self._pending_hosts, self._pending_key, cache
                )
                self._pending_title = _entry_title(
                    classification, self._pending_endpoints
                )
                self._pending_summary = _roles_summary(classification)
                return await self.async_step_confirm_detected_roles()

        return self.async_show_form(
            step_id="provision_key",
            data_schema=_PROVISION_SCHEMA,
            errors=errors,
            description_placeholders=placeholders or None,
        )

    async def async_step_confirm_detected_roles(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title=self._pending_title, data=self._pending_data
            )
        return self.async_show_form(
            step_id="confirm_detected_roles",
            data_schema=vol.Schema({}),
            description_placeholders={"detected": self._pending_summary},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> WrtsensorOptionsFlow:
        return WrtsensorOptionsFlow(config_entry)


class WrtsensorOptionsFlow(OptionsFlow):
    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry
        self._pending_endpoints: list[HostEndpoint] = []
        self._pending_overrides: dict[str, str] = {}
        self._pending_key: str = ""
        self._pending_hosts: str = ""
        self._pending_options: dict[str, Any] = {}

    def _update_connection_data(self, connection: dict[str, Any]) -> None:
        """Persist connection fields in ConfigEntry.data, not options."""
        new_data = {**self._config_entry.data, **connection}
        hass = getattr(self, "hass", None)
        if hass is None:
            # Unit-test stubs instantiate the flow directly without HA's flow
            # manager. Production OptionsFlow instances always have hass.
            self._config_entry.data = new_data
            return
        hass.config_entries.async_update_entry(self._config_entry, data=new_data)

    def _options_only(self, user_input: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in user_input.items() if k not in _CONNECTION_KEYS}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        current = {**self._config_entry.data, **self._config_entry.options}

        if user_input is not None:
            probe = await _probe_options_hosts(user_input, current)
            if probe.needs_provision:
                self._pending_endpoints = probe.endpoints
                self._pending_overrides = probe.overrides
                self._pending_key = probe.ssh_key_path
                self._pending_hosts = _canonical_hosts(probe.pairs)
                self._pending_options = self._options_only(user_input)
                return await self.async_step_provision_key()
            if not probe.errors:
                cache, _classification = await _detect_roles(
                    probe.endpoints,
                    probe.overrides,
                    probe.ssh_key_path,
                    current.get(CONF_DETECTED_ROLES, {}),
                )
                self._update_connection_data(
                    _connection_data(
                        _canonical_hosts(probe.pairs), probe.ssh_key_path, cache
                    )
                )
                return self.async_create_entry(
                    title="", data=self._options_only(user_input)
                )
            errors = probe.errors
            placeholders = probe.placeholders

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_HOSTS,
                    description={"suggested_value": current.get(CONF_HOSTS, "")},
                ): _hosts_field(),
                vol.Required(
                    CONF_SSH_KEY_PATH,
                    default=current.get(CONF_SSH_KEY_PATH, DEFAULT_SSH_KEY),
                ): str,
                vol.Optional(
                    CONF_LAN_IFACE,
                    description={
                        "suggested_value": _iface_suggestion(
                            current.get(CONF_LAN_IFACE), DEFAULT_LAN_IFACE
                        )
                    },
                ): str,
                vol.Optional(
                    CONF_WAN_IFACE,
                    description={
                        "suggested_value": _iface_suggestion(
                            current.get(CONF_WAN_IFACE), DEFAULT_WAN_IFACE
                        )
                    },
                ): str,
                vol.Optional(
                    CONF_DISCONNECT_THRESHOLD,
                    default=current.get(
                        CONF_DISCONNECT_THRESHOLD, DEFAULT_DISCONNECT_THRESHOLD
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=30, max=3600)),
                vol.Optional(
                    CONF_PRESENCE_MACS,
                    default=current.get(CONF_PRESENCE_MACS, ""),
                ): str,
                vol.Optional(
                    CONF_ENABLE_NETWORK_HOSTS,
                    default=current.get(
                        CONF_ENABLE_NETWORK_HOSTS, DEFAULT_ENABLE_NETWORK_HOSTS
                    ),
                ): bool,
                vol.Optional(
                    CONF_ENABLE_WAN_BANDWIDTH,
                    default=current.get(
                        CONF_ENABLE_WAN_BANDWIDTH, DEFAULT_ENABLE_WAN_BANDWIDTH
                    ),
                ): bool,
                vol.Optional(
                    CONF_ENABLE_DNS_STATS,
                    default=current.get(
                        CONF_ENABLE_DNS_STATS, DEFAULT_ENABLE_DNS_STATS
                    ),
                ): bool,
                vol.Optional(
                    CONF_ENABLE_HOST_METRICS,
                    default=current.get(
                        CONF_ENABLE_HOST_METRICS, DEFAULT_ENABLE_HOST_METRICS
                    ),
                ): bool,
                vol.Optional(
                    CONF_ENABLE_WIREGUARD,
                    default=current.get(
                        CONF_ENABLE_WIREGUARD, DEFAULT_ENABLE_WIREGUARD
                    ),
                ): bool,
                vol.Optional(
                    CONF_WG_STALE_THRESHOLD,
                    default=current.get(
                        CONF_WG_STALE_THRESHOLD, DEFAULT_WG_STALE_THRESHOLD
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=30, max=3600)),
                vol.Optional(
                    CONF_ENABLE_ASU,
                    default=current.get(CONF_ENABLE_ASU, DEFAULT_ENABLE_ASU),
                ): bool,
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders=placeholders or None,
        )

    async def async_step_provision_key(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}

        if user_input is not None:
            errors, placeholders = await _run_provision(
                self._pending_endpoints,
                self._pending_key,
                user_input["ssh_user"].strip(),
                user_input["ssh_password"],
            )
            if not errors:
                current = {**self._config_entry.data, **self._config_entry.options}
                cache, _classification = await _detect_roles(
                    self._pending_endpoints,
                    self._pending_overrides,
                    self._pending_key,
                    current.get(CONF_DETECTED_ROLES, {}),
                )
                self._update_connection_data(
                    _connection_data(self._pending_hosts, self._pending_key, cache)
                )
                return self.async_create_entry(title="", data=self._pending_options)

        return self.async_show_form(
            step_id="provision_key",
            data_schema=_PROVISION_SCHEMA,
            errors=errors,
            description_placeholders=placeholders or None,
        )
