/**
 * The live run, in the browser: one `EventSource`, one reducer, one state.
 *
 * **Why the canvas needs almost nothing from this.** Stage 3 shaped a React
 * Flow node's `data` as exactly `{definition, runtime}`, so lighting up the
 * canvas is one `setNodes` over `data.runtime` — every node component already
 * reads it. This module's whole job is to turn a stream of envelopes into the
 * `runtime` object each node should carry, and to keep `runtime` out of
 * anything that gets saved (it never enters `definition`, and `fromFlowNode`
 * writes `runtime: null` regardless).
 *
 * **Reconnection is the browser's, and it is free.** `EventSource` retries on
 * its own and replays `Last-Event-ID`; the server resumes at that `seq`. There
 * is no reconnect logic here, deliberately — the one thing we *must* do is
 * `close()` when the run reaches a terminal state, because otherwise the
 * browser treats the server's clean end-of-stream as a dropped connection and
 * reconnects forever to a run that will never say anything again.
 *
 * **Every frame carries `event: <type>`,** so a plain `onmessage` handler would
 * never fire — the browser dispatches named events to named listeners. Hence
 * the explicit loop over `RUN_EVENT_TYPES`.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "./api";
import {
  RUN_EVENT_TYPES,
  type AgentEvent,
  TERMINAL_RUN_STATUSES,
  type NodeRuntime,
  type OverallRunStatus,
  type Run,
  type RunArtifact,
  type RunStorage,
  type RunEvent,
  type ScopeQuestion,
  type ToolActivity,
} from "./types";

/** What one node looks like right now, by node id. */
export type Runtimes = Record<string, NodeRuntime>;

/**
 * One turn of what the operator and the run said to each other.
 *
 * Beside `decisions` and `questions` rather than instead of them, and the
 * difference is the whole reason it exists: those two hold what is being asked
 * *now* and are cleared the moment it is answered, because a form on screen
 * that nobody is waiting on is a lie. This is the record of what was asked and
 * what was said back, and it is never cleared — the chat panel is a
 * conversation, and a conversation that forgot its last exchange the instant it
 * ended would be a form with extra chrome.
 *
 * Built from the log like everything else here, so a tab that joined late reads
 * the same conversation as the tab that was open from the start.
 */
export type Exchange = { seq: number; nodeId: string } & (
  | { kind: "input"; text: string; filename: string | null }
  | { kind: "asked"; requestId: string; questions: ScopeQuestion[] }
  | { kind: "answered"; requestId: string; answers: Record<string, string> }
  | { kind: "gate"; prompt: string; incoming: string }
  | { kind: "verdict"; approved: boolean; note: string }
);

export type RunState = {
  run: Run | null;
  status: OverallRunStatus | null;
  detail: string | null;
  runtimes: Runtimes;
  costUsd: number;
  /** The branch this run owns, and the commit it produced. Survives the run —
   * it names a ref in the operator's own repository. */
  artifacts: RunArtifact[];
  /** Every touch of a shelf, by storage node id, in arrival order.
   *
   * Kept per node rather than flat because every reader asks the same question
   * — *what has happened to this shelf* — and because it is what tells a memory
   * or a base that it has a log worth opening. **No runtime is created for
   * these nodes**: storage is not a station, the pip stays dark, and nothing
   * here puts a card on the timeline. */
  storage: Record<string, RunStorage[]>;
  /**
   * What each memory node has filed, by node id — the **latest** state of the
   * document, not one entry per write.
   *
   * A map beside `artifacts` rather than a filter over it: a memory is written
   * again on every lap of a loop and by every writer wired into it, so the
   * append-only list holds several entries for one node, all but the last of
   * them stale. The node on the canvas wants the current size, and a footer
   * that listed the same memory four times would be counting writes rather
   * than showing storage.
   */
  memories: Record<string, RunArtifact>;
  /** The human nodes the walk is parked on, by node id.
   *
   * Driven by `run.decision.request` rather than by the run record, which is a
   * snapshot from before the walk started and never sees a node that parks
   * mid-graph. The event carries what the belts delivered, so a tab that joined
   * late renders the same form from the log alone. */
  decisions: Record<string, { prompt: string; incoming: string; loops: boolean }>;
  /** The open questionnaires a `scope` node is waiting on, by **request** id.
   *
   * Keyed by request rather than by node, which is the difference between
   * negotiating and gating: a gate asks once, so its node names the question;
   * a scope node asks repeatedly, and only the request does. Driven by
   * `run.question.request` and cleared by `run.question` — so a tab that joined
   * late renders the open form from the log alone, and a tab that was already
   * open loses the form the moment anybody answers it. */
  questions: Record<string, { nodeId: string; questions: ScopeQuestion[] }>;
  /** What the operator and the run have said to each other, oldest first. */
  exchanges: Exchange[];
  /** Blockers the *server* refused the run with, or a control-call failure. */
  problems: string[];
  error: string | null;
  busy: boolean;
};

