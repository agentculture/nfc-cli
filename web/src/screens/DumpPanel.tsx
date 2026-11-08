import { useState } from "react";
import { type ApiError, callVerb } from "../api/client";
import { getVerb } from "../api/verbs";
import { ErrorNote } from "./ErrorNote";
import { VerbForm } from "./VerbForm";
import { type WriteFlowProps, type WriteResult, WriteFlow } from "./WriteFlow";

interface DumpResult {
  path: string;
  format: string;
  blocks_read: number;
  unreadable_sectors: number[];
  complete: boolean;
}

type Shared = Pick<WriteFlowProps, "cardUid" | "onPending" | "onError">;

export function DumpPanel({ onApplied, ...shared }: Shared & { onApplied: (r: WriteResult) => void }) {
  const [dump, setDump] = useState<DumpResult | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  async function save(params: Record<string, unknown>) {
    setBusy(true);
    const res = await callVerb<DumpResult>("tag", "dump", params);
    setBusy(false);
    if (res.ok) {
      setDump(res.result);
      setError(null);
    } else {
      setError(res.error);
    }
  }

  return (
    <div className="panel-grid">
      <section aria-labelledby="dump-title">
        <h3 id="dump-title" className="panel-title">
          Save a dump
        </h3>
        <p className="field__hint">Reads every block and saves it on this computer (keys included, file readable only by you).</p>
        <VerbForm spec={getVerb("tag", "dump")} only={["output", "format"]} submitLabel="Save a dump" busy={busy} onSubmit={(p) => void save(p)} />
        {error && <ErrorNote error={error} />}
        {dump && (
          <div role="status" className="write-flow__done">
            <p>
              Saved {dump.blocks_read} blocks to <code>{dump.path}</code>.
            </p>
            {!dump.complete && (
              <p>
                Sectors {dump.unreadable_sectors.join(", ")} could not be read with the known keys; the dump is partial.
              </p>
            )}
          </div>
        )}
      </section>
      <section aria-labelledby="restore-title" className="write-zone">
        <h3 id="restore-title" className="panel-title">
          Restore a dump to the tag
        </h3>
        <WriteFlow
          key={dump?.path ?? "none"}
          {...shared}
          noun="tag"
          verb="restore"
          only={["file", "format", "resume"]}
          initial={dump ? { file: dump.path } : undefined}
          previewLabel="Preview restore"
          onApplied={onApplied}
        />
      </section>
    </div>
  );
}
