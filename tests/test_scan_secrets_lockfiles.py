"""scan-secrets.py skips npm's package-lock.json like any other lockfile (t15).

web/package-lock.json carries npm ``funding`` URLs (opencollective, GitHub
sponsors) — vendored registry metadata, not configured endpoints.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "scan_secrets_lockfiles", REPO_ROOT / "scripts" / "scan-secrets.py"
)
assert _spec is not None and _spec.loader is not None
scan_secrets = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = scan_secrets
_spec.loader.exec_module(scan_secrets)


def test_package_lock_is_excluded() -> None:
    assert "web/package-lock.json".endswith(scan_secrets.EXCLUDE_SUFFIXES)
    assert scan_secrets.scan_paths(["web/package-lock.json"]) == []


def test_other_json_is_still_scanned() -> None:
    assert not "web/package.json".endswith(scan_secrets.EXCLUDE_SUFFIXES)
