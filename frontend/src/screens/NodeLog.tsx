/**
 * Everything one machine did, in a window big enough to read it in.
 *
 * **Why this is fetched and not kept.** The browser sees every envelope already
 * — `run.ts` has them all pass through its reducer — and keeps almost none of
 * them, on purpose: it keeps what the canvas draws. It drops the thinking and
 * the tool deltas, keeps only the newest tool, folds the agent's words into one
 * flat string, and wipes that string when a loop sends a node round again, so
 * lap one is gone the instant lap two starts. Hoarding all of it instead would
 * grow a long run's tab without bound to answer a question nobody asks most of
 * the time. So the window asks the server, which has been writing every last
 * event to `events.jsonl` since the run began, and the reducer stays small.
 *
 * The consequence worth having: this works on a run that finished last week as
 * well as on the one happening now.
 *
 * **Live, too.** After the fetch it taps the same `EventSource` the canvas is
 * on (`useRun`'s `subscribe`) rather than opening a second one, which would
 * cost a second replay of the whole log per open window.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { knowledgeBase, type FlowNode } from "../graph";
import type {
  AgentEvent,
  KnowledgeEntry,
  NodeRun,
  RunEvent,
  TodoItem,
} from "../types";

type Tab =
  | "input"
  | "output"
  | "chat"
  | "decisions"
  | "events"
  | "written"
  | "read"
  | "content";

const TABS: { id: Tab; name: string }[] = [
  { id: "input", name: "Input" },
  { id: "output", name: "Output" },
  { id: "chat", name: "Agent chat" },
  { id: "decisions", name: "Decisions" },
  { id: "events", name: "Run events" },
];

/** **Storage gets its own four, and they are storage's questions.**
 *
 * A memory and a base have no input, no output, no transcript and no decisions
 * — four tabs that would open on "this machine produced nothing" four times
 * over, for a node that is not a machine. What a shelf is asked is what went
 * in, who took something out, and what is in it now; the raw log sits under
 * all three like everywhere else.
 *
 * The same four for both kinds, because a memory and a base **are the same
 * kind of thing to whoever is reading** — one empties with the run and one
 * outlives it, and that difference belongs in what the panes say, not in which
 * panes there are. */
const STORAGE_TABS: { id: Tab; name: string }[] = [
  { id: "written", name: "Written" },
  { id: "read", name: "Read" },
  { id: "content", name: "Content" },
  { id: "events", name: "Run events" },
];

