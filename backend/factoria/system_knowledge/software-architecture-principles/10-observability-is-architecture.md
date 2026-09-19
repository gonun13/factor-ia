# Observability and Operability Are Architecture

How a system is operated and understood in production is a design concern, at
the same level as how it is structured. You cannot operate what you cannot see,
and you cannot add visibility convincingly after the fact.

## Why it holds

Observability bolted on afterwards answers the questions somebody thought of
later, which are rarely the questions an incident asks. The useful ones —
which request, which tenant, which dependency, how long, how often, compared to
what — need identity and context to be carried through the system, and that is
a structural decision. It has to be made where the boundaries are drawn, not in
a dashboard afterwards.

## In practice

- Structured events, not prose log lines. A log that must be parsed with a
  regex is data that was thrown away and partly recovered.
- Propagate a correlation identifier across every boundary, including
  asynchronous ones, and include it in every event.
- Instrument the boundaries: rate, errors and duration for everything crossing
  a seam, as a default rather than a decision.
- Measure what the user experiences, not what the server did. Latency is
  distribution, so record percentiles; an average hides the outage.
- State service level objectives and alert on those, not on every anomaly.
  Alerts nobody can act on train people to ignore alerts.
- Never log secrets or personal data. Redaction is part of the design of the
  event, not a filter downstream.

## How to tell it is being violated

- Diagnosing an incident requires adding logging and waiting for it to recur.
- You cannot follow one request across two services.
- Dashboards show infrastructure health while users are failing.
- The first report of an outage comes from a customer.
