"""That the integration starts, and puts what it should where it should."""

from __future__ import annotations

import pytest
from conftest import DEVICE
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.ugreen_connect_plus.const import DOMAIN

CHARGER = (DOMAIN, DEVICE["deviceUniqueCode"])


async def test_the_entry_loads(hass, started) -> None:
    # The cheapest test here and the one that earns its keep: every module is
    # imported and every platform set up, so a name that no longer exists is a
    # failure rather than a surprise at somebody's next restart.
    assert started.state is not None
    assert started.runtime_data is not None


def _charger(hass, started):
    # By identifier within this entry: identifiers are no longer unique across
    # config entries, and the registry refuses the old lookup outright.
    return dr.async_get(hass).async_get_device_by_identifier(
        CHARGER, started.entry_id
    )


async def test_the_charger_becomes_a_device(hass, started) -> None:
    charger = _charger(hass, started)
    assert charger is not None
    assert charger.manufacturer == "UGREEN"
    assert charger.model_id == "X783"
    assert charger.sw_version == "1.2.1"


async def test_each_port_becomes_its_own_device(hass, started) -> None:
    # The option is on in this entry, so all eight ports of the report get a
    # device hung under the charger.
    charger = _charger(hass, started)
    entries = dr.async_entries_for_config_entry(dr.async_get(hass), started.entry_id)
    ports = [d for d in entries if d.via_device_id == charger.id]
    assert len(ports) == 8
    assert all(d.model == "Charging port" for d in ports)


@pytest.mark.parametrize(
    "suffix",
    [
        "_C3_power",            # a live reading
        "_C3_voltage",
        "_C3_current",
        "_C3_protocol",
        "_C3_charging",          # the binary sensor
        "_C3_charging_event",    # and the event entity beside it
        "_C3_session_energy",
        "_C3_energy_total",
        "_C3_custom_limit",      # the custom mode, read
        "_total_power",
        "_energy_total",
        "_brightness",           # a control, so a write path exists for it
        "_charging_mode",
        "_screensaver",
        # No wallpaper select: this charger's library is empty in the fixture,
        # and a select with nothing to choose from is not offered.
        "_firmware",
        "_status",
        "_last_poll",
        "_product_image",
    ],
)
async def test_the_entities_that_matter_exist(hass, started, suffix: str) -> None:
    # By unique id rather than entity id: entity ids follow the user's language
    # and can be renamed, unique ids are what the registry keys on.
    unique = DEVICE["deviceUniqueCode"] + suffix
    entries = er.async_entries_for_config_entry(er.async_get(hass), started.entry_id)
    assert any(e.unique_id == unique for e in entries), (
        f"no entity with unique id {unique}"
    )


async def test_a_reading_reaches_the_state_machine(hass, started) -> None:
    entries = er.async_entries_for_config_entry(er.async_get(hass), started.entry_id)
    entry = next(
        e for e in entries
        if e.unique_id == DEVICE["deviceUniqueCode"] + "_C3_power"
    )
    state = hass.states.get(entry.entity_id)
    assert state is not None and float(state.state) == 39.0


async def test_it_unloads_again(hass, started) -> None:
    # Every platform has to give its entities back, or a reload leaves
    # duplicates behind and the second setup fails on unique ids.
    assert await hass.config_entries.async_unload(started.entry_id)
    await hass.async_block_till_done()
    assert not hass.states.async_entity_ids(DOMAIN)
