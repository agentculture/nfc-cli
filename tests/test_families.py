"""Tests for nfc_cli.nfc.families using minimal in-test fake transports."""

import pytest

from nfc_cli.nfc import atr, families, ndef
from nfc_cli.nfc.errors import ApduError
from nfc_cli.nfc.families import UnsupportedOperation, family_ops

OK = b"\x90\x00"


class Fake:
    def __init__(self, handler):
        self.handler = handler
        self.log = []

    def transmit(self, apdu):
        self.log.append(bytes(apdu))
        return self.handler(bytes(apdu))

    def power_on(self):
        return b""

    def escape(self, cmd):
        return b""

    def present(self):
        return True

    def close(self):
        pass


def type2_tag(size=48):
    mem = bytearray(4 * 16)
    mem[12:16] = ndef.type2_cc(size)

    def h(a):
        if a[:2] == b"\xff\xb0":
            return bytes(mem[a[3] * 4 : a[3] * 4 + a[4]]), OK
        if a[:2] == b"\xff\xd6":
            mem[a[3] * 4 : a[3] * 4 + 4] = a[5:9]
            return b"", OK
        return b"", b"\x6a\x81"

    return Fake(h), mem


def test_type2_read_page_apdu():
    tr, _ = type2_tag()
    assert len(families.Type2Ops().read_pages(tr, 4, 4)) == 16
    assert tr.log[0] == bytes.fromhex("FFB0000410")


def test_type2_write_page_apdu_and_cc_size():
    tr, mem = type2_tag(size=48)
    ops = family_ops("ultralight_ntag")
    assert ops.memory_size(tr) == 48
    ops.write_page(tr, 4, b"\x01\x02\x03\x04")
    assert tr.log[-1] == bytes.fromhex("FFD60004" "04" "01020304")
    assert mem[16:20] == b"\x01\x02\x03\x04"


def test_type2_ndef_roundtrip_and_overflow():
    tr, _ = type2_tag(size=48)
    ops = families.Type2Ops()
    msg = ndef.encode_message([ndef.text_record("hi")])
    ops.write_ndef(tr, msg)
    assert ops.read_ndef(tr) == msg
    with pytest.raises(UnsupportedOperation):
        ops.write_ndef(tr, b"x" * 60)


def test_type2_error_status():
    tr = Fake(lambda a: (b"", b"\x63\x00"))
    ops = families.Type2Ops()
    with pytest.raises(ApduError):
        ops.read_pages(tr, 0, 4)


def iso15693_tag(blocks=8, bsize=4, sysinfo=True):
    mem = [bytes([i & 0xFF] * bsize) for i in range(blocks)]

    def h(a):
        if a[:2] == b"\xff\xb0":  # Read Binary: P1[2:0]+P2 = block, Le = length (0 -> block)
            blk = ((a[2] & 0x07) << 8) | a[3]
            return mem[blk][: a[4] or bsize], OK
        if a[:2] == b"\xff\xd6":  # Update Binary
            blk = ((a[2] & 0x07) << 8) | a[3]
            mem[blk] = a[5 : 5 + a[4]]
            return b"", OK
        assert a[:3] == b"\xff\xfb\x00"
        body = a[5:]
        if body[0] == 0x2B and sysinfo:
            uid = bytes(range(8))
            return b"\x00\x0f" + uid + b"\x00\x00" + bytes([blocks - 1, bsize - 1]) + b"\x01", OK
        return b"\x01\x01", OK

    return Fake(h), mem


def test_iso15693_geometry_and_rw():
    tr, mem = iso15693_tag()
    ops = family_ops("iso15693")
    assert ops.geometry(tr) == (8, 4)
    assert ops.read_block(tr, 2) == b"\x02" * 4
    assert tr.log[-1] == bytes.fromhex("FFB0000204")
    ops.write_block(tr, 3, b"abcd")
    assert mem[3] == b"abcd"
    assert tr.log[-1] == bytes.fromhex("FFD6000304" "61626364")
    with pytest.raises(UnsupportedOperation):
        ops.write_block(tr, 3, b"abc")


def test_iso15693_high_block_address_and_range():
    tr, _ = iso15693_tag(blocks=0x300)
    ops = family_ops("iso15693")
    ops.read_block(tr, 0x2A5)
    assert tr.log[-1] == bytes.fromhex("FFB002A504")
    with pytest.raises(UnsupportedOperation):
        ops.read_block(tr, 0x800)


def test_iso15693_geometry_fallback_probe():
    tr, _ = iso15693_tag(bsize=8, sysinfo=False)
    assert family_ops("iso15693").geometry(tr) == (None, 8)


