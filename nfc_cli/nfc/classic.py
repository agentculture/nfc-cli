"""MIFARE Classic operations over PC/SC pseudo-APDUs. Pure module: no CLI/USB imports.

Sector layout: 1K = 16 x 4 blocks; 4K = 32 x 4 blocks then 8 x 16 blocks (blocks 128..255).
After a failed authentication the card drops out of the ready state, so ``authenticate``
power-cycles the card before raising (the live probe needed this).
"""

from __future__ import annotations

from typing import Iterable

from nfc_cli.nfc import ndef
from nfc_cli.nfc.errors import ApduError, AuthFailed, CardRemoved, NfcError
from nfc_cli.nfc.transport import Transport

SW_OK = b"\x90\x00"
DEFAULT_KEYS = tuple(
    bytes.fromhex(k) for k in ("FFFFFFFFFFFF", "A0A1A2A3A4A5", "D3F7D3F7D3F7", "000000000000")
)
BLOCK_SIZE = 16


class ClassicError(NfcError):
    """A Classic operation was refused or malformed (nothing was sent to the card)."""

    default_remediation = "check the block number and data; use --allow-trailer only on purpose"


# ---------------------------------------------------------------- layout


def _check_block(block: int) -> None:
    if not 0 <= block <= 255:
        raise ClassicError(f"block {block} out of range (0..255)")


def sector_of(block: int) -> int:
    _check_block(block)
    return block // 4 if block < 128 else 32 + (block - 128) // 16


def first_block(sector: int) -> int:
    if not 0 <= sector <= 39:
        raise ClassicError(f"sector {sector} out of range (0..39)")
    return sector * 4 if sector < 32 else 128 + (sector - 32) * 16


def sector_size(sector: int) -> int:
    first_block(sector)
    return 4 if sector < 32 else 16


def trailer_block(sector: int) -> int:
    return first_block(sector) + sector_size(sector) - 1


def is_trailer(block: int) -> bool:
    return trailer_block(sector_of(block)) == block


def sector_count(blocks: int) -> int:
    """Sectors on a card with ``blocks`` blocks (64 -> 16, 256 -> 40)."""
    try:
        return {64: 16, 128: 32, 256: 40}[blocks]
    except KeyError:
        raise ClassicError(f"unsupported size {blocks}") from None


# ---------------------------------------------------------------- APDUs


def _ok(data: bytes, sw: bytes, what: str) -> bytes:
    if sw != SW_OK:
        raise ApduError(sw, f"{what} failed: card returned {sw.hex().upper()}")
    return data


# MIFARE Classic access conditions (NXP MF1S50 datasheet, section 8.7). C1C2C3 as a 3-bit
# int, C1 high. Which key type may WRITE a data block / any field of the trailer.
_DATA_WRITE = {
    0b000: "AB",
    0b010: "",
    0b100: "B",
    0b110: "B",
    0b001: "",
    0b011: "B",
    0b101: "",
    0b111: "",
}
# trailer: cond -> key types that may write at least one of key A / access bits / key B
_TRAILER_WRITE = {
    0b000: "A",
    0b010: "",
    0b100: "B",
    0b110: "",
    0b001: "A",
    0b011: "B",
    0b101: "B",
    0b111: "",
}


def access_conditions(trailer: bytes) -> list[int]:
    """C1C2C3 for access groups 0..3 (group 3 = the trailer) from a trailer's bytes 6-8."""
    b7, b8 = trailer[7], trailer[8]
    return [
        ((b7 >> (4 + g)) & 1) << 2 | ((b8 >> g) & 1) << 1 | ((b8 >> (4 + g)) & 1) for g in range(4)
    ]


