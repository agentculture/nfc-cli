import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { callVerb, clearTrace, getTrace, normalizeCard, readSecret } from "./client";

function stubFetch(status: number, body: unknown) {
  const fn = vi.fn(async () => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fn);
  return fn;
}

beforeEach(() => {
  clearTrace();
  const meta = document.createElement("meta");
  meta.name = "nfc-cli-secret";
  meta.content = "s3cret";
  document.head.appendChild(meta);
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.head.querySelectorAll('meta[name="nfc-cli-secret"]').forEach((m) => m.remove());
});

describe("api client", () => {
  it("reads the install secret injected by the server", () => {
    expect(readSecret()).toBe("s3cret");
  });

  it("POSTs JSON params with the bearer secret to the registry route", async () => {
    const fetchFn = stubFetch(200, { ok: true, result: { uid: "cb30c560" } });
    const res = await callVerb("tag", "uid", { wait: 2 });
    expect(res).toEqual({ ok: true, result: { uid: "cb30c560" } });
    const [url, init] = fetchFn.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/tag/uid");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer s3cret");
    expect(JSON.parse(init.body as string)).toEqual({ wait: 2 });
  });

  it("passes server errors through with message, remediation and kind", async () => {
    stubFetch(409, {
      ok: false,
      error: { message: "the reader is in use", remediation: "wait", kind: "env" },
    });
    const res = await callVerb("tag", "read", {});
    expect(res).toEqual({
      ok: false,
      error: { message: "the reader is in use", remediation: "wait", kind: "env", status: 409 },
    });
  });

  it("turns a network failure into an env error in words", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    const res = await callVerb("tag", "read", {});
    expect(res.ok).toBe(false);
    if (!res.ok) {
      expect(res.error.kind).toBe("env");
      expect(res.error.message).toMatch(/can't reach the nfc-cli server/i);
    }
  });

  it("records every call in the trace, masking any key the user typed", async () => {
    stubFetch(200, { ok: true, result: {} });
    await callVerb("tag", "read", { key: "a0a1a2a3a4a5", block: 4 });
    const [entry] = getTrace();
    expect(entry).toMatchObject({ method: "POST", path: "/api/tag/read", status: 200, ok: true });
    expect(JSON.stringify(entry)).not.toContain("a0a1a2a3a4a5");
    expect(entry.params).toEqual({ key: "(hidden)", block: 4 });
  });
});

describe("normalizeCard", () => {
  it("maps an absent card (server shape) to null", () => {
    expect(normalizeCard({ present: false, uid: null, family: null })).toBeNull();
    expect(normalizeCard(null)).toBeNull();
  });
  it("keeps a present card", () => {
    expect(normalizeCard({ present: true, uid: "cb30c560", family: "classic_1k" })).toEqual({
      present: true,
      uid: "cb30c560",
      family: "classic_1k",
    });
  });
});
