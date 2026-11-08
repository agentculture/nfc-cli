"""Fake reader and card simulators for hardware-free tests and ``ui serve --fake``.

Pure module: no CLI, USB or PC/SC imports. :class:`FakeTransport` implements the
:class:`~nfc_cli.nfc.transport.Transport` protocol (plus ``power_off`` and a no-op
``claim``) and answers the ACR1552 pseudo-APDUs: ``FF CA`` (UID), ``FF 82`` (load
key), ``FF 86`` (authenticate), ``FF B0`` (read) and ``FF D6`` (update). A Type 4
tag also answers ISO 7816 SELECT / READ BINARY / UPDATE BINARY for the NDEF app.
"""

from __future__ import annotations

import contextlib
from typing import Iterator

from nfc_cli.nfc.errors import CardRemoved, NfcError, NoCard, ReaderBusy

SW_OK = b"\x90\x00"
SW_DENIED = b"\x63\x00"  # auth / operation failed
SW_NOT_AUTH = b"\x69\x82"  # security status not satisfied
SW_NOT_FOUND = b"\x6a\x82"  # file / app not found
SW_NO_FILE = b"\x69\x85"  # conditions not satisfied (nothing selected)
SW_WRONG_LEN = b"\x67\x00"
SW_BAD_OFFSET = b"\x6b\x00"
SW_BAD_INS = b"\x6d\x00"

DEFAULT_KEY = bytes.fromhex("FFFFFFFFFFFF")
TRANSPORT_TRAILER_TAIL = bytes.fromhex("ff078069")  # access bits + GPB (transport config)
NDEF_AID = bytes.fromhex("D2760000850101")

ATR_CLASSIC_1K = "3b8f8001804f0ca000000306030001000000006a"
ATR_NTAG = "3b8f8001804f0ca0000003060300030000000068"
ATR_ISO15693 = "3b8f8001804f0ca0000003060b00140000000077"
ATR_TYPE4 = "3b8180018080"


class FakeCard:
    """Base card: handles ``FF CA``; subclasses implement :meth:`handle`."""

    atr = bytes.fromhex(ATR_CLASSIC_1K)

    def __init__(self, uid: bytes):
        self.uid = bytes(uid)

    def power_on(self) -> None:
        """Reset volatile state (auth, selections, loaded keys)."""

    def handle(self, apdu: bytes) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError


def _split(apdu: bytes) -> tuple[int, int, int, int, bytes, int | None]:
    """(cla, ins, p1, p2, data, le) of a short APDU."""
    if len(apdu) < 4:
        raise NfcError("malformed APDU", "APDUs are at least 4 bytes")
    cla, ins, p1, p2 = apdu[:4]
    rest = apdu[4:]
    if not rest:
        return cla, ins, p1, p2, b"", None
    if len(rest) == 1:
        return cla, ins, p1, p2, b"", rest[0]
    lc = rest[0]
    data = rest[1 : 1 + lc]
    tail = rest[1 + lc :]
    return cla, ins, p1, p2, data, (tail[0] if tail else None)


# --------------------------------------------------------------------- MIFARE Classic


def _access_bits(trailer: bytes) -> list[int]:
    """C1C2C3 (as a 3-bit int, C1 high) for the four blocks of a sector."""
    b6, b7, b8 = trailer[6], trailer[7], trailer[8]
    out = []
    for g in range(4):
        c1 = (b7 >> (4 + g)) & 1
        c2 = (b8 >> g) & 1
        c3 = (b8 >> (4 + g)) & 1
        out.append(c1 << 2 | c2 << 1 | c3)
    del b6
    return out


# data block conditions: cond -> (read by, write by); "A" = either key, "B" = key B only
_DATA_RULES = {
    0b000: ("AB", "AB"),
    0b010: ("AB", ""),
    0b100: ("AB", "B"),
    0b110: ("AB", "B"),
    0b001: ("AB", ""),
    0b011: ("B", "B"),
    0b101: ("B", ""),
    0b111: ("", ""),
}
# trailer conditions: cond -> (keyA w, acc r, acc w, keyB r, keyB w)
_TRAILER_RULES = {
    0b000: ("A", "A", "", "A", "A"),
    0b010: ("", "A", "", "A", ""),
    0b100: ("B", "AB", "", "", "B"),
    0b110: ("", "AB", "", "", ""),
    0b001: ("A", "A", "A", "A", "A"),
    0b011: ("B", "AB", "B", "", "B"),
    0b101: ("", "AB", "B", "", ""),
    0b111: ("", "AB", "", "", ""),
}


