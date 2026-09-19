# Record Decisions and Enforce Guardrails

Write down the architectural decisions that matter, with their reasons, and
make the rules that follow from them checkable by a machine. Knowledge that
lives only in people's heads leaves when they do; rules that are only in a
document drift.

## Why it holds

Two distinct failures, and they need each other's fix. Without records, the
same debate is had every year by people who do not know it was settled, and a
constraint that was chosen deliberately is undone by somebody who reasonably
assumed it was an accident — the reason is what makes a decision re-evaluable,
and it is the part that is always missing. Without automated guardrails, a
recorded decision decays anyway: the boundary holds in review for six months
and then somebody is in a hurry. A record says what was decided; a check keeps
it true.

## In practice

- One record per decision: the context, the options considered, what was
  chosen, the consequences accepted. Short enough to actually be written.
- Record the *reason*, not just the rule. A rule without its reason cannot be
  revisited, only obeyed or broken.
- Keep records immutable and supersede them. Editing a decision to say something
  else destroys the history that made it worth writing.
- Keep them with the code, in version control, so the decision and the thing it
  decided move together.
- Automate what can be automated: dependency direction, layering, allowed
  imports, contract compatibility, budgets. Run it in CI, where it blocks.
- A guardrail must name the rule it is enforcing and where that rule was
  decided. A failure that just says "forbidden" teaches nobody.

## How to tell it is being violated

- "Why is it like this?" has no answer but "before my time".
- A settled question is reopened in review every few months.
- A documented boundary is violated in several places and nobody noticed.
- A decision record has been edited to reverse its own verdict.
