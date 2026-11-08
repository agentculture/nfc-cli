import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import {
  NTAG_UID,
  X3_KEY,
  X3_UID,
  mockApi,
  readAgentState,
  scenarios,
  waitForTagRead,
  waitForViewReady,
} from "./fixtures/api";

async function expectNoSeriousAxeViolations(page: Page, label: string) {
  const results = await new AxeBuilder({ page }).analyze();
  const blocking = results.violations.filter(
    (v) => v.impact === "serious" || v.impact === "critical",
  );
  expect(blocking, `${label}: ${JSON.stringify(blocking, null, 2)}`).toEqual([]);
}

async function openTab(page: Page, name: string) {
  await page.getByRole("tab", { name }).click();
  await expect(page.getByRole("tab", { name })).toHaveAttribute("aria-selected", "true");
}

test.describe("x3 scenario (blank MIFARE Classic 1K)", () => {
  test("the tag is announced in words and drawn as 16 sectors with locked trailers", async ({ page }) => {
    await mockApi(page, scenarios.x3);
    await page.goto("/");
    await waitForTagRead(page);
    await expect(page.getByText(`MIFARE Classic 1K tag present, UID ${X3_UID}.`)).toBeVisible();
    await expect(page.getByText("ACS ACR1552 is connected (firmware ACR1552 1.05).")).toBeVisible();
    const sectors = page.getByRole("button", { name: /^Sector \d+:/ });
    await expect(sectors).toHaveCount(16);
    await expect(sectors.nth(0)).toHaveAccessibleName(
      "Sector 0: manufacturer block, 2 data blocks, 2 empty; trailer locked",
    );
    const state = await readAgentState(page);
    expect(state).toMatchObject({
      status: "ready",
      card: { present: true, uid: X3_UID },
      tag: { uid: X3_UID, family: "classic_1k", blocks_read: 64 },
      errors: [],
    });
  });

  test("every screen has no serious or critical axe violations", async ({ page }) => {
    await mockApi(page, scenarios.x3);
    await page.goto("/");
    await waitForTagRead(page);
    await expectNoSeriousAxeViolations(page, "sectors");
    await openTab(page, "NDEF");
    await expect(page.getByRole("tabpanel").getByText(/not NDEF formatted/)).toBeVisible();
    await expectNoSeriousAxeViolations(page, "ndef");
    await page.getByRole("button", { name: "Preview format" }).click();
    await expect(page.getByRole("table", { name: /changes this would make/i })).toBeVisible();
    await expectNoSeriousAxeViolations(page, "ndef format preview");
    await openTab(page, "Dump and restore");
    await expectNoSeriousAxeViolations(page, "dump");
    await openTab(page, "Raw blocks");
    await expectNoSeriousAxeViolations(page, "raw blocks");
    await page.getByText(/^Request log/).click();
    await expectNoSeriousAxeViolations(page, "trace drawer");
  });

  test("key bytes stay hidden until the user reveals them", async ({ page }) => {
    const calls = await mockApi(page, scenarios.x3);
    await page.goto("/");
    await waitForTagRead(page);
    await page.getByRole("button", { name: /^Sector 1:/ }).click();
    const panel = page.getByRole("tabpanel");
    await expect(panel.getByText("key hidden").first()).toBeVisible();
    await expect(panel).not.toContainText("ff ff ff ff ff ff");
    expect(calls.every((c) => c.body.include_keys !== true)).toBe(true);
    await panel.getByRole("button", { name: "Show key bytes for sector 1" }).click();
    await expect(panel).toContainText("ff ff ff ff ff ff");
    expect(calls.at(-1)!.body).toEqual({ block: 4, count: 4, include_keys: true });
    await panel.getByRole("button", { name: "Hide key bytes" }).click();
    await expect(panel).not.toContainText("ff ff ff ff ff ff");
  });

  test("NDEF format: dry-run diff first, trailer toggle, then an explicit Apply", async ({ page }) => {
    const calls = await mockApi(page, scenarios.x3);
    await page.goto("/");
    await waitForTagRead(page);
    await openTab(page, "NDEF");
    await page.getByRole("button", { name: "Preview format" }).click();
    await expect(page.getByRole("table", { name: /changes this would make/i })).toBeVisible();
    expect(calls.at(-1)).toMatchObject({ path: "/api/ndef/format", body: {} });
    // The preview hatches the blocks it would change on the specimen, in words too.
    await expect(page.getByRole("button", { name: /^Sector 0:/ })).toHaveAccessibleName(/3 blocks would change/);
    const apply = page.getByRole("button", { name: `Write 19 blocks to tag ${X3_UID}` });
    await expect(apply).toBeDisabled();
    await page.getByRole("checkbox", { name: "Allow writes to sector trailers and block 0" }).check();
    await expect.poll(() => calls.at(-1)!.body).toEqual({ include_trailers: true });
    await expect(apply).toBeEnabled();
    await apply.click();
    await expect(page.getByRole("status").filter({ hasText: "Wrote 19 blocks" })).toBeVisible();
    const applied = calls.find((c) => c.body.apply === true)!;
    expect(applied).toMatchObject({ path: "/api/ndef/format", body: { include_trailers: true, apply: true } });
    expect(Object.keys(applied.body).sort()).toEqual(["apply", "include_trailers"]);
    // The tag is re-read and NDEF now reads as an empty message.
    await expect(page.getByText("The tag is NDEF formatted and holds no message yet.")).toBeVisible();
    expect(JSON.stringify(await readAgentState(page))).not.toContain(X3_KEY);
  });

  test("a raw block write previews, names the blocks, and applies the same params", async ({ page }) => {
    const calls = await mockApi(page, scenarios.x3);
    await page.goto("/");
    await waitForTagRead(page);
    await openTab(page, "Raw blocks");
    const write = page.locator("section.write-zone");
    await write.getByLabel(/^Block/).fill("4");
    await write.getByLabel(/^Data/).fill("11".repeat(16));
    await page.getByRole("button", { name: "Preview write" }).click();
    await expect(page.getByText(`Block 4 will be overwritten on tag ${X3_UID}`, { exact: false })).toBeVisible();
    expect(calls.some((c) => c.body.apply === true)).toBe(false);
    await page.getByRole("button", { name: `Write 1 block to tag ${X3_UID}` }).click();
    await expect(page.getByRole("status").filter({ hasText: "Wrote 1 block" })).toBeVisible();
    expect(calls.find((c) => c.body.apply === true)!.body).toEqual({ block: 4, data: "11".repeat(16), apply: true, expect_uid: X3_UID });
  });

  test("writing a trailer without the toggle is refused in words", async ({ page }) => {
    await mockApi(page, scenarios.x3);
    await page.goto("/");
    await waitForTagRead(page);
    await openTab(page, "Raw blocks");
    const write = page.locator("section.write-zone");
    await write.getByLabel(/^Block/).fill("3");
    await write.getByLabel(/^Data/).fill("00".repeat(16));
    await page.getByRole("button", { name: "Preview write" }).click();
    await expect(write.getByRole("alert")).toContainText("refusing to write protected block(s) [3]");
    await expect(page.getByRole("button", { name: /^Write \d+ block/ })).toHaveCount(0);
    const state = await readAgentState(page);
    expect((state.errors as string[]).some((e) => e.includes("protected block"))).toBe(true);
  });

  test("dump then restore: the restore form starts from the new dump", async ({ page }) => {
    const calls = await mockApi(page, scenarios.x3);
    await page.goto("/");
    await waitForTagRead(page);
    await openTab(page, "Dump and restore");
    await page.getByRole("button", { name: "Save a dump" }).click();
    await expect(page.getByRole("status")).toContainText("Saved 64 blocks");
    await expect(page.locator("section.write-zone").getByLabel(/^File/)).toHaveValue(/cb30c560-.*\.json$/);
    await page.getByRole("button", { name: "Preview restore" }).click();
    await expect(page.getByRole("button", { name: `Write 2 blocks to tag ${X3_UID}` })).toBeEnabled();
    expect(calls.at(-1)!.path).toBe("/api/tag/restore");
  });

  test("the request log lists calls without key material", async ({ page }) => {
    await mockApi(page, scenarios.x3);
    await page.goto("/");
    await waitForTagRead(page);
    await openTab(page, "Raw blocks");
    await page.getByLabel(/^Key$/).first().fill("a0a1a2a3a4a5");
    await page.getByRole("button", { name: "Read blocks" }).click();
    await page.getByText(/^Request log/).click();
    const log = page.locator(".trace__list");
    await expect(log).toContainText("POST /api/tag/read");
    await expect(log).toContainText("POST /api/tag/info");
    await expect(log).not.toContainText("a0a1a2a3a4a5");
  });
});

