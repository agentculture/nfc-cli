"""Reader info and safe reader configuration for the ACR1552.

Only the PICC polling type is ever written. Keyboard/operating-mode commands
(``E0 00 00 9x``) are deliberately unreachable: :func:`_escape` refuses them.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any, Callable, Iterator

from nfc_cli.nfc.errors import NfcError, ReaderBusy

_GET_FIRMWARE = bytes.fromhex("E000001800")
_GET_POLLING = bytes.fromhex("E000012000")
_SET_POLLING_PREFIX = bytes.fromhex("E000012002")
_RESP_HEADER = bytes.fromhex("E1000000")

# (byte index counted from the 2-byte big-endian mask, bit) -> name
POLLING_BITS: dict[str, int] = {
    "iso14443a": 0x0100,
    "iso14443b": 0x0200,
    "felica": 0x0400,
    "topaz": 0x1000,
    "innovatron": 0x2000,
    "sri": 0x4000,
    "picopass-14443b": 0x0001,
    "picopass-15693": 0x0002,
    "iso15693": 0x0004,
    "cts": 0x0008,
}
_VALID_MASK = sum(POLLING_BITS.values())

_RESTORE_ATTEMPTS = 3


def _escape(transport: Any, cmd: bytes) -> bytes:
    """Send an escape command, refusing the keyboard/operating-mode family."""
    if cmd[:4] == b"\xe0\x00\x00" and 0x90 <= cmd[3] <= 0x9F:
        raise NfcError(
            "refusing to send a keyboard/operating-mode command",
            "nfc-cli never changes the reader operating mode",
        )
    if cmd != _GET_FIRMWARE and cmd != _GET_POLLING and not cmd.startswith(_SET_POLLING_PREFIX):
        raise NfcError(f"unsupported reader command {cmd.hex().upper()}", "internal error")
    return transport.escape(cmd)


def _payload(resp: bytes) -> bytes:
    if len(resp) < 5 or resp[:4] != _RESP_HEADER or len(resp) - 5 < resp[4]:
        raise NfcError(f"unexpected reader response {resp.hex().upper()}")
    return resp[5 : 5 + resp[4]]


def decode_polling(mask: int) -> list[str]:
    return [name for name, bit in POLLING_BITS.items() if mask & bit]


def encode_polling(names: list[str]) -> int:
    mask = 0
    for n in names:
        if n not in POLLING_BITS:
            raise NfcError(f"unknown polling type {n!r}", f"choose from: {', '.join(POLLING_BITS)}")
        mask |= POLLING_BITS[n]
    return mask


def firmware(transport: Any) -> str:
    return _payload(_escape(transport, _GET_FIRMWARE)).decode("ascii", "replace")


def polling_mask(transport: Any) -> int:
    data = _payload(_escape(transport, _GET_POLLING))
    if len(data) != 2:
        raise NfcError(f"unexpected polling-type length {len(data)}")
    return (data[0] << 8) | data[1]


def polling_type(transport: Any) -> list[str]:
    return decode_polling(polling_mask(transport))


def set_polling_mask(transport: Any, mask: int) -> int:
    if mask & ~_VALID_MASK & 0xFFFF:
        raise NfcError(f"polling mask {mask:#06x} sets reserved bits", "use only documented bits")
    cmd = _SET_POLLING_PREFIX + bytes([(mask >> 8) & 0xFF, mask & 0xFF])
    data = _payload(_escape(transport, cmd))
    return (data[0] << 8) | data[1] if len(data) == 2 else -1


def slot_status(transport: Any) -> dict:
    present = bool(transport.present())
    return {"present": present, "state": "present" if present else "absent"}


@contextlib.contextmanager
def with_polling_type(
    factory: Callable[[], Any], mask: int, *, sleep: Callable[[float], None] = time.sleep
) -> Iterator[Any]:
    """Temporarily set the polling type; always restore from a fresh claim.

    ``factory`` returns a new transport each call. The body runs in one claim; that
    claim is released before the restore, which re-claims on a fresh transport,
    retries while the reader is busy, and verifies by read-back.
    """
    first = factory()
    with first.claim() as t:
        original = polling_mask(t)
        set_polling_mask(t, mask)
        if polling_mask(t) != mask:
            _restore(factory, original, sleep)
            raise NfcError("could not set the polling type", "retry; the reader was restored")
    try:
        with first.claim() as t:
            yield t
    finally:
        _restore(factory, original, sleep)


def _restore(factory: Callable[[], Any], original: int, sleep: Callable[[float], None]) -> None:
    last: BaseException | None = None
    for attempt in range(_RESTORE_ATTEMPTS):
        try:
            fresh = factory()
            try:
                with fresh.claim() as t:
                    set_polling_mask(t, original)
                    if polling_mask(t) == original:
                        return
                    last = NfcError("read-back mismatch")
            finally:
                closer = getattr(fresh, "close", None)
                if closer:
                    closer()
        except ReaderBusy as exc:
            last = exc
        if attempt + 1 < _RESTORE_ATTEMPTS:
            sleep(0.2)
    raise NfcError(
        f"could not verify polling type restore to {original:#06x}: {last}",
        f"run `nfc-cli reader polling --set {original:#06x}` or replug the reader",
    )
