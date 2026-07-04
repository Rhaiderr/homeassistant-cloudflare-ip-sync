"""Tests for setup/unload of the integration."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cloudflare_ip_sync.api import CloudflareListItem

from .conftest import SOURCE_ENTITY


async def test_setup_and_unload_entry(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A config entry sets up its coordinator and sensor, then unloads."""
    hass.states.async_set(SOURCE_ENTITY, "1.2.3.4")
    mock_client.async_get_list_items.return_value = [
        CloudflareListItem(ip="1.2.3.4")
    ]
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert mock_config_entry.runtime_data is not None
    assert hass.states.get("sensor.casa_sync_status") is not None

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
