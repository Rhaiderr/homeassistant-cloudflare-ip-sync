# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

**v1.0.0 — all 14 milestones complete** (2026-07-04). Built as a sequence of milestone
branches, each merged to `main` via its own PR:

1. Repository structure and tooling
2. Integration manifest (`manifest.json`, domain `cloudflare_ip_sync`)
3. Cloudflare API client (`api.py`) — HA-free, unit-testable; token verify, accounts, rule
   lists, list items (paginated), replace-items (async bulk op), bulk-operation polling;
   `CloudflareError` exception hierarchy.
4. Config flow (`config_flow.py`) — 4-step UI setup (token → account → rule list → source
   entity), options flow (max retries, reconcile interval), reauth flow. Accepts both user
   API tokens and account-owned tokens (`cfat_`; verify-endpoint rejection falls back to
   listing accounts — see `_async_validate_token`).
5. Coordinator (`coordinator.py`, `__init__.py`) — `DataUpdateCoordinator[SyncState]`,
   debounced state-change listener + periodic reconcile.
6. Synchronization write path — replace items, poll bulk op, verify by re-read, exponential
   backoff up to `CONF_MAX_RETRIES`; on exhaustion: persistent notification (auto-dismissed
   on recovery) + `SyncState.last_error`. Sync failure does NOT raise `UpdateFailed` —
   entities stay available showing "out of sync".
7. Entities — `entity.py` base + `sensor.py` enum sensor (`in_sync`/`out_of_sync`) with
   local_ip/cloudflare_ips/last_synced/last_error attributes.
8. Diagnostics — token redacted via `async_redact_data`.
9. Repairs — non-fixable issue when the Rule List vanished from the account (confirmed via
   `async_get_rule_lists` on a non-auth read error); no `repairs.py` needed.
10. Services — `force_sync` / `reload`, both keyed by `config_entry_id`.
11. Tests — 41 tests, ~93% coverage (`pytest-homeassistant-custom-component`, `aioresponses`);
    install dev deps with `uv pip install -e ".[test]"`.
12. Documentation — full README (Cloudflare setup walkthrough, token permissions, custom
    IP-entity guide, troubleshooting) + MIT LICENSE.
13. HACS packaging — `hacs.json`, validate workflow (hacs/action + hassfest) on every PR.
14. Release v1.0.0 — validated end-to-end on a real HA instance (Raspberry Pi 5) against the
    live Cloudflare API, including the write path.

Post-1.0 direction: future Cloudflare modules (DNS, Tunnel, Access, Zero Trust, ...) — keep
`api.py` free of Home Assistant imports so they can share it. Check the working tree / git
log rather than trusting this file blindly — it drifts.

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

Target platform: Home Assistant 2026.6+. Requires a Cloudflare account, API token, and an
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
