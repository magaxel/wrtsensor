"""Update entities for wrtsensor — surfaces OpenWrt Attended Sysupgrade status."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.update import UpdateDeviceClass, UpdateEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import WrtsensorCoordinator
from .host_device import host_device_info
from .parser import asu_version_is_newer

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: WrtsensorCoordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator._enable_asu:
        return
    hosts = ([coordinator._gateway_host] if coordinator._gateway_host else []) + list(
        coordinator._ap_hosts
    )
    if not hosts:
        return
    async_add_entities(WrtsensorHostUpdate(coordinator, entry, host) for host in hosts)


class WrtsensorHostUpdate(CoordinatorEntity[WrtsensorCoordinator], UpdateEntity):
    """Per-host OpenWrt firmware update entity backed by `owut --quiet check`."""

    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = 0
    _attr_has_entity_name = True
    _attr_translation_key = "firmware"
    _attr_name = "Firmware"

    def __init__(
        self,
        coordinator: WrtsensorCoordinator,
        entry: ConfigEntry,
        hostname: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._hostname = hostname
        self._attr_unique_id = f"{entry.entry_id}_host_{hostname}_firmware"

    @property
    def device_info(self) -> DeviceInfo:
        return host_device_info(self._entry, self.coordinator, self._hostname)

    @property
    def available(self) -> bool:
        if not self.coordinator.last_update_success:
            return False
        info = self._asu_info()
        if not info:
            return False
        return info.get("tool") == "owut" and info.get("error") is None

    @property
    def installed_version(self) -> str | None:
        return (self._asu_info() or {}).get("installed_version")

    @property
    def latest_version(self) -> str | None:
        return (self._asu_info() or {}).get("latest_version")

    @property
    def release_url(self) -> str | None:
        return f"http://{self._hostname}/cgi-bin/luci/admin/system/attendedsysupgrade/overview"

    @property
    def release_summary(self) -> str | None:
        return (self._asu_info() or {}).get("summary")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        info = self._asu_info() or {}
        # Mirror release_url — supported_features=0 may hide it in More Info
        # depending on frontend version; the attribute is always visible.
        return {
            "tool": info.get("tool"),
            "error": info.get("error"),
            "installed_version_raw": info.get("installed_version_raw"),
            "luci_url": self.release_url,
        }

    def version_is_newer(self, latest: str, installed: str) -> bool:
        return asu_version_is_newer(latest, installed)

    def _asu_info(self) -> dict[str, Any] | None:
        return ((self.coordinator.data or {}).get("asu") or {}).get(self._hostname)
