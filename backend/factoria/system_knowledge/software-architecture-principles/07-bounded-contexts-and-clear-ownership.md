# Bounded Contexts and Clear Ownership

Draw explicit boundaries around parts of the system, and say who owns each one.
Inside a boundary, a term has one meaning, the rules are consistent, and there
is a name to ask. Across a boundary, terms are translated deliberately.

## Why it holds

This is the principle that bounds how much of the system any one person has to
hold in their head — which is the real scaling constraint, long before
throughput. The failure it prevents is subtler than a bug: the same word,
"order" or "account" or "user", meaning three different things in three parts
of the system, with no one place where the disagreement is visible. Code written
on the wrong meaning is code that works in testing and is wrong in production.

## In practice

- Write down each context's boundary and the one meaning it gives its central
  terms. A glossary per context beats a glossary per system.
- Translate explicitly at the seams. A mapping layer between two contexts is
  not overhead; it is where the disagreement is made visible and handled.
- Give every context a named owner — a team or a person — who decides what goes
  in it. Shared ownership of a boundary is no ownership of it.
- Let contexts follow how the organisation actually works. A boundary that cuts
  across a team will be routed around.
- Do not force one canonical model on the whole system. Two contexts disagreeing
  about what a "customer" is, and translating, is the correct answer.

## How to tell it is being violated

- The same noun means different things depending on who is talking.
- A change requires agreement from three teams with no obvious decider.
- One shared model has grown optional fields that only some consumers set.
- New joiners repeatedly make the same wrong assumption about a term.
