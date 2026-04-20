"""Diagnostics support for wrtsensor."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN


def _redact_mac(mac: str) -> str:
    parts = mac.split(":")
    return ":".join(parts[:3] + ["xx", "xx", "xx"]) if len(parts) == 6 else mac


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator = hass.data[DOMAIN].get(entry.entry_id)
    data = (coordinator.data or {}) if coordinator else {}

    return {
        "integration_version": entry.version,
        "scan_duration_s": data.get("scan_duration"),
        "device_count": data.get("device_count"),
        "wan_ip": "redacted",
        "wan_ip6": "redacted",
        "partial_scan": data.get("partial"),
        "host_stats": data.get("host_stats"),
        "devices": [
            {
                "mac": _redact_mac(d.get("mac", "")),
                "vendor": d.get("vendor"),
                "connection": d.get("connection"),
                "ap": d.get("ap"),
                "band": d.get("band"),
                "online": d.get("online"),
            }
            for d in data.get("devices", [])
        ],
    }
