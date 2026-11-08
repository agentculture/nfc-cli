// Detection-logic tests for scripts/check-no-external-origin.mjs, cited from
// agentculture/embodiment web/src/build-output.test.ts (worklet cases dropped).
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  DIST_DIR,
  checkCssText,
  checkDist,
  checkHtmlText,
  checkJsText,
} from "../scripts/check-no-external-origin.mjs";

describe("check-no-external-origin.mjs detection logic", () => {
  it("passes a self-hosted-only index.html", () => {
    const html = `<link rel="icon" href="/favicon.svg"><script type="module" src="/assets/index-abc.js"></script>`;
    expect(checkHtmlText(html)).toEqual([]);
  });

  it("flags an external <script src>", () => {
    const findings = checkHtmlText(`<script src="https://cdn.example.com/lib.js"></script>`);
    expect(findings).toHaveLength(1);
    expect(findings[0]).toContain("cdn.example.com");
  });

  it("flags a protocol-relative <link href>", () => {
    expect(checkHtmlText(`<link rel="stylesheet" href="//fonts.googleapis.com/css">`)).toHaveLength(1);
  });

  it("passes local @font-face url()s and flags remote ones", () => {
    expect(checkCssText(`@font-face { src: url(/assets/f-abc.woff2) format("woff2"); }`)).toEqual([]);
    expect(checkCssText(`@font-face { src: url(https://fonts.gstatic.com/f.woff2); }`)).toHaveLength(1);
  });

  it("flags an external @import", () => {
    const findings = checkCssText(`@import url("https://fonts.googleapis.com/css2?family=Inter");`);
    expect(findings.some((f) => f.includes("fonts.googleapis.com"))).toBe(true);
  });

  it("flags a planted external fetch in a JS bundle", () => {
    const findings = checkJsText('fetch("https://example.com/x").then(r=>r.json())', "index.js");
    expect(findings).toHaveLength(1);
    expect(findings[0]).toContain("index.js");
  });

  it("allows W3C namespace URIs and the React error-decoder link only", () => {
    expect(checkJsText('"http://www.w3.org/2000/svg";"https://reactjs.org/docs/error-decoder.html?x="')).toEqual([]);
    expect(checkJsText('"https://attacker.example/www.w3.org/2000/svg"')).toHaveLength(1);
  });
});

describe("checkDist against a synthetic dist/", () => {
  let dir: string | null = null;
  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true });
    dir = null;
  });

  it("reports a planted external origin in assets/*.js", () => {
    dir = mkdtempSync(join(tmpdir(), "nfc-web-dist-"));
    writeFileSync(join(dir, "index.html"), `<!doctype html><script src="/assets/a.js"></script>`);
    mkdirSync(join(dir, "assets"));
    writeFileSync(join(dir, "assets", "a.js"), `fetch("https://evil.example.com/")`);
    expect(checkDist(dir).some((f) => f.includes("evil.example.com"))).toBe(true);
  });

  it("is clean for a clean dist/", () => {
    dir = mkdtempSync(join(tmpdir(), "nfc-web-dist-"));
    writeFileSync(join(dir, "index.html"), `<!doctype html>`);
    expect(checkDist(dir)).toEqual([]);
  });
});

describe("the real built dist/", () => {
  const built = existsSync(join(DIST_DIR, "index.html"));
  it.skipIf(!built)("references no external origin", () => {
    expect(checkDist()).toEqual([]);
  });
});
