"""Per-host DeviceInfo helper shared by sensor and update platforms."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN


def host_device_info(
    entry: ConfigEntry,
    coordinator: DataUpdateCoordinator,
    hostname: str,
) -> DeviceInfo:
    host_data = (coordinator.data or {}).get("host_stats", {}).get(hostname, {})
    model = host_data.get("model") or "OpenWrt"
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_{hostname}")},
        name=hostname,
        manufacturer="OpenWrt",
        model=model,
    )
