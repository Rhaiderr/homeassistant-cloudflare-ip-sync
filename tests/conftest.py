"""Shared fixtures for the Cloudflare Dynamic IP Sync test suite."""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cloudflare_ip_sync.api import CloudflareListItem
from custom_components.cloudflare_ip_sync.const import (
    CONF_ACCOUNT_ID,
    CONF_ACCOUNT_NAME,
    CONF_API_TOKEN,
    CONF_LIST_ID,
    CONF_LIST_NAME,
    CONF_SOURCE_ENTITY_ID,
    DOMAIN,
)

SOURCE_ENTITY = "sensor.public_ip"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> None:
    """Enable loading of this custom integration in every test."""


@pytest.fixture(autouse=True)
def no_backoff_sleep() -> Generator[None]:
    """Skip the real exponential-backoff sleeps so sync failures test fast."""
    with patch(
        "custom_components.cloudflare_ip_sync.coordinator.asyncio.sleep",
        AsyncMock(),
    ):
        yield


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a fully-configured config entry for the integration."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="casa",
        unique_id="acc123:list123",
        data={
            CONF_API_TOKEN: "secret-token",
            CONF_ACCOUNT_ID: "acc123",
            CONF_ACCOUNT_NAME: "My Account",
            CONF_LIST_ID: "list123",
            CONF_LIST_NAME: "casa",
            CONF_SOURCE_ENTITY_ID: SOURCE_ENTITY,
        },
    )


@pytest.fixture
def mock_client() -> Generator[AsyncMock]:
    """Patch CloudflareClient everywhere the integration constructs one.

    Both __init__ and config_flow build clients; patching the class in both
    modules yields the same AsyncMock instance so tests can drive it.
    """
    client = AsyncMock()
    with (
        patch(
            "custom_components.cloudflare_ip_sync.CloudflareClient",
            return_value=client,
        ),
        patch(
            "custom_components.cloudflare_ip_sync.config_flow.CloudflareClient",
            return_value=client,
        ),
    ):
        yield client


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> AsyncIterator[MockConfigEntry]:
    """Set up a loaded config entry whose list already matches the source IP."""
    hass.states.async_set(SOURCE_ENTITY, "1.2.3.4")
    mock_client.async_get_list_items.return_value = [
        CloudflareListItem(ip="1.2.3.4")
    ]
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    yield mock_config_entry
