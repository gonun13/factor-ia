/**
 * The wire shapes, mirroring `backend/factoria/models.py` field for field.
 *
 * The one that matters is the node: every node carries `definition` (what you
 * drew, the only half that is persisted) and `runtime` (what is happening now,
 * `null` until stage 5 fills it from the event stream). Both halves live on the
 * same object, which is what makes "building and operating are the same
 * surface" mechanical rather than a matter of discipline (DESIGN.md).
 */

export type NodeType =
  | "input"
  | "agent"
  | "output"
  | "human"
  | "memory"
  | "knowledge"
  | "factory-ref";

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
/**
 * What a `human` node asks a person for — `models.HumanKind`.
 *
 * `approval` is a **gate**: one question written into the graph when it was
 * drawn, answered once. `scope` is a **negotiation**: an agent reads the
 * incoming belt, decides what it still does not know, and interviews the
 * operator in sequence; the answers become the markdown document that leaves by
 * the success port. Both park the run and both have two ports — a scope node
 * simply asks more than once, which is why the transport never had to change.
 */
export type HumanKind = "approval" | "scope";
/**
 * What an agent node is *for* — `models.AgentKind`.
 *
 * A `worker` does the job and has one way out. A `reviewer` judges what it is
 * given, must end its reply with a verdict, and its `fail` belt is the one belt
 * allowed to close a loop. `worker` is the default, which is a statement about
 * files already on disk: an agent drawn before this existed has no kind, and
 * what an agent has always done is work.
 */
export type AgentKind = "worker" | "reviewer";
export type NodeKind = InputKind | OutputKind | HumanKind | AgentKind;

export const DEFAULT_INPUT_KIND: InputKind = "request";
export const DEFAULT_OUTPUT_KIND: OutputKind = "commit";
export const DEFAULT_HUMAN_KIND: HumanKind = "approval";
export const DEFAULT_AGENT_KIND: AgentKind = "worker";

/** A reviewer's lap cap, and the bounds the server enforces — `verdict.py`. */
export const DEFAULT_MAX_LAPS = 3;
export const MAX_LAPS_LIMIT = 10;

/**
 * Which of a node's two outcomes a belt carries away — `models.EdgeOutcome`.
 *
 * It rides on the belt's `source_handle`, which React Flow has always set and
 * which has always round-tripped through the factory file, so a graph that says
 * what happens when a machine fails is not a file format change. `null` means
 * `success`, which is what makes every belt drawn before ports existed — and
 * every belt out of an input node, which has one way out — mean what it always
 * meant.
 */
export type EdgeOutcome = "success" | "fail";
export const DEFAULT_EDGE_OUTCOME: EdgeOutcome = "success";
export const EDGE_OUTCOMES: EdgeOutcome[] = ["success", "fail"];

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
 * What one knowledge base holds — `models.KnowledgeScope`.
 *
 * **Not a kind.** A kind is how one node type varies and lives in the node's
 * config; a scope is a property of the *base*, declared in its manifest and true
 * of it in every factory that selects it. A scope on the node would be a second
 * place the answer lives, and the first time the two disagreed the canvas would
 * be showing a permission the server does not grant.
 *
 * It controls exactly two things: whether an agent may write
 * (`KNOWLEDGE_READ_ONLY_SCOPES`), and precedence when sources disagree.
 */
export type KnowledgeScope = "principles" | "project" | "codebase" | "general";

export const KNOWLEDGE_SCOPES: KnowledgeScope[] = [
  "principles",
  "project",
  "codebase",
  "general",
];

/** One line each, for the library and the node's picker. */
export const KNOWLEDGE_SCOPE_NOTE: Record<KnowledgeScope, string> = {
  principles: "how work should be done, everywhere, always",
  project: "what is true of one project",
  codebase: "what is true of one repository, across the projects that touch it",
  general: "durable reference belonging to no project and no repo",
};

/** Scopes no agent may ever write to, with no per-base override — `models.READ_ONLY_SCOPES`. */
export const KNOWLEDGE_READ_ONLY_SCOPES = new Set<string>(["principles"]);

/**
 * A knowledge node's two handles, and the direction each one implies —
 * `models.KnowledgeHandle`.
 *
 * `write` is the node's **target** handle: a wire arrives there from the machine
 * that may contribute. `read` is its **source** handle: a wire leaves there for
 * each machine that may consult it.
 *
 * **Direction is the permission**, which is the memory node's rule unchanged —
 * an arrow into storage is a write and an arrow out of it is a read, and that
 * is readable off the picture without learning what a handle colour means.
 *
 * Knowledge needs the handles *named* where a memory does not, but only so a
 * base can take one away: a `principles` base renders no `write` handle, so a
 * contributing wire has nowhere to land. Nothing reads the name to decide a
 * permission — the ends of a wire are what decide, and they cannot disagree
 * with the drawing.
 */
