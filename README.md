# nfc-cli

Control NFC reader-writers via USB.

## What you get

- **An NFC CLI** that reads, writes, dumps, restores and NDEF-encodes tags
  through an ACS ACR1552 over raw USB CCID. Writes are a dry-run unless
  `--apply` is given, and sector trailers need `--include-trailers`.
- **An MCP server** (`nfc-cli mcp`, extra `nfc-cli[mcp]`) exposing the same
  verbs as `noun_verb` tools.
- **A local web console** (`nfc-cli ui serve`) with a tag memory map,
  preview-then-apply writes, NDEF, dump/restore and a request log.
- **An agent-first CLI** cited from [teken](https://github.com/agentculture/teken)
  (`afi-cli`). The base runtime has no third-party dependencies; the first
  optional extras are `usb` (pyusb), `mcp`, and `all`.
- **A mesh identity** - `culture.yaml` (`suffix` + `backend`) and the matching
  resident prompt file (`CLAUDE.md`, since this repo runs `backend: claude`).
  The mesh resident is one of **two separate selections** - see
  [Two selections, not one](#two-selections-not-one) below.
- **Four harness prompt files**, one per agent harness, each read by exactly
  one of them (see [Prompt files by harness](#prompt-files-by-harness) below).
- **The canonical guildmaster skill kit** (11 skills) under `.claude/skills/`,
  vendored cite-don't-import. See [`docs/skill-sources.md`](docs/skill-sources.md).
- **A build + deploy baseline** - pytest, lint, the agent-first rubric gate, and
  PyPI Trusted Publishing wired into GitHub Actions.

## Prompt files by harness

Four harnesses, four root files, no shared base — each file is read by
exactly one harness:

| Harness | File(s) |
|---------|---------|
| Claude Code | [`CLAUDE.md`](CLAUDE.md) |
| Pi / associate | [`AGENTS.override.md`](AGENTS.override.md) + [`.pi/SYSTEM.md`](.pi/SYSTEM.md) |
| colleague | [`AGENTS.colleague.md`](AGENTS.colleague.md) |
| Qwen Code | [`QWEN.md`](QWEN.md) |

**Claude Code** — `CLAUDE.md` is the fullest write-up of the repo's
conventions; read it first.

**Pi / associate** — `AGENTS.override.md` replaces this directory's
`AGENTS.md`/`CLAUDE.md` in Pi's context layer, so Pi does not inherit
`CLAUDE.md`. `.pi/SYSTEM.md` replaces Pi's default system prompt with the
non-coding `associate` identity (read/find/summarize only).

**colleague** — colleague's prompt cascade is `AGENTS.md` →
`AGENTS.colleague.md` → `AGENTS.colleague.<model>.md`. This repo ships only
the middle layer: there is no `AGENTS.md` (a shared base across harnesses was
considered and rejected) and no per-model override file.

**Qwen Code** — Qwen Code reads `QWEN.md` and `AGENTS.md`; since there is no
`AGENTS.md`, `QWEN.md` is its sole source of guidance.

There is intentionally **no `AGENTS.md`** at the root — each harness gets an
unrelated file rather than cascading from a shared base.

## Two selections, not one

It is tempting to read "switch harness" as one decision. It is actually two,
and this repo keeps them separate:

1. **The interactive harness** — which binary you run (`claude`, `pi`,
   `colleague`, `qwen`). `cd` into the clone and run any of them; all four
   are live simultaneously, and none of them requires editing a file or
   flipping a switch. A harness can be force-selected for one invocation
   (e.g. a CI smoke check) without ever touching `culture.yaml` — see
   [`docs/automation-contract.md`](docs/automation-contract.md).
2. **The mesh resident** — the single `backend` `culture.yaml` declares,
   which is what the Culture daemon starts and what `steward doctor`
   checks. `guild harness use <name>` changes only this.

`culture.yaml`'s `backend` affects (2) only. It never affects which harness
you can invoke interactively in (1). See
[`docs/harness-selection.md`](docs/harness-selection.md) for the full
writeup, including who reads this config and why existing siblings are not
retrofitted by this arc.

## Install

```bash
pip install 'nfc-cli[usb]'      # CLI + raw-USB reader access (pyusb)
pip install 'nfc-cli[all]'      # also the MCP server
```

Extras: `usb` (pyusb), `mcp`, `all`. Reader permissions on a headless host
need a udev rule - see [`docs/hardware.md`](docs/hardware.md) and
[`docs/udev/60-nfc-cli-acr1552.rules`](docs/udev/60-nfc-cli-acr1552.rules).

## Quickstart

```bash
uv sync
uv run pytest -n auto                 # run the test suite
uv run nfc-cli learn                  # self-teaching prompt (add --json)
uv run nfc-cli reader list            # find the reader
uv run nfc-cli tag info --wait 30     # identify a tag held on the reader
uv run teken cli doctor . --strict    # the agent-first rubric gate CI runs
NFC_CLI_FAKE=x3 uv run nfc-cli tag info   # no hardware: simulated Xteink X3 tag
```

## CLI

| Noun | Verbs |
|------|-------|
| `reader` | `list`, `info` |
| `tag` | `info`, `uid`, `wait`, `read`, `write`, `dump`, `restore`, `apdu` |
| `ndef` | `read`, `write`, `erase`, `format` |
| (identity) | `whoami`, `learn`, `explain <path>`, `overview`, `doctor`, `cli overview` |

Flags: `--json` (every command), `--trace` (APDU trace on stderr),
`--reader INDEX|BUS:ADDR`, and on mutating verbs `--apply` (default is a
dry-run) and `--include-trailers` (sector trailers and block 0). Use
`nfc-cli explain <noun> <verb>` for per-verb docs.

Results go to stdout, errors/diagnostics to stderr (never mixed). Exit codes:
`0` success, `1` user error, `2` environment error (no reader, no permission),
`3+` reserved.

Writes keep state on disk: snapshots in `$HOME/.local/share/nfc-cli/snapshots`,
write journals and `audit.log` in `$HOME/.local/state/nfc-cli`.

## MCP server

`nfc-cli mcp` runs an MCP server on stdio (install `nfc-cli[mcp]`). Tools are
named `noun_verb` (for example `tag_info`, `ndef_read`). Add it to an MCP
client:

```json
{"mcpServers": {"nfc-cli": {"command": "nfc-cli", "args": ["mcp"]}}}
```

## Web console

```bash
nfc-cli ui serve                 # http://127.0.0.1:8770
nfc-cli ui serve --fake x3       # simulated tag, no hardware
```

It binds loopback only unless `--bind-public` is given. Writes are guarded by
an install secret kept in `$HOME/.config/nfc-cli/web-secret`. Flags: `--host`,
`--port`, `--bind-public`, `--fake SCENARIO`, `--json`.

## The `gate` section

`culture.yaml` may carry a top-level `gate` section that declares how to set
up and test this repo, so tooling (for example a PR-fixer that re-runs the
checks before pushing) needs no per-repo knowledge:

```yaml
gate:
  setup:
  - [uv, sync]
  test:
  - [uv, run, pytest, -n, auto]
```

`setup` and `test` are each a list of commands, and each command is an argv
list (never a shell string), run in order from the repo root. The values are
configuration only; no tool supplies a default. Culture's loader ignores
unknown top-level keys, so the section is safe alongside `agents`.

## License

Apache 2.0 — see [`LICENSE`](LICENSE).
