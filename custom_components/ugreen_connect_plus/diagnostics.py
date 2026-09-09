"""Diagnostics support for UGREEN Connect."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from . import UgreenConfigEntry
from .protocol import FRAME_QUERY, QUERY_GET_WIFI_SSID

# The README asks people to attach this file to a public issue, so the bar is
# not "no credentials" but "nothing that belongs to the household". What makes
# an unknown charger supportable is its model name and its frames; its serial
# number, its MAC and its cloud id identify the one on someone's desk and say
# nothing about the model, so they go.
TO_REDACT = {
    CONF_EMAIL,
    CONF_PASSWORD,
    "accessToken",
    "refreshToken",
    "token",
    "sid",
    "deviceMac",
    "deviceUniqueCode",
    "iotId",
    # The name of the network the charger is joined to, which is the household's
    # own -- read for the diagnostic sensor, and out of place in a file meant to
    # travel.
    "ssid",
}

# Same reasoning, one level up: the cloud payload is keyed by the device code,
# and async_redact_data only ever looks at values.
KEYED_BY_DEVICE = ("detail", "power", "power_errors")

# The same name once more, and redacting the value above would mean little
# while the bytes it was decoded from sit two keys further down as plain ASCII.
# Every other frame is worth reading byte by byte; this one carries nothing a
# charger could be debugged with.
SSID_FRAME = f"{FRAME_QUERY:02X}/{QUERY_GET_WIFI_SSID}"


def _stand_ins(data: dict[str, Any]) -> dict[str, str]:
    """Map each device code to a name that keeps the payload cross-referenceable."""
    return {
        str(code): f"device_{index}"
        for index, device in enumerate(data.get("devices") or [])
        if (code := device.get("deviceUniqueCode"))
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: UgreenConfigEntry
) -> dict[str, Any]:
    """Return the raw cloud payload with anything identifying removed."""
    coordinator = entry.runtime_data
    data = async_redact_data(coordinator.data or {}, TO_REDACT)
    names = _stand_ins(coordinator.data or {})
    for key in KEYED_BY_DEVICE:
        if isinstance(section := data.get(key), dict):
            data[key] = {names.get(code, code): value for code, value in section.items()}
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "data": data,
        # The raw frames behind the readings above, keyed by the question that
        # was asked. On a charger this integration has never seen, the decoded
        # values are only as good as offsets established on a different one --
        # these bytes are what someone else can check them against, and what
        # turns "my ports are called P1" into a model in the table.
        "frames": {
            name: value
            for name, value in coordinator.rtcx.last_frames.items()
            if name != SSID_FRAME
        },
    }
