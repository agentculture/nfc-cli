import ast
import contextlib
import pathlib

import pytest

from nfc_cli.nfc import reader
from nfc_cli.nfc.errors import NfcError, ReaderBusy

FW = bytes.fromhex("E10000001441435231353532205220465720312E30362E3032")


class Fake:
    def __init__(self, state, busy=0, card=False):
        self.s, self.busy, self.card, self.sent = state, busy, card, []

    @contextlib.contextmanager
    def claim(self):
        if self.busy:
            self.busy -= 1
            raise ReaderBusy()
        yield self

    def close(self):
        pass

    def present(self):
        return self.card

    def escape(self, cmd):
        self.sent.append(cmd)
        if cmd == bytes.fromhex("E000001800"):
            return FW
        if cmd == bytes.fromhex("E000012000"):
            return b"\xe1\x00\x00\x00\x02" + self.s["m"].to_bytes(2, "big")
        if cmd.startswith(bytes.fromhex("E000012002")):
            self.s["m"] = int.from_bytes(cmd[5:], "big")
            return b"\xe1\x00\x00\x00\x02" + cmd[5:]
        raise AssertionError(cmd)


def test_firmware():
    assert reader.firmware(Fake({"m": 0})) == "ACR1552 R FW 1.06.02"


def test_polling_type_named():
    got = reader.polling_type(Fake({"m": 0x0705}))
    assert got == ["iso14443a", "iso14443b", "felica", "picopass-14443b", "iso15693"]


def test_slot_status():
    assert reader.slot_status(Fake({}, card=True))["present"] is True
    assert reader.slot_status(Fake({}, card=False))["state"] == "absent"


def test_set_echo_and_reserved():
    f = Fake({"m": 0x0705})
    assert reader.set_polling_mask(f, 0x0100) == 0x0100
    assert f.sent[-1] == bytes.fromhex("E00001200201 00".replace(" ", ""))
    with pytest.raises(NfcError):
        reader.set_polling_mask(f, 0x0800)


def test_with_polling_type_restores_after_busy():
    state = {"m": 0x0705}
    made = []

    def factory():
        # first transport is fine; the first restore transport is busy once
        f = Fake(state, busy=1 if len(made) == 1 else 0)
        made.append(f)
        return f

    with reader.with_polling_type(factory, 0x0100, sleep=lambda s: None):
        assert state["m"] == 0x0100
    assert state["m"] == 0x0705
    assert len(made) >= 3  # fresh transport(s) used for the restore


def test_restore_verification_failure_raises():
    state = {"m": 0x0705}

    class Stuck(Fake):
        def escape(self, cmd):
            if cmd.startswith(bytes.fromhex("E000012002")) and self.s.get("lock"):
                return b"\xe1\x00\x00\x00\x02" + cmd[5:]  # lies; no change
            return super().escape(cmd)

    n = []

    def factory():
        n.append(1)
        f = Stuck(state)
        return f

    with pytest.raises(NfcError):
        with reader.with_polling_type(factory, 0x0100, sleep=lambda s: None):
            state["lock"] = True
    assert state["m"] == 0x0100


def test_no_operating_mode_commands():
    f = Fake({"m": 0})
    for cmd in (bytes.fromhex("E000009001"), bytes.fromhex("E00000 9F".replace(" ", "")) + b"\0"):
        with pytest.raises(NfcError):
            reader._escape(f, cmd)
    assert not f.sent
    src = pathlib.Path(reader.__file__).read_text()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value.replace(" ", "").upper()
            assert not s.startswith("E000009"), s
        if isinstance(node, ast.Constant) and isinstance(node.value, bytes):
            v = node.value
            assert not (v[:3] == b"\xe0\x00\x00" and len(v) > 3 and 0x90 <= v[3] <= 0x9F)
