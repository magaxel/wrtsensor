"""wrtsensor — Home Assistant integration for OpenWrt network monitoring."""

from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_DISCONNECT_THRESHOLD,
    DEFAULT_DISCONNECT_THRESHOLD,
    DOMAIN,
    PLATFORMS,
    STATIC_PATH_URL,
)
from .coordinator import WrtsensorCoordinator

_LOGGER = logging.getLogger(__name__)

_WWW_DIR = Path(__file__).parent / "www"
_WS_TYPE_RECENT_EVENTS = f"{DOMAIN}/recent_events"
_WS_REGISTERED_KEY = f"{DOMAIN}_ws_registered"
_HOST_METRICS = ("cpu", "ram", "disk")


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.version < 2:
        new_data = {**entry.data}
        new_data.setdefault(CONF_DISCONNECT_THRESHOLD, DEFAULT_DISCONNECT_THRESHOLD)
        hass.config_entries.async_update_entry(entry, data=new_data, version=2)
        _LOGGER.info("Migrated wrtsensor config entry to version 2")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = WrtsensorCoordinator(hass, entry)
    await coordinator.async_setup()
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await _register_static_path(hass)
    _register_websocket_commands(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _prune_orphaned_host_entities(hass, entry, coordinator)
    _remove_legacy_event_log_entity(hass, entry)
    _prune_wireguard_entities(hass, entry, coordinator)
    _prune_asu_entities(hass, entry, coordinator)
    _prune_network_host_entities(hass, entry, coordinator)
    _prune_wan_bandwidth_entities(hass, entry, coordinator)
    _prune_dns_entities(hass, entry, coordinator)

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


def _prune_orphaned_host_entities(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: WrtsensorCoordinator
) -> None:
    """Remove per-host sensors whose hostname no longer appears in the scan.

    Triggers after every reload — including the reload kicked off by the
    reconfigure / options flow when a host is removed. Device trackers are
    intentionally left alone: MAC-keyed, HA-managed, disabled by default.
    """
    data = coordinator.data or {}
    host_stats = data.get("host_stats") or {}
    reg = er.async_get(hass)

    if not getattr(coordinator, "_enable_host_metrics", True):
        for reg_entry in list(er.async_entries_for_config_entry(reg, entry.entry_id)):
            if (
                _parse_host_metric_unique_id(entry.entry_id, reg_entry.unique_id)
                is None
            ):
                continue
            _LOGGER.info(
                "Removing wrtsensor host metric entity %s (option disabled)",
                reg_entry.entity_id,
            )
            reg.async_remove(reg_entry.entity_id)
        return

    if not host_stats:
        # Don't prune blindly if the scan returned no host data (partial or
        # cold start) — better to leave unavailable entities than nuke
        # everything.
        return
    live = set(host_stats.keys())
    for reg_entry in list(er.async_entries_for_config_entry(reg, entry.entry_id)):
        parsed = _parse_host_metric_unique_id(entry.entry_id, reg_entry.unique_id)
        if parsed is None:
            continue
        hostname, metric, legacy = parsed
        if not legacy and hostname in live:
            continue
        _LOGGER.info(
            "Pruning wrtsensor host entity %s (hostname %s, metric %s)",
            reg_entry.entity_id,
            hostname,
            metric,
        )
        reg.async_remove(reg_entry.entity_id)


def _parse_host_metric_unique_id(
    entry_id: str, unique_id: str
) -> tuple[str, str, bool] | None:
    """Return (hostname, metric, legacy) for wrtsensor host metric entities."""
    new_prefix = f"{entry_id}_host_metric_"
    old_prefix = f"{entry_id}_host_"
    if unique_id.startswith(new_prefix):
        rest = unique_id[len(new_prefix) :]
        legacy = False
    elif unique_id.startswith(old_prefix):
        rest = unique_id[len(old_prefix) :]
        legacy = True
    else:
        return None
    for metric in _HOST_METRICS:
        suffix = f"_{metric}"
        if rest.endswith(suffix):
            hostname = rest[: -len(suffix)]
            return (hostname, metric, legacy)
    return None


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator: WrtsensorCoordinator | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        if coordinator is not None:
            await coordinator.async_shutdown()
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def _register_static_path(hass: HomeAssistant) -> None:
    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_PATH_URL, str(_WWW_DIR), False)]
        )
    except RuntimeError:
        pass  # already registered (e.g. integration reloaded)


def _prune_wireguard_entities(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: WrtsensorCoordinator
) -> None:
    """Remove orphan WG peer trackers and the WG sensor when no longer wanted.

    Two scenarios:
    1. Option toggled off: coordinator._enable_wireguard is False — remove every
       _wgpeer_* registry entry and the _wireguard sensor.
    2. Option on, but a peer was removed from the server config: the live peer
       set is missing that id — remove just that tracker. Skipped on partial
       scans (data["wireguard"] is None or missing) to avoid nuking trackers
       on a transient gateway failure.
    """
    reg = er.async_get(hass)
    peer_prefix = f"{entry.entry_id}_wgpeer_"
    sensor_uid = f"{entry.entry_id}_wireguard"

    if not coordinator._enable_wireguard:
        for reg_entry in list(er.async_entries_for_config_entry(reg, entry.entry_id)):
            if (
                reg_entry.unique_id.startswith(peer_prefix)
                or reg_entry.unique_id == sensor_uid
            ):
                _LOGGER.info(
                    "Removing wrtsensor WG entity %s (option disabled)",
                    reg_entry.entity_id,
                )
                reg.async_remove(reg_entry.entity_id)
        return

    data = coordinator.data or {}
    wg = data.get("wireguard")
    if wg is None:
        # Partial scan / pre-first-refresh — leave registry alone so we don't
        # nuke trackers during a transient gateway failure.
        return
    # `wg` is a dict here (success). interfaces=[] with available=False means
    # the scan succeeded but no host has `wg` installed any more — that's a
    # legitimate "WG removed" signal, prune all peer trackers but keep the
    # WG sensor (option still on; user may reinstall).
    live_ids = {
        peer["id"]
        for iface in wg.get("interfaces", [])
        for peer in iface.get("peers", [])
        if peer.get("id")
    }
    for reg_entry in list(er.async_entries_for_config_entry(reg, entry.entry_id)):
        if not reg_entry.unique_id.startswith(peer_prefix):
            continue
        peer_id = reg_entry.unique_id[len(peer_prefix) :]
        if peer_id in live_ids:
            continue
        _LOGGER.info(
            "Pruning orphaned wrtsensor WG peer entity %s", reg_entry.entity_id
        )
        reg.async_remove(reg_entry.entity_id)


