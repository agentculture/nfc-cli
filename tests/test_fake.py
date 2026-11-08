import json
from pathlib import Path

import pytest

from nfc_cli.nfc import fake
from nfc_cli.nfc.errors import CardRemoved, NfcError, NoCard, ReaderBusy
from nfc_cli.nfc.transport import Transport

FIX = Path(__file__).parent / "fixtures" / "x3_dump.json"
KEY = "FFFFFFFFFFFF"


def auth(t, block, ktype=0x60, key=KEY, slot=0):
    assert t.transmit(bytes.fromhex("FF82000" + str(slot) + "06" + key))[1] == fake.SW_OK
    return t.transmit(bytes([0xFF, 0x86, 0, 0, 5, 1, 0, block, ktype, slot]))


def read(t, block, le=16):
    return t.transmit(bytes([0xFF, 0xB0, 0, block, le]))


def write(t, block, data):
    return t.transmit(bytes([0xFF, 0xD6, 0, block, len(data)]) + data)


def test_protocol_and_extras():
    t = fake.load_scenario("x3")
    assert isinstance(t, Transport)
    with t.claim() as c:
        assert c is t
    t.power_on()
    t.power_off()
    assert t.present()
    t.close()
    assert t.closed


def test_x3_matches_fixture():
    d = json.loads(FIX.read_text())
    t = fake.load_scenario("x3")
    assert t.power_on().hex() == d["atr"]
    assert t.transmit(bytes.fromhex("FFCA000000")) == (bytes.fromhex(d["uid"]), fake.SW_OK)
    for sec, entry in d["dump"].items():
        s = int(sec)
        assert auth(t, s * 4)[1] == fake.SW_OK
        for i, hexblk in enumerate(entry["blocks"]):
            data, sw = read(t, s * 4 + i)
            assert sw == fake.SW_OK and data.hex() == hexblk


def test_classic_auth_state_and_wrong_key():
    t = fake.load_scenario("x3")
    t.power_on()
    assert read(t, 4)[1] == fake.SW_NOT_AUTH
    assert auth(t, 4, key="A0A1A2A3A4A5")[1] == fake.SW_DENIED
    assert auth(t, 4)[1] == fake.SW_OK
    assert read(t, 4)[1] == fake.SW_OK
    assert read(t, 8)[1] == fake.SW_NOT_AUTH  # other sector
    t.power_on()  # reset drops auth
    assert read(t, 4)[1] == fake.SW_NOT_AUTH


def test_classic_write_and_block0_readonly():
    t = fake.load_scenario("x3")
    t.power_on()
    auth(t, 0)
    assert write(t, 0, bytes(16))[1] == fake.SW_DENIED
    auth(t, 4)
    assert write(t, 4, bytes(range(16))) == (b"", fake.SW_OK)
    assert read(t, 4)[0] == bytes(range(16))
    m = fake.load_scenario("magic-classic")
    m.power_on()
    auth(m, 0)
    assert write(m, 0, bytes(range(16)))[1] == fake.SW_OK
    assert read(m, 0)[0] == bytes(range(16))


def test_classic_trailer_masking_and_key_change():
    t = fake.load_scenario("x3")
    t.power_on()
    auth(t, 4)
    data, sw = read(t, 7)
    assert data == bytes(6) + bytes.fromhex("ff078069") + bytes.fromhex(KEY)
    new = bytes.fromhex("A0A1A2A3A4A5") + bytes.fromhex("ff078069") + bytes.fromhex("B0B1B2B3B4B5")
    assert write(t, 7, new)[1] == fake.SW_OK
    t.power_on()
    assert auth(t, 4)[1] == fake.SW_DENIED
    assert auth(t, 4, 0x60, "A0A1A2A3A4A5")[1] == fake.SW_OK
    assert auth(t, 4, 0x61, "B0B1B2B3B4B5", slot=1)[1] == fake.SW_OK


def test_classic_access_bits_enforced():
    t = fake.load_scenario("blank-classic")
    t.power_on()
    auth(t, 4)
    # block 4: C1C2C3 = 010 (read A|B, write never); trailer: 001 (transport-like)
    trailer = bytearray(t.card.blocks[7])
    trailer[7] = 0x00
    trailer[8] = 0x80 | 0x01
    t.card.blocks[7] = bytes(trailer)
    assert write(t, 4, bytes(16))[1] == fake.SW_DENIED
    assert read(t, 4)[1] == fake.SW_OK
    assert write(t, 5, bytes(16))[1] == fake.SW_OK  # 000: write with A


