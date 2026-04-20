"""Device tracker entities for wrtsensor."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.device_tracker import SourceType
from homeassistant.components.device_tracker.config_entry import ScannerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import WrtsensorCoordinator
from .parser import _is_random_mac
from .sensor import _device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: WrtsensorCoordinator = hass.data[DOMAIN][entry.entry_id]
    tracked: set[str] = set()

    @callback
    def _handle_coordinator_update() -> None:
        data = coordinator.data or {}
        new_entities = []
        for device in data.get("devices", []):
            mac = device.get("mac", "").lower()
            if mac and mac not in tracked:
                tracked.add(mac)
                new_entities.append(WrtsensorDeviceTracker(coordinator, entry, mac))
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_handle_coordinator_update))
    _handle_coordinator_update()


class WrtsensorDeviceTracker(CoordinatorEntity[WrtsensorCoordinator], ScannerEntity):
    _attr_has_entity_name = True
    _attr_source_type = SourceType.ROUTER

    def __init__(
        self,
        coordinator: WrtsensorCoordinator,
        entry: ConfigEntry,
        mac: str,
    ) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._attr_unique_id = f"{entry.entry_id}_tracker_{mac.replace(':', '')}"
        self._attr_device_info = _device_info(entry)

    def _get_device(self) -> dict[str, Any] | None:
        data = self.coordinator.data or {}
        for d in data.get("devices", []):
            if d.get("mac", "").lower() == self._mac:
                return d
        return None

    @property
    def name(self) -> str:
        dev = self._get_device()
        if dev:
            return dev.get("hostname") or dev.get("vendor") or self._mac
        return self._mac

    @property
    def is_connected(self) -> bool:
        dev = self._get_device()
        return bool(dev and dev.get("online"))

    @property
    def ip_address(self) -> str | None:
        dev = self._get_device()
        return dev.get("ip") if dev else None

    @property
    def mac_address(self) -> str:
        return self._mac

    @property
    def hostname(self) -> str | None:
        dev = self._get_device()
        return dev.get("hostname") if dev else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        dev = self._get_device()
        if not dev:
            return {}
        return {
            "ip6": dev.get("ip6"),
            "ap": dev.get("ap"),
            "signal": dev.get("signal"),
            "vendor": dev.get("vendor"),
            "rx_total": dev.get("rx_total"),
            "tx_total": dev.get("tx_total"),
            "random_mac": _is_random_mac(self._mac),
        }
