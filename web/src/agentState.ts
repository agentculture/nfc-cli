// The agent-state contract (spec: "UI is agent-first"). The root renders
// <script type="application/json" id="agent-state"> so agents and Playwright
// can read what the view shows without scraping it, and wait on view_ready.
// Pattern cited from culture-rules web/README.md (agent-state JSON node).

export type AgentStatus = "loading" | "ready" | "error";

/** Reader as reported by GET /api/state. Shape owned by the server (task t14). */
export interface ReaderState {
  connected: boolean;
  name?: string;
  [key: string]: unknown;
}

/** Tag on the reader as reported by GET /api/state; null when none present. */
export interface CardState {
  uid?: string;
  family?: string;
  [key: string]: unknown;
}

export interface AgentState {
  status: AgentStatus;
  view_ready: boolean;
  route: string;
  reader: ReaderState | null;
  card: CardState | null;
  errors: string[];
}

export function initialAgentState(route: string): AgentState {
  return { status: "loading", view_ready: false, route, reader: null, card: null, errors: [] };
}

/** JSON for a <script> body: `<` is escaped so no value can close the element. */
export function serializeAgentState(state: AgentState): string {
  return JSON.stringify(state).replace(/</g, "\\u003c");
}
