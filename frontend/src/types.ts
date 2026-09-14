/**
 * The wire shapes, mirroring `backend/factoria/models.py` field for field.
 *
 * The one that matters is the node: every node carries `definition` (what you
 * drew, the only half that is persisted) and `runtime` (what is happening now,
 * `null` until stage 5 fills it from the event stream). Both halves live on the
 * same object, which is what makes "building and operating are the same
 * surface" mechanical rather than a matter of discipline (DESIGN.md).
 */

export type NodeType = "input" | "agent" | "output" | "human" | "factory-ref";

/**
 * What an input node asks for, and what an output node does with the branch.
 *
 * **Kinds, not node types.** Both live in `definition.config.kind`, which is
 * what keeps "a factory starts with an input and ends with an output" a rule
 * about `NodeType` — one check, rather than a list of type names that grows
 * every time there is a new way to ask for work or to ship it. Mirrors
 * `models.InputKind` / `models.OutputKind`.
 */
export type InputKind = "request" | "prd";
export type OutputKind = "commit" | "commit-push" | "commit-merge";
export type NodeKind = InputKind | OutputKind;

export const DEFAULT_INPUT_KIND: InputKind = "request";
export const DEFAULT_OUTPUT_KIND: OutputKind = "commit";

/** `commit-push` and `commit-merge` put a ref on a remote; `commit` does not. */
export const PUSHING_OUTPUT_KINDS: OutputKind[] = ["commit-push", "commit-merge"];

/** A node's state machine. No `cancelled`: a cancelled node is `failed` with a
 * detail saying who stopped it (backend `models.NodeStatus`). */
export type RunStatus =
  | "pending"
  | "waiting_input"
  | "running"
  | "succeeded"
  | "failed";

/** A whole run's state — one member more than a node's, where cancelling is
 * genuinely a different outcome rather than a different reason for failing. */
export type OverallRunStatus = RunStatus | "cancelled";

export const TERMINAL_RUN_STATUSES: OverallRunStatus[] = [
  "succeeded",
  "failed",
  "cancelled",
];

export type Position = { x: number; y: number };

export type Codebase = {
  id: string;
  name: string;
  path: string;
  branch: string | null;
};

/**
 * A codebase's live git state — the clean-repo precondition, as data.
 *
 * Fetched separately from the codebase list because it costs a subprocess per
 * repo: discovery stays filesystem-only so a picker can never fail, and this is
 * asked for only where it matters — on an agent node, next to the Run button.
 */
export type CodebaseGit = {
  codebase_id: string;
  is_repo: boolean;
  branch: string | null;
  detached: boolean;
  head: string | null;
  dirty: boolean;
  change_count: number;
  changes: string[];
  detail: string;
};

export type NodeDefinition = {
  type: NodeType;
  label: string;
  position: Position;
  /** The agent's workspace requirement, by discovered codebase id. */
  codebase_id: string | null;
  config: Record<string, unknown>;
  factory_ref: string | null;
};

/**
 * What is happening on this node right now.
 *
 * The first five fields mirror the server's `NodeRuntime`; the rest are
 * assembled **in the browser** from the event stream — the transcript already
 * lives in `events.jsonl`, and a second server-side copy would be a cache with
 * no reader. Both halves land on the same object because the canvas renders one
 * thing, and `fromFlowNode` drops all of it before a save (graph.ts).
 */
export type NodeRuntime = {
  status: RunStatus;
  detail?: string | null;
  startedAt?: string | null;
  endedAt?: string | null;
  costUsd?: number;
  /** The agent's own words, as they stream in. */
  text?: string;
  /** The tool in flight, or the last one to finish. */
  tool?: ToolActivity | null;
  /** How many tools this node has run — the number on the node's chip. */
  toolCount?: number;
  todos?: TodoItem[];
  /** The worktree this node is writing in, and the commit it produced. */
  artifact?: RunArtifact | null;
};

/** Something durable that now exists outside `/data` — a branch in the
 * operator's own repository. Still true after the run ends, which is why it is
 * its own event and not a line of streamed text. */
export type RunArtifact = {
  kind: "worktree" | "commit" | "push" | "merge";
  codebaseId: string;
  branch: string;
  path: string;
  baseBranch: string | null;
  baseCommit: string;
  commitSha: string | null;
  fileCount: number;
  detail: string;
  /** Set on `push` / `merge`: the remote, and the branch it fast-forwarded. */
  remote?: string;
  mergedInto?: string | null;
};

export type ToolActivity = {
  id: string;
  name: string;
  /** `Edit src/main.py` — the argument that says what it is acting *on*. */
  target: string;
  state: "running" | "done" | "error";
};

export type TodoItem = { content: string; status: string };

export type GraphNode = {
  id: string;
  definition: NodeDefinition;
  runtime: NodeRuntime | null;
};

export type EdgePayload = { kind: "text" };

export type GraphEdge = {
  id: string;
  source: string;
  target: string;
  source_handle: string | null;
  target_handle: string | null;
  payload: EdgePayload;
};

export type Port = { name: string; kind: "text" };

export type Factory = {
  schema_version: number;
  id: string;
  project_id: string;
  name: string;
  inputs: Port[];
  outputs: Port[];
  nodes: GraphNode[];
  edges: GraphEdge[];
  created_at: string;
  updated_at: string;
};

export type Project = {
  id: string;
  name: string;
  codebase_ids: string[];
  created_at: string;
  updated_at: string;
};

export type Health = {
  status: string;
  version: string;
  workspace: string;
  workspace_present: boolean;
  workspace_writable: boolean;
};

/** The whole-graph save body — identity and timestamps are the server's. */
export type GraphWrite = {
  nodes: GraphNode[];
  edges: GraphEdge[];
};

