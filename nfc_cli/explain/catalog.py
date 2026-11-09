"""Markdown catalog for ``nfc-cli explain <path>``.

Each entry is verbatim markdown. Keys are command-path tuples. The empty tuple
and ``("nfc-cli",)`` both resolve to the root entry.

Keep bodies self-contained: an agent reading one entry should get enough
context without chaining reads.
"""

from __future__ import annotations

_ROOT = """\
# nfc-cli

Control NFC reader-writers (ACS ACR1552) via USB: read, write, dump, restore and
NDEF-encode tags. One verb registry feeds three surfaces: this CLI, an MCP server
(`nfc-cli mcp`) and a local web UI (`nfc-cli ui serve`). Writes are a dry-run
unless `--apply` is given. Identity lives in `culture.yaml` + `CLAUDE.md`.

## Verbs

- `nfc-cli whoami` — identity probe from `culture.yaml`.
- `nfc-cli learn` — structured self-teaching prompt.
- `nfc-cli explain <path>` — markdown docs for any noun/verb.
- `nfc-cli overview` — descriptive snapshot of the agent.
- `nfc-cli doctor` — check the agent-identity invariants.
- `nfc-cli cli overview` — describe the CLI surface.
- `nfc-cli reader list|info` — reader operations.
- `nfc-cli tag info|uid|magic-check|wait|read|write|dump|restore|apdu` — tag operations.
- `nfc-cli ndef read|write|erase|format` — NDEF operations.
- `nfc-cli mcp` — MCP server on stdio.
- `nfc-cli ui serve` — local web UI and /api.
- `nfc-cli <noun> overview` — describe reader, tag, ndef or ui.

## Exit-code policy

- `0` success
- `1` user-input error
- `2` environment / setup error
- `3+` reserved

## See also

- `nfc-cli explain whoami`
- `nfc-cli explain doctor`
"""

_WHOAMI = """\
# nfc-cli whoami

Reports the agent's identity from `culture.yaml`: nick (`suffix`), backend,
served model, and the package version. Read-only.

## Usage

    nfc-cli whoami
    nfc-cli whoami --json
"""

_LEARN = """\
# nfc-cli learn

Prints a structured self-teaching prompt covering purpose, command map,
exit-code policy, `--json` support, and the `explain` pointer.

## Usage

    nfc-cli learn
    nfc-cli learn --json
"""

_EXPLAIN = """\
# nfc-cli explain <path>

Prints markdown documentation for any noun/verb path. Unlike `--help` (terse,
positional), `explain` is global and addressable by path.

## Usage

    nfc-cli explain nfc-cli
    nfc-cli explain whoami
    nfc-cli explain --json <path>
"""

_OVERVIEW = """\
# nfc-cli overview

Read-only descriptive snapshot of the agent: identity (from `culture.yaml`), the
verb surface, and the sibling-pattern artifacts the template carries. Accepts an
ignored `target` so a stray path never hard-fails.

## Usage

    nfc-cli overview
    nfc-cli overview --json
"""

_DOCTOR = """\
# nfc-cli doctor

Checks the agent-identity invariants `steward doctor` verifies:
prompt-file-present and backend-consistency (`claude` → `CLAUDE.md`), plus a
skills-present check. Exits 1 when unhealthy.

prompt-file-present requires the *resident* prompt the declared backend
actually reads. Other harness prompt files recognized under the same backend
name (`AGENTS.override.md`, `.pi/SYSTEM.md`, `QWEN.md`) belong to
interactively available harnesses the mesh daemon never loads; they are
reported by the informational harness-prompts check and never substituted.

## Usage

    nfc-cli doctor
    nfc-cli doctor --json
"""

_CLI = """\
# nfc-cli cli

Noun group for CLI-surface introspection. `cli overview` describes the CLI
itself (distinct from the global `overview`, which describes the agent).

## Usage

    nfc-cli cli overview
    nfc-cli cli overview --json
"""


