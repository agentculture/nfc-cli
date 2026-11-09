"""Tests for scripts/check-culture-design.py (plan task t15).

Acceptance criterion: tokens.css is a provenance header plus a verbatim copy of
agentculture/org ``site-astro/src/styles/global.css`` at the pinned commit, and
``scripts/check-culture-design.py`` verifies the recorded sha256 and fails on a
one-byte edit. Shape cited from agentculture/embodiment's
``tests/test_culture_design_check.py`` (cite-don't-import), adapted to the
offline sha256 check this repo uses (no org checkout needed).
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check-culture-design.py"
REAL_DIR = REPO_ROOT / "web" / "src" / "culture-design"
PIN = "b4d939ba0aa354a5ae53065319a773e0013de698"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_culture_design", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def script() -> ModuleType:
    return _load_script()


@pytest.fixture
def copy_dir(tmp_path: Path) -> Path:
    dest = tmp_path / "culture-design"
    shutil.copytree(REAL_DIR, dest)
    return dest


def _failed(results: list[tuple[str, bool, str]]) -> list[str]:
    return [label for label, passed, _ in results if not passed]


def test_committed_layer_passes(script: ModuleType) -> None:
    results = script.run_checks(REAL_DIR)
    assert results, "no checks ran"
    assert _failed(results) == []


def test_readme_and_header_record_the_embodiment_pin(script: ModuleType) -> None:
    readme = (REAL_DIR / "README.md").read_text(encoding="utf-8")
    tokens = (REAL_DIR / "tokens.css").read_text(encoding="utf-8")
    assert script.read_pin_from_readme(readme) == PIN
    assert script.read_pin_from_tokens_header(tokens) == PIN
    assert len(script.read_sha256_from_readme(readme)) == 64


def test_one_byte_edit_in_body_fails(script: ModuleType, copy_dir: Path) -> None:
    tokens_path = copy_dir / "tokens.css"
    text = tokens_path.read_text(encoding="utf-8")
    _, body = script.split_tokens_css(text)
    # flip one byte deep inside the verbatim body
    idx = len(text) - len(body) + body.index("#f4f5fb") + 1
    edited = text[:idx] + ("e" if text[idx] != "e" else "d") + text[idx + 1 :]
    assert len(edited) == len(text)
    tokens_path.write_text(edited, encoding="utf-8")
    failed = _failed(script.run_checks(copy_dir))
    assert any("sha256" in label for label in failed)


def test_appended_byte_fails(script: ModuleType, copy_dir: Path) -> None:
    tokens_path = copy_dir / "tokens.css"
    tokens_path.write_text(tokens_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    assert _failed(script.run_checks(copy_dir))


def test_header_pin_mismatch_fails(script: ModuleType, copy_dir: Path) -> None:
    tokens_path = copy_dir / "tokens.css"
    text = tokens_path.read_text(encoding="utf-8").replace(PIN, "0" * 40, 1)
    tokens_path.write_text(text, encoding="utf-8")
    failed = _failed(script.run_checks(copy_dir))
    assert any("pin" in label for label in failed)


def test_header_edit_does_not_affect_body_hash(script: ModuleType, copy_dir: Path) -> None:
    tokens_path = copy_dir / "tokens.css"
    text = tokens_path.read_text(encoding="utf-8")
    tokens_path.write_text(text.replace(" * Re-pin:", " * Re-pin (see):", 1), encoding="utf-8")
    assert _failed(script.run_checks(copy_dir)) == []


def test_missing_files_fail_cleanly(script: ModuleType, tmp_path: Path) -> None:
    assert _failed(script.run_checks(tmp_path))


def test_missing_sentinel_raises(script: ModuleType) -> None:
    with pytest.raises(script.CheckFailure):
        script.split_tokens_css(":root { --bg: #fff; }\n")


def test_main_exit_codes(script: ModuleType, copy_dir: Path, capsys) -> None:
    assert script.main(["--dir", str(REAL_DIR)]) == 0
    assert "PASS" in capsys.readouterr().out
    (copy_dir / "tokens.css").write_text("broken", encoding="utf-8")
    assert script.main(["--dir", str(copy_dir)]) == 1
