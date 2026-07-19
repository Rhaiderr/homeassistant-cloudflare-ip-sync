"""Coordinator that reconciles the source IP with the Cloudflare Rule List.

It reads the public IP from the source entity and the current Rule List from
Cloudflare. When they already agree, nothing else happens. When they don't, it
replaces the Rule List with the source IP, waits for the resulting Cloudflare
bulk operation, and re-reads the list to verify the write actually took.
Optionally the same IP is also reconciled into a DNS record of a zone
(typically an un-proxied hostname VPN clients use as their endpoint), with its
own retry/notification handling so DNS trouble never blocks the Rule List
sync. A
failed attempt (replace, bulk operation, or verification) is retried with
exponential backoff up to a configurable maximum; if every attempt is
exhausted, a persistent notification is raised and the failure is recorded on
the coordinator's data (sensors, diagnostics surface it from there).

A separate, structural failure mode -- the configured Rule List no longer
existing in the Cloudflare account -- is handled differently: it raises a
Repair issue instead, since retrying or notifying won't help; the user needs
to reconfigure the integration.

The trigger model is hybrid:

* a state-change listener on the source entity requests an immediate (debounced)
  refresh whenever the IP changes, and
* the coordinator's ``update_interval`` reconciles periodically as a safety net
  against drift.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import ipaddress
import logging

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import (
    EventStateChangedData,
    async_track_state_change_event,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    CloudflareApiError,
    CloudflareAuthError,
    CloudflareClient,
    CloudflareDnsRecord,
    CloudflareError,
    CloudflareListItem,
)
from .const import (
    BULK_OPERATION_POLL_INTERVAL,
    BULK_OPERATION_TIMEOUT,
    CONF_ACCOUNT_ID,
    CONF_DNS_RECORD_NAME,
    CONF_DNS_ZONE_ID,
    CONF_LIST_ID,
    CONF_MAX_RETRIES,
    CONF_RECONCILE_INTERVAL,
    CONF_SOURCE_ENTITY_ID,
    DEFAULT_MAX_RETRIES,
    DEFAULT_RECONCILE_INTERVAL,
    DNS_RECORD_COMMENT,
    DNS_RECORD_TTL,
    DOMAIN,
    LIST_ITEM_COMMENT,
    SYNC_INITIAL_BACKOFF,
    SYNC_MAX_BACKOFF,
)

_LOGGER = logging.getLogger(__name__)

# Seconds to collapse rapid source-entity changes into a single refresh.
SYNC_DEBOUNCE_SECONDS = 5.0

_UNAVAILABLE_STATES = (STATE_UNAVAILABLE, STATE_UNKNOWN, "")

# Repair issue raised when the configured Rule List can no longer be found in
# the Cloudflare account (as opposed to a transient read/write failure).
ISSUE_RULE_LIST_MISSING = "rule_list_missing"


class _SyncVerificationFailed(Exception):
    """A replace + bulk-operation + re-read cycle didn't converge."""


