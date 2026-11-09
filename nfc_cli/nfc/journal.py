"""Append-only write journal (JSON lines, fsync per record). Data-only module."""

from __future__ import annotations

import json
import os
from pathlib import Path


class Journal:
    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self.records: list[dict] = []
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    self.records.append(json.loads(line))
                except json.JSONDecodeError:
                    break  # torn final line from a crash: ignore it

    def append(
        self, block: int, old: str, new: str, written: bool = False, verified: bool = False
    ) -> dict:
        rec = {"block": block, "old": old, "new": new, "written": written, "verified": verified}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (json.dumps(rec) + "\n").encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        self.records.append(rec)
        return rec

    def latest(self) -> dict[int, dict]:
        """Last record per block."""
        return {r["block"]: r for r in self.records}

    def pending(self) -> list[int]:
        """Blocks whose latest record is not both written and verified, in order."""
        return sorted(b for b, r in self.latest().items() if not (r["written"] and r["verified"]))
