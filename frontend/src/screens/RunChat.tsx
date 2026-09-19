/**
 * The chat: what the run is asking, and everything it has already asked.
 *
 * **Why it left the run panel.** The three forms an operator answers — the
 * request, a gate's sign-off, a scope node's questionnaire — used to be stacked
 * inside the run box, in a 25rem column they shared with the Run button, the
 * branch line, the problem list and a full transcript per machine. A gate's
 * whole purpose is that somebody reads what arrived before approving it, and
 * 25rem minus a scrollbar is not a width you read a diff in. So the forms moved
 * out to a panel wide enough for the job, floating over the canvas — which the
 * canvas can afford now that the transcript has gone too (`Timeline`).
 *
 * **It is a conversation, not a form.** The open form is pinned at the bottom,
 * and above it is what has already been said: the request that started the run,
 * every question and the answers given back, every verdict and its note. The
 * old run box cleared each one the instant it was answered, which is right for
 * a *form* — nobody is waiting on it any more — and wrong for the record of a
 * negotiation the operator is still in the middle of. `RunState.exchanges` is
 * that record, built from the log like everything else, so a second tab and a
 * reloaded page read the same conversation.
 *
 * Still not a chat in the other sense: there is no free-form message box, and
 * the run is not something you talk to. Input is one-shot and answers are
 * structured (`decisions/0007-one-shot-input-sse-stands.md`). What is shown
 * here is exactly what was posted through `/input`, `/decision` and `/answer`.
 */

import { useEffect, useRef, useState } from "react";
import type { Exchange } from "../run";
import type { NodeRun, ScopeQuestion } from "../types";

