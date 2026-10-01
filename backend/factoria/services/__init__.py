"""The services: the run and everything it is assembled from.

`orchestrator` is the use case — start, walk, stop, resume, cancel — and the
modules beside it are the rules it applies across several entities at once:
the run check, factory expansion, run memory, the commit pass, housekeeping.
"""
