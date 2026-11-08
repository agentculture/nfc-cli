// Mocked nfc-cli API for e2e: every /api/* request is answered by Playwright
// request interception, so no Python server and no reader hardware are
// involved (pattern from culture-rules e2e/fixtures/api.ts). Task t16 adds
// the scenario fixtures (x3, NTAG, removed card, busy reader).
import type { Page, Route } from "@playwright/test";

export interface ApiScenario {
  state: unknown;
  status?: number;
}

export const scenarios = {
  readerNoCard: {
    state: { reader: { connected: true, name: "ACS ACR1552" }, card: null },
  },
  serverDown: { state: { error: "unavailable" }, status: 503 },
} satisfies Record<string, ApiScenario>;

export async function mockApi(page: Page, scenario: ApiScenario): Promise<void> {
  await page.route("**/api/**", async (route: Route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/state") {
      await route.fulfill({
        status: scenario.status ?? 200,
        contentType: "application/json",
        body: JSON.stringify(scenario.state),
      });
      return;
    }
    await route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ error: `no mock for ${url.pathname}` }),
    });
  });
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
