import { useCallback, useEffect, useRef, useState } from "react";
import { type ApiError, callVerb } from "../api/client";
import { ErrorNote } from "./ErrorNote";
import { type WriteFlowProps, type WriteResult, WriteFlow } from "./WriteFlow";

interface NdefRecord {
  tnf: number;
  type: string;
  payload: string;
  text?: string;
  lang?: string;
  uri?: string;
  mime?: string;
}

interface NdefRead {
  empty: boolean;
  raw: string;
  records: NdefRecord[];
}

function describe(r: NdefRecord): { kind: string; value: string } {
  if (r.text !== undefined) return { kind: `Text${r.lang ? ` (${r.lang})` : ""}`, value: r.text };
  if (r.uri !== undefined) return { kind: "Link", value: r.uri };
  if (r.mime !== undefined) return { kind: `MIME ${r.mime}`, value: r.payload };
  return { kind: `Record type ${r.type || "(none)"}`, value: r.payload };
}

type Shared = Pick<WriteFlowProps, "cardUid" | "onPending" | "onError">;

export function NdefPanel({ cardUid, classic, onApplied, ...shared }: Shared & { classic: boolean; onApplied: (r: WriteResult) => void }) {
  const [data, setData] = useState<NdefRead | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  const forUid = useRef<string | null>(null);

  const read = useCallback(async () => {
    const mine = cardUid;
    setBusy(true);
    const res = await callVerb<NdefRead>("ndef", "read", {});
    if (forUid.current !== mine) return; // a different tag is on the reader now: drop this
    setBusy(false);
    if (res.ok) {
      setData(res.result);
      setError(null);
    } else {
      setData(null);
      setError(res.error);
    }
  }, [cardUid]);

  useEffect(() => {
    forUid.current = cardUid;
    setData(null); // clear the previous tag's NDEF while switching
    void read();
  }, [read, cardUid]);

  const applied = (r: WriteResult) => {
    onApplied(r);
    void read();
  };

  return (
    <div className="panel-grid">
      <section aria-labelledby="ndef-read-title">
        <h3 id="ndef-read-title" className="panel-title">
          What the tag says
        </h3>
        {busy && !data && <p>Reading the NDEF message…</p>}
        {error && <ErrorNote error={error} />}
        {data && data.empty && <p>The tag is NDEF formatted and holds no message yet.</p>}
        {data && !data.empty && (
          <ol className="records">
            {data.records.map((r, i) => {
              const d = describe(r);
              return (
                <li key={i}>
                  <span className="records__kind">{d.kind}</span>
                  <span className="records__value">{d.value}</span>
                </li>
              );
            })}
          </ol>
        )}
        <button type="button" className="console-button console-button--quiet" onClick={() => void read()} disabled={busy}>
          Read NDEF again
        </button>
      </section>
      <section aria-labelledby="ndef-write-title" className="write-zone">
        <h3 id="ndef-write-title" className="panel-title">
          Write to the tag
        </h3>
        <h4>New message</h4>
        <WriteFlow
          {...shared}
          cardUid={cardUid}
          noun="ndef"
          verb="write"
          only={["text", "lang", "uri", "mime", "payload"]}
          previewLabel="Preview message"
          intro={<p className="field__hint">Fill in one record: a text, a URI, or a MIME type with its payload.</p>}
          onApplied={applied}
        />
        <h4>Erase the message</h4>
        <WriteFlow {...shared} cardUid={cardUid} noun="ndef" verb="erase" only={[]} previewLabel="Preview erase" onApplied={applied} />
        {classic && (
          <>
            <h4>Format for NDEF</h4>
            <WriteFlow
              {...shared}
              cardUid={cardUid}
              noun="ndef"
              verb="format"
              only={[]}
              previewLabel="Preview format"
              intro={<p className="field__hint">Writes the NDEF directory and new sector trailers to a blank MIFARE Classic tag.</p>}
              onApplied={applied}
            />
          </>
        )}
      </section>
    </div>
  );
}
