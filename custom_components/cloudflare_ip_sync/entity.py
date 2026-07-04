"""Shared base entity for the integration.

Groups every entity a config entry creates under a single device representing
the Cloudflare Rule List being synced, so future platforms (buttons, more
sensors) share the same device page instead of each inventing their own.
"""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CloudflareIpSyncCoordinator


class CloudflareIpSyncEntity(CoordinatorEntity[CloudflareIpSyncCoordinator]):
    """Base entity tying every platform entity to the same Rule List device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: CloudflareIpSyncCoordinator) -> None:
        """Attach the device info shared by every entity of this config entry."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        assert entry is not None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Cloudflare",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://dash.cloudflare.com/",
        )
