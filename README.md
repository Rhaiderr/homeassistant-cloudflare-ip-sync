# Cloudflare Dynamic IP Sync for Home Assistant

A custom [Home Assistant](https://www.home-assistant.io/) integration that keeps a
**Cloudflare Rule List** in sync with your current public IP address — automatically, whenever
your ISP changes it.

It's built for the common self-hosting setup: you expose Home Assistant (or anything else)
through a **Cloudflare Tunnel**, protected by a **WAF rule** that only allows traffic from your
home IP. When your ISP rotates that IP, the WAF rule keeps pointing at the old one and you lock
yourself out — until you edit the Rule List by hand. This integration does that edit for you.

> **Example.** A WAF custom rule `not ip.src in $casa` blocks everything except IPs in the
> Rule List named `casa`. This integration watches a Home Assistant entity holding your public
> IP (e.g. `sensor.archer_be550_external_ip`) and rewrites `casa` to match whenever it changes.

The architecture is intentionally modular so it can grow into a broader Cloudflare integration
(DNS, Tunnel, Access, Zero Trust, Gateway, Analytics, Workers, Cache) — the Cloudflare API
client is kept free of Home Assistant internals.

---

## Features

- **Automatic public IP synchronization** into a Cloudflare Rule List (for WAF / Zero Trust /
  Tunnel access control).
- **Event-driven + periodic**: syncs immediately (debounced) when the source IP entity changes,
  and reconciles on a configurable interval as a safety net.
- **Robust write path**: replaces the Rule List, waits for Cloudflare's async bulk operation,
  re-reads to verify, and retries with exponential backoff.
- **Config Flow setup** — no YAML. Supports **multiple config entries** (several accounts /
  Rule Lists at once).
- **Sync-status sensor** with the local IP, Cloudflare IPs, last sync time and last error as
  attributes.
- **Diagnostics** (with the API token redacted) and **Repairs** (raised if the Rule List is
  deleted from Cloudflare).
- **Services** to force an immediate sync or reload an entry.
- Reauth, reload/unload support, translations, and detailed debug logging (tokens never
  logged).

---

## Requirements

- **Home Assistant 2026.6 or newer**
- A **Cloudflare account**
- A **Cloudflare API token** with permission to read and edit Account Rule Lists (see below)
- An **existing Cloudflare Rule List** of kind *IP* to sync into
- A **Home Assistant entity** whose state is your current public IPv4/IPv6 address

> This integration does **not** create the Rule List or the WAF rule for you — create those in
> the Cloudflare dashboard first, then point the integration at the list.

---

## Creating the Cloudflare API token

1. In the Cloudflare dashboard, go to **My Profile → API Tokens → Create Token → Create Custom
   Token**.
2. Give it a name (e.g. `home-assistant-ip-sync`).
3. Add **both** permissions:
   - **Account → Account Filter Lists → Edit** — reads and writes the Rule List itself.
   - **Account → Account Settings → Read** — required for the setup flow to list your
     accounts; without it, the token validates but the account step fails.
4. Under **Account Resources**, scope it to the account that owns your Rule List.
5. Leave **Client IP Address Filtering** empty — your public IP rotates (that's the whole
   point of this integration), so an IP-restricted token would break on the first change.
6. Create the token and copy it — you'll paste it into the config flow. Home Assistant stores it
   in the config entry; it is never written to YAML, logs, or diagnostics.

> **Account-owned tokens also work.** Tokens created under **Manage Account → API Tokens**
> (prefix `cfat_`) are supported too, with the same two permissions. Cloudflare rejects them on
> its user-token verify endpoint, so the setup flow validates them by listing your accounts
> instead — you don't need to do anything different.

---

## Installation

### HACS (recommended)

1. In HACS → **Integrations**, open the menu (⋮) → **Custom repositories**.
2. Add `https://github.com/Rhaiderr/homeassistant-cloudflare-ip-sync` as an **Integration**.
3. Install **Cloudflare Dynamic IP Sync** and restart Home Assistant.

### Manual

1. Copy `custom_components/cloudflare_ip_sync/` into your Home Assistant
   `config/custom_components/` directory.
2. Restart Home Assistant.

---

## Configuration

Add the integration under **Settings → Devices & Services → Add Integration →
Cloudflare Dynamic IP Sync**. The setup walks four steps:

1. **API token** — pasted and validated against Cloudflare immediately.
2. **Account** — pick the account the token can access.
3. **Rule List** — pick the IP Rule List to keep in sync.
4. **Source entity** — pick the Home Assistant entity whose state holds your public IP. Only
   entities whose current state parses as an IP address are offered.

You can add the integration multiple times to sync several Rule Lists (even across accounts).

### Options

Open the integration's **Configure** button to tune:

| Option | Default | Description |
| --- | --- | --- |
| **Maximum sync retries** | 5 | How many times to retry a failed sync (with exponential backoff) before giving up. |
| **Reconciliation interval** | 30 min | How often to re-check Cloudflare against the source entity, independent of state changes. |

---

## What it creates

### Sensor

`sensor.<rule_list>_sync_status` — an enum sensor with two states:

- `in_sync` — the Rule List holds exactly your current public IP.
- `out_of_sync` — it doesn't (yet), or the last sync failed.

Attributes:

| Attribute | Meaning |
| --- | --- |
| `local_ip` | The IP read from the source entity. |
| `cloudflare_ips` | The IPs currently in the Rule List. |
| `last_synced` | Timestamp of the last confirmed match. |
| `last_error` | The last sync error, if any. |

### Services

| Service | Description |
| --- | --- |
| `cloudflare_ip_sync.force_sync` | Immediately reconcile the targeted entry's Rule List. |
| `cloudflare_ip_sync.reload` | Reload the targeted config entry. |

Both take a required **`config_entry_id`** so you choose which configured instance to act on:

```yaml
action: cloudflare_ip_sync.force_sync
data:
  config_entry_id: <your entry id>
```

### Diagnostics

Download diagnostics from the integration's device page for troubleshooting — it includes the
integration version, config (with the **API token redacted**), coordinator health, and the
current sync state.

### Repairs

If the configured Rule List can no longer be found in your Cloudflare account (deleted, or the
token lost access), a repair issue appears under **Settings → System → Repairs** prompting you
to reconfigure. It clears automatically once the list is reachable again.

---

## How it works

The integration follows Home Assistant's `DataUpdateCoordinator` pattern:

1. It reads your public IP from the source entity and the current Rule List from Cloudflare.
2. If they already match (compared as normalized networks, so `1.2.3.4` and `1.2.3.4/32` are
   equal), it does nothing.
3. If they differ, it replaces the Rule List with your current IP, waits for Cloudflare's
   asynchronous bulk operation to complete, then re-reads the list to verify.
4. On failure it retries with exponential backoff up to your configured maximum. If it still
   can't sync, it raises a persistent notification and records the error (visible on the sensor
   and in diagnostics) — without marking entities unavailable, since the list was still read.

