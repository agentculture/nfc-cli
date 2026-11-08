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


def test_mute_with_card_really_gone_still_raises(tmp_path) -> None:
    t = load_scenario("blank-classic")
    t.card.mute_on_nak = True
    t.power_on()
    blank = _blank_dump(t, tmp_path)
    ops.ndef_format(t, apply=True)

    real_present = t.present
    calls = {"n": 0}

    def flaky_present() -> bool:
        # the tag leaves the field right when the refused write is noticed
        calls["n"] += 1
        return False if calls["n"] > 1 else real_present()

    t.present = flaky_present  # type: ignore[method-assign]
    with pytest.raises(CardRemoved):
        ops.restore(t, blank, apply=True, include_trailers=True)
