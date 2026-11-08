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
- Tag families (per ACR1552 manual 1.09 §1 + read/update-binary address tables): ISO14443A — MIFARE Classic 1K/4K/Mini (sectors/keys), MIFARE Ultralight/NTAG21x (Type 2 pages), Topaz (Type 1); ISO14443-4 A and B — Type 4 / T=CL via raw APDUs + NDEF app D2760000850101; ISO14443B-3 proprietary — SRI/SRIX (SRIX4K/SRT512), Picopass/iCLASS-legacy blocks, CTS, Innovatron; ISO15693 — Type 5 blocks; ISO/IEC 18092 NFC-F — FeliCa (Type 3) reads. Family detected from the PC/SC ATR (SS byte + card-name bytes, incl. Picopass 3B8F8001804F0CA0000003060600xx); polling types the reader skips by default (Topaz, SRI, Innovatron, Picopass-15693, CTS) are enabled only via the snapshot-and-restore boundary
  - instruction: unit-test detection with recorded ATRs per family; live-test whichever families are physically available
  - honesty: detection unit tests pass for a recorded ATR per family; each family's read path has a fake-card test
  - honesty: detection unit tests pass for a recorded/manual ATR per family (incl. Picopass, SRIX, FeliCa, ISO15693); each family's read path has a fake-card test
- Command surface: reader list|info|overview; tag info|uid|wait|read|write|dump|restore|apdu|overview (Classic keys via --key/--key-type/--keys-file); ndef read|write (--text/--uri/--mime/--raw-file)|erase|overview; ui serve|overview; mcp; dump format is JSON (atr, uid, family, per-sector/page hex, keys used) with a --format bin option
  - honesty: the parser tree matches the listed surface and each path has an explain entry
- Reader access is exclusive (probe: second process claiming interface 0 gets USBError 16 Resource busy). Every operation claims and releases the interface, an advisory lock file under $`XDG_RUNTIME_DIR` records the holder (pid + command), and a busy reader raises CliError exit 2 naming the holder (e.g. 'nfc-cli ui serve', or pcscd if installed later) instead of a raw USBError; ui serve and mcp never hold the interface between operations
  - honesty: a test holds the lock/claim in one process and asserts a second CLI call exits 2 naming the holder; live: tag info works while ui serve is idle
- Before any --apply write (write, restore, ndef write/erase/format) the current readable contents of the target tag are snapshotted to a timestamped dump file (0600, under the user data dir) and its path is printed; 'tag restore SNAPSHOT --apply' is the documented rollback
  - honesty: fake-card test: --apply write creates the snapshot file (mode 0600) before the first write APDU; snapshot failure aborts with zero writes
- Writes are journaled: each block/page write is followed by a read-back compare and appended to a per-operation journal; an interrupted restore (card lifted, USB error) reports written/verified/pending blocks and 'tag restore --resume JOURNAL' completes it
  - honesty: fake-card test removes the card mid-restore; journal lists pending blocks and --resume completes to a byte-identical read-back
- Dumps and journals contain sector keys/trailers: files are written 0600, keys are masked in text output and traces, and MCP/API responses redact key bytes unless the caller passes `include_keys`; dumps never land inside the repo tree by default (scan-secrets would not catch binary/hex keys)
  - honesty: tests assert file mode 0600 and that text/--trace/MCP/API outputs contain no key bytes without `include_keys`
- Observability: --trace prints every APDU/escape exchanged (direction, bytes, SW, timing) to stderr with key bytes masked, available on every tag/reader/ndef verb and as a server/MCP log option
  - honesty: --trace output on a fake read shows each APDU with masked keys
- Trailer-writing operations over MCP and the web API (ndef format, restore with `include_trailers`) require apply=true AND the explicit `include_trailers`=true / format=true argument, and every such call is appended to an audit log (timestamp, surface, tag UID, blocks)
  - honesty: MCP and API tests show a trailer-writing call with apply=true but without `include_trailers`/format sends no write APDU, and a permitted call appends an audit-log line

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
- a fake-card test formats a blank Classic 1K and an Android phone (NFC Tools) reads the written URI from the real X3
- a test asserts no E0 00 00 9x escape is ever constructed; live: polling type reads 07 05 after any config-changing command

## Success signals

- teken cli doctor . --strict passes, pytest -n auto passes with coverage >= 60% without hardware, and the CLI/MCP parity test passes
- On real hardware: the X3 dump has 16/16 sectors read, an NDEF URI write followed by ndef read returns the same URI, and a restore of the original blank dump reads back byte-identical (64/64 blocks, trailers excluded)
- Web UI: Playwright e2e passes against the mocked API with 0 serious/critical axe violations, and a live playwright-MCP session against 'nfc-cli ui serve' shows the X3 card info

## Scope / boundaries

