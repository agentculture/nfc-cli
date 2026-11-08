// What the tag looks like as memory: sectors (MIFARE Classic) or groups of 4 pages (Type 2 and
// other page-addressed tags). The memory map, sector detail and diff preview share this model.

export type BlockRole = "manufacturer" | "trailer" | "header" | "data";

export interface Unit {
  index: number;
  label: string;
  blocks: number[];
}

export interface Layout {
  kind: "classic" | "pages";
  /** Bytes per block (16 for Classic, 4 for Type 2 pages). */
  blockSize: number;
  units: Unit[];
  trailers: Set<number>;
  headers: Set<number>;
}

const FAMILY_NAMES: Record<string, string> = {
  classic: "MIFARE Classic",
  classic_1k: "MIFARE Classic 1K",
  classic_4k: "MIFARE Classic 4K",
  classic_mini: "MIFARE Classic Mini",
  ultralight: "NTAG / Ultralight",
  ultralight_ntag: "NTAG / Ultralight",
  ultralight_c: "MIFARE Ultralight C",
  iso15693: "ISO 15693 (vicinity)",
  iso14443_4: "ISO 14443-4 (Type 4)",
  topaz: "Topaz",
  felica: "FeliCa",
};

export function familyName(family: string | null | undefined): string {
  if (!family) return "Unknown tag";
  return FAMILY_NAMES[family] ?? family;
}

export function isClassic(family: string | null | undefined): boolean {
  return !family || family.startsWith("classic");
}

export function layoutFor(family: string | null | undefined, blockCount: number): Layout {
  if (isClassic(family)) {
    const total = blockCount > 0 ? blockCount : 64;
    const units: Unit[] = [];
    const trailers = new Set<number>();
    let block = 0;
    while (block < total) {
      // Classic 4K: sectors 32-39 hold 16 blocks; everything below block 128 holds 4.
      const size = block >= 128 ? 16 : 4;
      const blocks = Array.from({ length: Math.min(size, total - block) }, (_, i) => block + i);
      trailers.add(block + size - 1);
      units.push({ index: units.length, label: `Sector ${units.length}`, blocks });
      block += size;
    }
    return { kind: "classic", blockSize: 16, units, trailers, headers: new Set() };
  }
  const type2 = family?.startsWith("ultralight") ?? false;
  const units: Unit[] = [];
  for (let start = 0; start < blockCount; start += 4) {
    const blocks = Array.from({ length: Math.min(4, blockCount - start) }, (_, i) => start + i);
    const last = blocks[blocks.length - 1];
    units.push({
      index: units.length,
      label: blocks.length > 1 ? `Pages ${start}–${last}` : `Page ${start}`,
      blocks,
    });
  }
  return {
    kind: "pages",
    blockSize: type2 ? 4 : 4,
    units,
    trailers: new Set(),
    headers: new Set(type2 ? [0, 1, 2, 3] : []),
  };
}

export function blockRole(layout: Layout, block: number): BlockRole {
  if (layout.kind === "classic" && block === 0) return "manufacturer";
  if (layout.trailers.has(block)) return "trailer";
  if (layout.headers.has(block)) return "header";
  return "data";
}

/** Blocks nfc-cli refuses to write without include_trailers (trailers and block 0). */
export function isProtected(layout: Layout, block: number): boolean {
  const role = blockRole(layout, block);
  return role === "trailer" || role === "manufacturer";
}

export const ROLE_WORDS: Record<BlockRole, string> = {
  manufacturer: "Manufacturer block (UID, read-only)",
  trailer: "Sector trailer: key A, access bits, key B",
  header: "Header (UID, lock bits, capability container)",
  data: "Data",
};

export function groupHex(hex: string): string[] {
  const out: string[] = [];
  for (let i = 0; i < hex.length; i += 2) out.push(hex.slice(i, i + 2));
  return out;
}

export function isEmptyBlock(hex: string | undefined): boolean {
  return !!hex && /^0+$/.test(hex);
}

export function asciiOf(hex: string): string {
  return groupHex(hex)
    .map((b) => {
      if (b.includes("*")) return "·";
      const n = Number.parseInt(b, 16);
      return n >= 0x20 && n < 0x7f ? String.fromCharCode(n) : "·";
    })
    .join("");
}