const EMPTY: RunState = {
  run: null,
  status: null,
  detail: null,
  runtimes: {},
  costUsd: 0,
  artifacts: [],
  storage: {},
  memories: {},
  decisions: {},
  questions: {},
  exchanges: [],
  problems: [],
  error: null,
  busy: false,
};

/** The characteristic argument of a tool call — what it is acting *on*. */
function toolTarget(name: string, input: unknown): string {
  if (!input || typeof input !== "object") return "";
  const fields = input as Record<string, unknown>;
  for (const key of ["file_path", "path", "command", "pattern", "url", "prompt"]) {
    const value = fields[key];
    if (typeof value === "string") return value;
  }
  if (name === "TodoWrite") return "checklist";
  return "";
}

function withNode(
  runtimes: Runtimes,
  nodeId: string,
  patch: Partial<NodeRuntime>,
): Runtimes {
  const existing = runtimes[nodeId] ?? { status: "pending" as const };
  return { ...runtimes, [nodeId]: { ...existing, ...patch } };
}

/** Drop one key. Its own function so the reducer stays a list of returns. */
function withoutNode<T>(map: Record<string, T>, nodeId: string): Record<string, T> {
  const { [nodeId]: _removed, ...rest } = map;
  return rest;
}

/**
 * One envelope applied to the state. Pure, so the same function replays a log.
 */
