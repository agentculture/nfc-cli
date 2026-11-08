"""Tests for the pure NDEF codec and MIFARE Classic NDEF/MAD format template."""

import pytest

from nfc_cli.nfc import ndef
from nfc_cli.nfc.ndef import NdefError, NdefRecord


def _bits(c1, c2, c3):
    """Independent derivation of the 3 access bytes from per-block C1/C2/C3 nibbles (b3..b0)."""
    b6 = ((~c2 & 0xF) << 4) | (~c1 & 0xF)
    b7 = (c1 << 4) | (~c3 & 0xF)
    b8 = (c3 << 4) | c2
    return bytes([b6, b7, b8])


def test_access_bit_constants_follow_parity_rule():
    # NDEF sector: data blocks 000, trailer 011 -> C1=0000, C2=1000, C3=1000
    assert _bits(0b0000, 0b1000, 0b1000) == bytes.fromhex("7f0788")
    # MAD sector 0: blocks 0-2 100 (read-only), trailer 011 -> C1=0111, C2=1000, C3=1000
    assert _bits(0b0111, 0b1000, 0b1000) == bytes.fromhex("787788")
    assert ndef.ACCESS_NDEF == bytes.fromhex("7f0788")
    assert ndef.ACCESS_MAD == bytes.fromhex("787788")
    assert ndef.validate_access_bits(bytes(6) + ndef.ACCESS_NDEF + b"\x40" + bytes(6))
    assert ndef.validate_access_bits(bytes(6) + ndef.ACCESS_MAD + b"\xc1" + bytes(6))


def test_text_roundtrip():
    rec = ndef.text_record("héllo ✓", "en")
    out = ndef.decode_message(ndef.encode_message([rec]))
    assert out == [rec]
    assert ndef.record_text(out[0]) == ("en", "héllo ✓")
    assert ndef.encode_message([rec])[:3] == bytes([0xD1, 0x01, len(rec.payload)])


def test_uri_prefix_compression():
    rec = ndef.uri_record("https://www.example.com/x")
    assert rec.payload[0] == 0x02 and rec.payload[1:] == b"example.com/x"
    assert ndef.uri_record("https://a.b").payload[0] == 0x04
    assert ndef.uri_record("tel:+123").payload[0] == 0x05
    assert ndef.uri_record("custom:thing").payload[0] == 0x00
    out = ndef.decode_message(ndef.encode_message([rec]))
    assert ndef.record_uri(out[0]) == "https://www.example.com/x"


def test_mime_and_raw_roundtrip():
    mime = ndef.mime_record("application/json", b'{"a":1}')
    raw = NdefRecord(tnf=ndef.TNF_UNKNOWN, type=b"", id=b"", payload=b"\x01\x02")
    out = ndef.decode_message(ndef.encode_message([mime, raw]))
    assert out == [mime, raw]
    assert out[0].tnf == ndef.TNF_MIME


def test_multi_record_flags_and_ids():
    recs = [
        ndef.text_record("a"),
        NdefRecord(ndef.TNF_WELL_KNOWN, b"T", b"id1", b"\x02enb"),
        ndef.uri_record("http://x.y"),
    ]
    data = ndef.encode_message(recs)
    # first header: MB, SR, no ME, no IL, TNF 1
    assert data[0] == 0x80 | 0x10 | 0x01
    flags = []
    pos = 0
    for r in recs:
        flags.append(data[pos])
        pos += (
            3 + len(r.type) + len(r.id) + len(r.payload) - (0 if r.id else 0) + (1 if r.id else 0)
        )
    assert flags[0] & 0x80 and not flags[0] & 0x40
    assert not flags[1] & 0x80 and not flags[1] & 0x40 and flags[1] & 0x08
    assert flags[2] & 0x40 and not flags[2] & 0x80
    assert ndef.decode_message(data) == recs


def test_long_record_uses_4_byte_length():
    rec = ndef.mime_record("a/b", b"x" * 300)
    data = ndef.encode_message([rec])
    assert not data[0] & 0x10  # no SR
    assert ndef.decode_message(data) == [rec]


