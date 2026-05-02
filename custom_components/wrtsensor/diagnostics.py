"""Diagnostics support for wrtsensor."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, VERSION

_HOST_METRICS = ("cpu", "ram", "disk")


def _redact_mac(mac: str) -> str:
    parts = mac.split(":")
    return ":".join(parts[:3] + ["xx", "xx", "xx"]) if len(parts) == 6 else mac


def _host_metrics_summary(host_stats: dict[str, Any]) -> dict[str, Any]:
    metrics_present = {
        metric
        for stats in host_stats.values()
        for metric in _HOST_METRICS
        if stats.get(metric) is not None
    }
    hosts_with_missing_metrics = sum(
        1
        for stats in host_stats.values()
        if any(stats.get(metric) is None for metric in _HOST_METRICS)
    )
    return {
        "host_count": len(host_stats),
        "metrics_present": sorted(metrics_present),
        "hosts_with_missing_metrics": hosts_with_missing_metrics,
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator = hass.data[DOMAIN].get(entry.entry_id)
    data = (coordinator.data or {}) if coordinator else {}
    host_stats = data.get("host_stats") or {}

    return {
        "integration_version": VERSION,
        "scan_duration_s": data.get("scan_duration"),
        "device_count": data.get("device_count"),
        "wan_ip": "redacted",
        "wan_ip6": "redacted",
        "partial_scan": bool(data.get("partial")),
        "host_metrics_summary": _host_metrics_summary(host_stats),
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
