"""Regressions found during live validation on the real ACR1552 (plan t18).

2026-11-08: restoring an NDEF-formatted MIFARE Classic sticker (UID 3d100a1e) to its
blank dump failed with "card removed" and 0 blocks written. The trace showed the
restore authenticating sector 0 with key A (A0A1A2A3A4A5) and writing block 1; the
NFC Forum access bits 78 77 88 reserve sector-0 data writes for key B, the chip NAKed
and halted, and the reader reported the card as mute -> CardRemoved. The key-B
fallback only handled ApduError, so it never ran.
"""

from __future__ import annotations

import os

import pytest

from nfc_cli.nfc import ops
from nfc_cli.nfc.errors import CardRemoved
from nfc_cli.nfc.fake import load_scenario


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


def _blank_dump(t, tmp_path):
    out = tmp_path / "blank.json"
    res = ops.dump(t, output=out)
    assert res["complete"]
    return out


def test_restore_formatted_classic_when_refused_write_mutes_card(tmp_path) -> None:
    t = load_scenario("blank-classic")
    t.card.mute_on_nak = True  # behave like the real chip + ACR1552
    t.power_on()
    blank = _blank_dump(t, tmp_path)
    original = [bytes(b) for b in t.card.blocks]

    fmt = ops.ndef_format(t, apply=True)
    assert fmt["verified"] == 19

    res = ops.restore(t, blank, apply=True, include_trailers=True)
    assert res["verified"] == res["summary"]["write"] == 19
    assert t.card.blocks == original  # byte-identical, trailers and keys included


def test_mute_with_card_really_gone_still_raises(tmp_path, monkeypatch) -> None:
    t = load_scenario("blank-classic")
    t.card.mute_on_nak = True
    t.power_on()
    blank = _blank_dump(t, tmp_path)
    ops.ndef_format(t, apply=True)
    # force the reactive path: pretend the access bits allow key A, so the write NAKs
    monkeypatch.setattr(ops.classic, "write_key_types", lambda trailer, block: ("A",))
    monkeypatch.setattr(ops.time, "sleep", lambda s: None)

    real_off = t.power_off

    def off_and_leave() -> None:
        halted = t.card.halted
        real_off()
        if halted:
            t.remove_card()  # the tag leaves the field while we try to recover

    t.power_off = off_and_leave  # type: ignore[method-assign]
    with pytest.raises(CardRemoved):
        ops.restore(t, blank, apply=True, include_trailers=True)


def test_access_bits_choose_key_b_without_any_nak(tmp_path) -> None:
    """Proactive path: no refused write at all, so the chip never halts."""
    t = load_scenario("blank-classic")
    t.card.mute_on_nak = True
    t.power_on()
    blank = _blank_dump(t, tmp_path)
    ops.ndef_format(t, apply=True)
    halts = []
    real_update = t.card._update

    def watch(block, data):
        resp = real_update(block, data)
        if t.card.halted:
            halts.append(block)
        return resp

    t.card._update = watch  # type: ignore[method-assign]
    ops.restore(t, blank, apply=True, include_trailers=True)
    assert halts == []


def test_write_key_types_decodes_datasheet_conditions() -> None:
    from nfc_cli.nfc import classic

    transport = bytes(6) + bytes.fromhex("ff078069") + bytes(6)
    ndef_sector = bytes(6) + bytes.fromhex("7f078840") + bytes(6)
    mad_sector = bytes(6) + bytes.fromhex("787788c1") + bytes(6)
    assert classic.write_key_types(transport, 4) == ("A", "B")
    assert classic.write_key_types(transport, 7) == ("A",)
    assert classic.write_key_types(ndef_sector, 4) == ("A", "B")
    assert classic.write_key_types(ndef_sector, 7) == ("B",)
    assert classic.write_key_types(mad_sector, 1) == ("B",)
    assert classic.write_key_types(mad_sector, 3) == ("B",)


