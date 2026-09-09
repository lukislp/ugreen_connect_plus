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
from typing import Any, Final

_LOGGER = logging.getLogger(__name__)

# Port order of the power report on the Nexode Pro 300W, from the app's own port
# table. X783 is what the app calls that model internally, and is kept here
# because a second model would need its own list beside this one.
X783_PORTS: Final[tuple[str, ...]] = ("C1", "C2", "C3", "C4", "C5", "C6", "A1", "DC")
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


def parse_power_frame(value: str) -> dict[str, dict[str, Any]] | None:
    """Decode a ``GET_POWER_INFO`` reply into ``{port_name: {volt, amp, watt}}``.

    Returns None for anything else -- the property also holds replies to other
    commands, and the last one simply stays there until the device sends a new.
    """
    body = frame_body(value, FRAME_QUERY, QUERY_GET_POWER_INFO)
    if body is None:
        return None
    if len(body) < POWER_BODY_MIN:
        _LOGGER.debug("power body too short: %d < %d", len(body), POWER_BODY_MIN)
        return None

    def u16(offset: int) -> int:
        return int.from_bytes(body[offset : offset + 2], "big")

    ports: dict[str, dict[str, Any]] = {}
    for index, name in enumerate(X783_PORTS):
        base = PORT_RECORD * index
        # The protocol byte block follows the port records; a port that has
        # nothing attached reports 0 ("none").
        proto_at = POWER_BODY_MIN + index
        ports[name] = {
            "voltage": u16(base) / 10,
            "current": u16(base + 2) / 10,
            "power": u16(base + 4) / 10,
            "protocol": HANDSHAKE_PROTOCOL.get(
                body[proto_at] if len(body) > proto_at else 0, "unknown"
            ),
        }
    return ports


def parse_custom_mode(body: bytes) -> list[dict[str, Any]] | None:
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
