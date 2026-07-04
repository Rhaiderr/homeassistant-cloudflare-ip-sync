"""Tests for config-entry diagnostics."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cloudflare_ip_sync.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def test_diagnostics_redacts_token(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The API token is redacted while sync state is reported."""
    diag = await async_get_config_entry_diagnostics(hass, init_integration)

    assert diag["entry_data"]["api_token"] == "**REDACTED**"
    assert diag["entry_data"]["list_id"] == "list123"
    assert diag["sync_state"]["in_sync"] is True
    assert diag["sync_state"]["local_ip"] == "1.2.3.4"
    assert diag["sync_state"]["last_synced"] is not None
    assert diag["integration_version"] is not None


async def test_diagnostics_token_not_leaked_anywhere(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The raw token never appears anywhere in the diagnostics payload."""
    diag = await async_get_config_entry_diagnostics(hass, init_integration)
    assert "secret-token" not in str(diag)
