/**
 * The REST client. One factory is loaded whole and saved whole: the server's
 * primary write is `PUT .../factories/{id}` and `main.json` is written whole or
 * not at all, so the client has no reason to invent a per-node change journal.
 */

import type {
  AgentStatus,
  Codebase,
  CodebaseGit,
  Factory,
  GraphWrite,
  Health,
  KnowledgeBase,
  KnowledgeBaseSummary,
  LoginState,
  Project,
  Run,
  Skill,
  SkillSummary,
  SshKeyStatus,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: init?.body ? { "content-type": "application/json" } : undefined,
  });
  if (!response.ok) {
    throw new ApiError(response.status, await detail(response));
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** FastAPI puts the useful part in `detail`, which is sometimes a list. */
async function detail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    const value = body.detail;
    if (Array.isArray(value)) return value.map(String).join("; ");
    if (typeof value === "string") return value;
  } catch {
    /* not JSON */
  }
  return `${response.status} ${response.statusText}`;
}

export const api = {
  health: () => request<Health>("/api/health"),
  codebases: () => request<Codebase[]>("/api/codebases"),
  /** Live git state for one codebase: the clean-repo precondition, as the
      canvas sees it before it offers a Run. */
  codebaseGit: (id: string) => request<CodebaseGit>(`/api/codebases/${id}/git`),

  /** The workspace's standing instructions. A directory read, not a
      subprocess — cheap enough to sit in the canvas's own load. */
  skills: () => request<SkillSummary[]>("/api/skills"),
  skill: (name: string) => request<Skill>(`/api/skills/${encodeURIComponent(name)}`),
  /** The file was read in the browser and is posted as text — the bargain the
      PRD drop zone already struck: no upload endpoint, and no second path into
      the container that takes a filename. `overwrite` is reachable only from
      the conflict the first attempt reported. */
  importSkill: (text: string, filename?: string, overwrite = false) =>
    request<Skill>("/api/skills", {
      method: "POST",
      body: JSON.stringify({ text, filename, overwrite }),
    }),
  deleteSkill: (name: string) =>
    request<void>(`/api/skills/${encodeURIComponent(name)}`, { method: "DELETE" }),

  /** The workspace's knowledge bases. A directory walk, like `skills()`, and
      cheap enough to sit in the canvas's own load. */
  knowledge: () => request<KnowledgeBaseSummary[]>("/api/knowledge"),
  knowledgeBase: (id: string) =>
    request<KnowledgeBase>(`/api/knowledge/${encodeURIComponent(id)}`),
  /** Create a base, or import a `manifest.md` the browser already read.
      **No `overwrite`**, unlike a skill import: replacing a base would silently
      re-scope a store other projects are reading. */
  createKnowledgeBase: (body: {
    id: string;
    title?: string;
    scope?: string;
    about?: string;
    text?: string;
  }) =>
    request<KnowledgeBase>("/api/knowledge", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /** Deletes the directory and everything in it. The one recursive delete in
      the library, and it is a button only because git is the undo. */
  deleteKnowledgeBase: (id: string) =>
    request<void>(`/api/knowledge/${encodeURIComponent(id)}`, { method: "DELETE" }),

  projects: () => request<Project[]>("/api/projects"),
  createProject: (name: string, codebaseIds: string[] = []) =>
    request<Project>("/api/projects", {
      method: "POST",
      body: JSON.stringify({ name, codebase_ids: codebaseIds }),
    }),
  project: (id: string) => request<Project>(`/api/projects/${id}`),
  /** Unregisters the project and deletes its `_factories/<project>/` directory,
      sparing any nested factory shared with another project — that is the
      server's call, and the confirmation says so. */
  deleteProject: (id: string) =>
    request<void>(`/api/projects/${id}`, { method: "DELETE" }),
  /** Sparse: an omitted key means *unchanged*. `settings` is the exception and
      is replaced whole, because the panel that sends it always knows both
      halves. Writes to `/data/projects.json`, which is machine-local and not
      the git-tracked factory file — so this saves immediately rather than
      joining the canvas's explicit-save policy. */
  updateProject: (
    id: string,
    body: Partial<Pick<Project, "name" | "codebase_ids" | "settings">>,
  ) =>
    request<Project>(`/api/projects/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  factory: (projectId: string, factoryId = "main") =>
    request<Factory>(`/api/projects/${projectId}/factories/${factoryId}`),
  saveFactory: (projectId: string, factoryId: string, graph: GraphWrite) =>
    request<Factory>(`/api/projects/${projectId}/factories/${factoryId}`, {
      method: "PUT",
      body: JSON.stringify(graph),
    }),

  /* The settings surface. Sign-in is polled rather than streamed: SSE is
     stage 5's, and a login has a handful of state changes over a minute of
     human time. */
  agentStatus: () => request<AgentStatus>("/api/agent/status"),
  loginState: () => request<LoginState>("/api/agent/login"),
  /** Starts a sign-in, or joins the one already running (single-flight). */
  beginLogin: () => request<LoginState>("/api/agent/login", { method: "POST" }),
  /** `<code>#<state>`, sent whole — the backend writes it to stdin verbatim. */
  submitLoginCode: (code: string) =>
    request<LoginState>("/api/agent/login/code", {
      method: "POST",
      body: JSON.stringify({ code }),
    }),
  cancelLogin: () =>
    request<LoginState>("/api/agent/login/cancel", { method: "POST" }),

  /* Run control. Every one of these is a POST, so the Origin check covers it:
     starting a run is the most dangerous thing this server does, and no other
     page in the browser gets to do it. Watching a run is a GET — the SSE
     stream in `run.ts`. */
  startRun: (projectId: string, factoryId = "main") =>
    request<Run>(`/api/projects/${projectId}/factories/${factoryId}/runs`, {
      method: "POST",
    }),
  /**
   * One input node's submission. The last one starts the walk.
   *
   * `nodeId` addresses a single node, which is what a factory with a request
   * *and* a PRD needs. Omitting it keeps the original meaning — every input
   * node still waiting takes this text — so the single-request factory posts
   * exactly what it always did.
   */
  submitRunInput: (
    runId: string,
    text: string,
    options: { nodeId?: string; filename?: string } = {},
  ) =>
    request<Run>(`/api/runs/${runId}/input`, {
      method: "POST",
      body: JSON.stringify({
        text,
        node_id: options.nodeId ?? null,
        filename: options.filename ?? null,
      }),
    }),
  /** One human node's verdict, which resumes the walk down the matching belt.
   * `nodeId` is required, unlike on a submission: a verdict is not re-postable,
   * so a stale form has to fail loudly rather than answer the wrong question. */
  submitRunDecision: (
    runId: string,
    nodeId: string,
    approved: boolean,
    note = "",
  ) =>
    request<Run>(`/api/runs/${runId}/decision`, {
      method: "POST",
      body: JSON.stringify({ node_id: nodeId, approved, note }),
    }),
  /** One questionnaire's answers, from a `scope` node's form.
   *
   * Addressed one level finer than a verdict: a scope node asks many times, so
   * `requestId` is what names the question being answered. Answers are keyed by
   * question text exactly as asked — the provider matches on it, and the server
   * drops anything keyed to a question it did not pose. */
  submitRunAnswer: (
    runId: string,
    nodeId: string,
    requestId: string,
    answers: Record<string, string>,
  ) =>
    request<Run>(`/api/runs/${runId}/answer`, {
      method: "POST",
      body: JSON.stringify({ node_id: nodeId, request_id: requestId, answers }),
    }),
  /** Explicit cancel — closing the tab is not a decision to stop work. */
  cancelRun: (runId: string) =>
    request<Run>(`/api/runs/${runId}/cancel`, { method: "POST" }),
  /** Pick a run back up after the provider's allowance cut it short.
   *
   * The only call that reopens a finished run, and the server decides whether
   * a given one may be — a crash and a cancel are both refused with a 409. */
  resumeRun: (runId: string) =>
    request<Run>(`/api/runs/${runId}/resume`, { method: "POST" }),
  run: (runId: string) => request<Run>(`/api/runs/${runId}`),
  runs: () => request<Run[]>("/api/runs"),

  sshKey: () => request<SshKeyStatus>("/api/ssh/key"),
  generateSshKey: () => request<SshKeyStatus>("/api/ssh/key", { method: "POST" }),
};
