# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

**nfc-cli** is an AgentCulture mesh agent whose purpose is to **control NFC
reader-writers via USB**. It was scaffolded from `culture-agent-template`, so
today the repo contains only the template baseline: an agent-first CLI
(identity/introspection verbs), a mesh identity, the vendored guildmaster skill
kit, and a build/deploy pipeline. **No NFC functionality exists yet** — no
reader/tag code, no hardware dependency. Several strings still describe the
repo as "a clonable template" (the parser `description` in
`nfc_cli/cli/__init__.py`, `_ROOT` in `nfc_cli/explain/catalog.py`, the
other harness prompt files); update them as real NFC features land, and
update this section once NFC code exists.

### Hardware context (snapshot from 2026-11-08 — re-verify before relying on it)

Re-check with `lsusb`, `cat /sys/firmware/devicetree/base/compatible`, and
`which pcscd`; the host and installed packages can change.

- The dev host is an Arduino **VENTUNO Q** board (`arduino,monza`, Linux/aarch64).
  The CLI runs on the Linux side as an ordinary Python package; it is not an
  Arduino App and does not use `arduino-app-cli`, Bricks, or the Router Bridge.
- Attached reader: **ACS ACR1552** (`072f:2303`, a CCID/PC/SC-class reader).
- Not installed at the time: `pcscd`, `pcsc_scan`, `pyscard` (`smartcard`).
  The `arduino` user is not root. Adding a runtime dependency would be the
  first one (`dependencies = []` in `pyproject.toml` today) — a deliberate
  break from the template's zero-dependency runtime.

## Commands

```bash
uv sync                                   # install (dev group included)
uv run pytest -n auto                     # full suite (CI adds --cov=nfc_cli)
uv run pytest tests/test_cli.py::test_name -v   # single test
uv run nfc-cli whoami --json              # run the CLI

# Lint — exactly what CI's lint job runs
uv run black --check nfc_cli tests
uv run isort --check-only nfc_cli tests
uv run flake8 nfc_cli tests
uv run bandit -c pyproject.toml -r nfc_cli
markdownlint-cli2 "**/*.md" "#node_modules" "#.local" "#.claude/skills" "#.teken"
python3 scripts/scan-secrets.py           # committed secrets / non-localhost URLs in JSON config
uv run teken cli doctor . --strict        # agent-first rubric gate
uv run python scripts/harness-smoke.py --stage all --require config   # per-harness config check
```

Line length is 100 (black, isort, flake8). Coverage floor is 60%.
`test_harness_registries.py` skips one test unless a sibling `culture`
checkout exists at `~/git/culture` — that skip is expected.

## Architecture

### CLI contract (`nfc_cli/cli/`)

The CLI is cited (cite-don't-import) from teken's `python-cli` reference and
follows the **agent-first contract**, which `teken cli doctor --strict` enforces
in CI:

- `cli/__init__.py` builds an argparse tree; each verb/noun module in
  `cli/_commands/` exposes `register(sub)` and sets `func` on its subparser.
  New noun groups (e.g. a future `reader` / `tag` group) are registered in
  `_build_parser()` next to the existing ones.
- Handlers return `None`/`int` or raise `CliError(code, message, remediation)`
  (`cli/_errors.py`). `_dispatch` wraps any other exception into a `CliError`
  so no traceback ever leaks. Argparse errors are routed the same way via
  `_CliArgumentParser.error()`.
- Output goes through `cli/_output.py`: results → stdout, errors/diagnostics →
  stderr, never mixed. Text-mode errors render `error: …` + `hint: …` (the
  `hint:` line is required by the rubric).
- Every command must support `--json`. Because argparse errors happen before
  `args.json` exists, `main()` pre-scans raw argv for `--json`.
- Exit codes: `0` success, `1` user error, `2` environment error (e.g. a missing
  reader or daemon belongs here), `3+` reserved.
- Every registered noun/verb path needs a markdown entry in
  `nfc_cli/explain/catalog.py` (`ENTRIES`, keyed by command-path tuple) so
  `nfc-cli explain <path>` resolves. Keep `learn` output in step too.

### Mesh identity and `doctor`

`culture.yaml` declares `suffix: nfc-cli`, `backend: claude`, plus a `gate`
section (setup/test argv lists used by external tooling). `whoami` reads it by
walking up from the module file (not CWD), parsed without a YAML library.
`doctor` (`nfc_cli/cli/_commands/doctor.py`, `_diagnose()`) reports:
`prompt_file_present` (`backend: claude` requires this `CLAUDE.md`),
`backend_consistency` (fails when the declared backend is unknown),
`harness_prompts` (info only — other harness files never substitute for the
resident prompt), and `skills_present` (warning when `.claude/skills/` is
missing or empty). The
harness smoke probe also asks whether `CLAUDE.md` describes the project as
nfc-cli, so keep that name prominent here.

### Four harnesses, four prompt files, no shared base

| Harness | File(s) |
|---------|---------|
| Claude Code | `CLAUDE.md` (the fullest write-up; mesh resident prompt) |
| Pi / associate | `AGENTS.override.md` (context) + `.pi/SYSTEM.md` (read-only `associate` identity) |
| colleague | `AGENTS.colleague.md` |
| Qwen Code | `QWEN.md` |

There is intentionally **no `AGENTS.md`**. When repo conventions or purpose
change, update all four so they don't drift. **Known drift (pending
follow-up):** the other harnesses' prompt files still describe the repo as a generic
template rather than an NFC tool. `.qwen/skills`, `.pi/skills`, and
`.colleague/skills` are symlinks onto `.claude/skills`. `culture.yaml`'s
`backend` selects only the mesh resident, never which harness can run
interactively (see `docs/harness-selection.md`, `docs/automation-contract.md`).

## Conventions

- **Every PR bumps the version**, even docs/CI-only changes — use the
  `version-bump` skill (updates `pyproject.toml` and `CHANGELOG.md`). CI's
  `version-check` job blocks merge otherwise.
- PRs go through the `cicd` skill (layered on `devex pr`; it also checks
  the SonarCloud gate and unresolved review threads).
- Deploy: push to `main` publishes to PyPI via Trusted Publishing
  (`.github/workflows/publish.yml`); PRs publish a `.devN` build to TestPyPI.
- `.claude/skills/` is vendored **verbatim** from guildmaster (cite-don't-import).
  Don't edit or reformat those files; fixes go upstream, and re-syncs are
  recorded in `docs/skill-sources.md`. markdownlint ignores them.
- Cross-repo issues and mesh messages go through the `communicate` skill;
  issues in this repo use `gh issue create`.
