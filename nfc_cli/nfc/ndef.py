"""Pure NDEF codec and MIFARE Classic NDEF/MAD format template (no I/O)."""

from __future__ import annotations

from dataclasses import dataclass

TNF_EMPTY, TNF_WELL_KNOWN, TNF_MIME, TNF_URI, TNF_EXTERNAL, TNF_UNKNOWN = 0, 1, 2, 3, 4, 5

_MB, _ME, _CF, _SR, _IL = 0x80, 0x40, 0x20, 0x10, 0x08

URI_PREFIXES = (
    "",
    "http://www.",
    "https://www.",
    "http://",
    "https://",
    "tel:",
    "mailto:",
    "ftp://anonymous:anonymous@",
    "ftp://ftp.",
    "ftps://",
    "sftp://",
    "smb://",
    "nfs://",
    "ftp://",
    "dav://",
    "news:",
    "telnet://",
    "imap:",
    "rtsp://",
    "urn:",
    "pop:",
    "sip:",
    "sips:",
    "tftp:",
    "btspp://",
    "btl2cap://",
    "btgoep://",
    "tcpobex://",
    "irdaobex://",
    "file://",
    "urn:epc:id:",
    "urn:epc:tag:",
    "urn:epc:pat:",
    "urn:epc:raw:",
    "urn:epc:",
    "urn:nfc:",
)

# MIFARE Classic constants (access bytes verified against the parity rule in tests).
KEY_MAD = bytes.fromhex("A0A1A2A3A4A5")
KEY_NDEF = bytes.fromhex("D3F7D3F7D3F7")
KEY_DEFAULT = bytes.fromhex("FFFFFFFFFFFF")
ACCESS_NDEF = bytes.fromhex("7F0788")
GPB_NDEF = 0x40
ACCESS_MAD = bytes.fromhex("787788")
GPB_MAD = 0xC1
NDEF_AID = b"\x03\xe1"  # AID 0xE103, stored low byte first
MAD_INFO_BYTE = 0x01


class NdefError(ValueError):
    """Malformed NDEF/TLV/format input."""


@dataclass(frozen=True)
class NdefRecord:
    tnf: int
    type: bytes = b""
    id: bytes = b""
    payload: bytes = b""


# ---------------------------------------------------------------- records


def _encode_record(r: NdefRecord, *, first: bool, last: bool) -> bytes:
    """Serialise one record with its MB/ME/SR/IL header flags."""
    if not 0 <= r.tnf <= 6:
        raise NdefError(f"invalid TNF {r.tnf}")
    if len(r.type) > 255 or len(r.id) > 255:
        raise NdefError("record type/id longer than 255 bytes")
    sr = len(r.payload) < 256
    flags = r.tnf
    flags |= _MB if first else 0
    flags |= _ME if last else 0
    flags |= _SR if sr else 0
    flags |= _IL if r.id else 0
    out = bytearray()
    out.append(flags)
    out.append(len(r.type))
    out += bytes([len(r.payload)]) if sr else len(r.payload).to_bytes(4, "big")
    if r.id:
        out.append(len(r.id))
    out += r.type + r.id + r.payload
    return bytes(out)


def encode_message(records: list[NdefRecord]) -> bytes:
    if not records:
        raise NdefError("an NDEF message needs at least one record")
    last = len(records) - 1
    out = bytearray()
    for i, r in enumerate(records):
        out += _encode_record(r, first=i == 0, last=i == last)
    return bytes(out)


def _parse_record(data: bytes, pos: int, *, first: bool) -> tuple[NdefRecord, int, int]:
    """Parse one record at ``pos``; return (record, new_pos, flags)."""
    n = len(data)

    def take(count: int) -> bytes:
        nonlocal pos
        if pos + count > n:
            raise NdefError("truncated NDEF message")
        chunk = data[pos : pos + count]
        pos += count
        return chunk

    flags = take(1)[0]
    if first and not flags & _MB:
        raise NdefError("first record lacks the MB flag")
    if flags & _CF:
        raise NdefError("chunked records are not supported")
    type_len = take(1)[0]
    pay_len = take(1)[0] if flags & _SR else int.from_bytes(take(4), "big")
    id_len = take(1)[0] if flags & _IL else 0
    rtype, rid, payload = take(type_len), take(id_len), take(pay_len)
    return NdefRecord(flags & 0x07, rtype, rid, payload), pos, flags


def decode_message(data: bytes, strict: bool = False) -> list[NdefRecord]:
    if not data:
        raise NdefError("empty NDEF message")
    records: list[NdefRecord] = []
    pos, n = 0, len(data)
    while True:
        record, pos, flags = _parse_record(data, pos, first=not records)
        records.append(record)
        if flags & _ME:
            if strict and pos != n:
                raise NdefError("trailing bytes after the final NDEF record")
            return records
        if pos >= n:
            raise NdefError("message ended without an ME flag")


def text_record(text: str, lang: str = "en") -> NdefRecord:
    lang_b = lang.encode("ascii")
    if not 0 < len(lang_b) < 64:
        raise NdefError("language code must be 1-63 ASCII bytes")
    return NdefRecord(TNF_WELL_KNOWN, b"T", b"", bytes([len(lang_b)]) + lang_b + text.encode())


