"""Regressions found during live validation on the real ACR1552 (plan t18).

2026-11-08: restoring an NDEF-formatted MIFARE Classic sticker (UID 3d100a1e) to its
blank dump failed with "card removed" and 0 blocks written. The trace showed the
restore authenticating sector 0 with key A (A0A1A2A3A4A5) and writing block 1; the
NFC Forum access bits 78 77 88 reserve sector-0 data writes for key B, the chip NAKed
and halted, and the reader reported the card as mute -> CardRemoved. The key-B
fallback only handled ApduError, so it never ran.
"""

from __future__ import annotations

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
