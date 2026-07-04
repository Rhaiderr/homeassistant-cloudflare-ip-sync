"""The Cloudflare Dynamic IP Sync integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CloudflareClient
from .const import CONF_API_TOKEN
from .coordinator import CloudflareIpSyncCoordinator

# Entity platforms are added in a later milestone.
PLATFORMS: list[Platform] = []

type CloudflareIpSyncConfigEntry = ConfigEntry[CloudflareIpSyncCoordinator]


async def async_setup_entry(
    hass: HomeAssistant, entry: CloudflareIpSyncConfigEntry
) -> bool:
    """Set up Cloudflare Dynamic IP Sync from a config entry."""
    client = CloudflareClient(
        async_get_clientsession(hass), entry.data[CONF_API_TOKEN]
    )
    coordinator = CloudflareIpSyncCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    coordinator.async_setup_listeners()

    if PLATFORMS:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: CloudflareIpSyncConfigEntry
) -> bool:
    """Unload a config entry and its platforms."""
    if PLATFORMS:
        return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: CloudflareIpSyncConfigEntry
) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