def test_resume_refuses_to_silently_skip_pending_trailers(tmp_path, monkeypatch) -> None:
    """Live 2026-11-08: `tag restore J --resume --apply` skipped all 16 pending trailers."""
    t = load_scenario("blank-classic")
    t.power_on()
    blank = _blank_dump(t, tmp_path)
    ops.ndef_format(t, apply=True)
    real_update = t.card._update
    writes = {"n": 0}

    def update_then_leave(block, data):
        resp = real_update(block, data)
        writes["n"] += 1
        if writes["n"] == 3:
            t.remove_card()  # drop the tag part-way through the restore
        return resp

    t.card._update = update_then_leave  # type: ignore[method-assign]
    with pytest.raises(ops.WriteInterrupted) as exc:
        ops.restore(t, blank, apply=True, include_trailers=True)
    journal = exc.value.journal
    t.card._update = real_update  # type: ignore[method-assign]
    t.insert_card(t.card)
    t.power_on()
    with pytest.raises(ops.InvalidInput, match="trailer/block-0 writes pending") as refused:
        ops.resume(t, journal, apply=True)
    assert "--include-trailers" in refused.value.remediation
    res = ops.resume(t, journal, apply=True, include_trailers=True)
    assert res["summary"]["skip"] == 0
    original = ops.dumpfile.Dump.load(blank)
    for block, hexdata in original.blocks.items():
        assert t.card.blocks[int(block)].hex() == hexdata, block


def test_dump_resume_bin_format(tmp_path):
    """Review finding #2: a binary partial dump must be resumable (resume load hardcoded json).

    Before the fix, dump(resume_from=<bin file>) loaded it as JSON and failed with
    'cannot read dump'. The resume now loads the partial in the dump's own format.
    """
    from nfc_cli.nfc import dumpfile

    t = load_scenario("x3")
    t.power_on()
    uid = t.card.uid.hex()
    # a partial dump of this tag, saved as binary (sectors 0-1 only)
    partial = dumpfile.Dump(t.card.atr.hex(), uid, "classic")
    for b in range(8):
        partial.blocks[b] = t.card.blocks[b].hex()
    out = tmp_path / "x3.bin"
    partial.save(out, fmt="bin", include_keys=True)

    done = ops.dump(t, output=out, fmt="bin", resume_from=out)
    assert done["complete"]
    assert done["blocks_read"] == 64


def test_recover_reauths_with_access_bit_keys_not_hardcoded_b(tmp_path, monkeypatch):
    """Review finding #3: after a transient mute, recovery must re-authenticate with the
    key type the block's access bits allow (``allowed``), not an unconditional key B.
    A trailer or block whose access bits permit key A only would be bricked by forcing B.
    """
    t = load_scenario("blank-classic")
    t.power_on()
    monkeypatch.setattr(ops.time, "sleep", lambda s: None)
    monkeypatch.setattr(ops.classic, "write_key_types", lambda trailer, block: ("A",))

    acc = ops._ClassicAccess(t, "classic_1k")

    auth_calls: list[tuple] = []
    real_auth = acc.auth

    def record_auth(sector, key_types=("A", "B")):
        auth_calls.append(tuple(key_types))
        return real_auth(sector, key_types)

    monkeypatch.setattr(acc, "auth", record_auth)

    # transient mute on the first write of block 1, then success
    real_update = t.card._update
    tripped = {"done": False}

    def flaky(block, data):
        if block == 1 and not tripped["done"]:
            tripped["done"] = True
            t.card.halted = True
            from nfc_cli.nfc.fake import SW_MUTE

            return SW_MUTE
        return real_update(block, data)

    t.card._update = flaky  # type: ignore[method-assign]
    acc.write(1, bytes(range(16)))
    assert t.card.blocks[1] == bytes(range(16))
    # the post-mute re-auth used the access-bit key set ("A",), never a forced ("B",)
    assert ("B",) not in auth_calls
    assert ("A",) in auth_calls


def test_snapshot_must_be_complete_before_write(tmp_path):
    """Review finding #1: an incomplete pre-write snapshot (a sector could not be read)
    must abort the write, not proceed with a snapshot that cannot undo it."""
    from nfc_cli.nfc import fake as fakemod

    blocks = fakemod.MifareClassic1K.blank_blocks(bytes.fromhex("11223344"))
    secret = bytes.fromhex("A1A2A3A4A5A6")
    blocks[5 * 4 + 3] = secret + fakemod.TRANSPORT_TRAILER_TAIL + secret  # sector 5 unreadable
    card = fakemod.MifareClassic1K(bytes.fromhex("11223344"), blocks=blocks)
    t = fakemod.FakeTransport(card)
    t.power_on()

    before = [bytes(b) for b in t.card.blocks]
    with pytest.raises(ops.SnapshotFailed):
        ops.write(t, 4, bytes(range(16)), apply=True)  # target sector 1; sector 5 unreadable
    assert [bytes(b) for b in t.card.blocks] == before  # nothing written


