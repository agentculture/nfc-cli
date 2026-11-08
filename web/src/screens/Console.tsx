import { useCallback, useEffect, useRef, useState } from "react";
import { initialAgentState, serializeAgentState, type AgentState } from "../agentState";
import { type ApiError, type CardInfo, type ReaderInfo, callVerb, getState, subscribeCard } from "../api/client";
import { BlocksPanel } from "./BlocksPanel";
import { DumpPanel } from "./DumpPanel";
import { MapLegend, MemoryMap } from "./MemoryMap";
import { NdefPanel } from "./NdefPanel";
import { SectorDetail } from "./SectorDetail";
import { Tabs } from "./Tabs";
import { familyName, isClassic, layoutFor, type Unit } from "./tagModel";
import { TraceDrawer } from "./TraceDrawer";
import "./screens.css";

interface TagInfo {
  uid?: string;
  family?: string;
  atr?: string;
  standard?: string;
  sectors?: number;
  blocks?: number;
  capability?: { size?: number; read_only?: boolean };
}

/** agent-state for this console: the shell contract plus what the screens show. */
export interface ConsoleAgentState extends AgentState {
  screen: string;
  busy: boolean;
  tag: { uid: string | null; family: string | null; blocks_read: number; pending_blocks: number[] } | null;
  last_error: { message: string; remediation: string; kind: string } | null;
}

function currentRoute(): string {
  return typeof window === "undefined" ? "/" : window.location.pathname || "/";
}

function readerSentence(status: AgentState["status"], reader: ReaderInfo | null): string {
  if (status === "loading" && !reader) return "Checking the reader…";
  if (!reader) return "Reader status unknown.";
  const name = reader.name ?? "The reader";
  const fw = reader.firmware ? ` (firmware ${reader.firmware})` : "";
  if (reader.connected && reader.error) return `${name} is connected but busy: ${reader.error}.`;
  if (reader.connected) return `${name} is connected${fw}.`;
  return reader.error ? `${name} is not connected: ${reader.error}.` : `${name} is not connected.`;
}

function cardSentence(card: CardInfo | null): string {
  if (!card) return "No tag on the reader.";
  const family = card.family ? `${familyName(card.family)} tag` : "Tag";
  return card.uid ? `${family} present, UID ${card.uid}.` : `${family} present.`;
}

