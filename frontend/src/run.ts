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
  type RunEvent,
  type ToolActivity,
} from "./types";

/** What one node looks like right now, by node id. */
export type Runtimes = Record<string, NodeRuntime>;

export type RunState = {
  run: Run | null;
  status: OverallRunStatus | null;
  detail: string | null;
  runtimes: Runtimes;
  costUsd: number;
  /** The branch this run owns, and the commit it produced. Survives the run —
   * it names a ref in the operator's own repository. */
  artifacts: RunArtifact[];
  /** The human nodes the walk is parked on, by node id.
   *
   * Driven by `run.decision.request` rather than by the run record, which is a
   * snapshot from before the walk started and never sees a node that parks
   * mid-graph. The event carries what the belts delivered, so a tab that joined
   * late renders the same form from the log alone. */
  decisions: Record<string, { prompt: string; incoming: string }>;
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
  decisions: {},
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
        decisions: {
          ...state.decisions,
          [event.node_id]: { prompt: event.prompt, incoming: event.incoming },
        },
      };
    case "run.decision":
      return {
        ...state,
        decisions: withoutNode(state.decisions, event.node_id),
        runtimes: withNode(state.runtimes, event.node_id, {
          text: event.note || undefined,
        }),
      };
    case "run.input":
      return {
        ...state,
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
      };
      return {
        ...state,
        // Kept in arrival order, worktree before commit — a replay of the log
        // and a live run therefore produce the same list.
        artifacts: [...state.artifacts, artifact],
        runtimes: withNode(state.runtimes, event.node_id, { artifact }),
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

  const clear = useCallback(() => {
    disconnect();
    setState(EMPTY);
  }, [disconnect]);

  const live = useMemo(
    () => state.status !== null && !isTerminal(state.status),
    [state.status],
  );

  return { ...state, live, awaiting, parked, start, submit, decide, cancel, watch, clear };
}
