"""Tests for the PC/SC part 3 ATR parser (pure, no I/O)."""

from __future__ import annotations

import dataclasses

import pytest

from nfc_cli.nfc.atr import AtrInfo, parse

PREFIX = "3b8f8001804f0ca000000306"


def _atr(ss: int, nn: int) -> bytes:
    body = bytes.fromhex(PREFIX) + bytes([ss, nn >> 8, nn & 0xFF, 0, 0, 0, 0])
    tck = 0
    for b in body[1:]:
        tck ^= b
    return body + bytes([tck])


def test_live_x3_atr():
    info = parse(bytes.fromhex("3b8f8001804f0ca0000003060300010000000" + "06a"))
    assert isinstance(info, AtrInfo)
    assert info.standard == "ISO14443A-3"
    assert info.card_name == 0x0001
    assert info.family == "classic_1k"
    assert info.checksum_ok is True


def test_frozen():
    info = parse(_atr(0x03, 1))
    with pytest.raises(dataclasses.FrozenInstanceError):
        info.family = "x"  # type: ignore[misc]


@pytest.mark.parametrize(
    "ss,nn,family",
    [
        (0x03, 0x0002, "classic_4k"),
        (0x03, 0x0026, "classic_mini"),
        (0x03, 0x0003, "ultralight_ntag"),
        (0x03, 0x003A, "ultralight_c"),
        (0x02, 0x0030, "topaz"),
        (0x11, 0x003B, "felica"),
        (0x11, 0x0014, "felica"),
        (0x11, 0x0015, "felica"),
        (0x0B, 0x0000, "iso15693"),
        (0x0B, 0x0013, "iso15693"),
        (0x07, 0x0000, "iso15693"),
        (0x06, 0x001B, "picopass"),
        (0x06, 0x001C, "picopass"),
        (0x06, 0x001D, "picopass"),
        (0x0A, 0x0019, "picopass"),
        (0x06, 0x0007, "srix"),
    ],
)
def test_families(ss, nn, family):
    info = parse(_atr(ss, nn))
    assert info.family == family
    assert info.card_name == nn
    assert info.checksum_ok


def test_manual_recorded_atrs():
    for hexs, fam in [
        ("3B8F8001804F0CA0000003060300020000000069", "classic_4k"),
        ("3B8F8001804F0CA000000306030003000000006" + "8", "ultralight_ntag"),
        ("3B8F8001804F0CA000000306110 03B00000000 42".replace(" ", ""), "felica"),
        ("3B8F8001804F0CA00000030606001B00000000 75".replace(" ", ""), "picopass"),
    ]:
        assert parse(bytes.fromhex(hexs)).family == fam


def test_type4_non_rid():
    hist = bytes([0x80, 0x73, 0xC8, 0x21, 0x10])
    body = bytes([0x3B, 0x80 | len(hist), 0x80, 0x01]) + hist
    tck = 0
    for b in body[1:]:
        tck ^= b
    info = parse(body + bytes([tck]))
    assert info.family == "iso14443_4"
    assert info.historical == hist


def test_bad_checksum_is_unknown():
    atr = bytearray.fromhex("3b8f8001804f0ca000000306030001000000006a")
    atr[-1] ^= 0xFF
    info = parse(bytes(atr))
    assert info.family == "unknown"
    assert info.checksum_ok is False


@pytest.mark.parametrize("raw", [b"", b"\x3b", b"\x00\x01\x02", b"\x3b\x8f\x80"])
def test_garbage_no_exception(raw):
    assert parse(raw).family == "unknown"
