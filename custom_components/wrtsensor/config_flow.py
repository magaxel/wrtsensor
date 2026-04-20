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
    CONF_AP_HOSTS,
    CONF_DISCONNECT_THRESHOLD,
    CONF_GATEWAY_HOST,
    CONF_LAN_IFACE,
    CONF_PRESENCE_MACS,
    CONF_SCAN_INTERVAL,
    CONF_SSH_KEY_PATH,
    CONF_SSH_PORT,
    CONF_WAN_IFACE,
    DEFAULT_DISCONNECT_THRESHOLD,
    DEFAULT_LAN_IFACE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SSH_KEY,
    DEFAULT_SSH_PORT,
    DEFAULT_WAN_IFACE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _parse_hosts(raw: str) -> list[str]:
    return [h.strip() for h in raw.split(",") if h.strip()]


async def _test_ssh(
    gateway_host: str, ssh_key_path: str, ssh_port: int = 22
) -> str | None:
    """Return None on success or an error key string."""
    if not Path(ssh_key_path).exists():
        return "ssh_key_not_found"
    try:
        async with asyncio.timeout(10):
            async with asyncssh.connect(
                gateway_host,
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

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            gateway_host = user_input[CONF_GATEWAY_HOST].strip()
            ssh_key_path = user_input[CONF_SSH_KEY_PATH].strip()
            ssh_port = user_input.get(CONF_SSH_PORT, DEFAULT_SSH_PORT)

            await self.async_set_unique_id(gateway_host)
            self._abort_if_unique_id_configured()

            error = await _test_ssh(gateway_host, ssh_key_path, ssh_port)
            if error == "auth_failed":
                self._pending = {
                    CONF_GATEWAY_HOST: gateway_host,
                    CONF_SSH_KEY_PATH: ssh_key_path,
                    CONF_SSH_PORT: ssh_port,
                    CONF_AP_HOSTS: user_input.get(CONF_AP_HOSTS, ""),
                }
                return await self.async_step_provision_key()
            if error:
                errors["base"] = error
            else:
                return self.async_create_entry(
                    title=f"wrtsensor ({gateway_host})",
                    data={
                        CONF_GATEWAY_HOST: gateway_host,
                        CONF_SSH_KEY_PATH: ssh_key_path,
                        CONF_SSH_PORT: ssh_port,
                        CONF_AP_HOSTS: user_input.get(CONF_AP_HOSTS, ""),
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_GATEWAY_HOST,
                    description={"suggested_value": "192.168.1.1"},
                ): str,
                vol.Required(
                    CONF_SSH_KEY_PATH,
                    default=DEFAULT_SSH_KEY,
                ): str,
                vol.Optional(
                    CONF_SSH_PORT,
                    default=DEFAULT_SSH_PORT,
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
                vol.Optional(CONF_AP_HOSTS, default=""): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_provision_key(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            ssh_user = user_input["ssh_user"].strip()
            ssh_password = user_input["ssh_password"]
            key_path = self._pending[CONF_SSH_KEY_PATH]
            port = self._pending[CONF_SSH_PORT]
            hosts = [self._pending[CONF_GATEWAY_HOST]] + _parse_hosts(
                self._pending.get(CONF_AP_HOSTS, "")
            )

            for host in hosts:
                err = await _provision_ssh_key(
                    host, port, ssh_user, ssh_password, key_path
                )
                if err:
                    errors["base"] = err
                    break

            if not errors:
                error = await _test_ssh(
                    self._pending[CONF_GATEWAY_HOST], key_path, port
                )
                if error:
                    errors["base"] = "auth_failed_after_provision"
                else:
                    return self.async_create_entry(
                        title=f"wrtsensor ({self._pending[CONF_GATEWAY_HOST]})",
                        data=self._pending,
                    )

        schema = vol.Schema(
            {
                vol.Required("ssh_user", default="root"): str,
                vol.Required("ssh_password"): str,
            }
        )
        return self.async_show_form(
            step_id="provision_key", data_schema=schema, errors=errors
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
        current = {**self._config_entry.data, **self._config_entry.options}

        if user_input is not None:
            gateway_host = current[CONF_GATEWAY_HOST]
            ssh_key_path = user_input.get(
                CONF_SSH_KEY_PATH, current.get(CONF_SSH_KEY_PATH, DEFAULT_SSH_KEY)
            )
            ssh_port = user_input.get(
                CONF_SSH_PORT, current.get(CONF_SSH_PORT, DEFAULT_SSH_PORT)
            )
            error = await _test_ssh(gateway_host, ssh_key_path, ssh_port)
            if error:
                errors["base"] = error
            else:
                return self.async_create_entry(title="", data=user_input)

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SSH_KEY_PATH,
                    default=current.get(CONF_SSH_KEY_PATH, DEFAULT_SSH_KEY),
                ): str,
                vol.Optional(
                    CONF_SSH_PORT,
                    default=current.get(CONF_SSH_PORT, DEFAULT_SSH_PORT),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
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
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