def test_write_refuses_swapped_tag(tmp_path):
    """Review finding #2: expect_uid makes the server refuse a write to a tag other than
    the one that was previewed."""
    t = load_scenario("blank-classic")
    t.power_on()
    present_uid = t.card.uid.hex()

    before = [bytes(b) for b in t.card.blocks]
    with pytest.raises(ops.CardChanged):
        ops.write(t, 4, bytes(range(16)), apply=True, expect_uid="deadbeef")
    assert [bytes(b) for b in t.card.blocks] == before  # nothing written

    # the correct UID proceeds
    res = ops.write(t, 4, bytes(range(16)), apply=True, expect_uid=present_uid)
    assert res["applied"]
    assert res["summary"]["write"] == 1


def test_atomic_save_keeps_prior_copy_on_failure(tmp_path, monkeypatch):
    """Review finding #7: an interrupted save must not truncate an existing file."""
    from nfc_cli.nfc import dumpfile

    p = tmp_path / "d.json"
    p.write_bytes(b"GOOD-ORIGINAL")
    real_replace = os.replace

    def boom(src, dst):
        raise OSError("simulated crash during replace")

    monkeypatch.setattr(dumpfile.os, "replace", boom)
    with pytest.raises(OSError):
        dumpfile._write_private(p, b"NEW")
    assert p.read_bytes() == b"GOOD-ORIGINAL"  # prior copy intact
    assert list(tmp_path.glob(".d.json.*.tmp")) == []  # temp cleaned up
    monkeypatch.setattr(dumpfile.os, "replace", real_replace)


def test_bin_dump_roundtrips_4byte_pages():
    """Review finding #5: a binary dump of a 4-byte-page tag restores to 4-byte pages."""
    from nfc_cli.nfc import dumpfile

    d = dumpfile.Dump(
        "", "04a1b2c3", dumpfile.FAMILY_ULTRALIGHT, {i: f"{i:02x}" * 4 for i in range(8)}
    )
    raw = d.to_bytes()
    back = dumpfile.Dump.from_bytes(raw, family=dumpfile.FAMILY_ULTRALIGHT)
    assert back.blocks == d.blocks
    assert all(len(bytes.fromhex(h)) == 4 for h in back.blocks.values())


def test_raw_ndef_write_rejects_trailing_bytes():
    """Review finding #11: a raw message with bytes after the final record is refused."""
    good = ops.ndef.encode_message([ops.ndef.uri_record("https://x.org")])
    assert ops.build_message(raw=good) == good
    with pytest.raises(ops.InvalidInput):
        ops.build_message(raw=good + b"\x99\x99")


def test_dump_output_confined_over_mcp(tmp_path, monkeypatch):
    """Review finding #14: over MCP/web a dump cannot write outside the dumps dir."""
    from nfc_cli import verbs
    from nfc_cli.registry import Context

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    t = load_scenario("x3")
    ctx = Context(transport_factory=lambda: t)
    verb = next(v for v in verbs.REGISTRY.verbs() if v.path == ("tag", "dump"))
    with pytest.raises(verbs.ops.InvalidInput, match="must stay inside"):
        verbs.invoke(verb, {"output": "/etc/evil.json"}, ctx, surface="mcp")
    # a plain filename is fine and lands in the dumps dir
    res = verbs.invoke(verb, {"output": "ok.json"}, ctx, surface="mcp")
    assert res["path"].endswith("/ok.json")


def test_raw_apdu_refuses_ntag_lock_pages():
    """Review finding #15: a raw FF D6 to a Type 2 (NTAG) lock/CC page is refused on apply,
    so a raw sequence cannot permanently lock an Ultralight/NTAG tag by accident."""
    t = load_scenario("ntag215")
    t.power_on()
    with pytest.raises(ops.InvalidInput, match="page 2"):
        ops.raw_apdu(t, ["FFD6000204" + "00" * 4], apply=True)  # write page 2 (lock)
    # a data page is allowed
    res = ops.raw_apdu(t, ["FFD6001004" + "01020304"], apply=True)
    assert res["applied"]