export function RunChat({
  exchanges,
  awaiting,
  parked,
  asking,
  busy,
  labels,
  onSubmit,
  onDecide,
  onAnswer,
}: {
  exchanges: Exchange[];
  awaiting: NodeRun[];
  parked: { nodeRun: NodeRun; prompt: string; incoming: string; loops: boolean }[];
  asking: { nodeRun: NodeRun; requestId: string; questions: ScopeQuestion[] }[];
  busy: boolean;
  /** Node id to the name on the canvas, for the history above the forms. The
   * open forms carry their own `NodeRun`, which has the label frozen at the
   * moment the run started; the history is older than some of those and this
   * is the map the canvas is drawing right now. */
  labels: Record<string, string>;
  onSubmit: (text: string, options: { nodeId: string; filename?: string }) => void;
  onDecide: (nodeId: string, approved: boolean, note: string) => void;
  onAnswer: (nodeId: string, requestId: string, answers: Record<string, string>) => void;
}) {
  const open = awaiting.length + parked.length + asking.length;
  const [shown, setShown] = useState(true);
  const tail = useRef<HTMLDivElement>(null);

  // Something new to answer re-opens a collapsed panel. A run that stopped to
  // ask a question and got no answer because the panel was folded away would be
  // a run stuck behind a piece of chrome.
  useEffect(() => {
    if (open > 0) setShown(true);
  }, [open]);

  useEffect(() => {
    const element = tail.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [exchanges.length, open]);

  if (exchanges.length === 0 && open === 0) return null;

  const who = (nodeId: string) => labels[nodeId] ?? nodeId;

  return (
    <section className={`panel chatpane nowheel nopan${shown ? "" : " folded"}`}>
      <header className="chatpane-head">
        <span className="eyebrow">Chat</span>
        {open > 0 && <span className="chat-open">{open} waiting on you</span>}
        <span className="spacer" />
        <button
          className="link-button"
          onClick={() => setShown(!shown)}
          aria-expanded={shown}
        >
          {shown ? "hide" : "show"}
        </button>
      </header>

      {shown && (
        <>
          {exchanges.length > 0 && (
            <div className="chat-history" ref={tail}>
              {exchanges.map((exchange) => (
                <Said key={`${exchange.seq}-${exchange.kind}`} exchange={exchange} who={who} />
              ))}
            </div>
          )}

          <div className="chat-forms">
            {/* One form per input node still waiting — submitted once each, and
                not a message box (`DESIGN.md`, open question 2). The *run
                record* decides which are shown: the walk starts on the last
                submission, so a form that vanished because the server said so
                cannot disagree with the server. */}
            {awaiting.map((node_run) => (
              <InputForm
                key={node_run.node_id}
                nodeRun={node_run}
                busy={busy}
                remaining={awaiting.length}
                onSubmit={onSubmit}
              />
            ))}

            {/* And one per human node the walk is parked on. Driven by the
                *stream* rather than by the run record, which is a snapshot from
                before the walk and cannot see a node that parks mid-graph. */}
            {parked.map((gate) => (
              <DecisionForm
                key={gate.nodeRun.node_id}
                nodeRun={gate.nodeRun}
                prompt={gate.prompt}
                incoming={gate.incoming}
                loops={gate.loops}
                busy={busy}
                onDecide={(approved, note) => onDecide(gate.nodeRun.node_id, approved, note)}
              />
            ))}

            {/* And one per open questionnaire — keyed by request rather than by
                node: a scope node asks many times, and the form on screen has
                to be the one being answered. */}
            {asking.map((question) => (
              <QuestionForm
                key={question.requestId}
                nodeRun={question.nodeRun}
                questions={question.questions}
                busy={busy}
                onAnswer={(answers) =>
                  onAnswer(question.nodeRun.node_id, question.requestId, answers)
                }
              />
            ))}
          </div>
        </>
      )}
    </section>
  );
}

/** One turn of the conversation, after the fact. Compact on purpose: the thing
 * being answered is below, and this is what it follows from. */
function Said({
  exchange,
  who,
}: {
  exchange: Exchange;
  who: (nodeId: string) => string;
}) {
  const name = who(exchange.nodeId);

  if (exchange.kind === "input") {
    return (
      <article className="said" data-from="operator">
        <h5>{exchange.filename ? `you handed over ${exchange.filename}` : "you asked for"}</h5>
        <p>{exchange.text}</p>
      </article>
    );
  }
  if (exchange.kind === "gate") {
    return (
      <article className="said" data-from="run">
        <h5>{name} asked</h5>
        <p>{exchange.prompt || "approve or reject"}</p>
      </article>
    );
  }
  if (exchange.kind === "verdict") {
    return (
      <article className="said" data-from="operator">
        <h5 className={exchange.approved ? "good" : "bad"}>
          you {exchange.approved ? "approved" : "rejected"} {name}
        </h5>
        {exchange.note && <p>{exchange.note}</p>}
      </article>
    );
  }
  if (exchange.kind === "asked") {
    return (
      <article className="said" data-from="run">
        <h5>{name} asked</h5>
        <ul className="plain">
          {exchange.questions.map((question) => (
            <li key={question.question}>{question.question}</li>
          ))}
        </ul>
      </article>
    );
  }
  return (
    <article className="said" data-from="operator">
      <h5>you answered</h5>
      <ul className="plain">
        {Object.entries(exchange.answers).map(([question, answer]) => (
          <li key={question}>
            <b>{question}</b> — {answer}
          </li>
        ))}
      </ul>
    </article>
  );
}

/**
 * What one input node asks a human for.
 *
 * A `request` wants a sentence and a `prd` wants a document, which is the whole
 * reason input submission became per-node: broadcasting one string to every
 * input node stopped being a simplification the moment the two kinds existed.
 *
 * The PRD arrives as a **file read in the browser** and posted as text. No
 * upload endpoint, no file on disk to clean up, and — the reason that decided
 * it — no second path into the container that takes a filename. The name is
 * carried alongside purely as a label: it is shown, and written into the
 * commit's `Factor-ia-Source` trailer, and nothing ever opens it.
 */
function InputForm({
  nodeRun,
  busy,
  remaining,
  onSubmit,
}: {
  nodeRun: NodeRun;
  busy: boolean;
  remaining: number;
  onSubmit: (text: string, options: { nodeId: string; filename?: string }) => void;
}) {
  const [text, setText] = useState("");
  const [filename, setFilename] = useState<string | null>(null);
  const [reading, setReading] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const isPrd = nodeRun.kind === "prd";
  const name = nodeRun.label || nodeRun.node_id;

  const take = async (file: File | undefined) => {
    if (!file) return;
    setProblem(null);
    setReading(true);
    try {
      const contents = await file.text();
      if (!contents.trim()) {
        setProblem(`${file.name} is empty.`);
        return;
      }
      setText(contents);
      setFilename(file.name);
    } catch (cause) {
      setProblem(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setReading(false);
    }
  };

  return (
    <form
      className="request"
      onSubmit={(event) => {
        event.preventDefault();
        if (!text.trim()) return;
        onSubmit(isPrd ? text : text.trim(), {
          nodeId: nodeRun.node_id,
          filename: filename ?? undefined,
        });
      }}
    >
      <label htmlFor={`run-input-${nodeRun.node_id}`}>
        {isPrd ? `${name} — hand over a PRD` : "What should this factory do?"}
        {remaining > 1 && <span className="hint"> · {remaining} inputs waiting</span>}
      </label>

      {isPrd ? (
        <div
          className={`prd-drop${filename ? " has-file" : ""}`}
          onDragOver={(event) => {
            event.preventDefault();
            event.stopPropagation();
            event.dataTransfer.dropEffect = "copy";
          }}
          onDrop={(event) => {
            event.preventDefault();
            event.stopPropagation();
            void take(event.dataTransfer.files[0]);
          }}
        >
          <input
            id={`run-input-${nodeRun.node_id}`}
            className="nodrag"
            type="file"
            accept=".md,.markdown,.txt,text/markdown,text/plain"
            onChange={(event) => void take(event.target.files?.[0])}
          />
          {filename ? (
            <span className="prd-chosen">
              <span className="mono">{filename}</span>
              <span className="hint">
                {" "}
                · {text.split(/\r?\n/).length} lines, {Math.ceil(text.length / 1024)} kB
              </span>
            </span>
          ) : (
            <span className="hint">
              {reading ? "Reading…" : "Drop a PRD.md here, or choose one."}
            </span>
          )}
        </div>
      ) : (
        <textarea
          id={`run-input-${nodeRun.node_id}`}
          className="nodrag nowheel"
          autoFocus
          rows={3}
          value={text}
          placeholder="Tidy the README and fix the broken link."
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
              event.currentTarget.form?.requestSubmit();
            }
          }}
        />
      )}

      {problem && <p className="problems-note">{problem}</p>}
      <button className="primary" type="submit" disabled={!text.trim() || busy || reading}>
        {isPrd ? "Submit the PRD" : "Submit the request"}
      </button>
    </form>
  );
}

