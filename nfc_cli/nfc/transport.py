"""USB CCID transport for the ACS ACR1552, spoken directly over pyusb.

Pure module: no CLI imports, and ``usb`` is imported lazily inside
:class:`UsbCcidTransport` so the package imports without the ``usb`` extra.
Nothing else in nfc_cli may import ``usb``.
"""

from __future__ import annotations

import contextlib
import errno as _errno
import fcntl
import json
import os
import struct
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import IO, Any, Iterator, Protocol, runtime_checkable

from nfc_cli.nfc.errors import (
    CardRemoved,
    NfcError,
    NoCard,
    ReaderBusy,
    ReaderNotFound,
    ReaderPermission,
    UsbUnavailable,
)

VENDOR_ID = 0x072F
PRODUCT_ID = 0x2303
INTERFACE = 0
EP_OUT = 0x01
EP_IN = 0x81
HEADER = struct.Struct("<BIBB3B")
READ_SIZE = 65600
WRITE_TIMEOUT_MS = 2000
READ_TIMEOUT_MS = 5000

# PC_to_RDR message types and the RDR_to_PC type each one expects back.
PC_TO_RDR_ICC_POWER_ON = 0x62
PC_TO_RDR_ICC_POWER_OFF = 0x63
PC_TO_RDR_GET_SLOT_STATUS = 0x65
PC_TO_RDR_ESCAPE = 0x6B
PC_TO_RDR_XFR_BLOCK = 0x6F
RDR_TO_PC_DATA_BLOCK = 0x80
RDR_TO_PC_SLOT_STATUS = 0x81
RDR_TO_PC_ESCAPE = 0x83
_EXPECTED = {
    PC_TO_RDR_ICC_POWER_ON: RDR_TO_PC_DATA_BLOCK,
    PC_TO_RDR_XFR_BLOCK: RDR_TO_PC_DATA_BLOCK,
    PC_TO_RDR_ICC_POWER_OFF: RDR_TO_PC_SLOT_STATUS,
    PC_TO_RDR_GET_SLOT_STATUS: RDR_TO_PC_SLOT_STATUS,
    PC_TO_RDR_ESCAPE: RDR_TO_PC_ESCAPE,
}
_NAMES = {
    PC_TO_RDR_ICC_POWER_ON: "PowerOn",
    PC_TO_RDR_ICC_POWER_OFF: "PowerOff",
    PC_TO_RDR_GET_SLOT_STATUS: "SlotStatus",
    PC_TO_RDR_ESCAPE: "Escape",
    PC_TO_RDR_XFR_BLOCK: "XfrBlock",
}

# bStatus fields
ICC_ACTIVE = 0
ICC_INACTIVE = 1
ICC_ABSENT = 2
CMD_OK = 0
CMD_FAILED = 1
CMD_TIME_EXTENSION = 2
ERR_ICC_MUTE = 0xFE


@runtime_checkable
class Transport(Protocol):
    """What the tag layers need from a reader."""

    def power_on(self) -> bytes: ...

    def transmit(self, apdu: bytes) -> tuple[bytes, bytes]: ...

    def escape(self, cmd: bytes) -> bytes: ...

    def present(self) -> bool: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class CcidResponse:
    mtype: int
    seq: int
    status: int
    error: int
    specific: int
    data: bytes

    @property
    def icc_status(self) -> int:
        return self.status & 0x03

    @property
    def cmd_status(self) -> int:
        return self.status >> 6


def build_message(mtype: int, seq: int, data: bytes = b"", params=(0, 0, 0)) -> bytes:
    """Frame a PC_to_RDR message (10-byte header + payload)."""
    return HEADER.pack(mtype, len(data), 0, seq & 0xFF, *params) + bytes(data)


def parse_header(raw: bytes) -> tuple[int, int, int, int, int, int, int]:
    """(mtype, length, slot, seq, status, error, specific) of an RDR_to_PC message."""
    if len(raw) < HEADER.size:
        raise NfcError(f"short CCID response ({len(raw)} bytes)", "replug the reader and retry")
    return HEADER.unpack(raw[: HEADER.size])