export function reduceRunEvent(state: RunState, event: RunEvent): RunState {
  switch (event.type) {
    case "run.status": {
      if (!TERMINAL_RUN_STATUSES.includes(event.status)) {
        return { ...state, status: event.status, detail: event.detail };
      }
      // The run is over, so `pending` no longer means "about to start" — it
      // means the walk never came this way, either because the branch above it
      // was not taken or because the run ended first. Said as a detail, not as
      // a status: inventing a sixth node state would make a replay describe
      // something that never happened (`models.NodeStatus`).
      const runtimes: Runtimes = {};
      for (const [nodeId, runtime] of Object.entries(state.runtimes)) {
        runtimes[nodeId] =
          runtime.status === "pending" ? { ...runtime, detail: "not reached" } : runtime;
      }
      return { ...state, status: event.status, detail: event.detail, runtimes, decisions: {} };
    }
    case "node.status": {
      // A node that moved off `waiting_input` is no longer asking anything —
      // including when it moved because the run was cancelled out from under it.
      const decisions = state.decisions[event.node_id] && event.status !== "waiting_input"
        ? withoutNode(state.decisions, event.node_id)
        : state.decisions;
      // **A node sent round again by a loop starts empty.** `pending` after a
      // node has already run means `_reset_loop` reopened it for another lap,
      // and everything assembled here belongs to the lap that just ended — the
      // streamed text, the tool chip, the todos. Carrying them over would show
      // lap one's transcript under lap two's header, which is the one way this
      // surface can lie about what a machine is doing right now. The artifact
      // is dropped with them: the worktree event is re-emitted only if a new
      // worktree is cut, and this node is reusing the warm one.
      const reopened =
        event.status === "pending" && (state.runtimes[event.node_id]?.status ?? "pending") !== "pending";
      return {
        ...state,
        decisions,
        runtimes: withNode(state.runtimes, event.node_id, {
          status: event.status,
          detail: event.detail,
          lap: event.lap,
          ...(reopened
            ? {
                text: undefined,
                tool: null,
                toolCount: undefined,
                todos: undefined,
                artifact: null,
              }
            : {}),
        }),
      };
    }
    case "run.decision.request":
      return {
        ...state,
        exchanges: [
          ...state.exchanges,
          {
            seq: event.seq,
            nodeId: event.node_id,
            kind: "gate",
            prompt: event.prompt,
            incoming: event.incoming,
          },
        ],
        decisions: {
          ...state.decisions,
          [event.node_id]: {
            prompt: event.prompt,
            incoming: event.incoming,
            // Absent on a run log written before a sign-off could close a loop,
            // and false is what those runs meant.
            loops: event.loops === true,
          },
        },
      };
    case "run.decision":
      return {
        ...state,
        exchanges: [
          ...state.exchanges,
          {
            seq: event.seq,
            nodeId: event.node_id,
            kind: "verdict",
            approved: event.approved,
            note: event.note,
          },
        ],
        decisions: withoutNode(state.decisions, event.node_id),
        runtimes: withNode(state.runtimes, event.node_id, {
          text: event.note || undefined,
        }),
      };
    case "run.question.request":
      return {
        ...state,
        exchanges: [
          ...state.exchanges,
          {
            seq: event.seq,
            nodeId: event.node_id,
            kind: "asked",
            requestId: event.request_id,
            questions: event.questions,
          },
        ],
        questions: {
          ...state.questions,
          [event.request_id]: {
            nodeId: event.node_id,
            questions: event.questions,
          },
        },
      };
    case "run.question":
      return {
        ...state,
        exchanges: [
          ...state.exchanges,
          {
            seq: event.seq,
            nodeId: event.node_id,
            kind: "answered",
            requestId: event.request_id,
            answers: event.answers,
          },
        ],
        // Keyed by request, so `withoutNode` is doing its job on a different
        // kind of key — the function drops one entry and does not care what the
        // entry is about.
        questions: withoutNode(state.questions, event.request_id),
        // What the operator said, on the node, the way a decision's note is.
        // The node is mid-session and about to go back to running: this is the
        // last thing it was told, not its output.
        runtimes: withNode(state.runtimes, event.node_id, {
          text: Object.values(event.answers).join(" · ") || undefined,
        }),
      };
    case "run.input":
      return {
        ...state,
        exchanges: [
          ...state.exchanges,
          {
            seq: event.seq,
            nodeId: event.node_id,
            kind: "input",
            text: event.text,
            filename: event.filename,
          },
        ],
        runtimes: withNode(state.runtimes, event.node_id, {
          // A PRD is a document, and a node is not a document viewer. The
          // shell's `<pre>` scrolls, so the whole thing is there to read — but
          // what the node is *for* is saying which document produced this run,
          // so the filename leads.
          text: event.filename ? `${event.filename}\n\n${event.text}` : event.text,
        }),
      };
    case "run.cost":
      return {
        ...state,
        costUsd: event.total_usd,
        runtimes: withNode(state.runtimes, event.node_id, {
          costUsd: (state.runtimes[event.node_id]?.costUsd ?? 0) + event.delta_usd,
        }),
      };
    case "run.artifact": {
      const artifact: RunArtifact = {
        nodeId: event.node_id,
        kind: event.kind,
        codebaseId: event.codebase_id,
        branch: event.branch,
        path: event.path,
        baseBranch: event.base_branch,
        baseCommit: event.base_commit,
        commitSha: event.commit_sha,
        fileCount: event.file_count,
        detail: event.detail,
        remote: event.remote,
        mergedInto: event.merged_into,
        bytes: event.bytes,
        truncated: event.truncated,
      };
      return {
        ...state,
        // Kept in arrival order, worktree before commit — a replay of the log
        // and a live run therefore produce the same list.
        artifacts: [...state.artifacts, artifact],
        // Last write wins, which is the whole point of the map: lap 2 of a
        // writer replaces its section in the document, so lap 2's size is the
        // document's size and lap 1's is a number that was never true again.
        memories:
          event.kind === "memory"
            ? { ...state.memories, [event.node_id]: artifact }
            : state.memories,
        // **Only onto a machine that is running.** A `memory` or `knowledge`
        // artifact names a node that never runs and has no status of its own,
        // and creating a runtime for it here would put a pip on the canvas and
        // a card on the timeline for a node the walk is never going to reach —
        // the canvas claiming storage is a station on the line. Storage reads
        // its own history out of `artifacts` above, which is a list of what
        // happened and not a claim that anything ran. Every other artifact kind
        // is emitted by a machine that is mid-run, so it already has one.
        runtimes: state.runtimes[event.node_id]
          ? withNode(state.runtimes, event.node_id, { artifact })
          : state.runtimes,
      };
    }
    case "run.storage": {
      const entry: RunStorage = {
        nodeId: event.node_id,
        store: event.store,
        op: event.op,
        machineNodeId: event.machine_node_id,
        machineLabel: event.machine_label,
        lap: event.lap,
        path: event.path,
        bytes: event.bytes,
        sections: event.sections,
        truncated: event.truncated,
        baseId: event.base_id,
        tool: event.tool,
        refused: event.refused,
        detail: event.detail,
        seq: event.seq,
      };
      return {
        ...state,
        storage: {
          ...state.storage,
          [event.node_id]: [...(state.storage[event.node_id] ?? []), entry],
        },
      };
    }
    case "agent":
      return { ...state, runtimes: reduceAgent(state.runtimes, event.node_id, event.event) };
    default:
      return state;
  }
}