/**
 * The form a human node parks the run on: what arrived, and two ways out.
 *
 * Sibling of `InputForm`, and deliberately the same shape — it appears in the
 * chat panel over the canvas rather than in a modal, so the graph the operator
 * is being asked about never goes away while they answer.
 *
 * **Both buttons are always live, and neither is the default.** A sign-off whose
 * reject button is a second click behind a confirmation is a sign-off that gets
 * approved, and this is the one control on the screen whose entire purpose is
 * that somebody read something first. The note is optional on approve and the
 * thing that makes a rejection useful: it replaces what the node passes on, so
 * the fail belt carries a reason rather than a copy of what was rejected.
 */
/**
 * One questionnaire from a `scope` node, answered under the canvas.
 *
 * The same place and the same shape as `DecisionForm` — a form where the run is
 * rather than a modal, so the canvas never goes away while you answer — and the
 * differences are all the difference between a gate and an interview:
 *
 * - **Options are answers, not buttons.** The agent offers them, so they are a
 *   list you pick from rather than a fixed Approve/Reject pair, and "other" is
 *   always there because an agent's options are a guess at what you will say.
 * - **Keyed by question text.** That is the provider's own contract: the answer
 *   map goes back keyed by the question exactly as asked, so nothing here
 *   rewrites the wording it renders.
 * - **`1`–`9` picks an option**, because an interview is a sequence and reaching
 *   for the mouse once per question is the thing that makes people stop using
 *   one. The listener is scoped to the form, so it cannot eat a digit typed
 *   into a node's label on the canvas behind it.
 */
