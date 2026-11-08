// Mocked nfc-cli API for e2e: every /api/* request is answered by Playwright
// request interception, so no Python server and no reader hardware are
// involved (pattern from culture-rules e2e/fixtures/api.ts). Response shapes
// mirror nfc_cli/web/server.py and the verbs' real results against the fake
// transports in nfc_cli/nfc/fake.py (x3 = blank MIFARE Classic 1K, ntag215).
import type { Page, Route } from "@playwright/test";

export type Body = Record<string, unknown>;
export interface Reply {
  status?: number;
  body: unknown;
}
export type VerbHandler = (body: Body) => Reply;

export interface ApiScenario {
  state: unknown;
  status?: number;
  /** SSE "card" events sent on /api/events, in order. */
  events?: { present: boolean; uid: string | null }[];
  /** Factory so every test gets fresh per-tag state (formatted, written blocks, …). */
  verbs?: () => Record<string, VerbHandler>;
}

export interface Captured {
  path: string;
  body: Body;
  headers: Record<string, string>;
}

const ok = (result: unknown): Reply => ({ body: { ok: true, result } });
const fail = (status: number, kind: "user" | "env", message: string, remediation: string): Reply => ({
  status,
  body: { ok: false, error: { message, remediation, kind } },
});

// ---------------------------------------------------------------- tag data

export const X3_UID = "cb30c560";
const X3_ATR = "3b8f8001804f0ca000000306030001000000006a";
const ZERO16 = "00".repeat(16);
/** Transport keys of a blank tag; only ever sent when the request asks for include_keys. */
export const X3_KEY = "ffffffffffff";

function trailer(access: string, keys: boolean): string {
  return keys ? `${X3_KEY}${access}${X3_KEY}` : `${"*".repeat(12)}${access}${"*".repeat(12)}`;
}

function x3Blocks(keys: boolean, image: Record<number, string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (let b = 0; b < 64; b++) {
    if (b % 4 === 3) out[b] = trailer(image[b] ?? "ff078069", keys);
    else out[b] = image[b] ?? ZERO16;
  }
  out[0] = "cb30c5605e0804006263646566676869";
  return out;
}

function diffRow(block: number, oldHex: string, newHex: string, applied: boolean): Body {
  return {
    block,
    old: oldHex,
    new: newHex,
    action: oldHex === newHex ? "unchanged" : "write",
    ...(applied ? { verified: true } : {}),
  };
}

function writeResult(
  uid: string,
  family: string,
  op: string,
  diff: Body[],
  applied: boolean,
  extra: Body = {},
) {
  const writes = diff.filter((d) => d.action === "write").length;
  return {
    uid,
    family,
    op,
    applied,
    diff,
    summary: { write: writes, unchanged: diff.length - writes, skip: 0 },
    snapshot: applied ? `/home/user/.local/share/nfc-cli/snapshots/${uid}-20261108T120000Z.json` : null,
    journal: applied ? `/home/user/.local/state/nfc-cli/journals/${uid}-20261108T120000Z.journal` : null,
    verified: applied ? writes : 0,
    protected_blocks_written: [],
    ...extra,
  };
}

const NOT_NDEF = (): Reply =>
  fail(
    503,
    "env",
    "no valid MAD in sector 0: the tag is not NDEF formatted",
    "run `nfc-cli ndef format --apply` first (MIFARE Classic)",
  );

