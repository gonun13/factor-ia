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
  HousekeepingReport,
  KnowledgeBase,
  KnowledgeEntryText,
  MemoryDocument,
  KnowledgeBaseSummary,
  LibraryFactorySummary,
  LoginState,
  Project,
  ProviderStatus,
  NodeEventPage,
  Run,
  Skill,
  SkillSummary,
  SshKeyStatus,
  TidyOutcome,
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
  /** What every worn skill is told, in the words the agent actually gets.
      Fetched rather than bundled: a second copy of the text in this app would
      be a claim about the spawn that nothing keeps true. */
  machineContract: () => request<{ contract: string }>("/api/machine/contract"),

  /** The workspace's knowledge bases. A directory walk, like `skills()`, and
      cheap enough to sit in the canvas's own load. */
  knowledge: () => request<KnowledgeBaseSummary[]>("/api/knowledge"),
  knowledgeBase: (id: string) =>
    request<KnowledgeBase>(`/api/knowledge/${encodeURIComponent(id)}`),
  /** One file out of a base, as text — `manifest.md` included, which the
      contents listing leaves out because it is not content. `limit` is the
      editor's: it must see the whole file or it must not offer to save over
      it, and the server clamps it to the per-file budget. */
  knowledgeEntry: (id: string, path: string, limit?: number) =>
    request<KnowledgeEntryText>(
      `/api/knowledge/${encodeURIComponent(id)}/entry?path=${encodeURIComponent(path)}` +
        (limit ? `&limit=${limit}` : ""),
    ),
  /** Save one **existing content file** back. `baseText` is what the editor
      opened: the server compares it under the base's lock and refuses with a
      sentence if a machine filed into the file meanwhile, so nothing of
      anyone's is overwritten. No create, no rename, no delete, and
      `manifest.md` is read-only — a base's shape and its identity stay git's. */
  saveKnowledgeEntry: (id: string, path: string, text: string, baseText: string) =>
    request<KnowledgeEntryText>(
      `/api/knowledge/${encodeURIComponent(id)}/entry?path=${encodeURIComponent(path)}`,
      { method: "PUT", body: JSON.stringify({ text, base_text: baseText }) },
    ),
  /** Remove one content file from a base. Refused for `manifest.md`, and for
      anything the base does not already list. No agent can do this — it is not
      an MCP tool — and the undo is git, the same bargain deleting a whole base
      makes. */
  deleteKnowledgeEntry: (id: string, path: string) =>
    request<void>(
      `/api/knowledge/${encodeURIComponent(id)}/entry?path=${encodeURIComponent(path)}`,
      { method: "DELETE" },
    ),
  /** Create a base, or import a `manifest.md` the browser already read.
      **No `overwrite`**, unlike a skill import: replacing a base would silently
      re-type a store other projects are reading. */
  createKnowledgeBase: (body: {
    id: string;
    title?: string;
    type?: string;
    about?: string;
    text?: string;
  }) =>
    request<KnowledgeBase>("/api/knowledge", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /** Edit a base's title and prose — the two fields of the record a base keeps
      about itself that are **not** its identity.

      `PATCH` and not `PUT`: there is still no way to replace a base. Omitting a
      field leaves it alone, and neither `type`, `id` nor `format` is
      expressible here — those are what the base *is*. Fields rather than the
      whole `manifest.md` so the server splices into the file that is already
      there, keeping any frontmatter key it does not itself read. A system base
      takes `about` only. */
  editKnowledgeBase: (id: string, body: { title?: string; about?: string }) =>
    request<KnowledgeBase>(`/api/knowledge/${encodeURIComponent(id)}`, {
      method: "PATCH",
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
  /** Unregisters the project and deletes its `_factories/<project>/` directory.
      Library factories it used are the workspace's and stay. */
  deleteProject: (id: string) =>
    request<void>(`/api/projects/${id}`, { method: "DELETE" }),
  /** Sparse: an omitted key means *unchanged*. `settings` is the exception and
      is replaced whole, because the panel that sends it always knows both
      halves. Writes to `/data/projects.json`, which is machine-local and not
      the git-tracked factory file — so this saves immediately rather than
      joining the canvas's explicit-save policy. */
  updateProject: (
    id: string,
    body: Partial<Pick<Project, "name" | "codebase_ids" | "settings" | "icon">>,
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

  /* The factory library: reusable, fully automated blocks a project drops in
     as a factory node (`decisions/0029`). Saved whole, like a main factory. */
  library: () => request<LibraryFactorySummary[]>("/api/factories"),
  createLibraryFactory: (name: string) =>
    request<Factory>("/api/factories", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  libraryFactory: (id: string) =>
    request<Factory>(`/api/factories/${encodeURIComponent(id)}`),
  saveLibraryFactory: (id: string, graph: GraphWrite) =>
    request<Factory>(`/api/factories/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify(graph),
    }),
  /** Refused with a sentence naming the projects while any uses it. */
  deleteLibraryFactory: (id: string) =>
    request<void>(`/api/factories/${encodeURIComponent(id)}`, { method: "DELETE" }),
  /** Starts a simulation, which then waits for its input like any run. */
  startSimulation: (factoryId: string, codebaseId: string) =>
    request<Run>(`/api/factories/${encodeURIComponent(factoryId)}/simulations`, {
      method: "POST",
      body: JSON.stringify({ codebase_id: codebaseId }),
    }),

  /* The settings surface. Sign-in is polled rather than streamed: SSE is
     stage 5's, and a login has a handful of state changes over a minute of
     human time. */
  agentStatus: () => request<AgentStatus>("/api/agent/status"),
  /** One provider's account and model listing, without waiting on the other. */
  providerStatus: (provider: "claude" | "cursor") =>
    request<ProviderStatus>(`/api/agent/${provider}/status`),
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

  cursorLoginState: () => request<LoginState>("/api/agent/cursor/login"),
  beginCursorLogin: () =>
    request<LoginState>("/api/agent/cursor/login", { method: "POST" }),
  submitCursorLoginCode: (code: string) =>
    request<LoginState>("/api/agent/cursor/login/code", {
      method: "POST",
      body: JSON.stringify({ code }),
    }),
  cancelCursorLogin: () =>
    request<LoginState>("/api/agent/cursor/login/cancel", { method: "POST" }),

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
  /** Explicit cancel — closing the tab is not a decision to stop work.
   *
   * Discards the run: the branch survives, the worktree does not, and neither
   * door will pick it up again. Accepts a run that has already stopped, which
   * is how an operator says a halted run is not worth resuming. */
  cancelRun: (runId: string) =>
    request<Run>(`/api/runs/${runId}/cancel`, { method: "POST" }),
  /** Halt a run, keeping everything it has done.
   *
   * The other half of `cancelRun` (`decisions/0024`): same interrupt, opposite
   * intent. A stopped run keeps its worktree and is offered a Resume. */
  stopRun: (runId: string) =>
    request<Run>(`/api/runs/${runId}/stop`, { method: "POST" }),
  /** Pick a run back up: where it stopped, or from a node you name.
   *
   * The only call that reopens a finished run. Every stopped or failed run may
   * be; a succeeded or cancelled one is refused with a 409, the first because
   * there is nothing to do and the second because somebody decided there was
   * not. With `fromNodeId` it re-runs that node and everything forward of it
   * (`decisions/0023`). */
  resumeRun: (runId: string, fromNodeId?: string) =>
    request<Run>(`/api/runs/${runId}/resume`, {
      method: "POST",
      ...(fromNodeId ? { body: JSON.stringify({ from_node_id: fromNodeId }) } : {}),
    }),
  /** What finished runs have left on disk, and what is being spared.
   *
   * A GET that decides nothing: it is the preview both doors show before
   * anything is removed. `project_id` narrows what is *offered*, never what is
   * safe — the newest resumable run of every factory is held back either way. */
  housekeeping: (projectId?: string, olderThanDays?: number) =>
    request<HousekeepingReport>(
      `/api/housekeeping?${new URLSearchParams({
        ...(projectId ? { project_id: projectId } : {}),
        ...(olderThanDays ? { older_than_days: String(olderThanDays) } : {}),
      })}`,
    ),
  /** Act on a preview. The fingerprint is what ties the two together: if the
   * list moved in between, the server refuses rather than acting on a set the
   * operator never saw. */
  tidy: (body: { fingerprint: string; projectId?: string; olderThanDays?: number }) =>
    request<TidyOutcome>("/api/housekeeping", {
      method: "POST",
      body: JSON.stringify({
        fingerprint: body.fingerprint,
        ...(body.projectId ? { project_id: body.projectId } : {}),
        ...(body.olderThanDays ? { older_than_days: body.olderThanDays } : {}),
      }),
    }),
  run: (runId: string) => request<Run>(`/api/runs/${runId}`),
  runs: () => request<Run[]>("/api/runs"),
  /** Everything one machine did, straight out of the run's log.
   *
   * A GET, like the stream, and for the same reason it is a fetch rather than
   * more state in `run.ts`: the reducer keeps what the *canvas* needs and
   * throws the rest away on every lap, while this file keeps all of it for as
   * long as the run directory exists. `fromSeq` is how an open log window asks
   * for only what it has not already been handed. */
  nodeEvents: (runId: string, nodeId: string, fromSeq = 0) =>
    request<NodeEventPage>(
      `/api/runs/${runId}/nodes/${encodeURIComponent(nodeId)}/events?from=${fromSeq}`,
    ),

  /** One memory node's document, as text. **404 is an ordinary answer here**:
      a memory is discarded when the run ends and kept only when the run failed,
      so the error carries the sentence that says which of the two happened. */
  memoryDocument: (runId: string, nodeId: string) =>
    request<MemoryDocument>(
      `/api/runs/${runId}/memory/${encodeURIComponent(nodeId)}`,
    ),

  sshKey: () => request<SshKeyStatus>("/api/ssh/key"),
  generateSshKey: () => request<SshKeyStatus>("/api/ssh/key", { method: "POST" }),
};
