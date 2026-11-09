"""CCID transport tests against a fake USB endpoint (no pyusb, no hardware)."""

from __future__ import annotations

import io
import json
import struct
import sys
from pathlib import Path

import pytest

from nfc_cli.nfc import errors, transport
from nfc_cli.nfc.transport import Transport, UsbCcidTransport, select_device

# --------------------------------------------------------------------------- fakes


class FakeUSBError(Exception):
    """Mimics usb.core.USBError: carries an errno attribute."""

    def __init__(self, msg, errno=None):
        super().__init__(msg)
        self.errno = errno


def rdr(mtype, seq, data=b"", status=0x00, error=0x00, specific=0x00):
    return struct.pack("<BIBBBBB", mtype, len(data), 0, seq, status, error, specific) + data


class FakeDevice:
    """A fake CCID reader: queue response *factories* keyed off the request's seq."""

    def __init__(self, bus=1, address=2):
        self.bus = bus
        self.address = address
        self.writes: list[tuple[int, bytes]] = []
        self.script: list = []  # list of callables(req_header) -> list[bytes]
        self.pending: list[bytes] = []
        self.claimed = False
        self.claim_error: Exception | None = None
        self.claims = 0

    # pyusb-ish surface
    def write(self, ep, data, timeout=None):
        data = bytes(data)
        self.writes.append((ep, data))
        handler = self.script.pop(0)
        self.pending.extend(handler(data))
        return len(data)

    def read(self, ep, size, timeout=None):
        assert ep == 0x81
        if not self.pending:
            raise FakeUSBError("timeout", errno=110)
        return bytearray(self.pending.pop(0))

    def claim_interface(self, intf):
        if self.claim_error:
            raise self.claim_error
        assert intf == 0
        self.claimed = True
        self.claims += 1

    def release_interface(self, intf):
        self.claimed = False

    # helpers
    def respond(self, mtype, data=b"", status=0x00, error=0x00, extensions=0, stale=0):
        def handler(req):
            seq = req[6]
            out = [rdr(mtype, (seq - 1) & 0xFF, b"stale") for _ in range(stale)]
            out += [rdr(mtype, seq, status=0x80) for _ in range(extensions)]
            out.append(rdr(mtype, seq, data, status=status, error=error))
            return out

        self.script.append(handler)
        return self


@pytest.fixture
def lockdir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def dev():
    return FakeDevice()


@pytest.fixture
def t(dev, lockdir):
    tr = UsbCcidTransport(device=dev)
    yield tr
    tr.close()


def header(raw):
    return struct.unpack("<BIBB3B", raw[:10])


# --------------------------------------------------------------------------- framing


def test_module_imports_without_pyusb(monkeypatch):
    import importlib

    for name in ("usb", "usb.core", "usb.util"):
        monkeypatch.setitem(sys.modules, name, None)  # any import of usb now fails
    monkeypatch.delitem(sys.modules, "nfc_cli.nfc.transport")
    mod = importlib.import_module("nfc_cli.nfc.transport")
    assert hasattr(mod, "UsbCcidTransport")
    monkeypatch.setitem(sys.modules, "nfc_cli.nfc.transport", transport)


def test_nothing_outside_transport_imports_usb_or_pcsc():
    pkg = Path(transport.__file__).parent.parent
    for path in pkg.rglob("*.py"):
        text = path.read_text()
        assert "smartcard" not in text, path
        assert "pyscard" not in text, path
        if path.name != "transport.py":
            assert "import usb" not in text, path
            assert "from usb" not in text, path


def test_transport_protocol_implemented(t):
    assert isinstance(t, Transport)


def test_get_slot_status_framing(t, dev):
    dev.respond(0x81, status=0x00)
    assert t.present() is True
    ep, raw = dev.writes[0]
    assert ep == 0x01
    assert header(raw) == (0x65, 0, 0, 0, 0, 0, 0)
    assert len(raw) == 10


def test_present_false_when_no_icc(t, dev):
    dev.respond(0x81, status=0x02)
    assert t.present() is False


def test_power_on_returns_atr(t, dev):
    atr = bytes.fromhex("3b8f8001804f0ca000000306030001000000006a")
    dev.respond(0x80, atr)
    assert t.power_on() == atr
    assert header(dev.writes[0][1])[0] == 0x62


def test_power_on_no_card_raises(t, dev):
    dev.respond(0x80, status=0x42, error=0xFE)
    with pytest.raises(errors.NoCard):
        t.power_on()


def test_power_off_framing(t, dev):
    dev.respond(0x81, status=0x01)
    t.power_off()
    assert header(dev.writes[0][1])[0] == 0x63


