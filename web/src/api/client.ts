// Client for nfc-cli's local server (nfc_cli/web/server.py):
//   GET /api/state, GET /api/events (SSE "card"), POST /api/<noun>/<verb>.
// POSTs carry the install secret the server injects as <meta name="nfc-cli-secret">.
// Every call is recorded in an in-memory trace (the console's trace drawer).

export interface ApiError {
  message: string;
  remediation: string;
  kind: "user" | "env";
  status?: number;
}

export type Result = Record<string, unknown>;
export type VerbResponse<T = Result> = { ok: true; result: T } | { ok: false; error: ApiError };

export interface ReaderInfo {
  connected: boolean;
  name?: string;
  firmware?: string | null;
  error?: string | null;
  [key: string]: unknown;
}

export interface CardInfo {
  present?: boolean;
  uid?: string | null;
  family?: string | null;
  [key: string]: unknown;
}

export interface TraceEntry {
  id: number;
  at: string;
  method: "GET" | "POST";
  path: string;
  params?: Record<string, unknown>;
  status: number | null;
  ok: boolean;
  ms: number;
  summary: string;
}

const UNREACHABLE: ApiError = {
  message: "Can't reach the nfc-cli server.",
  remediation: "Start it with `nfc-cli ui serve`, then try again.",
  kind: "env",
};

let trace: TraceEntry[] = [];
let nextId = 1;
const listeners = new Set<(t: TraceEntry[]) => void>();

export function getTrace(): TraceEntry[] {
  return trace;
}

export function clearTrace(): void {
  trace = [];
  listeners.forEach((fn) => fn(trace));
}

export function onTrace(fn: (t: TraceEntry[]) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function record(entry: Omit<TraceEntry, "id" | "at">): void {
  trace = [{ id: nextId++, at: new Date().toISOString(), ...entry }, ...trace].slice(0, 200);
  listeners.forEach((fn) => fn(trace));
}

/** Key material the user typed never reaches the trace. */
function maskParams(params: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(params)) out[k] = k === "key" ? "(hidden)" : v;
  return out;
}

function summarize(res: VerbResponse): string {
  if (!res.ok) return res.error.message;
  const r = res.result;
  const bits: string[] = [];
  if (typeof r.uid === "string") bits.push(`UID ${r.uid}`);
  if (Array.isArray(r.diff)) bits.push(`${r.diff.length} block(s) in diff`);
  if (r.applied === true) bits.push("applied");
  else if (r.applied === false) bits.push("dry run");
  if (r.blocks && typeof r.blocks === "object") bits.push(`${Object.keys(r.blocks).length} block(s)`);
  return bits.join(", ") || "ok";
}

export function readSecret(doc: Document = document): string | null {
  return doc.querySelector<HTMLMetaElement>('meta[name="nfc-cli-secret"]')?.content || null;
}

export async function callVerb<T = Result>(
  noun: string,
  verb: string,
  params: Record<string, unknown>,
): Promise<VerbResponse<T>> {
  const path = `/api/${noun}/${verb}`;
  const started = performance.now();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json",
  };
  const secret = readSecret();
  if (secret) headers.Authorization = `Bearer ${secret}`;
  let status: number | null = null;
  let res: VerbResponse;
  try {
    const http = await fetch(path, { method: "POST", headers, body: JSON.stringify(params) });
    status = http.status;
    let body: unknown = null;
    try {
      body = await http.json();
    } catch {
      body = null;
    }
    const b = (body ?? {}) as { ok?: boolean; result?: Result; error?: unknown };
    if (http.ok && b.ok === true) {
      res = { ok: true, result: b.result ?? {} };
    } else {
      const e = (typeof b.error === "object" && b.error) || {};
      const err = e as Partial<ApiError>;
      res = {
        ok: false,
        error: {
          message:
            err.message ?? (typeof b.error === "string" ? b.error : `HTTP ${http.status} from ${path}`),
          remediation: err.remediation ?? "Check the nfc-cli server log.",
          kind: err.kind === "user" ? "user" : "env",
          status: http.status,
        },
      };
    }
  } catch {
    res = { ok: false, error: { ...UNREACHABLE } };
  }
  record({
    method: "POST",
    path,
    params: maskParams(params),
    status,
    ok: res.ok,
    ms: Math.round(performance.now() - started),
    summary: summarize(res),
  });
  return res as VerbResponse<T>;
}

export interface ApiState {
  reader: ReaderInfo | null;
  card: CardInfo | null;
}

/** A card the server reports as absent becomes null: the view has one notion of "no tag". */
export function normalizeCard(card: CardInfo | null | undefined): CardInfo | null {
  if (!card || card.present === false) return null;
  return card;
}

export async function getState(): Promise<ApiState> {
  const started = performance.now();
  let status: number | null = null;
  try {
    const res = await fetch("/api/state", { headers: { Accept: "application/json" } });
    status = res.status;
    if (!res.ok) throw new Error(`GET /api/state returned HTTP ${res.status}`);
    const body = (await res.json()) as { reader?: ReaderInfo | null; card?: CardInfo | null };
    record({ method: "GET", path: "/api/state", status, ok: true, ms: Math.round(performance.now() - started), summary: "ok" });
    return { reader: body.reader ?? null, card: normalizeCard(body.card) };
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    record({ method: "GET", path: "/api/state", status, ok: false, ms: Math.round(performance.now() - started), summary: detail });
    throw err;
  }
}

export interface CardEvent {
  present: boolean;
  uid: string | null;
}

/** Subscribe to card presence changes (SSE). A no-op where EventSource is missing (tests). */
export function subscribeCard(onCard: (e: CardEvent) => void): () => void {
  if (typeof EventSource === "undefined") return () => {};
  const source = new EventSource("/api/events");
  const handler = (ev: MessageEvent<string>) => {
    try {
      const data = JSON.parse(ev.data) as CardEvent;
      onCard({ present: data.present === true, uid: data.uid ?? null });
    } catch {
      /* ignore a malformed event */
    }
  };
  source.addEventListener("card", handler as EventListener);
  return () => {
    source.removeEventListener("card", handler as EventListener);
    source.close();
  };
}