function x3Verbs(): Record<string, VerbHandler> {
  // Data blocks hold hex; trailers hold only their access bits (keys are the transport keys).
  const image: Record<number, string> = {};
  let formatted = false;
  const read = (keys: boolean) => x3Blocks(keys, image);

  return {
    "tag/info": () =>
      ok({
        atr: X3_ATR,
        uid: X3_UID,
        family: "classic_1k",
        standard: "ISO14443A-3",
        checksum_ok: true,
        sectors: 16,
        blocks: 64,
      }),
    "tag/uid": () => ok({ uid: X3_UID, family: "classic_1k" }),
    "tag/read": (b) => {
      const all = read(b.include_keys === true);
      if (typeof b.block !== "number") {
        return ok({ atr: X3_ATR, uid: X3_UID, family: "classic", blocks: all });
      }
      const count = typeof b.count === "number" ? b.count : 1;
      const blocks: Record<string, string> = {};
      for (let i = b.block; i < b.block + count; i++) blocks[i] = all[i];
      return ok({ uid: X3_UID, family: "classic_1k", blocks });
    },
    "ndef/read": () =>
      formatted ? ok({ uid: X3_UID, family: "classic_1k", empty: true, raw: "", records: [] }) : NOT_NDEF(),
    "ndef/format": (b) => {
      const apply = b.apply === true;
      if (apply && b.include_trailers !== true) {
        return fail(
          400,
          "user",
          "ndef format writes sector trailers: over api it needs include_trailers=true as well as apply=true",
          "pass include_trailers=true together with apply=true to format the tag",
        );
      }
      const keys = b.include_keys === true;
      const before = read(keys);
      const diff: Body[] = [
        diffRow(1, before[1], "140103e103e103e103e103e103e103e1", apply),
        diffRow(2, before[2], "03e103e103e103e103e103e103e103e1", apply),
        diffRow(3, before[3], trailer("787788c1", keys), apply),
        diffRow(4, before[4], "0300fe00000000000000000000000000", apply),
      ];
      for (let s = 1; s < 16; s++) {
        diff.push(diffRow(s * 4 + 3, before[s * 4 + 3], trailer("7f078840", keys), apply));
      }
      if (apply) {
        formatted = true;
        image[1] = "140103e103e103e103e103e103e103e1";
        image[2] = "03e103e103e103e103e103e103e103e1";
        image[3] = "787788c1";
        image[4] = "0300fe00000000000000000000000000";
        for (let s = 1; s < 16; s++) image[s * 4 + 3] = "7f078840";
      }
      const extra = apply
        ? {
            protected_blocks_written: Array.from({ length: 16 }, (_, s) => s * 4 + 3),
            audit_log: "/home/user/.local/state/nfc-cli/audit.log",
          }
        : {};
      return ok(writeResult(X3_UID, "classic_1k", "ndef_format", diff, apply, extra));
    },
    "ndef/write": (b) => {
      if (!formatted) return NOT_NDEF();
      const apply = b.apply === true;
      const diff = [diffRow(4, read(false)[4], "0309d101055402656e6869fe00000000", apply)];
      if (apply) image[4] = "0309d101055402656e6869fe00000000";
      return ok(writeResult(X3_UID, "classic_1k", "ndef_write", diff, apply));
    },
    "ndef/erase": (b) => {
      if (!formatted) return NOT_NDEF();
      const apply = b.apply === true;
      const diff = [diffRow(4, read(false)[4], "0300fe00000000000000000000000000", apply)];
      return ok(writeResult(X3_UID, "classic_1k", "ndef_erase", diff, apply));
    },
    "tag/write": (b) => {
      const block = Number(b.block);
      const data = String(b.data ?? "").toLowerCase();
      if (!/^([0-9a-f]{32})+$/.test(data)) {
        return fail(
          400,
          "user",
          `data is not valid hex: '${data}'`,
          "check the arguments (hex data, block numbers, file paths)",
        );
      }
      const blocks = Array.from({ length: data.length / 32 }, (_, i) => block + i);
      const hit = blocks.filter((x) => x === 0 || x % 4 === 3);
      if (hit.length && b.include_trailers !== true) {
        return fail(
          400,
          "user",
          `refusing to write protected block(s) [${hit.join(", ")}] (sector trailer)`,
          "pass include_trailers / --include-trailers only if you really mean it",
        );
      }
      const apply = b.apply === true;
      const cur = read(false);
      const diff = blocks.map((x, i) => diffRow(x, cur[x], data.slice(i * 32, i * 32 + 32), apply));
      if (apply) blocks.forEach((x, i) => (image[x] = data.slice(i * 32, i * 32 + 32)));
      return ok(writeResult(X3_UID, "classic_1k", "write", diff, apply));
    },
    "tag/dump": () =>
      ok({
        path: `/home/user/.local/share/nfc-cli/dumps/${X3_UID}-20261108T120000Z.json`,
        format: "json",
        blocks_read: 64,
        unreadable_sectors: [],
        complete: true,
        dump: { uid: X3_UID, family: "classic", blocks: read(false) },
      }),
    "tag/restore": (b) => {
      if (!b.file) {
        return fail(400, "user", "missing required argument(s) file", "check the arguments");
      }
      const apply = b.apply === true;
      const cur = read(false);
      // The saved dump holds a text record in blocks 4-5 that the blank tag lacks.
      const saved = ["0311d1010d5402656e68656c6c6f2077", "6f726c64fe0000000000000000000000"];
      const diff = [diffRow(4, cur[4], saved[0], apply), diffRow(5, cur[5], saved[1], apply)];
      return ok(writeResult(X3_UID, "classic_1k", "restore", diff, apply));
    },
  };
}

