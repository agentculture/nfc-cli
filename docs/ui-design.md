# Web console UI design

Direction for nfc-cli's local web console (`web/`), chosen in plan task t15
with the frontend-design skill loaded. Task t15 ships only the shell and one
placeholder route; this document is the brief later screens (task t16)
follow.

## Subject, audience, job

- **Subject:** one NFC reader (ACS ACR1552) on the desk and the tag lying on
  it.
- **Audience:** the person at that desk, plus agents driving the page through
  Playwright.
- **Primary job:** show exactly what is on the reader (UID, family, memory
  map) and make any write to it deliberate and reversible.

## Direction: the tag as specimen

The hero object is the tag itself, drawn as a physical card: an outline at
ISO/IEC 7810 ID-1 proportions (85.60 × 53.98 mm) whose face is the tag's
memory map. For a MIFARE Classic 1K that is 16 sectors × 4 blocks. The page
reads like a specimen on a light table: the tag on one side, plain-language
facts about it on the other.

- **The one bold element** is the tag outline and its memory map. Everything
  else (header, text, buttons) stays quiet so the tag carries the page.
- **Structure carries information, not decoration.** The grid really is the
  tag's memory layout. Sector trailers (keys and access bits) are told apart
  from data blocks by **shape** (a solid, heavier rule versus a dashed
  outline), never by colour alone. Later screens keep that rule for
  read-only, locked or changed blocks.
- **State in words.** Reader and tag presence are sentences ("ACS ACR1552 is
  connected.", "No tag on the reader.") in `aria-live` regions. An empty
  outline means "no tag"; a solid accent outline means "tag present".
- **Safety-first writes (task t16).** Writes are never a single click: a diff
  preview, then a confirmation that names the tag UID and the blocks being
  changed. Trailer writes get an extra gate. Write controls sit apart from
  read controls.
- **Motion:** one motion only. The empty outline breathes slowly while
  waiting for a tag. It is gated behind `prefers-reduced-motion:
  no-preference`, and the tokens' reduced-motion kill switch removes it too.
- **Layout:** text column on the left, tag on the right on wide screens; the
  tag stacks first on narrow screens. Content is left-aligned on the tokens'
  `shell-rail`.

## Colour and type: culture-design tokens only

The console uses **only culture-design tokens for colour and type**. The
tokens are `web/src/culture-design/tokens.css`, a verbatim copy of
agentculture/org `site-astro/src/styles/global.css` at the pinned commit (see
that folder's README; `scripts/check-culture-design.py` guards it).

- Colour: every colour in `web/src/styles/app.css` is a `var(--…)` token
  (`--ink`, `--ink-soft`, `--surface`, `--accent`, `--accent-strong`,
  `--on-accent`, `--line`, `--line-soft`, `--mesh-thread`). No literal colour
  values. Dark mode is whatever the tokens' `prefers-color-scheme: dark`
  block defines; there is no toggle.
- Type: `--font-display` (Fraunces, self-hosted) for headings and the
  wordmark, `--font-body` (Albert Sans, self-hosted) for everything else,
  `--font-mono` reserved for hex data (UIDs, block bytes) in later screens.
  Sizes, leading and tracking come from the `--ac-type-*`, `--ac-leading-*`
  and `--ac-tracking-*` tokens.
- `web/src/styles/tokens-only.test.ts` fails if `app.css` gains a literal
  colour or a font family that isn't one of the font tokens.

## Accessibility baseline

- A skip link is the first focusable element and moves focus to `<main>`.
- Visible focus comes from the tokens' `:focus-visible` outline.
- Every interactive control is at least 44 × 44 px.
- `prefers-reduced-motion` is honoured.
- Playwright e2e runs axe (`@axe-core/playwright`) and fails on any serious
  or critical violation.

## Agent-state

The root renders `<script type="application/json" id="agent-state">` with
`status`, `view_ready`, `route`, `reader`, `card` and `errors`. Agents and
the e2e suite read it and wait for `view_ready: true` instead of scraping the
page.
