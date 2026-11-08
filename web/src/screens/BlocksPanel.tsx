import { useState } from "react";
import { type ApiError, callVerb } from "../api/client";
import { getVerb } from "../api/verbs";
import { ErrorNote } from "./ErrorNote";
import { HexBytes } from "./HexBytes";
import { VerbForm } from "./VerbForm";
import { type WriteFlowProps, type WriteResult, WriteFlow } from "./WriteFlow";

type Shared = Pick<WriteFlowProps, "cardUid" | "onPending" | "onError">;

export function BlocksPanel({ onApplied, ...shared }: Shared & { onApplied: (r: WriteResult) => void }) {
  const [blocks, setBlocks] = useState<Record<string, string> | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  async function read(params: Record<string, unknown>) {
    setBusy(true);
    const res = await callVerb<{ blocks: Record<string, string> }>("tag", "read", params);
    setBusy(false);
    if (res.ok) {
      setBlocks(res.result.blocks);
      setError(null);
    } else {
      setBlocks(null);
      setError(res.error);
    }
  }

  return (
    <div className="panel-grid">
      <section aria-labelledby="blocks-read-title">
        <h3 id="blocks-read-title" className="panel-title">
          Read blocks
        </h3>
        <VerbForm spec={getVerb("tag", "read")} only={["block", "count", "key", "key_type"]} submitLabel="Read blocks" busy={busy} onSubmit={(p) => void read(p)} />
        {error && <ErrorNote error={error} />}
        {blocks && (
          <div className="diff-wrap">
            <table className="bytes">
              <caption className="visually-hidden">Blocks read</caption>
              <thead>
                <tr>
                  <th scope="col">Block</th>
                  <th scope="col">Bytes</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(blocks).map(([b, hex]) => (
                  <tr key={b}>
                    <th scope="row">{b}</th>
                    <td>
                      <HexBytes hex={hex} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <section aria-labelledby="blocks-write-title" className="write-zone">
        <h3 id="blocks-write-title" className="panel-title">
          Write blocks
        </h3>
        <WriteFlow {...shared} noun="tag" verb="write" only={["block", "data", "key", "key_type"]} previewLabel="Preview write" onApplied={onApplied} />
      </section>
    </div>
  );
}
