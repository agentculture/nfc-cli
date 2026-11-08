import { useEffect, useState } from "react";
import { type TraceEntry, getTrace, onTrace } from "../api/client";

/**
 * Every request the console made to the nfc-cli server, newest first. Keys the user typed are
 * already masked by the client. The reader's APDU-level trace is a CLI feature (`--trace`).
 */
export function TraceDrawer() {
  const [entries, setEntries] = useState<TraceEntry[]>(() => getTrace());
  useEffect(() => onTrace(setEntries), []);
  return (
    <details className="trace">
      <summary>Request log ({entries.length})</summary>
      <p className="field__hint">
        Calls this page made to the nfc-cli server. For the reader&rsquo;s own APDU exchange, run the same verb with{" "}
        <code>--trace</code>.
      </p>
      {entries.length === 0 ? (
        <p>No requests yet.</p>
      ) : (
        <ol className="trace__list">
          {entries.map((e) => (
            <li key={e.id} className={e.ok ? "trace__item" : "trace__item trace__item--failed"}>
              <span className="trace__line">
                <span>{e.at.slice(11, 19)}</span> <span>{e.method}</span> <span>{e.path}</span>{" "}
                <span>{e.status ?? "no response"}</span> <span>{e.ms} ms</span>
                {!e.ok && <span> failed</span>}
              </span>
              <span className="trace__summary">{e.summary}</span>
              {e.params && Object.keys(e.params).length > 0 && (
                <code className="trace__params">{JSON.stringify(e.params)}</code>
              )}
            </li>
          ))}
        </ol>
      )}
    </details>
  );
}
