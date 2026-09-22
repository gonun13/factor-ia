"""The agent seam: the CLI subprocess layer and the normalized event union.

`DESIGN.md`, Architecture — `AgentAdapter` is what keeps provider-specific types
out of the orchestrator. Import events and the interface from here; import
`claude`/`cursor`/`fake` only where an implementation is actually chosen.

The permission-mode vocabulary (`resolve_permission_mode`, `PermissionDenied`)
lives on the seam too: every provider is handed one word, and a gate copied per
adapter is how two gates drift into one hole.
"""

from .adapter import (
    AgentAdapter,
    AgentRun,
    PermissionDenied,
    RunConfig,
    resolve_permission_mode,
)
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
    "PermissionDenied",
    "RunConfig",
    "resolve_permission_mode",
]
