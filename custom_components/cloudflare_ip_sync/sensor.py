"""Sensor entities exposing synchronization state."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import CloudflareIpSyncConfigEntry
from .coordinator import CloudflareIpSyncCoordinator
from .entity import CloudflareIpSyncEntity

SYNC_STATUS_IN_SYNC = "in_sync"
SYNC_STATUS_OUT_OF_SYNC = "out_of_sync"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CloudflareIpSyncConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sync-status sensor for this config entry."""
    async_add_entities([CloudflareSyncStatusSensor(entry.runtime_data)])


class CloudflareSyncStatusSensor(CloudflareIpSyncEntity, SensorEntity):
    """Reports whether the Cloudflare Rule List currently matches the source IP."""

    _attr_translation_key = "sync_status"
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, coordinator: CloudflareIpSyncCoordinator) -> None:
        """Derive this sensor's unique id from the owning config entry."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        assert entry is not None
        self._attr_unique_id = f"{entry.entry_id}_sync_status"
        self._attr_options = [SYNC_STATUS_IN_SYNC, SYNC_STATUS_OUT_OF_SYNC]

    @property
    def native_value(self) -> str | None:
        """Return the current sync status, or None before the first refresh."""
        data = self.coordinator.data
        if data is None:
            return None
        return SYNC_STATUS_IN_SYNC if data.in_sync else SYNC_STATUS_OUT_OF_SYNC

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the raw local/Cloudflare IPs and last error for troubleshooting."""
        data = self.coordinator.data
        if data is None:
            return {}
        return {
            "local_ip": data.local_ip,
            "cloudflare_ips": data.cloudflare_ips,
            "last_synced": data.last_synced,
            "last_error": data.last_error,
        }
