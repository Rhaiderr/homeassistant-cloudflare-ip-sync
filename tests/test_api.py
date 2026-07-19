"""Tests for the HA-free Cloudflare API client."""

from __future__ import annotations

from collections.abc import AsyncIterator

import aiohttp
from aioresponses import aioresponses
import pytest

from custom_components.cloudflare_ip_sync.api import (
    BULK_STATUS_COMPLETED,
    BULK_STATUS_FAILED,
    BULK_STATUS_RUNNING,
    CloudflareApiError,
    CloudflareAuthError,
    CloudflareClient,
    CloudflareConnectionError,
    CloudflareListItem,
    CloudflareRateLimitError,
    CloudflareResultError,
)

BASE = "https://api.cloudflare.com/client/v4"
ACCOUNT = "acc123"
LIST_ID = "list123"
ZONE = "zone123"


def _ok(result: object, **extra: object) -> dict[str, object]:
    """Wrap a result in a successful Cloudflare envelope."""
    return {"success": True, "errors": [], "result": result, **extra}


@pytest.fixture
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    """Provide a real aiohttp session for aioresponses to intercept."""
    async with aiohttp.ClientSession() as sess:
        yield sess


@pytest.fixture
def client(session: aiohttp.ClientSession) -> CloudflareClient:
    """Return a client bound to the test session."""
    return CloudflareClient(session, "tok", request_timeout=1)


async def test_verify_token_active(client: CloudflareClient) -> None:
    """An active token returns its status."""
    with aioresponses() as m:
        m.get(
            f"{BASE}/user/tokens/verify",
            payload=_ok({"id": "abc", "status": "active"}),
        )
        status = await client.async_verify_token()
    assert status.id == "abc"
    assert status.is_active


async def test_verify_token_inactive_raises_auth(client: CloudflareClient) -> None:
    """A token that verifies but is not active is an auth error."""
    with aioresponses() as m:
        m.get(
            f"{BASE}/user/tokens/verify",
            payload=_ok({"id": "abc", "status": "disabled"}),
        )
        with pytest.raises(CloudflareAuthError):
            await client.async_verify_token()


async def test_auth_error_on_401(client: CloudflareClient) -> None:
    """HTTP 401 maps to CloudflareAuthError with the API message."""
    with aioresponses() as m:
        m.get(
            f"{BASE}/user/tokens/verify",
            status=401,
            payload={"success": False, "errors": [{"message": "bad token"}]},
        )
        with pytest.raises(CloudflareAuthError, match="bad token"):
            await client.async_verify_token()


async def test_rate_limit_on_429(client: CloudflareClient) -> None:
    """HTTP 429 maps to CloudflareRateLimitError."""
    with aioresponses() as m:
        m.get(f"{BASE}/accounts?per_page=50", status=429, payload={"success": False})
        with pytest.raises(CloudflareRateLimitError):
            await client.async_get_accounts()


async def test_api_error_on_500(client: CloudflareClient) -> None:
    """A 5xx with an error envelope maps to CloudflareApiError carrying the code."""
    with aioresponses() as m:
        m.get(
            f"{BASE}/accounts?per_page=50",
            status=500,
            payload={"success": False, "errors": [{"message": "boom"}]},
        )
        with pytest.raises(CloudflareApiError) as err:
            await client.async_get_accounts()
    assert err.value.code == 500


async def test_success_false_without_http_error(client: CloudflareClient) -> None:
    """A 200 body with success=false is still an API error."""
    with aioresponses() as m:
        m.get(
            f"{BASE}/accounts?per_page=50",
            status=200,
            payload={"success": False, "result": []},
        )
        with pytest.raises(CloudflareApiError):
            await client.async_get_accounts()


async def test_connection_error_on_client_error(client: CloudflareClient) -> None:
    """A transport-level failure maps to CloudflareConnectionError."""
    with aioresponses() as m:
        m.get(f"{BASE}/accounts", exception=aiohttp.ClientError("nope"))
        with pytest.raises(CloudflareConnectionError):
            await client.async_get_accounts()