def write_key_types(trailer: bytes, block: int) -> tuple[str, ...]:
    """Key types allowed to write ``block`` under ``trailer``'s access bits ('' = none)."""
    first = first_block(sector_of(block))
    idx = block - first
    if is_trailer(block):
        group = 3
    elif sector_size(sector_of(block)) == 4:
        group = idx
    else:  # 4K large sectors: blocks 0-4, 5-9, 10-14 share access groups 0, 1, 2
        group = min(idx // 5, 2)
    cond = access_conditions(trailer)[group]
    allowed = (_TRAILER_WRITE if group == 3 else _DATA_WRITE)[cond]
    return tuple(k for k in ("A", "B") if k in allowed)


def _power_cycle(t: Transport) -> None:
    off = getattr(t, "power_off", None)
    if off is not None:
        off()
    t.power_on()


def _key_bytes(key: bytes | str) -> bytes:
    k = bytes.fromhex(key) if isinstance(key, str) else bytes(key)
    if len(k) != 6:
        raise ClassicError("a MIFARE Classic key is 6 bytes (12 hex digits)")
    return k


def get_uid(t: Transport) -> bytes:
    data, sw = t.transmit(bytes.fromhex("FFCA000000"))
    return _ok(data, sw, "get UID")


def authenticate(t: Transport, sector: int, key: bytes | str, key_type: str = "A") -> None:
    """Authenticate to ``sector`` with ``key`` (A or B); AuthFailed after a card power-cycle."""
    kt = {"A": 0x60, "B": 0x61}.get(str(key_type).upper())
    if kt is None:
        raise ClassicError("key type must be A or B")
    k = _key_bytes(key)
    _ok(*t.transmit(b"\xff\x82\x00\x00\x06" + k), "load key")
    blk = first_block(sector)
    _, sw = t.transmit(bytes([0xFF, 0x86, 0, 0, 5, 1, 0, blk, kt, 0]))
    if sw != SW_OK:
        _power_cycle(t)
        raise AuthFailed(f"authentication failed for sector {sector} key {key_type.upper()}")


def read_block(t: Transport, block: int) -> bytes:
    _check_block(block)
    data = _ok(*t.transmit(bytes([0xFF, 0xB0, 0, block, 0x10])), f"read block {block}")
    if len(data) != BLOCK_SIZE:
        raise ApduError(SW_OK, f"read block {block} returned {len(data)} bytes")
    return data


def write_block(t: Transport, block: int, data: bytes, allow_trailer: bool = False) -> None:
    """Write 16 bytes. Block 0 and trailers need ``allow_trailer``; trailers must have valid
    access bits (checked before any APDU is sent)."""
    _check_block(block)
    data = bytes(data)
    if len(data) != BLOCK_SIZE:
        raise ClassicError(f"a block is 16 bytes, got {len(data)}")
    trailer = is_trailer(block)
    if (trailer or block == 0) and not allow_trailer:
        what = "block 0" if block == 0 else f"trailer block {block}"
        raise ClassicError(
            f"refusing to write {what}",
            "pass allow_trailer / --allow-trailer if you really mean it",
        )
    if trailer and not ndef.validate_access_bits(data):
        raise ClassicError(
            f"refusing to write trailer {block}: access bits are inconsistent "
            "(this would permanently brick the sector)",
            "fix bytes 6-8 of the trailer so each bit has its inverted copy",
        )
    _ok(*t.transmit(bytes([0xFF, 0xD6, 0, block, 0x10]) + data), f"write block {block}")


def read_sector(t: Transport, sector: int, key: bytes | str, key_type: str = "A") -> list[bytes]:
    authenticate(t, sector, key, key_type)
    first = first_block(sector)
    return [read_block(t, first + i) for i in range(sector_size(sector))]


def probe_block0_writable(t: Transport, key: bytes | str = DEFAULT_KEYS[0]) -> bool:
    """Non-destructively test whether block 0 (the UID block) accepts a write.

    Writes block 0's **own current bytes back to itself** after a normal sector-0
    authentication. A genuine MIFARE Classic chip has block 0 permanently read-only
    and refuses; a UID-rewritable ("CUID"/gen2) card accepts the write. The content
    is identical either way, so the tag is unchanged.

    A refused write can make the chip halt and the reader report the card as mute;
    that is treated as "not writable", and the card is power-cycled so later calls
    work. Raises if the card never answers again.
    """
    authenticate(t, 0, key, "A")
    current = read_block(t, 0)
    try:
        _ok(*t.transmit(bytes([0xFF, 0xD6, 0, 0, 0x10]) + current), "block 0 self-write")
    except (ApduError, CardRemoved):
        _power_cycle(t)
        get_uid(t)  # confirm the same tag is still reachable; re-raises if it is gone
        return False
    return True


# ---------------------------------------------------------------- key dictionary


def load_keys_file(path: str) -> list[bytes]:
    """Keys (12 hex digits) one per line; blank lines and ``#`` comments are ignored."""
    keys = []
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            text = line.split("#", 1)[0].strip()
            if not text:
                continue
            try:
                keys.append(_key_bytes(text))
            except (ValueError, ClassicError):
                raise ClassicError(f"{path}:{n}: invalid key {text!r}") from None
    return keys


def find_key(
    t: Transport,
    sector: int,
    extra_keys: Iterable[bytes | str] = (),
    key_types: Iterable[str] = ("A", "B"),
) -> tuple[str, bytes]:
    """Try the default dictionary then ``extra_keys`` as A then B; (key_type, key) or AuthFailed.

    ``authenticate`` power-cycles after each failure, so the next attempt starts clean.
    """
    keys: list[bytes] = []
    for k in (*DEFAULT_KEYS, *(_key_bytes(x) for x in extra_keys)):
        if k not in keys:
            keys.append(k)
    for kt in key_types:
        for k in keys:
            try:
                authenticate(t, sector, k, kt)
            except AuthFailed:
                continue
            return kt.upper(), k
    raise AuthFailed(f"no known key opens sector {sector}", "supply more with --keys-file")
