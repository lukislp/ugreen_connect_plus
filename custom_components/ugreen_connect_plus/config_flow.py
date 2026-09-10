"""Config flow for UGREEN Connect Plus."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import UgreenApi, UgreenAuthError, UgreenError
from .const import (
    CONF_DEBUG_DUMP,
    CONF_EFFICIENCY,
    CONF_IDLE_END,
    CONF_NOMINAL_VOLTAGE,
    CONF_PORT_DEVICES,
    CONF_REGION,
    DEFAULT_EFFICIENCY,
    DEFAULT_IDLE_END,
    DEFAULT_LANGUAGE,
    DEFAULT_NOMINAL_VOLTAGE,
    DEFAULT_PORT_DEVICES,
    DEFAULT_REGION,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    REGIONS,
)

_LOGGER = logging.getLogger(__name__)

EMAIL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL))
PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): EMAIL_SELECTOR,
        vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
        vol.Required(CONF_REGION, default=DEFAULT_REGION): SelectSelector(
            SelectSelectorConfig(
                options=list(REGIONS),
                mode=SelectSelectorMode.DROPDOWN,
                translation_key="region",
            )
        ),
        # Asked here rather than left to the options because it decides how the
        # devices are laid out in the first place: changing it later works, but
        # it moves every entity between devices and leaves whatever pointed at
        # the old ones to be set up again.
        vol.Required(CONF_PORT_DEVICES, default=DEFAULT_PORT_DEVICES): bool,
        # Off by default: it writes the raw cloud payload, device ids included,
        # next to configuration.yaml on every refresh.
        vol.Required(CONF_DEBUG_DUMP, default=False): bool,
    }
)


# Only what a reconfigure is for: the connection. The tuning lives in the
# options and the layout was settled at setup, and dragging either through
# here would invite changing them by accident.
RECONFIGURE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): EMAIL_SELECTOR,
        vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
        vol.Required(CONF_REGION, default=DEFAULT_REGION): SelectSelector(
            SelectSelectorConfig(
                options=list(REGIONS),
                mode=SelectSelectorMode.DROPDOWN,
                translation_key="region",
            )
        ),
    }
)

OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): NumberSelector(
            NumberSelectorConfig(
                min=MIN_SCAN_INTERVAL,
                max=MAX_SCAN_INTERVAL,
                step=1,
                unit_of_measurement="s",
                mode=NumberSelectorMode.BOX,
            )
        ),
        vol.Required(CONF_REGION, default=DEFAULT_REGION): SelectSelector(
            SelectSelectorConfig(
                options=list(REGIONS),
                mode=SelectSelectorMode.DROPDOWN,
                translation_key="region",
            )
        ),
        vol.Required(
            CONF_NOMINAL_VOLTAGE, default=DEFAULT_NOMINAL_VOLTAGE
        ): NumberSelector(
            NumberSelectorConfig(
                min=1, max=30, step=0.05, unit_of_measurement="V",
                mode=NumberSelectorMode.BOX,
            )
        ),
        vol.Required(CONF_EFFICIENCY, default=DEFAULT_EFFICIENCY): NumberSelector(
            NumberSelectorConfig(
                min=50, max=100, step=1, unit_of_measurement="%",
                mode=NumberSelectorMode.SLIDER,
            )
        ),
        vol.Required(CONF_IDLE_END, default=DEFAULT_IDLE_END): NumberSelector(
            NumberSelectorConfig(
                min=5, max=1440, step=5, unit_of_measurement="min",
                mode=NumberSelectorMode.BOX,
            )
        ),
        vol.Required(CONF_PORT_DEVICES, default=DEFAULT_PORT_DEVICES): bool,
        vol.Required(CONF_DEBUG_DUMP, default=False): bool,
    }
)


class UgreenConnectConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for the UgreenConnect account and verify it against the cloud."""

    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry_data: Mapping[str, Any] | None = None

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> UgreenOptionsFlow:
        return UgreenOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            region = user_input[CONF_REGION]
            api = UgreenApi(
                async_get_clientsession(self.hass), REGIONS[region], DEFAULT_LANGUAGE
            )
            try:
                await api.login(email, user_input[CONF_PASSWORD])
            except UgreenAuthError:
                errors["base"] = "invalid_auth"
            except UgreenError as err:
                _LOGGER.debug("Cannot connect to UGREEN cloud: %s", err)
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(email.lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=email,
                    data={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_REGION: region,
                        CONF_DEBUG_DUMP: user_input[CONF_DEBUG_DUMP],
                    },
                    # Kept, not merely asked for: a form that collects a
                    # setting and drops it is the fault this dialog has just
                    # been rid of.
                    options={
                        CONF_PORT_DEVICES: bool(user_input[CONF_PORT_DEVICES]),
                    },
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the account or the region without starting over.

        Removing the integration and adding it again does the same job and
        takes every entity's history with it, which is a steep price for a
        changed password.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            region = user_input[CONF_REGION]
            session = async_get_clientsession(self.hass)
            api = UgreenApi(session, REGIONS[region], DEFAULT_LANGUAGE)
            try:
                await api.login(email, user_input[CONF_PASSWORD])
            except UgreenAuthError:
                errors["base"] = "invalid_auth"
            except UgreenError as err:
                _LOGGER.debug("Cannot connect to UGREEN cloud: %s", err)
                errors["base"] = "cannot_connect"
            else:
                if email.lower() != entry.unique_id:
                    # A different account is a different set of chargers, and
                    # the entities here belong to this one. That is an add,
                    # not a reconfigure.
                    errors["base"] = "wrong_account"
                else:
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={
                            CONF_EMAIL: email,
                            CONF_PASSWORD: user_input[CONF_PASSWORD],
                            CONF_REGION: region,
                        },
                    )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                RECONFIGURE_SCHEMA,
                {
                    CONF_EMAIL: entry.data.get(CONF_EMAIL),
                    CONF_REGION: entry.data.get(CONF_REGION, DEFAULT_REGION),
                },
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        self._reauth_entry_data = entry_data
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            region = entry.data[CONF_REGION]
            api = UgreenApi(
                async_get_clientsession(self.hass), REGIONS[region], DEFAULT_LANGUAGE
            )
            try:
                await api.login(entry.data[CONF_EMAIL], user_input[CONF_PASSWORD])
            except UgreenAuthError:
                errors["base"] = "invalid_auth"
            except UgreenError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR}),
            description_placeholders={"email": entry.data[CONF_EMAIL]},
            errors=errors,
        )


