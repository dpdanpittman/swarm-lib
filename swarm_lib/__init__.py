"""swarm-lib — filesystem-as-orchestrator for agentic workflows.

See ``DESIGN.md`` (or the rendered HTML companion) for the full v0.1 spec.

Public API:

- :func:`enqueue`, :func:`try_claim`, :func:`complete` — queue primitives
- :class:`Task` — claimed-task dataclass
- :mod:`swarm_lib.status` — durable checkpoint state (``status.json``)
"""

from swarm_lib import orphan, status
from swarm_lib.claims import CrossFilesystemError, Task, complete, enqueue, try_claim
from swarm_lib.orphan import ReapedClaim, ReapResult
from swarm_lib.status import Checkpoint, Status

__all__ = [
    "enqueue",
    "try_claim",
    "complete",
    "Task",
    "Status",
    "Checkpoint",
    "CrossFilesystemError",
    "ReapedClaim",
    "ReapResult",
    "status",
    "orphan",
]
__version__ = "0.2.0"
