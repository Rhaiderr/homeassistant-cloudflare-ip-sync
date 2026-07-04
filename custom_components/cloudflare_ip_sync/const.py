"""Constants for the Cloudflare Dynamic IP Sync integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "cloudflare_ip_sync"

# Cloudflare API v4.
API_BASE_URL: Final = "https://api.cloudflare.com/client/v4"

# Seconds before a single Cloudflare HTTP request is aborted.
DEFAULT_REQUEST_TIMEOUT: Final = 30

# Rule List kind that holds IP/CIDR entries (the only kind this integration syncs).
LIST_KIND_IP: Final = "ip"

# Config entry data keys.
CONF_API_TOKEN: Final = "api_token"
CONF_ACCOUNT_ID: Final = "account_id"
CONF_ACCOUNT_NAME: Final = "account_name"
CONF_LIST_ID: Final = "list_id"
CONF_LIST_NAME: Final = "list_name"
CONF_SOURCE_ENTITY_ID: Final = "source_entity_id"

# Config entry option keys and their defaults.
CONF_MAX_RETRIES: Final = "max_retries"
CONF_RECONCILE_INTERVAL: Final = "reconcile_interval"
DEFAULT_MAX_RETRIES: Final = 5
DEFAULT_RECONCILE_INTERVAL: Final = 30  # minutes

# Comment attached to the Rule List item this integration writes, so it's
# recognizable in the Cloudflare dashboard.
LIST_ITEM_COMMENT: Final = "Managed by Home Assistant (cloudflare_ip_sync)"

# Seconds between polls of an in-progress Cloudflare bulk operation, and the
# overall budget before giving up on a single sync attempt.
BULK_OPERATION_POLL_INTERVAL: Final = 2
BULK_OPERATION_TIMEOUT: Final = 30

# Exponential backoff between failed sync attempts (replace + verify).
SYNC_INITIAL_BACKOFF: Final = 5
SYNC_MAX_BACKOFF: Final = 60