def test_ntag215():
    t = fake.load_scenario("ntag215")
    atr = t.power_on()
    assert atr.hex() == fake.ATR_NTAG
    uid, sw = t.transmit(bytes.fromhex("FFCA000000"))
    assert len(uid) == 7 and sw == fake.SW_OK
    data, sw = read(t, 3)
    assert data[:4] == bytes.fromhex("e1103e00") and len(data) == 16
    assert write(t, 4, b"\x03\x00\xfe\x00") == (b"", fake.SW_OK)
    assert read(t, 4, 4)[0] == b"\x03\x00\xfe\x00"
    assert write(t, 0, bytes(4))[1] == fake.SW_DENIED
    assert write(t, 135, bytes(4))[1] == fake.SW_DENIED
    assert write(t, 4, b"\x00")[1] == fake.SW_WRONG_LEN


def test_iso15693_blocks():
    t = fake.load_scenario("iso15693")
    t.power_on()
    assert write(t, 2, b"\x01\x02\x03\x04")[1] == fake.SW_OK
    assert read(t, 2, 4) == (b"\x01\x02\x03\x04", fake.SW_OK)
    assert read(t, 2, 8)[0] == b"\x01\x02\x03\x04\x00\x00\x00\x00"
    assert read(t, 99, 4)[1] == fake.SW_DENIED


def test_type4_ndef_app():
    t = fake.load_scenario("type4")
    t.power_on()
    assert t.transmit(bytes.fromhex("00B0000002"))[1] == fake.SW_NO_FILE
    assert t.transmit(bytes.fromhex("00A4040007D276000085010100"))[1] == fake.SW_OK
    assert t.transmit(bytes.fromhex("00A4000C02E103"))[1] == fake.SW_OK
    cc, sw = t.transmit(bytes.fromhex("00B000000F"))
    assert sw == fake.SW_OK and cc[:2] == b"\x00\x0f" and cc[9:11] == b"\xe1\x04"
    assert t.transmit(bytes.fromhex("00A4000C02E104"))[1] == fake.SW_OK
    assert t.transmit(bytes.fromhex("00D60000" + "05" + "0003d1010a"))[1] == fake.SW_OK
    assert t.transmit(bytes.fromhex("00B0000005"))[0] == bytes.fromhex("0003d1010a")
    assert t.transmit(bytes.fromhex("00A4000C02E103"))[1] == fake.SW_OK
    assert t.transmit(bytes.fromhex("00D600000100"))[1] == fake.SW_DENIED  # CC read-only
    assert t.transmit(bytes.fromhex("00A4040007AABBCCDDEEFF00"))[1] == fake.SW_NOT_FOUND


def test_fault_card_removed_after():
    t = fake.load_scenario("x3")
    t.power_on()
    t.card_removed_after(2)
    t.transmit(bytes.fromhex("FFCA000000"))
    t.transmit(bytes.fromhex("FFCA000000"))
    with pytest.raises(CardRemoved):
        t.transmit(bytes.fromhex("FFCA000000"))
    assert not t.present()
    with pytest.raises(NoCard):
        t.power_on()


def test_fault_auth_fail():
    t = fake.load_scenario("x3")
    t.power_on()
    t.auth_fail()
    assert auth(t, 4)[1] == fake.SW_DENIED
    t.auth_fail({8})
    assert auth(t, 4)[1] == fake.SW_OK
    assert auth(t, 8)[1] == fake.SW_DENIED
    t.auth_fail(False)
    assert auth(t, 8)[1] == fake.SW_OK


def test_fault_sw_override():
    t = fake.load_scenario("x3")
    t.power_on()
    t.override_sw(0x6A81, ins=0xCA, times=1)
    assert t.transmit(bytes.fromhex("FFCA000000")) == (b"", b"\x6a\x81")
    assert t.transmit(bytes.fromhex("FFCA000000"))[1] == fake.SW_OK
    t.override_sw(b"\x65\x81")
    assert t.transmit(bytes.fromhex("FFCA000000"))[1] == b"\x65\x81"


def test_fault_busy_and_empty():
    t = fake.load_scenario("x3")
    t.set_busy()
    for fn in (t.power_on, t.present, lambda: t.transmit(b"\xff\xca\x00\x00\x00")):
        with pytest.raises(ReaderBusy):
            fn()
    with pytest.raises(ReaderBusy), t.claim():
        pass
    t.set_busy(False)
    assert t.present()
    e = fake.load_scenario("empty")
    assert not e.present()
    with pytest.raises(NoCard):
        e.power_on()


def test_unknown_scenario_and_escape():
    with pytest.raises(NfcError):
        fake.load_scenario("nope")
    t = fake.load_scenario("x3")
    t.escape_responses[b"\xe0\x00"] = b"\x01"
    assert t.escape(b"\xe0\x00") == b"\x01" and t.escape(b"\x00") == b""