def _prune_asu_entities(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: WrtsensorCoordinator
) -> None:
    """Remove update entities when the ASU option is disabled.

    Matches by unique-id pattern (`<entry_id>_host_<name>_firmware`) so future
    update.* entities introduced by other features are not accidentally swept.
    """
    if coordinator._enable_asu:
        return
    reg = er.async_get(hass)
    prefix = f"{entry.entry_id}_host_"
    suffix = "_firmware"
    for reg_entry in list(er.async_entries_for_config_entry(reg, entry.entry_id)):
        uid = reg_entry.unique_id
        if uid.startswith(prefix) and uid.endswith(suffix):
            _LOGGER.info(
                "Removing wrtsensor update entity %s (option disabled)",
                reg_entry.entity_id,
            )
            reg.async_remove(reg_entry.entity_id)


def _prune_network_host_entities(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: WrtsensorCoordinator
) -> None:
    """Remove network-host entities when the option is disabled.

    Covers the network scanner sensor, all per-MAC device_trackers, and all
    presence binary sensors. Skipped when the option is on so that legitimate
    devices are not nuked.
    """
    if coordinator._enable_network_hosts:
        return
    reg = er.async_get(hass)
    scanner_uid = f"{entry.entry_id}_network_scanner"
    tracker_prefix = f"{entry.entry_id}_tracker_"
    presence_prefix = f"{entry.entry_id}_presence_"
    for reg_entry in list(er.async_entries_for_config_entry(reg, entry.entry_id)):
        uid = reg_entry.unique_id
        if (
            uid == scanner_uid
            or uid.startswith(tracker_prefix)
            or uid.startswith(presence_prefix)
        ):
            _LOGGER.info(
                "Removing wrtsensor network-host entity %s (option disabled)",
                reg_entry.entity_id,
            )
            reg.async_remove(reg_entry.entity_id)


def _prune_wan_bandwidth_entities(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: WrtsensorCoordinator
) -> None:
    if coordinator._enable_wan_bandwidth:
        return
    reg = er.async_get(hass)
    targets = {f"{entry.entry_id}_wan_download", f"{entry.entry_id}_wan_upload"}
    for reg_entry in list(er.async_entries_for_config_entry(reg, entry.entry_id)):
        if reg_entry.unique_id in targets:
            _LOGGER.info(
                "Removing wrtsensor WAN bandwidth entity %s (option disabled)",
                reg_entry.entity_id,
            )
            reg.async_remove(reg_entry.entity_id)


def _prune_dns_entities(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: WrtsensorCoordinator
) -> None:
    if coordinator._enable_dns_stats:
        return
    reg = er.async_get(hass)
    targets = {f"{entry.entry_id}_dns_hit_pct", f"{entry.entry_id}_dns_latency"}
    for reg_entry in list(er.async_entries_for_config_entry(reg, entry.entry_id)):
        if reg_entry.unique_id in targets:
            _LOGGER.info(
                "Removing wrtsensor DNS entity %s (option disabled)",
                reg_entry.entity_id,
            )
            reg.async_remove(reg_entry.entity_id)


def _remove_legacy_event_log_entity(hass: HomeAssistant, entry: ConfigEntry) -> None:
    reg = er.async_get(hass)
    for reg_entry in list(er.async_entries_for_config_entry(reg, entry.entry_id)):
        if reg_entry.unique_id == f"{entry.entry_id}_event_log":
            reg.async_remove(reg_entry.entity_id)


def _register_websocket_commands(hass: HomeAssistant) -> None:
    if hass.data.get(_WS_REGISTERED_KEY):
        return
    websocket_api.async_register_command(hass, _ws_recent_events)
    hass.data[_WS_REGISTERED_KEY] = True


@websocket_api.websocket_command(
    {
        vol.Required("type"): _WS_TYPE_RECENT_EVENTS,
        vol.Required("entity_id"): str,
    }
)
@websocket_api.async_response
async def _ws_recent_events(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    entity_id = msg["entity_id"]
    reg = er.async_get(hass)
    reg_entry = reg.async_get(entity_id)
    if reg_entry is None or not reg_entry.unique_id.endswith("_network_scanner"):
        connection.send_error(
            msg["id"],
            "invalid_entity",
            f"Not a wrtsensor network scanner entity: {entity_id}",
        )
        return

    coordinator = hass.data.get(DOMAIN, {}).get(reg_entry.config_entry_id)
    if coordinator is None:
        connection.send_error(
            msg["id"],
            "entry_not_loaded",
            f"wrtsensor config entry not loaded for {entity_id}",
        )
        return

    connection.send_result(
        msg["id"],
        {
            "events": coordinator.get_recent_events(),
            "count": coordinator.get_event_count(),
            "buffer_size": coordinator.get_event_buffer_size(),
        },
    )