def map_usb_error(exc: BaseException) -> NfcError:
    """Translate a USBError/OSError (by errno) into a typed NfcError."""
    if isinstance(exc, NfcError):
        return exc
    code = getattr(exc, "errno", None)
    if code == _errno.EBUSY:
        return ReaderBusy(f"USB interface busy: {exc}")
    if code in (_errno.EACCES, _errno.EPERM):
        return ReaderPermission(f"permission denied on the USB reader: {exc}")
    if code in (_errno.ENODEV, _errno.ENOENT):
        return ReaderNotFound(f"reader disappeared: {exc}")
    if code == _errno.ETIMEDOUT:
        return NfcError(f"reader timed out: {exc}", "replug the reader and retry")
    return NfcError(f"USB error: {exc}", "replug the reader and retry; re-run with --trace")


def select_device(devices: list[Any], reader: int | str | None) -> Any:
    """Pick a device by index (``0``, ``"1"``) or ``"bus:addr"``; default the first."""
    devices = list(devices)
    if not devices:
        raise ReaderNotFound()
    if reader is None or reader == "":
        return devices[0]
    spec = str(reader).strip()
    if ":" in spec:
        bus_s, _, addr_s = spec.partition(":")
        try:
            bus, addr = int(bus_s, 10), int(addr_s, 10)
        except ValueError:
            raise ReaderNotFound(f"invalid --reader {spec!r}; use INDEX or BUS:ADDR") from None
        for dev in devices:
            if getattr(dev, "bus", None) == bus and getattr(dev, "address", None) == addr:
                return dev
        raise ReaderNotFound(f"no ACR1552 reader at bus {bus} address {addr}")
    try:
        index = int(spec, 10)
    except ValueError:
        raise ReaderNotFound(f"invalid --reader {spec!r}; use INDEX or BUS:ADDR") from None
    if not 0 <= index < len(devices):
        raise ReaderNotFound(f"reader index {index} out of range ({len(devices)} found)")
    return devices[index]


def _import_usb():
    try:
        import usb.core  # noqa: F401  (lazy: the usb extra is optional)
        import usb.util  # noqa: F401
    except ImportError as exc:
        raise UsbUnavailable(f"pyusb is not installed: {exc}") from None
    return sys.modules["usb.core"], sys.modules["usb.util"]


def find_devices() -> list[Any]:
    """All attached ACR1552 devices (requires pyusb)."""
    core, _ = _import_usb()
    try:
        return list(core.find(find_all=True, idVendor=VENDOR_ID, idProduct=PRODUCT_ID))
    except core.NoBackendError:
        raise UsbUnavailable(
            "libusb-1.0 backend not found", "install libusb-1.0 (e.g. apt install libusb-1.0-0)"
        ) from None
    except Exception as exc:  # USBError while enumerating
        raise map_usb_error(exc) from exc


def lock_dir() -> str:
    return os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()


class _ReaderLock:
    """Advisory flock on a per-reader file recording the holder's pid + argv."""

    def __init__(self, name: str):
        self.path = os.path.join(lock_dir(), f"nfc-cli-{name}.lock")
        self._fd: int | None = None

    def acquire(self) -> None:
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            holder = self._read_holder(fd)
            os.close(fd)
            raise ReaderBusy(f"the reader is in use by {holder}") from None
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps({"pid": os.getpid(), "argv": sys.argv}).encode())
        self._fd = fd

    def release(self) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None

    @staticmethod
    def _read_holder(fd: int) -> str:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            info = json.loads(os.read(fd, 65536).decode() or "{}")
            return f"pid {info.get('pid', '?')} ({' '.join(info.get('argv', [])) or '?'})"
        except (OSError, ValueError):
            return "another process"


