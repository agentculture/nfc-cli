import { describe, expect, it } from "vitest";
import { initialAgentState, serializeAgentState } from "./agentState";

describe("agent-state", () => {
  it("starts loading, not view_ready, with every required key", () => {
    const s = initialAgentState("/");
    expect(Object.keys(s).sort()).toEqual(
      ["card", "errors", "reader", "route", "status", "view_ready"].sort(),
    );
    expect(s.status).toBe("loading");
    expect(s.view_ready).toBe(false);
    expect(s.route).toBe("/");
    expect(s.reader).toBeNull();
    expect(s.card).toBeNull();
    expect(s.errors).toEqual([]);
  });

  it("serializes to JSON that cannot close the <script> element", () => {
    const s = { ...initialAgentState("/"), errors: ["</script><img src=x>"] };
    const text = serializeAgentState(s);
    expect(text).not.toContain("</script");
    expect(text).not.toContain("<");
    expect(JSON.parse(text)).toEqual(s);
  });
});
