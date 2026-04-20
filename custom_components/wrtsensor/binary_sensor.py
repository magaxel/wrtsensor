"""Presence binary sensors for wrtsensor."""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_PRESENCE_MACS, DOMAIN
from .coordinator import WrtsensorCoordinator
from .sensor import _device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: WrtsensorCoordinator = hass.data[DOMAIN][entry.entry_id]

    raw = entry.options.get(CONF_PRESENCE_MACS, "")
    macs = [m.strip().lower() for m in raw.split(",") if m.strip()]

    async_add_entities(WrtsensorPresenceSensor(coordinator, entry, mac) for mac in macs)


class WrtsensorPresenceSensor(
    CoordinatorEntity[WrtsensorCoordinator], BinarySensorEntity
):
    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.PRESENCE

    def __init__(
        self,
        coordinator: WrtsensorCoordinator,
        entry: ConfigEntry,
        mac: str,
    ) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._attr_name = f"Presence {mac}"
        self._attr_unique_id = f"{entry.entry_id}_presence_{mac.replace(':', '')}"
        self._attr_device_info = _device_info(entry)

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if not data:
            return None
        for device in data.get("devices", []):
            if device.get("mac", "").lower() == self._mac:
                return bool(device.get("online"))
        return False
