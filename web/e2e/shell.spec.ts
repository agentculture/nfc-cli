import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { mockApi, readAgentState, scenarios, waitForViewReady } from "./fixtures/api";

async function expectNoSeriousAxeViolations(page: import("@playwright/test").Page) {
  const results = await new AxeBuilder({ page }).analyze();
  const blocking = results.violations.filter(
    (v) => v.impact === "serious" || v.impact === "critical",
  );
  expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([]);
}

test.describe("console shell", () => {
  test("agent-state reaches view_ready with reader state from the mocked API", async ({ page }) => {
    await mockApi(page, scenarios.readerNoCard);
    await page.goto("/");
    await waitForViewReady(page);
    const state = await readAgentState(page);
    expect(state).toMatchObject({
      status: "ready",
      view_ready: true,
      route: "/",
      reader: { connected: true, name: "ACS ACR1552" },
      card: null,
      errors: [],
    });
    await expect(page.getByText("No tag on the reader.")).toBeVisible();
    await expectNoSeriousAxeViolations(page);
  });

  test("an unreachable server is an error in words and in agent-state", async ({ page }) => {
    await mockApi(page, scenarios.serverDown);
    await page.goto("/");
    await waitForViewReady(page);
    const state = await readAgentState(page);
    expect(state.status).toBe("error");
    expect((state.errors as string[]).length).toBe(1);
    await expect(page.getByRole("alert")).toContainText("Can't reach the nfc-cli server");
    await expectNoSeriousAxeViolations(page);
  });

  test("skip link is first in tab order, visibly focused, and moves focus to main", async ({
    page,
  }) => {
    await mockApi(page, scenarios.readerNoCard);
    await page.goto("/");
    await waitForViewReady(page);
    await page.keyboard.press("Tab");
    const skip = page.getByRole("link", { name: "Skip to main content" });
    await expect(skip).toBeFocused();
    await expect(skip).toBeInViewport();
    const outline = await skip.evaluate((el) => getComputedStyle(el).outlineStyle);
    expect(outline).not.toBe("none");
    await page.keyboard.press("Enter");
    await expect(page.locator("#main")).toBeFocused();
  });

  test("interactive controls are at least 44x44 px", async ({ page }) => {
    await mockApi(page, scenarios.readerNoCard);
    await page.goto("/");
    await waitForViewReady(page);
    for (const loc of [
      page.getByRole("button", { name: "Check reader again" }),
      page.getByRole("link", { name: "Skip to main content" }),
    ]) {
      const box = await loc.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.height).toBeGreaterThanOrEqual(44);
      expect(box!.width).toBeGreaterThanOrEqual(44);
    }
  });

  test("prefers-reduced-motion stops the waiting animation", async ({ browser }) => {
    const context = await browser.newContext({ reducedMotion: "reduce" });
    const page = await context.newPage();
    await mockApi(page, scenarios.readerNoCard);
    await page.goto("/");
    await waitForViewReady(page);
    const animation = await page
      .locator(".tag-slot")
      .evaluate((el) => getComputedStyle(el).animationName);
    expect(animation).toBe("none");
    await context.close();
  });
});