export const NTAG_UID = "04a1b2c3d4e580";

function ntagVerbs(): Record<string, VerbHandler> {
  const pages: Record<string, string> = { 0: "04a1b29f", 1: "c3d4e580", 2: "72480000", 3: "e1103e00" };
  for (let p = 4; p < 135; p++) pages[p] = "00000000";
  let records: Body[] = [];
  return {
    "tag/info": () =>
      ok({
        atr: "3b8f8001804f0ca0000003060300030000000068",
        uid: NTAG_UID,
        family: "ultralight_ntag",
        standard: "ISO14443A-3",
        checksum_ok: true,
        capability: { version: "1.0", size: 496, read_only: false },
      }),
    "tag/read": () => ok({ uid: NTAG_UID, family: "ultralight", blocks: { ...pages } }),
    "ndef/read": () =>
      ok({ uid: NTAG_UID, family: "ultralight_ntag", empty: records.length === 0, raw: "", records }),
    "ndef/write": (b) => {
      const apply = b.apply === true;
      const news = ["030ad101", "06550478", "2e6f7267", "fe000000"];
      const diff = news.map((n, i) => diffRow(4 + i, pages[4 + i], n, apply));
      if (apply) {
        news.forEach((n, i) => (pages[4 + i] = n));
        records = [{ tnf: 1, type: "U", id: "", payload: "04782e6f7267", uri: String(b.uri ?? "") }];
      }
      return ok(writeResult(NTAG_UID, "ultralight_ntag", "ndef_write", diff, apply, { records }));
    },
    "ndef/erase": (b) =>
      ok(
        writeResult(
          NTAG_UID,
          "ultralight_ntag",
          "ndef_erase",
          [diffRow(4, pages[4], "0300fe00", b.apply === true)],
          b.apply === true,
        ),
      ),
  };
}

// ---------------------------------------------------------------- scenarios

const READER = { connected: true, name: "ACS ACR1552", firmware: "ACR1552 1.05", error: null };