/* ---- settings: the agent CLI's two faces --------------------------------- */

/** `claude auth status --json`, as the backend reports it. */
export type AccountStatus = {
  authenticated: boolean;
  /** False when the CLI itself could not be run — a different fix entirely. */
  reachable: boolean;
  version: string | null;
  method: string | null;
  detail: string;
};

export type AgentModel = {
  value: string;
  label: string;
  detail: string;
  resolved: string;
};

/** The CLI's own `initialize` answer. `default_model` is null when it could not
 * be asked — never a model id this project chose (DESIGN.md, Assumptions). */
export type Handshake = {
  default_model: string | null;
  default_model_label: string | null;
  models: AgentModel[];
  permission_mode: string | null;
  detail: string;
};

export type AgentStatus = { account: AccountStatus; handshake: Handshake };

export type LoginPhase =
  | "starting"
  | "awaiting_code"
  | "verifying"
  | "success"
  | "failed";

export type LoginState = {
  active: boolean;
  phase: LoginPhase;
  /** The operator opens this in their *own* browser; the container is headless. */
  verification_url: string | null;
  detail: string | null;
  /** The CLI is still at the prompt — another paste will be read. */
  retryable: boolean;
  status: AccountStatus | null;
};

/** Note what is absent: there is no private-key field, here or on the wire. */
export type SshKeyStatus = {
  exists: boolean;
  public_key: string | null;
  fingerprint: string | null;
  detail: string;
};

/* ---- runs ---------------------------------------------------------------- */

export type NodeRun = {
  node_id: string;
  type: NodeType;
  /** The node's kind, frozen when the run was created — the factory file is
   * editable while a run is in flight, so a run record describes the run that
   * happened rather than the graph as it is now. */
  kind: string;
  /** Likewise frozen. It is what the run surface calls this node when it asks
   * a human for something, and a second tab has no canvas to read it off. */
  label: string;
  status: RunStatus;
  detail: string | null;
  started_at: string | null;
  ended_at: string | null;
  cost_usd: number;
  session_id: string | null;
  output_text: string;
  /** The document a `prd` input node was given. The contents are `output_text`;
   * this is only ever the label. */
  filename: string | null;
};

/** One codebase's isolated checkout for one run. `kept` is the cleanup policy:
 * a failed run leaves its worktree behind to be inspected. */
export type RunWorktree = {
  codebase_id: string;
  codebase_name: string;
  repo_path: string;
  path: string;
  branch: string;
  base_commit: string;
  base_branch: string | null;
  commit_sha: string | null;
  subject: string;
  files: string[];
  stat: string;
  kept: boolean;
  /** What left the machine. `merged_into` is a branch on the *remote*. */
  remote: string;
  pushed_branch: string | null;
  merged_into: string | null;
  push_detail: string;
};

export type Run = {
  schema_version: number;
  id: string;
  project_id: string;
  factory_id: string;
  factory_name: string;
  status: OverallRunStatus;
  detail: string | null;
  /** The one-shot request (open question 2). Null until it is submitted. */
  input_text: string | null;
  adapter: string;
  /** `factor-ia/<factory>/<run-id>`, known before any work starts. */
  branch: string | null;
  /** The run was allowed to bypass the agent's permission checks. Rendered as
   * dangerous wherever it appears (DESIGN.md §6). */
  dangerous: boolean;
  worktrees: RunWorktree[];
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
  total_cost_usd: number;
  node_runs: NodeRun[];
};

/** The adapter's normalized union, as it arrives inside an `agent` envelope.
 * Overseer's vocabulary, adopted verbatim (backend `agent/events.py`). */
export type AgentEvent = { session_id: string; timestamp: string } & (
  | { type: "session.init"; model: string; cwd: string; tools: string[] }
  | { type: "text.delta"; text: string }
  | { type: "thinking.delta"; text: string }
  | { type: "tool.start"; tool_use_id: string; name: string; input: unknown }
  | { type: "tool.delta"; tool_use_id: string; chunk: string }
  | { type: "tool.end"; tool_use_id: string; output: unknown; is_error: boolean }
  | { type: "permission.request"; request_id: string; tool_name: string }
  | { type: "todo.update"; items: TodoItem[] }
  | { type: "turn.end"; total_cost_usd: number; duration_ms: number }
  | { type: "error"; message: string; recoverable: boolean }
  | { type: "exit"; cause: string; code: number | null; detail: string | null }
);

/** The envelope: an adapter event addressed to a graph node, plus the two
 * things only the orchestrator knows — the node's state machine and the run's.
 * `seq` is the SSE `id:`, and the whole reconnection contract. */
export type RunEvent = { seq: number; run_id: string; timestamp: string } & (
  | { type: "run.status"; status: OverallRunStatus; detail: string | null }
  | { type: "node.status"; node_id: string; status: RunStatus; detail: string | null }
  | {
      type: "run.input";
      node_id: string;
      text: string;
      kind: string;
      filename: string | null;
    }
  | { type: "agent"; node_id: string; event: AgentEvent }
  | {
      type: "run.artifact";
      node_id: string;
      kind: "worktree" | "commit" | "push" | "merge";
      codebase_id: string;
      branch: string;
      path: string;
      base_branch: string | null;
      base_commit: string;
      commit_sha: string | null;
      file_count: number;
      detail: string;
      remote: string;
      merged_into: string | null;
    }
  | { type: "run.cost"; node_id: string; delta_usd: number; total_usd: number }
);

export const RUN_EVENT_TYPES: RunEvent["type"][] = [
  "run.status",
  "node.status",
  "run.input",
  "agent",
  "run.artifact",
  "run.cost",
];