def test_decode_errors():
    with pytest.raises(NdefError):
        ndef.decode_message(b"\xd1\x01")  # truncated
    with pytest.raises(NdefError):
        ndef.decode_message(b"")
    with pytest.raises(NdefError):
        ndef.decode_message(b"\x11\x00\x00")  # MB=0 start / no ME
    with pytest.raises(NdefError):
        ndef.decode_message(b"\xb1\x00\x00")  # chunked unsupported (CF)


def test_tlv_wrap_unwrap():
    msg = b"\xd1\x01\x01T"
    w = ndef.tlv_wrap(msg)
    assert w == b"\x03\x04" + msg + b"\xfe"
    assert ndef.tlv_unwrap(w + b"\x00\x00") == msg
    long = b"z" * 300
    w = ndef.tlv_wrap(long)
    assert w[:4] == b"\x03\xff\x01\x2c" and w[-1] == 0xFE
    assert ndef.tlv_unwrap(w) == long
    assert ndef.tlv_wrap(b"") == b"\x03\x00\xfe"
    assert ndef.tlv_unwrap(b"\x00\x01\x03\xaa\xbb\xbb\x03\x01\x41\xfe") == b"A"
    assert ndef.tlv_unwrap(b"\xfe") is None
    with pytest.raises(NdefError):
        ndef.tlv_unwrap(b"\x03\x10\x00")


def test_type2_cc():
    cc = ndef.type2_cc(504)
    assert cc == bytes([0xE1, 0x10, 63, 0x00])
    assert ndef.parse_type2_cc(cc) == {"version": "1.0", "size": 504, "read_only": False}
    assert ndef.parse_type2_cc(bytes([0xE1, 0x10, 0x12, 0x0F]))["read_only"] is True
    with pytest.raises(NdefError):
        ndef.parse_type2_cc(b"\x00\x10\x12\x00")
    with pytest.raises(NdefError):
        ndef.type2_cc(10000)


def test_mad_crc_known_vector():
    # Widely published NDEF-formatted Classic 1K MAD1: CRC 0x14, info 0x01, 15 x AID E103
    payload = b"\x01" + b"\x03\xe1" * 15
    assert ndef.mad_crc(payload) == 0x14
    assert ndef.mad_crc(b"") == 0xC7


def test_format_template():
    t = ndef.classic_format_template()
    assert set(t) >= {1, 2, 3} | {4 * s + 3 for s in range(1, 16)}
    assert 0 not in t
    assert t[1] == bytes([0x14, 0x01]) + b"\x03\xe1" * 7
    assert t[2] == b"\x03\xe1" * 8
    assert t[3] == bytes.fromhex("a0a1a2a3a4a5" "787788c1" "ffffffffffff")
    for s in range(1, 16):
        assert t[4 * s + 3] == bytes.fromhex("d3f7d3f7d3f7" "7f078840" "ffffffffffff")
    assert t[4] == b"\x03\x00\xfe" + bytes(13)
    assert ndef.mad_crc(t[1][1:] + t[2]) == t[1][0]
    for b, trailer in t.items():
        if b % 4 == 3:
            assert ndef.validate_access_bits(trailer)


def test_template_fewer_sectors():
    t = ndef.classic_format_template(sectors=4)
    assert 4 * 4 + 3 not in t and 4 * 3 + 3 in t
    assert t[1] == bytes([t[1][0], 0x01]) + b"\x03\xe1" * 3 + bytes(8)
    assert t[2] == bytes(16)
    assert ndef.mad_crc(t[1][1:] + t[2]) == t[1][0]
    with pytest.raises(NdefError):
        ndef.classic_format_template(sectors=1)


def test_classic_data_blocks():
    blocks = ndef.classic_data_blocks()
    assert blocks[:3] == [4, 5, 6] and blocks[3] == 8 and len(blocks) == 45


def test_validate_access_bits_rejects_flipped_bit():
    good = bytes.fromhex("d3f7d3f7d3f7" "7f078840" "ffffffffffff")
    assert ndef.validate_access_bits(good)
    for i in range(6, 9):
        for bit in range(8):
            bad = bytearray(good)
            bad[i] ^= 1 << bit
            assert not ndef.validate_access_bits(bytes(bad))
    assert not ndef.validate_access_bits(good[:15])
    assert not ndef.validate_access_bits(bytes(16))  # zeros: inverted copies mismatch
