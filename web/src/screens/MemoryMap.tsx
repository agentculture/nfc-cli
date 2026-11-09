import { type Layout, type Unit, blockRole, isEmptyBlock } from "./tagModel";

export interface MemoryMapProps {
  layout: Layout;
  present: boolean;
  uid?: string | null;
  blocks?: Record<string, string> | null;
  pending?: number[] | null;
  selected?: number | null;
  onSelect?: (unit: number) => void;
}

function plural(n: number, one: string, many = `${one}s`) {
  return `${n} ${n === 1 ? one : many}`;
}

function describeUnit(layout: Layout, unit: Unit, blocks: Record<string, string> | null | undefined, pending: Set<number>): string {
  const word = layout.kind === "classic" ? "block" : "page";
  const read = unit.blocks.filter((b) => blocks?.[b] !== undefined);
  const parts: string[] = [];
  if (!blocks || read.length === 0) {
    parts.push("not read");
  } else {
    const data = unit.blocks.filter((b) => blockRole(layout, b) === "data");
    const empty = data.filter((b) => isEmptyBlock(blocks[b]));
    const unread = unit.blocks.filter((b) => blocks[b] === undefined);
    if (unit.blocks.some((b) => blockRole(layout, b) === "manufacturer")) parts.push("manufacturer block");
    if (unit.blocks.some((b) => blockRole(layout, b) === "header")) {
      parts.push(plural(unit.blocks.filter((b) => blockRole(layout, b) === "header").length, "header page"));
    }
    if (data.length) parts.push(`${plural(data.length, `data ${word}`)}, ${empty.length} empty`);
    if (unread.length) parts.push(`${plural(unread.length, word)} unreadable`);
  }
  let text = `${unit.label}: ${parts.join(", ")}`;
  if (unit.blocks.some((b) => layout.trailers.has(b))) text += "; trailer locked";
  const changing = unit.blocks.filter((b) => pending.has(b)).length;
  if (changing) text += `; ${plural(changing, word)} would change`;
  return text;
}

function blockClass(layout: Layout, block: number, blocks: Record<string, string> | null | undefined, pending: Set<number>) {
  const role = blockRole(layout, block);
  const cls = ["blk"];
  if (role !== "data") cls.push("blk--locked");
  const hex = blocks?.[block];
  if (blocks && hex === undefined) cls.push("blk--unread");
  else if (hex !== undefined && role === "data" && !isEmptyBlock(hex)) cls.push("blk--filled");
  if (pending.has(block)) cls.push("blk--pending");
  return cls.join(" ");
}

/**
 * The hero object: the tag drawn at ID-1 proportions, its face the real memory map.
 * Locked blocks (trailers, block 0, Type 2 header) have a solid heavy rule, data blocks a
 * dashed one, filled data a solid fill, and blocks a preview would change are hatched.
 * Every state is also spelled out in each sector's accessible name and the legend.
 */
export function MemoryMap({ layout, present, uid, blocks, pending, selected, onSelect }: MemoryMapProps) {
  const pend = new Set(pending ?? []);
  const cols = layout.kind === "classic" ? 8 : Math.min(9, Math.max(4, Math.ceil(Math.sqrt(layout.units.length * 2.2))));
  const style = { "--map-cols": cols } as React.CSSProperties;

  if (!present) {
    return (
      <figure className="tag-slot" aria-hidden="true">
        <div className="tag-slot__map" style={style}>
          {layout.units.map((u) => (
            <div className="tag-slot__sector" key={u.index}>
              {u.blocks.map((b) => (
                <span key={b} className={layout.trailers.has(b) ? "tag-slot__block tag-slot__block--trailer" : "tag-slot__block"} />
              ))}
            </div>
          ))}
        </div>
      </figure>
    );
  }

  return (
    <figure className={`tag-slot tag-slot--present tag-slot--${layout.kind}`}>
      <figcaption className="tag-slot__uid">
        <span className="visually-hidden">Memory map of tag </span>
        {uid}
      </figcaption>
      {layout.units.length === 0 ? (
        <p className="tag-slot__none">This tag has no block memory to map.</p>
      ) : (
        <div className="tag-slot__map" style={style} role="group" aria-label={`${layout.units.length} ${layout.kind === "classic" ? "sectors" : "page groups"}; choose one to inspect`}>
          {layout.units.map((u) => (
            <button
              type="button"
              key={u.index}
              className="tag-slot__sector tag-slot__sector--button"
              aria-pressed={selected === u.index}
              aria-label={describeUnit(layout, u, blocks, pend)}
              onClick={() => onSelect?.(u.index)}
            >
              {u.blocks.map((b) => (
                <span key={b} className={blockClass(layout, b, blocks, pend)} />
              ))}
            </button>
          ))}
        </div>
      )}
    </figure>
  );
}

/** The legend: each block shape named in words. */
export function MapLegend({ layout }: { layout: Layout }) {
  return (
    <ul className="map-legend" aria-label="How to read the memory map">
      <li>
        <span className="blk" aria-hidden="true" /> Empty data
      </li>
      <li>
        <span className="blk blk--filled" aria-hidden="true" /> Holds data
      </li>
      <li>
        <span className="blk blk--locked" aria-hidden="true" />{" "}
        {layout.kind === "classic" ? "Trailer or block 0, locked" : "Header page, locked"}
      </li>
      <li>
        <span className="blk blk--pending" aria-hidden="true" /> Would change
      </li>
    </ul>
  );
}