def test_xfrblock_framing_and_sw_split(t, dev):
    dev.respond(0x80, bytes.fromhex("cb30c5609000"))
    data, sw = t.transmit(bytes.fromhex("FFCA000000"))
    assert data == bytes.fromhex("cb30c560")
    assert sw == b"\x90\x00"
    raw = dev.writes[0][1]
    assert header(raw) == (0x6F, 5, 0, 0, 0, 0, 0)
    assert raw[10:] == bytes.fromhex("FFCA000000")


def test_escape_framing(t, dev):
    dev.respond(0x83, b"ACR1552 R FW 1.06.02")
    assert t.escape(bytes.fromhex("E000001800")) == b"ACR1552 R FW 1.06.02"
    raw = dev.writes[0][1]
    assert header(raw)[0] == 0x6B
    assert header(raw)[1] == 5
    assert raw[10:] == bytes.fromhex("E000001800")


def test_sequence_numbers_increment_and_wrap(t, dev):
    for _ in range(258):
        dev.respond(0x81)
    for _ in range(258):
        t.present()
    seqs = [w[1][6] for w in dev.writes]
    assert seqs[:3] == [0, 1, 2]
    assert seqs[255] == 255
    assert seqs[256] == 0
    assert seqs[257] == 1


def test_time_extension_retries(t, dev):
    dev.respond(0x80, bytes.fromhex("9000"), extensions=3)
    data, sw = t.transmit(bytes.fromhex("FFB0000410"))
    assert sw == b"\x90\x00"
    assert data == b""
    assert dev.pending == []


def test_stale_sequence_responses_discarded(t, dev):
    dev.respond(0x80, bytes.fromhex("01029000"), stale=2)
    data, sw = t.transmit(bytes.fromhex("FFB0000010"))
    assert data == b"\x01\x02"
    assert sw == b"\x90\x00"


def test_time_extension_retry_limit(dev, lockdir):
    tr = UsbCcidTransport(device=dev, max_time_extensions=5)
    dev.respond(0x80, bytes.fromhex("9000"), extensions=10)
    apdu = bytes.fromhex("FFB0000410")
    with pytest.raises(errors.NfcError):
        tr.transmit(apdu)


def test_split_response_reassembled(t, dev):
    payload = bytes(range(70)) + b"\x90\x00"

    def handler(req):
        full = rdr(0x80, req[6], payload)
        return [full[:64], full[64:]]

    dev.script.append(handler)
    data, sw = t.transmit(bytes.fromhex("FFB0000046"))
    assert data == bytes(range(70))
    assert sw == b"\x90\x00"


def test_transmit_card_removed(t, dev):
    dev.respond(0x80, status=0x42, error=0xFE)
    apdu = bytes.fromhex("FFCA000000")
    with pytest.raises(errors.CardRemoved):
        t.transmit(apdu)


def test_ccid_failure_generic(t, dev):
    dev.respond(0x80, status=0x40, error=0x05)
    apdu = bytes.fromhex("FFCA000000")
    with pytest.raises(errors.NfcError) as ei:
        t.transmit(apdu)
    assert ei.value.remediation


def test_short_rapdu_raises(t, dev):
    dev.respond(0x80, b"\x90")
    apdu = bytes.fromhex("FFCA000000")
    with pytest.raises(errors.NfcError):
        t.transmit(apdu)


# --------------------------------------------------------------------------- claim + lock


def test_claim_context_manager_claims_and_releases(t, dev):
    with t.claim():
        assert dev.claimed
        dev.respond(0x81)
        t.present()
    assert not dev.claimed


def test_nested_claim_is_reentrant(t, dev):
    with t.claim():
        with t.claim():
            assert dev.claimed
        assert dev.claimed
    assert not dev.claimed
    assert dev.claims == 1


def test_unclaimed_call_claims_for_that_operation(t, dev):
    dev.respond(0x81)
    t.present()
    assert dev.claims == 1
    assert not dev.claimed


def test_lock_file_records_pid_and_argv(t, dev, lockdir):
    import os

    with t.claim():
        files = list(Path(lockdir).glob("nfc-cli*.lock"))
        assert len(files) == 1
        info = json.loads(files[0].read_text())
        assert info["pid"] == os.getpid()
        assert info["argv"] == sys.argv


def test_lock_falls_back_to_tmp(monkeypatch, tmp_path, dev):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(transport.tempfile, "gettempdir", lambda: str(tmp_path))
    tr = UsbCcidTransport(device=dev)
    with tr.claim():
        assert list(tmp_path.glob("nfc-cli*.lock"))


def test_second_holder_raises_reader_busy_naming_holder(lockdir):
    import os

    a = UsbCcidTransport(device=FakeDevice())
    b = UsbCcidTransport(device=FakeDevice())
    with a.claim():
        with pytest.raises(errors.ReaderBusy) as ei:
            with b.claim():
                pass
    assert str(os.getpid()) in str(ei.value)
    assert ei.value.remediation
    with b.claim():  # released after the first holder exits
        pass


