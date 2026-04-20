"""wrtsensor — Home Assistant integration for OpenWrt network monitoring."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_DISCONNECT_THRESHOLD,
    CONF_SSH_PORT,
    DEFAULT_DISCONNECT_THRESHOLD,
    DEFAULT_SSH_PORT,
    DOMAIN,
    PLATFORMS,
    STATIC_PATH_URL,
    VERSION,
)
from .coordinator import WrtsensorCoordinator

_LOGGER = logging.getLogger(__name__)

_WWW_DIR = Path(__file__).parent / "www"
_CARD_FILES = [
    "network-list-card.js",
    "network-table-card.js",
    "network-topology-card.js",
    "network-events-card.js",
    "dns-stats-card.js",
]


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
    await hass.async_add_executor_job(_register_lovelace_resources, hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


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


def _register_lovelace_resources(hass: HomeAssistant) -> None:
    storage_path = Path(hass.config.config_dir) / ".storage" / "lovelace_resources"
    if not storage_path.exists():
        _LOGGER.debug(
            "lovelace_resources storage not found — skipping auto-registration"
        )
        return

    try:
        data = json.loads(storage_path.read_text())
    except (json.JSONDecodeError, OSError):
        _LOGGER.warning("Could not read lovelace_resources storage")
        return

    items: list[dict] = data.get("data", {}).get("items", [])
    existing_urls = {item.get("url", "") for item in items}
    changed = False

    for js_file in _CARD_FILES:
        url = f"{STATIC_PATH_URL}/{js_file}?v={VERSION}"
        if not any(js_file in u for u in existing_urls):
            items.append(
                {"id": js_file.replace(".js", ""), "type": "module", "url": url}
            )
            changed = True
            _LOGGER.info("Registered Lovelace resource: %s", url)

    if changed:
        data.setdefault("data", {})["items"] = items
        try:
            storage_path.write_text(json.dumps(data, indent=2))
        except OSError:
            _LOGGER.warning("Could not write lovelace_resources storage")
