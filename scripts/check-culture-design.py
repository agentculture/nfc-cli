#!/usr/bin/env python3
"""check-culture-design.py — verify web/src/culture-design/ against its pin.

Cited (cite-don't-import) from agentculture/embodiment
``scripts/check-culture-design.py`` (embodiment plan task t17, itself a port of
culture-nodes' ``scripts/check-culture-design.mjs``), adapted for nfc-cli plan
task t15: the agentculture/org repo is not checked out next to this one, so the
mandatory check is **offline** — the sha256 of ``tokens.css``'s verbatim body
must equal the digest recorded in ``web/src/culture-design/README.md``. That
digest was taken from org's ``site-astro/src/styles/global.css`` at the pinned
commit (verified against GitHub on 2026-11-08, see the README).

Checks (all fatal):

1. README.md records ``pin: <40-hex sha>`` and ``sha256: <64-hex digest>``.
2. tokens.css's header ``Pinned commit:`` line matches the README pin.
3. sha256 of tokens.css's body (everything after the header comment's own
   closing ``*/`` line) equals the recorded digest — a one-byte edit fails.
4. Optional: when ``CULTURE_DESIGN_ORG_REPO`` points at an org checkout, the
   body is also byte-compared with ``git show <pin>:<path>`` there.

Run with::

    uv run python scripts/check-culture-design.py [--dir web/src/culture-design]

Exit code 0 on success, 1 on any failure. Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess  # nosec B404 - fixed argv, no shell, optional pinned-source compare
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = REPO_ROOT / "web" / "src" / "culture-design"

_ORG_REPO_ENV = "CULTURE_DESIGN_ORG_REPO"
_ORG_TOKENS_PATH = "site-astro/src/styles/global.css"

_SENTINEL = "---- verbatim copy of site-astro/src/styles/global.css follows ----"
_PIN_RE = re.compile(r"^pin:\s+([0-9a-f]{40})\s*$", re.MULTILINE)
_SHA_RE = re.compile(r"^sha256:\s+([0-9a-f]{64})\s*$", re.MULTILINE)
_HEADER_PIN_RE = re.compile(r"^\s*\*\s*Pinned commit:\s*([0-9a-f]{40})\s*$", re.MULTILINE)

Result = tuple[str, bool, str]


class CheckFailure(Exception):
    """Raised for any check failure; the message is the human-readable reason."""


def read_pin_from_readme(readme_text: str) -> str:
    match = _PIN_RE.search(readme_text)
    if not match:
        raise CheckFailure("README.md has no 'pin: <40-hex-char-sha>' line")
    return match.group(1)


def read_sha256_from_readme(readme_text: str) -> str:
    match = _SHA_RE.search(readme_text)
    if not match:
        raise CheckFailure("README.md has no 'sha256: <64-hex-char-digest>' line")
    return match.group(1)


def read_pin_from_tokens_header(tokens_css: str) -> str:
    match = _HEADER_PIN_RE.search(tokens_css)
    if not match:
        raise CheckFailure("tokens.css header comment is missing a 'Pinned commit: <sha>' line")
    return match.group(1)


def split_tokens_css(tokens_css: str) -> tuple[str, str]:
    """Split tokens.css into (header, verbatim_body) at the sentinel's ``*/`` line."""
    sentinel_idx = tokens_css.find(_SENTINEL)
    if sentinel_idx < 0:
        raise CheckFailure(f'tokens.css is missing the sentinel comment ("{_SENTINEL}")')
    close_idx = tokens_css.find("*/", sentinel_idx)
    if close_idx < 0:
        raise CheckFailure("tokens.css header comment is never closed with */")
    after_close = tokens_css.find("\n", close_idx)
    if after_close < 0:
        raise CheckFailure("tokens.css has no content after its header comment")
    return tokens_css[: after_close + 1], tokens_css[after_close + 1 :]


def read_org_file_at_pin(rel_path: str, pin: str, org_repo: str) -> str:
    try:
        result = subprocess.run(  # nosec B603 B607 - fixed argv, no shell, git on PATH
            ["git", "-C", org_repo, "show", f"{pin}:{rel_path}"],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise CheckFailure(f"git is not on PATH: {exc}") from exc
    except subprocess.CalledProcessError as exc:
        raise CheckFailure(f"git show {pin}:{rel_path} in {org_repo} failed: {exc.stderr}") from exc
    return result.stdout


def run_checks(design_dir: Path = DEFAULT_DIR) -> list[Result]:
    """Run every check; expected failure modes become failed results, never raise."""
    readme_path = design_dir / "README.md"
    tokens_path = design_dir / "tokens.css"
    if not readme_path.is_file():
        return [("README.md exists", False, f"missing: {readme_path}")]
    if not tokens_path.is_file():
        return [("tokens.css exists", False, f"missing: {tokens_path}")]

    readme = readme_path.read_text(encoding="utf-8")
    # newline="" keeps bytes exact: no CRLF translation may mask an edit.
    with tokens_path.open(encoding="utf-8", newline="") as fh:
        tokens = fh.read()

    try:
        pin = read_pin_from_readme(readme)
        expected_sha = read_sha256_from_readme(readme)
    except CheckFailure as exc:
        return [("README.md records pin and sha256", False, str(exc))]
    results: list[Result] = [("README.md records pin and sha256", True, pin[:12])]

    try:
        header_pin = read_pin_from_tokens_header(tokens)
        if header_pin != pin:
            raise CheckFailure(f"tokens.css header says {header_pin}, README says {pin}")
        results.append(("tokens.css header pin matches README pin", True, ""))
    except CheckFailure as exc:
        results.append(("tokens.css header pin matches README pin", False, str(exc)))

    label = f"tokens.css body sha256 matches org {_ORG_TOKENS_PATH}@{pin[:12]}"
    try:
        _, body = split_tokens_css(tokens)
        actual = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if actual != expected_sha:
            raise CheckFailure(f"body sha256 {actual} != recorded {expected_sha}")
        results.append((label, True, ""))
    except CheckFailure as exc:
        results.append((label, False, str(exc)))
        body = None

    org_repo = os.environ.get(_ORG_REPO_ENV)
    if org_repo and body is not None:
        label = f"tokens.css body is byte-identical to {org_repo}@{pin[:12]}"
        try:
            if read_org_file_at_pin(_ORG_TOKENS_PATH, pin, org_repo) != body:
                raise CheckFailure("body differs from the org source at the pin")
            results.append((label, True, ""))
        except CheckFailure as exc:
            results.append((label, False, str(exc)))

    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify culture-design tokens against the pin.")
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR, help="culture-design directory")
    args = parser.parse_args(argv)
    results = run_checks(args.dir)
    failures = 0
    for label, passed, detail in results:
        if passed:
            print(f"ok   - {label}" + (f" ({detail})" if detail else ""))
        else:
            failures += 1
            print(f"FAIL - {label}")
            if detail:
                print(f"       {detail}")
    print(f"{len(results) - failures}/{len(results)} checks passed")
    if failures:
        print(f"culture-design check: FAIL ({failures} failed)")
        return 1
    print("culture-design check: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
