"""The frame codec and the parsers, against frames a charger actually sent.

Every offset in `protocol.py` was established by hand: a value written through
the app, the frame read back, the byte that moved noted down. Nothing about
that is self-evident from the code, and a transposed offset produces plausible
numbers rather than an error. So the fixtures here are captures, not
constructions -- an X783 on firmware 1.2.1, taken from the live integration --
and they are what stops the decoding drifting away from the device.
"""

from conftest import protocol as p

# A charger with something on C3: 27.9 V, 1.3 A, 36.2 W over PD, and 5.1 V
# sitting on C1 and C2 with nothing drawing from them.
POWER = (
    "aa06003f00330000000001003300000000010117000900fb010000000000000000000000"
    "00000000000000000000000000000000000000000000000000000500000000541b"
)
# The same charger a few seconds later, drawing more.
POWER_LATER = (
    "aa06003f00330000000001003300000000010117002403ec010000000000000000000000"
    "00000000000000000000000000000000000000000000000000000500000000fb2b"
)
# Device state while the custom mode is running: C1 and C2 at 15 W, C3 at 140,
# C4 and C5 at 60, C6 and A sharing nothing, and two wallpapers in the library.
STATE_CUSTOM = (
    "aa01003e0037640004000f000f008c003c003c000000000100000001000000650000006500"
    "00006500000000010100ffffffffffff024231364135353439423636319fdd"
)


# --- the checksum ----------------------------------------------------------

def test_crc_matches_the_standard_check_value():
    # CRC-16/MODBUS is defined with this: "123456789" -> 0x4B37. Anything that
    # passes the device's own frames but fails this is not MODBUS.
    assert p.crc16_modbus(b"123456789") == 0x4B37


def test_crc_accepts_frames_the_charger_sent():
    for frame in (POWER, POWER_LATER, STATE_CUSTOM):
        raw = bytes.fromhex(frame)
        length = int.from_bytes(raw[2:4], "big")
        assert p.crc16_modbus(raw[: 4 + length]) == int.from_bytes(
            raw[4 + length : 6 + length], "little"
        )


# --- the frame ------------------------------------------------------------

def test_body_is_returned_for_the_reply_that_was_asked_for():
    body = p.frame_body(POWER, p.FRAME_QUERY, p.QUERY_GET_POWER_INFO)
    assert body is not None
    assert len(body) == int.from_bytes(bytes.fromhex(POWER)[2:4], "big")


def test_a_reply_to_a_different_question_is_not_mistaken_for_this_one():
    # The property keeps its last frame, so the answer to another command is
    # what is on offer more often than not.
    assert p.frame_body(POWER, p.FRAME_QUERY, p.QUERY_GET_DEVICE_STATE) is None


def test_a_corrupted_frame_is_refused():
    broken = POWER[:-4] + "0000"
    assert p.frame_body(broken, p.FRAME_QUERY, p.QUERY_GET_POWER_INFO) is None


def test_a_truncated_frame_is_refused():
    assert p.frame_body(POWER[:40], p.FRAME_QUERY, p.QUERY_GET_POWER_INFO) is None


def test_something_that_is_not_hex_is_refused():
    assert p.frame_body("not a frame", p.FRAME_QUERY, p.QUERY_GET_POWER_INFO) is None


def test_a_built_frame_carries_its_own_valid_checksum():
    built = p.build_frame(p.FRAME_QUERY, p.QUERY_GET_POWER_INFO)
    assert p.frame_body(built, p.FRAME_QUERY, p.QUERY_GET_POWER_INFO) == b"\x00"


# --- the readings ---------------------------------------------------------

def test_power_frame_reads_the_port_that_is_charging():
    ports = p.parse_power_frame(POWER)
    assert ports is not None
    assert ports["C3"] == {
        "voltage": 27.9, "current": 0.9, "power": 25.1, "protocol": "PD",
    }


def test_the_readings_of_a_port_agree_with_each_other():
    # Watts are reported, not computed, so this is a check on the offsets
    # rather than on arithmetic: read voltage or current from the wrong two
    # bytes and the three stop multiplying out.
    for frame in (POWER, POWER_LATER):
        c3 = p.parse_power_frame(frame)["C3"]
        assert abs(c3["voltage"] * c3["current"] - c3["power"]) < 1.0


