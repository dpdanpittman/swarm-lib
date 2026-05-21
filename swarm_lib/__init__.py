"""swarm-lib — filesystem-as-orchestrator for agentic workflows.

See ``DESIGN.md`` (or the rendered HTML companion) for the full v0.1 spec.

Public API:

- :func:`enqueue`, :func:`try_claim`, :func:`complete` — queue primitives
- :class:`Task` — claimed-task dataclass
- :mod:`swarm_lib.status` — durable checkpoint state (``status.json``)
"""

from swarm_lib import status
from swarm_lib.claims import Task, complete, enqueue, try_claim
from swarm_lib.status import Checkpoint, Status

__all__ = [
    "enqueue",
    "try_claim",
    "complete",
    "Task",
    "Status",
    "Checkpoint",
    "status",
]
__version__ = "0.1.0.dev0"
