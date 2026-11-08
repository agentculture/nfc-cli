# nfc read-write cli + web ui

> nfc-cli reads and writes every NFC tag family through the attached ACS ACR1552 over raw USB CCID — no pcscd, no root — so the operator can dump an Xteink X3's NFC memory and write it back, from an agent-first CLI or a beautiful local web UI built to Culture standards

## Audience

- The operator (Ori) at the VENTUNO Q board, and AgentCulture agents driving the reader through the nfc-cli CLI or its MCP server; humans additionally through the local web UI
  - instruction: learn/explain/README name all three surfaces and who they serve

## Before → After

- Before: Today nfc-cli is the bare culture-agent-template: identity verbs only, no NFC code, dependencies = \[\]; reading the X3 took hand-written scratchpad pyusb scripts
  - instruction: compare against git show main:`nfc_cli`/cli/`__init__.py`
- After: On the board, 'nfc-cli tag info --wait 30' identifies the X3; 'tag dump' saves it to a file; 'tag restore FILE --apply' and 'ndef write --uri ... --apply' write it back and verify by read-back; the same verbs work as MCP tools and in the web UI served by 'nfc-cli ui serve'
  - instruction: run the sequence against the real X3 and paste outputs into the PR

## Why it matters

- Agents and the operator can inspect, back up, and program NFC tags (the X3 first) safely and repeatably, without root, pcscd, or ad-hoc scripts, through whichever surface fits — CLI, MCP, or browser

## Requirements

- Transport talks USB CCID to the ACR1552 (072f:2303) directly via pyusb + libusb-1.0: PICC interface 0, bulk EP 0x01/0x81; `PC_to_RDR_XfrBlock` for APDUs, `PC_to_RDR_Escape` for reader commands — probed live: GetSlotStatus, IccPowerOn, Escape E000001800 returned 'ACR1552 R FW 1.06.02'
  - honesty: a test drives the CCID framing (XfrBlock/Escape/time-extension) against a fake USB endpoint, and 'reader info' on the board returns the ACR1552 firmware string
- pyusb is added as an optional extra (not a base dependency) per the culture-rules zero-runtime-deps precedent; recorded in CHANGELOG and the four prompt files, whose 'no NFC functionality' status text must be updated
  - honesty: pip install nfc-cli (no extras) imports and runs identity verbs; tag verbs without the usb extra exit 2 with an install hint
- New noun groups reader/tag/ndef (and ui) register in cli/`__init__.py` `_build_parser` via register(sub); every verb supports --json, each noun exposes overview, every path gets an explain ENTRIES entry, learn/overview list them; teken cli doctor --strict stays green
  - honesty: teken cli doctor . --strict passes and `nfc-cli explain <path>` resolves for every registered path (test enumerates the parser)
- Hardware failures (no reader, permission denied, no card, card removed, auth failure, APDU status != 9000) are caught in handlers and raised as CliError with `EXIT_ENV_ERROR` (2) and a concrete hint; library exceptions never reach `_dispatch`, which would mislabel them `EXIT_USER_ERROR` with 'file a bug'
  - honesty: tests inject each failure (no device, EACCES, no card, card removed, auth fail, SW!=9000) and assert exit 2 with a hint: line and no traceback
- Tests run without hardware: transport is injectable and a fake CCID/tag simulator backs CLI tests, keeping pytest -n auto green on GitHub ubuntu-latest (no reader) with coverage >= 60%
  - honesty: CI (ubuntu-latest, no reader) passes pytest -n auto with coverage >= 60%
- Web UI visual direction is set with the frontend-design skill (frontend-design:frontend-design), layered on top of whatever Culture/embodiment standard mandates, so it reads as intentional rather than templated
  - honesty: the UI design rationale names the frontend-design direction and uses only culture-design tokens for color/type
- Live hardware evidence: before claiming done, read the actual Xteink X3 (and at least one other tag) through the CLI and the UI on this board; slot is currently empty (GetSlotStatus bStatus=0x02, no ICC present)
  - honesty: PR description contains live outputs from the X3 and at least one other tag or reports none was available
- Base install stays zero-third-party-dependency: pyusb (usb) and any web server stack sit behind optional extras (e.g. nfc-cli\[usb\], nfc-cli\[web\]) imported lazily; a missing extra raises CliError `EXIT_ENV_ERROR` with an install hint — mirroring culture-rules serve.py
  - honesty: a test pins the base runtime dependency set to empty; each extra is imported lazily inside the verb
