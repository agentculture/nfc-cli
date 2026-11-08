# QWEN.md

This file provides guidance to Qwen Code when working with code in this
repository. Qwen Code's context loader reads exactly `QWEN.md` and `AGENTS.md`
in a directory; this repo deliberately ships only `QWEN.md` — there is no
`AGENTS.md` here (each harness gets its own file; see "Prompt files by
harness" below), so this file is the sole source of project guidance for a
Qwen Code session.

## What this project is

`nfc-cli` is an AgentCulture mesh agent whose purpose is to **control NFC
reader-writers via USB**. **Status: implemented.** It drives an ACS ACR1552
over raw USB CCID and exposes the same registry-derived verbs three ways: the
CLI (`reader`, `tag`, `ndef` nouns; `--json`, `--trace`, `--reader`; writes are
a dry-run unless `--apply`, trailers need `--include-trailers`), an MCP server
(`nfc-cli mcp`, extra `nfc-cli[mcp]`, tools named `noun_verb`), and a local web
console (`nfc-cli ui serve`, default 127.0.0.1:8770). Optional extras: `usb`
(pyusb), `mcp`, `all`. It was scaffolded from `culture-agent-template`, so the
agent-first identity/introspection CLI, mesh identity, skill kit and
build/deploy pipeline remain. Hardware notes: `docs/hardware.md`.

It is a sibling to [`guildmaster`](https://github.com/agentculture/guildmaster)
(the **skills supplier**), [`steward`](https://github.com/agentculture/steward)
(**alignment** — `steward doctor`, the sibling-pattern baseline), and
[`teken`](https://github.com/agentculture/teken) (the **afi-cli** "Agent First
Interface" scaffolder this CLI is cited from) within the Organic Development
framework.

## Prompt files by harness

This repo's root carries one prompt file per agent harness, each read by
exactly one of them — there is no shared base file for them to inherit from:

- **Claude Code** → [`CLAUDE.md`](CLAUDE.md) (the fullest write-up; read it
  first if you are new to the repo).
- **Pi / associate** → [`AGENTS.override.md`](AGENTS.override.md) for context,
  plus [`.pi/SYSTEM.md`](.pi/SYSTEM.md) for its system prompt.
- **colleague** → [`AGENTS.colleague.md`](AGENTS.colleague.md).
- **Qwen Code** → this file.

## Identity

Declared in `culture.yaml`:

```yaml
agents:
- suffix: nfc-cli
  backend: claude
```

`backend: claude` fixes the *mesh resident* prompt file to `CLAUDE.md` — the
mesh runtime reads that file, not this one. A Qwen Code session working in
this repo is a separate, local tool session; it reads `QWEN.md`
regardless of what `culture.yaml` declares, and running Qwen Code here neither
requires nor changes that declaration. The declaration and the resident prompt
together satisfy the two invariants `steward doctor` verifies:
**prompt-file-present** and **backend-consistency** (`claude` ↔ `CLAUDE.md`).

## The CLI

The CLI is cited (cite-don't-import) from teken's `python-cli` reference
(`teken cli cite`), so the base runtime package has **no third-party
dependencies** (optional extras: `usb`, `mcp`, `all`); `teken` (a.k.a.
`afi-cli`) is a dev dependency only. Agent-first verbs:

- `nfc-cli whoami` — identity from `culture.yaml`.
- `nfc-cli learn` — structured self-teaching prompt.
- `nfc-cli explain <path>` — markdown docs for any noun/verb.
- `nfc-cli overview` — descriptive snapshot of the agent.
- `nfc-cli doctor` — check the agent-identity invariants.
- `nfc-cli cli overview` — describe the CLI surface itself.
- `nfc-cli reader list|info`, `nfc-cli tag info|uid|wait|read|write|dump|restore|apdu`,
  `nfc-cli ndef read|write|erase|format` — the NFC verbs (writes need `--apply`).
- `nfc-cli mcp` — MCP server on stdio; `nfc-cli ui serve` — local web console.

Conventions: every command supports `--json`; results go to stdout, errors and
diagnostics to stderr (never mixed); exit codes are `0` success, `1` user
error, `2` environment error, `3+` reserved. The generic exception wrapper
reports everything as exit `1`, so hardware/daemon failures (no reader, PC/SC
service down) must be caught in the handler and raised as `CliError` with
`EXIT_ENV_ERROR`. The agent-first rubric is enforced in CI by
`teken cli doctor . --strict`.

## Skills

`.claude/skills/` vendors the **canonical guildmaster skill kit**
(cite-don't-import). Provenance and the re-sync procedure live in
`docs/skill-sources.md`. Do not reformat or edit vendored scripts — re-sync
from guildmaster instead.

## Conventions

- **Every PR bumps the version** — even docs/config/CI. Use the
  `version-bump` skill; the `version-check` CI job blocks merge otherwise.
- **Tests**: `uv run pytest -n auto`. **Lint**: black, isort, flake8 (line
  length 100), bandit, markdownlint.
- **Deploy**: pushing to `main` publishes to PyPI via Trusted Publishing
  (`.github/workflows/publish.yml`); PRs do a TestPyPI dry-run.

## Layout

```text
nfc_cli/   agent-first CLI (cited from teken's python-cli reference)
  cli/                    parser, error/output contract, _commands/ (verbs)
  nfc/                    reader, Classic/NDEF logic, fakes, journals
  mcp/  web/              MCP server and local web console
  registry.py verbs.py    one verb registry feeding CLI, MCP and web
  explain/                markdown catalog for `explain`
tests/                    pytest smoke + introspection tests
.claude/skills/           vendored guildmaster skill kit (cite-don't-import)
docs/skill-sources.md     skill provenance ledger
culture.yaml              mesh identity (suffix + backend)
.github/workflows/        tests + deploy (PyPI Trusted Publishing)
```

This file describes the repository **as it exists on disk today**. When you
edit, keep claims grounded in checked-in reality; if a section drifts ahead of
reality, mark it `(planned)` or move it under a `## Roadmap` heading. For the
fullest write-up (every lint/CI command, the CLI contract, `doctor` checks,
observed hardware), see [`CLAUDE.md`](CLAUDE.md) — its conventions apply to
work in this repo regardless of which harness is doing it.
