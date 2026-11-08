"""High-level tag operations: the one module composing transport/classic/families/ndef/dump/journal.

Every public function takes an already-claimed :class:`~nfc_cli.nfc.transport.Transport` and
returns a plain JSON-serialisable dict, so registry handlers pass results straight to the CLI,
MCP and /api/ surfaces. Only :class:`~nfc_cli.nfc.errors.NfcError` subclasses are raised.

Safety model for every mutating operation (``write``, ``restore``, ``resume``, ``ndef_*``):

* ``apply=False`` (the default) reads the current contents, returns a per-block diff and sends
  **zero** write APDUs.
* ``apply=True`` first saves a 0600 snapshot of everything readable (aborting with zero writes
  if that fails), records every planned block in a journal, then writes block by block, each
  write followed by a read-back compare that is journaled. A dropout raises
  :class:`WriteInterrupted` naming the journal; :func:`resume` completes the pending blocks.
* MIFARE Classic sector trailers and block 0 (and Type 2 pages 0-3) are skipped unless
  ``include_trailers``; :func:`ndef_format` is the only operation that writes trailers on its
  own, and only from :func:`nfc_cli.nfc.ndef.classic_format_template` after validating it.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from nfc_cli.nfc import atr as atr_mod
from nfc_cli.nfc import classic, dumpfile, families, ndef
from nfc_cli.nfc.errors import (
    ApduError,
    AuthFailed,
    CardRemoved,
    NfcError,
    NoCard,
)
from nfc_cli.nfc.journal import Journal
from nfc_cli.nfc.transport import Transport

__all__ = [
    "InvalidInput",
    "NotNdefFormatted",
    "SnapshotFailed",
    "VerifyFailed",
    "WriteInterrupted",
    "append_audit",
    "audit_log_path",
    "connect",
    "dump",
    "info",
    "journal_dir",
    "ndef_erase",
    "ndef_format",
    "ndef_read",
    "ndef_write",
    "raw_apdu",
    "read",
    "restore",
    "resume",
    "uid",
    "wait_for_card",
    "write",
]

CLASSIC_SECTORS = {"classic_1k": 16, "classic_4k": 40, "classic_mini": 5}
_MAX_DROPOUTS = 20
Sleep = Callable[[float], None]
Clock = Callable[[], float]


# ===================================================================== errors


class InvalidInput(NfcError):
    default_message = "invalid input"
    default_remediation = "check the arguments (hex data, block numbers, file paths)"


class SnapshotFailed(NfcError):
    default_message = "could not save the pre-write snapshot; nothing was written"
    default_remediation = (
        "make sure $XDG_DATA_HOME (or ~/.local/share) is writable and the tag stays on the reader"
    )


class VerifyFailed(NfcError):
    default_message = "read-back after write did not match"
    default_remediation = "re-run with --resume JOURNAL; if it persists the block may be locked"


class CardChanged(NfcError):
    default_message = "the tag on the reader is not the one that was previewed; nothing was written"
    default_remediation = "re-read the tag and preview the write again"


def _require_uid(card: Card, expect_uid: str | None) -> None:
    """Refuse to write when the present tag is not the one the caller previewed."""
    if expect_uid and card.uid_hex != expect_uid:
        raise CardChanged(
            f"the tag on the reader (UID {card.uid_hex}) is not the one you previewed "
            f"(UID {expect_uid}); nothing was written",
        )


class NotNdefFormatted(NfcError):
    default_message = "the tag is not NDEF formatted"
    default_remediation = "run `nfc-cli ndef format --apply` first (MIFARE Classic)"


class WriteInterrupted(CardRemoved):
    """The card left mid-write; the journal lists what is still pending."""

    def __init__(self, journal: str, pending: list[int], verified: int, cause: str):
        self.journal = journal
        self.pending = list(pending)
        self.verified = verified
        super().__init__(
            f"{cause}: {verified} block(s) written and verified, {len(pending)} pending "
            f"{pending}; journal {journal}",
            f"put the tag back and run `nfc-cli tag restore --resume {journal} --apply`",
        )


# ===================================================================== paths / audit


def _state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "nfc-cli"


def journal_dir() -> Path:
    return _state_dir() / "journals"


def dumps_dir() -> Path:
    return dumpfile.snapshot_dir().parent / "dumps"


def audit_log_path() -> Path:
    return _state_dir() / "audit.log"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _write_private(path: Path, payload: bytes, append: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC)
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)


def append_audit(surface: str, uid: str, blocks: Iterable[int]) -> Path:
    """Append one JSON line (timestamp, surface, uid, blocks) to the 0600 audit log."""
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "surface": surface,
        "uid": uid,
        "blocks": sorted(int(b) for b in blocks),
    }
    path = audit_log_path()
    try:
        _write_private(path, (json.dumps(rec) + "\n").encode(), append=True)
    except OSError as exc:
        raise NfcError(
            f"could not append to the audit log {path}: {exc}",
            "make sure $XDG_STATE_HOME (or ~/.local/state) is writable",
        ) from None
    return path


# ===================================================================== card session


@dataclass
class Card:
    atr: bytes
    info: atr_mod.AtrInfo
    uid: bytes

    @property
    def family(self) -> str:
        return self.info.family

    @property
    def uid_hex(self) -> str:
        return self.uid.hex()

    @property
    def is_classic(self) -> bool:
        return self.family in CLASSIC_SECTORS


def _hex(data: str | bytes, what: str = "data") -> bytes:
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    text = "".join(str(data).split()).replace(":", "")
    try:
        return bytes.fromhex(text)
    except ValueError:
        raise InvalidInput(f"{what} is not valid hex: {data!r}") from None


def wait_for_card(
    t: Transport,
    timeout: float,
    *,
    interval: float = 0.2,
    sleep: Sleep = time.sleep,
    clock: Clock = time.monotonic,
) -> dict:
    """Poll ``present()`` until a card is on the reader or ``timeout`` seconds pass (NoCard)."""
    start = clock()
    deadline = start + max(0.0, float(timeout))
    while True:
        if t.present():
            return {"present": True, "waited": round(clock() - start, 3)}
        now = clock()
        if now >= deadline:
            raise NoCard(f"no card on the reader after {timeout}s")
        sleep(min(interval, max(0.0, deadline - now)))


def connect(t: Transport) -> Card:
    """Power the card on, parse the ATR and read the UID (empty if the card refuses FF CA)."""
    raw = bytes(t.power_on())
    info = atr_mod.parse(raw)
    try:
        uid_b = classic.get_uid(t)
    except ApduError:
        uid_b = b""
    return Card(raw, info, uid_b)


def _power_cycle(t: Transport) -> None:
    off = getattr(t, "power_off", None)
    if off is not None:
        off()
    t.power_on()


def _reacquire(t: Transport, card: Card, timeout: float, sleep: Sleep, clock: Clock) -> None:
    """After a dropout: wait for the card, power it on and make sure it is the same tag."""
    deadline = clock() + timeout
    while True:
        wait_for_card(t, max(0.0, deadline - clock()), sleep=sleep, clock=clock)
        try:
            again = connect(t)
            break
        except (NoCard, CardRemoved):
            if clock() >= deadline:
                raise
    if card.uid and again.uid and again.uid != card.uid:
        raise NfcError(
            f"a different tag ({again.uid_hex}) was placed; expected {card.uid_hex}",
            "put the original tag back on the reader",
        )


# ===================================================================== block accessors


class _Access:
    """Per-family block access used by the plan/execute engine."""

    block_size = 16

    def __init__(self, t: Transport):
        self.t = t

    def all_blocks(self) -> list[int]:
        raise families.UnsupportedOperation("this tag family has no block map")

    def protected(self, block: int) -> str | None:
        return None

    def is_trailer(self, block: int) -> bool:
        return False

    def read(self, block: int) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError

    def write(self, block: int, data: bytes) -> None:
        raise families.UnsupportedOperation("writing is not supported for this tag family")

    def effective(self, block: int, raw: bytes) -> bytes:
        return raw

    def verify(self, block: int, data: bytes) -> bool:
        return self.read(block) == data

    def same(self, block: int, old: bytes, new: bytes) -> bool:
        return old == new

    def invalidate(self) -> None:
        """Forget volatile card state (after a dropout or power cycle)."""

    def mask(self, block: int, hexstr: str) -> str:
        return hexstr


class _ClassicAccess(_Access):
    block_size = 16

    def __init__(
        self,
        t: Transport,
        family: str,
        extra_keys: Iterable[bytes] = (),
        key: bytes | None = None,
        key_type: str | None = None,
    ):
        super().__init__(t)
        self.sectors = CLASSIC_SECTORS[family]
        self.extra = list(extra_keys)
        self.preferred = (str(key_type or "A").upper(), key) if key else None
        self.found: dict[int, tuple[str, bytes]] = {}
        self.authed: tuple[int, str] | None = None
        self._trailers: dict[int, bytes] = {}
        self._uid: bytes | None = None

    def sector_blocks(self, sector: int) -> list[int]:
        first = classic.first_block(sector)
        return list(range(first, first + classic.sector_size(sector)))

    def all_blocks(self) -> list[int]:
        return [b for s in range(self.sectors) for b in self.sector_blocks(s)]

    def protected(self, block: int) -> str | None:
        if block == 0:
            return "manufacturer block 0"
        if classic.is_trailer(block):
            return "sector trailer"
        return None

    def is_trailer(self, block: int) -> bool:
        return block == 0 or classic.is_trailer(block)

    def _try(self, sector: int, kt: str, key: bytes) -> bool:
        try:
            classic.authenticate(self.t, sector, key, kt)
        except AuthFailed:
            return False
        self.found[sector] = (kt, key)
        self.authed = (sector, kt)
        return True

    def auth(self, sector: int, key_types: tuple[str, ...] = ("A", "B")) -> None:
        if self.authed and self.authed[0] == sector and self.authed[1] in key_types:
            return
        for cand in (self.found.get(sector), self.preferred):
            if cand and cand[0] in key_types and self._try(sector, cand[0], cand[1]):
                return
        kt, k = classic.find_key(self.t, sector, self.extra, key_types)
        self.found[sector] = (kt, k)
        self.authed = (sector, kt)

    def read(self, block: int) -> bytes:
        self.auth(classic.sector_of(block))
        return classic.read_block(self.t, block)

    def _write_keys(self, block: int) -> tuple[str, ...]:
        """Key types the sector's access bits allow for writing ``block`` (cached per sector)."""
        sector = classic.sector_of(block)
        trailer = self._trailers.get(sector)
        if trailer is None:
            self.auth(sector)
            trailer = classic.read_block(self.t, classic.trailer_block(sector))
            self._trailers[sector] = trailer
        return classic.write_key_types(trailer, block)

    def _recover(self) -> bool:
        """After a mute card: power-cycle and report whether the same tag answers again."""
        for _ in range(5):
            try:
                _power_cycle(self.t)
                uid = classic.get_uid(self.t)
            except (NoCard, CardRemoved, ApduError):
                time.sleep(0.2)
                continue
            return self._uid is None or uid == self._uid
        return False

    def write(self, block: int, data: bytes) -> None:
        sector = classic.sector_of(block)
        if self._uid is None:
            self._uid = classic.get_uid(self.t)
        allowed = self._write_keys(block)
        if not allowed:
            raise classic.ClassicError(
                f"block {block}: the sector's access bits allow no key to write it",
                "this block is read-only on the tag; leave it out or restore another sector",
            )
        self.auth(sector, allowed)
        try:
            classic.write_block(self.t, block, data, allow_trailer=True)
        except (ApduError, CardRemoved):
            # A refused write shows up as an error SW, or -- on the ACR1552, because the chip
            # halts after the NAK -- as a mute card the reader drops from the field. Recover by
            # power-cycling; only a tag that does not come back keeps the CardRemoved.
            if not self._recover():
                raise
            self.authed = None
            self.auth(sector, allowed)  # re-auth with a key the access bits allow for this block
            classic.write_block(self.t, block, data, allow_trailer=True)
        if classic.is_trailer(block):
            self.found.pop(sector, None)
            self._trailers.pop(sector, None)
            self.authed = None

    def effective(self, block: int, raw: bytes) -> bytes:
        """Trailers read back key A as zeros: fill in the key that opened the sector."""
        if not classic.is_trailer(block):
            return raw
        kt, key = self.found.get(classic.sector_of(block), ("", b""))
        if kt == "A":
            raw = key + raw[6:]
        elif kt == "B" and raw[10:16] == bytes(6):
            raw = raw[:10] + key
        return raw

    def same(self, block: int, old: bytes, new: bytes) -> bool:
        """Trailers hide key B: equal if it is unreadable and the new key B authenticates."""
        if old == new:
            return True
        if not classic.is_trailer(block) or old[:10] != new[:10] or old[10:] != bytes(6):
            return False
        sector = classic.sector_of(block)
        self.authed = None
        return self._try(sector, "B", new[10:16])

    def verify(self, block: int, data: bytes) -> bool:
        back = self.read(block)
        if not classic.is_trailer(block):
            return back == data
        if back[6:10] != data[6:10]:
            return False
        sector = classic.sector_of(block)
        self.authed = None
        return self._try(sector, "A", data[:6])

    def invalidate(self) -> None:
        self.authed = None

    def mask(self, block: int, hexstr: str) -> str:
        if classic.is_trailer(block) and len(hexstr) == 32:
            return dumpfile.mask_trailer(hexstr)
        return hexstr


