import pytest

from avinox.protobuf import ProtobufError, decode_message, to_signed64
from ridebuilder import encode, varint


def test_varint_fixed_and_bytes():
    msg = encode({7: 300, 31: 1.5, 36: b"\x00\x00\x80\x3f"}) + varint(2 << 3 | 2) + varint(3) + b"abc"
    f = decode_message(msg)
    assert f[7] == 300
    assert len(f[31]) == 8
    assert f[36] == b"\x00\x00\x80\x3f"
    assert f[2] == b"abc"


def test_negative_varint():
    assert to_signed64(decode_message(encode({39: -250}))[39]) == -250


@pytest.mark.parametrize("data", [b"\x38", b"\x38\x80", b"\x09\x00\x00", b"\x13"])
def test_broken_messages_raise(data):
    with pytest.raises(ProtobufError):
        decode_message(data)
