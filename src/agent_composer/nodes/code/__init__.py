"""CODE node — run deterministic Python, either a `module:function` reference or inline source.

- `node.py` — the `CodeNode` + `classify_code_source` (shape-based reference / inline /
  reject classification). Both modes run **in-process**: reference imports and calls the
  function; inline wraps the author's bare body as `def main(inputs):`, compiles it, and
  calls `main(inputs)`. A killable subprocess runtime is a deferred later phase (see the
  design docs), so there is no `runner.py`/`_wrapper.py` here.
"""

from agent_composer.nodes.code.node import CodeNode, classify_code_source

__all__ = ["CodeNode", "classify_code_source"]