function reduceAgent(runtimes: Runtimes, nodeId: string, event: AgentEvent): Runtimes {
  const current = runtimes[nodeId] ?? { status: "running" as const };
  switch (event.type) {
    case "text.delta":
      return withNode(runtimes, nodeId, { text: (current.text ?? "") + event.text });
    case "tool.start": {
      const tool: ToolActivity = {
        id: event.tool_use_id,
        name: event.name,
        target: toolTarget(event.name, event.input),
        state: "running",
      };
      return withNode(runtimes, nodeId, { tool, toolCount: (current.toolCount ?? 0) + 1 });
    }
    case "tool.end":
      return withNode(runtimes, nodeId, {
        tool: current.tool ? { ...current.tool, state: event.is_error ? "error" : "done" } : null,
      });
    case "todo.update":
      return withNode(runtimes, nodeId, { todos: event.items });
    case "error":
      return withNode(runtimes, nodeId, { detail: event.message });
    case "permission.request":
      // There is nobody to ask (`agent/claude.py`, the permission policy): an
      // unattended run answers with a policy, and a request that still reaches
      // here was denied by it. Said out loud rather than swallowed, because a
      // node that quietly did less than it was asked to is the worst outcome.
      return withNode(runtimes, nodeId, {
        detail: `asked to use ${event.tool_name} — denied by policy (no human is watching an unattended run)`,
      });
    case "session.init":
      // The one thing worth taking off this event: the model the provider
      // actually chose. Which is not necessarily the one anything asked for —
      // a node and a project may both defer, and then the CLI decides. The
      // node reports what happened rather than what was requested.
      return event.model ? withNode(runtimes, nodeId, { model: event.model }) : runtimes;
    default:
      // `thinking.delta`, `tool.delta`, `turn.end`, `exit`: logged and
      // replayable, but the node's surface says nothing about them. An event
      // this reducer ignores is still in `events.jsonl`.
      return runtimes;
  }
}

function isTerminal(status: OverallRunStatus | null): boolean {
  return status !== null && TERMINAL_RUN_STATUSES.includes(status);
}

