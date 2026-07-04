"""Coordinator that observes the source IP and the Cloudflare Rule List.

This milestone is read-only: it reads the public IP from the source entity and
the current Rule List from Cloudflare, then reports whether they agree. The
write path (replacing the list, polling the async bulk operation, retrying with
backoff) is added in a later milestone.

The trigger model is hybrid:

* a state-change listener on the source entity requests an immediate (debounced)
  refresh whenever the IP changes, and
* the coordinator's ``update_interval`` reconciles periodically as a safety net
  against drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import ipaddress
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import (
    EventStateChangedData,
    async_track_state_change_event,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CloudflareAuthError, CloudflareClient, CloudflareError
from .const import (
    CONF_ACCOUNT_ID,
    CONF_LIST_ID,
    CONF_RECONCILE_INTERVAL,
    CONF_SOURCE_ENTITY_ID,
    DEFAULT_RECONCILE_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Seconds to collapse rapid source-entity changes into a single refresh.
SYNC_DEBOUNCE_SECONDS = 5.0

_UNAVAILABLE_STATES = (STATE_UNAVAILABLE, STATE_UNKNOWN, "")


@dataclass(frozen=True, slots=True)
class SyncState:
    """Snapshot of the local IP versus the Cloudflare Rule List."""

    local_ip: str | None
    cloudflare_ips: list[str]
    in_sync: bool


def _to_network(value: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network | None:
    """Parse an IP or CIDR string into a normalized network, or None."""
    try:
        return ipaddress.ip_network(value.strip(), strict=False)
    except ValueError:
        return None


class CloudflareIpSyncCoordinator(DataUpdateCoordinator[SyncState]):
    """Keep the source IP and the Cloudflare Rule List reconciled."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: CloudflareClient,
    ) -> None:
        """Set up the coordinator, its debouncer and update interval."""
        interval = entry.options.get(
            CONF_RECONCILE_INTERVAL, DEFAULT_RECONCILE_INTERVAL
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=interval),
            request_refresh_debouncer=Debouncer(
                hass,
                _LOGGER,
                cooldown=SYNC_DEBOUNCE_SECONDS,
                immediate=False,
            ),
        )
        self.client = client
        self._account_id: str = entry.data[CONF_ACCOUNT_ID]
        self._list_id: str = entry.data[CONF_LIST_ID]
        self._source_entity_id: str = entry.data[CONF_SOURCE_ENTITY_ID]

    @callback
    def async_setup_listeners(self) -> None:
        """Track the source entity so IP changes trigger a debounced refresh."""
        entry = self.config_entry
        assert entry is not None
        entry.async_on_unload(
            async_track_state_change_event(
                self.hass, [self._source_entity_id], self._handle_source_event
            )
        )

    @callback
    def _handle_source_event(self, event: Event[EventStateChangedData]) -> None:
        """Request a refresh when the source entity's state changes."""
        self.hass.async_create_task(self.async_request_refresh())

    async def _async_update_data(self) -> SyncState:
        """Read both sides and report whether they are in sync."""
        local_ip = self._read_source_ip()
        try:
            items = await self.client.async_get_list_items(
                self._account_id, self._list_id
            )
        except CloudflareAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except CloudflareError as err:
            raise UpdateFailed(str(err)) from err

        cloudflare_ips = [item.ip for item in items]
        in_sync = self._is_in_sync(local_ip, cloudflare_ips)
        _LOGGER.debug(
            "Sync check for %s: local=%s cloudflare=%s in_sync=%s",
            self._list_id,
            local_ip,
            cloudflare_ips,
            in_sync,
        )
        return SyncState(
            local_ip=local_ip,
            cloudflare_ips=cloudflare_ips,
            in_sync=in_sync,
        )

    def _read_source_ip(self) -> str | None:
        """Return the source entity's state if it is a valid IP address."""
        state = self.hass.states.get(self._source_entity_id)
        if state is None or state.state in _UNAVAILABLE_STATES:
            return None
        try:
            ipaddress.ip_address(state.state.strip())
        except ValueError:
            _LOGGER.warning(
                "Source entity %s state %r is not a valid IP address",
                self._source_entity_id,
                state.state,
            )
            return None
        return state.state.strip()

    @staticmethod
    def _is_in_sync(local_ip: str | None, cloudflare_ips: list[str]) -> bool:
        """Return whether the list holds exactly the local IP (own-the-list)."""
        if local_ip is None:
            return False
        desired = {_to_network(local_ip)}
        current = {_to_network(ip) for ip in cloudflare_ips}
        current.discard(None)
        return desired == current
