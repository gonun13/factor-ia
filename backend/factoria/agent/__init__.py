"""The agent seam: the CLI subprocess layer and the normalized event union.

`DESIGN.md`, Architecture — `AgentAdapter` is what keeps provider-specific types
out of the orchestrator. Import events and the interface from here; import
`claude`/`cursor`/`fake` only where an implementation is actually chosen.
"""

from .adapter import AgentAdapter, AgentRun, RunConfig
from .claude import ClaudeAdapter
from .cursor import CursorAdapter
from .events import EVENT_TYPES, AgentEvent
from .fake import FakeAdapter

__all__ = [
    "EVENT_TYPES",
    "AgentAdapter",
    "AgentEvent",
    "AgentRun",
    "ClaudeAdapter",
    "CursorAdapter",
    "FakeAdapter",
    "RunConfig",
]
