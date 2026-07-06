"""START_ID — the flow's parameter-binding boundary, internal-only. PURE.

Charter: this package owns the synthesized START_ID node — the IR realization of a flow's `input:`
section as its single root. The loader synthesizes ONE StartNode per flow from its input decls;
`run(record)` = coerce + e08 type-check + apply_defaults, returning the bound input record as one
object keyed by input name (store[<start id>], the `inputs`-namespace replacement).

Imports flow one way: `nodes.base`/`nodes.binding` (peer/lower) + `state` (lower). The loader
synthesizes and seeds it.
"""

from agent_composer.nodes.start.node import StartNode

__all__ = ["StartNode"]
