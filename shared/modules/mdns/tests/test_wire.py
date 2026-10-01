import struct

import pytest

import wire


def test_round_trip_keeps_every_section():
    msg = wire.Message(
        id=7, flags=wire.FLAG_RESPONSE,
        questions=[wire.Question("smoking-pi.local", wire.TYPE_A, unicast=True)],
        answers=wire.address_records("smoking-pi.local", ["192.168.1.10", "fd00::10"]),
        authority=wire.address_records("smoking-pi.local", ["192.168.1.10"], flush=False),
    )
    back = wire.parse(wire.build(msg))
    assert back.id == 7 and back.is_response
    assert back.questions == msg.questions
    assert back.answers == msg.answers
    assert back.authority == msg.authority
    assert [r.address() for r in back.answers] == ["192.168.1.10", "fd00::10"]


def test_compressed_names_are_followed():
    # Header, one question "a.local", one answer whose name points at it.
    q = b"\x01a\x05local\x00" + struct.pack("!HH", 1, 1)
    rr = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 120, 4) + bytes([10, 0, 0, 1])
    data = struct.pack("!HHHHHH", 0, 0x8400, 1, 1, 0, 0) + q + rr
    msg = wire.parse(data)
    assert msg.answers[0].name == "a.local"
    assert msg.answers[0].address() == "10.0.0.1"


@pytest.mark.parametrize("data", [
    b"\x00" * 5,
    struct.pack("!HHHHHH", 0, 0, 1, 0, 0, 0) + b"\x05ab",
    struct.pack("!HHHHHH", 0, 0, 1, 0, 0, 0) + b"\xc0\x0c" + b"\x00\x01\x00\x01",
])
def test_broken_messages_raise_wire_error(data):
    with pytest.raises(wire.WireError):
        wire.parse(data)


def test_names_compare_without_case_or_dot():
    assert wire.same_name("Smoking-Pi.LOCAL.", "smoking-pi.local")
