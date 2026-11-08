// docs/ui-design.md: colour and type come only from culture-design tokens.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(join(__dirname, "app.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

describe("app.css uses only culture-design tokens for colour and type", () => {
  it("has no literal colour values", () => {
    expect(css).not.toMatch(/#[0-9a-f]{3,8}\b/i);
    expect(css).not.toMatch(/\b(rgba?|hsla?|oklch|oklab|lab|lch|color-mix)\(/i);
    expect(css).not.toMatch(/:\s*(black|white|red|blue|green|gray|grey)\b/i);
  });

  it("names no font family except through the font tokens", () => {
    for (const m of css.matchAll(/font-family:\s*([^;]+);/g)) {
      expect(m[1].trim()).toMatch(/^var\(--font-(display|body|mono)\)$/);
    }
  });
});
