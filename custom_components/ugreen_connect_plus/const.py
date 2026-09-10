"""Constants for the UGREEN Connect Plus integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "ugreen_connect_plus"

# The wire format's own tables live with the codec that reads them; they are
# re-exported here so that nothing else has to know where the line was drawn.
from .protocol import (  # noqa: E402,F401  -- re-exported for the rest of the integration
    CUSTOM_PORTS,
    CUSTOM_PROTOCOLS,
    CUSTOM_SHARED_STEP,
    HANDSHAKE_PROTOCOL,
)

CONF_REGION: Final = "region"
CONF_LANGUAGE: Final = "language"

# Regional API endpoints, as published by /app/v1/system/country/list.
# `serverNodeCode` is what the app calls the region; the charger itself talks to
# a matching signalling host (europe -> eu-sig.ugreeniot.com).
REGIONS: Final[dict[str, str]] = {
    "europe": "https://api2.ugreeniot.com",
    "america": "https://api3.ugreeniot.com",
    "asia": "https://api1.ugreeniot.com",
    "china": "https://apicn.ugreeniot.com",
}
DEFAULT_REGION: Final = "europe"
DEFAULT_LANGUAGE: Final = "en-US"

# The API answers 200 OK for everything and signals real status in the body.
CODE_OK: Final = 100000
CODE_NO_PERMISSION: Final = 100003
CODE_MISSING_HEADER: Final = 100013
CODE_NO_SID: Final = 200010

# Every poll is two gateway calls plus a wait for the charger to answer, so this
# is a trade between a live-looking wattage and how hard someone else's cloud is
# leaned on. Five seconds suits watching a laptop charge; the options flow lets
# anyone who would rather be gentle raise it without touching the code.
DEFAULT_SCAN_INTERVAL: Final = 5
MIN_SCAN_INTERVAL: Final = 5
# Smallest pause between a reply and the next request. The poll period is
# measured start-to-start, so when a poll already overruns the configured
# period this is what stops it becoming a back-to-back loop against the cloud.
MIN_POLL_GAP: Final = 1.0
MAX_SCAN_INTERVAL: Final = 900
# Nothing is charging most of the time, and a charger with every port idle has
# nothing to say that five seconds apart is any better at catching. Backing off
# while that lasts is the difference between ~17,000 polls a day against
# someone else's cloud and a small fraction of it; the moment any port draws
# again the configured period is back.
IDLE_SCAN_FACTOR: Final = 6
IDLE_SCAN_MAX: Final = 60

# --- Charging sessions ------------------------------------------------------
# Watt-hours are what the charger actually delivers; milliamp-hours are what
# people think in. Converting between them needs a battery voltage and a
# conversion loss, neither of which the charger can know, so both are options.
# 3.85 V is the nominal cell voltage of essentially every phone and earbud; a
# laptop on USB-PD has a far higher pack voltage, and its milliamp-hour figure
# is meaningless until this is set to match.
CONF_NOMINAL_VOLTAGE: Final = "nominal_voltage"
CONF_EFFICIENCY: Final = "efficiency"
DEFAULT_NOMINAL_VOLTAGE: Final = 3.85
DEFAULT_EFFICIENCY: Final = 90

# A reading is only continuous with the one before it if it arrived roughly on
# schedule; this multiple of the poll period is where "roughly" stops.
SESSION_GAP_FACTOR: Final = 4

# How long a port has to draw nothing before its charging session counts as over.
# The charger cannot say whether a device is still attached -- it holds the port live
# for a bare cable -- so this is the only thing that can end a session. See session.py
# for what it is trading off.
CONF_IDLE_END: Final = "session_idle_end"
DEFAULT_IDLE_END: Final = 120  # minutes

# --- RTCX/Polaris gateway (live telemetry) ---------------------------------
# The gateway envelope uses an underscore locale, unlike the account API header.
GATEWAY_LANGUAGE: Final = "en_US"
# `platform` must be exactly this; `android`/`iot` return data: null.
APP_INFO_PLATFORM: Final = "rtcx"
# The gateway reports success as 200 in the body, not the API's 100000.
GATEWAY_OK: Final = 200
# iotTokens last 24 h; renew this far ahead of expiry.
RTCX_TOKEN_MARGIN: Final = 300
# The charger answers a PT_data query asynchronously, so the reply has to be
# waited for -- and each look is a request of its own, which is why this is a
# short wait that grows rather than a fast poll. A charger that answers
# quickly is not made to wait two seconds for the privilege; one that is slow
# costs no more requests than it used to.
PT_DATA_WAITS: Final[tuple[float, ...]] = (0.8, 1.5, 2.5)

# The screen settings only change when someone opens the app, and asking for
# them costs a round trip of its own. Read on a timer instead of beside every
# wattage; a setting written from here marks them stale at once, so nobody
# waits this out to see their own change.
DEVICE_STATE_INTERVAL: Final = 60

# How long the readings may go without a whole poll before Home Assistant is
# told. A quiet minute is this cloud's ordinary behaviour and worth no alarm;
# an hour is not. The distance between the two is the point: a notice that
# cries wolf is one people learn to close without reading.
STALE_AFTER: Final = 3600
# PT_data keeps its last value indefinitely, so anything older than this is
# treated as "no reading" rather than as a live one.
PT_DATA_MAX_AGE: Final = 300

# How far a reading that did arrive may be carried when the next one does not.
#
# The charger answers into a single cloud property, so anything else asking at
# the same moment -- a second Home Assistant, the phone app, the charger's own
# app being opened -- can take the reply meant for this one. Watched happening:
# with a second instance polling the same charger, every entity of it blinked
# out and back roughly every ten seconds, while every poll reported success.
#
# Two misses in a row and a minute are both deliberately short. This is here to
# cover the gap of a reply going astray, not to keep a wattage on screen for a
# charger that has been unplugged -- past either bound, "unavailable" is the
# honest answer again.
RETAIN_MISSES: Final = 2
RETAIN_SECONDS: Final = 60

# The charger's screen, in pixels. Its stock pictures are stored rotated, but
# what the app uploads is this way round.
WALLPAPER_SIZE: Final[tuple[int, int]] = (560, 170)



# Firmware version and SSID never change between polls; re-read them rarely.
STATIC_INFO_INTERVAL: Final = 3600

# Charging presets. "custom" is left out on purpose: it needs the 35 parameter
# bytes the presets leave at zero, and those are only meaningful alongside the
# app's own mode editor.
# Named as the app names them, so the two agree on screen.
CHARGING_MODES: Final[dict[int, str]] = {
    0: "adaptive_power",
    1: "thermal_safe",
    2: "dc_turbo",
    3: "priority",
    4: "custom",
}
# What may be asked for. "custom" is missing because setting it would mean
# sending the 35 parameter bytes the app's editor fills in, and inventing those
# would overwrite whatever the owner configured there.
#
# "dc_turbo" is here because the charger does accept the command -- but it does
# not always act on it. Asked for it on an X783 with nothing on the DC port,
# the frame went through and the mode stayed where it was. Nothing reports the
# refusal, which is why every write reads the charger back rather than trusting
# what it asked for.
SELECTABLE_MODES: Final[tuple[str, ...]] = (
    "adaptive_power", "thermal_safe", "dc_turbo", "priority",
)

# C6 and A are one setting but two sockets. Each socket carries it, so that a
# port's page answers "what is this port allowed" without sending anyone
# elsewhere; the name says which other port the figure is shared with.
CUSTOM_SHARED_GROUP: Final = "C6+A"
CUSTOM_SHARED_MEMBERS: Final[dict[str, str]] = {"C6": "A1", "A1": "C6"}
# The charger holds a custom mode's numbers but not what the owner called it;
# the name lives in the account beside a copy of the same figures. Re-read on
# a timer, since renaming one is a thing people do rarely and by hand.
SMART_MODE_INTERVAL: Final = 900


# The two bytes after the screensaver's on/off flag. Both were settled by
# changing them in the app and reading the frame it sent: picking 12- or 24-hour
# moves the first, and Clock Style 1 / 2 moves the second. (An earlier guess had
# the first as a clock position, which it is not.)
TIME_FORMATS: Final[dict[int, str]] = {0: "12h", 1: "24h"}
CLOCK_STYLES: Final[dict[int, str]] = {0: "style_1", 1: "style_2"}

# Screen Off Time is plain minutes; zero means the screen never sleeps. These
# are the app's own choices, confirmed by tapping each one and reading the frame
# it sent -- note it offers 10 minutes, not 15. The device itself takes any
# value up to 255, so more can be added here without touching anything else.
SLEEP_NEVER: Final = 0
SLEEP_OPTIONS: Final[dict[str, int]] = {
    "1_min": 1,
    "5_min": 5,
    "10_min": 10,
    "30_min": 30,
    "always_on": SLEEP_NEVER,
}

# Links to uploaded wallpapers are signed and last about ten minutes, which is
# why previews are served through the integration rather than pointed at the
# CDN -- the list itself only has to be current enough to name what the charger
# is showing.
WALLPAPER_LIST_INTERVAL: Final = 900
# ...and how often to go looking when the charger names a picture the library
# has never mentioned, which is what a picture uploaded from the phone app looks
# like. Rate limited, because a picture that has been replaced in the library
# stays on the charger and would otherwise be chased on every poll.
WALLPAPER_MISS_INTERVAL: Final = 60

# How long to let the charger download a picture before pointing the screensaver
# at it.
PICTURE_SETTLE_SECONDS: Final = 5

# Dumped next to configuration.yaml on every refresh while `debug_dump` is on.
# It is the raw, unmodified cloud payload and is what the entity layer is built
# from -- see the integration README.
DEBUG_DUMP_FILE: Final = "ugreen_connect_plus_debug.json"
CONF_DEBUG_DUMP: Final = "debug_dump"

# One Home Assistant device per socket, or one for the whole charger. A port is
# what people reason about -- "what is C3 doing" -- and eight of them with six
# readings each turn one device page into a list of fifty. But it is also eight
# more rows in every device list and every area, and whether that trade is
# worth making is not something this can decide for anyone.
CONF_PORT_DEVICES: Final = "port_devices"
DEFAULT_PORT_DEVICES: Final = True
