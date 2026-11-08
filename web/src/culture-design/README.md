# culture-design

The web console's design layer: agentculture/org's site design tokens,
extracted at a pinned commit. The extraction pattern, the header format and
the checker are cited (cite-don't-import) from agentculture/embodiment
(`web/src/culture-design/`, `scripts/check-culture-design.py`, embodiment task
t17), which in turn follows culture-nodes
(`docs/adr/0001-culture-design-source.md` in that repo).

nfc-cli never imports from the org repo or from embodiment at build or run
time — everything needed is copied here, once, at one pinned commit.

## Pinned commit

```text
org repo:     agentculture/org
pin:          b4d939ba0aa354a5ae53065319a773e0013de698
source path:  site-astro/src/styles/global.css
sha256:       8a37c0de9deb121ae119a75e51ebf14642d9ca22666b0c62a57aa7d79f974474
```

The pin is the one embodiment uses. The sha256 is of the org file at that pin
(12562 bytes), recorded on 2026-11-08 and cross-checked against GitHub with:

```bash
gh api "repos/agentculture/org/contents/site-astro/src/styles/global.css?ref=b4d939ba0aa354a5ae53065319a773e0013de698" \
  -H "Accept: application/vnd.github.raw" | sha256sum
```

## Contents

- `tokens.css` — a provenance header comment followed by `global.css` copied
  **verbatim** (color tokens for light and `@media (prefers-color-scheme:
  dark)`, type scale, layout rails, motion easings, and the reduced-motion
  kill switch). Everything after the header comment's closing `*/` line is
  byte-identical to the pinned org source; do not hand-edit it.

Fonts are not copied: the console self-hosts the two faces `tokens.css` names
in `--font-display` / `--font-body` via npm (`@fontsource-variable/fraunces`,
`@fontsource-variable/albert-sans`), imported once in `src/main.tsx`. No CDN,
no remote font host.

## Verification

`scripts/check-culture-design.py` (repo root) works offline: it checks the
README pin against the `tokens.css` header pin and the sha256 of the copied
body against the digest above, so a one-byte edit fails. When
`CULTURE_DESIGN_ORG_REPO` points at a local org checkout it also byte-compares
the body with `git show <pin>:site-astro/src/styles/global.css`.

```bash
uv run python scripts/check-culture-design.py
```

## Re-pin procedure

1. Pick the new org commit.
2. Re-copy `site-astro/src/styles/global.css` at that commit into `tokens.css`
   below the header, updating the header's `Pinned commit:` line.
3. Update `pin:` and `sha256:` above (`sha256sum` of the org file).
4. Run `uv run python scripts/check-culture-design.py`.

## License note

The org repo is Apache-2.0, as is nfc-cli. Apache-2.0 grants no trademark
license (§6); "AgentCulture" as a brand is not licensed by this extraction.

## Dark mode

Dark values live entirely under `@media (prefers-color-scheme: dark)`; there
is no theme toggle, matching the org site.