def type4_tag(msg=b"", mle=0x3B, mlc=0x34, fsize=0x80, writable=True):
    state = {"file": None, "ndef": bytearray(2 + fsize)}
    state["ndef"][0:2] = len(msg).to_bytes(2, "big")
    state["ndef"][2 : 2 + len(msg)] = msg
    cc = (
        (15).to_bytes(2, "big")
        + b"\x20"
        + mle.to_bytes(2, "big")
        + mlc.to_bytes(2, "big")
        + b"\x04\x06\xe1\x04"
        + fsize.to_bytes(2, "big")
        + b"\x00"
        + (b"\x00" if writable else b"\xff")
    )

    def h(a):
        if a[:2] == b"\x00\xa4":
            if a[2] == 4:
                return (
                    (b"", OK) if a[5:12] == bytes.fromhex("D2760000850101") else (b"", b"\x6a\x82")
                )
            state["file"] = a[5:7]
            return b"", OK
        f = state["file"]
        buf = cc if f == b"\xe1\x03" else state["ndef"]
        off = (a[2] << 8) | a[3]
        if a[1] == 0xB0:
            return bytes(buf[off : off + a[4]]), OK
        if a[1] == 0xD6:
            buf[off : off + a[4]] = a[5:]
            return b"", OK
        return b"", b"\x6d\x00"

    return Fake(h), state


def test_type4_ndef_read_sequence():
    msg = ndef.encode_message([ndef.uri_record("https://example.com")])
    tr, _ = type4_tag(msg)
    assert family_ops("iso14443_4").read_ndef(tr) == msg
    assert tr.log[0] == bytes.fromhex("00A4040007D276000085010100")
    assert tr.log[1] == bytes.fromhex("00A4000C02E103")


def test_type4_write_roundtrip_chunked_and_limits():
    tr, st = type4_tag(mle=16, mlc=16)
    ops = families.Type4Ops()
    msg = ndef.encode_message([ndef.text_record("x" * 50)])
    ops.write_ndef(tr, msg)
    assert ops.read_ndef(tr) == msg
    assert bytes(st["ndef"][:2]) == len(msg).to_bytes(2, "big")
    with pytest.raises(UnsupportedOperation):
        ops.write_ndef(tr, b"x" * 200)
    tr2, _ = type4_tag(writable=False)
    with pytest.raises(UnsupportedOperation):
        ops.write_ndef(tr2, b"x")


def recorder():
    return Fake(lambda a: (b"\xaa" * a[4], OK))


@pytest.mark.parametrize(
    "family,start,count,expect",
    [
        ("topaz", 2, 1, "FFB0001008"),  # byte address 0x10
        ("srix", 0x0A, 2, "FFB000" "0A08"),
        ("srix", 0xFF, 1, "FFB000FF04"),
        ("picopass", 5, 1, "FFB0000508"),
        ("cts", 3, 2, "FFB0000304"),
    ],
)
def test_readonly_reads(family, start, count, expect):
    tr = recorder()
    out = family_ops(family).read_blocks(tr, start, count)
    assert tr.log == [bytes.fromhex(expect)]
    assert len(out) == count * family_ops(family).block_size


@pytest.mark.parametrize(
    "family,start", [("topaz", 0x100), ("srix", 0x80), ("picopass", 256), ("cts", 32)]
)
def test_readonly_range_checked(family, start):
    ops = family_ops(family)
    rec = recorder()
    with pytest.raises(UnsupportedOperation):
        ops.read_blocks(rec, start, 1)


@pytest.mark.parametrize("family", ["felica", "topaz", "srix", "picopass", "cts"])
def test_readonly_writes_refused(family):
    tr = recorder()
    ops = family_ops(family)
    with pytest.raises(UnsupportedOperation, match="not supported"):
        ops.write_blocks(tr, 0, b"\x00" * 8)
    assert tr.log == []


def test_felica_read_block():
    idm = bytes.fromhex("01010601CB095703")
    block = bytes(range(16))

    def h(a):
        if a == bytes.fromhex("FFCA000000"):
            return idm, OK
        assert a[:4] == b"\xff\x00\x00\x00"
        assert a[5:] == b"\x10\x06" + idm + bytes.fromhex("01090101" "8000")
        return bytes([29, 7]) + idm + b"\x00\x00\x01" + block, OK

    assert family_ops("felica").read_blocks(Fake(h), 0, 1) == block


def test_dispatcher_covers_every_atr_family():
    emitted = set(atr._NAME_FAMILIES.values()) | {
        "felica",
        "picopass",
        "srix",
        "iso15693",
        "unknown",
        "iso14443_4",
    }
    assert emitted <= set(families.FAMILIES)
    for fam in families.FAMILIES:
        assert family_ops(fam) is not None
    assert isinstance(family_ops("ultralight_c"), families.Type2Ops)
    assert isinstance(family_ops("classic_1k"), families.ClassicFamily)
    classic_4k_ops = family_ops("classic_4k")
    with pytest.raises(UnsupportedOperation, match="nfc_cli.nfc.classic"):
        classic_4k_ops.read_blocks(None, 0, 1)
    unknown_ops = family_ops("unknown")
    with pytest.raises(UnsupportedOperation):
        unknown_ops.read_blocks(None, 0, 1)