export function NodeLog({
  runId,
  node,
  nodeRun,
  subscribe,
  onClose,
}: {
  runId: string;
  node: FlowNode;
  /** The node's record on the run, for the two things the log cannot assemble
   * from its own events: the collected output text and the session id. */
  nodeRun: NodeRun | undefined;
  subscribe: (tap: (event: RunEvent) => void) => () => void;
  onClose: () => void;
}) {
  const nodeId = node.id;
  // Storage is a shelf, not a machine, and its window is a different window.
  const storage =
    node.data.definition.type === "memory" || node.data.definition.type === "knowledge";
  const tabs = storage ? STORAGE_TABS : TABS;
  const [events, setEvents] = useState<RunEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>(storage ? "written" : "chat");
  const seen = useRef(0);

  // The history. Refetched from scratch when the window is pointed at a
  // different machine — a card on the timeline can be clicked while this is
  // open, and the window that answered is the window that must change.
  useEffect(() => {
    let open = true;
    setEvents(null);
    setError(null);
    seen.current = 0;
    api
      .nodeEvents(runId, nodeId, 0)
      .then((page) => {
        if (!open) return;
        seen.current = page.last_seq;
        setEvents(page.events);
      })
      .catch((cause: unknown) => {
        if (open) setError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => {
      open = false;
    };
  }, [runId, nodeId]);

  // …and the tail. `seen` is the watermark the fetch ended on, so an event that
  // arrived while the request was in flight is appended once rather than twice
  // — the same `seq` watermark the SSE catch-up uses, for the same reason.
  useEffect(
    () =>
      subscribe((event) => {
        if (event.type === "run.status") return;
        if (event.node_id !== nodeId) return;
        if (event.seq <= seen.current) return;
        seen.current = event.seq;
        setEvents((previous) => (previous === null ? previous : [...previous, event]));
      }),
    [subscribe, nodeId],
  );

  // Esc closes, like the settings panel. A window this size over the canvas
  // needs a way out that is not a hunt for the ✕.
  useEffect(() => {
    const onKey = (key: KeyboardEvent) => {
      if (key.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const laps = useMemo(() => (events ? intoLaps(events) : []), [events]);

  // **The live state, not the run record's.** `nodeRun` is a snapshot taken
  // when the run was created and refreshed only when a control call returns
  // one, so a header that read it would call a machine `pending` while its
  // transcript scrolled past underneath — the loop reset it and nothing has
  // POSTed since. The node's `runtime` is the canvas's own live state, which is
  // what the pip beside this window is showing.
  const runtime = node.data.runtime;
  const status = runtime?.status ?? nodeRun?.status ?? null;
  const model = runtime?.model || nodeRun?.model || "";
  const cost = runtime?.costUsd ?? nodeRun?.cost_usd ?? 0;

  return (
    <div className="node-log-backdrop" onMouseDown={onClose}>
      <section
        className="node-log panel"
        role="dialog"
        aria-label={`Log for ${node.data.definition.label}`}
        // The backdrop closes on a click that lands on the backdrop. Stopping
        // it here is what keeps a click *inside* the window — selecting a line
        // of a transcript, say — from closing the thing being read.
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="node-log-head">
          <span className="who">{node.data.definition.label}</span>
          <span className="eyebrow">{node.data.definition.type}</span>
          {status && (
            <span className="status-word" data-status={status}>
              {status.replace("_", " ")}
            </span>
          )}
          {laps.length > 1 && (
            <span className="lap">
              {laps.length} lap{laps.length === 1 ? "" : "s"}
            </span>
          )}
          <span className="spacer" />
          {model && <span className="mono dim">{model}</span>}
          {cost ? <span className="mono dim">${cost.toFixed(4)}</span> : null}
          <button className="node-log-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>

        <nav className="node-log-tabs">
          {tabs.map((entry) => (
            <button
              key={entry.id}
              className={entry.id === tab ? "on" : undefined}
              onClick={() => setTab(entry.id)}
            >
              {entry.name}
            </button>
          ))}
        </nav>

        <div className="node-log-body">
          {error && <p className="problems-note">{error}</p>}
          {!error && events === null && <p className="dim">Reading the log…</p>}
          {!error && events !== null && (
            <>
              {tab === "written" && <WrittenPane node={node} events={events} />}
              {tab === "read" && <ReadPane node={node} events={events} />}
              {tab === "content" && <ContentPane node={node} runId={runId} />}
              {tab === "input" && (
                <InputPane events={events} laps={laps} />
              )}
              {tab === "output" && (
                <OutputPane laps={laps} nodeRun={nodeRun} events={events} />
              )}
              {tab === "chat" && <ChatPane laps={laps} />}
              {tab === "decisions" && <DecisionPane events={events} />}
              {tab === "events" && <EventPane events={events} />}
            </>
          )}
        </div>
      </section>
    </div>
  );
}

/* ---- laps ---------------------------------------------------------------
   A loop does not re-run a *different* node; it re-runs this one, and the
   second lap's transcript is a different thing to read from the first. The
   boundary is a `node.status` back to `pending` after the node has already
   been somewhere else — the same test the live reducer uses to decide it must
   throw the previous lap away. Here nothing is thrown away, which is the whole
   point of the window. */

type Lap = { index: number; events: RunEvent[] };

function intoLaps(events: RunEvent[]): Lap[] {
  const laps: Lap[] = [{ index: 1, events: [] }];
  let started = false;
  for (const event of events) {
    if (event.type === "node.status") {
      if (event.status === "pending" && started) {
        laps.push({ index: laps.length + 1, events: [] });
      } else if (event.status !== "pending") {
        started = true;
      }
    }
    laps[laps.length - 1].events.push(event);
  }
  // A node that never ran leaves one lap holding nothing worth a header.
  return laps.length === 1 && laps[0].events.length === 0 ? [] : laps;
}

/* ---- the agent's side, folded ------------------------------------------- */

type ChatItem =
  | { kind: "model"; model: string }
  | { kind: "say"; text: string }
  | { kind: "think"; text: string }
  | {
      kind: "tool";
      id: string;
      name: string;
      input: unknown;
      chunks: string;
      output: unknown;
      isError: boolean;
      done: boolean;
    }
  | { kind: "todos"; items: TodoItem[] }
  | { kind: "note"; text: string; bad: boolean };

/**
 * One lap's agent stream as a list of things to read.
 *
 * Deltas are glued back into paragraphs rather than rendered one per line:
 * `text.delta` is a transport detail, and a transcript that showed it would be
 * a column of three-word rows.
 */
function intoChat(events: RunEvent[]): ChatItem[] {
  const items: ChatItem[] = [];
  const tools = new Map<string, Extract<ChatItem, { kind: "tool" }>>();

  const append = (kind: "say" | "think", text: string) => {
    const last = items[items.length - 1];
    if (last && last.kind === kind) last.text += text;
    else items.push({ kind, text });
  };

  for (const envelope of events) {
    if (envelope.type !== "agent") continue;
    const event: AgentEvent = envelope.event;
    switch (event.type) {
      case "session.init":
        if (event.model) items.push({ kind: "model", model: event.model });
        break;
      case "text.delta":
        append("say", event.text);
        break;
      case "thinking.delta":
        append("think", event.text);
        break;
      case "tool.start": {
        const tool = {
          kind: "tool" as const,
          id: event.tool_use_id,
          name: event.name,
          input: event.input,
          chunks: "",
          output: null as unknown,
          isError: false,
          done: false,
        };
        tools.set(event.tool_use_id, tool);
        items.push(tool);
        break;
      }
      case "tool.delta": {
        const tool = tools.get(event.tool_use_id);
        if (tool) tool.chunks += event.chunk;
        break;
      }
      case "tool.end": {
        const tool = tools.get(event.tool_use_id);
        if (tool) {
          tool.output = event.output;
          tool.isError = event.is_error;
          tool.done = true;
        }
        break;
      }
      case "todo.update":
        items.push({ kind: "todos", items: event.items });
        break;
      case "permission.request":
        items.push({
          kind: "note",
          text: `asked to use ${event.tool_name} — denied by policy (no human is watching an unattended run)`,
          bad: true,
        });
        break;
      case "error":
        items.push({ kind: "note", text: event.message, bad: true });
        break;
      case "exit":
        items.push({
          kind: "note",
          text: `exit: ${event.cause}${event.detail ? ` — ${event.detail}` : ""}`,
          bad: event.code !== 0,
        });
        break;
      default:
        // `turn.end` — a cost and a duration, and both are in the footer
        // already. It stays in the raw tab, where nothing is folded away.
        break;
    }
  }
  return items;
}

function ChatPane({ laps }: { laps: Lap[] }) {
  const tail = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const chats = useMemo(
    () => laps.map((lap) => ({ index: lap.index, items: intoChat(lap.events) })),
    [laps],
  );
  const total = chats.reduce((count, lap) => count + lap.items.length, 0);

  // Follow the stream, but only from the bottom — the same rule the old run
  // log's `<pre>` followed, and for the same reason: being yanked back
  // mid-sentence on every delta is worse than not following at all.
  useEffect(() => {
    const element = tail.current;
    if (element && following.current) element.scrollTop = element.scrollHeight;
  }, [total]);

  if (total === 0) return <p className="dim">This machine has not said anything.</p>;

  return (
    <div
      className="chat-scroll"
      ref={tail}
      onScroll={(event) => {
        const element = event.currentTarget;
        following.current =
          element.scrollHeight - element.scrollTop - element.clientHeight < 40;
      }}
    >
      {chats.map((lap) => (
        <section key={lap.index} className="lap-block">
          {chats.length > 1 && <h4 className="lap-rule">lap {lap.index}</h4>}
          {lap.items.map((item, index) => (
            <ChatRow key={index} item={item} />
          ))}
        </section>
      ))}
    </div>
  );
}

function ChatRow({ item }: { item: ChatItem }) {
  if (item.kind === "model") return <p className="chat-model mono">{item.model}</p>;
  if (item.kind === "say") return <pre className="chat-say">{item.text}</pre>;
  if (item.kind === "think") {
    // Collapsed by default. It is the most voluminous thing in the log and the
    // least often the answer — but when it is the answer, nothing else is.
    return (
      <details className="chat-think">
        <summary>thinking · {item.text.length} characters</summary>
        <pre>{item.text}</pre>
      </details>
    );
  }
  if (item.kind === "todos") {
    return (
      <ol className="todos">
        {item.items.map((entry) => (
          <li key={entry.content} data-status={entry.status}>
            <span className="box">{TODO_MARK[entry.status] ?? "☐"}</span>
            <span>{entry.content}</span>
          </li>
        ))}
      </ol>
    );
  }
  if (item.kind === "note") {
    return <p className={item.bad ? "chat-note bad" : "chat-note"}>{item.text}</p>;
  }
  return (
    <details className="chat-tool" data-error={item.isError || undefined}>
      <summary>
        <b>{item.name}</b>
        <span className="target mono">{summarise(item.input)}</span>
        <span className="state">
          {item.isError ? "error" : item.done ? "done" : "running"}
        </span>
      </summary>
      {/* The **whole** argument object, which is the thing the canvas has never
          been able to show: "Edit src/run.ts" and the diff it wrote are
          different facts, and only one of them fits on a chip. */}
      <pre className="mono">{stringify(item.input)}</pre>
      {item.chunks && <pre className="mono dim">{item.chunks}</pre>}
      {item.done && <pre className="mono">{stringify(item.output)}</pre>}
    </details>
  );
}

const TODO_MARK: Record<string, string> = {
  completed: "☑",
  in_progress: "▸",
  pending: "☐",
};

/** The characteristic argument — what a tool is acting *on*. The same fields
 * `run.ts`'s `toolTarget` reads, so the chip in the window and the chip on the
 * timeline name the same thing. */
function summarise(input: unknown): string {
  if (!input || typeof input !== "object") return "";
  const fields = input as Record<string, unknown>;
  for (const key of ["file_path", "path", "command", "pattern", "url", "prompt"]) {
    const value = fields[key];
    if (typeof value === "string") return value.length > 120 ? `${value.slice(0, 120)}…` : value;
  }
  return "";
}

function stringify(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

/* ---- the other four panes ----------------------------------------------- */

/* **What arrived is read, never inferred.** This pane used to be handed a list
   built from the *drawing* — every incoming belt whose source had any text —
   because what the belts carried was assembled when a node started and thrown
   away. That is wrong for any belt that was not taken, and most visibly wrong
   for a back-edge, whose source usually runs *after* its target: a worker was
   shown a critique "from the reviewer, up the belt" on a lap where the
   reviewer had not run. The server now records each delivery (`run.incoming`),
   so the guess is gone rather than demoted to a fallback — a wrong answer
   nobody can see is a guess is worse than no answer. */
function InputPane({ events, laps }: { events: RunEvent[]; laps: Lap[] }) {
  const submitted = events.filter((event) => event.type === "run.input");
  const arrived = laps
    .map((lap) => ({
      index: lap.index,
      events: lap.events.filter(
        (event): event is Extract<RunEvent, { type: "run.incoming" }> =>
          event.type === "run.incoming",
      ),
    }))
    .filter((lap) => lap.events.length > 0);

  // Only when there is no record of its own: a gate's `run.decision.request`
  // carries the same string, so drawing both would show it twice. A run from
  // before the record still renders from it.
  const gates = arrived.length
    ? []
    : events.filter((event) => event.type === "run.decision.request");

  if (submitted.length === 0 && gates.length === 0 && arrived.length === 0) {
    // Two different silences, and telling them apart is the point: a machine
    // that ran must have been handed something, so an empty pane on one means
    // the run finished before any of this was written down.
    const ran = events.some(
      (event) => event.type === "node.status" && event.status !== "pending",
    );
    return (
      <p className="dim">
        {ran
          ? "This run finished before what arrived at a machine was written down."
          : "Nothing arrived at this machine."}
      </p>
    );
  }

  return (
    <div className="log-pane">
      {submitted.map((event) =>
        event.type === "run.input" ? (
          <article key={event.seq} className="log-block">
            <h4>{event.filename ? `submitted · ${event.filename}` : "submitted"}</h4>
            <pre className="stream">{event.text}</pre>
          </article>
        ) : null,
      )}
      {gates.map((event) =>
        event.type === "run.decision.request" ? (
          <article key={event.seq} className="log-block">
            <h4>what was put up for judgement</h4>
            <pre className="stream">{event.incoming}</pre>
          </article>
        ) : null,
      )}
      {arrived.map((lap) =>
        lap.events.map((event) =>
          event.parts.length === 0 ? (
            <article key={event.seq} className="log-block">
              <h4>{arrived.length > 1 ? `lap ${lap.index} · ` : ""}nothing arrived</h4>
            </article>
          ) : (
            event.parts.map((part, index) => (
              <article key={`${event.seq}-${index}`} className="log-block">
                <h4>
                  {arrived.length > 1 ? `lap ${lap.index} · ` : ""}
                  {part.source === "request"
                    ? "the run’s request — no belt delivered"
                    : `from “${part.label}”, up the belt`}
                </h4>
                <pre className="stream">{part.text}</pre>
              </article>
            ))
          ),
        ),
      )}
    </div>
  );
}

function OutputPane({
  laps,
  nodeRun,
  events,
}: {
  laps: Lap[];
  nodeRun: NodeRun | undefined;
  events: RunEvent[];
}) {
  const said = laps.map((lap) => ({
    index: lap.index,
    text: lap.events
      .filter(
        (event): event is Extract<RunEvent, { type: "agent" }> => event.type === "agent",
      )
      .map((event) => (event.event.type === "text.delta" ? event.event.text : ""))
      .join(""),
  }));
  const collected = nodeRun?.output_text ?? "";

  /* **What the machine says it did**, which for some machines is the only
     thing there is to say. A commit node never speaks and streams nothing, and
     a `discard` output does not even leave a commit behind — so a pane that
     only knew about text and transcripts reported "nothing" about a node that
     had just done its whole job. The sentence is the one on the node's last
     `node.status`, so it is the same words as the pip's tooltip and it replays
     from the log for a run that finished last week. */
  const reported = [...events]
    .reverse()
    .find(
      (event): event is Extract<RunEvent, { type: "node.status" }> =>
        event.type === "node.status" && Boolean(event.detail),
    )?.detail;

  if (!collected && !reported && said.every((lap) => !lap.text)) {
    return <p className="dim">This machine produced nothing.</p>;
  }

  return (
    <div className="log-pane">
      {reported && (
        <article className="log-block">
          <h4>what it did</h4>
          <pre className="stream">{reported}</pre>
        </article>
      )}
      {collected && (
        <article className="log-block">
          {/* What actually travels: the run record's own copy, which is the
              newest lap only — that is what the belts hand on. An output node
              has no outgoing belt, so for one of those the same field is what
              it was handed and the heading says so rather than naming a belt
              that is not drawn. */}
          <h4>
            {nodeRun?.type === "output" ? "what it was handed" : "what went down the belt"}
          </h4>
          <pre className="stream">{collected}</pre>
        </article>
      )}
      {said
        .filter((lap) => lap.text)
        .map((lap) => (
          <article key={lap.index} className="log-block">
            <h4>{said.length > 1 ? `everything said on lap ${lap.index}` : "everything said"}</h4>
            <pre className="stream">{lap.text}</pre>
          </article>
        ))}
    </div>
  );
}

/** `8.2 kB`, `640 B` — the same scale the two storage nodes print on the
 * canvas, because a size is a size wherever it is read. */
function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  return kb < 1024 ? `${kb.toFixed(1)} kB` : `${(kb / 1024).toFixed(1)} MB`;
}

type StorageEvent = Extract<RunEvent, { type: "run.storage" }>;

/** Every touch of this shelf, in the order it happened.
 *
 * **A history, not a state.** The node itself already prints the state — what
 * is in the memory now, what the base holds — and repeating it here would
 * answer the question the canvas answered. What the canvas cannot show is the
 * sequence: a memory that grew over four writes, a base that took two and
 * refused a third, and, in both cases, **which machine was at the other end**.
 * Each row is one `run.storage`, which is why this works identically on a run
 * that finished last week.
 */
function touches(events: RunEvent[], ops: StorageEvent["op"][]): StorageEvent[] {
  return events.filter(
    (event): event is StorageEvent =>
      event.type === "run.storage" && ops.includes(event.op),
  );
}

/** Who wrote to this shelf, and what landed. */
function WrittenPane({ node, events }: { node: FlowNode; events: RunEvent[] }) {
  const written = touches(events, ["write"]);
  const memory = node.data.definition.type === "memory";

  if (written.length === 0) {
    return (
      <p className="dim">
        {memory
          ? "Nothing was filed here. Wire a machine into this memory to fill it."
          : "This run wrote nothing to this base. Most runs do not — a base is a source of truth, and consulting one is the ordinary use."}
      </p>
    );
  }

  return (
    <div className="log-pane">
      {written.map((event) => (
        <article
          key={event.seq}
          className={`log-block${event.refused ? " is-refused" : ""}`}
        >
          <h4>
            {event.refused ? `refused · ${event.refused}` : "written"} ·{" "}
            {event.machine_label || event.machine_node_id}
            {event.lap > 1 && ` · lap ${event.lap}`}
          </h4>
          <pre className="mono">{event.detail}</pre>
          {memory ? (
            <p className="dim">
              {event.sections} section{event.sections === 1 ? "" : "s"} ·{" "}
              {size(event.bytes)}
              {event.truncated && " · truncated"} · {event.path}
            </p>
          ) : (
            <p className="dim">
              {event.tool} · {event.path || "the base"}
            </p>
          )}
        </article>
      ))}
    </div>
  );
}

/** Who took something out of it — and who was only told where it is.
 *
 * **An offer is not a read**, and the two are kept apart because the difference
 * is the whole answer to "was this shelf any use". A memory reader is handed a
 * path in its prompt and is free never to open it; a pane that called that a
 * read would report a cache hit that never happened. A knowledge read is always
 * a real one: it is a tool call that came back with bytes.
 */
function ReadPane({ node, events }: { node: FlowNode; events: RunEvent[] }) {
  const seen = touches(events, ["offer", "read"]);
  const memory = node.data.definition.type === "memory";

  if (seen.length === 0) {
    return (
      <p className="dim">
        {memory
          ? "Nobody was ever told about this memory. Wire it out to a machine below the one that fills it — a memory nobody was pointed at saves no work."
          : "Nothing consulted this base on this run."}
      </p>
    );
  }

  return (
    <div className="log-pane">
      {seen.map((event) => (
        <article key={event.seq} className={`log-block${event.refused ? " is-refused" : ""}`}>
          <h4>
            {event.refused
              ? `refused · ${event.refused}`
              : event.op === "offer"
                ? "offered"
                : "opened"}{" "}
            · {event.machine_label || event.machine_node_id}
            {event.lap > 1 && ` · lap ${event.lap}`}
          </h4>
          <pre className="mono">{event.detail}</pre>
        </article>
      ))}
      {memory && touches(events, ["read"]).length === 0 && (
        // Worth saying rather than leaving as an absence: this is the state a
        // memory that cost tokens to build and saved none is actually in, and
        // it looks identical to a healthy one unless somebody says so.
        <p className="dim">
          Offered, but never opened — a pointer is a reference, and this machine
          did not follow it.
        </p>
      )}
    </div>
  );
}

/** What is actually in it.
 *
 * Fetched when the tab is opened rather than with the window: a document is the
 * one thing here that can be large, and most visits to this window are about
 * the two panes before it.
 *
 * The two shelves answer differently because they *are* different, and the pane
 * says which: a base is durable and its files can always be opened, while a
 * memory is discarded when the run ends and kept only when the run failed. "The
 * memory was empty" and "the memory was cleaned up" are different answers, and
 * a reader who cannot tell them apart goes looking for a bug that is not there.
 *
 * **This pane stays read-only**, and that is a choice rather than a gap. The
 * node log is a view of *one run*, and its other panes are history; a base's
 * files are here so that "a machine wrote `timeline.md`" can be checked
 * without leaving the canvas. Maintaining a shelf is a different act, in a
 * different place — the library, where a base is a document you keep rather
 * than evidence of what just happened, and where nothing may be holding its
 * lock (`ProjectList.KnowledgeEditor`).
 */
function ContentPane({ node, runId }: { node: FlowNode; runId: string }) {
  const memory = node.data.definition.type === "memory";
  const baseId = memory ? "" : knowledgeBase(node.data.definition);

  const [text, setText] = useState<string | null>(null);
  const [entries, setEntries] = useState<KnowledgeEntry[] | null>(null);
  const [opened, setOpened] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setText(null);
    setEntries(null);
    setOpened(null);
    setError(null);
    const load = memory
      ? api
          .memoryDocument(runId, node.id)
          .then((document) => live && setText(document.text))
      : baseId
        ? api.knowledgeBase(baseId).then((base) => live && setEntries(base.entries))
        : Promise.resolve(setError("This node points at no base yet."));
    load.catch((cause: unknown) => {
      if (live) setError(cause instanceof Error ? cause.message : String(cause));
    });
    return () => {
      live = false;
    };
  }, [memory, baseId, runId, node.id]);

  const open = (path: string) => {
    setOpened(path);
    setText(null);
    setError(null);
    api
      .knowledgeEntry(baseId, path)
      .then((entry) => setText(entry.text))
      .catch((cause: unknown) =>
        setError(cause instanceof Error ? cause.message : String(cause)),
      );
  };

  if (error) return <p className="dim">{error}</p>;

  if (memory) {
    return text === null ? (
      <p className="dim">Reading the document…</p>
    ) : (
      <pre className="stream">{text}</pre>
    );
  }

  return (
    <div className="log-pane">
      {entries === null && <p className="dim">Reading the base…</p>}
      {entries?.length === 0 && (
        <p className="dim">This base has no entries yet.</p>
      )}
      {entries && entries.length > 0 && (
        <nav className="entry-list">
          {entries.map((entry) => (
            <button
              key={entry.path}
              type="button"
              className={entry.path === opened ? "on" : undefined}
              onClick={() => open(entry.path)}
            >
              <span className="mono">{entry.path}</span>
              <span className="dim">
                {size(entry.bytes)}
                {entry.summary && ` · ${entry.summary}`}
              </span>
            </button>
          ))}
        </nav>
      )}
      {opened && text === null && <p className="dim">Reading {opened}…</p>}
      {opened && text !== null && <pre className="stream">{text}</pre>}
    </div>
  );
}

function DecisionPane({ events }: { events: RunEvent[] }) {
  const relevant = events.filter(
    (event) =>
      event.type === "run.decision.request" ||
      event.type === "run.decision" ||
      event.type === "run.question.request" ||
      event.type === "run.question",
  );
  if (relevant.length === 0) {
    return <p className="dim">Nothing was asked of anybody here.</p>;
  }
  return (
    <div className="log-pane">
      {relevant.map((event) => {
        if (event.type === "run.decision.request") {
          return (
            <article key={event.seq} className="log-block">
              <h4>asked</h4>
              <p>{event.prompt}</p>
            </article>
          );
        }
        if (event.type === "run.decision") {
          return (
            <article key={event.seq} className="log-block">
              <h4 className={event.approved ? "good" : "bad"}>
                {event.approved ? "approved" : "rejected"}
              </h4>
              {event.note && <pre className="stream">{event.note}</pre>}
            </article>
          );
        }
        if (event.type === "run.question.request") {
          return (
            <article key={event.seq} className="log-block">
              <h4>asked {event.questions.length} question{event.questions.length === 1 ? "" : "s"}</h4>
              <ul className="plain">
                {event.questions.map((question) => (
                  <li key={question.question}>
                    <b>{question.header}</b> — {question.question}
                  </li>
                ))}
              </ul>
            </article>
          );
        }
        if (event.type === "run.question") {
          return (
            <article key={event.seq} className="log-block">
              <h4>answered</h4>
              <ul className="plain">
                {Object.entries(event.answers).map(([question, answer]) => (
                  <li key={question}>
                    <b>{question}</b> — {answer}
                  </li>
                ))}
              </ul>
            </article>
          );
        }
        return null;
      })}
    </div>
  );
}

/** The raw envelopes. The escape hatch for when a tab folded away the one
 * thing that mattered — and the only place `turn.end` and the deltas are
 * visible as themselves. */
function EventPane({ events }: { events: RunEvent[] }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <ol className="raw-events">
      {events.map((event) => (
        <li key={event.seq}>
          <button onClick={() => setOpen(open === event.seq ? null : event.seq)}>
            <span className="seq mono">{event.seq}</span>
            <span className="kind mono">
              {event.type}
              {event.type === "agent" ? ` · ${event.event.type}` : ""}
            </span>
            <span className="when mono dim">{event.timestamp.slice(11, 19)}</span>
          </button>
          {open === event.seq && <pre className="mono">{stringify(event)}</pre>}
        </li>
      ))}
    </ol>
  );
}
