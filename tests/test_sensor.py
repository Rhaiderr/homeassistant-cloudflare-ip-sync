"""Tests for the sync-status sensor."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cloudflare_ip_sync.api import (
    BULK_STATUS_COMPLETED,
    CloudflareBulkOperation,
    CloudflareListItem,
)

from .conftest import SOURCE_ENTITY

ENTITY_ID = "sensor.casa_sync_status"


async def test_sensor_in_sync(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The sensor reads 'in_sync' and exposes the raw IPs as attributes."""
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == "in_sync"
    assert state.attributes["local_ip"] == "1.2.3.4"
    assert state.attributes["cloudflare_ips"] == ["1.2.3.4"]
    assert state.attributes["last_error"] is None


async def test_sensor_out_of_sync_on_write_failure(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """When the list can't be reconciled, the sensor reports 'out_of_sync'."""
    hass.states.async_set(SOURCE_ENTITY, "1.2.3.4")
    # Read always returns the wrong IP; writes never converge.
    mock_client.async_get_list_items.return_value = [
        CloudflareListItem(ip="9.9.9.9")
    ]
    mock_client.async_replace_list_items.return_value = "op1"
    mock_client.async_get_bulk_operation.return_value = CloudflareBulkOperation(
        id="op1", status=BULK_STATUS_COMPLETED
    )

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == "out_of_sync"
    assert state.attributes["last_error"] is not None
