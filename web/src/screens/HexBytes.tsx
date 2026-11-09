import { groupHex } from "./tagModel";

/** Hex bytes in byte groups; a masked run (key bytes) reads "key hidden", never the bytes. */
export function HexBytes({ hex }: { hex: string }) {
  const parts: { masked: boolean; bytes: string[] }[] = [];
  for (const b of groupHex(hex)) {
    const masked = b.includes("*");
    const last = parts[parts.length - 1];
    if (last && last.masked === masked) last.bytes.push(b);
    else parts.push({ masked, bytes: [b] });
  }
  return (
    <span className="hex">
      {parts.map((p, i) =>
        p.masked ? (
          <span key={i} className="hex__key">
            key hidden
          </span>
        ) : (
          <span key={i} className="hex__run">
            {p.bytes.join(" ")}
          </span>
        ),
      )}
    </span>
  );
}