- Tag-mutating verbs (write, restore, ndef write, format/lock) are dry-run by default and only touch the tag with --apply; dry-run reports exactly which pages/blocks would change (diff against current contents)
  - honesty: a test shows write/restore/ndef write without --apply sends zero write APDUs and prints the planned diff
- UI uses culture-design tokens: a verbatim, hash-pinned copy of agentculture/org site-astro/src/styles/global.css (embodiment pins b4d939ba0aa354a5ae53065319a773e0013de698), with a check script byte-comparing it to the pin, self-hosted fonts (Fraunces display, Albert Sans body; no CDN), and a build check that dist/ references no external origin; frontend-design guides composition on top of these tokens
  - honesty: a check script byte-compares tokens.css to the pinned org source hash and fails on a one-byte edit; dist/ contains no external origin
- UI is agent-first: root renders <script type=application/json id=agent-state> (status, `view_ready`, route, identity, errors, plus reader/card state) so agents and playwright can drive it; accessibility bar copied from culture-rules: skip link, full keyboard reach, visible focus, 44x44 targets, state announced in words not color, prefers-reduced-motion, axe zero serious/critical
  - honesty: playwright reads #agent-state and waits on `view_ready`; axe reports zero serious/critical violations
- UI e2e tests run Playwright against a mocked API / fake reader (no hardware, no Python server) with axe, matching culture-rules e2e/fixtures/api.ts; CI builds the bundle before uv build
  - honesty: npm run test:e2e passes in CI without a reader or Python server
- Python side serves the UI with stdlib http.server.ThreadingHTTPServer (zero new runtime deps): static bundle at /, JSON API under /api/ (reader status, tag info/read/write/dump/restore, ndef read/write), loopback-only by default with `resolve_bind` refusing routable addresses unless --bind-public, own default port distinct from embodiment's 8823 and culture-rules' 8765/8791/8796
  - honesty: a test starts the server on an ephemeral loopback port, fetches / and /api/reader, and a non-loopback --host without --bind-public exits 1 with a hint
- Mutating /api/ endpoints are guarded: install secret in a 0600 file sent as Authorization header plus Host/Origin allow-list — loopback is not authentication (embodiment CLAUDE.md:362), and a browser tab on another origin must not be able to write a tag via DNS rebinding/CSRF
  - honesty: tests show a mutating /api/ call without the secret or with a foreign Origin/Host is refused (401/403) and sends no APDU
- Wheel build runs the frontend build (`hatch_build.py`: npm ci && npm run build) and force-includes web/dist as `nfc_cli`/web/dist (gitignored), with a test that the wheel ships the dashboard; a checkout without dist degrades to a one-line text page while /api/ stays up
  - honesty: a test builds the wheel and asserts `nfc_cli`/web/dist/index.html is inside it; with dist missing, / serves a text page and /api/ still answers
- MIFARE Classic support is first-class: load key, authenticate per sector with key A/B, try a default key dictionary (FFFFFFFFFFFF, A0A1A2A3A4A5, D3F7D3F7D3F7, 000000000000), read/write data blocks, decode MAD + NDEF (sector 0 MAD with A0A1A2A3A4A5, NDEF sectors with D3F7D3F7D3F7), and dump/restore whole cards to a file
  - honesty: fake-card tests cover auth with each key type and dictionary fallback; live X3 dump reads 16/16 sectors
- User decision 2026-11-08: write-back to the Xteink X3 means both byte-exact dump/restore and writing new NDEF records (URL/text)
  - honesty: live: restore and NDEF write both read back identical on the X3
- User decision 2026-11-08: web UI is React 18 + Vite 6 + TypeScript (embodiment/culture-nodes pattern); Node LTS installed in user space under ~/.local on the board so the build and the playwright MCP can run
  - honesty: npm run build produces web/dist on the board with the user-space Node
- RF coupling with the X3 is marginal (detected only after ~18 s of repositioning, dropped out between runs): tag verbs need --wait/--timeout to poll for presence, and dump/restore must resume per sector after dropouts and verify every write by reading back
  - honesty: a fake-card test drops the card mid-dump and the dump resumes and completes; every write is followed by a read-back compare
- MCP server: 'nfc-cli mcp' serves every CLI verb as an MCP tool over stdio (optional 'mcp' extra, lazily imported, mcp>=1.2,<2), tools named `<noun>_<verb>`, mutating tools dry-run unless apply is exactly JSON true, and a parity test keeps CLI and MCP surfaces identical — so agents can drive the reader via CLI or MCP
  - honesty: a parity test asserts the set of MCP tool names equals the set of CLI verb paths and that mutating tools dry-run without apply=true