Syncs are triggered both by **source-entity state changes** (debounced, so ISP flapping doesn't
thrash the API) and by the periodic **reconciliation interval**.

---

## Troubleshooting

Enable debug logging to see each reconcile and sync attempt (the API token is never logged):

```yaml
logger:
  default: info
  logs:
    custom_components.cloudflare_ip_sync: debug
```

- **`invalid_auth` during setup** — the token is wrong, inactive, or missing the *Account
  Filter Lists → Edit* permission.
- **Token validates but the account step fails (or shows no accounts)** — the token is missing
  the *Account Settings → Read* permission, which the setup flow needs to list your accounts.
- **No Rule Lists to choose from** — the account has no *IP*-kind Rule Lists; create one in the
  Cloudflare dashboard first.
- **No entities to choose from** — no entity's state currently looks like an IP address; make
  sure your public-IP sensor is set up and populated first.
- **Sensor stuck `out_of_sync`** — check `last_error` on the sensor and the debug log; a
  persistent notification is raised after retries are exhausted.

---

## Roadmap

The first supported feature is Cloudflare Rule List synchronization. The integration is
structured to add further Cloudflare modules over time: DNS, Tunnel, Access, Zero Trust,
Gateway, Analytics, Workers, and Cache.

---

## Contributing

Issues and pull requests are welcome at
[`Rhaiderr/homeassistant-cloudflare-ip-sync`](https://github.com/Rhaiderr/homeassistant-cloudflare-ip-sync).

Development setup:

```bash
uv venv --python 3.13
uv pip install -e ".[test]"
pytest -q
ruff check .
mypy custom_components/cloudflare_ip_sync/
```

## License

MIT — see [`LICENSE`](LICENSE).
