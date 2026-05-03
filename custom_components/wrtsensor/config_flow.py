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
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    ASU_INTERVAL_MAX_H,
    ASU_INTERVAL_MIN_H,
    CONF_AP_HOSTS,
    CONF_ASU_INTERVAL_H,
    CONF_DISCONNECT_THRESHOLD,
    CONF_ENABLE_ASU,
    CONF_ENABLE_DNS_STATS,
    CONF_ENABLE_HOST_METRICS,
    CONF_ENABLE_NETWORK_HOSTS,
    CONF_ENABLE_WAN_BANDWIDTH,
    CONF_ENABLE_WIREGUARD,
    CONF_GATEWAY_HOST,
    CONF_LAN_IFACE,
    CONF_PRESENCE_MACS,
    CONF_SSH_KEY_PATH,
    CONF_WAN_IFACE,
    CONF_WG_STALE_THRESHOLD,
    DEFAULT_ASU_INTERVAL_H,
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
)
from .hosts import HostEndpoint, HostEndpointError, parse_host_endpoint

_LOGGER = logging.getLogger(__name__)


def _parse_hosts(raw: str) -> list[str]:
    return [h.strip() for h in raw.split(",") if h.strip()]


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


def _parse_config_endpoints(
    gateway_host: str, ap_hosts: list[str]
) -> list[HostEndpoint]:
    raw_hosts = ([gateway_host] if gateway_host else []) + ap_hosts
    return [parse_host_endpoint(host) for host in raw_hosts]


@dataclass
class ProbeResult:
    errors: dict[str, str] = field(default_factory=dict)
    placeholders: dict[str, str] = field(default_factory=dict)
    needs_provision: bool = False
    gateway_host: str = ""
    ssh_key_path: str = ""
    ap_hosts_raw: str = ""
    ap_hosts: list[str] = field(default_factory=list)


async def _probe_hosts(user_input: dict[str, Any]) -> ProbeResult:
    """Parse user input, validate, and SSH-probe all configured hosts.

    Routes to provisioning only when *every* failure is auth_failed; any
    non-auth failure (cannot_connect, no_response, ssh_key_not_found)
    short-circuits to setup_failed so a dead host isn't misreported as a
    provisioning failure later.
    """
    result = ProbeResult(
        gateway_host=user_input.get(CONF_GATEWAY_HOST, "").strip(),
        ssh_key_path=user_input[CONF_SSH_KEY_PATH].strip(),
        ap_hosts_raw=user_input.get(CONF_AP_HOSTS, ""),
    )
    result.ap_hosts = _parse_hosts(result.ap_hosts_raw)

    if not result.gateway_host and not result.ap_hosts:
        result.errors["base"] = "at_least_one_host"
        return result

    try:
        all_endpoints = _parse_config_endpoints(result.gateway_host, result.ap_hosts)
    except HostEndpointError:
        result.errors["base"] = "invalid_host"
        result.placeholders["failures"] = ", ".join(
            ([result.gateway_host] if result.gateway_host else []) + result.ap_hosts
        )
        return result

    test_results = await asyncio.gather(
        *[_test_ssh(ep.host, result.ssh_key_path, ep.port) for ep in all_endpoints]
    )
    failures = [(ep, r) for ep, r in zip(all_endpoints, test_results) if r]
    non_auth = [(ep, r) for ep, r in failures if r != "auth_failed"]
    auth_failed = [(ep, r) for ep, r in failures if r == "auth_failed"]

    if non_auth:
        result.errors["base"] = "setup_failed"
        result.placeholders["failures"] = _format_failures(
            [(ep.raw, r) for ep, r in non_auth]
        )
    elif auth_failed:
        result.needs_provision = True

    return result


def _normalized_connection(result: ProbeResult) -> dict[str, str]:
    """Connection-field overlay for storage: stripped + canonical CSV."""
    return {
        CONF_GATEWAY_HOST: result.gateway_host,
        CONF_SSH_KEY_PATH: result.ssh_key_path,
        CONF_AP_HOSTS: ",".join(result.ap_hosts),
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
    pending: dict[str, Any], ssh_user: str, ssh_password: str
) -> tuple[dict[str, str], dict[str, str]]:
    """Push the public key to every pending host, then re-probe with the key.

    Returns (errors, placeholders). Empty errors == success.
    """
    errors: dict[str, str] = {}
    placeholders: dict[str, str] = {}

    key_path = pending[CONF_SSH_KEY_PATH]
    gateway = pending.get(CONF_GATEWAY_HOST, "")
    ap_hosts = _parse_hosts(pending.get(CONF_AP_HOSTS, ""))
    endpoints = _parse_config_endpoints(gateway, ap_hosts)

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


class WrtsensorConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 2

    def __init__(self) -> None:
        super().__init__()
        self._pending: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}

        if user_input is not None:
            probe = await _probe_hosts(user_input)
            if probe.needs_provision:
                self._pending = _normalized_connection(probe)
                return await self.async_step_provision_key()
            if not probe.errors:
                unique_id = (
                    parse_host_endpoint(probe.gateway_host).host
                    if probe.gateway_host
                    else parse_host_endpoint(probe.ap_hosts[0]).host
                )
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                title = (
                    f"wrtsensor ({probe.gateway_host})"
                    if probe.gateway_host
                    else f"wrtsensor (APs: {','.join(probe.ap_hosts)})"
                )
                return self.async_create_entry(
                    title=title,
                    data=_normalized_connection(probe),
                )
            errors = probe.errors
            placeholders = probe.placeholders

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_GATEWAY_HOST,
                    default="",
                    description={"suggested_value": "192.168.1.1"},
                ): str,
                vol.Required(
                    CONF_SSH_KEY_PATH,
                    default=DEFAULT_SSH_KEY,
                ): str,
                vol.Optional(CONF_AP_HOSTS, default=""): str,
            }
        )
        return self.async_show_form(
            step_id="user",
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
                self._pending,
                user_input["ssh_user"].strip(),
                user_input["ssh_password"],
            )
            if not errors:
                gateway = self._pending.get(CONF_GATEWAY_HOST, "")
                ap_hosts_csv = self._pending.get(CONF_AP_HOSTS, "")
                title = (
                    f"wrtsensor ({gateway})"
                    if gateway
                    else f"wrtsensor (APs: {ap_hosts_csv})"
                )
                return self.async_create_entry(title=title, data=self._pending)

        return self.async_show_form(
            step_id="provision_key",
            data_schema=_PROVISION_SCHEMA,
            errors=errors,
            description_placeholders=placeholders or None,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> WrtsensorOptionsFlow:
        return WrtsensorOptionsFlow(config_entry)


class WrtsensorOptionsFlow(OptionsFlow):
    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry
        self._pending: dict[str, Any] = {}

    def _update_connection_data(self, connection: dict[str, str]) -> None:
        """Persist connection fields in ConfigEntry.data, not options."""
        new_data = {**self._config_entry.data, **connection}
        hass = getattr(self, "hass", None)
        if hass is None:
            # Unit-test stubs instantiate the flow directly without HA's flow
            # manager. Production OptionsFlow instances always have hass.
            self._config_entry.data = new_data
            return
        hass.config_entries.async_update_entry(self._config_entry, data=new_data)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        current = {**self._config_entry.data, **self._config_entry.options}

        if user_input is not None:
            probe = await _probe_hosts(user_input)
            if probe.needs_provision:
                self._pending = {**user_input, **_normalized_connection(probe)}
                return await self.async_step_provision_key()
            if not probe.errors:
                connection = _normalized_connection(probe)
                self._update_connection_data(connection)
                return self.async_create_entry(
                    title="",
                    data={**user_input, **connection},
                )
            errors = probe.errors
            placeholders = probe.placeholders

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SSH_KEY_PATH,
                    default=current.get(CONF_SSH_KEY_PATH, DEFAULT_SSH_KEY),
                ): str,
                vol.Optional(
                    CONF_GATEWAY_HOST,
                    default=current.get(CONF_GATEWAY_HOST, ""),
                ): str,
                vol.Optional(
                    CONF_AP_HOSTS,
                    default=current.get(CONF_AP_HOSTS, ""),
                ): str,
                vol.Optional(
                    CONF_LAN_IFACE,
                    default=current.get(CONF_LAN_IFACE, DEFAULT_LAN_IFACE),
                ): str,
                vol.Optional(
                    CONF_WAN_IFACE,
                    default=current.get(CONF_WAN_IFACE, DEFAULT_WAN_IFACE),
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
                vol.Optional(
                    CONF_ASU_INTERVAL_H,
                    default=current.get(CONF_ASU_INTERVAL_H, DEFAULT_ASU_INTERVAL_H),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=ASU_INTERVAL_MIN_H,
                        max=ASU_INTERVAL_MAX_H,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="h",
                    )
                ),
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
                self._pending,
                user_input["ssh_user"].strip(),
                user_input["ssh_password"],
            )
            if not errors:
                self._update_connection_data(
                    {
                        CONF_GATEWAY_HOST: self._pending.get(CONF_GATEWAY_HOST, ""),
                        CONF_SSH_KEY_PATH: self._pending[CONF_SSH_KEY_PATH],
                        CONF_AP_HOSTS: self._pending.get(CONF_AP_HOSTS, ""),
                    }
                )
                return self.async_create_entry(title="", data=self._pending)

        return self.async_show_form(
            step_id="provision_key",
            data_schema=_PROVISION_SCHEMA,
            errors=errors,
            description_placeholders=placeholders or None,
        )
