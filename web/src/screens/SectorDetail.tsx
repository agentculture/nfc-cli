import { HexBytes } from "./HexBytes";
import { type Layout, ROLE_WORDS, type Unit, asciiOf, blockRole } from "./tagModel";

export interface SectorDetailProps {
  layout: Layout;
  unit: Unit | null;
  blocks: Record<string, string> | null;
  revealed: Record<string, string> | null;
  revealing?: boolean;
  onReveal?: (unit: Unit) => void;
  onHide?: () => void;
}

/** The selected sector (or page group) as a table: bytes, printable text and what each block is. */
export function SectorDetail({ layout, unit, blocks, revealed, revealing, onReveal, onHide }: SectorDetailProps) {
  if (!unit) {
    return <p>Choose a {layout.kind === "classic" ? "sector" : "page group"} on the tag to see its bytes.</p>;
  }
  const hasTrailer = unit.blocks.some((b) => layout.trailers.has(b));
  const shown = (b: number) => revealed?.[b] ?? blocks?.[b];
  return (
    <div className="sector-detail">
      <h3 className="panel-title">{unit.label}</h3>
      <div className="diff-wrap">
        <table className="bytes">
          <caption className="visually-hidden">Bytes in {unit.label}</caption>
          <thead>
            <tr>
              <th scope="col">{layout.kind === "classic" ? "Block" : "Page"}</th>
              <th scope="col">Bytes</th>
              <th scope="col">Text</th>
              <th scope="col">What it is</th>
            </tr>
          </thead>
          <tbody>
            {unit.blocks.map((b) => {
              const hex = shown(b);
              return (
                <tr key={b}>
                  <th scope="row">{b}</th>
                  <td>{hex === undefined ? "Not readable with the known keys" : <HexBytes hex={hex} />}</td>
                  <td className="bytes__ascii">{hex ? asciiOf(hex) : ""}</td>
                  <td>{ROLE_WORDS[blockRole(layout, b)]}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {hasTrailer && blocks && (
        <p className="sector-detail__keys">
          {revealed ? (
            <button type="button" className="console-button console-button--quiet" onClick={onHide}>
              Hide key bytes
            </button>
          ) : (
            <button
              type="button"
              className="console-button console-button--quiet"
              disabled={revealing}
              onClick={() => onReveal?.(unit)}
            >
              Show key bytes for {unit.label.toLowerCase()}
            </button>
          )}
          <span className="field__hint">Keys stay hidden unless you ask; they are never kept after the tag leaves.</span>
        </p>
      )}
    </div>
  );
}
