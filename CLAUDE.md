# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Implementation is underway, built as a sequence of milestone branches (each merged to `main`
via its own PR). Current state (2026-07-04, branch `milestone-5-coordinator`):

- **Milestone 1** — repository structure and tooling
- **Milestone 2** — integration manifest (`manifest.json`, domain `cloudflare_ip_sync`)
- **Milestone 3** — Cloudflare API client (`api.py`): token verify, accounts, rule lists,
  list items (paginated), replace-items (async bulk op), bulk-operation polling. HA-free,
  unit-testable, exceptions as a `CloudflareError` hierarchy (`CloudflareAuthError`,
  `CloudflareConnectionError`, `CloudflareRateLimitError`, `CloudflareApiError`,
  `CloudflareResultError`).
- **Milestone 4** — config flow (`config_flow.py`): 4-step UI setup (token → account →
  rule list → source entity), options flow (max retries, reconcile interval), reauth flow.
- **Milestone 5** (current HEAD) — coordinator and entry wiring (`coordinator.py`,
  `__init__.py`): `CloudflareIpSyncCoordinator(DataUpdateCoordinator[SyncState])` does
  **read-only** reconciliation — reads the source entity's IP and the Cloudflare list,
  compares them (normalized via `ipaddress`), and reports `in_sync`. Triggered by both a
  debounced state-change listener on the source entity and a periodic `update_interval`.
  The write path (actually calling `async_replace_list_items` + polling the bulk operation
  + retry/backoff) is explicitly deferred — not yet implemented.

The full plan is 14 milestones (renumbered slightly from the original kickoff spec, since the
implementation merged "entity discovery" into the config-flow milestone and moved the API
client before the config flow):

6. **Synchronization (write path) — next.** On mismatch: replace Rule List items, re-read and
   verify, retry with exponential backoff up to `CONF_MAX_RETRIES` (already in `const.py`,
   default 5); if still failing: persistent notification + error log + recorded last error.
7. Entities — `entity.py` base class + `sensor.py` (stub docstrings confirm "Milestone 7").
8. Diagnostics — `diagnostics.py` (stub confirms "Milestone 8"), token redaction.
9. Repairs (HA's repair-issue framework — a hard requirement from the kickoff spec with no
   dedicated slot in the original numbered list; presumed to land here, confirm with user).
10. Services — `services.py`/`services.yaml` (stub confirms "Milestone 10"):
    `cloudflare_ip_sync.force_sync`, `cloudflare_ip_sync.reload`.
11. Tests (`pytest-homeassistant-custom-component` conventions).
12. Documentation.
13. HACS packaging.
14. Release v1.0.0.

`tests/` currently only has `tests/__init__.py` — no test suite has been written yet despite
milestones 3-5 having real logic worth testing.

Check the working tree / git log rather than trusting this file blindly — it will drift as
milestones land.

## What this project is

A custom Home Assistant integration that keeps a Cloudflare Rule List updated with the user's
current public IP address, for use in WAF rules, Zero Trust policies, and Tunnel access control.
It's meant to eventually become the official Cloudflare integration for advanced networking
features, so the architecture must stay modular enough to add future Cloudflare modules (DNS,
Tunnel, Access, Zero Trust, Gateway, Analytics, Workers, Cache) without reworking the core —
this is why `api.py` is deliberately kept free of Home Assistant imports.

Concrete driving scenario: the user exposes Home Assistant via Cloudflare Tunnel, restricted by
a WAF rule `not ip.src in $casa` where `casa` is a Cloudflare Rule List. Their ISP rotates their
public IP periodically; the HA entity holding the current IP is
`sensor.archer_be550_external_ip`. This integration replaces the manual list update they do
today.

Feature set (per README / kickoff spec):

- Automatic public IP synchronization
- Cloudflare Rule Lists support (WAF and Zero Trust integration)
- Retry and error handling around the sync
- Config Flow-based setup (no YAML), supporting multiple config entries
- Diagnostics support (with token redaction) and Repairs
- Detailed logging, translations, reload/unload support
- Home Assistant Quality Scale compliance, full typing, Ruff/mypy-clean, pytest coverage
- HACS-installable

Target platform: Home Assistant 2026.7+. Requires a Cloudflare account, API token, and an
existing Cloudflare Rule List to sync into. GitHub repo: `Rhaiderr/homeassistant-cloudflare-ip-sync`.

## Conventions in use

This is a standard Home Assistant custom integration, following HA's established integration
structure:

- Code lives under `custom_components/cloudflare_ip_sync/`: `manifest.json`, `__init__.py`,
  `config_flow.py`, `const.py`, `api.py`, `coordinator.py`, plus stubs for `entity.py`,
  `sensor.py`, `diagnostics.py`, `services.py`.
- `DataUpdateCoordinator` pattern (not ad-hoc polling loops) drives both the periodic
  reconcile and the debounced on-change refresh.
- Cloudflare API tokens are entered and stored via Config Flow, never hardcoded or in YAML.
- `entry.runtime_data` holds the coordinator (typed via `type CloudflareIpSyncConfigEntry =
  ConfigEntry[CloudflareIpSyncCoordinator]` in `__init__.py`).
- Diagnostics will follow HA's diagnostics platform contract, redacting the API token
  (Milestone 8, not yet implemented).
- Tests belong under `tests/` mirroring `pytest-homeassistant-custom-component` conventions.

## Workflow

- One branch per milestone (`milestone-N-<short-name>`), merged to `main` via a GitHub PR.
- Commit messages describe what the milestone adds and end with a
  `Co-Authored-By: Claude <model> <noreply@anthropic.com>` trailer.