async def test_non_dict_result_raises_result_error(client: CloudflareClient) -> None:
    """A response missing a dict 'result' where one is required raises cleanly."""
    with aioresponses() as m:
        m.get(f"{BASE}/user/tokens/verify", payload={"success": True, "result": None})
        with pytest.raises(CloudflareResultError):
            await client.async_verify_token()


async def test_get_accounts_filters_missing_ids(client: CloudflareClient) -> None:
    """Accounts without an id are skipped."""
    with aioresponses() as m:
        m.get(
            f"{BASE}/accounts?per_page=50",
            payload=_ok([{"id": "a", "name": "A"}, {"name": "no id"}]),
        )
        accounts = await client.async_get_accounts()
    assert len(accounts) == 1
    assert accounts[0].id == "a"


async def test_get_rule_lists_parses_fields(client: CloudflareClient) -> None:
    """Rule lists parse id/name/kind/num_items."""
    with aioresponses() as m:
        m.get(
            f"{BASE}/accounts/{ACCOUNT}/rules/lists",
            payload=_ok(
                [{"id": "l1", "name": "casa", "kind": "ip", "num_items": 3}]
            ),
        )
        lists = await client.async_get_rule_lists(ACCOUNT)
    assert lists[0].name == "casa"
    assert lists[0].kind == "ip"
    assert lists[0].num_items == 3


async def test_get_list_items_follows_pagination(client: CloudflareClient) -> None:
    """Items are collected across cursor-paginated pages."""
    url = f"{BASE}/accounts/{ACCOUNT}/rules/lists/{LIST_ID}/items"
    with aioresponses() as m:
        m.get(
            f"{url}?per_page=500",
            payload=_ok(
                [{"id": "1", "ip": "1.1.1.1"}],
                result_info={"cursors": {"after": "CUR"}},
            ),
        )
        m.get(
            f"{url}?per_page=500&cursor=CUR",
            payload=_ok([{"id": "2", "ip": "2.2.2.2"}], result_info={"cursors": {}}),
        )
        items = await client.async_get_list_items(ACCOUNT, LIST_ID)
    assert [i.ip for i in items] == ["1.1.1.1", "2.2.2.2"]


async def test_list_items_skips_entries_without_ip(client: CloudflareClient) -> None:
    """List entries missing an ip field are ignored."""
    url = f"{BASE}/accounts/{ACCOUNT}/rules/lists/{LIST_ID}/items"
    with aioresponses() as m:
        m.get(
            f"{url}?per_page=500",
            payload=_ok([{"id": "1", "ip": "1.1.1.1"}, {"id": "2"}]),
        )
        items = await client.async_get_list_items(ACCOUNT, LIST_ID)
    assert [i.ip for i in items] == ["1.1.1.1"]


async def test_replace_items_returns_operation_id(client: CloudflareClient) -> None:
    """Replacing items returns the async bulk operation id."""
    with aioresponses() as m:
        m.put(
            f"{BASE}/accounts/{ACCOUNT}/rules/lists/{LIST_ID}/items",
            payload=_ok({"operation_id": "op1"}),
        )
        op_id = await client.async_replace_list_items(
            ACCOUNT, LIST_ID, [CloudflareListItem(ip="9.9.9.9", comment="hi")]
        )
    assert op_id == "op1"


async def test_replace_items_missing_operation_id(client: CloudflareClient) -> None:
    """A replace response without an operation_id raises a result error."""
    with aioresponses() as m:
        m.put(
            f"{BASE}/accounts/{ACCOUNT}/rules/lists/{LIST_ID}/items",
            payload=_ok({}),
        )
        with pytest.raises(CloudflareResultError):
            await client.async_replace_list_items(
                ACCOUNT, LIST_ID, [CloudflareListItem(ip="9.9.9.9")]
            )


async def test_get_bulk_operation_status(client: CloudflareClient) -> None:
    """Bulk operation status parses and exposes convenience flags."""
    with aioresponses() as m:
        m.get(
            f"{BASE}/accounts/{ACCOUNT}/rules/lists/bulk_operations/op1",
            payload=_ok({"id": "op1", "status": BULK_STATUS_COMPLETED}),
        )
        op = await client.async_get_bulk_operation(ACCOUNT, "op1")
    assert op.is_complete
    assert not op.is_failed
    assert not op.is_pending


