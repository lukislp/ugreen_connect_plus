"""Fixtures for the tests that do need Home Assistant.

The suite in ``tests/`` deliberately loads the protocol and the session tracker
straight from their source, without Home Assistant anywhere near them. That
covers the rules, and it covers nothing about whether the integration starts.

A linter's own autofix once removed a constant another module imported: every
test in that suite stayed green, and the integration failed at startup. These
tests exist for that class of mistake -- they set the thing up for real, with
only the two clouds replaced.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ugreen_connect_plus.const import (
    CONF_PORT_DEVICES,
    CONF_REGION,
    DOMAIN,
)

# One charger, mid-charge on C3, with a screen and a configured custom mode --
# the shape a real poll returns, trimmed to what the entities read.
READING = {
    "ports": {
        "C1": {"voltage": 5.1, "current": 0.0, "power": 0.0, "protocol": "none"},
        "C2": {"voltage": 5.1, "current": 0.0, "power": 0.0, "protocol": "none"},
        "C3": {"voltage": 27.9, "current": 1.4, "power": 39.0, "protocol": "PD"},
        "C4": {"voltage": 0.0, "current": 0.0, "power": 0.0, "protocol": "none"},
        "C5": {"voltage": 0.0, "current": 0.0, "power": 0.0, "protocol": "none"},
        "C6": {"voltage": 0.0, "current": 0.0, "power": 0.0, "protocol": "none"},
        "A1": {"voltage": 0.0, "current": 0.0, "power": 0.0, "protocol": "none"},
        "DC": {"voltage": 0.0, "current": 0.0, "power": 0.0, "protocol": "none"},
    },
    "total": 39.0,
    "brightness": 100,
    "sleep_time": 0,
    "charging_mode": "custom",
    "custom": [
        {"port": "C1", "limit": 15, "protocols": ["Apple5V/2.4A"], "mask": 1},
        {"port": "C2", "limit": 15, "protocols": ["Apple5V/2.4A"], "mask": 1},
        {"port": "C3", "limit": 140, "protocols": ["AFC"], "mask": 0x65},
        {"port": "C4", "limit": 60, "protocols": ["AFC"], "mask": 0x65},
        {"port": "C5", "limit": 60, "protocols": ["AFC"], "mask": 0x65},
        {"port": "C6+A", "limit": 0, "protocols": [], "mask": 0},
    ],
    "screensaver": True,
    "screensaver_theme": 1,
    "screensaver_flag": 0,
    "wallpaper": None,
    "wallpapers": [],
    "firmware": "1.2.1",
    "ssid": "a network",
    "ota": {"available": None, "progress": None, "module": None, "size": None},
    "custom_name": "Laptop Prio",
    "wallpaper_list": [],
}

DEVICE = {
    "productSerialNo": "030002",
    "deviceUniqueCode": "FF7J0000000000001",
    "deviceName": "UGREEN Nexode Pro X783",
    "deviceMac": "EC:1A:C3:00:00:01",
    "deviceType": "smart_charger",
    "extra": {"iotId": "an-iot-id", "networkStatus": 1, "onlineStatus": 1},
}

PAYLOAD = {
    "devices": [DEVICE],
    "detail": {DEVICE["deviceUniqueCode"]: {"name": "UGREEN Nexode Pro X783",
                                            "productNo": "X783",
                                            "image": "https://example.invalid/x783.png"}},
    "power": {DEVICE["deviceUniqueCode"]: READING},
    "power_errors": {},
}


@pytest.fixture(autouse=True)
def _event_loop_needs_a_socketpair(socket_enabled):
    """Let the loop build itself.

    pytest-homeassistant-custom-component blocks sockets, which is right: a
    test has no business dialling out. On Windows the event loop itself is
    built out of a TCP socketpair, so blocking that stops the tests before they
    start. Nothing here reaches a network -- both cloud clients are replaced
    and the shared session with them.
    """
    yield


@pytest.fixture(autouse=True)
def _custom_integrations(enable_custom_integrations):
    """Home Assistant will not load a custom component in tests without this."""
    yield


@pytest.fixture
def entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="an account",
        data={
            "email": "someone@example.invalid",
            "password": "not a real one",
            CONF_REGION: "europe",
        },
        options={CONF_PORT_DEVICES: True},
    )


@pytest.fixture
async def started(hass, entry: MockConfigEntry):
    """The integration, set up for real, with both clouds replaced.

    Only the two network clients are faked. Everything above them -- the
    coordinator, the platforms, the entity and device registries -- runs as it
    does on a real installation, which is the whole point.
    """
    entry.add_to_hass(hass)
    with (
        # The session is never used -- both clients are fakes -- but building a
        # real one opens a socket, and these tests are not allowed any.
        patch("custom_components.ugreen_connect_plus.async_get_clientsession"),
        patch("custom_components.ugreen_connect_plus.UgreenApi", autospec=True),
        patch("custom_components.ugreen_connect_plus.RtcxClient", autospec=True),
        patch(
            "custom_components.ugreen_connect_plus.coordinator.UgreenCoordinator._async_poll",
            AsyncMock(return_value=PAYLOAD),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry
