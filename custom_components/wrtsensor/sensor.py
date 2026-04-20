"""Sensor entities for wrtsensor."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import WrtsensorCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: WrtsensorCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = [
        WrtsensorNetworkScannerSensor(coordinator, entry),
    ]
    # Gateway-only sensors: WAN bandwidth + DNS stats require the router.
    if coordinator._gateway_host:
        entities += [
            WrtsensorWANDownloadSensor(coordinator, entry),
            WrtsensorWANUploadSensor(coordinator, entry),
            WrtsensorDNSHitPctSensor(coordinator, entry),
            WrtsensorDNSLatencySensor(coordinator, entry),
        ]

    # Per-host stats: gateway + each AP
    data = coordinator.data or {}
    host_stats = data.get("host_stats", {})
    for hostname in host_stats:
        entities += [
            WrtsensorHostCPUSensor(coordinator, entry, hostname),
            WrtsensorHostRAMSensor(coordinator, entry, hostname),
            WrtsensorHostDiskSensor(coordinator, entry, hostname),
        ]

    async_add_entities(entities)


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="wrtsensor",
        manufacturer="wrtsensor",
        model="OpenWrt Network Monitor",
        sw_version="1.0.0",
    )


class _WrtsensorBase(CoordinatorEntity[WrtsensorCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: WrtsensorCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = _device_info(entry)


class WrtsensorNetworkScannerSensor(_WrtsensorBase):
    """Compatibility sensor — matches the legacy command_line sensor schema exactly."""

    _attr_has_entity_name = False  # produces entity_id sensor.network_scanner, not sensor.wrtsensor_network_scanner
    _attr_name = "Network Scanner"
    _attr_icon = "mdi:lan"

    def __init__(self, coordinator: WrtsensorCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = "wrtsensor_network_scanner"

    @property
    def state(self) -> int | None:
        data = self.coordinator.data
        if not data:
            return None
        devices = data.get("devices", [])
        return sum(1 for d in devices if d.get("online"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.coordinator.data or {}


class WrtsensorWANDownloadSensor(_WrtsensorBase):
    _attr_name = "WAN Download"
    _attr_native_unit_of_measurement = "Mbit/s"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:download-network"

    def __init__(self, coordinator: WrtsensorCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_wan_download"

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        if not data:
            return None
        bps = data.get("wan_rx_rate")
        if bps is None:
            return None
        return round(bps * 8 / 1_000_000, 2)


class WrtsensorWANUploadSensor(_WrtsensorBase):
    _attr_name = "WAN Upload"
    _attr_native_unit_of_measurement = "Mbit/s"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:upload-network"

    def __init__(self, coordinator: WrtsensorCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_wan_upload"

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        if not data:
            return None
        bps = data.get("wan_tx_rate")
        if bps is None:
            return None
        return round(bps * 8 / 1_000_000, 2)


class WrtsensorDNSHitPctSensor(_WrtsensorBase):
    _attr_name = "DNS Cache Hit %"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:dns"

    def __init__(self, coordinator: WrtsensorCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_dns_hit_pct"

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        if not data:
            return None
        dns = data.get("dns_stats", {})
        return dns.get("hit_pct")


class WrtsensorDNSLatencySensor(_WrtsensorBase):
    _attr_name = "DNS Latency"
    _attr_native_unit_of_measurement = "ms"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator: WrtsensorCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_dns_latency"

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        if not data:
            return None
        dns = data.get("dns_stats", {})
        return dns.get("latency_ms")


class WrtsensorHostCPUSensor(_WrtsensorBase):
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:cpu-64-bit"

    def __init__(
        self,
        coordinator: WrtsensorCoordinator,
        entry: ConfigEntry,
        hostname: str,
    ) -> None:
        super().__init__(coordinator, entry)
        self._hostname = hostname
        self._attr_name = f"{hostname} CPU"
        self._attr_unique_id = f"{entry.entry_id}_host_{hostname}_cpu"

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        if not data:
            return None
        return data.get("host_stats", {}).get(self._hostname, {}).get("cpu")


class WrtsensorHostRAMSensor(_WrtsensorBase):
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:memory"

    def __init__(
        self,
        coordinator: WrtsensorCoordinator,
        entry: ConfigEntry,
        hostname: str,
    ) -> None:
        super().__init__(coordinator, entry)
        self._hostname = hostname
        self._attr_name = f"{hostname} RAM"
        self._attr_unique_id = f"{entry.entry_id}_host_{hostname}_ram"

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        if not data:
            return None
        return data.get("host_stats", {}).get(self._hostname, {}).get("ram")


class WrtsensorHostDiskSensor(_WrtsensorBase):
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:harddisk"

    def __init__(
        self,
        coordinator: WrtsensorCoordinator,
        entry: ConfigEntry,
        hostname: str,
    ) -> None:
        super().__init__(coordinator, entry)
        self._hostname = hostname
        self._attr_name = f"{hostname} Disk"
        self._attr_unique_id = f"{entry.entry_id}_host_{hostname}_disk"

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        if not data:
            return None
        return data.get("host_stats", {}).get(self._hostname, {}).get("disk")
