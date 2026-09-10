"""The charger's own protocol: frames in, readings out.

Kept clear of Home Assistant and of aiohttp on purpose, exactly as
``session.py`` is. Every byte offset in here was established by hand against a
live charger -- written to and read back, not inferred -- and that is precisely
the kind of knowledge that a test suite has to be able to reach without an
installation standing in the way.

The frame::

    TYPE(1) CMD(1) LEN(2, big endian) PAYLOAD(LEN) CRC16(2, MODBUS, low byte first)

The same frames travel over the cloud's ``PT_data`` property and, unchanged,
over the charger's own Bluetooth characteristic -- which is what makes keeping
them here rather than beside the transport worth the separate module.
"""

from __future__ import annotations

import logging
from typing import Any, Final, NamedTuple

_LOGGER = logging.getLogger(__name__)

# Port order of the power report on the Nexode Pro 300W, from the app's own port
# table. X783 is what the app calls that model internally, and is kept here
# because a second model would need its own list beside this one.
# The order the power report puts its ports in, per model. `productNo` is what
# the account API calls the model, and it is fetched already for the device
# page, so knowing which list to use costs nothing extra.
PORTS_BY_MODEL: Final[dict[str, tuple[str, ...]]] = {
    # Read off the app's own port table, and confirmed against a charger.
    "X783": ("C1", "C2", "C3", "C4", "C5", "C6", "A1", "DC"),
    # Nexode Pro 160W, confirmed against one by its owner: devices were put on
    # the built-in cable and on C2, and records 0 and 2 -- and only those --
    # carried voltage. Its report is 32 bytes, four records and four protocol
    # bytes, so the eighth byte the X783 leaves off is that model's quirk
    # rather than the protocol's habit.
    "X776": ("C-Cable", "C1", "C2", "A"),
}

# The name the rest of the integration knew this list by.
X783_PORTS: Final[tuple[str, ...]] = PORTS_BY_MODEL["X783"]

# Which fields of the GET_DEVICE_STATE reply have been read on real hardware,
# per model.
#
# Deliberately not the same list as the ports above, and the difference is the
# point. How many ports a report describes can be counted from its length, so
# readings work anywhere. Where brightness, the screen timeout, the charging
# mode and the screensaver sit cannot be counted -- they are offsets,
# established by changing a value in the app and watching which byte moved. On
# a model laid out differently they would read something plausible and wrong,
# and every one of these entities writes back as well as reads.
#
# Per field rather than per model, because a model does not arrive understood
# all at once. The X776's owner mapped its brightness and its screen timeout by
# hand and found them at the same offsets as the X783's; the rest of its
# 59-byte reply is arranged differently and is still being worked out. Under an
# all-or-nothing rule that knowledge would have to sit unused until the last
# byte fell, which is a poor trade for the owner of that charger.
STATE_FIELDS_ALL: Final[frozenset[str]] = frozenset(
    {
        "brightness",
        "sleep_time",
        "charging_mode",
        "custom",
        "screensaver",
        "screensaver_theme",
        "screensaver_flag",
        "wallpaper",
        "wallpapers",
    }
)

STATE_FIELDS_BY_MODEL: Final[dict[str, frozenset[str]]] = {
    "X783": STATE_FIELDS_ALL,
    # Mapped on a live 160W by its owner, one change at a time, in
    # https://github.com/s1mptom/ugreen_connect/issues/2. Brightness, the
    # timeout and the charging mode sit exactly where the X783 keeps them; the
    # screensaver group and the wallpaper follow nine bytes earlier, because
    # the parameter block between them is 26 bytes rather than 35.
    #
    # `wallpapers` is missing on purpose. The byte where the X783 counts its
    # library reads 5 on that charger whether three ids follow or four, so
    # whatever it counts, it is not them.
    "X776": frozenset(
        {
            "brightness",
            "sleep_time",
            "charging_mode",
            "screensaver",
            "screensaver_theme",
            "screensaver_flag",
            "wallpaper",
        }
    ),
}

