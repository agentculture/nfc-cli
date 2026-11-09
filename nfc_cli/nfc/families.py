"""Block-level operations for the non-Classic tag families.

Pure module (no CLI/USB imports). Each family is a small stateless class whose methods take a
:class:`~nfc_cli.nfc.transport.Transport`. MIFARE Classic lives in ``nfc_cli.nfc.classic``;
:func:`family_ops` returns a :class:`ClassicFamily` marker for those families.
"""

from __future__ import annotations

from nfc_cli.nfc import ndef
from nfc_cli.nfc.errors import ApduError, NfcError
from nfc_cli.nfc.transport import Transport

OK = b"\x90\x00"


class UnsupportedOperation(NfcError):
    default_message = "this operation is not supported for this tag family"
    default_remediation = "use a supported tag type, or read-only access for this family"


def _check(sw: bytes, what: str) -> None:
    if bytes(sw) != OK:
        raise ApduError(sw, f"{what} failed: card returned status {bytes(sw).hex().upper()}")


def _exchange(tr: Transport, apdu: bytes, what: str) -> bytes:
    data, sw = tr.transmit(apdu)
    _check(sw, what)
    return bytes(data)


class _Unsupported:
    """Shared marker behaviour: every operation explains where to go instead."""

    family = "unknown"
    reason = "this tag family is not supported"

    def __init__(self, family: str | None = None):
        if family:
            self.family = family

    def _fail(self, *_a, **_k):
        raise UnsupportedOperation(f"{self.family}: {self.reason}")

    read_blocks = write_blocks = read_ndef = write_ndef = _fail


class ClassicFamily(_Unsupported):
    """Marker: MIFARE Classic families are handled by ``nfc_cli.nfc.classic``."""

    module = "nfc_cli.nfc.classic"
    reason = "MIFARE Classic is handled by nfc_cli.nfc.classic, not nfc_cli.nfc.families"


class UnknownFamily(_Unsupported):
    reason = "unrecognised tag family; no block access is available"


# ------------------------------------------------------------------ Type 2


