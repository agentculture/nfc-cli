"""Magic-card (UID-rewritable MIFARE Classic) detection — deviation d2.

`tag magic-check` reports whether block 0 (the UID block) accepts a write, by
writing block 0's own bytes back to itself: a genuine chip refuses, a CUID/gen2
card accepts. The probe is non-destructive.
"""

from __future__ import annotations

import pytest

from nfc_cli.nfc import classic, ops
from nfc_cli.nfc.errors import CardRemoved, NoCard
from nfc_cli.nfc.fake import load_scenario


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


def test_genuine_card_block0_read_only():
    t = load_scenario("blank-classic")
    t.power_on()
    res = ops.magic_check(t)
    assert res["block0_writable"] is False
    assert "read-only" in res["note"]


def test_magic_card_block0_writable():
    t = load_scenario("magic-classic")
    t.power_on()
    res = ops.magic_check(t)
    assert res["block0_writable"] is True
    assert "magic" in res["note"].lower()


def test_probe_is_non_destructive_on_magic_card():
    t = load_scenario("magic-classic")
    t.power_on()
    before = t.card.blocks[0]
    ops.magic_check(t)
    assert t.card.blocks[0] == before


def test_probe_leaves_genuine_card_usable():
    # a refused block-0 write must not strand the card: later reads still work
    t = load_scenario("blank-classic")
    t.power_on()
    assert classic.probe_block0_writable(t) is False
    assert ops.uid(t)["uid"] == t.card.uid.hex()


def test_magic_check_refuses_non_classic():
    t = load_scenario("ntag215")
    t.power_on()
    with pytest.raises(ops.InvalidInput, match="MIFARE Classic"):
        ops.magic_check(t)


def test_probe_reraises_when_card_truly_gone():
    # the refused block-0 write happens AND the tag then leaves the field for good:
    # the probe must not claim the card is read-only, it must surface the removal.
    t = load_scenario("blank-classic")
    t.power_on()
    real_update = t.card._update

    def refuse_then_leave(block, data):
        if block == 0:
            t.remove_card()
            from nfc_cli.nfc.fake import SW_DENIED

            return SW_DENIED
        return real_update(block, data)

    t.card._update = refuse_then_leave  # type: ignore[method-assign]
    with pytest.raises((CardRemoved, NoCard)):
        classic.probe_block0_writable(t)