# Reading a byte and writing it are separate permissions, because the commands
# are not symmetrical. Brightness and the screen timeout are set by a command
# carrying one byte, so knowing where to read them is knowing how to set them.
# The charging mode is not: its command carries the whole parameter block, and
# the 160W's block is nine bytes shorter than the one that shape was learned
# on. The screensaver's command carries a block of its own.
#
# So a field is writable where a write has actually been made and read back.
# Everything else can be shown and not set, which is a better answer than
# either hiding it or sending a frame nobody has tried.
STATE_WRITABLE_BY_MODEL: Final[dict[str, frozenset[str]]] = {
    "X783": STATE_FIELDS_ALL,
    "X776": frozenset({"brightness", "sleep_time"}),
}


class StateLayout(NamedTuple):
    """Where the tail of a state reply sits on one model.

    Brightness, the screen timeout and the charging mode are at 2, 3 and 4 on
    both chargers seen so far, so they stay constants. Everything after the
    custom parameter block moves with its length, which is what this carries.
    ``wallpaper_count`` is None where that byte has been seen and not
    understood -- reading a list from a count that does not count is worse than
    publishing no list.
    """

    screensaver: int          # then clock style at +1 and time format at +2
    image_id: int             # six ASCII bytes naming the picture on screen
    wallpaper_count: int | None


STATE_LAYOUT_BY_MODEL: Final[dict[str, StateLayout]] = {
    "X783": StateLayout(screensaver=40, image_id=43, wallpaper_count=49),
    "X776": StateLayout(screensaver=31, image_id=34, wallpaper_count=None),
}


def state_layout(model: str | None) -> StateLayout:
    """Where to read this model's screen settings; the X783's where unknown."""
    return STATE_LAYOUT_BY_MODEL.get(model or "", STATE_LAYOUT_BY_MODEL["X783"])


def state_writable(model: str | None) -> frozenset[str]:
    """Which of this model's state fields may be set as well as read."""
    if model is None:
        return STATE_FIELDS_ALL
    return STATE_WRITABLE_BY_MODEL.get(model, frozenset())


def state_fields(model: str | None) -> frozenset[str]:
    """Which parts of a state reply may be believed on this model.

    A charger whose model the account API would not name is read in full, as it
    always has been: that is the charger this was written on far more often
    than it is a stranger, and the alternative is an integration that loses its
    screen the moment one cloud call fails.
    """
    if model is None:
        return STATE_FIELDS_ALL
    return STATE_FIELDS_BY_MODEL.get(model, frozenset())


def state_is_readable(model: str | None) -> bool:
    """Whether any of this model's state reply can be trusted to mean what it says.

    Refusal needs positive evidence that the charger is a different one, which
    is why an unknown model is still read in full rather than refused.
    """
    return bool(state_fields(model))


