"""PC/SC part 3 supplemental-document ATR parser and tag-family detection.

Pure functions, no I/O. Contactless ATR layout (20 bytes)::

    3B 8F 80 01 80 4F 0C A0 00 00 03 06 SS NN NN 00 00 00 00 TCK

``SS`` is the standard byte, ``NN NN`` the card name, ``TCK`` the XOR of
bytes 1..18. ATRs without the RID are treated as ISO 14443-4 candidates.
"""

from __future__ import annotations

from dataclasses import dataclass

_RID_PREFIX = bytes.fromhex("3b8f8001804f0ca000000306")
_PICOPASS_NAMES = range(0x17, 0x21)

_STANDARDS = {
    0x02: "ISO14443A-2",
    0x03: "ISO14443A-3",
    0x06: "ISO14443B-2",
    0x07: "ISO15693",
    0x09: "ISO15693-1",
    0x0A: "ISO15693-2",
    0x0B: "ISO15693-3",
    0x11: "FeliCa",
    0x12: "FeliCa",
}

_NAME_FAMILIES = {
    0x0001: "classic_1k",
    0x0002: "classic_4k",
    0x0026: "classic_mini",
    0x0003: "ultralight_ntag",
    0x003D: "ultralight_ntag",
    0x003A: "ultralight_c",
    0x0030: "topaz",
}


@dataclass(frozen=True)
class AtrInfo:
    """Result of parsing an ATR."""

    raw: bytes
    checksum_ok: bool
    standard: str | None = None
    standard_byte: int | None = None
    card_name: int | None = None
    family: str = "unknown"
    historical: bytes = b""


def _xor(data: bytes) -> int:
    out = 0
    for b in data:
        out ^= b
    return out


def _family_from_rid(ss: int, nn: int) -> str:
    if ss in (0x11, 0x12):
        return "felica"
    if ss in (0x06, 0x0A) and nn in _PICOPASS_NAMES:
        return "picopass"
    if ss == 0x06 and nn == 0x0007:
        return "srix"
    if ss in (0x07, 0x09, 0x0A, 0x0B):
        return "iso15693"
    if ss in (0x02, 0x03):
        return _NAME_FAMILIES.get(nn, "unknown")
    return "unknown"


def parse(atr: bytes) -> AtrInfo:
    """Parse an ATR; never raises. Bad checksum or unknown layout -> family 'unknown'."""
    raw = bytes(atr)
    if len(raw) < 3 or raw[0] != 0x3B or _xor(raw[1:-1]) != raw[-1]:
        return AtrInfo(raw=raw, checksum_ok=False)
    if len(raw) == 20 and raw.startswith(_RID_PREFIX):
        ss = raw[12]
        nn = (raw[13] << 8) | raw[14]
        return AtrInfo(
            raw=raw,
            checksum_ok=True,
            standard=_STANDARDS.get(ss),
            standard_byte=ss,
            card_name=nn,
            family=_family_from_rid(ss, nn),
        )
    n = raw[1] & 0x0F
    if raw[1] & 0xF0 == 0x80 and len(raw) == 4 + n + 1 and raw[2:4] == b"\x80\x01":
        return AtrInfo(
            raw=raw,
            checksum_ok=True,
            standard="ISO14443-4",
            family="iso14443_4",
            historical=raw[4 : 4 + n],
        )
    return AtrInfo(raw=raw, checksum_ok=True)