- CLI verbs are defined once in a registry (name, summary, params schema, mutating flag, handler) that argparse, the MCP server, and the web UI's /api/ all consume, so the three surfaces cannot drift (culture-rules cli/registry.py pattern)
  - honesty: CLI argparse, MCP `tool_specs` and /api/ routes are all generated from the same registry object (single source asserted in a test)
- Tag families: ISO14443A MIFARE Classic 1K/4K/Mini (Classic sectors/keys), MIFARE Ultralight/NTAG21x (NFC Forum Type 2, page read/write), ISO14443-4 / Type 4 (raw APDUs + NDEF application D2760000850101), ISO15693 / Type 5 (block read/write), FeliCa / Type 3 (read via reader pseudo-APDUs where the ACR1552 supports it); family detected from the PC/SC ATR card-name bytes
  - instruction: unit-test detection with recorded ATRs per family; live-test whichever families are physically available
  - honesty: detection unit tests pass for a recorded ATR per family; each family's read path has a fake-card test
- Command surface: reader list|info|overview; tag info|uid|wait|read|write|dump|restore|apdu|overview (Classic keys via --key/--key-type/--keys-file); ndef read|write (--text/--uri/--mime/--raw-file)|erase|overview; ui serve|overview; mcp; dump format is JSON (atr, uid, family, per-sector/page hex, keys used) with a --format bin option
  - honesty: the parser tree matches the listed surface and each path has an explain entry

## Honesty conditions

- every verb in the `after_state` runs on the board against the real X3 and the outputs are recorded in the PR
- the full test suite and live run succeed with no smartcard module and no pcscd process
- a test restores a dump containing modified trailers and asserts no trailer/block-0 write APDU is sent without --include-trailers
- learn/explain/README mention CLI, MCP and web UI surfaces
- git show main shows no NFC modules and dependencies = \[\]
- the X3 workflow completes end-to-end with no root, no pcscd and no scratchpad scripts
- each step of the `after_state` is reproduced live on the board
- the three commands run green locally and in CI
- live outputs for the three checks are recorded in the PR
- e2e + axe results and a playwright-MCP screenshot are attached to the PR

## Success signals

- teken cli doctor . --strict passes, pytest -n auto passes with coverage >= 60% without hardware, and the CLI/MCP parity test passes
- On real hardware: the X3 dump has 16/16 sectors read, an NDEF URI write followed by ndef read returns the same URI, and a restore of the original blank dump reads back byte-identical (64/64 blocks, trailers excluded)
- Web UI: Playwright e2e passes against the mocked API with 0 serious/critical axe violations, and a live playwright-MCP session against 'nfc-cli ui serve' shows the X3 card info

## Scope / boundaries

- pcscd/pyscard are not required: not installed (which pcscd empty, import smartcard fails) and sudo needs a password; a PC/SC backend may be added optionally later but the default USB backend must work on this host without it
- Restore never writes sector trailers (block 3 of each sector) or manufacturer block 0 unless --include-trailers is passed explicitly; a wrong access-bits write permanently bricks a MIFARE Classic sector

## Non-goals

- No MIFARE Classic key cracking (nested/darkside/hardnested attacks) — only known keys and a default-key dictionary
- No image push to the Xteink Tong Card or any NFC e-ink display in this frame (v2 park)
- No PC/SC (pcscd/pyscard) backend in this frame; the transport interface leaves room to add one later

## Assumptions

- The arduino user can open the reader without root: /dev/bus/usb/004/002 carries a user:arduino:rw- ACL (logind seat ACL); this may not hold on other hosts or after replug, so a permissions failure must map to `EXIT_ENV_ERROR` with a udev-rule remediation
- UI verification uses the playwright MCP: node/npx are now installed in user space (Node v26.13.0 LTS under ~/.local/lib/nodejs, wrappers in ~/.local/bin; libatomic.so.1 extracted from the libatomic1 deb into ~/.local/lib/nodejs/lib via `LD_LIBRARY_PATH` in the node wrapper). The MCP needs a reconnect (/mcp) to pick it up; Chromium system libraries on aarch64 remain unverified (v1)
- Confirmed on hardware 2026-11-08: the Xteink X3 NFC is a MIFARE Classic 1K (PC/SC ATR 3b8f8001804f0ca000000306030001000000006a, SS=03 ISO14443A-3, card name 0001), UID cb30c560 (4-byte, BCC 5e), SAK 08 / ATQA 0004 in block 0
- Block 0 manufacturer data reads 6263646566676869 ('bcdefghi'), the signature of Chinese MIFARE Classic clones commonly sold as UID-changeable 'magic' cards (gen1a/CUID); this fits Xteink's reported 'card emulation for personal access cards' feature — i.e. the X3 may be meant to receive a cloned access card including its UID

