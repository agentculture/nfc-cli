# Colleague Resident — `nfc-cli`

You are a colleague session working in the `nfc-cli` repo — reading this file
because colleague's prompt cascade resolves it here, not because
`culture.yaml` selected you. That declaration says `backend: claude`, so
`CLAUDE.md` is this repo's *mesh resident* prompt; colleague remains fully
usable interactively over the same checkout, and this file is what it loads
when you run it. Declaring `backend: colleague` would promote this file to the
resident prompt as well — the guidance below holds either way.

Your job is to assist with scoped tasks delegated by the operator or peer
agents, using the colleague tool-loop (`read_file` / `write_file` /
`edit_file` / `list_dir` / `run_command` / `finish`).

## The prompt cascade (and what this repo actually ships)

colleague concatenates up to three files, in order, as its prompt cascade:

1. `AGENTS.md` — a shared base, if present.
2. `AGENTS.colleague.md` — this file.
3. `AGENTS.colleague.<sanitized-model>.md` — a model-specific override, if
   present.

**This repo ships only layer 2.** There is deliberately no `AGENTS.md` at the
root (a shared base across the four harness files was proposed and rejected —
each harness gets its own, unrelated file; see `CLAUDE.md`'s "Four harnesses,
four prompt files, no shared base"), so the cascade for colleague in this repo starts and ends at this
file. There is also no `AGENTS.colleague.<sanitized-model>.md` — this repo
doesn't need per-model overrides today. If you add one of those files later,
update this section so the docs keep matching what's actually on disk.

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
`CLAUDE.md` in
this repo is written for a Claude Code session working *on* the repo — it is
not your runtime prompt, but it is the fullest write-up of the repo's
conventions if you need more context than fits here (commands and lint gates,
the CLI error/exit-code contract, `doctor` checks, observed hardware).

## How you work

- Prefer small, reversible steps; hand off via `finish` when done.
- Follow the operator's instructions and any skills loaded from
  `.colleague/skills/` when present.
- The vendored skills under `.claude/skills/` are cited **verbatim** from
  guildmaster — don't reformat or edit their scripts; a fix belongs upstream
  (see `docs/skill-sources.md` for the re-sync procedure).
- Every PR bumps the version (`version-bump` skill) — CI's `version-check` job
  blocks merge otherwise.
- CLI handlers raise `CliError`; hardware/daemon failures (no reader, PC/SC
  service down) are `EXIT_ENV_ERROR` (2), caught in the handler rather than
  left to the generic wrapper, which reports everything as exit 1.
- Tests: `uv run pytest -n auto`. Lint: black, isort, flake8 (line length 100),
  bandit, markdownlint.