class _Type2Access(_Access):
    block_size = 4

    def __init__(self, t: Transport):
        super().__init__(t)
        self.ops = families.Type2Ops()

    def all_blocks(self) -> list[int]:
        try:
            size = self.ops.memory_size(self.t)
        except ndef.NdefError:
            size = 0
        return list(range(4 + size // 4))

    def protected(self, block: int) -> str | None:
        return "UID / lock / capability-container page" if block < 4 else None

    def is_trailer(self, block: int) -> bool:
        return block < 4

    def read(self, block: int) -> bytes:
        return self.ops.read_pages(self.t, block, 1)[:4]

    def write(self, block: int, data: bytes) -> None:
        self.ops.write_page(self.t, block, data)


class _Iso15693Access(_Access):
    def __init__(self, t: Transport):
        super().__init__(t)
        self.ops = families.Iso15693Ops()
        self.count, self.block_size = self.ops.geometry(t)

    def all_blocks(self) -> list[int]:
        if self.count is not None:
            return list(range(self.count))
        out = []
        for b in range(256):
            try:
                self.read(b)
            except ApduError:
                break
            out.append(b)
        return out

    def read(self, block: int) -> bytes:
        return self.ops.read_block(self.t, block, self.block_size)

    def write(self, block: int, data: bytes) -> None:
        self.ops.write_block(self.t, block, data)


class _ReadOnlyAccess(_Access):
    def __init__(self, t: Transport, ops: families.ReadOnlyOps):
        super().__init__(t)
        self.ops = ops
        self.block_size = ops.block_size

    def all_blocks(self) -> list[int]:
        out = []
        for b in range(self.ops.max_block + 1):
            try:
                self.read(b)
            except (ApduError, NfcError):
                break
            out.append(b)
        return out

    def read(self, block: int) -> bytes:
        return self.ops.read_blocks(self.t, block, 1)


def _access(t: Transport, card: Card, keys: Iterable[bytes] = (), key=None, key_type=None):
    if card.is_classic:
        return _ClassicAccess(t, card.family, keys, key, key_type)
    ops = families.family_ops(card.family)
    if isinstance(ops, families.Type2Ops):
        return _Type2Access(t)
    if isinstance(ops, families.Iso15693Ops):
        return _Iso15693Access(t)
    if isinstance(ops, families.ReadOnlyOps):
        return _ReadOnlyAccess(t, ops)
    raise families.UnsupportedOperation(
        f"{card.family}: block access is not available for this tag family",
        "use `nfc-cli ndef read` (Type 4) or `nfc-cli tag apdu`",
    )


def _dump_family(family: str) -> str:
    if family in CLASSIC_SECTORS:
        return dumpfile.FAMILY_CLASSIC
    if family.startswith("ultralight"):
        return dumpfile.FAMILY_ULTRALIGHT
    return family


def _out_dump(d: dumpfile.Dump, include_keys: bool) -> dict:
    return d.to_dict(include_keys=include_keys)


# ===================================================================== reading


def info(t: Transport) -> dict:
    card = connect(t)
    out: dict[str, Any] = {
        "atr": card.atr.hex(),
        "uid": card.uid_hex,
        "family": card.family,
        "standard": card.info.standard,
        "checksum_ok": card.info.checksum_ok,
    }
    try:
        if card.is_classic:
            n = CLASSIC_SECTORS[card.family]
            out["sectors"] = n
            out["blocks"] = len(_ClassicAccess(t, card.family).all_blocks())
        else:
            ops = families.family_ops(card.family)
            if isinstance(ops, families.Type2Ops):
                out["capability"] = ops.capability(t)
            elif isinstance(ops, families.Iso15693Ops):
                count, size = ops.geometry(t)
                out["block_count"], out["block_size"] = count, size
            elif isinstance(ops, families.Type4Ops):
                cc = ops.open_ndef(t)
                out["ndef"] = {**cc, "fid": cc["fid"].hex()}
    except (NfcError, ndef.NdefError) as exc:
        if isinstance(exc, CardRemoved):
            raise
        out["note"] = str(exc)
    return out


def uid(t: Transport) -> dict:
    card = connect(t)
    return {"uid": card.uid_hex, "family": card.family}


def magic_check(t: Transport, **keyargs) -> dict:
    """Report whether a MIFARE Classic tag's UID block (block 0) can be rewritten.

    Non-destructive: it writes block 0's own bytes back to itself. A genuine chip
    refuses (block 0 is read-only); a UID-rewritable card accepts. Used to tell
    whether `tag write --block0` can clone a UID onto this tag.
    """
    card = connect(t)
    if card.family not in CLASSIC_SECTORS:
        raise InvalidInput(
            f"magic-check is for MIFARE Classic; this tag is {card.family}",
            "block-0 rewriting is a MIFARE Classic magic-card feature",
        )
    acc = _access(t, card, **_key_args(keyargs))
    acc.auth(0)  # honours --key/--key-type, then found keys, then the default dictionary
    kt, key = acc.found[0]
    writable = classic.probe_block0_writable(t, key, kt)
    return {
        "uid": card.uid_hex,
        "family": card.family,
        "block0_writable": writable,
        "note": (
            "UID-rewritable (magic) card: `tag write --block0` can change the UID"
            if writable
            else "block 0 is read-only (genuine chip); the UID cannot be changed"
        ),
    }


def _read_all(
    t: Transport,
    card: Card,
    acc: _Access,
    *,
    existing: dumpfile.Dump | None = None,
    retry_timeout: float = 30.0,
    sleep: Sleep = time.sleep,
    clock: Clock = time.monotonic,
    on_progress: Callable[[dumpfile.Dump], None] | None = None,
) -> tuple[dumpfile.Dump, list[int]]:
    """Read every block, per sector (Classic) / chunk, resuming after dropouts."""
    d = existing or dumpfile.Dump(card.atr.hex(), card.uid_hex, _dump_family(card.family))
    if isinstance(acc, _ClassicAccess):
        groups = [(s, acc.sector_blocks(s)) for s in range(acc.sectors)]
    else:
        blocks = acc.all_blocks()
        groups = [(i // 4, blocks[i : i + 4]) for i in range(0, len(blocks), 4)]
    failed: list[int] = []
    dropouts = 0
    for group, blocks in groups:
        if all(b in d.blocks for b in blocks):
            continue
        while True:
            try:
                rows = [acc.effective(b, acc.read(b)) for b in blocks]
                break
            except AuthFailed:
                rows = None
                break
            except (CardRemoved, NoCard):
                dropouts += 1
                if dropouts > _MAX_DROPOUTS:
                    raise
                acc.invalidate()
                _reacquire(t, card, retry_timeout, sleep, clock)
        if rows is None:
            failed.append(group)
            continue
        for b, row in zip(blocks, rows):
            d.blocks[b] = row.hex()
        if isinstance(acc, _ClassicAccess) and group in acc.found:
            kt, k = acc.found[group]
            d.keys_used[group] = (kt, k.hex())
        if on_progress:
            on_progress(d)
    return d, failed


def read(
    t: Transport,
    block: int | None = None,
    count: int = 1,
    *,
    keys: Iterable[bytes] = (),
    key: bytes | None = None,
    key_type: str | None = None,
    include_keys: bool = False,
) -> dict:
    """Read ``count`` blocks from ``block``, or the whole tag when ``block`` is None."""
    card = connect(t)
    acc = _access(t, card, keys, key, key_type)
    if block is None:
        d, failed = _read_all(t, card, acc)
        return {**_out_dump(d, include_keys), "unreadable_sectors": failed}
    if count < 1:
        raise InvalidInput("count must be at least 1")
    blocks = {}
    for b in range(block, block + count):
        raw = acc.effective(b, acc.read(b)).hex()
        blocks[str(b)] = raw if include_keys else acc.mask(b, raw)
    return {"uid": card.uid_hex, "family": card.family, "blocks": blocks}


def dump(
    t: Transport,
    *,
    output: str | os.PathLike | None = None,
    fmt: str = "json",
    resume_from: str | os.PathLike | None = None,
    keys: Iterable[bytes] = (),
    key: bytes | None = None,
    key_type: str | None = None,
    include_keys: bool = False,
    retry_timeout: float = 30.0,
    sleep: Sleep = time.sleep,
    clock: Clock = time.monotonic,
) -> dict:
    """Dump the whole tag to a 0600 file (keys included in the file, masked in the result).

    Progress is saved after every sector, so a dump interrupted beyond ``retry_timeout`` can be
    continued with ``resume_from`` (the partial file).
    """
    if fmt not in ("json", "bin"):
        raise InvalidInput(f"unknown dump format {fmt!r}", "use json or bin")
    card = connect(t)
    acc = _access(t, card, keys, key, key_type)
    existing = None
    if resume_from:
        existing = _load_dump(resume_from, fmt)
        if existing.uid and card.uid_hex and existing.uid != card.uid_hex:
            raise NfcError(
                f"partial dump is for tag {existing.uid}, but tag {card.uid_hex} is present",
                "put the original tag on the reader or start a fresh dump",
            )
    path = Path(output or resume_from or dumps_dir() / f"{card.uid_hex or 'tag'}-{_stamp()}.json")
    partial_fmt = fmt if output else "json"

    def save(d: dumpfile.Dump) -> None:
        try:
            d.save(path, fmt=partial_fmt, include_keys=True)
        except OSError as exc:
            raise NfcError(f"could not write dump {path}: {exc}", "choose a writable --output")

    d, failed = _read_all(
        t,
        card,
        acc,
        existing=existing,
        retry_timeout=retry_timeout,
        sleep=sleep,
        clock=clock,
        on_progress=save,
    )
    save(d)
    return {
        "path": str(path),
        "format": partial_fmt,
        "blocks_read": len(d.blocks),
        "unreadable_sectors": failed,
        "complete": not failed,
        "dump": _out_dump(d, include_keys),
    }


def _load_dump(path: str | os.PathLike, fmt: str = "json") -> dumpfile.Dump:
    try:
        return dumpfile.Dump.load(path, fmt=fmt)
    except FileNotFoundError:
        raise InvalidInput(f"dump file not found: {path}") from None
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        raise InvalidInput(f"cannot read dump {path}: {exc}") from None


# ===================================================================== write engine


def _diff_entry(acc: _Access, block: int, old: bytes | None, new: bytes, action: str, **kw):
    return {
        "block": block,
        "old": None if old is None else old.hex(),
        "new": new.hex(),
        "action": action,
        **kw,
    }


def _plan(acc: _Access, changes: dict[int, bytes], include_trailers: bool) -> list[dict]:
    entries = []
    for block in sorted(changes):
        new = changes[block]
        if len(new) != acc.block_size:
            raise InvalidInput(
                f"block {block} needs {acc.block_size} bytes, got {len(new)}",
            )
        reason = acc.protected(block)
        if reason and not include_trailers:
            entries.append(_diff_entry(acc, block, None, new, "skip", reason=reason))
            continue
        try:
            old = acc.effective(block, acc.read(block))
        except (AuthFailed, ApduError) as exc:
            entries.append(_diff_entry(acc, block, None, new, "write", error=str(exc)))
            continue
        action = "unchanged" if acc.same(block, old, new) else "write"
        entries.append(_diff_entry(acc, block, old, new, action))
    return entries


def _masked(acc: _Access, entries: list[dict], include_keys: bool) -> list[dict]:
    if include_keys:
        return entries
    out = []
    for e in entries:
        e = dict(e)
        for k in ("old", "new"):
            if e.get(k):
                e[k] = acc.mask(e["block"], e[k])
        out.append(e)
    return out


def _snapshot(t: Transport, card: Card, acc: _Access, path: str | os.PathLike | None) -> Path:
    try:
        d, failed = _read_all(t, card, acc, retry_timeout=0)
        if failed:
            # An incomplete snapshot cannot undo the write, so never proceed with one.
            raise SnapshotFailed(
                f"could not read the whole tag for a pre-write snapshot "
                f"(unreadable sector(s) {failed}); nothing was written",
                "supply the missing keys (--key / --keys-file) so the whole tag can be read",
            )
        target = Path(path) if path else dumpfile.snapshot_path(card.uid_hex, _stamp())
        d.save(target, include_keys=True)
    except SnapshotFailed:
        raise
    except (OSError, ValueError, NfcError) as exc:
        raise SnapshotFailed(f"could not save the pre-write snapshot ({exc}); nothing was written")
    return target


def _new_journal(card: Card, op: str, include_trailers: bool, snapshot: Path) -> Journal:
    path = journal_dir() / f"{card.uid_hex or 'tag'}-{_stamp()}.journal"
    meta = {
        "uid": card.uid_hex,
        "family": card.family,
        "op": op,
        "include_trailers": include_trailers,
        "snapshot": str(snapshot),
        "created": datetime.now(timezone.utc).isoformat(),
    }
    try:
        _write_private(_meta_path(path), json.dumps(meta, indent=2).encode())
        _write_private(path, b"")
    except OSError as exc:
        raise SnapshotFailed(f"could not create the write journal {path}: {exc}") from None
    return Journal(path)


def _meta_path(journal: Path) -> Path:
    return journal.with_name(journal.name + ".meta.json")


def _execute(
    t: Transport,
    card: Card,
    acc: _Access,
    entries: list[dict],
    journal: Journal,
) -> tuple[int, list[int]]:
    """Write every ``write`` entry with read-back verification; returns (verified, protected)."""
    todo = [e for e in entries if e["action"] == "write"]
    if not todo:
        return 0, []
    pending_old = {e["block"]: e for e in todo}
    for e in todo:  # intent first: every planned block is pending until verified
        journal.append(e["block"], e["old"] or "", e["new"], False, False)
    verified, protected = 0, []
    for e in todo:
        block, new = e["block"], bytes.fromhex(e["new"])
        try:
            acc.write(block, new)
            journal.append(block, pending_old[block]["old"] or "", e["new"], True, False)
            ok = acc.verify(block, new)
        except (CardRemoved, NoCard) as exc:
            raise WriteInterrupted(str(journal.path), journal.pending(), verified, str(exc))
        journal.append(block, pending_old[block]["old"] or "", e["new"], True, ok)
        if not ok:
            raise VerifyFailed(
                f"block {block}: read-back did not match after write; journal {journal.path}",
                f"run `nfc-cli tag restore --resume {journal.path} --apply` to retry",
            )
        e["verified"] = True
        verified += 1
        if acc.is_trailer(block):
            protected.append(block)
    return verified, protected


def _mutate(
    t: Transport,
    card: Card,
    acc: _Access,
    changes: dict[int, bytes],
    *,
    op: str,
    apply: bool,
    include_trailers: bool,
    include_keys: bool,
    snapshot_path: str | os.PathLike | None = None,
    journal: Journal | None = None,
    extra: dict | None = None,
    expect_uid: str | None = None,
) -> dict:
    _require_uid(card, expect_uid)
    entries = _plan(acc, changes, include_trailers)
    result: dict[str, Any] = {
        "uid": card.uid_hex,
        "family": card.family,
        "op": op,
        "applied": False,
        **(extra or {}),
    }
    summary = {"write": 0, "unchanged": 0, "skip": 0}
    for e in entries:
        summary[e["action"]] += 1
    snap = None
    verified, protected = 0, []
    if apply and summary["write"]:
        unreadable = [e["block"] for e in entries if e.get("error")]
        if unreadable:
            raise AuthFailed(
                f"cannot read blocks {unreadable} before writing; nothing was written",
                "supply the sector keys with --key or --keys-file",
            )
        snap = _snapshot(t, card, acc, snapshot_path)
        if journal is None:
            journal = _new_journal(card, op, include_trailers, snap)
        verified, protected = _execute(t, card, acc, entries, journal)
        result["applied"] = True
    elif apply:
        result["applied"] = True  # nothing to change
    result.update(
        diff=_masked(acc, entries, include_keys),
        summary=summary,
        snapshot=str(snap) if snap else None,
        journal=str(journal.path) if journal and apply and summary["write"] else None,
        verified=verified,
        protected_blocks_written=protected,
    )
    return result


def _key_args(kw: dict) -> dict:
    return {k: kw.get(k) for k in ("keys", "key", "key_type") if kw.get(k) is not None}


def write(
    t: Transport,
    block: int,
    data: str | bytes,
    *,
    apply: bool = False,
    include_trailers: bool = False,
    include_keys: bool = False,
    snapshot_path: str | os.PathLike | None = None,
    **keyargs,
) -> dict:
    """Write ``data`` (one or more whole blocks) starting at ``block``."""
    payload = _hex(data)
    card = connect(t)
    acc = _access(t, card, **_key_args(keyargs))
    size = acc.block_size
    if not payload or len(payload) % size:
        raise InvalidInput(f"data must be a non-empty multiple of {size} bytes for {card.family}")
    changes = {block + i: payload[i * size : (i + 1) * size] for i in range(len(payload) // size)}
    refused = [b for b in changes if acc.protected(b)]
    if refused and not include_trailers:
        raise InvalidInput(
            f"refusing to write protected block(s) {refused} ({acc.protected(refused[0])})",
            "pass include_trailers / --include-trailers only if you really mean it",
        )
    return _mutate(
        t,
        card,
        acc,
        changes,
        op="write",
        apply=apply,
        include_trailers=include_trailers,
        include_keys=include_keys,
        snapshot_path=snapshot_path,
        expect_uid=keyargs.get("expect_uid"),
    )


def restore(
    t: Transport,
    source: str | os.PathLike | dumpfile.Dump,
    *,
    fmt: str = "json",
    apply: bool = False,
    include_trailers: bool = False,
    include_keys: bool = False,
    snapshot_path: str | os.PathLike | None = None,
    **keyargs,
) -> dict:
    """Write a dump back; trailers and block 0 are skipped unless ``include_trailers``."""
    d = source if isinstance(source, dumpfile.Dump) else _load_dump(source, fmt)
    card = connect(t)
    acc = _access(t, card, **_key_args(keyargs))
    changes: dict[int, bytes] = {}
    masked: list[int] = []
    for b, h in d.blocks.items():
        if dumpfile.KEY_MASK in h:
            masked.append(b)
            continue
        changes[b] = _hex(h, f"block {b}")
    extra: dict[str, Any] = {"source_uid": d.uid}
    if d.uid and card.uid_hex and d.uid != card.uid_hex:
        extra["warning"] = f"dump is from tag {d.uid}, restoring onto {card.uid_hex}"
    res = _mutate(
        t,
        card,
        acc,
        changes,
        op="restore",
        apply=apply,
        include_trailers=include_trailers,
        include_keys=include_keys,
        snapshot_path=snapshot_path,
        extra=extra,
        expect_uid=keyargs.get("expect_uid"),
    )
    for b in sorted(masked):
        res["diff"].append(
            {"block": b, "old": None, "new": None, "action": "skip", "reason": "keys masked"}
        )
        res["summary"]["skip"] += 1
    return res


def resume(
    t: Transport,
    journal_path: str | os.PathLike,
    *,
    apply: bool = False,
    include_trailers: bool = False,
    include_keys: bool = False,
    snapshot_path: str | os.PathLike | None = None,
    **keyargs,
) -> dict:
    """Finish an interrupted write from its journal (pending blocks only)."""
    path = Path(journal_path)
    try:
        meta = json.loads(_meta_path(path).read_text())
        journal = Journal(path)
    except FileNotFoundError:
        raise InvalidInput(f"journal not found: {path} (or its .meta.json)") from None
    except (OSError, ValueError) as exc:
        raise InvalidInput(f"cannot read journal {path}: {exc}") from None
    card = connect(t)
    if meta.get("uid") and card.uid_hex and meta["uid"] != card.uid_hex:
        raise NfcError(
            f"journal is for tag {meta['uid']}, but tag {card.uid_hex} is present",
            "put the original tag on the reader",
        )
    latest = journal.latest()
    changes = {b: bytes.fromhex(latest[b]["new"]) for b in journal.pending()}
    acc = _access(t, card, **_key_args(keyargs))
    protected_pending = sorted(b for b in changes if acc.protected(b))
    if meta.get("include_trailers") and protected_pending and not include_trailers:
        # The interrupted write was allowed to touch trailers/block 0; never finish it
        # half-way by silently skipping them, and never write them without the flag again.
        raise InvalidInput(
            f"this journal still has trailer/block-0 writes pending {protected_pending}",
            f"rerun with --include-trailers to finish them: nfc-cli tag restore {path} "
            "--resume --include-trailers --apply",
        )
    return _mutate(
        t,
        card,
        acc,
        changes,
        op="resume",
        apply=apply,
        include_trailers=include_trailers,
        include_keys=include_keys,
        snapshot_path=snapshot_path,
        journal=journal if apply else None,
        extra={"resumed": str(path), "pending_before": sorted(changes)},
        expect_uid=keyargs.get("expect_uid"),
    )


# ===================================================================== NDEF


def _records_out(raw: bytes | None) -> list[dict]:
    if not raw:
        return []
    try:
        recs = ndef.decode_message(raw)
    except ndef.NdefError as exc:
        raise NfcError(f"malformed NDEF message on the tag: {exc}", "rewrite it with ndef write")
    out = []
    for r in recs:
        item: dict[str, Any] = {
            "tnf": r.tnf,
            "type": r.type.decode("ascii", "replace"),
            "id": r.id.hex(),
            "payload": r.payload.hex(),
        }
        try:
            if r.tnf == ndef.TNF_WELL_KNOWN and r.type == b"T":
                item["lang"], item["text"] = ndef.record_text(r)
            elif r.tnf == ndef.TNF_WELL_KNOWN and r.type == b"U":
                item["uri"] = ndef.record_uri(r)
        except ndef.NdefError:
            pass
        out.append(item)
    return out


def _classic_ndef_blocks(acc: _ClassicAccess) -> list[int]:
    """Data blocks of the MAD-declared NDEF sectors (sectors 1..15), in order."""
    mad = acc.read(1) + acc.read(2)
    if mad[1] != ndef.MAD_INFO_BYTE or ndef.mad_crc(mad[1:32]) != mad[0]:
        raise NotNdefFormatted("no valid MAD in sector 0: the tag is not NDEF formatted")
    sectors = [
        s
        for s in range(1, min(acc.sectors, 16))
        if mad[2 * s : 2 * s + 2] == ndef.NDEF_AID  # AID of sector s at MAD bytes 2s..2s+1
    ]
    if not sectors:
        raise NotNdefFormatted("the MAD declares no NDEF sectors")
    return [b for s in sectors for b in acc.sector_blocks(s)[:-1]]


def _ndef_ops(card: Card):
    ops = families.family_ops(card.family)
    if not hasattr(ops, "read_ndef") or isinstance(ops, families.ReadOnlyOps):
        raise families.UnsupportedOperation(f"{card.family}: NDEF is not supported")
    return ops


def ndef_read(t: Transport, **keyargs) -> dict:
    card = connect(t)
    try:
        if card.is_classic:
            acc = _ClassicAccess(t, card.family, **_classic_keys(keyargs))
            raw_area = b"".join(acc.read(b) for b in _classic_ndef_blocks(acc))
            msg = ndef.tlv_unwrap(raw_area)
        else:
            msg = _ndef_ops(card).read_ndef(t)
    except ndef.NdefError as exc:
        raise NotNdefFormatted(f"cannot parse the NDEF area: {exc}") from None
    return {
        "uid": card.uid_hex,
        "family": card.family,
        "empty": not msg,
        "raw": (msg or b"").hex(),
        "records": _records_out(msg),
    }


def _classic_keys(kw: dict) -> dict:
    out = {}
    if kw.get("keys") is not None:
        out["extra_keys"] = kw["keys"]
    if kw.get("key") is not None:
        out["key"], out["key_type"] = kw["key"], kw.get("key_type")
    return out


def build_message(
    *,
    text: str | None = None,
    lang: str = "en",
    uri: str | None = None,
    mime: str | None = None,
    payload: str | bytes | None = None,
    raw: bytes | None = None,
) -> bytes:
    """Encode exactly one of text / uri / mime(+payload) / raw message bytes."""
    given = [x for x in (text, uri, mime, raw) if x is not None]
    if len(given) != 1:
        raise InvalidInput(
            "give exactly one of text, uri, mime or raw_file", "e.g. --uri https://example.org"
        )
    try:
        if raw is not None:
            ndef.decode_message(raw)  # validate before writing
            return bytes(raw)
        if text is not None:
            rec = ndef.text_record(text, lang or "en")
        elif uri is not None:
            rec = ndef.uri_record(uri)
        else:
            data = payload.encode() if isinstance(payload, str) else bytes(payload or b"")
            rec = ndef.mime_record(str(mime), data)
        return ndef.encode_message([rec])
    except (ndef.NdefError, UnicodeError) as exc:
        raise InvalidInput(f"cannot build the NDEF message: {exc}") from None


def _area_changes(blocks: list[int], size: int, payload: bytes, clear_rest: bool) -> dict:
    payload += b"\x00" * (-len(payload) % size)
    if len(payload) > len(blocks) * size:
        raise InvalidInput(
            f"NDEF data needs {len(payload)} bytes but the tag holds {len(blocks) * size}"
        )
    changes = {}
    for i, b in enumerate(blocks):
        chunk = payload[i * size : (i + 1) * size]
        if chunk:
            changes[b] = chunk
        elif clear_rest:
            changes[b] = bytes(size)
        else:
            break
    return changes


def _ndef_area(t: Transport, card: Card, keyargs: dict) -> tuple[_Access, list[int]]:
    if card.is_classic:
        acc = _ClassicAccess(t, card.family, **_classic_keys(keyargs))
        return acc, _classic_ndef_blocks(acc)
    ops = _ndef_ops(card)
    if isinstance(ops, families.Type2Ops):
        acc2 = _Type2Access(t)
        try:
            cc = ops.capability(t)
        except ndef.NdefError as exc:
            raise NotNdefFormatted(str(exc)) from None
        if cc["read_only"]:
            raise families.UnsupportedOperation("tag is read-only (capability container)")
        return acc2, list(range(4, 4 + cc["size"] // 4))
    raise families.UnsupportedOperation(f"{card.family}: NDEF writes are block-less here")


def _ndef_mutate(t, card, tlv: bytes, *, op, apply, clear_rest, include_keys, snapshot_path, kw):
    expect_uid = kw.get("expect_uid")
    if isinstance(families.family_ops(card.family), families.Type4Ops):
        return _type4_mutate(
            t, card, tlv, op=op, apply=apply, snapshot_path=snapshot_path, expect_uid=expect_uid
        )
    acc, blocks = _ndef_area(t, card, kw)
    changes = _area_changes(blocks, acc.block_size, tlv, clear_rest)
    return _mutate(
        t,
        card,
        acc,
        changes,
        op=op,
        apply=apply,
        include_trailers=False,
        include_keys=include_keys,
        expect_uid=expect_uid,
        snapshot_path=snapshot_path,
    )


def ndef_write(
    t: Transport,
    message: bytes,
    *,
    apply: bool = False,
    include_keys: bool = False,
    snapshot_path: str | os.PathLike | None = None,
    **keyargs,
) -> dict:
    """Write an encoded NDEF message (see :func:`build_message`)."""
    card = connect(t)
    try:
        tlv = ndef.tlv_wrap(message)
    except ndef.NdefError as exc:
        raise InvalidInput(str(exc)) from None
    res = _ndef_mutate(
        t,
        card,
        message if _is_type4(card) else tlv,
        op="ndef_write",
        apply=apply,
        clear_rest=False,
        include_keys=include_keys,
        snapshot_path=snapshot_path,
        kw=keyargs,
    )
    res["records"] = _records_out(message)
    return res


def ndef_erase(
    t: Transport,
    *,
    apply: bool = False,
    include_keys: bool = False,
    snapshot_path: str | os.PathLike | None = None,
    **keyargs,
) -> dict:
    """Replace the NDEF message with an empty one and zero the rest of the NDEF area."""
    card = connect(t)
    return _ndef_mutate(
        t,
        card,
        b"" if _is_type4(card) else b"\x03\x00\xfe",
        op="ndef_erase",
        apply=apply,
        clear_rest=True,
        include_keys=include_keys,
        snapshot_path=snapshot_path,
        kw=keyargs,
    )


def _is_type4(card: Card) -> bool:
    return isinstance(families.family_ops(card.family), families.Type4Ops)


def _type4_mutate(
    t, card: Card, message: bytes, *, op, apply, snapshot_path, expect_uid=None
) -> dict:
    """Type 4 has no block map: the NDEF file is block 0 of the diff/snapshot/journal."""
    _require_uid(card, expect_uid)
    ops = families.Type4Ops()
    old = ops.read_ndef(t) or b""
    action = "unchanged" if old == message else "write"
    entry = {"block": 0, "old": old.hex(), "new": message.hex(), "action": action}
    res: dict[str, Any] = {
        "uid": card.uid_hex,
        "family": card.family,
        "op": op,
        "applied": bool(apply),
        "diff": [entry],
        "summary": {
            "write": int(action == "write"),
            "unchanged": int(action != "write"),
            "skip": 0,
        },
        "snapshot": None,
        "journal": None,
        "verified": 0,
        "protected_blocks_written": [],
    }
    if not apply or action != "write":
        return res
    d = dumpfile.Dump(card.atr.hex(), card.uid_hex, card.family, {0: old.hex()})
    target = (
        Path(snapshot_path) if snapshot_path else dumpfile.snapshot_path(card.uid_hex, _stamp())
    )
    try:
        d.save(target, include_keys=True)
    except (OSError, ValueError) as exc:
        raise SnapshotFailed(f"could not save the pre-write snapshot ({exc}); nothing was written")
    journal = _new_journal(card, op, False, target)
    journal.append(0, old.hex(), message.hex(), False, False)
    try:
        if message:
            ops.write_ndef(t, message)
        else:
            ops.open_ndef(t)
            _, sw = t.transmit(bytes.fromhex("00D6000002") + b"\x00\x00")
            if sw != families.OK:
                raise ApduError(sw, "clearing NLEN failed")
        journal.append(0, old.hex(), message.hex(), True, False)
        ok = (ops.read_ndef(t) or b"") == message
    except (CardRemoved, NoCard) as exc:
        raise WriteInterrupted(str(journal.path), journal.pending(), 0, str(exc))
    journal.append(0, old.hex(), message.hex(), True, ok)
    if not ok:
        raise VerifyFailed(f"NDEF read-back did not match; journal {journal.path}")
    res.update(snapshot=str(target), journal=str(journal.path), verified=1)
    return res


def ndef_format(
    t: Transport,
    *,
    apply: bool = False,
    include_keys: bool = False,
    snapshot_path: str | os.PathLike | None = None,
    **keyargs,
) -> dict:
    """NDEF-format a MIFARE Classic tag from the fixed NFC Forum template (MAD + NDEF keys).

    The only operation that writes sector trailers by itself: every trailer of the template is
    access-bit validated before anything is read or written; block 0 is never touched.
    """
    card = connect(t)
    if card.family not in ("classic_1k", "classic_mini"):
        raise families.UnsupportedOperation(
            f"{card.family}: ndef format supports MIFARE Classic 1K/Mini only",
            "Type 2 and Type 4 tags ship NDEF formatted; use ndef write",
        )
    template = ndef.classic_format_template(CLASSIC_SECTORS[card.family])
    bad = [
        b for b, v in template.items() if classic.is_trailer(b) and not ndef.validate_access_bits(v)
    ]
    if bad or 0 in template:
        raise classic.ClassicError(f"format template failed validation at blocks {bad or [0]}")
    acc = _ClassicAccess(t, card.family, **_classic_keys(keyargs))
    return _mutate(
        t,
        card,
        acc,
        dict(template),
        op="ndef_format",
        apply=apply,
        include_trailers=True,
        include_keys=include_keys,
        snapshot_path=snapshot_path,
        expect_uid=keyargs.get("expect_uid"),
    )


# ===================================================================== raw APDU


def raw_apdu(
    t: Transport,
    apdus: list[str | bytes],
    *,
    apply: bool = False,
    include_trailers: bool = False,
) -> dict:
    """Send raw APDUs (dry-run lists them). FF D6 to block 0 / a Classic trailer is refused
    without ``include_trailers``; escape (reader) commands are never reachable from here."""
    cmds = [_hex(a, "apdu") for a in apdus]
    if not cmds or any(len(c) < 4 for c in cmds):
        raise InvalidInput("give at least one APDU of 4+ bytes")
    card = connect(t) if apply else None
    if not include_trailers:
        for c in cmds:
            if c[0] == 0xFF and c[1] == 0xD6 and (card is None or card.is_classic):
                if c[3] == 0 or classic.is_trailer(c[3]):
                    raise InvalidInput(
                        f"refusing raw write to block {c[3]} (block 0 / sector trailer)",
                        "pass include_trailers / --include-trailers only if you really mean it",
                    )
    if not apply:
        return {"applied": False, "apdus": [c.hex() for c in cmds], "responses": []}
    out = []
    for c in cmds:
        data, sw = t.transmit(c)
        out.append({"apdu": c.hex(), "data": bytes(data).hex(), "sw": bytes(sw).hex()})
    return {"applied": True, "uid": card.uid_hex, "family": card.family, "responses": out}
