import pytest

from nfc_cli.nfc import classic
from nfc_cli.nfc.errors import ApduError, AuthFailed
from nfc_cli.nfc.transport import Transport
from tests._classic_fake import FakeClassic

KEY_X = bytes.fromhex("112233445566")


def test_fake_is_transport():
    assert isinstance(FakeClassic(), Transport)


def test_layout_1k():
    assert [classic.sector_of(b) for b in (0, 3, 4, 63)] == [0, 0, 1, 15]
    assert classic.first_block(15) == 60
    assert classic.trailer_block(0) == 3
    assert classic.is_trailer(63)
    assert not classic.is_trailer(62)


def test_layout_4k():
    assert classic.sector_of(127) == 31
    assert classic.sector_of(128) == 32
    assert classic.sector_of(143) == 32
    assert classic.sector_of(144) == 33
    assert classic.sector_of(255) == 39
    assert classic.first_block(31) == 124
    assert classic.first_block(32) == 128
    assert classic.first_block(39) == 240
    assert classic.sector_size(32) == 16
    assert classic.sector_size(31) == 4
    assert classic.trailer_block(32) == 143
    assert classic.trailer_block(39) == 255
    assert classic.is_trailer(143)
    assert not classic.is_trailer(142)
    assert [classic.sector_count(n) for n in (64, 256)] == [16, 40]
    for s in range(40):
        assert classic.sector_of(classic.first_block(s)) == s
        assert classic.sector_of(classic.trailer_block(s)) == s


def test_layout_bounds():
    for bad in (-1, 256):
        with pytest.raises(classic.ClassicError):
            classic.sector_of(bad)
    with pytest.raises(classic.ClassicError):
        classic.first_block(40)


def test_authenticate_apdus_and_read():
    card = FakeClassic()
    classic.authenticate(card, 1, "FFFFFFFFFFFF", "A")
    assert card.log[0] == bytes.fromhex("FF82000006FFFFFFFFFFFF")
    assert card.log[1] == bytes.fromhex("FF8600000501000460" "00")
    assert classic.read_block(card, 4) == bytes(16)
    assert card.log[-1] == bytes.fromhex("FFB0000410")


def test_authenticate_key_b():
    card = FakeClassic(keys={2: {"B": KEY_X}})
    classic.authenticate(card, 2, KEY_X, "B")
    assert card.log[-1][8] == 0x61


def test_failed_auth_power_cycles_and_raises():
    card = FakeClassic()
    with pytest.raises(AuthFailed):
        classic.authenticate(card, 0, KEY_X, "A")
    assert card.cycles == 1
    assert not card.needs_cycle
    classic.authenticate(card, 0, "FFFFFFFFFFFF", "A")  # works because of the cycle


def test_write_and_read_block():
    card = FakeClassic()
    classic.authenticate(card, 1, "FFFFFFFFFFFF")
    classic.write_block(card, 5, bytes(range(16)))
    assert classic.read_block(card, 5) == bytes(range(16))
    assert card.writes()[0][:5] == bytes.fromhex("FFD6000510")


def test_read_sector_4k_large():
    card = FakeClassic(blocks=256)
    out = classic.read_sector(card, 33, "FFFFFFFFFFFF")
    assert len(out) == 16
    assert card.log[-1] == bytes.fromhex("FFB000" "9F" "10")


def test_read_error_status():
    card = FakeClassic()
    with pytest.raises(ApduError):
        classic.read_block(card, 4)  # not authenticated


def test_write_refuses_block0_and_trailer():
    card = FakeClassic()
    classic.authenticate(card, 0, "FFFFFFFFFFFF")
    payload = bytes(16)
    with pytest.raises(classic.ClassicError):
        classic.write_block(card, 0, payload)
    with pytest.raises(classic.ClassicError):
        classic.write_block(card, 3, payload)
    assert card.writes() == []


def test_write_trailer_invalid_access_bits_zero_apdus():
    card = FakeClassic()
    bad = bytes(6) + bytes.fromhex("00000000") + bytes(6)
    with pytest.raises(classic.ClassicError, match="access bits"):
        classic.write_block(card, 3, bad, allow_trailer=True)
    assert card.log == []


def test_write_trailer_valid_with_allow():
    card = FakeClassic()
    classic.authenticate(card, 0, "FFFFFFFFFFFF")
    good = bytes.fromhex("A0A1A2A3A4A5" "787788C1" "FFFFFFFFFFFF")
    classic.write_block(card, 3, good, allow_trailer=True)
    assert len(card.writes()) == 1


def test_write_block0_allowed_flag():
    card = FakeClassic()
    classic.authenticate(card, 0, "FFFFFFFFFFFF")
    classic.write_block(card, 0, bytes(16), allow_trailer=True)
    assert len(card.writes()) == 1


def test_write_bad_length():
    card = FakeClassic()
    with pytest.raises(classic.ClassicError):
        classic.write_block(card, 4, b"x")


def test_find_key_default_order_and_cycles():
    keys = {1: {"A": bytes.fromhex("D3F7D3F7D3F7")}}
    card = FakeClassic(keys=keys)
    assert classic.find_key(card, 1) == ("A", bytes.fromhex("D3F7D3F7D3F7"))
    loads = [x[5:11].hex().upper() for x in card.log if x[:2] == b"\xff\x82"]
    assert loads == ["FFFFFFFFFFFF", "A0A1A2A3A4A5", "D3F7D3F7D3F7"]
    assert card.cycles == 2  # re-selected after each of the two failures


def test_find_key_extra_keys_and_key_b():
    card = FakeClassic(keys={3: {"A": bytes(6), "B": KEY_X}})
    # A is 000000000000 (a default) so found as A
    assert classic.find_key(card, 3) == ("A", bytes(6))
    card = FakeClassic(keys={3: {"A": bytes.fromhex("010203040506"), "B": KEY_X}})
    assert classic.find_key(card, 3, [KEY_X.hex()], ("B",)) == ("B", KEY_X)


def test_find_key_exhausted():
    card = FakeClassic(keys={1: {"A": KEY_X, "B": KEY_X}})
    with pytest.raises(AuthFailed):
        classic.find_key(card, 1)
    assert card.cycles == 8  # 4 default keys x A and B


def test_load_keys_file(tmp_path):
    p = tmp_path / "k.txt"
    p.write_text("# c\n\n112233445566  # inline\nAABBCCDDEEFF\n")
    assert classic.load_keys_file(str(p)) == [KEY_X, bytes.fromhex("AABBCCDDEEFF")]
    p.write_text("xyz\n")
    key_path = str(p)
    with pytest.raises(classic.ClassicError):
        classic.load_keys_file(key_path)


def test_bad_key_and_type():
    card = FakeClassic()
    with pytest.raises(classic.ClassicError):
        classic.authenticate(card, 0, "FF")
    with pytest.raises(classic.ClassicError):
        classic.authenticate(card, 0, "FFFFFFFFFFFF", "C")


def test_get_uid():
    assert classic.get_uid(FakeClassic(uid=b"\x01\x02\x03\x04")) == b"\x01\x02\x03\x04"
