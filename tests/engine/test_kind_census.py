"""Kind-dispatch census — a ratchet that guards the kind-agnostic refactor.

The engine is being migrated so that its core (`runtime/engine.py` + `runtime/eval_node.py`)
knows only an abstract node contract and a closed `Outcome` sum — it must NOT branch on a node's
`NodeKind` nor on a concrete `*Expansion` type. This test *counts* the remaining kind-dispatch
sites and asserts the count never rises above a recorded ceiling.

Each refactor phase lowers `BASELINE`; the final phase drives it to `0`. The count is derived
from the source (self-calibrating) rather than a hand-maintained line list, so it survives
line-number drift — only the ceiling constant moves.

A "kind-dispatch site" is a distinct source line that does any of:
  - reference a `NodeKind` member  (`NodeKind.AGENT`, `NodeKind.CASE`, ...)   -> branches on kind
  - reference `_SPAWNER_KINDS`      (the spawner-kind set used for membership) -> branches on kind
  - call `isinstance(x, <...>Expansion)`                                       -> branches on concrete type

Import lines are excluded (importing `NodeKind` / `_SPAWNER_KINDS` is not dispatch), and a
diagnostic read like `node.kind.value` (for an error message) is not counted — it touches the
instance attribute, not a `NodeKind` member or an `*Expansion` type.

Known blind spots (do NOT route dispatch through these to dodge the ratchet — none exist in the
core today, and reintroducing kind dispatch in one of these shapes defeats the whole refactor):
indirect enum access (`getattr(NodeKind, name)`, `NodeKind[name]`, `NodeKind(value)`), a
`match node.kind` / `case NodeKind.X` block, kind-keyed dispatch dicts built off-file, and
type-name string compares (`type(desc).__name__ == "CallExpansion"`). The `*Expansion` detection
is a name-suffix heuristic tied to the codebase convention that every expansion type ends in
`Expansion`; keep that convention so the census stays accurate.
"""

import ast
from pathlib import Path

from agent_composer.runtime import engine as engine_mod
from agent_composer.runtime import eval_node as eval_node_mod

# The two modules that make up the engine core the census guards.
CORE_MODULES = {
    "runtime/engine.py": engine_mod,
    "runtime/eval_node.py": eval_node_mod,
}

# Ratchet ceiling: the number of kind-dispatch lines allowed in the engine core.
# LOWER this as each refactor phase removes dispatch; the final phase drives it to 0.
# (Measured at P0 baseline 20; dropped to 19 when the `eval_node` grow guard moved from a
# `node.kind not in _SPAWNER_KINDS` membership test to the kind-blind `not node.is_spawner`.
# Rose to 21 during the CALL->Grow migration: the live CALL path now grows via the labelled
# `_grow_residual` CALL arm — a census-counted kind-shaped residual (its `NodeKind.CALL` check
# + the cloned spawner-eligible `_SPAWNER_KINDS` stamp) that COEXISTS with the still-live legacy
# `_apply_enqueue`/`_grow_call` replay path. Rose to 23 during the MAP->Grow migration: the live
# MAP path adds a `_grow_residual` MAP arm (`NodeKind.MAP`) + a `_grow_map_residual` with the same
# spawner-eligible `_SPAWNER_KINDS` stamp. Rose to 26 during the AGENT->Grow migration: the live
# AGENT path adds a `_grow_residual` AGENT arm (`NodeKind.AGENT`) + a `_grow_agent_residual` with
# the three-branch `isinstance(parent_desc, AgentExpansion)` ledger + the BOTH-id spawner-eligible
# `_SPAWNER_KINDS` stamp. The residuals + the legacy arms are all deleted in the final sub-phase,
# which drops the ceiling below 20.)
BASELINE = 26


def _import_lines(tree: ast.Module) -> set[int]:
    """Line numbers spanned by `import` / `from ... import` statements (excluded from the census)."""
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            end = node.end_lineno or node.lineno
            lines.update(range(node.lineno, end + 1))
    return lines


def _is_expansion_isinstance(node: ast.AST) -> bool:
    """True for `isinstance(x, <Name ending in 'Expansion'>)` — dispatch on a concrete expansion type."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "isinstance"):
        return False
    if len(node.args) < 2:
        return False
    cls = node.args[1]
    names = cls.elts if isinstance(cls, ast.Tuple) else [cls]
    return any(isinstance(n, ast.Name) and n.id.endswith("Expansion") for n in names)


def _dispatch_lines(source: str) -> set[int]:
    """Distinct source line numbers carrying a kind-dispatch construct, imports excluded."""
    tree = ast.parse(source)
    skip = _import_lines(tree)
    hits: set[int] = set()
    for node in ast.walk(tree):
        # NodeKind.<member> — an attribute access on the NodeKind enum.
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "NodeKind":
            hits.add(node.lineno)
        # _SPAWNER_KINDS — the spawner-kind set, used only for kind membership tests.
        elif isinstance(node, ast.Name) and node.id == "_SPAWNER_KINDS":
            hits.add(node.lineno)
        # isinstance(x, *Expansion) — dispatch on a concrete expansion type.
        elif _is_expansion_isinstance(node):
            hits.add(node.lineno)
    return hits - skip


def _census() -> dict[str, set[int]]:
    """Map each core module's relative path -> the set of its kind-dispatch line numbers."""
    return {
        rel: _dispatch_lines(Path(mod.__file__).read_text())
        for rel, mod in CORE_MODULES.items()
    }


def test_kind_dispatch_stays_at_or_below_baseline():
    census = _census()
    total = sum(len(lines) for lines in census.values())
    breakdown = {rel: sorted(lines) for rel, lines in census.items()}
    assert total <= BASELINE, (
        f"kind-dispatch census rose to {total} (ceiling {BASELINE}). "
        f"The engine core must not gain kind dispatch. Sites: {breakdown}"
    )


def test_baseline_is_tight():
    """The ceiling must track reality: if dispatch was removed, LOWER BASELINE (don't leave slack)."""
    total = sum(len(lines) for lines in _census().values())
    assert total == BASELINE, (
        f"kind-dispatch census is {total} but BASELINE is {BASELINE}. "
        f"A phase removed dispatch — lower BASELINE to {total} to keep the ratchet tight."
    )
