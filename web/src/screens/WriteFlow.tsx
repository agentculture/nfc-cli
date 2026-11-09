import { type ReactNode, useCallback, useEffect, useId, useRef, useState } from "react";
import { type ApiError, callVerb } from "../api/client";
import { type RawValues, getVerb, hasParam } from "../api/verbs";
import { DiffTable, type DiffRow } from "./DiffTable";
import { ErrorNote } from "./ErrorNote";
import { isProtected, layoutFor } from "./tagModel";
import { VerbForm } from "./VerbForm";

export interface WriteResult {
  uid: string;
  family: string;
  applied: boolean;
  diff: DiffRow[];
  summary?: { write: number; unchanged: number; skip: number };
  snapshot?: string | null;
  journal?: string | null;
  verified?: number;
  protected_blocks_written?: number[];
  audit_log?: string;
  [key: string]: unknown;
}

export interface WriteFlowProps {
  noun: string;
  verb: string;
  only?: readonly string[];
  omit?: readonly string[];
  initial?: RawValues;
  cardUid: string | null;
  previewLabel: string;
  intro?: ReactNode;
  onApplied?: (result: WriteResult) => void;
  /** Blocks the current preview would write (null when there is no preview). */
  onPending?: (blocks: number[] | null) => void;
  onError?: (error: ApiError | null) => void;
}

function plural(n: number, one: string, many = `${one}s`) {
  return `${n} ${n === 1 ? one : many}`;
}

function blockList(blocks: number[]): string {
  return blocks.length === 1 ? `Block ${blocks[0]}` : `Blocks ${blocks.join(", ")}`;
}

/**
 * Safety-first write: the verb runs as a dry run first, its diff is shown, and only an
 * explicit Apply (naming the tag and block count) commits it with the very same params.
 * Trailer-writing verbs need the extra "allow trailer writes" toggle (include_trailers).
 */
