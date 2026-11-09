"""Deviation d1 follow-up: classic.py and families.py against the shared FakeTransport.

t8 and t9 were built in the same wave as t7 (FakeTransport), so their own suites use
private in-test fakes. These tests close the gap by driving the production modules
through ``nfc_cli.nfc.fake`` scenarios, so the two fakes cannot silently disagree.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nfc_cli.nfc import atr, classic, families, ndef
from nfc_cli.nfc.errors import AuthFailed, CardRemoved
from nfc_cli.nfc.fake import load_scenario

FIXTURE = Path(__file__).parent / "fixtures" / "x3_dump.json"


def _x3_blocks() -> dict[int, str]:
    raw = json.loads(FIXTURE.read_text())
    out: dict[int, str] = {}
    for sector, entry in raw["dump"].items():
        for i, hexblock in enumerate(entry["blocks"]):
            out[int(sector) * 4 + i] = hexblock
    return out


def test_x3_scenario_detected_as_classic_1k() -> None:
    t = load_scenario("x3")
    info = atr.parse(t.power_on())
    assert info.family == "classic_1k"
    assert isinstance(families.family_ops(info.family), families.ClassicFamily)
    assert classic.get_uid(t).hex() == "cb30c560"


def test_classic_reads_whole_x3_matching_live_dump() -> None:
    t = load_scenario("x3")
    t.power_on()
    expected = _x3_blocks()
    for sector in range(16):
        key_type, key = classic.find_key(t, sector)
        rows = classic.read_sector(t, sector, key, key_type)
        for i, row in enumerate(rows):
            block = sector * 4 + i
            if classic.is_trailer(block):
                # key A never reads back on a real card; access bits + key B do
                assert row[6:].hex() == expected[block][12:]
            else:
                assert row.hex() == expected[block]


def test_classic_write_then_read_back_on_fake() -> None:
    t = load_scenario("x3")
    t.power_on()
    classic.authenticate(t, 1, "FFFFFFFFFFFF", "A")
    payload = bytes(range(16))
    classic.write_block(t, 4, payload)
    assert classic.read_block(t, 4) == payload


def test_classic_trailer_and_block0_guarded_with_zero_writes() -> None:
    t = load_scenario("x3")
    t.power_on()
    classic.authenticate(t, 0, "FFFFFFFFFFFF", "A")

    def writes() -> int:
        return sum(1 for kind, a in t.log if kind == "apdu" and a[:2] == b"\xff\xd6")

    before = writes()
    for block in (0, 3):
        with pytest.raises(classic.ClassicError):
            classic.write_block(t, block, bytes(16))
    assert writes() == before
    # sanity: the counter is live — a permitted data-block write is counted
    classic.write_block(t, 1, bytes(16))
    assert writes() == before + 1


def test_classic_format_template_accepted_by_fake_access_rules() -> None:
    t = load_scenario("blank-classic")
    t.power_on()
    tmpl = ndef.classic_format_template()
    classic.authenticate(t, 1, "FFFFFFFFFFFF", "A")
    classic.write_block(t, 7, tmpl[7], allow_trailer=True)
    # new key A for sector 1 is the NDEF key; the old transport key no longer works
    with pytest.raises(AuthFailed):
        classic.authenticate(t, 1, "FFFFFFFFFFFF", "A")
    classic.authenticate(t, 1, "D3F7D3F7D3F7", "A")


def test_classic_card_removed_mid_read() -> None:
    t = load_scenario("x3")
    t.power_on()
    classic.authenticate(t, 0, "FFFFFFFFFFFF", "A")
    t.card_removed_after(1)
    classic.read_block(t, 0)
    with pytest.raises(CardRemoved):
        classic.read_block(t, 1)


def test_type2_ndef_round_trip_on_fake_ntag215() -> None:
    t = load_scenario("ntag215")
    info = atr.parse(t.power_on())
    ops = families.family_ops(info.family)
    assert isinstance(ops, families.Type2Ops)
    msg = ndef.encode_message([ndef.uri_record("https://crosspoint.local/")])
    ops.write_ndef(t, msg)
    assert ops.read_ndef(t) == msg


def test_iso15693_blocks_on_fake() -> None:
    t = load_scenario("iso15693")
    info = atr.parse(t.power_on())
    ops = families.family_ops(info.family)
    assert isinstance(ops, families.Iso15693Ops)
    ops.write_block(t, 2, b"\x01\x02\x03\x04")
    assert ops.read_block(t, 2) == b"\x01\x02\x03\x04"


def test_type4_ndef_round_trip_on_fake() -> None:
    t = load_scenario("type4")
    info = atr.parse(t.power_on())
    ops = families.family_ops(info.family)
    assert isinstance(ops, families.Type4Ops)
    msg = ndef.encode_message([ndef.text_record("hello X3", "en")])
    ops.write_ndef(t, msg)
    assert ops.read_ndef(t) == msg