function QuestionForm({
  nodeRun,
  questions,
  busy,
  onAnswer,
}: {
  nodeRun: NodeRun;
  questions: ScopeQuestion[];
  busy: boolean;
  onAnswer: (answers: Record<string, string>) => void;
}) {
  // Keyed by question text, like the thing being built. `picked` holds the
  // option labels chosen; `typed` holds whatever was written in "other". A
  // question with both sends what was typed — you cannot type by accident.
  const [picked, setPicked] = useState<Record<string, string[]>>({});
  const [typed, setTyped] = useState<Record<string, string>>({});
  const name = nodeRun.label || nodeRun.node_id;
  const form = useRef<HTMLFormElement>(null);

  // **Focus follows the question.** The digit shortcuts are listened for on the
  // form, so they only work while it holds focus — and a form the operator has
  // to click before they can answer is a shortcut nobody discovers. This is
  // also the accessible behaviour: the run stopped to ask something, so the
  // thing it is asking is where the keyboard should be. Safe to take: a scope
  // node parks the whole run, so nothing else on the canvas is mid-edit.
  useEffect(() => {
    form.current?.focus();
  }, []);

  const answerFor = (question: ScopeQuestion): string => {
    const written = (typed[question.question] ?? "").trim();
    if (written) return written;
    return (picked[question.question] ?? []).join(", ");
  };
  const answers = Object.fromEntries(
    questions.map((question) => [question.question, answerFor(question)]),
  );
  // Every question, or none. A partially answered form posted anyway would be
  // filtered server-side into a shorter map, and the agent would read the gaps
  // as questions it has to ask again.
  const complete = questions.every((question) => answers[question.question] !== "");

  const choose = (question: ScopeQuestion, label: string) => {
    setPicked((previous) => {
      const current = previous[question.question] ?? [];
      if (!question.multi_select) {
        return { ...previous, [question.question]: current[0] === label ? [] : [label] };
      }
      return {
        ...previous,
        [question.question]: current.includes(label)
          ? current.filter((one) => one !== label)
          : [...current, label],
      };
    });
  };

  return (
    <form
      className="request question"
      ref={form}
      // Focusable but not in the tab order: the form is a container, and a stop
      // on the way to its own first button would be a tab stop that does
      // nothing.
      tabIndex={-1}
      onSubmit={(event) => {
        event.preventDefault();
        if (complete && !busy) onAnswer(answers);
      }}
      onKeyDown={(event) => {
        // Only a bare digit, and only when the focus is not in a text box —
        // otherwise "1" would be untypeable in the "other" field.
        if (event.metaKey || event.ctrlKey || event.altKey) return;
        const target = event.target as HTMLElement;
        if (target.tagName === "INPUT" || target.tagName === "TEXTAREA") return;
        if (busy) return;
        const digit = Number(event.key);
        if (!Number.isInteger(digit) || digit < 1 || digit > 9) return;
        // The first unanswered question, which is the one being read.
        const open =
          questions.find((question) => answers[question.question] === "") ?? questions[0];
        const option = open?.options[digit - 1];
        if (!option) return;
        event.preventDefault();
        choose(open, option.label);
      }}
    >
      <label>{name} is asking</label>

      {questions.map((question) => (
        <fieldset className="question-block" key={question.question}>
          <legend>
            {question.header && <span className="question-chip">{question.header}</span>}
            {question.question}
          </legend>

          {question.options.length > 0 && (
            <ol className="question-options">
              {question.options.map((option, index) => {
                const chosen = (picked[question.question] ?? []).includes(option.label);
                return (
                  <li key={option.label}>
                    <button
                      type="button"
                      className={`question-option${chosen ? " chosen" : ""}`}
                      aria-pressed={chosen}
                      disabled={busy}
                      onClick={() => choose(question, option.label)}
                    >
                      <kbd>{index + 1}</kbd>
                      <span className="question-label">{option.label}</span>
                      {option.description && (
                        <span className="question-what">{option.description}</span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ol>
          )}

          <input
            className="nodrag nowheel"
            value={typed[question.question] ?? ""}
            aria-label={`another answer to: ${question.question}`}
            placeholder={
              question.options.length > 0 ? "…or say something else" : "Your answer"
            }
            disabled={busy}
            onChange={(event) =>
              setTyped((previous) => ({
                ...previous,
                [question.question]: event.target.value,
              }))
            }
          />
        </fieldset>
      ))}

      <div className="decision-actions">
        <button className="primary" type="submit" disabled={busy || !complete}>
          Answer
        </button>
        {!complete && (
          <span className="question-hint">
            {questions.length === 1
              ? "Pick an option or write an answer."
              : "Every question needs an answer."}
          </span>
        )}
      </div>
    </form>
  );
}

/**
 * Approve or reject, and the note that travels with it.
 *
 * **The note is optional on a gate whose rejection runs forward, and required
 * on one whose rejection loops.** On a forward belt the note is a comment and
 * what travels is the work being escalated; on a back-edge the note *replaces*
 * what the node passes on, so it is the entire brief for the next lap. Sending
 * a worker round again with nothing to act on is the human version of the
 * missing verdict that already ends a run, so Reject stays disabled until the
 * box has something in it. `submit_decision` refuses the same thing server-side
 * — this is the courtesy half, and the server is the control.
 */
function DecisionForm({
  nodeRun,
  prompt,
  incoming,
  loops,
  busy,
  onDecide,
}: {
  nodeRun: NodeRun;
  prompt: string;
  incoming: string;
  loops: boolean;
  busy: boolean;
  onDecide: (approved: boolean, note: string) => void;
}) {
  const [note, setNote] = useState("");
  const name = nodeRun.label || nodeRun.node_id;
  const needsNote = loops && note.trim() === "";

  return (
    <form className="request decision" onSubmit={(event) => event.preventDefault()}>
      <label htmlFor={`run-note-${nodeRun.node_id}`}>
        {prompt || `${name} — approve or reject`}
      </label>

      {incoming && (
        <pre className="stream nowheel decision-incoming">{incoming}</pre>
      )}

      <textarea
        id={`run-note-${nodeRun.node_id}`}
        className="nodrag nowheel"
        rows={2}
        value={note}
        placeholder={
          loops
            ? "What needs to change? Rejecting sends the work back up the line, and this is all the next lap gets."
            : "Optional on approve. On reject, this is what goes back down the line."
        }
        onChange={(event) => setNote(event.target.value)}
      />

      <div className="decision-actions">
        <button
          className="primary"
          type="button"
          disabled={busy}
          onClick={() => onDecide(true, note)}
        >
          Approve
        </button>
        <button
          className="destructive"
          type="button"
          disabled={busy || needsNote}
          onClick={() => onDecide(false, note)}
        >
          Reject
        </button>
      </div>
      {needsNote && (
        <span className="decision-hint">
          Rejecting sends this back up the line — say what to change.
        </span>
      )}
    </form>
  );
}