def ports_for(model: str | None, body_length: int) -> tuple[str, ...]:
    """What to call each port of a report this long, on this model.

    An unknown model still gets its readings: seven bytes of measurement and
    up to one protocol byte per port means the report's own length says how
    many there are. Only the names are lost, and numbered ports are honest
    about that -- better than one model's labels on another model's sockets.

    "Up to" matters. The X783 sends 63 bytes for eight ports: 56 of
    measurement and only seven protocol bytes, the last one simply absent. So
    the count is taken as high as the protocol block allows and no higher than
    the measurements can fill.
    """
    if known := PORTS_BY_MODEL.get(model or ""):
        return known
    by_protocol = -(-body_length // (PORT_RECORD + 1))   # aufgerundet
    by_measurement = body_length // PORT_RECORD
    return tuple(f"P{index + 1}" for index in range(min(by_protocol, by_measurement)))
# The charging protocol each port negotiated, reported one byte per port at the
# tail of the power frame.
HANDSHAKE_PROTOCOL: Final[dict[int, str]] = {
    0: "none", 1: "QC", 2: "AFC", 3: "FCP", 4: "UFCS", 5: "PD", 6: "PPS", 7: "AVS",
}
# --- The custom mode's parameter block --------------------------------------
# The 35 bytes the presets leave at zero. Settled against the app's own editor
# on a live X783: five ports carry a plain wattage, C6 and A share one setting
# -- one slider in the app, one byte here -- and each group then has a bitmask
# of the protocols it may negotiate.
CUSTOM_PORTS: Final[tuple[str, ...]] = ("C1", "C2", "C3", "C4", "C5", "C6+A")
# The shared C6+A slider offers 0, 15 and 30 W, and stores the step, not the
# watts. The five plain ports store watts outright.
CUSTOM_SHARED_STEP: Final = 15
# Bit positions in a group's protocol mask. The app lists exactly these seven,
# in this order. Bit 1 belongs to something this model has nothing to put in:
# ticking every box the app offers for the 140 W port sets the mask to 0xFD,
# which is these seven and not it. The slot is there in the protocol; the
# X783 simply never fills it.
CUSTOM_PROTOCOLS: Final[dict[int, str]] = {
    0: "Apple5V/2.4A",
    2: "AFC",
    3: "SCP",
    4: "UFCS",
    5: "5-11V PPS",
    6: "5-21V PPS",
    7: "AVS",
}

FRAME_QUERY = 0xAA
FRAME_NOTIFY = 0xEE
FRAME_SETTING = 0x11

QUERY_GET_DEVICE_STATE = 1
# Answers with exactly the deviceUniqueCode the account API has already
# handed over, so there is nothing to be gained by asking. Checked against a
# live X783 rather than assumed.
QUERY_GET_SN = 5
QUERY_GET_POWER_INFO = 6
# Answers empty on the X783 -- asked for and read back as an empty string --
# so there is nothing here to publish either. OTA state comes from the
# OTA_ugrade property instead, which is what ota_state() reads.
QUERY_GET_UPGRADE_STATUS = 7
QUERY_GET_WIFI_SSID = 8
QUERY_GET_PRODUCT_VERSION = 10

SETTING_SET_BRIGHTNESS = 1
SETTING_SET_SLEEP_TIME = 2
SETTING_SET_CHARGING_MODE = 4
SETTING_SET_SCREENSAVER = 5

# The mode byte is followed by 35 parameter bytes; every preset leaves them zero
# and only the app's "custom" mode fills them in.
CHARGING_MODE_PARAMS = 35

# One 7-byte record per port, then one handshake-protocol byte per port.
PORT_RECORD = 7
PORT_COUNT = 8
POWER_BODY_MIN = PORT_RECORD * PORT_COUNT

# Offsets into the GET_DEVICE_STATE reply. Each was confirmed by writing a
# distinctive value and reading it back, not inferred.
STATE_BRIGHTNESS = 2
STATE_SLEEP_TIME = 3
STATE_CHARGING_MODE = 4
STATE_SCREENSAVER = 40  # then theme at 41 and a further flag at 42
# The custom mode's 35 parameters sit between the mode byte and the screensaver
# flag, which is why nothing needed them to be understood before now.
STATE_CUSTOM = 5
STATE_CUSTOM_MASKS = 16
STATE_CUSTOM_END = 40
CUSTOM_LIMITS = 5  # ports carrying a plain wattage; C6+A follows as one byte
STATE_IMAGE_ID = 43  # six ASCII bytes naming the wallpaper in use
STATE_WALLPAPER_COUNT = 49  # then that many six-byte ids
IMAGE_ID_LEN = 6


def crc16_modbus(data: bytes) -> int:
    """CRC-16/MODBUS, the checksum the charger's frames carry."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def build_frame(frame_type: int, cmd: int, payload: bytes = b"\x00") -> str:
    """Encode one protocol frame as the uppercase hex string ``PT_data`` wants."""
    body = bytes((frame_type, cmd)) + len(payload).to_bytes(2, "big") + payload
    return (body + crc16_modbus(body).to_bytes(2, "little")).hex().upper()


def frame_body(value: str, frame_type: int, cmd: int) -> bytes | None:
    """Return a frame's payload if it is the reply we asked for and the CRC holds."""
    try:
        raw = bytes.fromhex(value)
    except ValueError:
        _LOGGER.debug("PT_data is not hex: %r", value)
        return None
    if len(raw) < 6:
        return None
    length = int.from_bytes(raw[2:4], "big")
    body = raw[4 : 4 + length]
    if len(body) != length:
        return None
    if crc16_modbus(raw[: 4 + length]) != int.from_bytes(
        raw[4 + length : 6 + length], "little"
    ):
        _LOGGER.debug("PT_data CRC mismatch: %s", value)
        return None
    if raw[0] != frame_type or raw[1] != cmd:
        return None
    return body


def parse_power_frame(
    value: str, model: str | None = None
) -> dict[str, dict[str, Any]] | None:
    """Decode a ``GET_POWER_INFO`` reply into ``{port_name: {volt, amp, watt}}``.

    Returns None for anything else -- the property also holds replies to other
    commands, and the last one simply stays there until the device sends a new.
    """
    body = frame_body(value, FRAME_QUERY, QUERY_GET_POWER_INFO)
    if body is None:
        return None
    ports_named = ports_for(model, len(body))
    measured = PORT_RECORD * len(ports_named)
    if not ports_named or len(body) < measured:
        _LOGGER.debug("power body too short: %d for %d ports", len(body), len(ports_named))
        return None

    def u16(offset: int) -> int:
        return int.from_bytes(body[offset : offset + 2], "big")

    ports: dict[str, dict[str, Any]] = {}
    for index, name in enumerate(ports_named):
        base = PORT_RECORD * index
        # The protocol byte block follows the port records; a port that has
        # nothing attached reports 0 ("none").
        proto_at = measured + index
        ports[name] = {
            "voltage": u16(base) / 10,
            "current": u16(base + 2) / 10,
            "power": u16(base + 4) / 10,
            "protocol": HANDSHAKE_PROTOCOL.get(
                body[proto_at] if len(body) > proto_at else 0, "unknown"
            ),
        }
    return ports


def parse_custom_mode(
    body: bytes, model: str | None = None
) -> list[dict[str, Any]] | None:
    """Decode the parameter block only the custom charging mode fills in.

    Layout, established against the app's editor on a live charger by changing
    one slider at a time and reading the frame back::

         5..14   C1..C5 power limit, U16 big endian, in watts
        15       C6 and A together, in 15 W steps -- their slider has three
        16..39   one U32 big-endian protocol bitmask per group, C1 first

    A preset leaves the whole block at zero -- checked by switching to one and
    watching it go -- which is how "no custom mode configured" is told apart
    from a configured one that merely happens to be idle.
    """
    if "custom" not in state_fields(model):
        # Five plain wattages, a shared pair counted in steps, then a mask
        # each: that shape is the X783's, and another model's ports do not
        # divide the same way -- the 160W's block repeats a seven-byte group
        # instead. Guessing would put numbers on a page that mean nothing,
        # which is worse than showing none.
        return None
    if len(body) < STATE_CUSTOM_END:
        return None
    if not any(body[STATE_CUSTOM:STATE_CUSTOM_END]):
        return None

    limits = [
        int.from_bytes(body[STATE_CUSTOM + 2 * i : STATE_CUSTOM + 2 * i + 2], "big")
        for i in range(CUSTOM_LIMITS)
    ]
    # The shared group stores its step rather than its wattage.
    limits.append(body[STATE_CUSTOM + 2 * CUSTOM_LIMITS] * CUSTOM_SHARED_STEP)

    groups = []
    for index, name in enumerate(CUSTOM_PORTS):
        at = STATE_CUSTOM_MASKS + 4 * index
        mask = int.from_bytes(body[at : at + 4], "big")
        groups.append(
            {
                "port": name,
                "limit": limits[index],
                "protocols": [
                    label for bit, label in CUSTOM_PROTOCOLS.items() if mask >> bit & 1
                ],
                "mask": mask,
            }
        )
    return groups
