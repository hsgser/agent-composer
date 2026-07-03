"""`case` `then:/else: ${call}` inline-call branch targets.

A branch target may be an inline call (a fresh owned call) instead of a placed node id; it
desugars to a synth `__call_<n>` node and the then:/else: is rewritten to that synth id. Sound
because the case veto skip-floods the non-chosen synth branch. Accepted grammar: a single bare
whole-span `${call}` (a route target is one node id).
"""

import pytest

from agent_composer.compose import LoadError, load_flow, run_flow

# An in-file `take` def (the branch callable): echoes its stance via the `took` CODE fn.
_TAKE_DEF = """
defs:
  take:
    input:
      stance: str
    nodes:
      x:
        kind: code
        code: tests.engine._compose_codefns:took
        input:
          stance: ${input.stance}
        output: str
    output: ${x.output}
"""


def _flow():
    return f"""
id: cc
name: cc
input:
  score: float
{_TAKE_DEF}
nodes:
  gate:
    kind: case
    cases:
      - when: "${{input.score}} >= 0.5"
        then: ${{ take(stance="pro") }}
    else: ${{ take(stance="con") }}
output: ${{gate.output}}
"""


def test_then_else_call_desugars_to_synth_branch_nodes():
    loaded = load_flow(_flow())
    # both then: and else: inline calls became synth call nodes
    synth = [nid for nid in loaded.compiled.nodes if nid.startswith("__call_")]
    assert len(synth) == 2
    # the gate's control edges target the synth ids (not a placed user node)
    control_targets = {e.to for e in loaded.compiled.edges if e.source_handle is not None and e.from_ == "gate"}
    assert control_targets == set(synth)


def test_then_call_branch_runs_taken_value():
    out = run_flow(load_flow(_flow()), {"score": 0.9})
    assert out.status == "succeeded"
    assert out.output == "took:pro"


def test_else_call_branch_runs_taken_value():
    out = run_flow(load_flow(_flow()), {"score": 0.2})
    assert out.status == "succeeded"
    assert out.output == "took:con"


def test_non_bare_then_call_is_rejected():
    # a route target must resolve to ONE node id: a coalesce of calls is loud.
    text = f"""
id: bad
name: bad
input:
  score: float
{_TAKE_DEF}
nodes:
  gate:
    kind: case
    cases:
      - when: "${{input.score}} >= 0.5"
        then: ${{ take(stance="a") | take(stance="b") }}
    else: nope
output: ${{gate.output}}
"""
    with pytest.raises(LoadError):
        load_flow(text)


def test_inline_binding_and_then_call_share_minter_no_collision():
    # an inline-binding call AND a then: ${call} in one flow must get DISTINCT synth ids.
    text = f"""
id: both
name: both
input:
  score: float
  topic: str
defs:
  take:
    input:
      stance: str
    nodes:
      x:
        kind: code
        code: tests.engine._compose_codefns:took
        input:
          stance: ${{input.stance}}
        output: str
    output: ${{x.output}}
  enrich:
    input:
      topic: str
    nodes:
      y:
        kind: code
        code: tests.engine._compose_codefns:echo
        input:
          topic: ${{input.topic}}
        output: str
    output: ${{y.output}}
nodes:
  use:
    kind: code
    code: tests.engine._compose_codefns:echo
    input:
      topic: ${{ enrich(topic=${{input.topic}}) }}
    output: str
  gate:
    kind: case
    cases:
      - when: "${{input.score}} >= 0.5"
        then: ${{ take(stance="pro") }}
    else: use
output: ${{gate.output}}
"""
    loaded = load_flow(text)
    synth = [nid for nid in loaded.compiled.nodes if nid.startswith("__call_")]
    assert len(synth) == len(set(synth)) == 2  # distinct ids, no collision


# --------------------------------------------------------------------------- #
# The `call(...)` directive spelling of a then:/else: branch target — the
# compile-time form that coexists with the legacy `${ take(...) }` span above.
# Mirrors the legacy tests one-for-one but with `call(take, stance="…")`.
# --------------------------------------------------------------------------- #


def _flow_directive():
    return f"""
id: cc
name: cc
input:
  score: float
{_TAKE_DEF}
nodes:
  gate:
    kind: case
    cases:
      - when: "${{input.score}} >= 0.5"
        then: call(take, stance="pro")
    else: call(take, stance="con")
output: ${{gate.output}}
"""