def test_power_frame_reads_a_live_port_with_nothing_drawing():
    # A cable with nothing on it: the port is held at 5.1 V and delivers
    # nothing, which is exactly what a full battery looks like too.
    ports = p.parse_power_frame(POWER)
    assert ports["C1"]["voltage"] == 5.1
    assert ports["C1"]["current"] == 0.0
    assert ports["C1"]["protocol"] == "none"


def test_power_frame_covers_every_port():
    ports = p.parse_power_frame(POWER)
    assert list(ports) == list(p.X783_PORTS)


def test_power_frame_follows_the_charger_between_readings():
    first = p.parse_power_frame(POWER)["C3"]["power"]
    later = p.parse_power_frame(POWER_LATER)["C3"]["power"]
    assert later > first


def test_a_state_frame_is_not_read_as_a_power_frame():
    assert p.parse_power_frame(STATE_CUSTOM) is None


# --- the custom mode ------------------------------------------------------

def _custom(frame: str):
    body = p.frame_body(frame, p.FRAME_QUERY, p.QUERY_GET_DEVICE_STATE)
    return p.parse_custom_mode(body)


def test_custom_mode_reads_the_wattage_of_every_group():
    groups = {g["port"]: g["limit"] for g in _custom(STATE_CUSTOM)}
    assert groups == {"C1": 15, "C2": 15, "C3": 140, "C4": 60, "C5": 60, "C6+A": 0}


def test_the_shared_group_counts_in_steps_rather_than_watts():
    # C6 and A share one setting whose slider offers 0, 15 and 30 W, and the
    # frame carries the step. Every other port carries plain watts.
    body = bytearray(p.frame_body(STATE_CUSTOM, p.FRAME_QUERY, p.QUERY_GET_DEVICE_STATE))
    body[p.STATE_CUSTOM + 2 * p.CUSTOM_LIMITS] = 2
    shared = next(g for g in p.parse_custom_mode(bytes(body)) if g["port"] == "C6+A")
    assert shared["limit"] == 30


def test_custom_mode_reads_the_protocols_each_group_may_negotiate():
    groups = {g["port"]: g["protocols"] for g in _custom(STATE_CUSTOM)}
    assert groups["C1"] == ["Apple5V/2.4A"]
    assert groups["C3"] == ["Apple5V/2.4A", "AFC", "5-11V PPS", "5-21V PPS"]
    assert groups["C6+A"] == []


def test_the_mask_is_reported_as_well_as_the_names():
    # A bit nobody has named yet would be invisible in the names alone, and
    # this is the field that would show it on a charger that sets one.
    masks = {g["port"]: g["mask"] for g in _custom(STATE_CUSTOM)}
    assert masks["C3"] == 0x65
    assert masks["C1"] == 0x01


def test_every_protocol_the_app_offers_is_a_bit_this_can_name():
    # Ticking all seven boxes on the strongest port sets the mask to 0xFD.
    body = bytearray(p.frame_body(STATE_CUSTOM, p.FRAME_QUERY, p.QUERY_GET_DEVICE_STATE))
    at = p.STATE_CUSTOM_MASKS + 4 * 2  # C3
    body[at : at + 4] = (0xFD).to_bytes(4, "big")
    c3 = next(g for g in p.parse_custom_mode(bytes(body)) if g["port"] == "C3")
    assert len(c3["protocols"]) == 7


def test_a_preset_reads_as_no_custom_mode_at_all():
    # Switching to a preset empties the block, which is how a charger with no
    # custom mode configured is told apart from one that merely sits idle.
    body = bytearray(p.frame_body(STATE_CUSTOM, p.FRAME_QUERY, p.QUERY_GET_DEVICE_STATE))
    body[p.STATE_CUSTOM : p.STATE_CUSTOM_END] = bytes(p.STATE_CUSTOM_END - p.STATE_CUSTOM)
    assert p.parse_custom_mode(bytes(body)) is None


def test_a_body_too_short_to_hold_the_block_is_refused():
    assert p.parse_custom_mode(b"\x00" * 10) is None