class MifareClassic1K(FakeCard):
    """MIFARE Classic 1K: 64 blocks of 16 bytes, key A/B auth, access bits."""

    def __init__(self, uid: bytes, *, magic: bool = False, blocks: list[bytes] | None = None):
        super().__init__(uid)
        self.magic = magic
        self.atr = bytes.fromhex(ATR_CLASSIC_1K)
        if blocks is None:
            blocks = self.blank_blocks(uid)
        if len(blocks) != 64 or any(len(b) != 16 for b in blocks):
            raise ValueError("Classic 1K needs 64 blocks of 16 bytes")
        self.blocks = [bytes(b) for b in blocks]
        self.keyslots: dict[int, bytes] = {}
        self.auth: tuple[int, str] | None = None  # (sector, "A"|"B")

    @staticmethod
    def blank_blocks(uid: bytes, block0: bytes | None = None) -> list[bytes]:
        if block0 is None:
            bcc = uid[0] ^ uid[1] ^ uid[2] ^ uid[3]
            block0 = bytes(uid[:4]) + bytes([bcc, 0x08, 0x04, 0x00]) + bytes(8)
        blocks = [bytes(16)] * 64
        blocks[0] = bytes(block0)
        for s in range(16):
            blocks[s * 4 + 3] = DEFAULT_KEY + TRANSPORT_TRAILER_TAIL + DEFAULT_KEY
        return blocks

    def power_on(self) -> None:
        self.auth = None
        self.keyslots = {}

    # -- helpers
    @staticmethod
    def _is_trailer(block: int) -> bool:
        return block % 4 == 3

    def _cond(self, block: int) -> int:
        trailer = self.blocks[block // 4 * 4 + 3]
        bits = _access_bits(trailer)
        return bits[3] if self._is_trailer(block) else bits[block % 4]

    def _authed_as(self, block: int) -> str | None:
        if self.auth and self.auth[0] == block // 4:
            return self.auth[1]
        return None

    def _allowed(self, who: str | None, rule: str) -> bool:
        return who is not None and who in rule

    # -- APDUs
    def handle(self, apdu: bytes) -> bytes:
        cla, ins, p1, p2, data, le = _split(apdu)
        if cla != 0xFF:
            return SW_BAD_INS
        if ins == 0x82:
            if len(data) != 6:
                return SW_WRONG_LEN
            self.keyslots[p2] = bytes(data)
            return SW_OK
        if ins == 0x86:
            return self._authenticate(data)
        if ins == 0xB0:
            return self._read(p2, le or 16)
        if ins == 0xD6:
            return self._update(p2, data)
        return SW_BAD_INS

    def _authenticate(self, data: bytes) -> bytes:
        if len(data) != 5:
            return SW_WRONG_LEN
        block, ktype, slot = data[2], data[3], data[4]
        if block >= 64 or ktype not in (0x60, 0x61) or slot not in self.keyslots:
            self.auth = None
            return SW_DENIED
        trailer = self.blocks[block // 4 * 4 + 3]
        key = trailer[:6] if ktype == 0x60 else trailer[10:16]
        if self.keyslots[slot] != key:
            self.auth = None
            return SW_DENIED
        self.auth = (block // 4, "A" if ktype == 0x60 else "B")
        return SW_OK

    def _read(self, block: int, length: int) -> bytes:
        if block >= 64 or length != 16:
            return SW_WRONG_LEN if length != 16 else SW_DENIED
        who = self._authed_as(block)
        if who is None:
            return SW_NOT_AUTH
        raw = self.blocks[block]
        if self._is_trailer(block):
            _, acc_r, _, keyb_r, _ = _TRAILER_RULES[self._cond(block)]
            keyb = raw[10:16] if self._allowed(who, keyb_r) else bytes(6)
            acc = raw[6:10] if self._allowed(who, acc_r) else bytes(4)
            return bytes(6) + acc + keyb + SW_OK
        if not self._allowed(who, _DATA_RULES[self._cond(block)][0]):
            return SW_DENIED
        return raw + SW_OK

    def _update(self, block: int, data: bytes) -> bytes:
        if len(data) != 16:
            return SW_WRONG_LEN
        if block >= 64:
            return SW_DENIED
        who = self._authed_as(block)
        if who is None:
            return SW_NOT_AUTH
        if block == 0 and not self.magic:
            return SW_DENIED
        if self._is_trailer(block):
            keya_w, _, acc_w, _, keyb_w = _TRAILER_RULES[self._cond(block)]
            old = self.blocks[block]
            new = bytearray(old)
            if self._allowed(who, keya_w):
                new[0:6] = data[0:6]
            if self._allowed(who, acc_w):
                new[6:10] = data[6:10]
            if self._allowed(who, keyb_w):
                new[10:16] = data[10:16]
            self.blocks[block] = bytes(new)
            return SW_OK
        if not self._allowed(who, _DATA_RULES[self._cond(block)][1]):
            return SW_DENIED
        self.blocks[block] = bytes(data)
        return SW_OK


# --------------------------------------------------------------------- NTAG215


class Ntag215(FakeCard):
    """NTAG215 (Type 2): 135 pages of 4 bytes, CC at page 3, 504 bytes user memory."""

    PAGES = 135

    def __init__(self, uid: bytes | None = None, *, pages: list[bytes] | None = None):
        uid = bytes(uid) if uid else bytes.fromhex("04a1b2c3d4e580")
        if len(uid) != 7:
            raise ValueError("NTAG215 UID is 7 bytes")
        super().__init__(uid)
        self.atr = bytes.fromhex(ATR_NTAG)
        if pages is None:
            bcc0 = 0x88 ^ uid[0] ^ uid[1] ^ uid[2]
            bcc1 = uid[3] ^ uid[4] ^ uid[5] ^ uid[6]
            pages = [bytes(4)] * self.PAGES
            pages[0] = uid[0:3] + bytes([bcc0])
            pages[1] = uid[3:7]
            pages[2] = bytes([bcc1, 0x48, 0x00, 0x00])
            pages[3] = bytes.fromhex("e1103e00")
        if len(pages) != self.PAGES or any(len(p) != 4 for p in pages):
            raise ValueError("NTAG215 needs 135 pages of 4 bytes")
        self.pages = [bytes(p) for p in pages]

    def handle(self, apdu: bytes) -> bytes:
        cla, ins, _p1, p2, data, le = _split(apdu)
        if cla != 0xFF:
            return SW_BAD_INS
        if ins == 0xB0:
            length = le or 16
            if p2 >= self.PAGES:
                return SW_DENIED
            out = b"".join(self.pages[(p2 + i) % self.PAGES] for i in range((length + 3) // 4))
            return out[:length] + SW_OK
        if ins == 0xD6:
            if len(data) != 4:
                return SW_WRONG_LEN
            if p2 < 3 or p2 >= self.PAGES:
                return SW_DENIED
            self.pages[p2] = bytes(data)
            return SW_OK
        return SW_BAD_INS


# --------------------------------------------------------------------- ISO 15693


class Iso15693(FakeCard):
    """ISO 15693 tag with 4-byte blocks (default 28 blocks, SLIX-like)."""

    def __init__(self, uid: bytes | None = None, *, nblocks: int = 28):
        uid = bytes(uid) if uid else bytes.fromhex("e00401501122334a")[:8]
        super().__init__(uid)
        self.atr = bytes.fromhex(ATR_ISO15693)
        self.blocks = [bytes(4) for _ in range(nblocks)]

    def handle(self, apdu: bytes) -> bytes:
        cla, ins, _p1, p2, data, le = _split(apdu)
        if cla != 0xFF:
            return SW_BAD_INS
        if ins == 0xB0:
            length = le or 4
            count = (length + 3) // 4
            if p2 + count > len(self.blocks):
                return SW_DENIED
            return b"".join(self.blocks[p2 : p2 + count])[:length] + SW_OK
        if ins == 0xD6:
            if len(data) != 4:
                return SW_WRONG_LEN
            if p2 >= len(self.blocks):
                return SW_DENIED
            self.blocks[p2] = bytes(data)
            return SW_OK
        return SW_BAD_INS


# --------------------------------------------------------------------- Type 4


class Type4Tag(FakeCard):
    """ISO 14443-4 Type 4 tag with the NDEF application (CC E103, NDEF E104)."""

    def __init__(self, uid: bytes | None = None, *, ndef_size: int = 0xFF):
        super().__init__(bytes(uid) if uid else bytes.fromhex("0811223344556677")[:7])
        self.atr = bytes.fromhex(ATR_TYPE4)
        self.ndef_size = ndef_size
        self.cc = (
            b"\x00\x0f\x20\x00\x3b\x00\x34\x04\x06\xe1\x04"
            + ndef_size.to_bytes(2, "big")
            + b"\x00\x00"
        )
        self.ndef = bytearray(ndef_size)  # first two bytes are NLEN
        self.app_selected = False
        self.selected: int | None = None

    def power_on(self) -> None:
        self.app_selected = False
        self.selected = None

    def _file(self) -> bytearray | None:
        if self.selected == 0xE103:
            return bytearray(self.cc)
        if self.selected == 0xE104:
            return self.ndef
        return None

    def handle(self, apdu: bytes) -> bytes:
        cla, ins, p1, p2, data, le = _split(apdu)
        if cla == 0xFF:
            return SW_BAD_INS
        if ins == 0xA4:
            return self._select(p1, p2, data)
        if not self.app_selected or self.selected is None:
            return SW_NO_FILE
        offset = p1 << 8 | p2
        if ins == 0xB0:
            buf = self._file()
            length = le or 256
            if buf is None or offset > len(buf):
                return SW_BAD_OFFSET
            return bytes(buf[offset : offset + length]) + SW_OK
        if ins == 0xD6:
            if self.selected != 0xE104:
                return SW_DENIED
            if offset + len(data) > len(self.ndef):
                return SW_BAD_OFFSET
            self.ndef[offset : offset + len(data)] = data
            return SW_OK
        return SW_BAD_INS

    def _select(self, p1: int, p2: int, data: bytes) -> bytes:
        if p1 == 0x04:
            if data == NDEF_AID:
                self.app_selected = True
                self.selected = None
                return SW_OK
            return SW_NOT_FOUND
        if p1 == 0x00 and len(data) == 2 and self.app_selected:
            fid = data[0] << 8 | data[1]
            if fid in (0xE103, 0xE104):
                self.selected = fid
                return SW_OK
        return SW_NOT_FOUND


# --------------------------------------------------------------------- transport


class FakeTransport:
    """In-memory reader implementing the Transport protocol with fault injection."""

    def __init__(self, card: FakeCard | None = None):
        self.card = card
        self.log: list[tuple[str, bytes]] = []  # ("apdu"|"escape", bytes)
        self.escape_responses: dict[bytes, bytes] = {}
        self._removed = False
        self._remove_after: int | None = None
        self._apdu_count = 0
        self._auth_fail: set[int] | bool = False
        self._sw: list[list] = []  # [ins|None, sw, remaining|None]
        self._busy = False
        self.powered = False
        self.closed = False

    # -- fault injection
    def card_removed_after(self, n_apdus: int) -> None:
        """Let ``n_apdus`` more transmits succeed, then the card is gone."""
        self._remove_after = self._apdu_count + n_apdus

    def remove_card(self) -> None:
        self._removed = True

    def insert_card(self, card: FakeCard) -> None:
        self.card, self._removed, self._remove_after = card, False, None
        self.powered = False

    def auth_fail(self, blocks: "set[int] | list[int] | bool" = True) -> None:
        """Make FF 86 answer 63 00 for all blocks (True), a set of blocks, or none (False)."""
        self._auth_fail = set(blocks) if isinstance(blocks, (set, list, tuple)) else bool(blocks)

    def override_sw(self, sw: bytes | int, *, ins: int | None = None, times: int | None = None):
        """Answer ``sw`` (data empty) for APDUs with this INS (any if None), ``times`` times."""
        if isinstance(sw, int):
            sw = sw.to_bytes(2, "big")
        self._sw.append([ins, bytes(sw), times])

    def set_busy(self, busy: bool = True) -> None:
        self._busy = busy

    def clear_faults(self) -> None:
        self._remove_after = None
        self._removed = False
        self._auth_fail = False
        self._sw = []
        self._busy = False

    # -- Transport protocol
    def _check_busy(self) -> None:
        if self._busy:
            raise ReaderBusy("the reader is in use by another process (fake)")

    def _gone(self) -> bool:
        return self.card is None or self._removed

    def present(self) -> bool:
        self._check_busy()
        return not self._gone()

    def power_on(self) -> bytes:
        self._check_busy()
        if self._gone():
            raise NoCard()
        self.powered = True
        self.card.power_on()
        return self.card.atr

    def power_off(self) -> None:
        self._check_busy()
        self.powered = False
        if self.card is not None:
            self.card.power_on()  # drop volatile state

    def transmit(self, apdu: bytes) -> tuple[bytes, bytes]:
        self._check_busy()
        apdu = bytes(apdu)
        if self._gone():
            raise NoCard() if self.card is None else CardRemoved()
        if self._remove_after is not None and self._apdu_count >= self._remove_after:
            self._removed = True
            raise CardRemoved()
        self._apdu_count += 1
        self.log.append(("apdu", apdu))
        ins = apdu[1] if len(apdu) > 1 else None
        for rule in self._sw:
            if rule[0] in (None, ins) and (rule[2] is None or rule[2] > 0):
                if rule[2] is not None:
                    rule[2] -= 1
                return b"", rule[1]
        if ins == 0x86 and len(apdu) >= 8 and self._auth_fail:
            if self._auth_fail is True or apdu[7] in self._auth_fail:
                return b"", SW_DENIED
        resp = self._dispatch(apdu)
        return resp[:-2], resp[-2:]

    def _dispatch(self, apdu: bytes) -> bytes:
        if len(apdu) >= 4 and apdu[0] == 0xFF and apdu[1] == 0xCA:
            return self.card.uid + SW_OK
        return self.card.handle(apdu)

    def escape(self, cmd: bytes) -> bytes:
        self._check_busy()
        cmd = bytes(cmd)
        self.log.append(("escape", cmd))
        return self.escape_responses.get(cmd, b"")

    def close(self) -> None:
        self.closed = True

    @contextlib.contextmanager
    def claim(self) -> Iterator["FakeTransport"]:
        self._check_busy()
        yield self

    def __enter__(self) -> "FakeTransport":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# --------------------------------------------------------------------- scenarios

X3_UID = bytes.fromhex("cb30c560")
X3_BLOCK0 = bytes.fromhex("cb30c5605e0804006263646566676869")


def _x3() -> FakeTransport:
    blocks = MifareClassic1K.blank_blocks(X3_UID, X3_BLOCK0)
    return FakeTransport(MifareClassic1K(X3_UID, blocks=blocks))


SCENARIOS = {
    "x3": _x3,
    "blank-classic": lambda: FakeTransport(MifareClassic1K(bytes.fromhex("01020304"))),
    "magic-classic": lambda: FakeTransport(MifareClassic1K(bytes.fromhex("01020304"), magic=True)),
    "ntag215": lambda: FakeTransport(Ntag215()),
    "iso15693": lambda: FakeTransport(Iso15693()),
    "type4": lambda: FakeTransport(Type4Tag()),
    "empty": lambda: FakeTransport(None),
}


def load_scenario(name: str) -> FakeTransport:
    """Build a FakeTransport for a named scenario (``x3`` reproduces the live X3 tag)."""
    try:
        return SCENARIOS[name]()
    except KeyError:
        raise NfcError(
            f"unknown fake scenario {name!r}", f"choose one of: {', '.join(sorted(SCENARIOS))}"
        ) from None
