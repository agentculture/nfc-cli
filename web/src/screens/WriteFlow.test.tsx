import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WriteFlow } from "./WriteFlow";

type Body = Record<string, unknown>;

function diffResult(body: Body, rows: Body[]) {
  return {
    ok: true,
    result: {
      uid: "cb30c560",
      family: "classic_1k",
      applied: body.apply === true,
      diff: rows,
      summary: { write: rows.filter((r) => r.action === "write").length, unchanged: 0, skip: 0 },
      snapshot: body.apply ? "/data/snap.json" : null,
      journal: null,
      verified: body.apply ? rows.length : 0,
      protected_blocks_written: [],
    },
  };
}

function stubApi(handler: (path: string, body: Body) => unknown) {
  const calls: { path: string; body: Body }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init: RequestInit) => {
      const body = JSON.parse(String(init.body)) as Body;
      calls.push({ path, body });
      return new Response(JSON.stringify(handler(path, body)), { status: 200 });
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

const TRAILER = "************ff078069************";
const TRAILER_KEYS = "a0a1a2a3a4a5ff078069b0b1b2b3b4b5";

describe("WriteFlow: preview first, then an explicit Apply", () => {
  it("dry-runs first and only applies on an explicit click with the same params", async () => {
    const calls = stubApi((_p, body) =>
      diffResult(body, [{ block: 4, old: "00".repeat(16), new: "11".repeat(16), action: "write" }]),
    );
    const onApplied = vi.fn();
    render(
      <WriteFlow noun="tag" verb="write" only={["block", "data"]} cardUid="cb30c560" previewLabel="Preview write" onApplied={onApplied} />,
    );
    expect(screen.queryByRole("button", { name: /write 1 block/i })).toBeNull();
    await userEvent.type(screen.getByLabelText(/^Block/), "4");
    await userEvent.type(screen.getByLabelText(/^Data/), "11".repeat(16));
    await userEvent.click(screen.getByRole("button", { name: "Preview write" }));
    const apply = await screen.findByRole("button", { name: "Write 1 block to tag cb30c560" });
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ block: 4, data: "11".repeat(16) });
    expect(screen.getByRole("table", { name: /changes/i })).toBeInTheDocument();
    expect(screen.getByText(/will be overwritten/i)).toHaveTextContent(
      /block 4 will be overwritten on tag cb30c560/i,
    );
    await userEvent.click(apply);
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[1].body).toEqual({ block: 4, data: "11".repeat(16), apply: true, expect_uid: "cb30c560" });
    expect(await screen.findByRole("status")).toHaveTextContent(/wrote 1 block/i);
    expect(onApplied).toHaveBeenCalled();
  });

  it("needs the extra trailer toggle before a trailer-writing action can apply", async () => {
    const calls = stubApi((_p, body) =>
      diffResult(body, [
        { block: 1, old: "00".repeat(16), new: "14".repeat(16), action: "write" },
        { block: 3, old: TRAILER, new: "************787788c1************", action: "write" },
      ]),
    );
    render(<WriteFlow noun="ndef" verb="format" cardUid="cb30c560" previewLabel="Preview format" />);
    await userEvent.click(screen.getByRole("button", { name: "Preview format" }));
    const apply = await screen.findByRole("button", { name: "Write 2 blocks to tag cb30c560" });
    expect(apply).toBeDisabled();
    expect(screen.getByText(/rewrites sector trailers/i)).toBeInTheDocument();
    const toggle = screen.getByRole("checkbox", { name: /allow writes to sector trailers/i });
    expect(toggle).not.toBeChecked();
    await userEvent.click(toggle);
    await waitFor(() => expect(calls.at(-1)!.body).toEqual({ include_trailers: true }));
    await waitFor(() => expect(screen.getByRole("button", { name: /write 2 blocks/i })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: /write 2 blocks/i }));
    await waitFor(() =>
      expect(calls.at(-1)!.body).toEqual({ include_trailers: true, apply: true, expect_uid: "cb30c560" }),
    );
  });

  it("never shows key bytes unless the user reveals them", async () => {
    const calls = stubApi((_p, body) =>
      diffResult(body, [
        { block: 3, old: body.include_keys ? TRAILER_KEYS : TRAILER, new: body.include_keys ? TRAILER_KEYS : TRAILER, action: "write" },
      ]),
    );
    render(<WriteFlow noun="ndef" verb="format" cardUid="cb30c560" previewLabel="Preview format" />);
    await userEvent.click(screen.getByRole("button", { name: "Preview format" }));
    const table = await screen.findByRole("table", { name: /changes/i });
    expect(table.textContent).not.toContain("a0");
    expect(within(table).getAllByText(/key hidden/i).length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("checkbox", { name: /show key bytes/i }));
    await waitFor(() => expect(calls.at(-1)!.body).toEqual({ include_keys: true }));
    await waitFor(() => expect(screen.getByRole("table", { name: /changes/i }).textContent).toContain("a0"));
  });

  it("refuses to apply once a different tag is on the reader", async () => {
    stubApi((_p, body) =>
      diffResult(body, [{ block: 4, old: "00".repeat(16), new: "11".repeat(16), action: "write" }]),
    );
    const { rerender } = render(
      <WriteFlow noun="ndef" verb="erase" cardUid="cb30c560" previewLabel="Preview erase" />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Preview erase" }));
    await screen.findByRole("button", { name: /write 1 block/i });
    rerender(<WriteFlow noun="ndef" verb="erase" cardUid="deadbeef" previewLabel="Preview erase" />);
    expect(screen.queryByRole("button", { name: /write 1 block/i })).toBeNull();
    expect(screen.getByText(/preview again/i)).toBeInTheDocument();
  });

  it("shows a refused preview as an error with its remediation", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({ ok: false, error: { message: "data is not valid hex: 'zz'", remediation: "check the arguments", kind: "user" } }),
          { status: 400 },
        ),
      ),
    );
    render(<WriteFlow noun="tag" verb="write" only={["block", "data"]} cardUid="cb30c560" previewLabel="Preview write" />);
    await userEvent.type(screen.getByLabelText(/^Block/), "4");
    await userEvent.type(screen.getByLabelText(/^Data/), "zz");
    await userEvent.click(screen.getByRole("button", { name: "Preview write" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("data is not valid hex");
    expect(alert).toHaveTextContent("check the arguments");
  });
});