@pytest.mark.parametrize(
    ("status", "is_failed", "is_pending"),
    [
        (BULK_STATUS_FAILED, True, False),
        (BULK_STATUS_RUNNING, False, True),
    ],
)
async def test_bulk_operation_flags(
    client: CloudflareClient, status: str, is_failed: bool, is_pending: bool
) -> None:
    """Failed and running statuses set the right flags."""
    with aioresponses() as m:
        m.get(
            f"{BASE}/accounts/{ACCOUNT}/rules/lists/bulk_operations/op1",
            payload=_ok({"id": "op1", "status": status, "error": "e"}),
        )
        op = await client.async_get_bulk_operation(ACCOUNT, "op1")
    assert op.is_failed is is_failed
    assert op.is_pending is is_pending


async def test_get_zones_follows_page_pagination(client: CloudflareClient) -> None:
    """Zones are collected across page-based pagination."""
    with aioresponses() as m:
        m.get(
            f"{BASE}/zones?page=1&per_page=50",
            payload=_ok(
                [{"id": "z1", "name": "example.com"}],
                result_info={"total_pages": 2},
            ),
        )
        m.get(
            f"{BASE}/zones?page=2&per_page=50",
            payload=_ok(
                [{"id": "z2", "name": "example.org"}],
                result_info={"total_pages": 2},
            ),
        )
        zones = await client.async_get_zones()
    assert [(z.id, z.name) for z in zones] == [
        ("z1", "example.com"),
        ("z2", "example.org"),
    ]


async def test_get_dns_records_passes_filters(client: CloudflareClient) -> None:
    """Name/type filters land in the query string and records parse fully."""
    with aioresponses() as m:
        m.get(
            f"{BASE}/zones/{ZONE}/dns_records?name=vpn.example.com&per_page=100&type=A",
            payload=_ok(
                [
                    {
                        "id": "r1",
                        "name": "vpn.example.com",
                        "type": "A",
                        "content": "1.2.3.4",
                        "proxied": False,
                        "ttl": 60,
                        "comment": "c",
                    }
                ]
            ),
        )
        records = await client.async_get_dns_records(
            ZONE, name="vpn.example.com", record_type="A"
        )
    assert len(records) == 1
    record = records[0]
    assert record.id == "r1"
    assert record.content == "1.2.3.4"
    assert record.proxied is False
    assert record.ttl == 60


async def test_create_dns_record_sends_body(client: CloudflareClient) -> None:
    """Creating a record posts the full body and parses the result."""
    with aioresponses() as m:
        m.post(
            f"{BASE}/zones/{ZONE}/dns_records",
            payload=_ok(
                {
                    "id": "r1",
                    "name": "vpn.example.com",
                    "type": "A",
                    "content": "1.2.3.4",
                    "proxied": False,
                    "ttl": 60,
                }
            ),
        )
        record = await client.async_create_dns_record(
            ZONE,
            name="vpn.example.com",
            record_type="A",
            content="1.2.3.4",
            ttl=60,
            proxied=False,
            comment="managed",
        )
        request = next(iter(m.requests.values()))[0]
    assert record.id == "r1"
    assert request.kwargs["json"] == {
        "name": "vpn.example.com",
        "type": "A",
        "content": "1.2.3.4",
        "ttl": 60,
        "proxied": False,
        "comment": "managed",
    }


async def test_update_dns_record_patches_content(client: CloudflareClient) -> None:
    """Updating a record patches content/ttl/proxied and parses the result."""
    with aioresponses() as m:
        m.patch(
            f"{BASE}/zones/{ZONE}/dns_records/r1",
            payload=_ok(
                {
                    "id": "r1",
                    "name": "vpn.example.com",
                    "type": "A",
                    "content": "5.6.7.8",
                    "proxied": False,
                    "ttl": 60,
                }
            ),
        )
        record = await client.async_update_dns_record(
            ZONE, "r1", content="5.6.7.8", ttl=60, proxied=False
        )
        request = next(iter(m.requests.values()))[0]
    assert record.content == "5.6.7.8"
    assert request.kwargs["json"] == {
        "content": "5.6.7.8",
        "ttl": 60,
        "proxied": False,
    }
