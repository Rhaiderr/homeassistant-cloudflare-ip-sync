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