class UgreenOptionsFlow(OptionsFlow):
    """Change how often the cloud is polled, and which region it is asked.

    Region lives here as well as in the initial form because an account moved to
    another server would otherwise mean deleting the entry and setting it up
    again; the credentials are re-checked against the new region before the
    change is kept.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self.config_entry

        if user_input is not None:
            region = user_input[CONF_REGION]
            if region != entry.data.get(CONF_REGION, DEFAULT_REGION):
                api = UgreenApi(
                    async_get_clientsession(self.hass), REGIONS[region], DEFAULT_LANGUAGE
                )
                try:
                    await api.login(entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD])
                except UgreenAuthError:
                    errors["base"] = "invalid_auth"
                except UgreenError as err:
                    _LOGGER.debug("Cannot reach region %s: %s", region, err)
                    errors["base"] = "cannot_connect"
            if not errors:
                # Region and the dump flag are read from `data`, so they are
                # written back there and only the interval lives in options.
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={
                        **entry.data,
                        CONF_REGION: region,
                        CONF_DEBUG_DUMP: user_input[CONF_DEBUG_DUMP],
                    },
                )
                return self.async_create_entry(
                    data={
                        CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                        CONF_NOMINAL_VOLTAGE: float(user_input[CONF_NOMINAL_VOLTAGE]),
                        CONF_EFFICIENCY: int(user_input[CONF_EFFICIENCY]),
                        CONF_IDLE_END: int(user_input[CONF_IDLE_END]),
                        CONF_PORT_DEVICES: bool(user_input[CONF_PORT_DEVICES]),
                    }
                )

        current = {
            CONF_SCAN_INTERVAL: entry.options.get(
                CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
            ),
            CONF_NOMINAL_VOLTAGE: entry.options.get(
                CONF_NOMINAL_VOLTAGE, DEFAULT_NOMINAL_VOLTAGE
            ),
            CONF_EFFICIENCY: entry.options.get(CONF_EFFICIENCY, DEFAULT_EFFICIENCY),
            CONF_IDLE_END: entry.options.get(CONF_IDLE_END, DEFAULT_IDLE_END),
            CONF_PORT_DEVICES: entry.options.get(
                CONF_PORT_DEVICES, DEFAULT_PORT_DEVICES
            ),
            CONF_REGION: entry.data.get(CONF_REGION, DEFAULT_REGION),
            CONF_DEBUG_DUMP: entry.data.get(CONF_DEBUG_DUMP, False),
        }
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(OPTIONS_SCHEMA, current),
            errors=errors,
        )
