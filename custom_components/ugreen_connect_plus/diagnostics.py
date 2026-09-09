"""Diagnostics support for UGREEN Connect."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from . import UgreenConfigEntry

TO_REDACT = {CONF_EMAIL, CONF_PASSWORD, "accessToken", "refreshToken", "token", "sid"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: UgreenConfigEntry
) -> dict[str, Any]:
    """Return the raw cloud payload with credentials removed."""
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "data": async_redact_data(coordinator.data or {}, TO_REDACT),
        # The raw frames behind the readings above, keyed by the question that
        # was asked. On a charger this integration has never seen, the decoded
        # values are only as good as offsets established on a different one --
        # these bytes are what someone else can check them against, and what
        # turns "my ports are called P1" into a model in the table.
        "frames": coordinator.rtcx.last_frames,
    }
