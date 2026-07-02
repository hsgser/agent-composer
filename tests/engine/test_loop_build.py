"""build_loop_node + loader dispatch + the `'a -> 'a` shape contract.

A `kind: loop` node bakes a `LoopNode` from a `LoopDescriptor`: it resolves+bakes the
body subflow (the `call:` child, a `defs:` callable here so no external resolver is
needed), stamps `output_shape`, and enforces the two halves of the shape contract:

- FIELD-NAME-set (in `build_loop_node`): body output field NAMES == carried keys,
  body input NAMES subset of carried keys — a located `LoadError` on a name mismatch.
- FULL TYPE equality (in the loader post-build pass `check_loop_shape_contract`):
  body output Shape == carried record Shape per field — a located `LoadError` on a
  same-names/different-TYPES mismatch (needs the assembled producers/flow-input shapes).
"""

import pytest

from agent_composer.compose.loader import load_flow
from agent_composer.compose.errors import LoadError
from agent_composer.nodes.base import NodeKind


# A good loop: the body `bump` reads a subset of the carried record and returns the
# WHOLE next carried record ({n, exited}); `chat_loop` carries {n, exited}, loops
# while `not ${exited}`, capped at max: 5.
GOOD = """
id: loop-good
name: loop_good
defs:
  bump:
    input:
      n: int
      exited: bool
    nodes:
      step:
        kind: code
        code: tests.engine._compose_codefns:make_report
        input:
          n: ${input.n}
          exited: ${input.exited}
        output:
          n: int
          exited: bool
    output: ${step.output}
nodes:
  chat_loop:
    kind: loop
    call: bump
    input:
      n: 0
      exited: false
    while: not ${exited}
    max: 5
output: ${chat_loop.output}
"""


# The body output OMITS `exited`, so its output field NAMES ({n}) != the carried keys
# ({n, exited}) -> caught in build_loop_node (the field-name-set half).
BAD_FIELDSET = """
id: loop-bad-fieldset
name: loop_bad_fieldset
defs:
  bump:
    input:
      n: int
      exited: bool
    nodes:
      step:
        kind: code
        code: tests.engine._compose_codefns:make_report
        input:
          n: ${input.n}
          exited: ${input.exited}
        output:
          n: int
    output: ${step.output}
nodes:
  chat_loop:
    kind: loop
    call: bump
    input:
      n: 0
      exited: false
    while: not ${exited}
    max: 5
output: ${chat_loop.output}
"""


# Same field NAMES ({n, exited}) but the body output declares `n: str` while the
# carried `n` is seeded from `${input.n0}` (an int flow input) -> a same-names/
# different-TYPES mismatch, caught in the loader post-build pass
# check_loop_shape_contract. (The carried field must be seeded from a ref with a
# resolvable Shape — a bare literal stays lenient/opaque in the type pass.)
BAD_TYPES = """
id: loop-bad-types
name: loop_bad_types
input:
  n0: int
defs:
  bump:
    input:
      n: int
      exited: bool
    nodes:
      step:
        kind: code
        code: tests.engine._compose_codefns:make_report
        input:
          n: ${input.n}
          exited: ${input.exited}
        output:
          n: str
          exited: bool
    output: ${step.output}
nodes:
  chat_loop:
    kind: loop
    call: bump
    input:
      n: ${input.n0}
      exited: false
    while: not ${exited}
    max: 5
output: ${chat_loop.output}
"""


def test_good_loop_bakes_loopnode():
    flow = load_flow(GOOD)
    node = flow.compiled.nodes["chat_loop"]
    assert node.kind == NodeKind.LOOP
    assert node.child is not None


def test_body_output_fieldset_must_equal_carried():
    with pytest.raises(LoadError) as e:
        load_flow(BAD_FIELDSET)
    assert "output" in str(e.value).lower()
    assert e.value.line is not None


def test_body_output_types_must_equal_carried():
    with pytest.raises(LoadError) as e:
        load_flow(BAD_TYPES)
    assert e.value.line is not None
