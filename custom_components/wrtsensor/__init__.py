"""wrtsensor — Home Assistant integration for OpenWrt network monitoring."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_DISCONNECT_THRESHOLD,
    CONF_SSH_PORT,
    DEFAULT_DISCONNECT_THRESHOLD,
    DEFAULT_SSH_PORT,
    DOMAIN,
    PLATFORMS,
    STATIC_PATH_URL,
)
from .coordinator import WrtsensorCoordinator

_LOGGER = logging.getLogger(__name__)

_WWW_DIR = Path(__file__).parent / "www"


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.version < 2:
        new_data = {**entry.data}
        new_data.setdefault(CONF_SSH_PORT, DEFAULT_SSH_PORT)
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

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _prune_orphaned_host_entities(hass, entry, coordinator)

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
    if not host_stats:
        # Don't prune blindly if the scan returned no host data (partial or
        # cold start) — better to leave unavailable entities than nuke
        # everything.
        return
    live = set(host_stats.keys())
    reg = er.async_get(hass)
    prefix = f"{entry.entry_id}_host_"
    for reg_entry in list(er.async_entries_for_config_entry(reg, entry.entry_id)):
        if not reg_entry.unique_id.startswith(prefix):
            continue
        rest = reg_entry.unique_id[len(prefix) :]
        hostname = None
        for suffix in ("_cpu", "_ram", "_disk"):
            if rest.endswith(suffix):
                hostname = rest[: -len(suffix)]
                break
        if hostname is None or hostname in live:
            continue
        _LOGGER.info(
            "Pruning orphaned wrtsensor entity %s (hostname %s no longer scanned)",
            reg_entry.entity_id,
            hostname,
        )
        reg.async_remove(reg_entry.entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
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