## Scope exploration

- `s1` — `ACR1552 USB descriptors + live CCID probe`: lsusb shows 3 interfaces (PICC CCID, SAM CCID, HID keyboard); no kernel driver bound to if0; scratchpad probe2.py claimed it and exchanged GetSlotStatus/PowerOn/Escape successfully as user arduino
  - seeds: `c2`
- `s2` — `/dev/bus/usb/004/002 ACL + sudo`: getfacl shows user:arduino:rw-; sudo -n requires a password; so pcscd cannot be installed by the agent
  - seeds: `c3`
- `s3` — `PC/SC stack on host`: libpcsclite1 installed but no pcscd daemon/ccid driver, no pyscard; pcscd.socket inactive
  - seeds: `c4`
- `s4` — `pyproject.toml + CLAUDE.md hardware note`: dependencies = \[\]; CLAUDE.md flags the first runtime dep as a deliberate break from the zero-dependency template
  - seeds: `c5`
- `s5` — `nfc_cli/cli/ (__init__, _commands/cli.py, overview.py, learn.py, explain/catalog.py)`: argparse tree with register(sub) per noun; cli.py shows noun-with-overview pattern required by rubric `overview_cli_noun_exists`; catalog keyed by path tuples
  - seeds: `c6`
- `s6` — `nfc_cli/cli/_errors.py + _dispatch`: `_dispatch` wraps unknown exceptions as `EXIT_USER_ERROR` 'file a bug'; exit 2 reserved for environment errors
  - seeds: `c7`
- `s7` — `.github/workflows/tests.yml + pyproject coverage`: CI runs pytest on ubuntu-latest with no USB device; `fail_under` = 60
  - seeds: `c8`
- `s8` — `Xteink device naming`: WebSearch: only the Xteink X3 (2026) lists NFC; X4 specs show Wi-Fi/BT, no NFC; protocol undocumented — deferred to research scout + live probe
- `s9` — `playwright MCP (plugin:playwright)`: MCP connection failed: ENOENT npx; which node/npx empty; nodejs not installed (apt candidate 18.19.1); sudo needs password
  - seeds: `c9`
- `s10` — `frontend-design skill`: listed as an available plugin skill (frontend-design:frontend-design): guidance for distinctive, intentional visual design of new UI
  - seeds: `c10`
- `s11` — `ACR1552 slot state (2026-11-08)`: two GetSlotStatus probes returned bStatus 0x02 (no card present); IccPowerOn returned 0x42/ICC mute
  - seeds: `c11`
- `s12` — `culture-rules CLAUDE.md:354-357 + serve.py:17,31`: library has zero third-party runtime deps; everything third-party behind an optional extra imported lazily; serve.py lazy-imports the server extra and maps ImportError to CliError exit 2
  - seeds: `c12`, `c5`
- `s13` — `culture-rules CLAUDE.md:514-536 (CLI verb contract)`: writes are dry-run by default, --apply commits; --json everywhere; explain+learn entry per verb; teken cli doctor --strict
  - seeds: `c13`
- `s14` — `culture-rules README.md:100-102 + serve.py:42-43`: wheel ships `culture_rules`/`web_dist` served same-origin so no Node at runtime; serve defaults to 127.0.0.1:8765 with env overrides; scan-secrets rejects non-localhost URLs
  - seeds: `c14`
- `s15` — `culture-rules web/scripts/check-tokens.mjs + web/README.md:13`: tokens.css = provenance header + verbatim copy of agentculture/org site-astro/src/styles/global.css at a pinned commit; byte-identity checked in CI; design canvas is visual source of truth
  - seeds: `c15` (rejected)
- `s16` — `culture-rules web/README.md:243-318`: agent-state JSON node pattern (webglass gate) and accessibility bar (skip link, 44px targets, words not color, reduced motion, axe in playwright)
  - seeds: `c16`
- `s17` — `culture-rules web/README.md:325-332 + CLAUDE.md:466-469`: Playwright chromium e2e with API served by request interception, vitest units, CI web job, publish.yml builds bundle before uv build
  - seeds: `c17`
- `s18` — `culture-rules web/README.md:3 (stack)`: Vite 6 + React 18 + TypeScript is culture-rules' own choice; scout found no org-wide mandate in culture-rules — it's precedent, not a standard
- `s19` — `embodiment/http/server.py:111,190,592`: ThreadingHTTPServer serving web/dist + /api/ JSON; `DEFAULT_PORT` 8823; `resolve_bind` refuses routable bind without --bind-public via CliError
  - seeds: `c18`