export default function Console() {
  const [base, setBase] = useState<AgentState>(() => initialAgentState(currentRoute()));
  const [serverError, setServerError] = useState<string | null>(null);
  const [info, setInfo] = useState<TagInfo | null>(null);
  const [blocks, setBlocks] = useState<Record<string, string> | null>(null);
  const [revealed, setRevealed] = useState<Record<string, string> | null>(null);
  const [revealing, setRevealing] = useState(false);
  const [readError, setReadError] = useState<ApiError | null>(null);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [selected, setSelected] = useState<number | null>(0);
  const [pending, setPending] = useState<number[] | null>(null);
  const [tab, setTab] = useState("memory");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const gen = useRef(0);
  const loadedUid = useRef<string | null>(null);

  const forgetTag = useCallback(() => {
    gen.current++;
    loadedUid.current = null;
    setInfo(null);
    setBlocks(null);
    setRevealed(null);
    setRevealing(false);
    setReadError(null);
    setActionError(null);
    setPending(null);
    setBusy(false);
  }, []);

  const readTag = useCallback(async () => {
    const mine = ++gen.current;
    setBusy(true);
    setRevealed(null);
    const infoRes = await callVerb<TagInfo>("tag", "info", {});
    if (mine !== gen.current) return;
    if (!infoRes.ok) {
      setBusy(false);
      setReadError(infoRes.error);
      return;
    }
    setInfo(infoRes.result);
    const readRes = await callVerb<{ blocks: Record<string, string> }>("tag", "read", {});
    if (mine !== gen.current) return;
    setBusy(false);
    if (readRes.ok) {
      setBlocks(readRes.result.blocks ?? {});
      setReadError(null);
    } else {
      setBlocks(null);
      setReadError(readRes.error);
    }
  }, []);

  const load = useCallback(
    async (force = false) => {
      setBase((s) => ({ ...s, status: "loading" }));
      try {
        const { reader, card } = await getState();
        setServerError(null);
        setBase((s) => ({ ...s, status: "ready", view_ready: true, reader, card: card as AgentState["card"] }));
        const uid = (card?.uid as string | null | undefined) ?? null;
        if (!card) {
          if (loadedUid.current) forgetTag();
        } else if (force || uid !== loadedUid.current) {
          if (uid !== loadedUid.current) {
            forgetTag();
            setSelected(0);
          }
          loadedUid.current = uid;
          setNotice(null);
          void readTag();
        }
      } catch (err) {
        const detail = err instanceof Error ? err.message : String(err);
        setServerError(`Can't reach the nfc-cli server (${detail}).`);
        setBase((s) => ({ ...s, status: "error", view_ready: true, reader: null, card: null }));
        forgetTag();
      }
    },
    [forgetTag, readTag],
  );

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(
    () =>
      subscribeCard((ev) => {
        if (!ev.present) {
          if (loadedUid.current || ev.uid === null) {
            const gone = loadedUid.current;
            setBase((s) => (s.card ? { ...s, card: null } : s));
            if (gone) setNotice(`Tag ${gone} was removed from the reader.`);
            forgetTag();
          }
        } else if (ev.uid !== loadedUid.current) {
          void load();
        }
      }),
    [load, forgetTag],
  );

  const revealKeys = useCallback(async (unit: Unit) => {
    const forUid = loadedUid.current; // the tag we are revealing keys for
    setRevealing(true);
    const res = await callVerb<{ blocks: Record<string, string> }>("tag", "read", {
      block: unit.blocks[0],
      count: unit.blocks.length,
      include_keys: true,
    });
    if (loadedUid.current !== forUid) return; // tag swapped mid-read: drop this response
    setRevealing(false);
    if (res.ok) setRevealed(res.result.blocks);
    else setActionError(res.error);
  }, []);

  const card = base.card;
  const present = !!card;
  const uid = (card?.uid as string | null | undefined) ?? info?.uid ?? null;
  const family = info?.family ?? (card?.family as string | null | undefined) ?? null;
  const blockCount = blocks ? Object.keys(blocks).length : (info?.blocks ?? 0);
  const layout = layoutFor(family, isClassic(family) ? (info?.blocks ?? blockCount) : blockCount);
  const unit = selected !== null ? (layout.units[selected] ?? null) : null;

  const errors: string[] = [];
  if (serverError) errors.push(serverError);
  if (base.reader?.error) errors.push(`Reader: ${base.reader.error}`);
  if (readError) errors.push(readError.message);
  if (actionError) errors.push(actionError.message);
  const shownError = readError ?? actionError;

  const agentState: ConsoleAgentState = {
    ...base,
    errors,
    screen: present ? tab : "waiting",
    busy,
    tag: present
      ? { uid, family, blocks_read: blocks ? Object.keys(blocks).length : 0, pending_blocks: pending ?? [] }
      : null,
    last_error: shownError ? { message: shownError.message, remediation: shownError.remediation, kind: shownError.kind } : null,
  };

  const onApplied = () => void load(true);
  const shared = { cardUid: uid, onPending: setPending, onError: setActionError };
  const notFound = base.route !== "/";

  return (
    <>
      <script type="application/json" id="agent-state" dangerouslySetInnerHTML={{ __html: serializeAgentState(agentState) }} />
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <header className="console-header">
        <div className="shell-rail console-header__row">
          <p className="console-mark">nfc-cli</p>
          <p className="console-reader" aria-live="polite">
            {readerSentence(base.status, base.reader)}
          </p>
        </div>
      </header>
      <main id="main" tabIndex={-1} className="shell-rail console-main">
        {notFound ? (
          <section aria-labelledby="page-title">
            <h1 id="page-title">Page not found</h1>
            <p>
              Nothing lives at <code>{base.route}</code>. <a href="/">Go to the tag view</a>.
            </p>
          </section>
        ) : (
          <>
            <section aria-labelledby="page-title" className="tag-view">
              <div className="tag-view__text">
                <h1 id="page-title">{present ? familyName(family) : "Place a tag on the reader"}</h1>
                <p className="console-card" aria-live="polite">
                  {present ? (
                    cardSentence(card ? { ...card, family } : null)
                  ) : (
                    <>
                      {notice && <span>{notice} </span>}
                      <span>No tag on the reader.</span>
                    </>
                  )}
                </p>
                {present && info ? (
                  <dl className="tag-facts">
                    <div>
                      <dt>UID</dt>
                      <dd className="uid">{uid}</dd>
                    </div>
                    {info.standard && (
                      <div>
                        <dt>Standard</dt>
                        <dd>{info.standard}</dd>
                      </div>
                    )}
                    {info.sectors !== undefined && (
                      <div>
                        <dt>Memory</dt>
                        <dd>
                          {info.sectors} sectors, {info.blocks} blocks of 16 bytes
                        </dd>
                      </div>
                    )}
                    {info.capability?.size !== undefined && (
                      <div>
                        <dt>NDEF space</dt>
                        <dd>
                          {info.capability.size} bytes{info.capability.read_only ? ", read-only" : ""}
                        </dd>
                      </div>
                    )}
                    {info.atr && (
                      <div>
                        <dt>ATR</dt>
                        <dd className="mono">{info.atr}</dd>
                      </div>
                    )}
                  </dl>
                ) : (
                  <p className="muted">
                    {present
                      ? "Reading the tag…"
                      : "The tag’s UID, family and memory map will appear here. Nothing is written to a tag without a preview and your confirmation."}
                  </p>
                )}
                {serverError ? (
                  <div role="alert" className="console-error">
                    <p>{serverError}</p>
                    <p>
                      Start it with <code>nfc-cli ui serve</code>, then check again.
                    </p>
                  </div>
                ) : shownError ? (
                  <div role="alert" className="console-error">
                    <p>{shownError.message}</p>
                    <p>{shownError.remediation}</p>
                  </div>
                ) : null}
                <button
                  type="button"
                  className={present ? "console-button console-button--quiet console-button--recheck" : "console-button"}
                  onClick={() => void load(true)}
                >
                  Check reader again
                </button>
              </div>
              <div className="tag-view__specimen">
                <MemoryMap
                  layout={layout}
                  present={present}
                  uid={uid}
                  blocks={blocks}
                  pending={pending}
                  selected={selected}
                  onSelect={(i) => {
                    setSelected(i);
                    setRevealed(null);
                    setTab("memory");
                  }}
                />
                {present && <MapLegend layout={layout} />}
              </div>
            </section>
            {present && (
              <section aria-labelledby="tools-title" className="tools">
                <h2 id="tools-title" className="visually-hidden">
                  Work with tag {uid}
                </h2>
                <Tabs
                  label={`Work with tag ${uid ?? ""}`}
                  active={tab}
                  onChange={(t) => {
                    setTab(t);
                    setPending(null);
                    setActionError(null);
                  }}
                  tabs={[
                    {
                      id: "memory",
                      label: layout.kind === "classic" ? "Sectors" : "Pages",
                      panel: (
                        <SectorDetail
                          layout={layout}
                          unit={unit}
                          blocks={blocks}
                          revealed={revealed}
                          revealing={revealing}
                          onReveal={(u) => void revealKeys(u)}
                          onHide={() => setRevealed(null)}
                        />
                      ),
                    },
                    {
                      id: "ndef",
                      label: "NDEF",
                      panel: <NdefPanel {...shared} classic={isClassic(family)} onApplied={onApplied} />,
                    },
                    { id: "dump", label: "Dump and restore", panel: <DumpPanel {...shared} onApplied={onApplied} /> },
                    { id: "blocks", label: "Raw blocks", panel: <BlocksPanel {...shared} onApplied={onApplied} /> },
                  ]}
                />
              </section>
            )}
          </>
        )}
      </main>
      <footer className="shell-rail console-footer">
        <TraceDrawer />
      </footer>
    </>
  );
}
