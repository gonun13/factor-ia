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
  LoginState,
  Project,
  Run,
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

  projects: () => request<Project[]>("/api/projects"),
  createProject: (name: string, codebaseIds: string[] = []) =>
    request<Project>("/api/projects", {
      method: "POST",
      body: JSON.stringify({ name, codebase_ids: codebaseIds }),
    }),
  project: (id: string) => request<Project>(`/api/projects/${id}`),

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
  /** Explicit cancel — closing the tab is not a decision to stop work. */
  cancelRun: (runId: string) =>
    request<Run>(`/api/runs/${runId}/cancel`, { method: "POST" }),
  run: (runId: string) => request<Run>(`/api/runs/${runId}`),
  runs: () => request<Run[]>("/api/runs"),

  sshKey: () => request<SshKeyStatus>("/api/ssh/key"),
  generateSshKey: () => request<SshKeyStatus>("/api/ssh/key", { method: "POST" }),
};