export const scenarios = {
  readerNoCard: {
    state: { reader: { connected: true, name: "ACS ACR1552" }, card: null },
  },
  serverDown: { state: { error: "unavailable" }, status: 503 },
  x3: {
    state: { reader: READER, card: { present: true, uid: X3_UID, family: "classic_1k" } },
    events: [{ present: true, uid: X3_UID }],
    verbs: x3Verbs,
  },
  ntag: {
    state: { reader: READER, card: { present: true, uid: NTAG_UID, family: "ultralight_ntag" } },
    events: [{ present: true, uid: NTAG_UID }],
    verbs: ntagVerbs,
  },
  removedCard: {
    // The last poll saw the tag; it leaves before the read completes.
    state: { reader: READER, card: { present: true, uid: X3_UID, family: "classic_1k" } },
    events: [{ present: false, uid: null }],
    verbs: () => ({
      "tag/info": () =>
        fail(503, "env", "the card was removed during the operation", "hold the tag still on the reader and retry"),
      "tag/read": () =>
        fail(503, "env", "no card on the reader", "place a tag on the reader (or pass --wait SECONDS)"),
    }),
  },
  busyReader: {
    state: {
      reader: { ...READER, error: "the reader is in use by another process" },
      card: { present: true, uid: X3_UID, family: "classic_1k" },
    },
    verbs: () => {
      const busy = () =>
        fail(
          409,
          "env",
          "the reader is in use by another process",
          "wait for the other nfc-cli process to finish, or stop the process holding the reader",
        );
      return { "tag/info": busy, "tag/read": busy, "ndef/read": busy };
    },
  },
} satisfies Record<string, ApiScenario>;

export async function mockApi(page: Page, scenario: ApiScenario): Promise<Captured[]> {
  const captured: Captured[] = [];
  const verbs = scenario.verbs?.() ?? {};
  // The real card-event stream logically follows the first /api/state poll (the UI
  // fetches state, then subscribes). Order the mock the same way so a removal event
  // never races ahead of the initial state the client just loaded — otherwise, under
  // heavy parallel load, the client can process "removed" before it knows the UID.
  let stateServed = false;
  const waitForState = async () => {
    for (let i = 0; i < 400 && !stateServed; i++) {
      await new Promise((r) => setTimeout(r, 5));
    }
    await new Promise((r) => setTimeout(r, 300)); // let the client mount + apply the state first
  };
  await page.route("**/api/**", async (route: Route) => {
    const req = route.request();
    const url = new URL(req.url());
    if (url.pathname === "/api/state") {
      await route.fulfill({
        status: scenario.status ?? 200,
        contentType: "application/json",
        body: JSON.stringify(scenario.state),
      });
      stateServed = true;
      return;
    }
    if (url.pathname === "/api/events") {
      await waitForState();
      // retry: keeps EventSource from reconnecting in a tight loop once the body ends.
      const events = (scenario.events ?? [])
        .map((e) => `event: card\ndata: ${JSON.stringify(e)}\n\n`)
        .join("");
      await route.fulfill({
        status: 200,
        headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-store" },
        body: `retry: 600000\n\n${events}`,
      });
      return;
    }
    const handler = verbs[url.pathname.replace(/^\/api\//, "")];
    if (req.method() === "POST" && handler) {
      const body = (req.postDataJSON() ?? {}) as Body;
      captured.push({ path: url.pathname, body, headers: req.headers() });
      const reply = handler(body);
      await route.fulfill({
        status: reply.status ?? 200,
        contentType: "application/json",
        body: JSON.stringify(reply.body),
      });
      return;
    }
    await route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({
        ok: false,
        error: {
          message: `no mock for ${url.pathname}`,
          remediation: "add one to e2e/fixtures/api.ts",
          kind: "user",
        },
      }),
    });
  });
  return captured;
}

export async function readAgentState(page: Page): Promise<Record<string, unknown>> {
  const text = await page.locator("script#agent-state").textContent();
  return JSON.parse(text ?? "null");
}

export async function waitForViewReady(page: Page): Promise<void> {
  await page.waitForFunction(() => {
    const node = document.getElementById("agent-state");
    if (!node?.textContent) return false;
    return JSON.parse(node.textContent).view_ready === true;
  });
}

/** Wait until the tag on the reader has been read (agent-state tag.blocks_read > 0, not busy). */
export async function waitForTagRead(page: Page): Promise<void> {
  await page.waitForFunction(() => {
    const node = document.getElementById("agent-state");
    if (!node?.textContent) return false;
    const s = JSON.parse(node.textContent);
    return !!s.tag && s.tag.blocks_read > 0 && s.busy === false;
  });
}