@dataclass(frozen=True, slots=True)
class SyncState:
    """Snapshot of the local IP versus the Cloudflare Rule List.

    The ``dns_*`` fields describe the optional DNS-record sync target; they
    stay ``None`` when that feature is not configured (and ``dns_in_sync`` is
    also ``None`` when there is no usable local IP to compare against).
    """

    local_ip: str | None
    cloudflare_ips: list[str]
    in_sync: bool
    last_synced: datetime | None = None
    last_error: str | None = None
    dns_record_name: str | None = None
    dns_record_ip: str | None = None
    dns_in_sync: bool | None = None
    dns_last_error: str | None = None


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
        self._max_retries: int = entry.options.get(
            CONF_MAX_RETRIES, DEFAULT_MAX_RETRIES
        )
        self._dns_record_name: str | None = entry.options.get(CONF_DNS_RECORD_NAME)
        self._dns_zone_id: str | None = entry.options.get(CONF_DNS_ZONE_ID)
        self._notification_id = f"{DOMAIN}_{entry.entry_id}_sync_failed"
        self._dns_notification_id = f"{DOMAIN}_{entry.entry_id}_dns_sync_failed"
        self._rule_list_missing_issue_id = (
            f"{DOMAIN}_{entry.entry_id}_{ISSUE_RULE_LIST_MISSING}"
        )
        self._last_synced: datetime | None = None

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
        """Reconcile the source IP with the Cloudflare Rule List.

        Reads both sides; if they already agree (or there is no usable local
        IP to sync), returns immediately. Otherwise attempts to write the
        Rule List, with retries, and reports the outcome either way -- a sync
        failure is surfaced via ``last_error`` rather than raising
        ``UpdateFailed``, since the Cloudflare list itself was still read
        successfully and entities should stay available to show "out of sync".
        ``last_synced`` tracks the most recent time the list was confirmed to
        match the source IP, persisting across calls that don't (re)sync.
        When the optional DNS record sync is configured it is reconciled
        afterwards, reported through the ``dns_*`` fields of the state.
        """
        local_ip = self._read_source_ip()
        try:
            items = await self.client.async_get_list_items(
                self._account_id, self._list_id
            )
        except CloudflareAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except CloudflareApiError as err:
            if await self._async_is_rule_list_missing():
                self._async_raise_rule_list_missing_issue()
            else:
                self._async_clear_rule_list_missing_issue()
            raise UpdateFailed(str(err)) from err
        except CloudflareError as err:
            raise UpdateFailed(str(err)) from err

        self._async_clear_rule_list_missing_issue()
        cloudflare_ips = [item.ip for item in items]
        in_sync = self._is_in_sync(local_ip, cloudflare_ips)
        last_error: str | None = None
        _LOGGER.debug(
            "Sync check for %s: local=%s cloudflare=%s in_sync=%s",
            self._list_id,
            local_ip,
            cloudflare_ips,
            in_sync,
        )
        if in_sync or local_ip is None:
            self._async_clear_sync_failure()
            if in_sync:
                self._last_synced = dt_util.utcnow()
        else:
            try:
                cloudflare_ips = await self._async_sync_with_retry(local_ip)
            except CloudflareAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except _SyncVerificationFailed as err:
                _LOGGER.error(
                    "Giving up syncing Rule List %s after %s attempts: %s",
                    self._list_id,
                    self._max_retries,
                    err,
                )
                self._async_notify_sync_failure(str(err))
                last_error = str(err)
            else:
                self._async_clear_sync_failure()
                self._last_synced = dt_util.utcnow()
                in_sync = True

        dns_record_ip, dns_in_sync, dns_last_error = await self._async_reconcile_dns(
            local_ip
        )
        return SyncState(
            local_ip=local_ip,
            cloudflare_ips=cloudflare_ips,
            in_sync=in_sync,
            last_synced=self._last_synced,
            last_error=last_error,
            dns_record_name=self._dns_record_name if self._dns_enabled else None,
            dns_record_ip=dns_record_ip,
            dns_in_sync=dns_in_sync,
            dns_last_error=dns_last_error,
        )

    async def _async_sync_with_retry(self, local_ip: str) -> list[str]:
        """Replace and verify the Rule List, retrying with backoff.

        Raises:
            CloudflareAuthError: immediately, since a bad token won't heal on
                retry -- the caller maps it to a reauth flow.
            _SyncVerificationFailed: once every attempt has been exhausted.
        """
        delay = SYNC_INITIAL_BACKOFF
        last_err: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                return await self._async_replace_and_verify(local_ip)
            except CloudflareAuthError:
                raise
            except (CloudflareError, _SyncVerificationFailed) as err:
                last_err = err
                _LOGGER.warning(
                    "Sync attempt %s/%s for Rule List %s failed: %s",
                    attempt,
                    self._max_retries,
                    self._list_id,
                    err,
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, SYNC_MAX_BACKOFF)
        raise _SyncVerificationFailed(
            f"Failed after {self._max_retries} attempts: {last_err}"
        ) from last_err

    async def _async_replace_and_verify(self, local_ip: str) -> list[str]:
        """Write the source IP to the Rule List and confirm it took effect."""
        operation_id = await self.client.async_replace_list_items(
            self._account_id,
            self._list_id,
            [CloudflareListItem(ip=local_ip, comment=LIST_ITEM_COMMENT)],
        )
        await self._async_wait_for_bulk_operation(operation_id)

        items = await self.client.async_get_list_items(self._account_id, self._list_id)
        cloudflare_ips = [item.ip for item in items]
        if not self._is_in_sync(local_ip, cloudflare_ips):
            raise _SyncVerificationFailed(
                "Rule List still did not match the source IP after the update"
            )
        return cloudflare_ips

    async def _async_wait_for_bulk_operation(self, operation_id: str) -> None:
        """Poll a Cloudflare bulk operation until it completes or fails."""
        try:
            async with asyncio.timeout(BULK_OPERATION_TIMEOUT):
                while True:
                    operation = await self.client.async_get_bulk_operation(
                        self._account_id, operation_id
                    )
                    if operation.is_complete:
                        return
                    if operation.is_failed:
                        raise _SyncVerificationFailed(
                            f"Cloudflare bulk operation failed: {operation.error}"
                        )
                    await asyncio.sleep(BULK_OPERATION_POLL_INTERVAL)
        except TimeoutError as err:
            raise _SyncVerificationFailed(
                "Timed out waiting for the Cloudflare bulk operation"
            ) from err

    @property
    def _dns_enabled(self) -> bool:
        """Return whether the optional DNS-record sync is configured."""
        return bool(self._dns_record_name and self._dns_zone_id)

    async def _async_reconcile_dns(
        self, local_ip: str | None
    ) -> tuple[str | None, bool | None, str | None]:
        """Reconcile the optional DNS record, returning (ip, in_sync, error).

        Failures never abort the whole update: the Rule List sync (the
        security-critical half) already ran, so DNS problems are surfaced via
        the returned error string plus a persistent notification. An auth
        failure here most likely means the token is missing the zone's DNS
        edit permission, which a reauth flow would not fix, so it is reported
        the same way rather than raising ``ConfigEntryAuthFailed``.
        """
        if not self._dns_enabled or local_ip is None:
            return (None, None, None)
        try:
            record = await self._async_sync_dns_with_retry(local_ip)
        except CloudflareAuthError as err:
            error = (
                "Cloudflare rejected the DNS update; check that the API token "
                f"has permission to edit DNS records in the record's zone: {err}"
            )
        except (CloudflareError, _SyncVerificationFailed) as err:
            error = str(err)
        else:
            self._async_clear_dns_failure()
            return (record.content, True, None)
        _LOGGER.error(
            "Giving up syncing DNS record %s: %s", self._dns_record_name, error
        )
        self._async_notify_dns_failure(error)
        return (None, False, error)

    async def _async_sync_dns_with_retry(self, local_ip: str) -> CloudflareDnsRecord:
        """Upsert and verify the DNS record, retrying with backoff.

        Mirrors :meth:`_async_sync_with_retry`: auth errors propagate
        immediately, anything else is retried until the attempts run out.
        """
        delay = SYNC_INITIAL_BACKOFF
        last_err: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                return await self._async_upsert_dns_record(local_ip)
            except CloudflareAuthError:
                raise
            except (CloudflareError, _SyncVerificationFailed) as err:
                last_err = err
                _LOGGER.warning(
                    "DNS sync attempt %s/%s for %s failed: %s",
                    attempt,
                    self._max_retries,
                    self._dns_record_name,
                    err,
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, SYNC_MAX_BACKOFF)
        raise _SyncVerificationFailed(
            f"Failed after {self._max_retries} attempts: {last_err}"
        ) from last_err

    async def _async_upsert_dns_record(self, local_ip: str) -> CloudflareDnsRecord:
        """Create or update the DNS record so it holds exactly ``local_ip``.

        The record is always written un-proxied: it exists so VPN clients can
        reach the home IP directly, and a proxied record would hand them a
        Cloudflare edge address instead.
        """
        assert self._dns_record_name and self._dns_zone_id
        record_type = "AAAA" if ":" in local_ip else "A"
        records = await self.client.async_get_dns_records(
            self._dns_zone_id, name=self._dns_record_name, record_type=record_type
        )
        if not records:
            record = await self.client.async_create_dns_record(
                self._dns_zone_id,
                name=self._dns_record_name,
                record_type=record_type,
                content=local_ip,
                ttl=DNS_RECORD_TTL,
                proxied=False,
                comment=DNS_RECORD_COMMENT,
            )
        else:
            if len(records) > 1:
                _LOGGER.warning(
                    "Multiple %s records exist for %s; updating the first",
                    record_type,
                    self._dns_record_name,
                )
            record = records[0]
            if (
                record.content != local_ip
                or record.proxied
                or record.ttl != DNS_RECORD_TTL
            ):
                record = await self.client.async_update_dns_record(
                    self._dns_zone_id,
                    record.id,
                    content=local_ip,
                    ttl=DNS_RECORD_TTL,
                    proxied=False,
                    comment=record.comment or DNS_RECORD_COMMENT,
                )
        if record.content != local_ip or record.proxied:
            raise _SyncVerificationFailed(
                "DNS record still did not match the source IP after the update"
            )
        return record

    def _async_notify_dns_failure(self, error: str) -> None:
        """Raise a persistent notification about the DNS record sync failing."""
        persistent_notification.async_create(
            self.hass,
            (
                f"Could not update the DNS record {self._dns_record_name} "
                f"with the current IP.\n\nLast error: {error}"
            ),
            title="Cloudflare IP Sync failed",
            notification_id=self._dns_notification_id,
        )

    def _async_clear_dns_failure(self) -> None:
        """Dismiss any previously raised DNS-failure notification."""
        persistent_notification.async_dismiss(self.hass, self._dns_notification_id)

    def _async_notify_sync_failure(self, error: str) -> None:
        """Raise a persistent notification describing the exhausted retries."""
        persistent_notification.async_create(
            self.hass,
            (
                f"Could not update the Cloudflare Rule List ({self._list_id}) "
                f"with the current IP after {self._max_retries} attempts.\n\n"
                f"Last error: {error}"
            ),
            title="Cloudflare IP Sync failed",
            notification_id=self._notification_id,
        )

    def _async_clear_sync_failure(self) -> None:
        """Dismiss any previously raised sync-failure notification."""
        persistent_notification.async_dismiss(self.hass, self._notification_id)

    async def _async_is_rule_list_missing(self) -> bool:
        """Check whether the configured Rule List still exists in the account.

        Used to tell a structural problem (the list was deleted, or the token
        lost access to it) apart from a transient read failure -- only the
        former is worth raising a Repair issue over. If this check itself
        fails, we can't confirm anything, so we conservatively assume the
        list is still there and let the ordinary UpdateFailed handle it.
        """
        try:
            rule_lists = await self.client.async_get_rule_lists(self._account_id)
        except CloudflareError:
            return False
        return not any(rule.id == self._list_id for rule in rule_lists)

    def _async_raise_rule_list_missing_issue(self) -> None:
        """Raise a Repair issue pointing the user at reconfiguring the entry."""
        entry = self.config_entry
        assert entry is not None
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._rule_list_missing_issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_RULE_LIST_MISSING,
            translation_placeholders={"list_name": entry.title},
        )

    def _async_clear_rule_list_missing_issue(self) -> None:
        """Dismiss the Rule-List-missing Repair issue if one was raised."""
        ir.async_delete_issue(self.hass, DOMAIN, self._rule_list_missing_issue_id)

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
