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
