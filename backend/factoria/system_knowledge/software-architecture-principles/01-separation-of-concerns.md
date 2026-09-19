# Separation of Concerns

Divide a system so that each part addresses one concern, and a change to that
concern touches one part. Presentation, business rules and data access are the
classic three, but the principle is not about those three names — it is about
the question "if this requirement changes, how many places do I open?"

## Why it holds

A concern that is spread across layers has no single owner and no single place
to be correct. The cost is not the first edit; it is the third one, made by
somebody who found two of the three places. Separation is what keeps the blast
radius of a change proportional to the size of the change.

## In practice

- Name the concerns before naming the modules. If you cannot say what a module
  is *about* in one clause, it is about more than one thing.
- Keep a rule in one layer. A validation that exists in the form, in the
  service and in the database constraint is three rules that will disagree.
- Let data cross a boundary, not behaviour. A layer that reaches into another
  layer's internals to do its work has erased the boundary while leaving the
  directory structure that suggests one.
- Separate along axes of change, not along nouns. Two things that always change
  together belong together, whatever they are called.

## How to tell it is being violated

- A one-sentence requirement change produces a diff across four layers.
- The same business rule appears, in different words, in more than one file.
- A test for a business rule needs a database, a HTTP client, or a rendered
  template to run.
- Nobody can answer "where does this decision live?" without grepping.
