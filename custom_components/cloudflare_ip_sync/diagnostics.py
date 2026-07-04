"""Diagnostics support with token redaction."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from . import CloudflareIpSyncConfigEntry
from .const import CONF_API_TOKEN, DOMAIN

TO_REDACT = {CONF_API_TOKEN}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CloudflareIpSyncConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry, with the API token redacted."""
    coordinator = entry.runtime_data
    data = coordinator.data
    integration = await async_get_integration(hass, DOMAIN)

    return {
        "integration_version": integration.version,
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "entry_options": dict(entry.options),
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval_minutes": (
                coordinator.update_interval.total_seconds() / 60
                if coordinator.update_interval
                else None
            ),
        },
        "sync_state": {
            "local_ip": data.local_ip if data else None,
            "cloudflare_ips": data.cloudflare_ips if data else [],
            "in_sync": data.in_sync if data else None,
            "last_synced": (
                data.last_synced.isoformat() if data and data.last_synced else None
            ),
            "last_error": data.last_error if data else None,
        },
    }