def test_usb_ebusy_maps_to_reader_busy(t, dev):
    dev.claim_error = FakeUSBError("Resource busy", errno=16)
    with pytest.raises(errors.ReaderBusy):
        with t.claim():
            pass


def test_usb_eacces_maps_to_reader_permission(t, dev):
    dev.claim_error = FakeUSBError("Access denied", errno=13)
    with pytest.raises(errors.ReaderPermission) as ei:
        with t.claim():
            pass
    assert "udev" in ei.value.remediation


def test_usb_enodev_maps_to_reader_not_found(t, dev):
    dev.claim_error = FakeUSBError("No such device", errno=19)
    with pytest.raises(errors.ReaderNotFound):
        with t.claim():
            pass


def test_usb_error_during_io_mapped(t, dev):
    def handler(req):
        raise FakeUSBError("Resource busy", errno=16)

    dev.script.append(handler)
    with pytest.raises(errors.ReaderBusy):
        t.present()


# --------------------------------------------------------------------------- selection


def test_select_device_default_index_and_busaddr():
    devs = [FakeDevice(1, 5), FakeDevice(3, 7)]
    assert select_device(devs, None) is devs[0]
    assert select_device(devs, "1") is devs[1]
    assert select_device(devs, 1) is devs[1]
    assert select_device(devs, "3:7") is devs[1]
    assert select_device(devs, "003:007") is devs[1]


def test_select_device_not_found():
    with pytest.raises(errors.ReaderNotFound):
        select_device([], None)
    dev_index = FakeDevice()
    with pytest.raises(errors.ReaderNotFound):
        select_device([dev_index], "5")
    dev_busaddr = FakeDevice()
    with pytest.raises(errors.ReaderNotFound):
        select_device([dev_busaddr], "9:9")
    dev_bogus = FakeDevice()
    with pytest.raises(errors.ReaderNotFound):
        select_device([dev_bogus], "bogus")


def test_missing_pyusb_raises_usb_unavailable(monkeypatch, lockdir):
    monkeypatch.setitem(sys.modules, "usb", None)
    monkeypatch.setitem(sys.modules, "usb.core", None)
    monkeypatch.setitem(sys.modules, "usb.util", None)
    with pytest.raises(errors.UsbUnavailable) as ei:
        UsbCcidTransport()
    assert "nfc-cli[usb]" in ei.value.remediation


# --------------------------------------------------------------------------- trace


def test_trace_records_every_exchange(dev, lockdir):
    buf = io.StringIO()
    tr = UsbCcidTransport(device=dev, trace=buf)
    dev.respond(0x80, bytes.fromhex("cb30c5609000"))
    dev.respond(0x83, b"\x01\x02")
    tr.transmit(bytes.fromhex("FFCA000000"))
    tr.escape(bytes.fromhex("E000001800"))
    out = buf.getvalue()
    lines = out.strip().splitlines()
    assert len(lines) == 4
    assert lines[0].startswith(">")
    assert "FFCA000000" in lines[0]
    assert lines[1].startswith("<")
    assert "CB30C560" in lines[1]
    assert "SW=9000" in lines[1]
    assert "ms" in lines[1]
    assert "E000001800" in lines[2]
    assert "0102" in lines[3]


def test_trace_masks_load_key(dev, lockdir):
    buf = io.StringIO()
    tr = UsbCcidTransport(device=dev, trace=buf)
    key = "A0A1A2A3A4A5"
    dev.respond(0x80, bytes.fromhex("9000"))
    tr.transmit(bytes.fromhex("FF82000006" + key))
    out = buf.getvalue()
    assert key not in out.upper()
    assert "FF82000006" + "**" * 6 in out


def test_no_trace_by_default(t, dev, capsys):
    dev.respond(0x81)
    t.present()
    cap = capsys.readouterr()
    assert cap.out == ""
    assert cap.err == ""


# --------------------------------------------------------------------------- errors


@pytest.mark.parametrize(
    "cls",
    [
        errors.ReaderNotFound,
        errors.ReaderPermission,
        errors.ReaderBusy,
        errors.NoCard,
        errors.CardRemoved,
        errors.AuthFailed,
        errors.UsbUnavailable,
    ],
)
def test_error_classes_have_remediation(cls):
    e = cls()
    assert isinstance(e, errors.NfcError)
    assert e.remediation
    assert str(e)
    e2 = cls("custom", remediation="do x")
    assert str(e2) == "custom"
    assert e2.remediation == "do x"


def test_reader_permission_hint_names_udev_docs():
    assert "docs/udev" in errors.ReaderPermission().remediation


def test_apdu_error_carries_sw():
    e = errors.ApduError(b"\x6a\x82")
    assert e.sw == b"\x6a\x82"
    assert "6A82" in str(e)
    assert e.remediation
    assert errors.ApduError(0x6982).sw == b"\x69\x82"
