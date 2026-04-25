"""Sensor entities for wrtsensor."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
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
    tracked_hosts: set[str] = set()

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

    async_add_entities(entities)

    @callback
    def _handle_coordinator_update() -> None:
        data = coordinator.data or {}
        new_entities: list[SensorEntity] = []
        for host_key in data.get("host_stats", {}):
            if host_key in tracked_hosts:
                continue
            tracked_hosts.add(host_key)
            new_entities.extend(
                [
                    WrtsensorHostCPUSensor(coordinator, entry, host_key),
                    WrtsensorHostRAMSensor(coordinator, entry, host_key),
                    WrtsensorHostDiskSensor(coordinator, entry, host_key),
                ]
            )
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_handle_coordinator_update))
    _handle_coordinator_update()


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=_entry_title(entry),
        manufacturer="wrtsensor",
        model="OpenWrt Network Sensor",
        sw_version="1.0.0",
    )


def _entry_title(entry: ConfigEntry) -> str:
    return getattr(entry, "title", None) or "wrtsensor"


class _WrtsensorBase(CoordinatorEntity[WrtsensorCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: WrtsensorCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = _device_info(entry)


class WrtsensorNetworkScannerSensor(_WrtsensorBase):
    """Compatibility sensor — matches the legacy command_line sensor schema exactly."""

    _attr_has_entity_name = False
    _attr_name = "Network Scanner"
    _attr_suggested_object_id = "wrtsensor_network_scanner"
    _attr_icon = "mdi:lan"

    def __init__(self, coordinator: WrtsensorCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_network_scanner"

    @property
    def state(self) -> int | None:
        data = self.coordinator.data
        if not data:
            return None
        devices = data.get("devices", [])
        return sum(1 for d in devices if d.get("online"))

    _COMPAT_KEYS = (
        "devices",
        "device_count",
        "wan_ip",
        "wan_ip6",
        "gateway_mac",
        "wan_rx_rate",
        "wan_tx_rate",
        "host_stats",
        "dns_stats",
        "scan_duration",
        "partial",
    )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        if not data:
            return {}
        return {k: data[k] for k in self._COMPAT_KEYS if k in data}


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
        dns = data.get("dns_stats") or {}
        # Empty period rollups are always present as dicts with hit_pct=None;
        # walk the chain and pick the first period with actual data.
        for key in ("last_24h", "last_8h", "last_1h", "last_scan"):
            period = dns.get(key) or {}
            val = period.get("hit_pct")
            if val is not None:
                return val
        return None


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
        dns = data.get("dns_stats") or {}
        return dns.get("latency_ms")


class _WrtsensorHostBase(_WrtsensorBase):
    """Base for per-host sensors; overrides device_info to show hardware model."""

    _hostname: str

    @property
    def device_info(self) -> DeviceInfo:
        host_data = (
            (self.coordinator.data or {}).get("host_stats", {}).get(self._hostname, {})
        )
        model = host_data.get("model") or "OpenWrt"
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.entry_id}_{self._hostname}")},
            name=self._hostname,
            manufacturer="OpenWrt",
            model=model,
        )


class WrtsensorHostCPUSensor(_WrtsensorHostBase):
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


class WrtsensorHostRAMSensor(_WrtsensorHostBase):
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


class WrtsensorHostDiskSensor(_WrtsensorHostBase):
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
