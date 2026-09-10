"""Polling coordinator for UGREEN Connect."""

from __future__ import annotations

import json
import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import UgreenApi, UgreenAuthError, UgreenError
from .const import (
    CONF_IDLE_END,
    DEBUG_DUMP_FILE,
    DEFAULT_IDLE_END,
    DEFAULT_SCAN_INTERVAL,
    DEVICE_STATE_INTERVAL,
    DOMAIN,
    IDLE_SCAN_FACTOR,
    IDLE_SCAN_MAX,
    MIN_POLL_GAP,
    RETAIN_MISSES,
    RETAIN_SECONDS,
    SESSION_GAP_FACTOR,
    SMART_MODE_INTERVAL,
    STALE_AFTER,
    STATIC_INFO_INTERVAL,
    WALLPAPER_LIST_INTERVAL,
    WALLPAPER_MISS_INTERVAL,
)

# Straight from the protocol module rather than through rtcx: a name
# re-exported by a module that does not use it is one a linter will remove.
from .protocol import PORTS_BY_MODEL, QUERY_GET_WIFI_SSID, STATE_FIELDS_ALL, state_fields
from .rtcx import RtcxClient
from .session import MAX_GAP, SessionTracker

_LOGGER = logging.getLogger(__name__)


class UgreenCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch the account's devices and, for each, whatever detail the cloud gives."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: UgreenApi,
        rtcx: RtcxClient,
        *,
        debug_dump: bool = True,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
            config_entry=entry,
        )
        # What the option means to the user: one reading every N seconds. The
        # interval handed to the coordinator is only the gap that is left after
        # a poll, and `_reschedule` keeps the two in step.
        self._target_period = float(
            entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        self.api = api
        self.rtcx = rtcx
        # Readings are only continuous with each other if they keep to the poll
        # period, so what counts as a hole has to follow the configured interval
        # rather than a fixed number of seconds.
        self.sessions = SessionTracker(
            max_gap=max(MAX_GAP, self._target_period * SESSION_GAP_FACTOR),
            idle_end=entry.options.get(CONF_IDLE_END, DEFAULT_IDLE_END) * 60,
        )
        # When a poll last came back whole, published as a sensor of its own.
        self.last_success: float | None = None
        # Whether the last poll found any port drawing, which decides how soon
        # the next one is due.
        self._drawing = True
        self._debug_dump = debug_dump
        self._dumped = False
        self._power_errors: dict[str, str] = {}
        # The last reading that arrived whole, per charger, and how many polls
        # have come up empty since.
        self._good: dict[str, tuple[dict[str, Any], float]] = {}
        self._misses: dict[str, int] = {}
        self._static: dict[str, tuple[dict[str, Any], float]] = {}
        self._wallpaper_cache: dict[str, tuple[list[dict[str, Any]], float]] = {}
        self._wallpaper_missed: dict[str, float] = {}
        self._modes: dict[str, tuple[list[dict[str, Any]], float]] = {}
        self._products: dict[str, Any] = {}
        # Chargers already told about, so the notice is written once rather
        # than on every poll.
        self._noted: set[str] = set()
        self._state: dict[str, tuple[dict[str, Any], float]] = {}

    async def _async_update_data(self) -> dict[str, Any]:
        started = time.monotonic()
        try:
            data = await self._async_poll()
            # Only after a poll has come back whole: the point of publishing
            # this is to say how stale the readings are while the cloud is
            # away, and this cloud goes away for minutes at a time.
            self.last_success = time.time()
            return data
        finally:
            self._reschedule(time.monotonic() - started)
            self._flag_staleness()

    def _reschedule(self, elapsed: float) -> None:
        """Keep a steady poll *period*, not a steady gap between polls.

        The coordinator counts its interval from the moment a poll finishes, so
        the real period is interval + however long the poll took -- and a poll
        here is never quick: it writes a query into the charger's ``PT_data``
        and then waits for the device to answer. Asking every 5 s therefore
        produced a reading only every ~9 s.

        Subtracting the time already spent makes the configured value mean what
        it looks like it means: a reading every N seconds. `MIN_POLL_GAP` keeps
        a slow or silent device from turning that into back-to-back requests,
        which is the one way this could make things worse rather than better.
        """
        period = self._target_period
        if not self._drawing:
            period = min(period * IDLE_SCAN_FACTOR, IDLE_SCAN_MAX)
            # ...unless the owner already asked for something slower.
            period = max(period, self._target_period)
        gap = max(MIN_POLL_GAP, period - elapsed)
        wanted = timedelta(seconds=gap)
        if self.update_interval != wanted:
            self.update_interval = wanted

    async def _async_poll(self) -> dict[str, Any]:
        try:
            devices = await self.api.get_devices()
        except UgreenAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except UgreenError as err:
            raise UpdateFailed(str(err)) from err

        # Product metadata rarely changes, so it is fetched once and cached.
        for device in devices:
            serial = device.get("productSerialNo")
            key = device_key(device)
            if key is None or not serial or key in self._products:
                continue
            try:
                self._products[key] = await self.api.get_product_model(serialNo=serial)
            except UgreenError as err:
                _LOGGER.debug("product model for %s failed: %s", serial, err)

        # Live readings come from a different cloud (the RTCX gateway) and are
        # per-device, so a failure there must not take the inventory down with
        # it -- the connectivity sensors stay useful either way.
        power: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for device in devices:
            key = device_key(device)
            iot_id = (device.get("extra") or {}).get("iotId")
            if key is None or not iot_id:
                continue
            try:
                # productNo is the account API's name for the model, and it
                # is what decides how many ports the report has and what they
                # are called.
                model = (self._products.get(key) or {}).get("productNo")
                self._note_support(key, model)
                power[key] = await self.rtcx.async_power(iot_id, model)
                if power[key] is None:
                    errors[key] = "device returned no usable PT_data frame"
                    power[key] = self._carry(key)
                else:
                    power[key].update(await self._device_state(key, iot_id, model))
                    power[key].update(await self._static_info(key, iot_id))
                    power[key]["ota"] = self.rtcx.ota_state()
                    power[key]["custom_name"] = await self._custom_mode_name(
                        device, key, power[key].get("custom")
                    )
                    # A picture uploaded from the phone app is on the charger the
                    # moment it is chosen, while the library was last read up to
                    # a quarter of an hour ago and has never heard of it. Seeing
                    # an id that cannot be named is the signal to look again,
                    # rather than leaving the card blank until the timer comes
                    # round.
                    power[key]["wallpaper_list"] = await self._name_current(
                        device, key, await self._wallpapers(device),
                        power[key].get("wallpaper"),
                    )
                    # Only a reading with everything in it is worth carrying
                    # into a poll that comes back empty.
                    self._good[key] = (power[key], time.time())
                    self._misses[key] = 0
            except UgreenError as err:
                # Warn rather than debug: without this the entities simply never
                # appear, with nothing anywhere saying why.
                if self._power_errors.get(key) != str(err):
                    _LOGGER.warning("Live power unavailable for %s: %s", key, err)
                errors[key] = str(err)
                power[key] = self._carry(key)
        self._power_errors = errors
        # A poll that failed says nothing about whether anything is charging,
        # so an outage keeps the fast rate rather than quietly slowing down
        # exactly when someone is watching for the charger to come back. A
        # carried reading is a failed poll wearing the last answer's clothes,
        # and counts the same way.
        self._drawing = not power or any(
            reading is None or reading.get("carried_for") or reading.get("total")
            for reading in power.values()
        )

        # Only readings that actually arrived are folded in: a failed poll has to
        # leave every session untouched, or an outage would read as an unplug.
        stamp = time.time()
        for key, reading in power.items():
            # A carried reading is the previous one shown again rather than a
            # new measurement, so it must not be integrated: doing so would
            # invent energy across exactly the gap where none was measured.
            if reading and not reading.get("carried_for"):
                self.sessions.update(stamp, key, reading["ports"])

        data = {
            "devices": devices,
            "detail": self._products,
            "power": power,
            "power_errors": errors,
        }

        if self._debug_dump:
            await self.hass.async_add_executor_job(self._write_dump, data)

        return data

    def _flag_staleness(self) -> None:
        """Tell Home Assistant when the readings have gone properly old.

        Every entity keeps its last value while the cloud is unreachable, and
        nothing about a dashboard says whether a number arrived a second ago
        or last Tuesday. The timestamp sensor answers that for anyone who
        looks; this is for everyone who does not.
        """
        gone = (
            self.last_success is None
            or time.time() - self.last_success > STALE_AFTER
        )
        if not gone or self.last_success is None:
            ir.async_delete_issue(self.hass, DOMAIN, "readings_stale")
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            "readings_stale",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="readings_stale",
            translation_placeholders={
                "minutes": str(round((time.time() - self.last_success) / 60)),
            },
        )

    async def _device_state(
        self, key: str, iot_id: str, model: str | None
    ) -> dict[str, Any]:
        """The screen settings and the custom mode, on their own slow timer.

        They only change when someone opens the app, and asking costs a round
        trip of its own -- so asking beside every wattage doubles the traffic
        for an answer that is the same one poll after poll. Reading them once
        a minute halves it.

        Except when this has just written to the charger: then the copy is
        known to be out of date, and waiting out the timer would mean watching
        one's own change take a minute to appear.
        """
        cached, fetched_at = self._state.get(key, ({}, 0.0))
        recent = cached and time.time() - fetched_at < DEVICE_STATE_INTERVAL
        if recent and not self.rtcx.state_is_stale(iot_id):
            return cached
        state = await self.rtcx.async_device_state(iot_id, model)
        if state is None:
            # A reply that did not arrive says nothing about what the settings
            # are; the last ones that did are still the best answer.
            return cached
        self.rtcx.state_was_read(iot_id)
        self._state[key] = (state, time.time())
        return state

    def _carry(self, key: str) -> dict[str, Any] | None:
        """The last reading that did arrive, while it is still worth showing.

        A reply going missing is ordinary rather than exceptional. The charger
        answers into one cloud property, and anything else asking at the same
        moment can take the answer meant for this poll -- a second Home
        Assistant against the same charger made every entity of it blink out
        and back every few seconds, while every poll reported success.

        Blanking a charger for one cycle reads like the device fell off the
        shelf, and none of what it says is any less true for being a few
        seconds old. So the previous reading is shown again -- but marked, and
        only across the gap: two misses or a minute, whichever comes first,
        and then unavailable is the honest answer.
        """
        reading, arrived = self._good.get(key, (None, 0.0))
        self._misses[key] = misses = self._misses.get(key, 0) + 1
        age = time.time() - arrived
        if reading is None or misses > RETAIN_MISSES or age > RETAIN_SECONDS:
            return None
        # Everything downstream tells a carried reading from a fresh one by
        # this: the session tracker refuses to integrate it, the poll timer
        # treats it as the failure it stands in for, and diagnostics say how
        # old it is.
        return reading | {"carried_for": round(age, 1)}

    async def async_read_back(self, key: str, iot_id: str) -> None:
        """Publish what the charger did with a write, not what it was asked.

        A setting can be declined without anything saying so: the frame is
        accepted, and the state simply does not change. DC turbo is declined
        on an X783 unless the DC port has something on it -- watched happening,
        not supposed. An entity that wrote its own request into the reading and
        called it current would then show a value the charger never held, until
        a state read next came round. Idle polling stretches that to a minute,
        which is exactly when someone is sitting in front of the settings.

        The write has already marked the cached state stale, so this costs one
        round trip and no request the next poll would not have made anyway.
        """
        reading = ((self.data or {}).get("power") or {}).get(key)
        if reading is None:
            return
        model = (self._products.get(key) or {}).get("productNo")
        reading.update(await self._device_state(key, iot_id, model))
        self.async_update_listeners()

    async def _static_info(self, key: str, iot_id: str) -> dict[str, Any]:
        """Firmware version and SSID -- cached, since each costs a round trip to
        the device and neither changes between polls."""
        cached, fetched_at = self._static.get(key, ({}, 0.0))
        if cached and time.time() - fetched_at < STATIC_INFO_INTERVAL:
            return cached
        info = {
            "firmware": await self.rtcx.async_firmware_version(iot_id),
            "ssid": await self.rtcx.async_text_query(iot_id, QUERY_GET_WIFI_SSID),
        }
        # Keep whatever was already known if the device declined to answer.
        info = {k: v if v is not None else cached.get(k) for k, v in info.items()}
        self._static[key] = (info, time.time())
        return info

    def _note_support(self, key: str, model: str | None) -> None:
        """Say once what a charger of this model gets, and what it does not.

        Silence would be the wrong answer here: someone whose charger shows
        ports called P1..P4 and no screen settings deserves to be told that
        this is deliberate and what would change it, rather than left to
        conclude the integration is broken.
        """
        if model is None or key in self._noted:
            return
        self._noted.add(key)
        if model in PORTS_BY_MODEL and state_fields(model) == STATE_FIELDS_ALL:
            return
        if model in PORTS_BY_MODEL:
            _LOGGER.warning(
                "%s: port readings and their names are known for this model, but its "
                "screen settings have never been read on one, so those entities are "
                "left out rather than guessed at. A diagnostics download on the "
                "issue tracker is what would change that",
                model,
            )
            return
        _LOGGER.warning(
            "%s is a model this has not seen: its ports are counted from the "
            "charger's own report and numbered P1 upwards, and the screen settings "
            "and custom mode are left out rather than read from offsets that were "
            "established on a different charger. Readings are unaffected. A "
            "diagnostics download on the issue tracker is what would change that",
            model,
        )

    async def _custom_mode_name(
        self, device: dict[str, Any], key: str, groups: list[dict[str, Any]] | None
    ) -> str | None:
        """What the owner called the custom mode the charger is running.

        The charger carries the mode's numbers and not its name -- the name is
        the account's, stored beside a copy of the same figures. So the mode is
        recognised by matching those rather than assumed, which is what makes
        this right for someone who keeps several.
        """
        if not groups:
            return None
        cached, fetched_at = self._modes.get(key, ([], 0.0))
        if not cached or time.time() - fetched_at > SMART_MODE_INTERVAL:
            try:
                cached = await self.api.get_smart_modes(
                    deviceUniqueCode=device["deviceUniqueCode"],
                    productSerialNo=device["productSerialNo"],
                ) or []
            except (UgreenError, KeyError) as err:
                _LOGGER.debug("smart modes for %s failed: %s", key, err)
                return None
            self._modes[key] = (cached, time.time())

        limits = {group["port"]: group["limit"] for group in groups}
        for mode in cached:
            listed = {
                port.get("portName"): port.get("portPower")
                for port in mode.get("portList") or []
            }
            # A group set to nothing is left out of the account's copy, so it
            # has to match by being absent rather than by being zero.
            if all(limits.get(name) == watts for name, watts in listed.items()) and all(
                not watts for name, watts in limits.items() if name not in listed
            ):
                return mode.get("modeName")
        # The account may name a shared group differently from the frame, and
        # then nothing matches. With one mode stored there is no ambiguity
        # about which it is, so say its name rather than none at all.
        return cached[0].get("modeName") if len(cached) == 1 else None

    async def _wallpapers(self, device: dict[str, Any]) -> list[dict[str, Any]]:
        """The pictures available for this charger, with preview URLs.

        The dashboard card needs somewhere to point an <img> at, and the device
        itself only ever names pictures by a six-character id. Links to uploads
        are signed and expire, so this is re-read on a timer rather than cached
        for the session.
        """
        key = device_key(device) or ""
        cached, fetched_at = self._wallpaper_cache.get(key, ([], 0.0))
        if cached and time.time() - fetched_at < WALLPAPER_LIST_INTERVAL:
            return cached
        try:
            items = await self.api.get_wallpapers(
                device["deviceUniqueCode"], device["productSerialNo"]
            )
        except (UgreenError, KeyError) as err:
            _LOGGER.debug("wallpaper list for %s failed: %s", key, err)
            return cached
        listed = [
            {
                "id": item.get("fileNameMd5"),
                "url": item.get("url"),
                "name": item.get("fileName"),
                "size": item.get("fileSize"),
                "stock": item.get("stock", True),
            }
            for item in items
            if item.get("fileNameMd5")
        ]
        self._wallpaper_cache[key] = (listed, time.time())
        return listed

    async def _name_current(
        self,
        device: dict[str, Any],
        key: str,
        listed: list[dict[str, Any]],
        current: str | None,
    ) -> list[dict[str, Any]]:
        """Re-read the library when the charger shows a picture it does not list.

        Not every unknown id can be found -- a custom picture that has since been
        replaced in the library stays on the charger but is gone from the
        account -- so the look-up is rate limited, or a picture like that would
        have this fetching the library on every single poll.
        """
        if not current or any(item.get("id") == current for item in listed):
            return listed
        if time.time() - self._wallpaper_missed.get(key, 0.0) < WALLPAPER_MISS_INTERVAL:
            return listed
        self._wallpaper_missed[key] = time.time()
        self._wallpaper_cache.pop(key, None)
        return await self._wallpapers(device)

    async def async_wallpaper_url(self, image_id: str, *, refresh: bool = False) -> str | None:
        """The signed link for one picture, optionally re-read from the account.

        Links live about ten minutes, so whatever was cached for the card is
        usually past it by the time a browser asks. `refresh` throws the cached
        list away and fetches the library again, which is what the image view
        does before giving up on a picture.
        """
        if refresh:
            self._wallpaper_cache.clear()
            for device in self.data.get("devices", []):
                reading = (self.data.get("power") or {}).get(device_key(device) or "")
                if reading is not None:
                    reading["wallpaper_list"] = await self._wallpapers(device)
        for reading in (self.data.get("power") or {}).values():
            for item in (reading or {}).get("wallpaper_list") or []:
                if item.get("id") == image_id and item.get("url"):
                    return item["url"]
        return None

    def _write_dump(self, data: dict[str, Any]) -> None:
        """Write one raw snapshot so the entity layer can be built from real data."""
        path = self.hass.config.path(DEBUG_DUMP_FILE)
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
        except OSError as err:
            _LOGGER.warning("Could not write %s: %s", path, err)
        else:
            _LOGGER.info("Wrote raw UGREEN cloud snapshot to %s", path)


def device_ports(coordinator: UgreenCoordinator, key: str) -> tuple[str, ...]:
    """The ports this charger's own report carries, in the order it sends them.

    Taken from the reading rather than from a table, so a model nobody here
    has ever seen still gets an entity per port.
    """
    reading = (coordinator.data.get("power") or {}).get(key) or {}
    return tuple(reading.get("ports") or ())


def device_key(device: dict[str, Any]) -> str | None:
    """Stable per-device identifier.

    `deviceUniqueCode` is the serial the cloud keys everything on; `iotId` is the
    Alibaba-style `<productKey><deviceName>` pair and serves as a fallback.
    """
    if value := device.get("deviceUniqueCode"):
        return str(value)
    if value := (device.get("extra") or {}).get("iotId"):
        return str(value)
    return None