def _mask_apdu(apdu: bytes) -> str:
    """Hex of an APDU, with key bytes of FF 82 (Load Key) replaced by ``**``."""
    hx = apdu.hex().upper()
    if len(apdu) > 5 and apdu[0] == 0xFF and apdu[1] == 0x82:
        return hx[:10] + "**" * (len(apdu) - 5)
    return hx


class UsbCcidTransport:
    """Transport over raw USB CCID (bulk EP 0x01/0x81, interface 0).

    ``device`` may be injected (tests); otherwise pyusb is imported here and the
    reader is chosen with ``reader`` (index or ``bus:addr``). The USB interface and
    the advisory lock are held only inside :meth:`claim`; an operation called
    outside a claim claims for just that operation.
    """

    def __init__(
        self,
        reader: int | str | None = None,
        *,
        device: Any = None,
        trace: IO[str] | None = None,
        max_time_extensions: int = 200,
        max_stale: int = 16,
    ):
        self._usb_util = None
        if device is None:
            _, self._usb_util = _import_usb()
            device = select_device(find_devices(), reader)
        self._dev = device
        self._trace = trace
        self._max_ext = max_time_extensions
        self._max_stale = max_stale
        self._seq = 0
        self._depth = 0
        bus, addr = getattr(device, "bus", None), getattr(device, "address", None)
        name = f"{bus:03d}-{addr:03d}" if bus is not None and addr is not None else "default"
        self._lock = _ReaderLock(name)

    # ------------------------------------------------------------------ claim

    @contextlib.contextmanager
    def claim(self) -> Iterator[UsbCcidTransport]:
        """Hold the reader lock and USB interface for the duration of the block."""
        if self._depth:
            self._depth += 1
            try:
                yield self
            finally:
                self._depth -= 1
            return
        self._lock.acquire()
        try:
            self._claim_interface()
        except BaseException:
            self._lock.release()
            raise
        self._depth = 1
        try:
            yield self
        finally:
            self._depth = 0
            try:
                self._release_interface()
            finally:
                self._lock.release()

    def __enter__(self) -> UsbCcidTransport:
        self._outer = self.claim()
        return self._outer.__enter__()

    def __exit__(self, *exc) -> None:
        self._outer.__exit__(*exc)

    def _claim_interface(self) -> None:
        dev = self._dev
        try:
            if self._usb_util is not None:
                with contextlib.suppress(NotImplementedError):
                    if dev.is_kernel_driver_active(INTERFACE):
                        dev.detach_kernel_driver(INTERFACE)
                self._usb_util.claim_interface(dev, INTERFACE)
            elif hasattr(dev, "claim_interface"):
                dev.claim_interface(INTERFACE)
        except Exception as exc:
            raise map_usb_error(exc) from exc

    def _release_interface(self) -> None:
        dev = self._dev
        with contextlib.suppress(Exception):
            if self._usb_util is not None:
                self._usb_util.release_interface(dev, INTERFACE)
            elif hasattr(dev, "release_interface"):
                dev.release_interface(INTERFACE)

    # ------------------------------------------------------------------ CCID

    def _exchange(self, mtype: int, data: bytes = b"") -> CcidResponse:
        with self.claim():
            seq = self._seq
            self._seq = (self._seq + 1) & 0xFF
            msg = build_message(mtype, seq, data)
            try:
                self._dev.write(EP_OUT, msg, WRITE_TIMEOUT_MS)
                return self._read_response(_EXPECTED[mtype], seq)
            except NfcError:
                raise
            except Exception as exc:
                raise map_usb_error(exc) from exc

    def _read_raw(self) -> bytes:
        raw = bytes(self._dev.read(EP_IN, READ_SIZE, READ_TIMEOUT_MS))
        _, length, *_ = parse_header(raw)
        while len(raw) < HEADER.size + length:
            more = bytes(self._dev.read(EP_IN, READ_SIZE, READ_TIMEOUT_MS))
            if not more:
                break
            raw += more
        return raw

    def _read_response(self, expected: int, seq: int) -> CcidResponse:
        extensions = stale = 0
        while True:
            raw = self._read_raw()
            mtype, length, _slot, rseq, status, error, specific = parse_header(raw)
            if rseq != seq or mtype != expected:
                stale += 1
                if stale > self._max_stale:
                    raise NfcError(
                        "reader responses out of sequence", "replug the reader and retry"
                    )
                continue
            resp = CcidResponse(
                mtype, rseq, status, error, specific, raw[HEADER.size : HEADER.size + length]
            )
            if resp.cmd_status == CMD_TIME_EXTENSION:
                extensions += 1
                if extensions > self._max_ext:
                    raise NfcError(
                        "card did not answer (too many time extensions)",
                        "hold the tag still on the reader and retry",
                    )
                continue
            return resp

    def _check(self, resp: CcidResponse, *, removed: bool) -> CcidResponse:
        if resp.cmd_status == CMD_FAILED:
            if resp.icc_status == ICC_ABSENT or resp.error == ERR_ICC_MUTE:
                raise CardRemoved() if removed else NoCard()
            raise NfcError(
                f"reader command failed (bStatus={resp.status:#04x} bError={resp.error:#04x})",
                "lift and re-place the tag, then retry; re-run with --trace",
            )
        return resp

    def _emit(self, line: str) -> None:
        if self._trace is not None:
            self._trace.write(line + "\n")
            with contextlib.suppress(Exception):
                self._trace.flush()

    def _traced(self, mtype: int, data: bytes, shown: str) -> tuple[CcidResponse, float]:
        name = _NAMES[mtype]
        self._emit(f"> {name} {shown}".rstrip())
        start = time.monotonic()
        resp = self._exchange(mtype, data)
        return resp, (time.monotonic() - start) * 1000.0

    # ------------------------------------------------------------------ Transport API

    def present(self) -> bool:
        resp, ms = self._traced(PC_TO_RDR_GET_SLOT_STATUS, b"", "")
        self._emit(f"< SlotStatus icc={resp.icc_status} {ms:.1f}ms")
        return resp.icc_status != ICC_ABSENT

    def power_on(self) -> bytes:
        resp, ms = self._traced(PC_TO_RDR_ICC_POWER_ON, b"", "")
        self._emit(f"< PowerOn {resp.data.hex().upper()} {ms:.1f}ms")
        return self._check(resp, removed=False).data

    def power_off(self) -> None:
        resp, ms = self._traced(PC_TO_RDR_ICC_POWER_OFF, b"", "")
        self._emit(f"< PowerOff icc={resp.icc_status} {ms:.1f}ms")

    def transmit(self, apdu: bytes) -> tuple[bytes, bytes]:
        apdu = bytes(apdu)
        resp, ms = self._traced(PC_TO_RDR_XFR_BLOCK, apdu, _mask_apdu(apdu))
        self._check(resp, removed=True)
        rapdu = resp.data
        if len(rapdu) < 2:
            self._emit(f"< XfrBlock {rapdu.hex().upper()} SW=? {ms:.1f}ms")
            raise NfcError(
                f"short response from card ({len(rapdu)} bytes)",
                "lift and re-place the tag, then retry",
            )
        data, sw = rapdu[:-2], rapdu[-2:]
        self._emit(f"< XfrBlock {data.hex().upper()} SW={sw.hex().upper()} {ms:.1f}ms")
        return data, sw

    def escape(self, cmd: bytes) -> bytes:
        cmd = bytes(cmd)
        resp, ms = self._traced(PC_TO_RDR_ESCAPE, cmd, cmd.hex().upper())
        self._emit(f"< Escape {resp.data.hex().upper()} {ms:.1f}ms")
        return self._check(resp, removed=False).data

    def close(self) -> None:
        if self._depth:
            self._depth = 0
            self._release_interface()
            self._lock.release()
        if self._usb_util is not None:
            with contextlib.suppress(Exception):
                self._usb_util.dispose_resources(self._dev)