_MCP = """\
# nfc-cli mcp

Runs an MCP server on stdio exposing every reader / tag / ndef verb as a tool
(`tag_read`, `ndef_write`, ...). Mutating tools are a dry-run unless `apply=true`.
Needs the optional extra: `pip install 'nfc-cli[mcp]'` (missing extra: exit 2).

## Usage

    nfc-cli mcp
"""

_UI = """\
# nfc-cli ui

Noun group for the local web UI. `ui serve` serves the UI and the /api routes
(same verbs as the CLI); `ui overview` describes the group.

## Usage

    nfc-cli ui overview
    nfc-cli ui overview --json
"""

_UI_SERVE = """\
# nfc-cli ui serve

Serves the web UI and /api on 127.0.0.1 by default. A non-loopback `--host` is
refused (exit 1) unless `--bind-public` is passed. `--fake SCENARIO` serves a
fake reader (e.g. `x3`) so no hardware is needed.

## Usage

    nfc-cli ui serve
    nfc-cli ui serve --port 8080 --fake x3
    nfc-cli ui serve --host 0.0.0.0 --bind-public
"""

_NOUNS = {
    "reader": "ACR1552 reader operations.",
    "tag": "Tag operations.",
    "ndef": "NDEF operations.",
}


def _noun_doc(noun: str, blurb: str, verbs: list) -> str:
    """The overview page for one noun: its blurb plus a list of its verbs."""
    lines = "\n".join(f"- `nfc-cli {noun} {v.verb}` — {v.summary}" for v in verbs)
    return (
        f"# nfc-cli {noun}\n\n{blurb} Every verb accepts `--json`, `--trace` (APDU trace on "
        f"stderr) and `--reader`; mutating verbs are a dry-run unless `--apply`.\n\n"
        f"## Verbs\n\n{lines}\n- `nfc-cli {noun} overview` — describe this noun.\n"
    )


def _verb_doc(noun: str, v) -> str:
    """The per-verb page: summary, options (params + --apply), trailer note, usage."""
    flags = []
    for p in v.params:
        shown = p.name if p.positional else "--" + p.name.replace("_", "-")
        flags.append(f"- `{shown}` — {p.help}")
    if v.mutating:
        flags.append("- `--apply` — commit the write (default is a dry-run)")
    flag_text = "\n".join(flags) or "- (none)"
    note = ""
    if v.extra.get("trailer_flag"):
        note = (
            "\nSector trailers and block 0 are protected: they are only written with "
            "`--include-trailers` (ndef format writes its fixed template trailers).\n"
        )
    return (
        f"# nfc-cli {noun} {v.verb}\n\n{v.summary}.\n\n## Options\n\n{flag_text}\n"
        f"- `--json`, `--trace`, `--reader`\n{note}\n## Usage\n\n"
        f"    nfc-cli {noun} {v.verb} --json\n"
    )


def _registry_entries() -> dict[tuple[str, ...], str]:
    import nfc_cli.verbs  # noqa: F401  (registers the verbs)
    from nfc_cli.registry import REGISTRY

    out: dict[tuple[str, ...], str] = {}
    for noun, blurb in _NOUNS.items():
        verbs = REGISTRY.verbs(noun)
        out[(noun,)] = _noun_doc(noun, blurb, verbs)
        out[(noun, "overview")] = out[(noun,)]
        for v in verbs:
            out[v.path] = _verb_doc(noun, v)
    return out


ENTRIES: dict[tuple[str, ...], str] = {
    (): _ROOT,
    ("nfc-cli",): _ROOT,
    ("whoami",): _WHOAMI,
    ("learn",): _LEARN,
    ("explain",): _EXPLAIN,
    ("overview",): _OVERVIEW,
    ("doctor",): _DOCTOR,
    ("cli",): _CLI,
    ("cli", "overview"): _CLI,
}
ENTRIES.update(_registry_entries())
ENTRIES.update({("mcp",): _MCP, ("ui",): _UI, ("ui", "overview"): _UI, ("ui", "serve"): _UI_SERVE})
