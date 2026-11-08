import { useCallback, useEffect, useState } from "react";
import {
  type AgentState,
  type CardState,
  type ReaderState,
  initialAgentState,
  serializeAgentState,
} from "./agentState";

// Shell only (plan task t15): one placeholder route, "/", that loads
// GET /api/state and sets view_ready. Screens arrive in task t16.

const SECTORS = 16; // MIFARE Classic 1K: 16 sectors x 4 blocks, block 3 is the trailer
const BLOCKS_PER_SECTOR = 4;

function currentRoute(): string {
  return typeof window === "undefined" ? "/" : window.location.pathname || "/";
}

async function fetchState(): Promise<{ reader: ReaderState | null; card: CardState | null }> {
  const res = await fetch("/api/state", { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`GET /api/state returned HTTP ${res.status}`);
  const body = (await res.json()) as { reader?: ReaderState | null; card?: CardState | null };
  return { reader: body.reader ?? null, card: body.card ?? null };
}

function readerSentence(state: AgentState): string {
  if (state.status === "loading") return "Checking the reader…";
  if (!state.reader) return "Reader status unknown.";
  const name = state.reader.name ?? "The reader";
  return state.reader.connected ? `${name} is connected.` : `${name} is not connected.`;
}

function cardSentence(card: CardState | null): string {
  if (!card) return "No tag on the reader.";
  const family = card.family ? `${card.family} tag` : "Tag";
  return card.uid ? `${family} present, UID ${card.uid}.` : `${family} present.`;
}

/** The tag outline with its (empty) memory map: the console's hero object. */
function TagSlot({ card }: { card: CardState | null }) {
  return (
    <figure className={card ? "tag-slot tag-slot--present" : "tag-slot"} aria-hidden="true">
      <div className="tag-slot__map">
        {Array.from({ length: SECTORS }, (_, sector) => (
          <div className="tag-slot__sector" key={sector}>
            {Array.from({ length: BLOCKS_PER_SECTOR }, (_, block) => (
              <span
                key={block}
                className={
                  block === BLOCKS_PER_SECTOR - 1
                    ? "tag-slot__block tag-slot__block--trailer"
                    : "tag-slot__block"
                }
              />
            ))}
          </div>
        ))}
      </div>
    </figure>
  );
}

export default function App() {
  const [state, setState] = useState<AgentState>(() => initialAgentState(currentRoute()));

  const load = useCallback(async () => {
    setState((s) => ({ ...s, status: "loading" }));
    try {
      const { reader, card } = await fetchState();
      setState((s) => ({ ...s, status: "ready", view_ready: true, reader, card, errors: [] }));
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      setState((s) => ({
        ...s,
        status: "error",
        view_ready: true,
        reader: null,
        card: null,
        errors: [`Can't reach the nfc-cli server (${detail}).`],
      }));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const notFound = state.route !== "/";

  return (
    <>
      <script
        type="application/json"
        id="agent-state"
        dangerouslySetInnerHTML={{ __html: serializeAgentState(state) }}
      />
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <header className="console-header">
        <div className="shell-rail console-header__row">
          <p className="console-mark">nfc-cli</p>
          <p className="console-reader" aria-live="polite">
            {readerSentence(state)}
          </p>
        </div>
      </header>
      <main id="main" tabIndex={-1} className="shell-rail console-main">
        {notFound ? (
          <section aria-labelledby="page-title">
            <h1 id="page-title">Page not found</h1>
            <p>
              Nothing lives at <code>{state.route}</code>. <a href="/">Go to the tag view</a>.
            </p>
          </section>
        ) : (
          <section aria-labelledby="page-title" className="tag-view">
            <div className="tag-view__text">
              <h1 id="page-title">Place a tag on the reader</h1>
              <p className="console-card" aria-live="polite">
                {cardSentence(state.card)}
              </p>
              <p className="muted">
                The tag&rsquo;s UID, family and memory map will appear here. Nothing is written
                to a tag without a preview and your confirmation.
              </p>
              {state.errors.length > 0 && (
                <div role="alert" className="console-error">
                  <p>{state.errors[0]}</p>
                  <p>
                    Start it with <code>nfc-cli ui serve</code>, then check again.
                  </p>
                </div>
              )}
              <button type="button" className="console-button" onClick={() => void load()}>
                Check reader again
              </button>
            </div>
            <TagSlot card={state.card} />
          </section>
        )}
      </main>
    </>
  );
}
