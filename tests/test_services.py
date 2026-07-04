"""Tests for the force_sync and reload services."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cloudflare_ip_sync.const import DOMAIN
from custom_components.cloudflare_ip_sync.services import (
    ATTR_CONFIG_ENTRY_ID,
    SERVICE_FORCE_SYNC,
    SERVICE_RELOAD,
)


async def test_services_registered(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Both services are registered once the integration is set up."""
    assert hass.services.has_service(DOMAIN, SERVICE_FORCE_SYNC)
    assert hass.services.has_service(DOMAIN, SERVICE_RELOAD)


async def test_force_sync_refreshes_coordinator(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """force_sync requests a coordinator refresh for the targeted entry."""
    coordinator = init_integration.runtime_data
    with patch.object(coordinator, "async_request_refresh") as refresh:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_FORCE_SYNC,
            {ATTR_CONFIG_ENTRY_ID: init_integration.entry_id},
            blocking=True,
        )
    refresh.assert_called_once()


async def test_reload_reloads_entry(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """reload delegates to config_entries.async_reload for the entry."""
    with patch.object(
        hass.config_entries, "async_reload", return_value=True
    ) as reload:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RELOAD,
            {ATTR_CONFIG_ENTRY_ID: init_integration.entry_id},
            blocking=True,
        )
    reload.assert_called_once_with(init_integration.entry_id)


async def test_force_sync_unknown_entry_raises(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Targeting an unknown config entry raises a validation error."""
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_FORCE_SYNC,
            {ATTR_CONFIG_ENTRY_ID: "does-not-exist"},
            blocking=True,
        )