def test_then_else_call_directive_desugars_to_synth_branch_nodes():
    loaded = load_flow(_flow_directive())
    # both then: and else: call(...) directives became synth call nodes
    synth = [nid for nid in loaded.compiled.nodes if nid.startswith("__call_")]
    assert len(synth) == 2
    # the gate's control edges target the synth ids (not a placed user node)
    control_targets = {
        e.to for e in loaded.compiled.edges if e.source_handle is not None and e.from_ == "gate"
    }
    assert control_targets == set(synth)


def test_then_call_directive_branch_runs_taken_value():
    out = run_flow(load_flow(_flow_directive()), {"score": 0.9})
    assert out.status == "succeeded"
    assert out.output == "took:pro"


def test_else_call_directive_branch_runs_taken_value():
    out = run_flow(load_flow(_flow_directive()), {"score": 0.2})
    assert out.status == "succeeded"
    assert out.output == "took:con"


def test_non_bare_then_call_directive_is_rejected():
    # a route target must resolve to ONE node id: a coalesce of call(...) directives is loud.
    text = f"""
id: bad
name: bad
input:
  score: float
{_TAKE_DEF}
nodes:
  gate:
    kind: case
    cases:
      - when: "${{input.score}} >= 0.5"
        then: call(take, stance="a") | call(take, stance="b")
    else: nope
output: ${{gate.output}}
"""
    with pytest.raises(LoadError):
        load_flow(text)


def test_inline_binding_and_then_call_directive_share_minter_no_collision():
    # an inline-binding call(...) AND a then: call(...) in one flow -> DISTINCT synth ids.
    text = f"""
id: both
name: both
input:
  score: float
  topic: str
defs:
  take:
    input:
      stance: str
    nodes:
      x:
        kind: code
        code: tests.engine._compose_codefns:took
        input:
          stance: ${{input.stance}}
        output: str
    output: ${{x.output}}
  enrich:
    input:
      topic: str
    nodes:
      y:
        kind: code
        code: tests.engine._compose_codefns:echo
        input:
          topic: ${{input.topic}}
        output: str
    output: ${{y.output}}
nodes:
  use:
    kind: code
    code: tests.engine._compose_codefns:echo
    input:
      topic: call(enrich, topic=${{input.topic}})
    output: str
  gate:
    kind: case
    cases:
      - when: "${{input.score}} >= 0.5"
        then: call(take, stance="pro")
    else: use
output: ${{gate.output}}
"""
    loaded = load_flow(text)
    synth = [nid for nid in loaded.compiled.nodes if nid.startswith("__call_")]
    assert len(synth) == len(set(synth)) == 2  # distinct ids, no collision


def test_case_output_over_call_directive_then_taken():
    # ${gate.output} over a call(...)-desugared then: — the taken then value comes back.
    out = run_flow(load_flow(_flow_directive()), {"score": 0.9})
    assert out.status == "succeeded"
    assert out.output == "took:pro"


def test_case_output_over_call_directive_else_taken():
    # ${gate.output} over a call(...)-desugared else: — the taken else value comes back.
    out = run_flow(load_flow(_flow_directive()), {"score": 0.2})
    assert out.status == "succeeded"
    assert out.output == "took:con"


def _flow_nested_directive():
    # a NESTED branch target: the then: call(...) has an inner call(...) as a kwarg, so the
    # single target lifts TWO synth nodes (inner-first) — exercises the multi-node lift loop.
    return f"""
id: cc
name: cc
input:
  score: float
{_TAKE_DEF}
nodes:
  gate:
    kind: case
    cases:
      - when: "${{input.score}} >= 0.5"
        then: call(take, stance=call(take, stance="pro"))
    else: call(take, stance="con")
output: ${{gate.output}}
"""


def test_nested_then_call_directive_lifts_two_synth_nodes():
    # the nested then: target lifts BOTH the inner and outer call as synth nodes; the else:
    # lifts one more — three synth call nodes in all, the gate's then-edge on the outer.
    loaded = load_flow(_flow_nested_directive())
    synth = [nid for nid in loaded.compiled.nodes if nid.startswith("__call_")]
    assert len(synth) == len(set(synth)) == 3


def test_nested_then_call_directive_branch_runs_taken_value():
    # inner take(stance="pro") -> "took:pro"; outer take(stance="took:pro") -> "took:took:pro".
    out = run_flow(load_flow(_flow_nested_directive()), {"score": 0.9})
    assert out.status == "succeeded"
    assert out.output == "took:took:pro"
