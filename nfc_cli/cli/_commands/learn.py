"""``nfc-cli learn`` — the learnability affordance.

Prints a structured self-teaching prompt. Must satisfy the agent-first rubric:
>=200 chars and mention purpose, command map, exit codes, --json, and explain.
"""

from __future__ import annotations

import argparse

from nfc_cli import __version__
from nfc_cli.cli._output import emit_result

_TEXT = """\
nfc-cli — control NFC reader-writers (ACR1552) via USB.

Purpose
-------
Read, write, dump, restore and NDEF-encode NFC tags through an ACS ACR1552 over
raw USB CCID. The same registry-derived verbs are exposed three ways: this CLI,
an MCP server on stdio (`nfc-cli mcp`), and a local web UI (`nfc-cli ui serve`).
Writes are a dry-run unless --apply is given; sector trailers need
--include-trailers.

Commands
--------
  nfc-cli whoami             Identity from culture.yaml.
  nfc-cli learn              This self-teaching prompt.
  nfc-cli explain <path>...  Markdown docs for any noun/verb path.
  nfc-cli overview           Descriptive snapshot of the agent.
  nfc-cli doctor             Check the agent-identity invariants.
  nfc-cli cli overview       Describe the CLI surface itself.
  nfc-cli reader list|info   Reader operations.
  nfc-cli tag info|uid|wait|read|write|dump|restore|apdu   Tag operations.
  nfc-cli ndef read|write|erase|format                     NDEF operations.
  nfc-cli mcp                MCP server on stdio (pip install 'nfc-cli[mcp]').
  nfc-cli ui serve           Local web UI + /api (localhost; --fake x3 for no hardware).
  nfc-cli <noun> overview    Describe reader / tag / ndef / ui.

Per-verb flags: --json, --trace (APDU trace on stderr), --reader INDEX|BUS:ADDR;
mutating verbs add --apply.

Machine-readable output
-----------------------
Every command supports --json. Errors in JSON mode emit
{"code", "message", "remediation"} to stderr. Stdout and stderr never mix.

Exit-code policy
----------------
  0 success
  1 user-input error (bad flag, bad path, missing arg)
  2 environment / setup error
  3+ reserved

More detail
-----------
  nfc-cli explain nfc-cli
"""


def _registry_commands() -> list[dict[str, object]]:
    import nfc_cli.verbs  # noqa: F401  (registers the verbs)
    from nfc_cli.registry import REGISTRY

    return [{"path": list(v.path), "summary": v.summary} for v in REGISTRY.verbs()]


def _as_json_payload() -> dict[str, object]:
    return {
        "tool": "nfc-cli",
        "version": __version__,
        "purpose": "Control NFC reader-writers (ACR1552) via USB: CLI, MCP and web UI.",
        "commands": [
            {"path": ["whoami"], "summary": "Identity probe from culture.yaml."},
            {"path": ["learn"], "summary": "Self-teaching prompt."},
            {"path": ["explain"], "summary": "Markdown docs by path."},
            {"path": ["overview"], "summary": "Descriptive snapshot of the agent."},
            {"path": ["doctor"], "summary": "Check the agent-identity invariants."},
            {"path": ["cli", "overview"], "summary": "Describe the CLI surface."},
            *_registry_commands(),
            {"path": ["reader", "overview"], "summary": "Describe the reader noun."},
            {"path": ["tag", "overview"], "summary": "Describe the tag noun."},
            {"path": ["ndef", "overview"], "summary": "Describe the ndef noun."},
            {"path": ["ui", "serve"], "summary": "Web UI surface: serve the UI and /api."},
            {"path": ["ui", "overview"], "summary": "Describe the ui noun."},
            {"path": ["mcp"], "summary": "MCP surface: every verb as a tool over stdio."},
        ],
        "surfaces": ["cli", "mcp", "web-ui"],
        "exit_codes": {
            "0": "success",
            "1": "user-input error",
            "2": "environment/setup error",
        },
        "json_support": True,
        "explain_pointer": "nfc-cli explain <path>",
    }


def cmd_learn(args: argparse.Namespace) -> int:
    if getattr(args, "json", False):
        emit_result(_as_json_payload(), json_mode=True)
    else:
        emit_result(_TEXT, json_mode=False)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "learn",
        help="Print a structured self-teaching prompt for agent consumers.",
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=cmd_learn)
