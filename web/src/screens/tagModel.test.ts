import { describe, expect, it } from "vitest";
import { blockRole, familyName, groupHex, layoutFor } from "./tagModel";

describe("tag model", () => {
  it("lays a Classic 1K out as 16 sectors of 4 blocks, block 3 of each the trailer", () => {
    const layout = layoutFor("classic_1k", 64);
    expect(layout.kind).toBe("classic");
    expect(layout.units).toHaveLength(16);
    expect(layout.units[1].blocks).toEqual([4, 5, 6, 7]);
    expect(blockRole(layout, 7)).toBe("trailer");
    expect(blockRole(layout, 0)).toBe("manufacturer");
    expect(blockRole(layout, 5)).toBe("data");
  });

  it("defaults to the Classic 1K layout before anything is read", () => {
    expect(layoutFor(null, 0).units).toHaveLength(16);
  });

  it("groups Type 2 pages in fours and protects the header pages", () => {
    const layout = layoutFor("ultralight_ntag", 135);
    expect(layout.kind).toBe("pages");
    expect(layout.units).toHaveLength(34);
    expect(layout.units[33].blocks).toEqual([132, 133, 134]);
    expect(blockRole(layout, 2)).toBe("header");
    expect(blockRole(layout, 4)).toBe("data");
  });

  it("names families in words", () => {
    expect(familyName("classic_1k")).toBe("MIFARE Classic 1K");
    expect(familyName("ultralight_ntag")).toBe("NTAG / Ultralight");
    expect(familyName("weird")).toBe("weird");
  });

  it("splits hex into byte groups, keeping masked key bytes masked", () => {
    expect(groupHex("cb30c560")).toEqual(["cb", "30", "c5", "60"]);
    expect(groupHex("****ff07")).toEqual(["**", "**", "ff", "07"]);
  });
});
