"""Integration services (force_sync, reload)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import selector
import voluptuous as vol

from .const import DOMAIN

SERVICE_FORCE_SYNC = "force_sync"
SERVICE_RELOAD = "reload"

ATTR_CONFIG_ENTRY_ID = "config_entry_id"

_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): selector.ConfigEntrySelector(
            {"integration": DOMAIN}
        ),
    }
)


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's services.

    Called once from async_setup, not per config entry, since the services
    are shared across every configured Cloudflare IP Sync instance and
    target one via config_entry_id.
    """

    async def _async_force_sync(call: ServiceCall) -> None:
        """Immediately reconcile the targeted entry's Rule List."""
        entry = _async_get_loaded_entry(hass, call.data[ATTR_CONFIG_ENTRY_ID])
        await entry.runtime_data.async_request_refresh()

    async def _async_reload(call: ServiceCall) -> None:
        """Reload the targeted config entry."""
        entry = _async_get_loaded_entry(hass, call.data[ATTR_CONFIG_ENTRY_ID])
        await hass.config_entries.async_reload(entry.entry_id)

    hass.services.async_register(
        DOMAIN, SERVICE_FORCE_SYNC, _async_force_sync, schema=_SERVICE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RELOAD, _async_reload, schema=_SERVICE_SCHEMA
    )


def _async_get_loaded_entry(hass: HomeAssistant, entry_id: str) -> ConfigEntry:
    """Return the loaded Cloudflare IP Sync config entry for entry_id."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            f"'{entry_id}' is not a Cloudflare IP Sync config entry"
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            f"Cloudflare IP Sync entry '{entry.title}' is not currently loaded"
        )
    return entry
