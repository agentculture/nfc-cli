// docs/ui-design.md: the screens' stylesheet obeys the same tokens-only rule as app.css.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(join(__dirname, "screens.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

describe("screens.css uses only culture-design tokens for colour and type", () => {
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