function message(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

/**
 * Start, watch, submit to and cancel one run.
 *
 * `watch` is separate from `start` so a run that is already in flight — after a
 * reload, or in a second tab — is attached to the same way a new one is. That
 * is the late-joining client the server's catch-up exists for.
 */
export function useRun(projectId: string, factoryId: string) {
  const [state, setState] = useState<RunState>(EMPTY);
  const source = useRef<EventSource | null>(null);
  /**
   * Who else wants to see the envelopes, besides the reducer.
   *
   * A node log window has fetched one machine's history and needs the events
   * that arrive *after* that fetch, which is a different question from the one
   * `RunState` answers — it wants the envelopes themselves, all of them, for
   * one node, rather than the small summary the canvas draws. A second
   * `EventSource` would get them at the cost of a second replay of the whole
   * log and a second connection per open window; a tap on the one stream costs
   * nothing. In a ref, not state: adding a listener must not re-run `watch`
   * and reconnect the stream underneath it.
   */
  const taps = useRef(new Set<(event: RunEvent) => void>());

  /** Watch the raw stream. Returns the unsubscribe; call it on unmount. */
  const subscribe = useCallback((tap: (event: RunEvent) => void) => {
    taps.current.add(tap);
    return () => {
      taps.current.delete(tap);
    };
  }, []);

  const disconnect = useCallback(() => {
    source.current?.close();
    source.current = null;
  }, []);

  const watch = useCallback(
    (runId: string) => {
      disconnect();
      const stream = new EventSource(`/api/runs/${runId}/events`);
      source.current = stream;
      const onEvent = (message_: MessageEvent<string>) => {
        const event = JSON.parse(message_.data) as RunEvent;
        setState((previous) => {
          const next = reduceRunEvent(previous, event);
          if (event.type === "run.status" && isTerminal(event.status)) {
            // The server has nothing left to say. Closing here is what stops
            // `EventSource` from reconnecting to a finished run forever.
            stream.close();
            if (source.current === stream) source.current = null;
          }
          return next;
        });
        // After the reducer, never inside it: `setState`'s updater is called
        // twice under StrictMode, and a tap that ran in there would append
        // every event to an open log window twice.
        for (const tap of taps.current) tap(event);
      };
      for (const type of RUN_EVENT_TYPES) stream.addEventListener(type, onEvent as EventListener);
      stream.onerror = () => {
        // Not fatal: this is also what a reconnect looks like from here, and
        // the browser is already retrying with `Last-Event-ID`.
        if (stream.readyState === EventSource.CLOSED) {
          setState((previous) => ({ ...previous, error: "the event stream closed" }));
        }
      };
    },
    [disconnect],
  );

  useEffect(() => disconnect, [disconnect]);

  // Attach to a run that is already in flight — after a reload, or in a second
  // tab. This is the late-joining client the server's catch-up exists for: the
  // stream replays from `seq` 1, so the canvas fills in with everything that
  // happened before this page existed rather than starting blank at "now".
  useEffect(() => {
    let attached = true;
    api
      .runs()
      .then((runs) => {
        const inFlight = runs.find(
          (candidate) =>
            candidate.project_id === projectId &&
            candidate.factory_id === factoryId &&
            !isTerminal(candidate.status),
        );
        if (!attached || !inFlight) return;
        setState({ ...EMPTY, run: inFlight, status: inFlight.status });
        watch(inFlight.id);
      })
      .catch(() => {
        /* no run history is not an error worth a banner */
      });
    return () => {
      attached = false;
    };
  }, [projectId, factoryId, watch]);

  const start = useCallback(async () => {
    setState({ ...EMPTY, busy: true });
    try {
      const run = await api.startRun(projectId, factoryId);
      setState({ ...EMPTY, run, status: run.status, busy: false });
      watch(run.id);
      return run;
    } catch (cause) {
      // 422 carries every reason the graph cannot run; 409 says a run is
      // already in flight. Both are sentences the operator can act on.
      const problems =
        cause instanceof ApiError && cause.status === 422
          ? cause.message.split("; ")
          : [];
      setState({
        ...EMPTY,
        problems,
        error: problems.length ? null : message(cause),
        busy: false,
      });
      return null;
    }
  }, [projectId, factoryId, watch]);

  const submit = useCallback(
    async (text: string, options: { nodeId?: string; filename?: string } = {}) => {
      const runId = state.run?.id;
      if (!runId) return;
      setState((previous) => ({ ...previous, busy: true, error: null }));
      try {
        const run = await api.submitRunInput(runId, text, options);
        // The run record is the authority on which inputs are still waiting —
        // the walk starts on the *last* submission, so a form that disappeared
        // because the server said so cannot disagree with the server.
        setState((previous) => ({ ...previous, run, status: run.status, busy: false }));
      } catch (cause) {
        setState((previous) => ({ ...previous, busy: false, error: message(cause) }));
      }
    },
    [state.run?.id],
  );

  const decide = useCallback(
    async (nodeId: string, approved: boolean, note = "") => {
      const runId = state.run?.id;
      if (!runId) return;
      setState((previous) => ({ ...previous, busy: true, error: null }));
      try {
        const run = await api.submitRunDecision(runId, nodeId, approved, note);
        // The form is cleared by the stream, not here: the server is the
        // authority on what is still being asked, and a form that vanished
        // because this call returned could disagree with it.
        setState((previous) => ({ ...previous, run, busy: false }));
      } catch (cause) {
        setState((previous) => ({ ...previous, busy: false, error: message(cause) }));
      }
    },
    [state.run?.id],
  );

  const answer = useCallback(
    async (nodeId: string, requestId: string, answers: Record<string, string>) => {
      const runId = state.run?.id;
      if (!runId) return;
      setState((previous) => ({ ...previous, busy: true, error: null }));
      try {
        const run = await api.submitRunAnswer(runId, nodeId, requestId, answers);
        // The form is cleared by the stream, not here — `decide`'s reason: the
        // server is the authority on what is still being asked, and a scope
        // node's *next* question arrives the same way this one's answer left.
        setState((previous) => ({ ...previous, run, busy: false }));
      } catch (cause) {
        setState((previous) => ({ ...previous, busy: false, error: message(cause) }));
      }
    },
    [state.run?.id],
  );

  /** The questionnaires a `scope` node is waiting on, in factory order.
   *
   * In factory order like `parked`, and for the same reason — the canvas reads
   * left to right — but keyed by request, so the entry carries its own id back
   * to the server when it is answered. */
  const asking = useMemo(
    () =>
      (state.run?.node_runs ?? []).flatMap((node_run) =>
        Object.entries(state.questions)
          .filter(([, open]) => open.nodeId === node_run.node_id)
          .map(([requestId, open]) => ({
            nodeRun: node_run,
            requestId,
            questions: open.questions,
          })),
      ),
    [state.run, state.questions],
  );

  /** The human nodes the walk is parked on, in factory order. */
  const parked = useMemo(
    () =>
      (state.run?.node_runs ?? [])
        .filter((node_run) => state.decisions[node_run.node_id])
        .map((node_run) => ({ nodeRun: node_run, ...state.decisions[node_run.node_id] })),
    [state.run, state.decisions],
  );

  /** The input nodes that still need a human, in factory order. */
  const awaiting = useMemo(
    () =>
      (state.run?.node_runs ?? []).filter(
        (node_run) => node_run.type === "input" && node_run.status === "waiting_input",
      ),
    [state.run],
  );

  const cancel = useCallback(async () => {
    const runId = state.run?.id;
    if (!runId) return;
    setState((previous) => ({ ...previous, busy: true, error: null }));
    try {
      const run = await api.cancelRun(runId);
      setState((previous) => ({ ...previous, run, status: run.status, busy: false }));
    } catch (cause) {
      setState((previous) => ({ ...previous, busy: false, error: message(cause) }));
    }
  }, [state.run?.id]);

  /** Pick this run back up where the provider's allowance ran out.
   *
   * Deliberately **not** folded into `start`, though the two do almost the same
   * thing afterwards: `start` creates a run and clears the screen for it, and
   * this one reopens the run already on screen. Clearing state here would throw
   * away the transcript of the first walk, which is the half of the story that
   * explains why there is a second one.
   */
  const resume = useCallback(async () => {
    const runId = state.run?.id;
    if (!runId) return;
    setState((previous) => ({ ...previous, busy: true, error: null, problems: [] }));
    try {
      const run = await api.resumeRun(runId);
      setState((previous) => ({ ...previous, run, status: run.status, busy: false }));
      // The same run id, so the same log: the stream reconnects and carries on
      // appending rather than starting a second account of one run.
      watch(run.id);
    } catch (cause) {
      // 409 is the ordinary answer here, and it is a sentence: the run was not
      // blocked, or the factory has changed under it, or something else is in
      // flight. Shown as-is rather than translated into "could not resume".
      setState((previous) => ({ ...previous, busy: false, error: message(cause) }));
    }
  }, [state.run?.id, watch]);

  const clear = useCallback(() => {
    disconnect();
    setState(EMPTY);
  }, [disconnect]);

  const live = useMemo(
    () => state.status !== null && !isTerminal(state.status),
    [state.status],
  );

  /** Whether this run is one the operator may pick back up.
   *
   * Read off the record rather than inferred from the status, because "failed"
   * covers both the run that broke and the run that merely ran out of
   * allowance, and only the second is resumable. The server decides the same
   * question the same way and has the final say — this is what draws the
   * button, not what authorises the act. */
  const resumable = useMemo(
    () => state.status === "failed" && Boolean(state.run?.blocked_node_id),
    [state.status, state.run?.blocked_node_id],
  );

  return {
    ...state,
    live,
    awaiting,
    parked,
    asking,
    start,
    submit,
    decide,
    answer,
    cancel,
    resume,
    resumable,
    watch,
    subscribe,
    clear,
  };
}
