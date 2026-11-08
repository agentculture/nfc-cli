import { HexBytes } from "./HexBytes";
import { type Layout, blockRole } from "./tagModel";

export interface DiffRow {
  block: number;
  old: string;
  new: string;
  action: "write" | "unchanged" | "skip";
  reason?: string;
  verified?: boolean;
}

const ROLE_SHORT = { manufacturer: "block 0", trailer: "trailer", header: "header", data: "data" };

function changeWords(row: DiffRow): string {
  if (row.action === "write") return row.verified ? "written, verified" : "will change";
  if (row.action === "skip") return row.reason ? `skipped: ${row.reason}` : "skipped";
  return "unchanged";
}

export function DiffTable({ rows, uid, applied, layout }: { rows: DiffRow[]; uid: string; applied: boolean; layout: Layout }) {
  return (
    <div className="diff-wrap">
      <table className="diff">
        <caption>
          {applied ? `Changes written to tag ${uid}` : `Changes this would make to tag ${uid} (preview, nothing written yet)`}
        </caption>
        <thead>
          <tr>
            <th scope="col">Block</th>
            <th scope="col">Now</th>
            <th scope="col">After</th>
            <th scope="col">Change</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const role = blockRole(layout, r.block);
            return (
              <tr key={r.block} className={`diff__row diff__row--${r.action}${role !== "data" ? " diff__row--protected" : ""}`}>
                <th scope="row">
                  {r.block}
                  {role !== "data" && <span className="diff__role"> {ROLE_SHORT[role]}</span>}
                </th>
                <td>
                  <HexBytes hex={r.old} />
                </td>
                <td>
                  <HexBytes hex={r.new} />
                </td>
                <td>{changeWords(r)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
