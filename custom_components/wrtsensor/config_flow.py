from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import asyncssh
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    ASU_INTERVAL_MAX_S,
    ASU_INTERVAL_MIN_S,
    CONF_AP_HOSTS,
    CONF_ASU_INTERVAL_S,
    CONF_DISCONNECT_THRESHOLD,
    CONF_ENABLE_ASU,
    CONF_ENABLE_DNS_STATS,
    CONF_ENABLE_HOST_METRICS,
    CONF_ENABLE_WIREGUARD,
    CONF_GATEWAY_HOST,
    CONF_LAN_IFACE,
    CONF_PRESENCE_MACS,
    CONF_SCAN_INTERVAL,
    CONF_SSH_KEY_PATH,
    CONF_WAN_IFACE,
    CONF_WG_STALE_THRESHOLD,
    DEFAULT_ASU_INTERVAL_S,
    DEFAULT_DISCONNECT_THRESHOLD,
    DEFAULT_ENABLE_ASU,
    DEFAULT_ENABLE_DNS_STATS,
    DEFAULT_ENABLE_HOST_METRICS,
    DEFAULT_ENABLE_WIREGUARD,
    DEFAULT_LAN_IFACE,
    DEFAULT_SCAN_INTERVAL,
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


async def _test_ssh(host: str, ssh_key_path: str, ssh_port: int = 22) -> str | None:
    """Return None on success or an error key string."""
    if not Path(ssh_key_path).exists():
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
        priv_key = asyncssh.read_private_key(key_path)
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


class WrtsensorConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 2

    def __init__(self) -> None:
        super().__init__()
        self._pending: dict[str, Any] = {}
        self._is_reconfigure: bool = False

    async def _async_probe_hosts(
        self, user_input: dict[str, Any]
    ) -> tuple[dict[str, str], dict[str, str], bool, str, str, str]:
        """Parse user input, validate, and SSH-probe all configured hosts.

        Returns (errors, placeholders, needs_provision, gateway_host,
        ssh_key_path, ap_hosts_raw).
        When needs_provision is True, self._pending has been populated.
        """
        gateway_host = user_input.get(CONF_GATEWAY_HOST, "").strip()
        ssh_key_path = user_input[CONF_SSH_KEY_PATH].strip()
        ap_hosts_raw = user_input.get(CONF_AP_HOSTS, "")
        ap_hosts = _parse_hosts(ap_hosts_raw)

        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}

        if not gateway_host and not ap_hosts:
            errors["base"] = "at_least_one_host"
            return (
                errors,
                placeholders,
                False,
                gateway_host,
                ssh_key_path,
                ap_hosts_raw,
            )

        try:
            all_endpoints = _parse_config_endpoints(gateway_host, ap_hosts)
        except HostEndpointError:
            errors["base"] = "invalid_host"
            placeholders["failures"] = ", ".join(
                ([gateway_host] if gateway_host else []) + ap_hosts
            )
            return (
                errors,
                placeholders,
                False,
                gateway_host,
                ssh_key_path,
                ap_hosts_raw,
            )

        # Probe every host so a single working gateway can't mask an AP
        # with a missing key. Any auth_failed → provision; any other
        # error → surface it and stop.
        results = await asyncio.gather(
            *[_test_ssh(ep.host, ssh_key_path, ep.port) for ep in all_endpoints]
        )
        needs_provision = any(r == "auth_failed" for r in results)
        other_failures = [
            (ep.raw, r)
            for ep, r in zip(all_endpoints, results)
            if r and r != "auth_failed"
        ]
        if needs_provision:
            self._pending = {
                CONF_GATEWAY_HOST: gateway_host,
                CONF_SSH_KEY_PATH: ssh_key_path,
                CONF_AP_HOSTS: ap_hosts_raw,
            }
        elif other_failures:
            errors["base"] = "setup_failed"
            placeholders["failures"] = _format_failures(other_failures)

        return (
            errors,
            placeholders,
            needs_provision,
            gateway_host,
            ssh_key_path,
            ap_hosts_raw,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}

        if user_input is not None:
            (
                errors,
                placeholders,
                needs_provision,
                gateway_host,
                ssh_key_path,
                ap_hosts_raw,
            ) = await self._async_probe_hosts(user_input)
            if needs_provision:
                return await self.async_step_provision_key()
            if not errors:
                unique_id = (
                    parse_host_endpoint(gateway_host).host
                    if gateway_host
                    else parse_host_endpoint(_parse_hosts(ap_hosts_raw)[0]).host
                )
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                title = (
                    f"wrtsensor ({gateway_host})"
                    if gateway_host
                    else f"wrtsensor (APs: {ap_hosts_raw})"
                )
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_GATEWAY_HOST: gateway_host,
                        CONF_SSH_KEY_PATH: ssh_key_path,
                        CONF_AP_HOSTS: ap_hosts_raw,
                    },
                )

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

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Edit gateway + APs + SSH settings in place on an existing entry."""
        self._is_reconfigure = True
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        current = entry.data
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}

        if user_input is not None:
            (
                errors,
                placeholders,
                needs_provision,
                gateway_host,
                ssh_key_path,
                ap_hosts_raw,
            ) = await self._async_probe_hosts(user_input)
            if needs_provision:
                return await self.async_step_provision_key()
            if not errors:
                new_data = {
                    **entry.data,
                    CONF_GATEWAY_HOST: gateway_host,
                    CONF_SSH_KEY_PATH: ssh_key_path,
                    CONF_AP_HOSTS: ap_hosts_raw,
                }
                new_data.pop("ssh_port", None)
                return self.async_update_reload_and_abort(
                    entry, data=new_data, reason="reconfigure_successful"
                )

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_GATEWAY_HOST,
                    default=current.get(CONF_GATEWAY_HOST, ""),
                ): str,
                vol.Required(
                    CONF_SSH_KEY_PATH,
                    default=current.get(CONF_SSH_KEY_PATH, DEFAULT_SSH_KEY),
                ): str,
                vol.Optional(
                    CONF_AP_HOSTS,
                    default=current.get(CONF_AP_HOSTS, ""),
                ): str,
            }
        )
        return self.async_show_form(
            step_id="reconfigure",
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
            ssh_user = user_input["ssh_user"].strip()
            ssh_password = user_input["ssh_password"]
            key_path = self._pending[CONF_SSH_KEY_PATH]
            gateway = self._pending.get(CONF_GATEWAY_HOST, "")
            ap_hosts = _parse_hosts(self._pending.get(CONF_AP_HOSTS, ""))
            endpoints = _parse_config_endpoints(gateway, ap_hosts)

            prov_results = await asyncio.gather(
                *[
                    _provision_ssh_key(
                        ep.host, ep.port, ssh_user, ssh_password, key_path
                    )
                    for ep in endpoints
                ]
            )
            prov_failures = [(ep.raw, r) for ep, r in zip(endpoints, prov_results) if r]

            if prov_failures:
                errors["base"] = "provision_failed"
                placeholders["failures"] = _format_failures(prov_failures)
            else:
                post_results = await asyncio.gather(
                    *[_test_ssh(ep.host, key_path, ep.port) for ep in endpoints]
                )
                post_failures = [
                    (ep.raw, r) for ep, r in zip(endpoints, post_results) if r
                ]
                if post_failures:
                    errors["base"] = "auth_failed_after_provision"
                    placeholders["failures"] = _format_failures(post_failures)
                else:
                    if self._is_reconfigure:
                        entry = self.hass.config_entries.async_get_entry(
                            self.context["entry_id"]
                        )
                        new_data = {**entry.data, **self._pending}
                        new_data.pop("ssh_port", None)
                        return self.async_update_reload_and_abort(
                            entry,
                            data=new_data,
                            reason="reconfigure_successful",
                        )
                    title = (
                        f"wrtsensor ({gateway})"
                        if gateway
                        else f"wrtsensor (APs: {self._pending.get(CONF_AP_HOSTS, '')})"
                    )
                    return self.async_create_entry(
                        title=title,
                        data=self._pending,
                    )

        schema = vol.Schema(
            {
                vol.Required("ssh_user", default="root"): str,
                vol.Required("ssh_password"): str,
            }
        )
        return self.async_show_form(
            step_id="provision_key",
            data_schema=schema,
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

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        current = {**self._config_entry.data, **self._config_entry.options}

        if user_input is not None:
            gateway_host = current.get(CONF_GATEWAY_HOST, "")
            ap_hosts_raw = user_input.get(CONF_AP_HOSTS, current.get(CONF_AP_HOSTS, ""))
            ap_hosts = _parse_hosts(ap_hosts_raw)
            ssh_key_path = user_input.get(
                CONF_SSH_KEY_PATH, current.get(CONF_SSH_KEY_PATH, DEFAULT_SSH_KEY)
            )
            if not gateway_host and not ap_hosts:
                errors["base"] = "at_least_one_host"
            else:
                try:
                    endpoints = _parse_config_endpoints(gateway_host, ap_hosts)
                except HostEndpointError:
                    errors["base"] = "invalid_host"
                    placeholders["failures"] = ", ".join(
                        ([gateway_host] if gateway_host else []) + ap_hosts
                    )
                else:
                    results = await asyncio.gather(
                        *[_test_ssh(ep.host, ssh_key_path, ep.port) for ep in endpoints]
                    )
                    failures = [(ep.raw, r) for ep, r in zip(endpoints, results) if r]
                    if failures:
                        errors["base"] = "setup_failed"
                        placeholders["failures"] = _format_failures(failures)
                    else:
                        return self.async_create_entry(title="", data=user_input)

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SSH_KEY_PATH,
                    default=current.get(CONF_SSH_KEY_PATH, DEFAULT_SSH_KEY),
                ): str,
                vol.Optional(
                    CONF_AP_HOSTS,
                    default=current.get(CONF_AP_HOSTS, ""),
                ): str,
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=current.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=30, max=3600)),
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
                    CONF_ASU_INTERVAL_S,
                    default=current.get(CONF_ASU_INTERVAL_S, DEFAULT_ASU_INTERVAL_S),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=ASU_INTERVAL_MIN_S, max=ASU_INTERVAL_MAX_S),
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders=placeholders or None,
        )
