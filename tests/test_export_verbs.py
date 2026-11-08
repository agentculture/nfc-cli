"""scripts/export-verbs.py: web/src/api/verbs.json is generated from REGISTRY and kept fresh.

CI's web job has no uv, so verbs.json is committed; this test fails when it is stale.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess  # nosec B404 - runs our own script with the current interpreter
import sys
from pathlib import Path

from nfc_cli import verbs  # noqa: F401 - registers every verb
from nfc_cli.registry import REGISTRY, api_route

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "export-verbs.py"
COMMITTED = ROOT / "web" / "src" / "api" / "verbs.json"


def _load():
    spec = importlib.util.spec_from_file_location("export_verbs", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_export_covers_every_registry_verb_with_schema_and_route():
    doc = _load().export()
    got = {(v["noun"], v["verb"]): v for v in doc["verbs"]}
    assert set(got) == {v.path for v in REGISTRY.verbs()}
    for v in REGISTRY.verbs():
        entry = got[v.path]
        assert entry["route"] == api_route(v)
        assert entry["mutating"] is v.mutating
        assert entry["summary"] == v.summary
        assert entry["schema"] == v.params_schema()
        assert entry["trailer_flag"] == v.extra.get("trailer_flag")
        assert entry["writes_trailers"] is bool(v.extra.get("writes_trailers"))


def test_committed_verbs_json_is_fresh():
    expected = _load().render()
    assert (
        COMMITTED.read_text(encoding="utf-8") == expected
    ), "web/src/api/verbs.json is stale: run `uv run python scripts/export-verbs.py`"


def test_check_flag_exits_zero_when_fresh(tmp_path):
    out = tmp_path / "verbs.json"
    run = [sys.executable, str(SCRIPT), "--output", str(out)]
    assert subprocess.run(run, check=False).returncode == 0  # nosec B603
    assert json.loads(out.read_text())["verbs"]
    assert subprocess.run([*run, "--check"], check=False).returncode == 0  # nosec B603
    out.write_text("{}\n")
    assert subprocess.run([*run, "--check"], check=False).returncode == 1  # nosec B603
