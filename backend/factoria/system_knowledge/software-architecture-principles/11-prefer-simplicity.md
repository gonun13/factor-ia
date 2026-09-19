# Prefer Simplicity Until You Earn Complexity

Start with the simplest structure that solves the problem in front of you. Take
on complexity — distribution, extra services, caching layers, generalised
abstractions — when a measured need demands it, and not before.

## Why it holds

Complexity taken on speculatively is paid for immediately and continuously,
while the benefit is hypothetical and often never arrives. The scale that was
designed for does not appear, the second implementation of the interface is
never written, the pluggability is used once. Meanwhile every change costs
more, every incident has more places to look, and every new joiner takes longer.
Simple systems are also easier to *change into* complex ones later, because the
decisions are still legible; the reverse is rarely true.

## In practice

- Solve today's problem with today's information. A requirement you have been
  told about is real; one you have imagined is not.
- Take on a new moving part when you can say what forced it: a number, a limit
  you hit, a team boundary that already exists.
- Prefer one well-factored component over three that must be released together.
  Distribution is a cost paid for independence you can actually use.
- Two occurrences are a coincidence. Abstract on the third, when the shape of
  the commonality is visible rather than guessed.
- Deleting is a design action. The cheapest component to operate is the one
  that is not there.

## How to tell it is being violated

- An abstraction has exactly one implementation, and has had for a year.
- A configuration option nobody has ever set to anything but the default.
- Explaining the system to a new joiner needs a diagram and an apology.
- A service exists because of a scale requirement that was never measured.
