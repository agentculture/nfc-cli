import json
import os
import stat
from pathlib import Path

from nfc_cli.nfc.dumpfile import Dump, snapshot_path

FIX = Path(__file__).parent / "fixtures" / "x3_dump.json"


def load():
    return Dump.load(FIX)


def test_fixture_loaded_all_blocks():
    d = load()
    assert len(d.blocks) == 64
    assert len(d.keys_used) == 16
    assert d.uid == "cb30c560"


def test_json_roundtrip_with_keys():
    d = load()
    assert Dump.from_json(d.to_json(include_keys=True)) == d


def test_default_has_no_key_hex():
    d = load()
    text = json.dumps(d.to_dict())
    assert "ffffffffffff" not in text.lower()
    assert "**" in text
    assert d.to_dict(include_keys=True)["keys_used"]["0"] == ["A", "FFFFFFFFFFFF"]


def test_mask_keys_trailers():
    m = load().mask_keys()
    assert m.blocks[3] == "************" + "ff078069" + "************"
    assert m.blocks[0] == load().blocks[0]
    assert m.keys_used[0] == ("A", "************")


def test_bin_roundtrip_and_unmasked():
    d = load()
    raw = d.to_bytes(include_keys=True)
    assert len(raw) == 1024
    assert Dump.from_bytes(raw, d.atr, d.uid).blocks == d.blocks
    assert b"\xff" * 6 not in d.to_bytes()


def test_files_0600_under_permissive_umask(tmp_path):
    old = os.umask(0)
    try:
        for fmt in ("json", "bin"):
            p = load().save(tmp_path / f"d.{fmt}", fmt=fmt, include_keys=True)
            assert stat.S_IMODE(p.stat().st_mode) == 0o600
    finally:
        os.umask(old)


def test_save_load_json(tmp_path):
    d = load()
    p = d.save(tmp_path / "d.json", include_keys=True)
    assert Dump.load(p) == d


def test_snapshot_path_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert snapshot_path("CB30C560").parent == tmp_path / "nfc-cli" / "snapshots"


def test_snapshot_path_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    p = snapshot_path("cb30c560", "20260101")
    assert p == tmp_path / ".local/share/nfc-cli/snapshots/cb30c560-20260101.json"
