"""Config, options and reauth flow for Cloudflare Dynamic IP Sync.

The setup is entirely UI-driven (no YAML). It walks the user through four
validated steps -- token, account, Rule List and source entity -- and stores
the result in a config entry. Tuning knobs live in a separate options flow, and
an expired/invalid token triggers a reauth flow.
"""

from __future__ import annotations

import ipaddress
import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .api import (
    CloudflareAuthError,
    CloudflareClient,
    CloudflareConnectionError,
    CloudflareError,
    CloudflareRateLimitError,
)
from .const import (
    CONF_ACCOUNT_ID,
    CONF_ACCOUNT_NAME,
    CONF_API_TOKEN,
    CONF_LIST_ID,
    CONF_LIST_NAME,
    CONF_MAX_RETRIES,
    CONF_RECONCILE_INTERVAL,
    CONF_SOURCE_ENTITY_ID,
    DEFAULT_MAX_RETRIES,
    DEFAULT_RECONCILE_INTERVAL,
    DOMAIN,
    LIST_KIND_IP,
)

_LOGGER = logging.getLogger(__name__)

TOKEN_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)


def _error_key(err: CloudflareError) -> str:
    """Map a Cloudflare client error onto a config-flow error key."""
    if isinstance(err, CloudflareAuthError):
        return "invalid_auth"
    if isinstance(err, CloudflareRateLimitError):
        return "rate_limit"
    if isinstance(err, CloudflareConnectionError):
        return "cannot_connect"
    return "unknown"


class CloudflareIpSyncConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the multi-step setup flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the transient state shared across steps."""
        self._data: dict[str, Any] = {}

    # -- step 1: token ----------------------------------------------------- #
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for and validate the Cloudflare API token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_API_TOKEN]
            client = self._client(token)
            try:
                await client.async_verify_token()
            except CloudflareError as err:
                errors["base"] = _error_key(err)
            else:
                self._data[CONF_API_TOKEN] = token
                return await self.async_step_account()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_API_TOKEN): TOKEN_SELECTOR}),
            errors=errors,
        )

    # -- step 2: account --------------------------------------------------- #
    async def async_step_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick one of the accounts the token can access."""
        client = self._client(self._data[CONF_API_TOKEN])
        try:
            accounts = await client.async_get_accounts()
        except CloudflareError as err:
            return self.async_abort(reason=_error_key(err))

        if not accounts:
            return self.async_abort(reason="no_accounts")

        names = {account.id: account.name for account in accounts}
        if user_input is not None:
            account_id = user_input[CONF_ACCOUNT_ID]
            self._data[CONF_ACCOUNT_ID] = account_id
            self._data[CONF_ACCOUNT_NAME] = names.get(account_id, account_id)
            return await self.async_step_rule_list()

        options = [
            selector.SelectOptionDict(value=account.id, label=account.name)
            for account in accounts
        ]
        return self.async_show_form(
            step_id="account",
            data_schema=vol.Schema(
                {vol.Required(CONF_ACCOUNT_ID): _dropdown(options)}
            ),
        )

    # -- step 3: rule list ------------------------------------------------- #
    async def async_step_rule_list(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick the IP Rule List to keep in sync."""
        client = self._client(self._data[CONF_API_TOKEN])
        try:
            rule_lists = await client.async_get_rule_lists(
                self._data[CONF_ACCOUNT_ID]
            )
        except CloudflareError as err:
            return self.async_abort(reason=_error_key(err))

        ip_lists = [rule for rule in rule_lists if rule.kind == LIST_KIND_IP]
        if not ip_lists:
            return self.async_abort(reason="no_rule_lists")

        names = {rule.id: rule.name for rule in ip_lists}
        if user_input is not None:
            list_id = user_input[CONF_LIST_ID]
            self._data[CONF_LIST_ID] = list_id
            self._data[CONF_LIST_NAME] = names.get(list_id, list_id)
            await self.async_set_unique_id(
                f"{self._data[CONF_ACCOUNT_ID]}:{list_id}"
            )
            self._abort_if_unique_id_configured()
            return await self.async_step_entity()

        options = [
            selector.SelectOptionDict(value=rule.id, label=rule.name)
            for rule in ip_lists
        ]
        return self.async_show_form(
            step_id="rule_list",
            data_schema=vol.Schema({vol.Required(CONF_LIST_ID): _dropdown(options)}),
        )

    # -- step 4: source entity --------------------------------------------- #
    async def async_step_entity(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick the HA entity that holds the public IP."""
        options = self._ip_entity_options()
        if not options:
            return self.async_abort(reason="no_ip_entities")

        if user_input is not None:
            self._data[CONF_SOURCE_ENTITY_ID] = user_input[CONF_SOURCE_ENTITY_ID]
            return self.async_create_entry(
                title=self._data[CONF_LIST_NAME],
                data=self._data,
            )

        return self.async_show_form(
            step_id="entity",
            data_schema=vol.Schema(
                {vol.Required(CONF_SOURCE_ENTITY_ID): _dropdown(options)}
            ),
        )

    # -- reauth ------------------------------------------------------------ #
    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Start reauth when the stored token is rejected."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new token and update the existing entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_API_TOKEN]
            client = self._client(token)
            try:
                await client.async_verify_token()
            except CloudflareError as err:
                errors["base"] = _error_key(err)
            else:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates={CONF_API_TOKEN: token},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_TOKEN): TOKEN_SELECTOR}),
            errors=errors,
        )

    # -- helpers ----------------------------------------------------------- #
    def _client(self, token: str) -> CloudflareClient:
        """Build a Cloudflare client bound to Home Assistant's shared session."""
        return CloudflareClient(async_get_clientsession(self.hass), token)

    def _ip_entity_options(self) -> list[selector.SelectOptionDict]:
        """Return HA entities whose current state parses as an IP address."""
        options: list[selector.SelectOptionDict] = []
        for state in self.hass.states.async_all():
            if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN, ""):
                continue
            try:
                ipaddress.ip_address(state.state.strip())
            except ValueError:
                continue
            name = state.name or state.entity_id
            options.append(
                selector.SelectOptionDict(
                    value=state.entity_id,
                    label=f"{name} ({state.state})",
                )
            )
        return options

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> OptionsFlow:
        """Return the options flow handler."""
        return CloudflareIpSyncOptionsFlow()


class CloudflareIpSyncOptionsFlow(OptionsFlow):
    """Handle reconfigurable tuning options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage retry count and reconciliation interval."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MAX_RETRIES,
                        default=current.get(CONF_MAX_RETRIES, DEFAULT_MAX_RETRIES),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0, max=10, mode=selector.NumberSelectorMode.BOX
                        )
                    ),
                    vol.Required(
                        CONF_RECONCILE_INTERVAL,
                        default=current.get(
                            CONF_RECONCILE_INTERVAL, DEFAULT_RECONCILE_INTERVAL
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1,
                            max=1440,
                            unit_of_measurement="min",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
        )


def _dropdown(options: list[selector.SelectOptionDict]) -> selector.SelectSelector:
    """Build a single-select dropdown selector from the given options."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options, mode=selector.SelectSelectorMode.DROPDOWN
        )
    )