export type KnowledgeHandle = "read" | "write";

/** One file in a base, as the library and the node list it. */
export type KnowledgeEntry = {
  path: string;
  bytes: number;
  /** The file's opening markdown heading, read off disk rather than written. */
  summary: string;
};

/**
 * A knowledge base's manifest, as metadata — `models.KnowledgeBaseSummary`.
 *
 * Discovered rather than owned, like a codebase and a skill: the directory is
 * the record and git is the history. The directory name is the id, and the id
 * is what a knowledge node selects it by.
 */
export type KnowledgeBaseSummary = {
  id: string;
  title: string;
  scope: string;
  format: string;
  /** The manifest's prose: shown here, and given to the agent in its index. */
  about: string;
  files: number;
  bytes: number;
  /** Derived from `scope` server-side, never stored and never trusted from here. */
  writable: boolean;
  updated_at: string;
};

/** A base with its contents listed — what a picker opens. */
export type KnowledgeBase = KnowledgeBaseSummary & { entries: KnowledgeEntry[] };

/**
 * A standing-instructions document in the workspace's `_skills/`.
 *
 * Owned by nothing — not a project, not a factory — so it is fetched from a
 * workspace-scoped route and offered to every agent node everywhere.
 */
export type SkillSummary = {
  name: string;
  /** The one line an agent node's picker can show about a skill nobody opened. */
  description: string;
  bytes: number;
  updated_at: string;
};

/** A skill and its instructions. Only the import round-trip needs the body. */
export type Skill = SkillSummary & { body: string };

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
  /** The model the provider says it chose, off `session.init`. Not what we
   * asked for — the two differ whenever we asked for nothing. */
  model?: string;
  /** Which lap of a loop this node is on. 1 for everything that runs once, so
   * the chip only shows above that. Comes down on `node.status` rather than
   * being counted here — the counter lives on the server's `NodeRun`, and a
   * second implementation of it that only a reconnecting tab exercises is the
   * kind that drifts. */
  lap?: number;
};

/** Something durable that now exists outside `/data` — a branch in the
 * operator's own repository, or a memory document on disk. Still true after
 * the run ends, which is why it is its own event and not a line of streamed
 * text. */
