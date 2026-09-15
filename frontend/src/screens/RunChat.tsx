/**
 * The run pane's log: one thread per machine, left to right along the line.
 *
 * The node used to carry the agent's words itself, and a 15rem chassis holds
 * about five lines of them — a keyhole to watch a run through. The words live
 * here instead, in a pane docked beside the canvas, while the node keeps the
 * half that is legible at a glance: its pip and its status word.
 *
 * It reads `runtimes` straight off `useRun` — the same map that lights the
 * canvas, so a thread and its machine can never disagree about what is
 * happening. Nothing is fetched here and nothing is stored: a thread exists for
 * exactly as long as the run that filled it.
 */

import { useEffect, useRef } from "react";
import type { FlowNode } from "../graph";
import type { Runtimes } from "../run";
import type { NodeRuntime, NodeType } from "../types";

const TYPE_NAME: Record<string, string> = {
  input: "request",
  agent: "agent",
  output: "commit",
};

export function RunChat({ nodes, runtimes }: { nodes: FlowNode[]; runtimes: Runtimes }) {
  // Only machines the run has reached, ordered by where they sit on the sheet:
  // the belts run left to right, so that is also the order they work in.
  const threads = nodes
    .filter((node) => runtimes[node.id])
    .sort((first, second) => first.position.x - second.position.x);

  return (
    <section className="panel runchat">
      <span className="eyebrow">Run log</span>
      {threads.length === 0 ? (
        <p className="runchat-empty">
          Nothing has run yet. Press Run and every machine reports here — the
          tool it is on, its checklist, and its words as they arrive.
        </p>
      ) : (
        threads.map((node) => (
          <Thread
            key={node.id}
            label={node.data.definition.label}
            type={node.data.definition.type}
            runtime={runtimes[node.id]}
          />
        ))
      )}
    </section>
  );
}

/** One machine's side of the run, assembled from the event stream. */
function Thread({
  label,
  type,
  runtime,
}: {
  label: string;
  type: NodeType;
  runtime: NodeRuntime;
}) {
  const tail = useRef<HTMLPreElement>(null);
  const following = useRef(true);
  const text = runtime.text ?? "";

  useEffect(() => {
    // Follow the stream — a thread showing the first words of a reply forever
    // reads as "stuck" rather than "working". But only while the operator is
    // already at the bottom: yanking them back mid-sentence, every delta, is
    // worse than not following at all.
    const element = tail.current;
    if (element && following.current) element.scrollTop = element.scrollHeight;
  }, [text]);

  const todos = runtime.todos ?? [];
  const artifact = runtime.artifact ?? null;
  const foot = Boolean(runtime.toolCount || runtime.costUsd || artifact?.commitSha);

  return (
    <article className="thread" data-status={runtime.status}>
      <header className="thread-head">
        <span className="pip" data-status={runtime.status} title={`status: ${runtime.status}`} />
        <span className="who">{label}</span>
        <span className="eyebrow">{TYPE_NAME[type] ?? type}</span>
        <span className="status-word">{runtime.status.replace("_", " ")}</span>
      </header>

      {runtime.detail && <p className="thread-detail">{runtime.detail}</p>}

      {runtime.tool && (
        <span className="tool" data-state={runtime.tool.state}>
          <b>{runtime.tool.name}</b>
          {runtime.tool.target && <span className="target mono">{runtime.tool.target}</span>}
        </span>
      )}

      {/* The checklist in full, not a count: the pane has the room the node
          never did, and it is the clearest statement of the step it is on. */}
      {todos.length > 0 && (
        <ol className="todos">
          {todos.map((item) => (
            <li key={item.content} data-status={item.status}>
              <span className="box">{TODO_MARK[item.status] ?? "☐"}</span>
              <span>{item.content}</span>
            </li>
          ))}
        </ol>
      )}

      {text && (
        <pre
          className="stream"
          ref={tail}
          onScroll={(event) => {
            const element = event.currentTarget;
            following.current =
              element.scrollHeight - element.scrollTop - element.clientHeight < 24;
          }}
        >
          {text}
        </pre>
      )}

      {foot && (
        <footer className="thread-foot">
          {runtime.toolCount ? (
            <span>
              {runtime.toolCount} {runtime.toolCount === 1 ? "tool" : "tools"}
            </span>
          ) : null}
          {runtime.costUsd ? <span className="mono">${runtime.costUsd.toFixed(4)}</span> : null}
          {artifact?.commitSha && (
            <span className="commit mono" title={artifact.branch}>
              ✓ {artifact.commitSha.slice(0, 8)}
            </span>
          )}
        </footer>
      )}
    </article>
  );
}

const TODO_MARK: Record<string, string> = {
  completed: "☑",
  in_progress: "▸",
  pending: "☐",
};
