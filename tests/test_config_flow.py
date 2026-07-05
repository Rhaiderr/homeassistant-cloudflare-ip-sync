"""Tests for the config, options and reauth flows."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cloudflare_ip_sync.api import (
    CloudflareAccount,
    CloudflareApiError,
    CloudflareAuthError,
    CloudflareRuleList,
    CloudflareTokenStatus,
)
from custom_components.cloudflare_ip_sync.const import (
    CONF_ACCOUNT_ID,
    CONF_API_TOKEN,
    CONF_LIST_ID,
    CONF_MAX_RETRIES,
    CONF_RECONCILE_INTERVAL,
    CONF_SOURCE_ENTITY_ID,
    DOMAIN,
)

from .conftest import SOURCE_ENTITY


def _configure_happy_client(client: AsyncMock) -> None:
    """Wire a mock client that walks the flow to completion."""
    client.async_verify_token.return_value = CloudflareTokenStatus(
        id="t", status="active"
    )
    client.async_get_accounts.return_value = [
        CloudflareAccount(id="acc123", name="My Account")
    ]
    client.async_get_rule_lists.return_value = [
        CloudflareRuleList(id="list123", name="casa", kind="ip", num_items=1)
    ]


async def test_full_user_flow(
    hass: HomeAssistant, mock_client: AsyncMock
) -> None:
    """The four-step flow creates an entry with the collected data."""
    _configure_happy_client(mock_client)
    hass.states.async_set(SOURCE_ENTITY, "1.2.3.4")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "secret-token"}
    )
    assert result["step_id"] == "account"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCOUNT_ID: "acc123"}
    )
    assert result["step_id"] == "rule_list"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LIST_ID: "list123"}
    )
    assert result["step_id"] == "entity"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SOURCE_ENTITY_ID: SOURCE_ENTITY}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "casa"
    assert result["data"][CONF_API_TOKEN] == "secret-token"
    assert result["data"][CONF_LIST_ID] == "list123"
    assert result["data"][CONF_SOURCE_ENTITY_ID] == SOURCE_ENTITY


async def test_invalid_token_shows_error(
    hass: HomeAssistant, mock_client: AsyncMock
) -> None:
    """A rejected token re-shows the first step with invalid_auth."""
    mock_client.async_verify_token.side_effect = CloudflareAuthError("nope")
    mock_client.async_get_accounts.side_effect = CloudflareAuthError("nope")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "bad"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_account_owned_token_validates_via_accounts(
    hass: HomeAssistant, mock_client: AsyncMock
) -> None:
    """Account-owned tokens (cfat_) fail /user/tokens/verify but still work.

    Cloudflare rejects account-owned tokens on the user-scoped verify
    endpoint; the flow must fall back to listing accounts and proceed.
    """
    _configure_happy_client(mock_client)
    mock_client.async_verify_token.side_effect = CloudflareApiError(
        "Invalid API Token", code=400
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "cfat_account_owned"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "account"


async def test_duplicate_rule_list_aborts(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Selecting an already-configured account+list aborts the flow."""
    mock_config_entry.add_to_hass(hass)
    _configure_happy_client(mock_client)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "secret-token"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCOUNT_ID: "acc123"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LIST_ID: "list123"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The options flow stores retry and interval tuning."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        mock_config_entry.entry_id
    )
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_MAX_RETRIES: 3, CONF_RECONCILE_INTERVAL: 15},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_MAX_RETRIES: 3, CONF_RECONCILE_INTERVAL: 15}


async def test_reauth_flow_updates_token(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Reauth validates a new token and updates the entry."""
    mock_config_entry.add_to_hass(hass)
    mock_client.async_verify_token.return_value = CloudflareTokenStatus(
        id="t", status="active"
    )

    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "fresh-token"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_API_TOKEN] == "fresh-token"
