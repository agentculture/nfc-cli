import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Console from "./Console";

function agentState() {
  return JSON.parse(document.getElementById("agent-state")!.textContent ?? "null");
}

const blocks: Record<string, string> = {};
for (let i = 0; i < 64; i++) blocks[i] = i % 4 === 3 ? "************ff078069************" : "00".repeat(16);

function stub(routes: Record<string, { status?: number; body: unknown }>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      const r = routes[path] ?? { status: 404, body: { error: "no mock" } };
      return new Response(JSON.stringify(r.body), { status: r.status ?? 200 });
    }),
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("Console", () => {
  it("announces the tag in words, reads it, and mirrors it into agent-state", async () => {
    stub({
      "/api/state": {
        body: {
          reader: { connected: true, firmware: "ACR1552 1.02", error: null },
          card: { present: true, uid: "cb30c560", family: "classic_1k" },
        },
      },
      "/api/tag/info": { body: { ok: true, result: { uid: "cb30c560", family: "classic_1k", sectors: 16, blocks: 64 } } },
      "/api/tag/read": { body: { ok: true, result: { uid: "cb30c560", family: "classic", blocks } } },
      "/api/ndef/read": { body: { ok: true, result: { empty: true, raw: "", records: [] } } },
    });
    render(<Console />);
    expect(await screen.findByText("MIFARE Classic 1K tag present, UID cb30c560.")).toHaveAttribute(
      "aria-live",
      "polite",
    );
    expect(screen.getByText(/is connected \(firmware ACR1552 1.02\)/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByRole("button", { name: /^Sector \d+:/ })).toHaveLength(16));
    await waitFor(() => expect(agentState().tag).toMatchObject({ uid: "cb30c560", blocks_read: 64 }));
    expect(agentState().card).toMatchObject({ present: true, uid: "cb30c560" });
    expect(agentState().errors).toEqual([]);
    expect(screen.getByRole("tab", { name: "Sectors" })).toHaveAttribute("aria-selected", "true");
  });

  it("treats the server's absent card as no tag", async () => {
    stub({
      "/api/state": {
        body: { reader: { connected: true, firmware: null, error: null }, card: { present: false, uid: null, family: null } },
      },
    });
    render(<Console />);
    await waitFor(() => expect(agentState().view_ready).toBe(true));
    expect(agentState().card).toBeNull();
    expect(screen.getByText("No tag on the reader.")).toBeInTheDocument();
    expect(screen.queryByRole("tablist")).toBeNull();
  });

  it("puts a busy reader into words and into agent-state errors", async () => {
    stub({
      "/api/state": {
        body: {
          reader: { connected: true, firmware: null, error: "the reader is in use by another process" },
          card: { present: false, uid: null, family: null },
        },
      },
    });
    render(<Console />);
    expect(await screen.findByText(/connected but busy: the reader is in use by another process/)).toBeInTheDocument();
    expect(agentState().errors).toEqual(["Reader: the reader is in use by another process"]);
  });

  it("reports a failed tag read with its remediation", async () => {
    stub({
      "/api/state": { body: { reader: { connected: true }, card: { present: true, uid: "cb30c560", family: "classic_1k" } } },
      "/api/tag/info": {
        status: 503,
        body: { ok: false, error: { message: "the card was removed during the operation", remediation: "hold the tag still on the reader and retry", kind: "env" } },
      },
    });
    render(<Console />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("the card was removed during the operation");
    expect(alert).toHaveTextContent("hold the tag still");
    expect(agentState().last_error).toMatchObject({ kind: "env" });
  });
});
