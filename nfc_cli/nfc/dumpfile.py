"""Tag dump model: JSON / raw-binary serialisation, key masking, snapshot paths.

Data-only module: no transport imports.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

KEY_MASK = "**"
FAMILY_CLASSIC = "classic"
FAMILY_ULTRALIGHT = "ultralight"
FAMILY_ISO15693 = "iso15693"

# Bytes per block/page in a binary dump, by (simplified) family. Type 2 (Ultralight/NTAG)
# and ISO 15693 use 4-byte pages; MIFARE Classic uses 16-byte blocks.
_SMALL_PAGE = ("ultralight", "iso15693")


def _block_size(family: str) -> int:
    return 4 if family in _SMALL_PAGE else BLOCK_SIZE


BLOCK_SIZE = 16


def is_classic_trailer(block: int) -> bool:
    """True if ``block`` is a sector trailer on a MIFARE Classic 1K/4K."""
    if block < 128:
        return block % 4 == 3
    return block % 16 == 15


def mask_trailer(hexstr: str) -> str:
    """Replace key A (bytes 0-5) and key B (bytes 10-15) of a trailer with asterisks."""
    if len(hexstr) != 32:
        raise ValueError("a trailer is 16 bytes (32 hex chars)")
    return KEY_MASK * 6 + hexstr[12:20] + KEY_MASK * 6


@dataclass
class Dump:
    atr: str
    uid: str
    family: str = FAMILY_CLASSIC
    blocks: dict[int, str] = field(default_factory=dict)  # block/page index -> hex
    keys_used: dict[int, tuple[str, str]] = field(default_factory=dict)  # sector -> (A|B, hex)

    def _trailer(self, idx: int) -> bool:
        return self.family == FAMILY_CLASSIC and is_classic_trailer(idx)

    def mask_keys(self) -> "Dump":
        """Return a copy with key bytes replaced by asterisks."""
        blocks = {i: (mask_trailer(h) if self._trailer(i) else h) for i, h in self.blocks.items()}
        keys = {s: (t, KEY_MASK * 6) for s, (t, _) in self.keys_used.items()}
        return Dump(self.atr, self.uid, self.family, blocks, keys)

    def to_dict(self, include_keys: bool = False) -> dict:
        src = self if include_keys else self.mask_keys()
        return {
            "atr": src.atr,
            "uid": src.uid,
            "family": src.family,
            "blocks": {str(i): h for i, h in sorted(src.blocks.items())},
            "keys_used": {str(s): [t, k] for s, (t, k) in sorted(src.keys_used.items())},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Dump":
        return cls(
            atr=data["atr"],
            uid=data["uid"],
            family=data.get("family", FAMILY_CLASSIC),
            blocks={int(i): h for i, h in data.get("blocks", {}).items()},
            keys_used={int(s): (v[0], v[1]) for s, v in data.get("keys_used", {}).items()},
        )

    @classmethod
    def from_legacy_sectors(cls, data: dict) -> "Dump":
        """Load the per-sector layout ({"dump": {"0": {"key": [t, k], "blocks": [..]}}})."""
        blocks: dict[int, str] = {}
        keys: dict[int, tuple[str, str]] = {}
        first = 0
        for sec in sorted(data["dump"], key=int):
            ent = data["dump"][sec]
            keys[int(sec)] = (ent["key"][0], ent["key"][1])
            for off, h in enumerate(ent["blocks"]):
                blocks[first + off] = h
            first += len(ent["blocks"])
        return cls(data["atr"], data["uid"], FAMILY_CLASSIC, blocks, keys)

    def to_json(self, include_keys: bool = False) -> str:
        return json.dumps(self.to_dict(include_keys), indent=2)

    @classmethod
    def from_json(cls, text: str) -> "Dump":
        return cls.from_dict(json.loads(text))

    def to_bytes(self, include_keys: bool = False) -> bytes:
        """Raw concatenation of blocks in index order (masked keys become 0x2A bytes)."""
        src = self if include_keys else self.mask_keys()
        out = b""
        for _, h in sorted(src.blocks.items()):
            out += bytes.fromhex(h.replace(KEY_MASK, "2a"))
        return out

    @classmethod
    def from_bytes(
        cls, raw: bytes, atr: str = "", uid: str = "", family: str = FAMILY_CLASSIC
    ) -> "Dump":
        size = _block_size(family)
        if len(raw) % size:
            raise ValueError(f"binary dump length must be a multiple of {size} for {family}")
        blocks = {i: raw[i * size : i * size + size].hex() for i in range(len(raw) // size)}
        return cls(atr, uid, family, blocks)

    def save(self, path: str | os.PathLike, fmt: str = "json", include_keys: bool = False) -> Path:
        if fmt == "json":
            payload = self.to_json(include_keys).encode()
        elif fmt == "bin":
            payload = self.to_bytes(include_keys)
        else:
            raise ValueError(f"unknown dump format: {fmt}")
        _write_private(Path(path), payload)
        return Path(path)

    @classmethod
    def load(cls, path: str | os.PathLike, fmt: str = "json") -> "Dump":
        raw = Path(path).read_bytes()
        if fmt == "bin":
            return cls.from_bytes(raw)
        data = json.loads(raw)
        return cls.from_legacy_sectors(data) if "dump" in data else cls.from_dict(data)


def _write_private(path: Path, payload: bytes) -> None:
    """Atomically write ``payload`` with mode 0600, keeping any prior copy on failure.

    Writes a sibling temp file, fsyncs it, then ``os.replace``s it into place, so an
    interruption mid-save never truncates or corrupts an existing dump/journal/snapshot.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fd = -1
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if fd != -1:
            os.close(fd)
        try:
            tmp.unlink()
        except OSError:
            pass


def snapshot_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "nfc-cli" / "snapshots"


def snapshot_path(uid: str, stamp: str | None = None) -> Path:
    """Path of a snapshot for ``uid`` under the user data dir (never inside a repo)."""
    safe = "".join(c for c in uid.lower() if c in "0123456789abcdef") or "unknown"
    name = f"{safe}-{stamp}.json" if stamp else f"{safe}.json"
    return snapshot_dir() / name