test.describe("NTAG scenario", () => {
  test("pages map, NDEF read and an NDEF write preview", async ({ page }) => {
    const calls = await mockApi(page, scenarios.ntag);
    await page.goto("/");
    await waitForTagRead(page);
    await expect(page.getByText(`NTAG / Ultralight tag present, UID ${NTAG_UID}.`)).toBeVisible();
    await expect(page.getByRole("button", { name: /^Pages \d+–\d+:/ })).toHaveCount(34);
    await expect(page.getByRole("button", { name: /^Pages 0–3:/ })).toHaveAccessibleName(/4 header pages/);
    await expectNoSeriousAxeViolations(page, "ntag pages");
    await openTab(page, "NDEF");
    await expect(page.getByText("The tag is NDEF formatted and holds no message yet.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Preview format" })).toHaveCount(0);
    await page.getByLabel(/^Uri/).fill("https://x.org");
    await page.getByRole("button", { name: "Preview message" }).click();
    await page.getByRole("button", { name: `Write 4 blocks to tag ${NTAG_UID}` }).click();
    await expect(page.locator(".records")).toContainText("https://x.org");
    expect(calls.find((c) => c.body.apply === true)!.body).toEqual({ uri: "https://x.org", apply: true, expect_uid: "04a1b2c3d4e580" });
    await expectNoSeriousAxeViolations(page, "ntag ndef");
  });
});

test.describe("removed card", () => {
  test("removal is announced in words and agent-state drops the card", async ({ page }) => {
    await mockApi(page, scenarios.removedCard);
    await page.goto("/");
    await waitForViewReady(page);
    await expect(page.getByText("No tag on the reader.")).toBeVisible();
    await expect(page.getByText(`Tag ${X3_UID} was removed from the reader.`)).toBeVisible();
    await expect.poll(async () => (await readAgentState(page)).card).toBeNull();
    expect((await readAgentState(page)).tag).toBeNull();
    await expect(page.getByRole("tablist")).toHaveCount(0);
    await expectNoSeriousAxeViolations(page, "removed card");
  });
});

test.describe("busy reader", () => {
  test("busy is stated in words, as an alert with its fix, and in agent-state", async ({ page }) => {
    await mockApi(page, scenarios.busyReader);
    await page.goto("/");
    await waitForViewReady(page);
    await expect(page.getByText(/connected but busy: the reader is in use by another process/)).toBeVisible();
    await expect(page.getByRole("alert")).toContainText("wait for the other nfc-cli process to finish");
    await expect
      .poll(async () => (await readAgentState(page)).last_error)
      .toMatchObject({ kind: "env", message: "the reader is in use by another process" });
    const errors = (await readAgentState(page)).errors as string[];
    expect(errors).toContain("Reader: the reader is in use by another process");
    await expectNoSeriousAxeViolations(page, "busy reader");
  });
});

test.describe("screenshots", () => {
  for (const scheme of ["light", "dark"] as const) {
    test(`x3 console in ${scheme} scheme`, async ({ browser }) => {
      const context = await browser.newContext({ colorScheme: scheme, viewport: { width: 1280, height: 1000 } });
      const page = await context.newPage();
      await mockApi(page, scenarios.x3);
      await page.goto("/");
      await waitForTagRead(page);
      await page.getByRole("button", { name: /^Sector 1:/ }).click();
      await expectNoSeriousAxeViolations(page, `x3 ${scheme}`);
      await page.screenshot({ path: `test-results/console-x3-${scheme}.png`, fullPage: true });
      await context.close();
    });
  }
});
