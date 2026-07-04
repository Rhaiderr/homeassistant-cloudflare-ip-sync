"""Tests for the reconcile/sync coordinator."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cloudflare_ip_sync.api import (
    BULK_STATUS_COMPLETED,
    BULK_STATUS_FAILED,
    CloudflareApiError,
    CloudflareAuthError,
    CloudflareBulkOperation,
    CloudflareListItem,
    CloudflareRuleList,
)
from custom_components.cloudflare_ip_sync.const import DOMAIN
from custom_components.cloudflare_ip_sync.coordinator import (
    ISSUE_RULE_LIST_MISSING,
    CloudflareIpSyncCoordinator,
)

from .conftest import SOURCE_ENTITY

ACCOUNT = "acc123"
LIST_ID = "list123"


def _items(*ips: str) -> list[CloudflareListItem]:
    """Build a list of Cloudflare list items from raw IPs."""
    return [CloudflareListItem(ip=ip) for ip in ips]


def _completed() -> CloudflareBulkOperation:
    """Return a completed bulk operation."""
    return CloudflareBulkOperation(id="op1", status=BULK_STATUS_COMPLETED)


async def _make_coordinator(
    hass: HomeAssistant, entry: MockConfigEntry, client: AsyncMock
) -> CloudflareIpSyncCoordinator:
    """Register the entry and build a coordinator against the mock client."""
    entry.add_to_hass(hass)
    return CloudflareIpSyncCoordinator(hass, entry, client)


async def test_in_sync_no_write(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """When the list already holds the local IP, nothing is written."""
    hass.states.async_set(SOURCE_ENTITY, "1.2.3.4")
    client = AsyncMock()
    client.async_get_list_items.return_value = _items("1.2.3.4")

    coordinator = await _make_coordinator(hass, mock_config_entry, client)
    state = await coordinator._async_update_data()

    assert state.in_sync is True
    assert state.last_synced is not None
    client.async_replace_list_items.assert_not_called()


async def test_out_of_sync_writes_and_verifies(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A mismatch triggers a replace, then re-read confirms the new IP."""
    hass.states.async_set(SOURCE_ENTITY, "1.2.3.4")
    client = AsyncMock()
    client.async_get_list_items.side_effect = [_items("9.9.9.9"), _items("1.2.3.4")]
    client.async_replace_list_items.return_value = "op1"
    client.async_get_bulk_operation.return_value = _completed()

    coordinator = await _make_coordinator(hass, mock_config_entry, client)
    state = await coordinator._async_update_data()

    assert state.in_sync is True
    assert state.local_ip == "1.2.3.4"
    client.async_replace_list_items.assert_called_once()
    written = client.async_replace_list_items.call_args.args[2]
    assert written[0].ip == "1.2.3.4"


async def test_no_local_ip_skips_write(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """An unavailable/invalid source IP reports out of sync without writing."""
    hass.states.async_set(SOURCE_ENTITY, "unavailable")
    client = AsyncMock()
    client.async_get_list_items.return_value = _items("9.9.9.9")

    coordinator = await _make_coordinator(hass, mock_config_entry, client)
    state = await coordinator._async_update_data()

    assert state.in_sync is False
    assert state.local_ip is None
    client.async_replace_list_items.assert_not_called()


async def test_sync_retries_then_gives_up(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Persistent verification failure exhausts retries and notifies."""
    hass.states.async_set(SOURCE_ENTITY, "1.2.3.4")
    client = AsyncMock()
    # Every read returns the wrong IP, so verification never converges.
    client.async_get_list_items.return_value = _items("9.9.9.9")
    client.async_replace_list_items.return_value = "op1"
    client.async_get_bulk_operation.return_value = _completed()

    coordinator = await _make_coordinator(hass, mock_config_entry, client)
    with patch(
        "custom_components.cloudflare_ip_sync.coordinator."
        "persistent_notification.async_create"
    ) as notify:
        state = await coordinator._async_update_data()

    assert state.in_sync is False
    assert state.last_error is not None
    # Default CONF_MAX_RETRIES is 5.
    assert client.async_replace_list_items.call_count == 5
    notify.assert_called_once()


async def test_failed_bulk_operation_is_retried(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A failed bulk operation counts as a failed attempt."""
    hass.states.async_set(SOURCE_ENTITY, "1.2.3.4")
    client = AsyncMock()
    client.async_get_list_items.return_value = _items("9.9.9.9")
    client.async_replace_list_items.return_value = "op1"
    client.async_get_bulk_operation.return_value = CloudflareBulkOperation(
        id="op1", status=BULK_STATUS_FAILED, error="nope"
    )

    coordinator = await _make_coordinator(hass, mock_config_entry, client)
    with patch(
        "custom_components.cloudflare_ip_sync.coordinator."
        "persistent_notification.async_create"
    ):
        state = await coordinator._async_update_data()

    assert state.in_sync is False
    assert client.async_replace_list_items.call_count == 5


async def test_auth_error_raises_config_entry_auth_failed(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """An auth error while reading triggers reauth, never a retry."""
    hass.states.async_set(SOURCE_ENTITY, "1.2.3.4")
    client = AsyncMock()
    client.async_get_list_items.side_effect = CloudflareAuthError("bad token")

    coordinator = await _make_coordinator(hass, mock_config_entry, client)
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_missing_rule_list_raises_repair_issue(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A non-auth read error plus a vanished list raises a Repair issue."""
    hass.states.async_set(SOURCE_ENTITY, "1.2.3.4")
    client = AsyncMock()
    client.async_get_list_items.side_effect = CloudflareApiError("gone", code=404)
    # The configured list is absent from the account's lists.
    client.async_get_rule_lists.return_value = [
        CloudflareRuleList(id="other", name="x", kind="ip", num_items=0)
    ]

    coordinator = await _make_coordinator(hass, mock_config_entry, client)
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    issue_reg = ir.async_get(hass)
    issue = issue_reg.async_get_issue(
        DOMAIN, f"{DOMAIN}_{mock_config_entry.entry_id}_{ISSUE_RULE_LIST_MISSING}"
    )
    assert issue is not None


async def test_present_rule_list_no_repair_issue(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A transient read error while the list still exists raises no issue."""
    hass.states.async_set(SOURCE_ENTITY, "1.2.3.4")
    client = AsyncMock()
    client.async_get_list_items.side_effect = CloudflareApiError("hiccup", code=500)
    client.async_get_rule_lists.return_value = [
        CloudflareRuleList(id=LIST_ID, name="casa", kind="ip", num_items=1)
    ]

    coordinator = await _make_coordinator(hass, mock_config_entry, client)
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    issue_reg = ir.async_get(hass)
    assert (
        issue_reg.async_get_issue(
            DOMAIN, f"{DOMAIN}_{mock_config_entry.entry_id}_{ISSUE_RULE_LIST_MISSING}"
        )
        is None
    )


async def test_last_synced_persists_across_out_of_sync(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """last_synced survives a later cycle that can't determine the IP."""
    hass.states.async_set(SOURCE_ENTITY, "1.2.3.4")
    client = AsyncMock()
    client.async_get_list_items.return_value = _items("1.2.3.4")

    coordinator = await _make_coordinator(hass, mock_config_entry, client)
    first = await coordinator._async_update_data()
    assert first.last_synced is not None

    # Source becomes unavailable; last_synced should carry over.
    hass.states.async_set(SOURCE_ENTITY, "unknown")
    second = await coordinator._async_update_data()
    assert second.in_sync is False
    assert second.last_synced == first.last_synced