export type RunArtifact = {
  kind: "worktree" | "commit" | "push" | "merge" | "memory";
  codebaseId: string;
  branch: string;
  path: string;
  baseBranch: string | null;
  baseCommit: string;
  commitSha: string | null;
  /** Files in the commit — or, on a `memory`, sections in the document. */
  fileCount: number;
  detail: string;
  /** Set on `push` / `merge`: the remote, and the branch it fast-forwarded. */
  remote?: string;
  mergedInto?: string | null;
  /** Set on `memory`: the document's body, and whether the budget cut it.
   * `truncated` travels with the size for the reason the frontmatter carries
   * it — a size that might be a ceiling and might be the whole thing is a
   * number a reader would trust wrongly. */
  bytes?: number;
  truncated?: boolean;
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

/** Project-wide execution defaults an agent node inherits unless it overrides
 * them. `null` means *ask nobody*: the provider falls through to the
 * container's, and the model to the CLI's own — which is the only place a model
 * id ever comes from. */
export type ProjectSettings = {
  default_provider: "claude" | "fake" | null;
  default_model: string | null;
};

export type Project = {
  id: string;
  name: string;
  codebase_ids: string[];
  settings: ProjectSettings;
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

/** What one provider can be asked to run on. Not the same question as whether
 * the CLI is healthy — see `handshake` for that. `fixed` means the provider
 * reports its own model id whatever it is handed (the fake does), so a picker
 * must offer no choice rather than one that will be ignored. */
export type ModelOffer = {
  models: AgentModel[];
  default_model: string | null;
  default_model_label: string | null;
  fixed: boolean;
};

export type AgentStatus = {
  account: AccountStatus;
  handshake: Handshake;
  /** Every provider that exists, keyed by the name a node's `adapter` and a
   * project's `default_provider` are spelled with. */
  providers: Record<string, ModelOffer>;
  /** What a run uses when neither the node nor the project names a provider.
   * An environment fact on the container (`FACTOR_IA_ADAPTER`), which the
   * browser has no other way to learn. */
  default_provider: "claude" | "fake";
};

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
  /** A `human` node's question, frozen the same way and for the same reason. */
  prompt: string;
  status: RunStatus;
  detail: string | null;
  started_at: string | null;
  ended_at: string | null;
  cost_usd: number;
  session_id: string | null;
  /** What this node actually ran on, reported by the provider itself. `""`
   * until the first `session.init`, and on every node that is not an agent. */
  model: string;
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

/**
 * One memory node's document — where it is, what went into it, and whether it
 * survived the run.
 *
 * `kept` is the cleanup policy's answer written down rather than re-derived: a
 * memory is scratch for one run and goes with its worktrees, except on a failed
 * run, where what the agents believed they knew is the post-mortem.
 */
export type RunMemory = {
  node_id: string;
  slug: string;
  label: string;
  path: string;
  /** One per source node that wrote into it, not per write — a second lap of
   * the same writer replaces its section rather than adding one. */
  sections: number;
  bytes: number;
  truncated: boolean;
  kept: boolean;
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
  /** One per memory node that ran. Empty on every factory that has none, which
   * is what makes it additive — `models.RunMemory`. */
  memory: RunMemory[];
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
  total_cost_usd: number;
  node_runs: NodeRun[];
  /** The node a spent provider allowance stopped this run at, and the
   * provider's own sentence about it — reset time included, which is the part
   * that tells an operator whether resuming now is worth trying.
   *
   * **Set only for a usage limit**, never for a crash or a cancel. Together
   * they are the whole of what makes the Resume button appear: a failed run
   * with a `blocked_node_id` is one the provider cut short, and nothing about
   * the work it had done is in doubt. `models.Run` has the rest. */
  blocked_node_id: string | null;
  blocked_reason: string;
  /** How many times this run was picked back up. Shown so the gap in the
   * timings has an explanation on the record. */
  resumed_count: number;
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

/**
 * One question a `scope` node's agent is putting to the operator — the server's
 * `questions.Question`, already parsed and clamped on the way in.
 *
 * `question` is also the **key** an answer is returned under, verbatim: the
 * provider matches answers to questions by their text, so a form that tidied
 * the wording would send back an answer the agent reads as no answer at all.
 */
export type ScopeQuestion = {
  question: string;
  header: string;
  multi_select: boolean;
  options: { label: string; description: string }[];
};

/** The envelope: an adapter event addressed to a graph node, plus the two
 * things only the orchestrator knows — the node's state machine and the run's.
 * `seq` is the SSE `id:`, and the whole reconnection contract. */
export type RunEvent = { seq: number; run_id: string; timestamp: string } & (
  | { type: "run.status"; status: OverallRunStatus; detail: string | null }
  | {
      type: "node.status";
      node_id: string;
      status: RunStatus;
      detail: string | null;
      lap?: number;
    }
  | {
      type: "run.input";
      node_id: string;
      text: string;
      kind: string;
      filename: string | null;
    }
  | {
      type: "run.decision.request";
      node_id: string;
      prompt: string;
      /** What the belts brought in — the thing being judged. It travels with
       * the question so a canvas that joined late can render the form from the
       * log alone, without refetching the run. */
      incoming: string;
      kind: string;
    }
  | { type: "run.decision"; node_id: string; approved: boolean; note: string }
  | {
      type: "run.question.request";
      node_id: string;
      /** What an answer names. A scope node asks many times, so the node id
       * stopped being enough the moment it did — and a form left open in a
       * second tab fails against the question it was showing rather than
       * answering the one on screen now. */
      request_id: string;
      questions: ScopeQuestion[];
    }
  | {
      type: "run.question";
      node_id: string;
      request_id: string;
      answers: Record<string, string>;
    }
  | { type: "agent"; node_id: string; event: AgentEvent }
  | {
      type: "run.artifact";
      node_id: string;
      kind: "worktree" | "commit" | "push" | "merge" | "memory";
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
      bytes: number;
      truncated: boolean;
    }
  | { type: "run.cost"; node_id: string; delta_usd: number; total_usd: number }
);

export const RUN_EVENT_TYPES: RunEvent["type"][] = [
  "run.status",
  "node.status",
  "run.input",
  "run.decision.request",
  "run.decision",
  "run.question.request",
  "run.question",
  "agent",
  "run.artifact",
  "run.cost",
];
