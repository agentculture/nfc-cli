"""t11: high-level operations against FakeTransport (no hardware)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from nfc_cli.nfc import classic, dumpfile, ndef, ops
from nfc_cli.nfc.errors import CardRemoved, NfcError, NoCard
from nfc_cli.nfc.fake import X3_BLOCK0, FakeTransport, MifareClassic1K, load_scenario

KEY = bytes.fromhex("FFFFFFFFFFFF")


@pytest.fixture(autouse=True)
def _xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def writes(t: FakeTransport) -> list[bytes]:
    return [a for kind, a in t.log if kind == "apdu" and len(a) > 1 and a[1] == 0xD6]


def card_blocks(t: FakeTransport) -> list[bytes]:
    return list(t.card.blocks)


def snapshot_files() -> list[Path]:
    d = dumpfile.snapshot_dir()
    return sorted(d.glob("*.json")) if d.exists() else []


def mode(p: str | Path) -> int:
    return stat.S_IMODE(os.stat(p).st_mode)


def guard_snapshot_first(t: FakeTransport) -> list[bool]:
    """Record, at every write APDU, whether a 0600 snapshot already existed."""
    seen: list[bool] = []
    real = t.transmit

    def transmit(apdu):
        apdu = bytes(apdu)
        if len(apdu) > 1 and apdu[1] == 0xD6:
            files = snapshot_files()
            seen.append(bool(files) and all(mode(f) == 0o600 for f in files))
        return real(apdu)

    t.transmit = transmit
    return seen


# ------------------------------------------------------------------ reading


def test_info_classic_and_others():
    t = load_scenario("x3")
    out = ops.info(t)
    assert out["family"] == "classic_1k" and out["uid"] == "cb30c560"
    assert out["sectors"] == 16 and out["blocks"] == 64
    assert ops.info(load_scenario("ntag215"))["capability"]["size"] == 496
    assert ops.info(load_scenario("type4"))["family"] == "iso14443_4"
    assert ops.info(load_scenario("iso15693"))["block_size"] == 4


def test_uid_and_info_without_card():
    assert ops.uid(load_scenario("x3"))["uid"] == "cb30c560"
    with pytest.raises(NoCard):
        ops.info(load_scenario("empty"))


def test_read_block_and_whole_tag_masks_keys():
    t = load_scenario("x3")
    one = ops.read(t, 0, 1)
    assert one["blocks"]["0"] == X3_BLOCK0.hex()
    trailer = ops.read(t, 3, 1)["blocks"]["3"]
    assert trailer.startswith("**") and "ffffffffffff" not in trailer
    assert ops.read(t, 3, 1, include_keys=True)["blocks"]["3"].startswith("ffffffffffff")
    whole = ops.read(t)
    assert len(whole["blocks"]) == 64 and whole["unreadable_sectors"] == []


def test_read_ntag_pages():
    out = ops.read(load_scenario("ntag215"), 3, 1)
    assert out["blocks"]["3"] == "e1103e00"


def test_wait_returns_when_card_arrives_and_times_out():
    t = load_scenario("empty")
    clock = [0.0]

    def sleep(s):
        clock[0] += s
        if clock[0] >= 1.0:
            t.insert_card(MifareClassic1K(bytes.fromhex("01020304")))

    out = ops.wait_for_card(t, 5, sleep=sleep, clock=lambda: clock[0])
    assert out["present"] is True and out["waited"] >= 1.0

    t2, clock2 = load_scenario("empty"), [0.0]
    with pytest.raises(NoCard):
        ops.wait_for_card(
            t2,
            2,
            sleep=lambda s: clock2.__setitem__(0, clock2[0] + s),
            clock=lambda: clock2[0],
        )
    assert clock2[0] == pytest.approx(2.0)


# ------------------------------------------------------------------ dump


def test_dump_writes_0600_file_with_keys_masked_in_result(tmp_path):
    t = load_scenario("x3")
    out = ops.dump(t, output=tmp_path / "x3.json")
    assert out["complete"] and out["blocks_read"] == 64
    assert mode(out["path"]) == 0o600
    saved = dumpfile.Dump.load(out["path"])
    assert saved.blocks[3].startswith("ffffffffffff")  # file keeps keys (0600)
    assert out["dump"]["blocks"]["3"].startswith("**")
    assert out["dump"]["keys_used"]["0"] == ["A", "************"]


def test_dump_resumes_per_sector_after_card_removal(tmp_path):
    t = load_scenario("x3")
    t.card_removed_after(40)  # drop out a few sectors in
    out = ops.dump(t, output=tmp_path / "d.json", sleep=lambda s: t.clear_faults())
    assert out["complete"] and out["blocks_read"] == 64
    ref = ops.dump(load_scenario("x3"), output=tmp_path / "ref.json")
    assert dumpfile.Dump.load(out["path"]).blocks == dumpfile.Dump.load(ref["path"]).blocks


def test_dump_resume_from_partial_file(tmp_path):
    t = load_scenario("x3")
    path = tmp_path / "partial.json"
    t.card_removed_after(30)
    with pytest.raises((CardRemoved, NoCard)):
        ops.dump(t, output=path, retry_timeout=0)
    partial = dumpfile.Dump.load(path)
    assert 0 < len(partial.blocks) < 64
    t.clear_faults()
    before = len(t.log)
    out = ops.dump(t, resume_from=path)
    assert out["complete"] and out["blocks_read"] == 64 and out["path"] == str(path)
    reads = [a for k, a in t.log[before:] if a[1] == 0xB0]
    assert len(reads) == 64 - len(partial.blocks)


def test_dump_ntag_and_iso15693(tmp_path):
    n = ops.dump(load_scenario("ntag215"), output=tmp_path / "n.json")
    assert n["dump"]["family"] == "ultralight" and n["blocks_read"] == 4 + 496 // 4
    i = ops.dump(load_scenario("iso15693"), output=tmp_path / "i.json")
    assert i["blocks_read"] == 28


# ------------------------------------------------------------------ write


def test_write_dry_run_sends_no_write_apdu_and_diffs():
    t = load_scenario("x3")
    out = ops.write(t, 4, "00112233445566778899aabbccddeeff")
    assert writes(t) == [] and out["applied"] is False and out["snapshot"] is None
    assert out["diff"] == [
        {"block": 4, "old": "00" * 16, "new": "00112233445566778899aabbccddeeff", "action": "write"}
    ]
    assert snapshot_files() == []


def test_write_apply_snapshots_first_then_verifies_and_journals():
    t = load_scenario("x3")
    seen = guard_snapshot_first(t)
    data = bytes(range(32))
    out = ops.write(t, 4, data.hex(), apply=True)
    assert seen == [True, True]
    assert out["applied"] and out["verified"] == 2
    assert t.card.blocks[4] == data[:16] and t.card.blocks[5] == data[16:]
    assert mode(out["snapshot"]) == 0o600
    snap = dumpfile.Dump.load(out["snapshot"])
    assert snap.blocks[4] == "00" * 16 and len(snap.blocks) == 64
    recs = [json.loads(x) for x in Path(out["journal"]).read_text().splitlines()]
    assert recs[-1] == {
        "block": 5,
        "old": "00" * 16,
        "new": data[16:].hex(),
        "written": True,
        "verified": True,
    }
    assert mode(out["journal"]) == 0o600


def test_write_refuses_trailer_and_block0_without_flag():
    t = load_scenario("x3")
    for block in (0, 3):
        with pytest.raises(ops.InvalidInput):
            ops.write(t, block, "00" * 16, apply=True)
    assert writes(t) == []


def test_snapshot_failure_aborts_with_zero_writes(tmp_path, monkeypatch):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    monkeypatch.setenv("XDG_DATA_HOME", str(blocker))
    t = load_scenario("x3")
    with pytest.raises(ops.SnapshotFailed):
        ops.write(t, 4, "11" * 16, apply=True)
    assert writes(t) == []
    assert t.card.blocks[4] == bytes(16)


def test_unchanged_blocks_are_not_written():
    t = load_scenario("x3")
    out = ops.write(t, 4, "00" * 16, apply=True)
    assert out["summary"] == {"write": 0, "unchanged": 1, "skip": 0}
    assert writes(t) == [] and out["snapshot"] is None


# ------------------------------------------------------------------ restore


def _modified_dump(tmp_path) -> Path:
    src = ops.dump(load_scenario("x3"), output=tmp_path / "orig.json")
    d = dumpfile.Dump.load(src["path"])
    for b in (4, 5, 8, 9, 10, 13):
        d.blocks[b] = bytes([b]) * 16 if b != 13 else bytes(range(16)).hex()
        if isinstance(d.blocks[b], bytes):
            d.blocks[b] = d.blocks[b].hex()
    d.blocks[0] = "01020304" + "00" * 12  # different block 0
    d.blocks[7] = "a0a1a2a3a4a5" + "787788c1" + "ffffffffffff"  # modified trailer
    p = tmp_path / "mod.json"
    d.save(p, include_keys=True)
    return p


def test_restore_dry_run_lists_skipped_trailers_and_block0(tmp_path):
    p = _modified_dump(tmp_path)
    t = load_scenario("x3")
    out = ops.restore(t, p)
    assert writes(t) == []
    by_block = {e["block"]: e for e in out["diff"]}
    for b in [0] + [s * 4 + 3 for s in range(16)]:
        assert by_block[b]["action"] == "skip"
    assert by_block[0]["reason"] == "manufacturer block 0"
    assert by_block[7]["reason"] == "sector trailer"
    assert {b for b, e in by_block.items() if e["action"] == "write"} == {4, 5, 8, 9, 10, 13}


def test_restore_apply_never_writes_trailers_or_block0(tmp_path):
    p = _modified_dump(tmp_path)
    t = load_scenario("x3")
    out = ops.restore(t, p, apply=True)
    targets = sorted(a[3] for a in writes(t))
    assert targets == [4, 5, 8, 9, 10, 13]
    assert all(not classic.is_trailer(b) and b != 0 for b in targets)
    assert out["protected_blocks_written"] == [] and out["verified"] == 6
    want = dumpfile.Dump.load(p)
    for b in targets:
        assert t.card.blocks[b].hex() == want.blocks[b]


def test_restore_with_include_trailers_writes_trailer(tmp_path):
    p = _modified_dump(tmp_path)
    t = load_scenario("magic-classic")
    out = ops.restore(t, p, apply=True, include_trailers=True)
    assert 7 in out["protected_blocks_written"] and 0 in out["protected_blocks_written"]
    assert t.card.blocks[7][:10].hex() == "a0a1a2a3a4a5787788c1"
    assert t.card.blocks[0].hex().startswith("01020304")


def test_restore_skips_masked_trailers(tmp_path):
    src = ops.dump(load_scenario("x3"), output=tmp_path / "o.json")
    masked = tmp_path / "masked.json"
    dumpfile.Dump.load(src["path"]).save(masked)  # include_keys=False masks trailers
    t = load_scenario("magic-classic")
    out = ops.restore(t, masked, include_trailers=True)
    reasons = {e["block"]: e.get("reason") for e in out["diff"]}
    assert reasons[3] == "keys masked"


def test_restore_dropout_then_resume_byte_identical(tmp_path):
    p = _modified_dump(tmp_path)
    t = load_scenario("x3")
    real, count = t.transmit, [0]

    def flaky(apdu):
        apdu = bytes(apdu)
        if apdu[1] == 0xD6:
            count[0] += 1
            if count[0] == 3:
                t.remove_card()
        return real(apdu)

    t.transmit = flaky
    with pytest.raises(ops.WriteInterrupted) as ei:
        ops.restore(t, p, apply=True)
    err = ei.value
    assert isinstance(err, CardRemoved)
    assert err.pending == [8, 9, 10, 13] and err.verified == 2
    journal = err.journal
    t.transmit = real
    t.clear_faults()
    dry = ops.resume(t, journal)
    assert dry["applied"] is False and dry["pending_before"] == [8, 9, 10, 13]
    n = len(writes(t))
    out = ops.resume(t, journal, apply=True)
    assert out["verified"] == 4 and len(writes(t)) - n == 4
    want = dumpfile.Dump.load(p)
    for b in (4, 5, 8, 9, 10, 13):
        assert t.card.blocks[b].hex() == want.blocks[b]
    from nfc_cli.nfc.journal import Journal

    assert Journal(journal).pending() == []


def test_resume_refuses_other_tag(tmp_path):
    p = _modified_dump(tmp_path)
    t = load_scenario("x3")
    t.card_removed_after(10**6)
    real = t.transmit

    def drop(apdu):
        if bytes(apdu)[1] == 0xD6:
            t.remove_card()
        return real(apdu)

    t.transmit = drop
    with pytest.raises(ops.WriteInterrupted) as ei:
        ops.restore(t, p, apply=True)
    other = load_scenario("blank-classic")
    with pytest.raises(NfcError, match="journal is for tag"):
        ops.resume(other, ei.value.journal, apply=True)


# ------------------------------------------------------------------ NDEF


def test_ndef_format_dry_run_then_apply_then_write_read_erase():
    t = load_scenario("blank-classic")
    dry = ops.ndef_format(t)
    assert writes(t) == [] and dry["summary"]["write"] == 19  # MAD 2 + block 4 + 16 trailers
    assert dry["applied"] is False
    seen = guard_snapshot_first(t)
    out = ops.ndef_format(t, apply=True)
    assert seen and all(seen)
    assert out["protected_blocks_written"] == [s * 4 + 3 for s in range(16)]
    template = ndef.classic_format_template()
    assert sorted(a[3] for a in writes(t)) == sorted(template)
    assert 0 not in [a[3] for a in writes(t)]
    assert t.card.blocks[7][:6] == ndef.KEY_NDEF

    assert ops.ndef_read(t)["empty"] is True
    msg = ops.build_message(uri="https://crosspoint.local/")
    dry = ops.ndef_write(t, msg)
    assert dry["applied"] is False
    before = len(writes(t))
    ops.ndef_write(t, msg, apply=True)
    assert len(writes(t)) > before
    got = ops.ndef_read(t)
    assert got["records"][0]["uri"] == "https://crosspoint.local/"

    erased = ops.ndef_erase(t, apply=True)
    assert erased["verified"] >= 1
    assert ops.ndef_read(t)["empty"] is True


def test_ndef_format_again_is_a_no_op():
    t = load_scenario("blank-classic")
    ops.ndef_format(t, apply=True)
    n = len(writes(t))
    again = ops.ndef_format(t, apply=True)
    assert again["summary"]["write"] == 0 and len(writes(t)) == n


def test_ndef_format_validates_template_first(monkeypatch):
    bad = ndef.classic_format_template()
    bad[7] = bad[7][:6] + b"\x00\x00\x00" + bad[7][9:]
    monkeypatch.setattr(ndef, "classic_format_template", lambda sectors=16: bad)
    t = load_scenario("blank-classic")
    with pytest.raises(classic.ClassicError):
        ops.ndef_format(t, apply=True)
    assert writes(t) == [] and snapshot_files() == []


def test_ndef_write_on_unformatted_classic_fails():
    with pytest.raises(ops.NotNdefFormatted):
        ops.ndef_write(load_scenario("x3"), ops.build_message(text="hi"), apply=True)


def test_ndef_format_refused_on_ntag():
    with pytest.raises(NfcError):
        ops.ndef_format(load_scenario("ntag215"), apply=True)


def test_ndef_ntag_write_read_erase():
    t = load_scenario("ntag215")
    msg = ops.build_message(text="hello X3", lang="en")
    assert ops.ndef_write(t, msg)["applied"] is False and writes(t) == []
    out = ops.ndef_write(t, msg, apply=True)
    assert out["verified"] >= 1 and out["snapshot"]
    rec = ops.ndef_read(t)["records"][0]
    assert rec["text"] == "hello X3" and rec["lang"] == "en"
    ops.ndef_erase(t, apply=True)
    assert ops.ndef_read(t)["empty"] is True


def test_ndef_type4_write_read_erase_with_snapshot():
    t = load_scenario("type4")
    msg = ops.build_message(mime="text/plain", payload="abc")
    dry = ops.ndef_write(t, msg)
    assert dry["applied"] is False and writes(t) == []
    out = ops.ndef_write(t, msg, apply=True)
    assert out["verified"] == 1 and mode(out["snapshot"]) == 0o600
    assert ops.ndef_read(t)["raw"] == msg.hex()
    ops.ndef_erase(t, apply=True)
    assert ops.ndef_read(t)["empty"] is True


def test_build_message_requires_exactly_one_kind():
    with pytest.raises(ops.InvalidInput):
        ops.build_message()
    with pytest.raises(ops.InvalidInput):
        ops.build_message(text="a", uri="b")
    with pytest.raises(ops.InvalidInput):
        ops.build_message(raw=b"\x00")


# ------------------------------------------------------------------ apdu / audit


def test_raw_apdu_dry_run_and_trailer_guard():
    t = load_scenario("x3")
    dry = ops.raw_apdu(None, ["FFCA000000"])
    assert dry["applied"] is False and t.log == []
    with pytest.raises(ops.InvalidInput):
        ops.raw_apdu(t, ["FFD6000310" + "00" * 16], apply=True)
    assert writes(t) == []
    out = ops.raw_apdu(t, ["FFCA000000"], apply=True)
    assert out["responses"][0] == {"apdu": "ffca000000", "data": "cb30c560", "sw": "9000"}


def test_append_audit_writes_0600_json_lines():
    p = ops.append_audit("mcp", "01020304", [7, 3])
    ops.append_audit("api", "01020304", [11])
    assert mode(p) == 0o600
    lines = [json.loads(x) for x in p.read_text().splitlines()]
    assert lines[0]["surface"] == "mcp" and lines[0]["blocks"] == [3, 7]
    assert lines[1]["surface"] == "api" and "ts" in lines[0]