export function WriteFlow(props: WriteFlowProps) {
  const { noun, verb, only, omit, initial, cardUid, previewLabel, intro, onApplied, onPending, onError } = props;
  const spec = getVerb(noun, verb);
  const canTrailers = spec.trailer_flag !== null;
  const canKeys = hasParam(spec, "include_keys");
  const id = useId();

  const [params, setParams] = useState<Record<string, unknown> | null>(null);
  const [includeTrailers, setIncludeTrailers] = useState(false);
  const [includeKeys, setIncludeKeys] = useState(false);
  const [preview, setPreview] = useState<WriteResult | null>(null);
  const [done, setDone] = useState<WriteResult | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const seq = useRef(0);

  const body = useCallback(
    (p: Record<string, unknown>, apply: boolean) => ({
      ...p,
      ...(includeTrailers && spec.trailer_flag ? { [spec.trailer_flag]: true } : {}),
      ...(includeKeys ? { include_keys: true } : {}),
      ...(apply ? { apply: true } : {}),
    }),
    [includeTrailers, includeKeys, spec.trailer_flag],
  );

  const fail = useCallback(
    (e: ApiError | null) => {
      setError(e);
      onError?.(e);
    },
    [onError],
  );

  const runPreview = useCallback(
    async (p: Record<string, unknown>) => {
      const mine = ++seq.current;
      setBusy(true);
      setDone(null);
      const res = await callVerb<WriteResult>(noun, verb, body(p, false));
      if (mine !== seq.current) return;
      setBusy(false);
      if (res.ok) {
        setPreview(res.result);
        fail(null);
        onPending?.(res.result.diff.filter((r) => r.action === "write").map((r) => r.block));
      } else {
        setPreview(null);
        fail(res.error);
        onPending?.(null);
      }
    },
    [noun, verb, body, fail, onPending],
  );

  // Changing a toggle re-runs the preview so Apply always commits exactly what is shown.
  const toggles = `${includeTrailers}|${includeKeys}`;
  const lastToggles = useRef(toggles);
  useEffect(() => {
    if (lastToggles.current === toggles) return;
    lastToggles.current = toggles;
    if (params) void runPreview(params);
  }, [toggles, params, runPreview]);

  function submit(p: Record<string, unknown>) {
    setParams(p);
    void runPreview(p);
  }

  function invalidate() {
    if (preview || error) {
      seq.current++;
      setPreview(null);
      setError(null);
      onPending?.(null);
    }
  }

  async function apply() {
    if (!params || !preview) return;
    const mine = ++seq.current;
    setBusy(true);
    // Carry the previewed tag's UID so the server refuses the write if the tag was
    // swapped between preview and apply. The browser's own wrongTag check is only feedback.
    const req = { ...body(params, true), expect_uid: preview.uid };
    const res = await callVerb<WriteResult>(noun, verb, req);
    if (mine !== seq.current) return;
    setBusy(false);
    setPreview(null);
    onPending?.(null);
    if (res.ok) {
      setDone(res.result);
      fail(null);
      onApplied?.(res.result);
    } else {
      fail(res.error);
    }
  }

  const layout = layoutFor(preview?.family ?? null, 64);
  const writes = preview ? preview.diff.filter((r) => r.action === "write").map((r) => r.block) : [];
  const touchesTrailers = spec.writes_trailers || writes.some((b) => isProtected(layout, b));
  const trailerGateOpen = !touchesTrailers || includeTrailers;
  const wrongTag = !!preview && !!cardUid && preview.uid !== cardUid;
  const noCard = !!preview && !cardUid;

  return (
    <div className="write-flow">
      {intro}
      <VerbForm
        spec={spec}
        only={only}
        omit={omit}
        initial={initial}
        submitLabel={previewLabel}
        busy={busy}
        onSubmit={submit}
        onChange={invalidate}
      />
      {(canTrailers || canKeys) && (
        <div className="write-flow__gates">
          {canTrailers && (
            <div className="field field--check field--gate">
              <input
                id={`${id}-trailers`}
                type="checkbox"
                checked={includeTrailers}
                onChange={(e) => setIncludeTrailers(e.target.checked)}
                aria-describedby={`${id}-trailers-hint`}
              />
              <label htmlFor={`${id}-trailers`}>Allow writes to sector trailers and block 0</label>
              <span id={`${id}-trailers-hint`} className="field__hint">
                {spec.writes_trailers
                  ? "This action rewrites sector trailers (keys and access bits). It can't be applied until you allow that."
                  : "Leave this off unless you mean to change keys or access bits; a wrong trailer can lock a sector for good."}
              </span>
            </div>
          )}
          {canKeys && (
            <div className="field field--check">
              <input
                id={`${id}-keys`}
                type="checkbox"
                checked={includeKeys}
                onChange={(e) => setIncludeKeys(e.target.checked)}
              />
              <label htmlFor={`${id}-keys`}>Show key bytes</label>
            </div>
          )}
        </div>
      )}
      {error && <ErrorNote error={error} />}
      {preview && (
        <div className="write-flow__preview">
          <DiffTable rows={preview.diff} uid={preview.uid} applied={false} layout={layout} />
          {writes.length === 0 ? (
            <p>Nothing to write: the tag already holds this.</p>
          ) : wrongTag || noCard ? (
            <p className="write-flow__stale">
              {noCard
                ? `Tag ${preview.uid} is no longer on the reader. Put it back and preview again.`
                : `A different tag is on the reader now (${cardUid}). Preview again before writing.`}
            </p>
          ) : (
            <div className="write-flow__confirm">
              <p>
                {blockList(writes)} will be overwritten on tag <span className="uid">{preview.uid}</span>
                {preview.summary?.unchanged ? `; ${plural(preview.summary.unchanged, "block")} stay as they are` : ""}.
                {" "}A snapshot is taken first so the write can be undone.
              </p>
              {touchesTrailers && !includeTrailers && (
                <p className="write-flow__gate-note">Allow writes to sector trailers above to continue.</p>
              )}
              <button
                type="button"
                className="console-button console-button--write"
                disabled={busy || !trailerGateOpen}
                onClick={() => void apply()}
              >
                Write {plural(writes.length, "block")} to tag {preview.uid}
              </button>
            </div>
          )}
        </div>
      )}
      {done && (
        <div role="status" className="write-flow__done">
          <p>
            Wrote {plural(done.summary?.write ?? done.diff.filter((r) => r.action === "write").length, "block")} to tag{" "}
            <span className="uid">{done.uid}</span>
            {typeof done.verified === "number" ? ` and verified ${done.verified}` : ""}.
          </p>
          {done.snapshot && (
            <p className="muted">
              Undo snapshot: <code>{done.snapshot}</code>
            </p>
          )}
          {done.journal && (
            <p className="muted">
              Write journal: <code>{done.journal}</code>
            </p>
          )}
        </div>
      )}
    </div>
  );
}
