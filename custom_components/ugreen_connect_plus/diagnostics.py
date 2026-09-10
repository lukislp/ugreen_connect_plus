"""Diagnostics support for UGREEN Connect Plus."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from . import UgreenConfigEntry
from .const import DOMAIN
from .coordinator import device_key
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


def _frames(coordinator: Any, iot_id: str | None) -> dict[str, str]:
    """The frames seen for one charger, minus the one that names a network.

    The SSID reply carries the household's Wi-Fi name as plain ASCII inside the
    bytes, where redaction by key name cannot reach it. Every other frame is
    worth reading byte by byte; that one holds nothing a charger could be
    debugged with.
    """
    if iot_id is None:
        return {}
    seen = coordinator.rtcx.last_frames.get(iot_id) or {}
    return {name: value for name, value in seen.items() if name != SSID_FRAME}


def _charger_for(coordinator: Any, device: DeviceEntry) -> str | None:
    """Which charger this Home Assistant device belongs to.

    A port is a device of its own when the owner has asked for that, and its
    identifier is the charger's with the port appended -- so a diagnostics
    download taken from a port page answers about the charger behind it, which
    is the only thing that has frames.
    """
    for entry in coordinator.data.get("devices") or []:
        key = device_key(entry)
        if key is None:
            continue
        if any(
            domain == DOMAIN and (value == key or value.startswith(f"{key}_"))
            for domain, value in device.identifiers
        ):
            return key
    return None


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: UgreenConfigEntry, device: DeviceEntry
) -> dict[str, Any]:
    """The same, for one charger rather than for the whole account.

    Someone with two chargers reporting a problem with one of them should not
    have to hand over the other, and whoever reads it should not have to work
    out which half is relevant.
    """
    coordinator = entry.runtime_data
    raw = coordinator.data or {}
    key = _charger_for(coordinator, device)
    if key is None:
        return {"error": "this device does not belong to a charger in the account"}

    listed = next(
        (d for d in raw.get("devices") or [] if device_key(d) == key), {}
    )
    iot_id = (listed.get("extra") or {}).get("iotId")
    return {
        "device": async_redact_data(listed, TO_REDACT),
        "detail": async_redact_data((raw.get("detail") or {}).get(key) or {}, TO_REDACT),
        "reading": async_redact_data((raw.get("power") or {}).get(key) or {}, TO_REDACT),
        "error": (raw.get("power_errors") or {}).get(key),
        "frames": _frames(coordinator, iot_id),
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
        # The raw frames behind the readings above, per charger and keyed by
        # the question that was asked. On a charger this integration has never
        # seen, the decoded values are only as good as offsets established on a
        # different one -- these bytes are what someone else can check them
        # against, and what turns "my ports are called P1" into a model in the
        # table.
        "frames": {
            names.get(key, key): _frames(
                coordinator, ((listed.get("extra") or {}).get("iotId"))
            )
            for key, listed in (
                (device_key(d), d) for d in (coordinator.data or {}).get("devices") or []
            )
            if key is not None
        },
    }
