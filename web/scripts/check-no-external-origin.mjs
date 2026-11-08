#!/usr/bin/env node
// scripts/check-no-external-origin.mjs
//
// Cited (cite-don't-import) from agentculture/embodiment
// web/scripts/check-no-external-origin.mjs (embodiment tasks t17/t18). nfc-cli
// plan task t15 drops embodiment's AudioWorklet presence check (d) — there is
// no worklet here — and keeps the rest unchanged.
//
// Acceptance: the build fails if web/dist references any external origin. Run
// AFTER `vite build` (it is the last step of `npm run build`). Checks the
// surfaces a browser actually resolves at runtime:
//
//   (a) dist/index.html: no href/src attribute pointing at a remote origin.
//   (b) every dist/assets/*.css: no `@import` / `url()` to a remote origin
//       (fonts are bundled from @fontsource-variable, never a CDN).
//   (c) every dist/assets/*.js: no `http(s)://` literal at all, except a
//       narrow allow-list of known-inert literals (W3C namespace URIs and
//       React's error-decoder link, which is printed, never fetched).
//
// The functions are exported so src/build-output.test.ts can exercise the
// detection logic against synthetic fixtures without a build.

import { readFileSync, readdirSync } from "node:fs";
import { extname, join } from "node:path";
import { fileURLToPath } from "node:url";
import path from "node:path";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = path.resolve(SCRIPT_DIR, "..");
export const DIST_DIR = path.join(WEB_ROOT, "dist");

const EXTERNAL_RE = /^(https?:)?\/\//;

export const ALLOWED_JS_ORIGIN_PATTERNS = [
  /^https?:\/\/www\.w3\.org\//i,
  /^https?:\/\/reactjs\.org\/docs\/error-decoder\.html/i,
  /^https?:\/\/react\.dev\/errors\//i,
];

function isAllowedJsOrigin(url) {
  return ALLOWED_JS_ORIGIN_PATTERNS.some((pattern) => pattern.test(url));
}

export function walk(dir, exts) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const p = join(dir, entry.name);
    if (entry.isDirectory()) return walk(p, exts);
    return exts.includes(extname(p)) ? [p] : [];
  });
}

/** Findings for external href/src attributes in an HTML string. Pure. */
export function checkHtmlText(html, label = "index.html") {
  const findings = [];
  const attrRe = /(href|src)\s*=\s*"([^"]*)"/g;
  let match;
  while ((match = attrRe.exec(html))) {
    const [, attr, value] = match;
    if (EXTERNAL_RE.test(value)) {
      findings.push(`${label}: ${attr}="${value}" is an external origin`);
    }
  }
  return findings;
}

/** Findings for external url()/@import in a CSS string. Pure. */
export function checkCssText(css, label = "styles.css") {
  const findings = [];
  const urlRe = /url\(\s*['"]?([^'")]+)['"]?\s*\)/g;
  let match;
  while ((match = urlRe.exec(css))) {
    const value = match[1];
    if (EXTERNAL_RE.test(value)) {
      findings.push(`${label}: url(${value}) is an external origin`);
    }
  }
  const importRe = /@import\s+(?:url\()?['"]?([^'")\s;]+)/g;
  while ((match = importRe.exec(css))) {
    const value = match[1];
    if (EXTERNAL_RE.test(value)) {
      findings.push(`${label}: @import ${value} is an external origin`);
    }
  }
  return findings;
}

/** Findings for http(s):// literals not on the allow-list in a JS string. Pure. */
export function checkJsText(js, label = "bundle.js") {
  const findings = [];
  const urlRe = /https?:\/\/[^\s"'`)]+/g;
  let match;
  while ((match = urlRe.exec(js))) {
    const url = match[0];
    if (!isAllowedJsOrigin(url)) {
      findings.push(`${label}: external origin literal "${url}" is not on the allow-list`);
    }
  }
  return findings;
}

/** Run every check against a built dist/. Throws if dist/index.html is missing. */
export function checkDist(distDir = DIST_DIR) {
  const indexPath = join(distDir, "index.html");
  const findings = [...checkHtmlText(readFileSync(indexPath, "utf8"), indexPath)];
  const assetsDir = join(distDir, "assets");
  let cssFiles = [];
  let jsFiles = [];
  try {
    cssFiles = walk(assetsDir, [".css"]);
    jsFiles = walk(assetsDir, [".js"]);
  } catch {
    cssFiles = [];
    jsFiles = [];
  }
  for (const file of cssFiles) {
    findings.push(...checkCssText(readFileSync(file, "utf8"), file));
  }
  for (const file of jsFiles) {
    findings.push(...checkJsText(readFileSync(file, "utf8"), file));
  }
  return findings;
}

function main() {
  const findings = checkDist();
  if (findings.length > 0) {
    console.error("FAIL - external origin(s) referenced by dist/:");
    for (const f of findings) console.error(`  ${f}`);
    process.exit(1);
  }
  console.log("ok   - dist/index.html, dist/assets/*.css and *.js reference no external origin");
  console.log("check-no-external-origin: PASS");
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main();
}
