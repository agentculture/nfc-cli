import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

function readAgentState() {
  const node = document.getElementById("agent-state");
  expect(node).not.toBeNull();
  expect(node!.tagName).toBe("SCRIPT");
  expect(node!.getAttribute("type")).toBe("application/json");
  return JSON.parse(node!.textContent ?? "null");
}

function mockFetch(impl: () => Promise<Response>) {
  const fn = vi.fn(impl);
  vi.stubGlobal("fetch", fn);
  return fn;
}

const okState = {
  reader: { connected: true, name: "ACS ACR1552" },
  card: null,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("App shell", () => {
  it("renders agent-state and flips view_ready once /api/state settles", async () => {
    const fetchFn = mockFetch(async () => new Response(JSON.stringify(okState), { status: 200 }));
    render(<App />);
    expect(readAgentState().view_ready).toBe(false);
    await waitFor(() => expect(readAgentState().view_ready).toBe(true));
    const state = readAgentState();
    expect(state.status).toBe("ready");
    expect(state.route).toBe("/");
    expect(state.reader).toEqual(okState.reader);
    expect(state.card).toBeNull();
    expect(state.errors).toEqual([]);
    expect(fetchFn).toHaveBeenCalledWith("/api/state", expect.anything());
  });

  it("states reader and card presence in words", async () => {
    mockFetch(async () => new Response(JSON.stringify(okState), { status: 200 }));
    render(<App />);
    expect(await screen.findByText(/ACS ACR1552 is connected/i)).toBeInTheDocument();
    expect(screen.getByText(/no tag on the reader/i)).toBeInTheDocument();
  });

  it("records an unreachable API as an error but still becomes view_ready", async () => {
    mockFetch(async () => {
      throw new TypeError("Failed to fetch");
    });
    render(<App />);
    await waitFor(() => expect(readAgentState().view_ready).toBe(true));
    const state = readAgentState();
    expect(state.status).toBe("error");
    expect(state.errors.length).toBe(1);
    expect(screen.getByRole("alert")).toHaveTextContent(/can't reach the nfc-cli server/i);
  });

  it("has a skip link to main content as the first focusable element", async () => {
    mockFetch(async () => new Response(JSON.stringify(okState), { status: 200 }));
    render(<App />);
    await userEvent.tab();
    const skip = screen.getByRole("link", { name: /skip to main content/i });
    expect(skip).toHaveFocus();
    expect(skip).toHaveAttribute("href", "#main");
    expect(document.getElementById("main")).not.toBeNull();
  });

  it("re-checks the reader on demand", async () => {
    const fetchFn = mockFetch(async () => new Response(JSON.stringify(okState), { status: 200 }));
    render(<App />);
    await waitFor(() => expect(readAgentState().view_ready).toBe(true));
    await userEvent.click(screen.getByRole("button", { name: /check reader again/i }));
    await waitFor(() => expect(fetchFn).toHaveBeenCalledTimes(2));
  });
});
