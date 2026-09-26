/**
 * The run panel for a project's main factory: the run as a whole — start it,
 * stop it, resume it, discard it, the branch it owns, what it cost, what is
 * stopping it. Top-right over the canvas, and never covered.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useHoverNote } from "../nodes/NodeNote";
import { blockers, type Problem } from "../validation";
import type { EditorRun } from "./GraphEditor";

export function RunBox({
  problems,
  onFocus,
  run,
}: {
  problems: Problem[];
  onFocus: (ids: string[]) => void;
  run: EditorRun;
}) {
  /** The run panel's one piece of local state: whether Cancel has been pressed
   * once and is waiting to be meant.
   *
   * Inline rather than a modal, like deleting a project (`ProjectList`), and
   * for the reason `ui-ux.md` gives about this corner of the screen: nothing
   * covers the run panel. */
  const [confirmingCancel, setConfirmingCancel] = useState(false);
  const keepItButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (confirmingCancel) keepItButton.current?.focus();
  }, [confirmingCancel]);
  // A run that ends while the confirmation is open leaves it open over a
  // question that no longer applies; drop it when there is nothing to cancel.
  useEffect(() => {
    if (!run.cancellable) setConfirmingCancel(false);
  }, [run.cancellable]);
  /** What this run has written to each knowledge base, by node.
   *
   * Counted, not listed: a base written to six times is one base, and the strip
   * has room for a fact rather than a history — the history is in the node's
   * own log. Refusals are counted separately because they are the opposite
   * news: a machine was told to change a source of truth and did not. **Reads
   * are left out**: the strip is what a run *did*, and consulting a base did
   * nothing to it. */
  const knowledgeWrites = useMemo(() => {
    const tally = new Map<
      string,
      { written: number; refused: number; base: string; detail: string }
    >();
    for (const entries of Object.values(run.storage)) {
      for (const entry of entries) {
        if (entry.store !== "knowledge" || entry.op !== "write") continue;
        const seen = tally.get(entry.nodeId) ?? {
          written: 0,
          refused: 0,
          base: entry.baseId,
          detail: "",
        };
        if (entry.refused) seen.refused += 1;
        else seen.written += 1;
        seen.detail = seen.detail ? `${seen.detail}\n${entry.detail}` : entry.detail;
        tally.set(entry.nodeId, seen);
      }
    }
    return tally;
  }, [run.storage]);

  const stopping = blockers(problems);
  const runnable = stopping.length === 0;
  const notices = problems.filter((problem) => problem.severity === "notice");

  // **What a run does to your repository, under the pointer like a node's.**
  // It is the same three sentences every session and nobody re-reads them, so
  // they answer the question the way every description on this screen answers
  // it now: a second's dwell on the panel, a popup where you are looking.
  const stageNote = useHoverNote(
    <>
      One run at a time. The agent works in its own git worktree on{" "}
      <code>factor-ia/&lt;factory&gt;/&lt;run&gt;</code> — your working tree is
      never touched, and the branch is what it leaves behind. A pushing output
      sends that branch to the remote; nothing is ever forced, and your local
      branches only move when you pull.
    </>,
  );

  return (
    <div
      className="panel runbox"
      data-run-status={run.status ?? "idle"}
      {...stageNote.handlers}
    >
      {stageNote.popup}
      <div className="runbox-head">
        {/* **`Run` is never beside `Resume`, and never reachable while a run
            exists.** Starting fresh re-pays for every node that already
            finished, so the expensive button is one deliberate Cancel away
            rather than one misclick (`decisions/0024`). The three states are
            running, halted, and neither. */}
        {confirmingCancel ? (
          // Inline, replacing the controls rather than covering them: this
          // panel is the one thing on the screen that must never be hidden.
          <span
            className="run-confirm"
            onKeyDown={(event) => {
              if (event.key === "Escape" && !run.busy) setConfirmingCancel(false);
            }}
          >
            <span className="run-confirm-question">Cancel this run?</span>
            <button
              type="button"
              className="run destructive"
              disabled={run.busy}
              onClick={() => {
                setConfirmingCancel(false);
                void run.cancel();
              }}
              title={
                "Discards the run. The branch and its commits survive; the " +
                "worktree under _runs/ does not, and it cannot be resumed after."
              }
            >
              Cancel run
            </button>
            <button
              type="button"
              className="run keep"
              ref={keepItButton}
              disabled={run.busy}
              onClick={() => setConfirmingCancel(false)}
            >
              Keep it
            </button>
          </span>
        ) : (
          <>
            {run.live ? (
              <button
                className="run stop"
                onClick={() => void run.stop()}
                disabled={run.busy}
                title={
                  "Halt this run and keep everything it has done. Its worktree " +
                  "stays, and Resume picks it back up."
                }
              >
                ■ Stop
              </button>
            ) : run.resumable ? (
              // The provider's own sentence is in the tooltip, reset time and
              // all, because "can I resume yet" is a question only that
              // sentence answers — when there is one.
              <button
                className="run resume"
                onClick={() => void run.resume()}
                disabled={run.busy}
                title={[
                  "Pick this run back up where it stopped",
                  run.run?.blocked_reason ?? "",
                  "Nothing that already finished runs again.",
                ]
                  .filter(Boolean)
                  .join("\n")}
              >
                ▶ Resume
              </button>
            ) : (
              <button
                className="run"
                disabled={!runnable || run.busy}
                onClick={() => void run.start()}
                title={
                  runnable
                    ? [
                        "Run this factory",
                        ...notices.map((notice) => notice.message),
                      ].join("\n")
                    : stopping.map((problem) => problem.message).join("\n")
                }
              >
                ▶ Run
              </button>
            )}
            {run.cancellable && (
              <button
                className="run cancel"
                onClick={() => setConfirmingCancel(true)}
                disabled={run.busy}
                title="Discard this run. Asks first."
              >
                ✕ Cancel
              </button>
            )}
          </>
        )}
        <span className="state">
          {run.status
            ? `run ${run.status.replace("_", " ")}`
            : runnable
              ? "This factory is runnable."
              : `${stopping.length} ${stopping.length === 1 ? "thing blocks" : "things block"} a run`}
        </span>
      </div>

      {/* The branch is the run's deliverable and is named before any work
          starts, so it is shown from the first frame rather than at the end —
          and the commit lands on the same line when it exists. */}
      {run.run?.branch && (
        <p className="branchline">
          <span className="mono">{run.run.branch}</span>
          {run.run.dangerous && (
            <span className="danger" title="This run bypasses the agent's own permission checks.">
              ⚠ permissions bypassed
            </span>
          )}
          {run.artifacts.map((artifact) => {
            const key = `${artifact.kind}-${artifact.commitSha ?? artifact.path}`;
            // Memories are drawn below, off `run.memories` — one line per
            // memory node rather than one per write. Skipped here so they do
            // not also fall through to the default below, which would announce
            // an "isolated worktree" every time a document was filed: the one
            // artifact kind with no worktree and no branch behind it,
            // described as the thing it is not.
            if (artifact.kind === "memory") return null;
            if (artifact.kind === "commit") {
              return (
                <span key={key} className="commit">
                  ✓ {artifact.commitSha?.slice(0, 8)} · {artifact.fileCount} file
                  {artifact.fileCount === 1 ? "" : "s"}
                </span>
              );
            }
            if (artifact.kind === "push") {
              return (
                <span key={key} className="commit" title={artifact.detail}>
                  ↥ pushed to {artifact.remote}
                </span>
              );
            }
            if (artifact.kind === "merge") {
              // The one artifact worth its own emphasis: a branch other people
              // pull from has moved.
              return (
                <span key={key} className="danger" title={artifact.detail}>
                  ⇢ {artifact.remote}/{artifact.mergedInto} fast-forwarded
                </span>
              );
            }
            return (
              <span key={key} className="worktree" title={artifact.path}>
                isolated worktree{artifact.baseBranch ? ` from ${artifact.baseBranch}` : ""}
              </span>
            );
          })}
          {/* One per memory node that has anything in it, in node order and
              not in write order: this is a shelf's contents, and a shelf
              written to four times is still one shelf. */}
          {Object.entries(run.memories).map(([nodeId, filed]) => (
            <span key={`memory-${nodeId}`} className="memory" title={filed.path}>
              ▣ filed {filed.fileCount} section{filed.fileCount === 1 ? "" : "s"}
            </span>
          ))}
          {/* One per base this run wrote to, counted rather than listed: a
              base that took six writes is still one base, and **a refusal is
              said out loud** — it is the one thing here that means a source of
              truth did *not* change after a machine was told to change it. */}
          {[...knowledgeWrites.entries()].map(([nodeId, tally]) => (
            <span
              key={`knowledge-${nodeId}`}
              className={tally.refused ? "danger" : "memory"}
              title={tally.detail}
            >
              ◆ {tally.written} written to {tally.base || "a base"}
              {tally.refused > 0 && ` · ${tally.refused} refused`}
            </span>
          ))}
        </p>
      )}

      {/* **The three forms used to be here** — the request, a gate's sign-off,
          a scope node's questionnaire — and they are the chat panel's now
          (`RunChat`). They were in the run box because the run box was the only
          thing beside the canvas, and they were the worst-served thing in it: a
          sign-off exists so that somebody reads what arrived first, and this
          column is not a width anybody reads a diff in. What is left here is
          what this panel is actually for — the run as a whole. */}

      {run.status && !run.live && (
        // **Coloured by how it ended, not by the fact that it ended.** This line
        // borrowed `.ready`, which is green because the line it was written for
        // says the factory is ready to run — so a run that failed announced
        // itself in the colour of success, with the worktree it left behind for
        // you to go and look at reading as good news. The status is right here
        // on the element; the colour comes off it.
        <p className="ready outcome" data-status={run.status}>
          {/* The detail is often just the status word again ("cancelled"), and
              "cancelled clear" is not a sentence. */}
          {run.detail && run.detail !== run.status
            ? run.detail
            : `The run ${run.status}.`}
          {run.costUsd > 0 && <span className="mono"> · ${run.costUsd.toFixed(4)}</span>}{" "}
          <button className="link-button" onClick={() => run.clear()}>
            clear
          </button>
        </p>
      )}

      {run.live && run.costUsd > 0 && (
        // Not `.outcome`: a running total is a fact, not a verdict, and it has
        // no status to take a colour from yet.
        <p className="ready running-cost mono">${run.costUsd.toFixed(4)} so far</p>
      )}

      {run.error && <p className="problems-note">{run.error}</p>}

      {/* The server's own refusal. It is a different, stricter check than the
          save-time one, and it is the one that actually stops a run — the list
          below is the client saying the same thing early. */}
      {run.problems.length > 0 && (
        <ul className="problems">
          {run.problems.map((problem) => (
            <li key={problem}>
              <span className="marker">▲</span>
              <span>{problem}</span>
            </li>
          ))}
        </ul>
      )}

      {/* Blockers and notices in one list, because they belong in one place on
          the page. Only the blockers disable Run. */}
      {!run.status && problems.length > 0 && (
        <ul className="problems">
          {problems.map((problem) => (
            <li
              key={problem.message}
              className={problem.severity === "notice" ? "notice" : undefined}
            >
              <button
                onClick={() => onFocus(problem.nodeIds)}
                disabled={problem.nodeIds.length === 0}
              >
                <span className="marker">▲</span>
                <span>{problem.message}</span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {!run.status && runnable && notices.length === 0 && (
        <p className="ready">Every machine is on the line and every agent has a codebase.</p>
      )}

      {/* The popup's twin for screen readers: it is `aria-hidden`, and a
          sentence that exists only while a pointer rests on something is no
          sentence at all to a reader who has no pointer. */}
      <p className="offscreen">
        One run at a time. The agent works in its own git worktree on
        factor-ia/&lt;factory&gt;/&lt;run&gt; — your working tree is never
        touched, and the branch is what it leaves behind. A pushing output sends
        that branch to the remote; nothing is ever forced, and your local
        branches only move when you pull.
      </p>
    </div>
  );
}
