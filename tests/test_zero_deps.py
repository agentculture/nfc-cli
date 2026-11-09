"""The base runtime has no dependencies; usb/mcp are optional extras."""

from __future__ import annotations

import json
import subprocess  # nosec B404 - fixed argv
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def test_base_dependencies_empty() -> None:
    assert _project()["dependencies"] == []


def test_extras_declared() -> None:
    extras = _project()["optional-dependencies"]
    assert extras["usb"] == ["pyusb>=1.2"]
    assert extras["mcp"] == ["mcp>=1.2,<2"]
    assert sorted(extras["all"]) == sorted(extras["usb"] + extras["mcp"])


_BLOCKER = """
import sys
class _Block:
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in ('usb', 'mcp'):
            raise ImportError('blocked: ' + name)
sys.meta_path.insert(0, _Block())
from nfc_cli.cli import main
sys.exit(main(['whoami', '--json']))
"""


def test_whoami_works_with_usb_and_mcp_blocked() -> None:
    result = subprocess.run(  # nosec B603
        [sys.executable, "-c", _BLOCKER],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)