- pcscd/pyscard are not required: not installed (which pcscd empty, import smartcard fails) and sudo needs a password; a PC/SC backend may be added optionally later but the default USB backend must work on this host without it
- Restore never writes sector trailers (block 3 of each sector) or manufacturer block 0 unless --include-trailers is passed explicitly; the single sanctioned exception is 'ndef format', which writes trailers only from the fixed, parity-checked NFC-Forum template (user decision 2026-11-08)
- Reader configuration is read-mostly: the CLI may read reader settings and set the PICC polling type only with snapshot-then-restore in an independent step that verifies by read-back; it never sets the HID keyboard / operating mode (E0 00 00 9x), because 'Only HID Keyboard' mode removes the CCID interface the CLI depends on

## Non-goals

- No MIFARE Classic key cracking (nested/darkside/hardnested attacks) — only known keys and a default-key dictionary
- No image push to the Xteink Tong Card or any NFC e-ink display in this frame (v2 park)
- No PC/SC (pcscd/pyscard) backend in this frame; the transport interface leaves room to add one later
- No NFC peer-to-peer (ISO 18092 active/passive initiator-target with LLCP/SNEP): the ACR1552 manual documents reader/writer, card emulation and keyboard emulation modes only

## Assumptions

- The arduino user opens the reader without root only via systemd-logind uaccess: udev tags the device ':security-device:seat:systemd:uaccess:' and the ACL follows the active seat0 session (tty2 today). A headless boot, SSH-only use, or a system service has no seat session and gets EACCES — so permission failures map to exit 2 with a remediation that ships a udev rule (072f:2303 -> GROUP/MODE) in docs
- UI verification uses the playwright MCP: node/npx are now installed in user space (Node v26.13.0 LTS under ~/.local/lib/nodejs, wrappers in ~/.local/bin; libatomic.so.1 extracted from the libatomic1 deb into ~/.local/lib/nodejs/lib via `LD_LIBRARY_PATH` in the node wrapper). The MCP needs a reconnect (/mcp) to pick it up; Chromium system libraries on aarch64 remain unverified (v1)
- Confirmed on hardware 2026-11-08: the Xteink X3 NFC is a MIFARE Classic 1K (PC/SC ATR 3b8f8001804f0ca000000306030001000000006a, SS=03 ISO14443A-3, card name 0001), UID cb30c560 (4-byte, BCC 5e), SAK 08 / ATQA 0004 in block 0
- Block 0 manufacturer data reads 6263646566676869 ('bcdefghi'), the signature of Chinese MIFARE Classic clones commonly sold as UID-changeable 'magic' cards (gen1a/CUID); this fits Xteink's reported 'card emulation for personal access cards' feature — i.e. the X3 may be meant to receive a cloned access card including its UID
- NDEF on MIFARE Classic (the X3 and the transparent sticker) requires formatting per the NFC Forum / NXP MIFARE Classic NDEF mapping: MAD in sector 0 with key A A0A1A2A3A4A5 and NDEF sectors with key A D3F7D3F7D3F7 and NDEF access bits — i.e. writing sector trailers, which c23 forbids without --include-trailers (from memory of NXP AN1304/AN1305, not re-read this session)
- Phone-side verification is Android-only: iPhones cannot read MIFARE Classic NDEF (spokenlikeageek 2026-09-09: NFC Tools on iPhone can't read/write the X3; crosspoint discussion 2259: iPhone unreliable), so 'tap the X3 with a phone' success means Android

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
- `s33` — `User's spare tags (2026-11-08 live scan)`: transparent sticker = MIFARE Classic 1K, UID 3d100a1e, blank, key A FFFFFFFFFFFF, block 0 manufacturer bytes 'bcdefghi' (same clone signature as the X3) — a valid byte-copy target for X3 data blocks; the card and the spot sticker were not detected with default polling (14443A/B, FeliCa, ISO15693) nor with all types enabled (Topaz, SRI, Innovatron, Picopass, CTS) — likely 125 kHz/UHF, out of the ACR1552's reach; reader polling type restored to factory 07 05 after a failed in-process restore
  - seeds: `c21`, `c27`
- `s34` — `challenge pass / concurrency lens: two-process claim probe (scratchpad hold.py + second.py)`: process A holding `claim_interface`(0) makes process B fail with \[Errno 16\] Resource busy — CLI, MCP and ui serve contend for one reader
  - seeds: `c43`
- `s35` — `challenge pass / data-loss + failure-mode lens: X3 coupling dropouts (s30) + restore semantics (c24)`: a write sequence on a tag that drops out mid-run leaves a mixed old/new card; nothing in the frame captured pre-write backup or partial-write recovery
  - seeds: `c44`, `c45`
- `s36` — `challenge pass / unstated-assumptions lens: c24 (NDEF writes to X3) vs c23 (trailer protection)`: NDEF-on-Classic needs trailer writes; the frame confirmed both 'write NDEF to the X3' and 'never write trailers' without noticing the conflict
  - seeds: `c46`
- `s37` — `challenge pass / security lens: dump format (c35) + scripts/scan-secrets.py`: dump JSON carries 'keys used' and trailer bytes; scan-secrets targets JSON config URLs/tokens, not MIFARE keys
  - seeds: `c47`
- `s38` — `challenge pass / adjacent-systems lens: udevadm info /dev/bus/usb/004/002 + loginctl`: TAGS=:security-device:seat:systemd:uaccess:, `ID_FOR_SEAT` set; one active session arduino seat0 tty2 — access is session-bound, not group-bound
  - seeds: `c3`
- `s39` — `challenge pass / reversibility lens: ACR1552 manual 6.1.6 + 6.1.14 (scratchpad acs/acr1552.txt)`: polling type and keyboard/operating mode are persistent reader settings; operating mode 00h = Only HID Keyboard (default 01h Only CCID); interface 2 is bound to usbhid today
  - seeds: `c48`
- `s40` — `challenge pass / observability lens: frame claims c7, c27`: errors map to exit codes but nothing lets an operator see what was sent to the tag when a write misbehaves
  - seeds: `c49`
- `s41` — `challenge pass / overlooked-actors lens: phone users of the X3 tag`: frame assumed generic phone readers; research says iPhone support for the X3's Classic tag is partial/unreliable
  - seeds: `c50`
- `s42` — `challenge pass / operations lens: embodiment hatch_build.py (staleness-aware npm ci && npm run build, NPM_CI_TIMEOUT_S 600)`: a wheel/editable build runs npm: CI test job, publish.yml and every 'uv sync' on the board need Node on PATH or a skip path — a how-to-build risk for the plan, not a spec change
- `s43` — `challenge pass / security lens: web UI + MCP (c18, c19, c28)`: loopback bind + install secret + Origin/Host allow-list cover browser CSRF/DNS rebinding; examined and kept; residual: any local process can read the 0600 secret as the same user — accepted, same as embodiment
- `s44` — `challenge pass / migration lens: none`: no persisted state to migrate: dumps/journals are new files; frame has no schema migration — examined, clean
- `s45` — `challenge pass / adjacent-systems lens: ACR1552 manual 1.09 lines 119-121, 1955-1964, 3185-3245`: reader supports ISO14443 A/B, ISO15693, ISO18092, MIFARE, FeliCa, NFC tags, SRI/SRIX, CTS, Innovatron, Picopass, Topaz; Picopass ATRs carry SS 06; block-address tables exist for SRIX/PicoPass/CTS — c34 previously omitted Type B and 18092
  - seeds: `c34`
- `s46` — `challenge pass / reversibility lens: ACR1552 manual §6.1.15 Card Emulation`: emulation modes 02h Type 2 Tag (lock bytes unsupported, UID programmable) and 03h FeliCa; must enter Card Read/Write mode before switching — a mode the CLI must never leave the reader stuck in
  - seeds: `c51` (rejected)

## Decisions

- User decision 2026-11-08: confirmed all proposed claims/honesty conditions except duplicate c15 (rejected); card cloning onto the X3 is deferred as a follow-up
- User decisions 2026-11-08 (challenge pass): confirm all challenge findings, honesty conditions, obligations and lapse l1; ndef format = sole trailer-write exception via fixed NFC-Forum template; trailer-writing MCP/API tools need apply=true plus `include_trailers`/format=true and are logged (parity kept); card emulation parked as follow-up

## Hard questions

- contradiction with c23? (resolved: User 2026-11-08: yes — ndef format may write sector trailers only from the fixed, validated NFC-Forum template (MAD + NDEF keys/access bits, parity-checked before sending); arbitrary trailer writes still need --include-trailers)
- May 'ndef format' write sector trailers using only a fixed, validated NFC-Forum template (MAD + NDEF keys/access bits, access-bit parity checked before sending), as the single sanctioned exception to c23?

## Open parks

- [unknown_nonblocking] Whether headless Chromium can run on this aarch64 board without root-installed system libraries (needed for playwright verification)
- [unknown_nonblocking] The Tong Card (NFC-powered e-ink display, 428x428 red/yellow/black/white) is a separate device with unknown chip/protocol; image push to it is out of this frame unless the user says that's the target
- [unknown_nonblocking] Whether disabling the reader's RF-off interval (polling option bit 1, default 8Bh) improves detection of the weakly coupled X3 — cheap probe during build
- [follow_up] Clone other cards (e.g. access cards incl. UID block 0) onto the X3's suspected magic MIFARE Classic tag: gen1a/CUID detection + guarded block-0 write
- [follow_up] Build hook needs Node wherever the wheel is built (CI tests, publish, uv sync on the board); route as a plan risk with a skip-when-dist-fresh path
- [follow_up] Reader card emulation ('nfc-cli emulate type2|felica', ACR1552 §6.1.15, programmable UID; must always return to read/write mode) — deferred by user 2026-11-08