class Type2Ops:
    """MIFARE Ultralight / NTAG (NFC Forum Type 2): 4-byte pages."""

    page_size = 4
    first_data_page = 4

    def read_pages(self, tr: Transport, page: int, count: int = 4) -> bytes:
        if not 0 <= page <= 0xFF or not 1 <= count <= 63:
            raise UnsupportedOperation("page/count out of range")
        return _exchange(
            tr, bytes([0xFF, 0xB0, 0x00, page, count * self.page_size]), f"read page {page}"
        )

    def write_page(self, tr: Transport, page: int, data: bytes) -> None:
        if len(data) != self.page_size:
            raise UnsupportedOperation(f"a page is exactly {self.page_size} bytes")
        _exchange(
            tr, bytes([0xFF, 0xD6, 0x00, page, self.page_size]) + bytes(data), f"write {page}"
        )

    read_blocks = read_pages

    def write_blocks(self, tr: Transport, page: int, data: bytes) -> None:
        if len(data) % self.page_size:
            raise UnsupportedOperation("data must be a multiple of 4 bytes")
        for i in range(0, len(data), self.page_size):
            self.write_page(tr, page + i // self.page_size, data[i : i + self.page_size])

    def capability(self, tr: Transport) -> dict:
        """Parse the capability container (page 3); ``size`` is the NDEF data area in bytes."""
        return ndef.parse_type2_cc(self.read_pages(tr, 3, 1))

    def memory_size(self, tr: Transport) -> int:
        return self.capability(tr)["size"]

    def read_ndef(self, tr: Transport) -> bytes | None:
        size = self.memory_size(tr)
        raw = b""
        pages = size // self.page_size
        page = self.first_data_page
        while pages > 0:
            n = min(pages, 4)
            raw += self.read_pages(tr, page, n)
            page, pages = page + n, pages - n
        return ndef.tlv_unwrap(raw)

    def write_ndef(self, tr: Transport, message: bytes) -> None:
        cc = self.capability(tr)
        if cc["read_only"]:
            raise UnsupportedOperation("tag is read-only (capability container)")
        tlv = ndef.tlv_wrap(message)
        if len(tlv) > cc["size"]:
            raise UnsupportedOperation(
                f"NDEF message needs {len(tlv)} bytes but the tag holds {cc['size']}"
            )
        tlv += b"\x00" * (-len(tlv) % self.page_size)
        self.write_blocks(tr, self.first_data_page, tlv)


# ------------------------------------------------------------------ ISO 15693


class Iso15693Ops:
    """ISO 15693: blocks via Read/Update Binary (FF B0 / FF D6); system info via pass-through."""

    def command(self, tr: Transport, cmd: int, payload: bytes = b"", flags: int = 0) -> bytes:
        body = bytes([cmd]) + bytes(payload)
        apdu = bytes([0xFF, 0xFB, 0x00, flags, len(body)]) + body
        resp = _exchange(tr, apdu, f"ISO15693 command {cmd:02X}")
        if not resp:
            raise NfcError("empty ISO15693 response", "re-place the tag and retry")
        if resp[0] & 0x01:
            code = resp[1] if len(resp) > 1 else 0
            raise NfcError(
                f"ISO15693 tag reported error {code:02X} for command {cmd:02X}",
                "check the block number and that the block is not locked",
            )
        return resp[1:]  # strip the response-flags byte

    def system_info(self, tr: Transport) -> dict:
        """Get System Information (2Bh): block count and size when the tag reports them."""
        r = self.command(tr, 0x2B)
        info, pos = r[0], 9  # info flags + 8-byte UID
        out: dict = {"uid": r[1:9][::-1].hex().upper()}
        if info & 0x01:
            out["dsfid"], pos = r[pos], pos + 1
        if info & 0x02:
            out["afi"], pos = r[pos], pos + 1
        if info & 0x04:
            out["blocks"], out["block_size"] = r[pos] + 1, (r[pos + 1] & 0x1F) + 1
        return out

    def geometry(self, tr: Transport) -> tuple[int | None, int]:
        """(block count or None, block size); falls back to probing block 0."""
        try:
            info = self.system_info(tr)
            if "block_size" in info:
                return info["blocks"], info["block_size"]
        except (NfcError, IndexError):
            pass
        return None, len(self._read_binary(tr, 0, 0))

    @staticmethod
    def _address(block: int) -> tuple[int, int]:
        if not 0 <= block <= 0x7FF:
            raise UnsupportedOperation("ISO15693 blocks 0-2047 are addressable via Read Binary")
        return (block >> 8) & 0x07, block & 0xFF

    def _read_binary(self, tr: Transport, block: int, length: int) -> bytes:
        p1, p2 = self._address(block)
        return _exchange(tr, bytes([0xFF, 0xB0, p1, p2, length]), f"ISO15693 read block {block}")

    def read_block(self, tr: Transport, block: int, size: int = 4) -> bytes:
        """Read one block via the ACR1552 Read Binary pseudo-APDU (manual 1.09 5.3.3.4)."""
        return self._read_binary(tr, block, size)

    def write_block(self, tr: Transport, block: int, data: bytes) -> None:
        """Write one block via Update Binary (FF D6); data length must equal the block size."""
        _, size = self.geometry(tr)
        if len(data) != size:
            raise UnsupportedOperation(f"this tag has {size}-byte blocks; got {len(data)} bytes")
        p1, p2 = self._address(block)
        apdu = bytes([0xFF, 0xD6, p1, p2, len(data)]) + bytes(data)
        _exchange(tr, apdu, f"ISO15693 write block {block}")

    def read_blocks(self, tr: Transport, start: int, count: int) -> bytes:
        _, size = self.geometry(tr)
        return b"".join(self.read_block(tr, b, size) for b in range(start, start + count))

    def write_blocks(self, tr: Transport, start: int, data: bytes) -> None:
        _, size = self.geometry(tr)
        if len(data) % size:
            raise UnsupportedOperation(f"data must be a multiple of the {size}-byte block size")
        for i in range(0, len(data), size):
            self.write_block(tr, start + i // size, data[i : i + size])


# ------------------------------------------------------------------ Type 4


class Type4Ops:
    """ISO 14443-4 NFC Forum Type 4 NDEF (application D2760000850101)."""

    AID = bytes.fromhex("D2760000850101")
    CC_FILE = bytes.fromhex("E103")

    def _select(self, tr: Transport, p1: int, p2: int, data: bytes, what: str) -> None:
        _exchange(tr, bytes([0x00, 0xA4, p1, p2, len(data)]) + data + (b"\x00" * (p1 == 4)), what)

    def _read(self, tr: Transport, off: int, n: int) -> bytes:
        return _exchange(tr, bytes([0x00, 0xB0, off >> 8, off & 0xFF, n]), "READ BINARY")

    def _read_range(self, tr: Transport, off: int, n: int, chunk: int) -> bytes:
        out = b""
        while len(out) < n:
            out += self._read(tr, off + len(out), min(chunk, n - len(out)))
        return out

    def open_ndef(self, tr: Transport) -> dict:
        """Select the app and NDEF file; return ``{fid, max_size, mle, mlc, writable}``."""
        self._select(tr, 0x04, 0x00, self.AID, "SELECT NDEF application")
        self._select(tr, 0x00, 0x0C, self.CC_FILE, "SELECT capability container")
        cc = self._read(tr, 0, 15)
        if len(cc) < 15 or cc[7] != 0x04:
            raise NfcError("malformed Type 4 capability container", "this tag has no NDEF file")
        info = {
            "mle": int.from_bytes(cc[3:5], "big"),
            "mlc": int.from_bytes(cc[5:7], "big"),
            "fid": cc[9:11],
            "max_size": int.from_bytes(cc[11:13], "big"),
            "writable": cc[14] == 0x00,
        }
        self._select(tr, 0x00, 0x0C, info["fid"], "SELECT NDEF file")
        return info

    def read_ndef(self, tr: Transport) -> bytes | None:
        info = self.open_ndef(tr)
        chunk = max(1, min(info["mle"] or 0xFF, 0xFF))
        nlen = int.from_bytes(self._read(tr, 0, 2), "big")
        if nlen == 0:
            return None
        return self._read_range(tr, 2, nlen, chunk)

    def write_ndef(self, tr: Transport, message: bytes) -> None:
        info = self.open_ndef(tr)
        if not info["writable"]:
            raise UnsupportedOperation("NDEF file is write-protected")
        if len(message) + 2 > info["max_size"]:
            raise UnsupportedOperation(
                f"NDEF message needs {len(message) + 2} bytes but the file holds {info['max_size']}"
            )
        chunk = max(1, min(info["mlc"] or 0xFF, 0xFF))

        def update(off: int, data: bytes) -> None:
            _exchange(tr, bytes([0x00, 0xD6, off >> 8, off & 0xFF, len(data)]) + data, "UPDATE")

        update(0, b"\x00\x00")  # invalidate NLEN while the body is rewritten
        for i in range(0, len(message), chunk):
            update(2 + i, message[i : i + chunk])
        update(0, len(message).to_bytes(2, "big"))

    def read_blocks(self, *_a, **_k):
        raise UnsupportedOperation("iso14443_4: use read_ndef/write_ndef")

    write_blocks = read_blocks


# ------------------------------------------------------------------ read-only families


class ReadOnlyOps:
    """Direct block reads via ``FF B0 P1 P2 Le``; writes are refused, never guessed."""

    family = ""
    block_size = 4
    max_block = 0xFF

    def __init__(self, family: str | None = None):
        if family:
            self.family = family

    def _address(self, block: int) -> int:
        return block

    def valid(self, block: int) -> bool:
        return 0 <= block <= self.max_block

    def read_blocks(self, tr: Transport, start: int, count: int = 1) -> bytes:
        if count < 1 or not (self.valid(start) and self.valid(start + count - 1)):
            raise UnsupportedOperation(
                f"{self.family}: blocks {start}..{start + count - 1} outside 0..{self.max_block}"
            )
        addr = self._address(start)
        apdu = bytes([0xFF, 0xB0, (addr >> 8) & 0x0F, addr & 0xFF, count * self.block_size])
        return _exchange(tr, apdu, f"{self.family} read block {start}")

    def write_blocks(self, tr: Transport, start: int, data: bytes) -> None:
        raise UnsupportedOperation(
            f"{self.family}: writing is not supported (read-only access); "
            "no write sequence has been verified for this family"
        )

    write_block = write_blocks

    def read_ndef(self, tr: Transport):
        raise UnsupportedOperation(f"{self.family}: NDEF is not supported")

    write_ndef = read_ndef


class TopazOps(ReadOnlyOps):
    family = "topaz"
    block_size = 8  # addressed by byte (000h-7FFh); 8-byte blocks
    max_block = 0x7FF // 8

    def _address(self, block: int) -> int:
        return block * 8


class SrixOps(ReadOnlyOps):
    family = "srix"
    block_size = 4
    max_block = 0x7F  # 0FFh is the system area

    def valid(self, block: int) -> bool:
        return block == 0xFF or 0 <= block <= self.max_block


class PicopassOps(ReadOnlyOps):
    family = "picopass"
    block_size = 8
    max_block = 0xFF


class CtsOps(ReadOnlyOps):
    family = "cts"
    block_size = 2
    max_block = 0x1F


class FelicaOps(ReadOnlyOps):
    """FeliCa Read Without Encryption through ``FF 00 00 00 Lc <felica command>``."""

    family = "felica"
    block_size = 16
    max_block = 0xFF
    service = 0x0109  # service code from the manual's Read example (little-endian on the wire)

    def idm(self, tr: Transport) -> bytes:
        return _exchange(tr, bytes.fromhex("FFCA000000"), "FeliCa read IDm")[:8]

    def read_blocks(self, tr: Transport, start: int, count: int = 1) -> bytes:
        if count < 1 or count > 4 or not (self.valid(start) and self.valid(start + count - 1)):
            raise UnsupportedOperation("felica: read 1-4 blocks within 0..255 at a time")
        idm = self.idm(tr)
        blocks = b"".join(bytes([0x80, start + i]) for i in range(count))
        cmd = bytes([0x06]) + idm + b"\x01" + self.service.to_bytes(2, "little")
        cmd += bytes([count]) + blocks
        cmd = bytes([len(cmd) + 1]) + cmd
        resp = _exchange(tr, bytes([0xFF, 0x00, 0x00, 0x00, len(cmd)]) + cmd, "FeliCa read")
        if len(resp) >= 13 and resp[1] == 0x07 and resp[0] == len(resp):
            if resp[10] != 0 or resp[11] != 0:
                raise NfcError(
                    f"FeliCa status {resp[10]:02X} {resp[11]:02X}",
                    "the service or block is not readable without authentication",
                )
            return resp[13:]
        return resp  # reader already stripped the FeliCa framing


_OPS = {
    "ultralight_ntag": Type2Ops,
    "ultralight_c": Type2Ops,
    "iso15693": Iso15693Ops,
    "iso14443_4": Type4Ops,
    "felica": FelicaOps,
    "topaz": TopazOps,
    "srix": SrixOps,
    "picopass": PicopassOps,
    "cts": CtsOps,
}
_CLASSIC = ("classic_1k", "classic_4k", "classic_mini")
FAMILIES = tuple(_OPS) + _CLASSIC + ("unknown",)


def family_ops(family: str):
    """Ops object for an ``atr.parse(...).family`` value (every family atr.py emits)."""
    if family in _CLASSIC:
        return ClassicFamily(family)
    cls = _OPS.get(family)
    if cls is None:
        return UnknownFamily(family if family != "unknown" else None)
    return cls()
