"""Async Cloudflare API client, isolated from Home Assistant internals.

The client wraps the subset of the Cloudflare API v4 needed to synchronize a
public IP address into a Rule List:

* validating an API token,
* discovering accounts and Rule Lists,
* reading and replacing Rule List items,
* polling the asynchronous bulk operation that item writes trigger.

It deliberately imports nothing from ``homeassistant`` so it can be unit tested
and reused by future Cloudflare modules (DNS, Tunnel, Zero Trust, ...). The
caller injects an :class:`aiohttp.ClientSession`; inside Home Assistant that is
``homeassistant.helpers.aiohttp_client.async_get_clientsession(hass)``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any, Final

import aiohttp

from .const import API_BASE_URL, DEFAULT_REQUEST_TIMEOUT

_LOGGER = logging.getLogger(__name__)

# Cloudflare bulk operation statuses.
BULK_STATUS_PENDING: Final = "pending"
BULK_STATUS_RUNNING: Final = "running"
BULK_STATUS_COMPLETED: Final = "completed"
BULK_STATUS_FAILED: Final = "failed"


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #
class CloudflareError(Exception):
    """Base error for every Cloudflare client failure."""


class CloudflareConnectionError(CloudflareError):
    """Network problem: timeout, DNS failure, connection reset, ..."""


class CloudflareAuthError(CloudflareError):
    """The token was rejected (HTTP 401) or lacks permissions (HTTP 403)."""


class CloudflareRateLimitError(CloudflareError):
    """Cloudflare returned HTTP 429; the caller should back off and retry."""


class CloudflareApiError(CloudflareError):
    """Cloudflare answered with a structured error (non-success envelope)."""

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        """Store the primary message plus the raw Cloudflare error list."""
        super().__init__(message)
        self.code = code
        self.errors = errors or []


class CloudflareResultError(CloudflareError):
    """The response was well-formed but missing data the client relied on."""


# --------------------------------------------------------------------------- #
# Data models (parsed, typed views of the raw JSON)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class CloudflareTokenStatus:
    """Result of verifying an API token."""

    id: str
    status: str

    @property
    def is_active(self) -> bool:
        """Return whether the token is currently usable."""
        return self.status == "active"


@dataclass(frozen=True, slots=True)
class CloudflareAccount:
    """A Cloudflare account the token can access."""

    id: str
    name: str


@dataclass(frozen=True, slots=True)
class CloudflareRuleList:
    """A Cloudflare Rule List (the sync target)."""

    id: str
    name: str
    kind: str
    num_items: int
    description: str | None = None


@dataclass(frozen=True, slots=True)
class CloudflareListItem:
    """A single entry inside a Rule List of kind ``ip``."""

    ip: str
    id: str | None = None
    comment: str | None = None


@dataclass(frozen=True, slots=True)
class CloudflareBulkOperation:
    """Status of an asynchronous Rule List item write."""

    id: str
    status: str
    error: str | None = None

    @property
    def is_complete(self) -> bool:
        """Return whether the operation finished successfully."""
        return self.status == BULK_STATUS_COMPLETED

    @property
    def is_failed(self) -> bool:
        """Return whether the operation terminated in failure."""
        return self.status == BULK_STATUS_FAILED

    @property
    def is_pending(self) -> bool:
        """Return whether the operation is still in progress."""
        return self.status in (BULK_STATUS_PENDING, BULK_STATUS_RUNNING)


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class CloudflareClient:
    """Thin async wrapper around the Cloudflare API v4."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token: str,
        *,
        base_url: str = API_BASE_URL,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        """Store the injected session and credentials.

        The token is kept only in memory and is sent exclusively in the
        ``Authorization`` header; it is never logged.
        """
        self._session = session
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._request_timeout = request_timeout

    # -- public API -------------------------------------------------------- #
    async def async_verify_token(self) -> CloudflareTokenStatus:
        """Validate the API token via ``GET /user/tokens/verify``.

        Raises:
            CloudflareAuthError: if the token is invalid or inactive.
        """
        payload = await self._request("GET", "/user/tokens/verify")
        result = self._require_result(payload)
        status = CloudflareTokenStatus(
            id=str(result.get("id", "")),
            status=str(result.get("status", "")),
        )
        if not status.is_active:
            raise CloudflareAuthError(f"Token is not active (status: {status.status})")
        return status

    async def async_get_accounts(self) -> list[CloudflareAccount]:
        """Return every account the token can access."""
        payload = await self._request("GET", "/accounts", params={"per_page": "50"})
        result = self._require_list(payload)
        return [
            CloudflareAccount(id=str(item["id"]), name=str(item.get("name", "")))
            for item in result
            if item.get("id")
        ]

    async def async_get_rule_lists(self, account_id: str) -> list[CloudflareRuleList]:
        """Return all Rule Lists for ``account_id``."""
        payload = await self._request(
            "GET", f"/accounts/{account_id}/rules/lists"
        )
        result = self._require_list(payload)
        return [
            CloudflareRuleList(
                id=str(item["id"]),
                name=str(item.get("name", "")),
                kind=str(item.get("kind", "")),
                num_items=int(item.get("num_items", 0)),
                description=item.get("description"),
            )
            for item in result
            if item.get("id")
        ]

    async def async_get_list_items(
        self, account_id: str, list_id: str
    ) -> list[CloudflareListItem]:
        """Return every item in the Rule List, following cursor pagination."""
        items: list[CloudflareListItem] = []
        params: dict[str, str] = {"per_page": "500"}
        while True:
            payload = await self._request(
                "GET",
                f"/accounts/{account_id}/rules/lists/{list_id}/items",
                params=params,
            )
            for raw in self._require_list(payload):
                ip = raw.get("ip")
                if ip is None:
                    continue
                items.append(
                    CloudflareListItem(
                        ip=str(ip),
                        id=raw.get("id"),
                        comment=raw.get("comment"),
                    )
                )
            cursor = self._next_cursor(payload)
            if cursor is None:
                return items
            params = {"per_page": "500", "cursor": cursor}

    async def async_replace_list_items(
        self, account_id: str, list_id: str, items: list[CloudflareListItem]
    ) -> str:
        """Replace all items in the list, returning the bulk ``operation_id``.

        This is an asynchronous Cloudflare operation: the returned id must be
        polled with :meth:`async_get_bulk_operation` until it completes.
        """
        body = [
            {"ip": item.ip, **({"comment": item.comment} if item.comment else {})}
            for item in items
        ]
        payload = await self._request(
            "PUT",
            f"/accounts/{account_id}/rules/lists/{list_id}/items",
            json_data=body,
        )
        result = self._require_result(payload)
        operation_id = result.get("operation_id")
        if not operation_id:
            raise CloudflareResultError("Cloudflare did not return an operation_id")
        return str(operation_id)

    async def async_get_bulk_operation(
        self, account_id: str, operation_id: str
    ) -> CloudflareBulkOperation:
        """Return the current status of a bulk operation."""
        payload = await self._request(
            "GET",
            f"/accounts/{account_id}/rules/lists/bulk_operations/{operation_id}",
        )
        result = self._require_result(payload)
        return CloudflareBulkOperation(
            id=str(result.get("id", operation_id)),
            status=str(result.get("status", "")),
            error=result.get("error"),
        )

    # -- internals --------------------------------------------------------- #
    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_data: Any | None = None,
    ) -> dict[str, Any]:
        """Perform a request and return the parsed Cloudflare envelope.

        Maps transport and HTTP errors onto the ``CloudflareError`` hierarchy
        and validates the ``success`` flag of the response envelope.
        """
        url = f"{self._base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        _LOGGER.debug("Cloudflare request: %s %s", method, path)
        try:
            async with asyncio.timeout(self._request_timeout):
                async with self._session.request(
                    method, url, headers=headers, params=params, json=json_data
                ) as response:
                    payload = await self._parse_json(response)
                    self._raise_for_status(response.status, payload)
                    return payload
        except TimeoutError as err:
            raise CloudflareConnectionError(
                f"Timeout talking to Cloudflare ({method} {path})"
            ) from err
        except aiohttp.ClientError as err:
            raise CloudflareConnectionError(
                f"Connection error talking to Cloudflare ({method} {path}): {err}"
            ) from err

    @staticmethod
    async def _parse_json(response: aiohttp.ClientResponse) -> dict[str, Any]:
        """Decode a Cloudflare response body, tolerating non-JSON errors."""
        try:
            data = await response.json(content_type=None)
        except (aiohttp.ContentTypeError, ValueError) as err:
            raise CloudflareApiError(
                f"Invalid response from Cloudflare (HTTP {response.status})",
                code=response.status,
            ) from err
        if not isinstance(data, dict):
            raise CloudflareApiError(
                f"Unexpected response from Cloudflare (HTTP {response.status})",
                code=response.status,
            )
        return data

    @staticmethod
    def _raise_for_status(status: int, payload: dict[str, Any]) -> None:
        """Translate HTTP status and the ``success`` flag into exceptions."""
        errors = payload.get("errors") or []
        message = CloudflareClient._first_error_message(errors)
        if status in (401, 403):
            raise CloudflareAuthError(message or f"Unauthorized (HTTP {status})")
        if status == 429:
            raise CloudflareRateLimitError(message or "Cloudflare rate limit reached")
        if status >= 400 or not payload.get("success", False):
            raise CloudflareApiError(
                message or f"Cloudflare API error (HTTP {status})",
                code=status,
                errors=errors,
            )

    @staticmethod
    def _first_error_message(errors: list[dict[str, Any]]) -> str | None:
        """Extract the first human-readable message from an error list."""
        for error in errors:
            if message := error.get("message"):
                return str(message)
        return None

    @staticmethod
    def _require_result(payload: dict[str, Any]) -> dict[str, Any]:
        """Return the ``result`` object, or raise if it is missing/typed wrong."""
        result = payload.get("result")
        if not isinstance(result, dict):
            raise CloudflareResultError("Cloudflare response is missing 'result'")
        return result

    @staticmethod
    def _require_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Return the ``result`` list, or raise if it is missing/typed wrong."""
        result = payload.get("result")
        if not isinstance(result, list):
            raise CloudflareResultError(
                "Cloudflare response is missing a 'result' list"
            )
        return result

    @staticmethod
    def _next_cursor(payload: dict[str, Any]) -> str | None:
        """Return the pagination cursor for the next page, if any."""
        result_info = payload.get("result_info")
        if not isinstance(result_info, dict):
            return None
        cursors = result_info.get("cursors")
        if not isinstance(cursors, dict):
            return None
        after = cursors.get("after")
        return str(after) if after else None
