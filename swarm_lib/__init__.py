"""swarm-lib — filesystem-as-orchestrator for agentic workflows.

See DESIGN.md (or the rendered HTML companion) for the full v0.1 spec.
"""

from swarm_lib.claims import enqueue, try_claim, complete, Task

__all__ = ["enqueue", "try_claim", "complete", "Task"]
__version__ = "0.1.0.dev0"
