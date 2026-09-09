"""The UGREEN Connect integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import UgreenApi, UgreenAuthError, UgreenError
from .const import (
    CONF_DEBUG_DUMP,
    CONF_PORT_DEVICES,
    CONF_REGION,
    DEFAULT_LANGUAGE,
    DEFAULT_PORT_DEVICES,
    DEFAULT_REGION,
    DOMAIN,
    REGIONS,
)
from .coordinator import UgreenCoordinator, device_key, device_ports
from .frontend import async_register_card
from .image_proxy import async_register_view
from .rtcx import RtcxClient
from .services import async_register

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.EVENT,
    Platform.IMAGE,
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.UPDATE,
]

type UgreenConfigEntry = ConfigEntry[UgreenCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: UgreenConfigEntry) -> bool:
    """Log in and start polling."""
    region = entry.data.get(CONF_REGION, DEFAULT_REGION)
    session = async_get_clientsession(hass)
    api = UgreenApi(
        session,
        REGIONS.get(region, REGIONS[DEFAULT_REGION]),
        DEFAULT_LANGUAGE,
        region,
    )

    try:
        await api.login(entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD])
    except UgreenAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except UgreenError as err:
        raise ConfigEntryNotReady(str(err)) from err

    # Telemetry lives behind a second cloud. Setting it up must not block the
    # entry, since the inventory sensors work without it.
    rtcx = RtcxClient(session, api)
    try:
        await rtcx.async_login()
    except UgreenError as err:
        _LOGGER.warning("RTCX gateway unavailable, live power disabled: %s", err)

    coordinator = UgreenCoordinator(
        hass, entry, api, rtcx, debug_dump=entry.data.get(CONF_DEBUG_DUMP, False)
    )
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await async_register(hass)
    await async_register_card(hass)
    async_register_view(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Only once the entities exist: until they have been added, they still
    # belong to the devices of the previous layout, and removing one of those
    # would take its entities with it rather than let them move.
    _prune_devices(hass, entry)
    # The poll interval is fixed when the coordinator is built, so a changed
    # option only takes effect once the entry is set up again.
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


def _prune_devices(hass: HomeAssistant, entry: UgreenConfigEntry) -> None:
    """Drop devices this layout no longer calls for.

    A device outlives the entities that were on it. Turning per-port devices
    off moves every entity onto the charger and leaves eight empty shells
    behind in the device list, and nothing else will ever remove them --
    likewise a charger that has left the account.

    Runs after the platforms, not before: until the entities have been added
    under the new layout they are still registered against the old devices,
    and removing one then deletes its entities instead of leaving them to
    move -- taking their history with it.
    """
    keys = {key for device in coordinator_devices(entry) if (key := device_key(device))}
    if not keys:
        # A poll that came back empty says nothing about what should exist, and
        # acting on it would clear the whole device list on a bad morning.
        return

    wanted = {(DOMAIN, key) for key in keys}
    if entry.options.get(CONF_PORT_DEVICES, DEFAULT_PORT_DEVICES):
        wanted |= {
            (DOMAIN, f"{key}_{port}")
            for key in keys
            for port in device_ports(entry.runtime_data, key)
        }

    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if device.identifiers & wanted:
            continue
        _LOGGER.debug("removing device no longer in this layout: %s", device.name)
        registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


def coordinator_devices(entry: UgreenConfigEntry) -> list[dict]:
    """The account's device inventory from the poll that has just happened."""
    return entry.runtime_data.data.get("devices", [])


async def _async_options_updated(hass: HomeAssistant, entry: UgreenConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: UgreenConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