- `s20` — `embodiment/http/guard.py + CLAUDE.md:362`: install-secret Authorization header + Host/Origin allow-list guard everything except static files; 'Loopback is not authentication'
  - seeds: `c19`
- `s21` — `embodiment web/src/culture-design/tokens.css + scripts/check-culture-design.py + web/scripts/check-no-external-origin.mjs`: tokens are framework-agnostic CSS custom properties, verbatim at pin b4d939ba (light+dark, reduced-motion kill switch); fonts self-hosted via @fontsource-variable; build fails on external origins
  - seeds: `c14`
- `s22` — `embodiment hatch_build.py + pyproject force-include + tests/test_wheel_ships_the_dashboard.py`: frontend built at wheel time, dist force-included, gitignored; missing dist serves a text page instead of failing
  - seeds: `c20`
- `s23` — `agentculture/embodiment (premise check)`: embodiment is not a UI standard: it is the Gwen realtime voice app's agentic loop + dashboard; it is the reference implementation to cite (React 18.3 + Vite 6 + TS 5.6 + Vitest), and culture-nodes is named as the closest UI pattern (React+Vite+TS+Vitest+Playwright)
- `s24` — `Xteink X3 NFC (web research)`: spokenlikeageek.com/2026/09/09: Android identifies 'NXP MIFARE Classic 1K', NFC Tools writes URL/text; crosspoint-reader discussions/2259: chip 'independent of the firmware', iPhone unreliable; no firmware NFC driver found
  - seeds: `c21`
- `s25` — `ACR1552 MIFARE Classic APDU set (PC/SC part 3)`: Load Key FF 82, General Authenticate FF 86, Read Binary FF B0, Update Binary FF D6 apply to Classic via the reader's pseudo-APDUs
  - seeds: `c22`
- `s26` — `MIFARE Classic sector trailer semantics`: trailer = KeyA|access bits|KeyB; invalid access bits lock the sector irreversibly; block 0 is read-only on genuine chips
  - seeds: `c23`
- `s27` — `user-space Node install (2026-11-08)`: official tarball sha256 OK; first run failed: libatomic.so.1 missing; fixed by extracting libatomic1 deb locally; node/npm/npx 26.13.0/11.20.0 resolve on PATH
  - seeds: `c9`
- `s28` — `Live X3 dump (scratchpad x3-dump-0.json)`: all 16 sectors authenticate with key A FFFFFFFFFFFF; every data block is zero; every trailer is 000000000000 ff078069 ffffffffffff (transport access bits) — the tag is factory blank, so there is no X3-owned data to preserve
  - seeds: `c21`
- `s29` — `X3 block 0 (cb30c5605e0804006263646566676869)`: UID cb30c560 + BCC 5e + SAK 08 + ATQA 0400 + manufacturer bytes 'bcdefghi' (ASCII 62..69) — matches common clone-card factory pattern, not NXP genuine
  - seeds: `c26`
- `s30` — `X3 presence polling (2026-11-08)`: first full-dump attempt failed with slot absent; a wait-then-dump loop with per-sector resume succeeded; user heard the reader beep on detection
  - seeds: `c27`
- `s31` — `culture-rules culture_rules/mcp/{server,tools}.py + README.md:169-178 + tests/test_surface_parity.py`: low-level mcp SDK Server built from a verb registry; `tool_specs`() from `params_schema`(); 'Writes are a dry-run unless apply=true'; ServerExtraMissing on missing extra; parity test enforces CLI==MCP
  - seeds: `c28`
- `s32` — `culture-rules culture_rules/cli/registry.py (Verb, Context(apply))`: tools.py builds MCP tools from REGISTRY.verbs(); handlers receive Context(client, apply) so dry-run is uniform
  - seeds: `c29`

## Decisions

- User decision 2026-11-08: confirmed all proposed claims/honesty conditions except duplicate c15 (rejected); card cloning onto the X3 is deferred as a follow-up

## Open parks

- [unknown_nonblocking] Whether headless Chromium can run on this aarch64 board without root-installed system libraries (needed for playwright verification)
- [unknown_nonblocking] The Tong Card (NFC-powered e-ink display, 428x428 red/yellow/black/white) is a separate device with unknown chip/protocol; image push to it is out of this frame unless the user says that's the target
- [follow_up] Clone other cards (e.g. access cards incl. UID block 0) onto the X3's suspected magic MIFARE Classic tag: gen1a/CUID detection + guarded block-0 write