def record_text(rec: NdefRecord) -> tuple[str, str]:
    if rec.tnf != TNF_WELL_KNOWN or rec.type != b"T" or not rec.payload:
        raise NdefError("not a Well-Known Text record")
    status = rec.payload[0]
    n = status & 0x3F
    lang = rec.payload[1 : 1 + n].decode("ascii", "replace")
    enc = "utf-16" if status & 0x80 else "utf-8"
    return lang, rec.payload[1 + n :].decode(enc, "replace")


def uri_record(uri: str) -> NdefRecord:
    best = 0
    for code, prefix in enumerate(URI_PREFIXES):
        if prefix and uri.startswith(prefix) and len(prefix) > len(URI_PREFIXES[best]):
            best = code
    return NdefRecord(
        TNF_WELL_KNOWN, b"U", b"", bytes([best]) + uri[len(URI_PREFIXES[best]) :].encode()
    )


def record_uri(rec: NdefRecord) -> str:
    if rec.tnf != TNF_WELL_KNOWN or rec.type != b"U" or not rec.payload:
        raise NdefError("not a Well-Known URI record")
    code = rec.payload[0]
    if code >= len(URI_PREFIXES):
        raise NdefError(f"unknown URI prefix code {code:#04x}")
    return URI_PREFIXES[code] + rec.payload[1:].decode("utf-8", "replace")


def mime_record(mime: str, data: bytes) -> NdefRecord:
    return NdefRecord(TNF_MIME, mime.encode("ascii"), b"", bytes(data))


# ---------------------------------------------------------------- TLV / Type 2


def tlv_wrap(message: bytes) -> bytes:
    n = len(message)
    if n >= 0xFFFF:
        raise NdefError("NDEF message too long for a TLV")
    length = bytes([n]) if n < 0xFF else b"\xff" + n.to_bytes(2, "big")
    return b"\x03" + length + message + b"\xfe"


def _read_tlv_length(data: bytes, pos: int) -> tuple[int, int]:
    """Read a TLV length (1 byte, or 0xFF + 2 bytes) at ``pos``; return (length, new_pos)."""
    n = len(data)
    if pos >= n:
        raise NdefError("truncated TLV")
    length = data[pos]
    pos += 1
    if length == 0xFF:
        if pos + 2 > n:
            raise NdefError("truncated TLV length")
        length = int.from_bytes(data[pos : pos + 2], "big")
        pos += 2
    return length, pos


def tlv_unwrap(data: bytes) -> bytes | None:
    """Return the NDEF message bytes, or None if there is none (terminator first)."""
    pos, n = 0, len(data)
    while pos < n:
        tag = data[pos]
        pos += 1
        if tag == 0x00:
            continue
        if tag == 0xFE:
            return None
        length, pos = _read_tlv_length(data, pos)
        if pos + length > n:
            raise NdefError("TLV value runs past end of data")
        if tag == 0x03:
            return bytes(data[pos : pos + length])
        pos += length
    return None


def type2_cc(data_area_size: int, read_only: bool = False) -> bytes:
    if data_area_size % 8 or not 0 < data_area_size <= 8 * 255:
        raise NdefError("data area size must be a multiple of 8, at most 2040")
    return bytes([0xE1, 0x10, data_area_size // 8, 0x0F if read_only else 0x00])


def parse_type2_cc(cc: bytes) -> dict:
    if len(cc) < 4 or cc[0] != 0xE1:
        raise NdefError("no NDEF capability container (magic 0xE1 missing)")
    return {
        "version": f"{cc[1] >> 4}.{cc[1] & 0xF}",
        "size": cc[2] * 8,
        "read_only": (cc[3] & 0x0F) == 0x0F,
    }


# ---------------------------------------------------------------- MIFARE Classic


def mad_crc(data: bytes) -> int:
    """CRC-8 per NXP AN10787 (poly 0x1D, init 0xC7, no reflection, no final xor)."""
    crc = 0xC7
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1D) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def validate_access_bits(trailer: bytes) -> bool:
    """True iff bytes 6-8 of a 16-byte trailer hold consistent inverted/plain copies."""
    if len(trailer) != 16:
        return False
    b6, b7, b8 = trailer[6], trailer[7], trailer[8]
    c1, c2, c3 = b7 >> 4, b8 & 0xF, b8 >> 4
    return (b6 & 0xF) == (~c1 & 0xF) and (b6 >> 4) == (~c2 & 0xF) and (b7 & 0xF) == (~c3 & 0xF)


def classic_data_blocks(sectors: int = 16) -> list[int]:
    """Data block numbers of NDEF sectors 1..sectors-1 (trailers excluded)."""
    return [4 * s + b for s in range(1, sectors) for b in range(3)]


def classic_format_template(sectors: int = 16) -> dict[int, bytes]:
    """Blocks written to NDEF-format a Classic 1K (<=16 sectors); block 0 is never included."""
    if not 2 <= sectors <= 16:
        raise NdefError("sectors must be between 2 and 16")
    aids = [NDEF_AID] * (sectors - 1) + [b"\x00\x00"] * (15 - (sectors - 1))
    body = bytes([MAD_INFO_BYTE]) + b"".join(aids)
    mad = bytes([mad_crc(body)]) + body
    t: dict[int, bytes] = {1: mad[:16], 2: mad[16:]}
    t[3] = KEY_MAD + ACCESS_MAD + bytes([GPB_MAD]) + KEY_DEFAULT
    t[4] = b"\x03\x00\xfe" + bytes(13)
    for s in range(1, sectors):
        t[4 * s + 3] = KEY_NDEF + ACCESS_NDEF + bytes([GPB_NDEF]) + KEY_DEFAULT
    return t
