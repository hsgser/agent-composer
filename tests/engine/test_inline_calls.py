"""Inline `${ f(arg=...) }` call expressions.

An inline call is applicative expression syntax that DESUGARS at load into an
anonymous `call` node, with the host binding rewritten to `${<synth>.output}` —
pure sugar, no new runtime kind. Two layers:

- `expr.desugar_calls` (pure string→string + plain-data) — exercised here in the
  STEP 1 block (whole-string / embedded / coalesce / nested / arg-value kinds /
  `$$` / non-call / unbalanced).
- `compose.calls.desugar_inline_calls` + the loader wiring — exercised end-to-end
  (load + run, edge inference, defs callee, flow output) in the STEP 2 block.
- the `${item}`-capture + synth-id-collision guards — the STEP 3 block.
"""

import pytest

from agent_composer.expr import ExpressionError, InlineCall, desugar_calls


# --------------------------------------------------------------------------- #
# STEP 1 — `desugar_calls` (pure): no loader, deterministic ids.
# --------------------------------------------------------------------------- #


def _ids():
    """A deterministic synth-id minter for the pure tests (`__call_0`, `__call_1`, …)."""
    counter = iter(range(1000))
    return lambda: f"__call_{next(counter)}"


def test_whole_string_call():
    s, calls = desugar_calls("${ f(x=1) }", _ids())
    assert s == "${__call_0.output}"  # a clean whole-string ref -> typed value
    assert calls == [InlineCall(id="__call_0", callee="f", args={"x": 1})]


def test_embedded_call_is_stringified_host():
    s, calls = desugar_calls("pe=${ relevance(t=${input.x}) }", _ids())
    assert s == "pe=${__call_0.output}"  # embedded -> host stringifies at runtime
    assert calls == [InlineCall(id="__call_0", callee="relevance", args={"t": "${input.x}"})]


def test_coalesce_of_calls():
    s, calls = desugar_calls("${ a(x=1) | b(y=2) }", _ids())
    assert s == "${__call_0.output|__call_1.output}"
    assert calls == [
        InlineCall(id="__call_0", callee="a", args={"x": 1}),
        InlineCall(id="__call_1", callee="b", args={"y": 2}),
    ]


def test_nested_call_inner_first():
    s, calls = desugar_calls("${ outer(x=${inner(y=1)}) }", _ids())
    assert s == "${__call_1.output}"  # the host binds the OUTER call result
    # inner is minted first (inner-first), the outer references it by synth ref.
    assert calls == [
        InlineCall(id="__call_0", callee="inner", args={"y": 1}),
        InlineCall(id="__call_1", callee="outer", args={"x": "${__call_0.output}"}),
    ]


def test_multi_arg_typed_ref_coalesce_values():
    s, calls = desugar_calls(
        '${ f(n=30, s="hi", z=null, b=true, r=${y.output}, co=${a | b}) }', _ids()
    )
    assert s == "${__call_0.output}"
    assert calls == [
        InlineCall(
            id="__call_0",
            callee="f",
            # bare literals (int/None/bool); a quoted scalar -> unquoted; refs/coalesce
            # stay binding strings.
            args={"n": 30, "s": "hi", "z": None, "b": True, "r": "${y.output}", "co": "${a | b}"},
        )
    ]


def test_callee_with_hyphen_is_a_flow_id():
    _, calls = desugar_calls("${ research-one(topic=${input.t}) }", _ids())
    assert calls[0].callee == "research-one"


def test_dollar_escape_untouched():
    s, calls = desugar_calls("cost $$5 then ${ f(x=1) }", _ids())
    assert s == "cost $$5 then ${__call_0.output}"  # `$$` is not a span start
    assert len(calls) == 1


def test_non_call_refs_untouched():
    # a plain ref / coalesce with no call round-trips with no synth calls.
    assert desugar_calls("${input.x}", _ids()) == ("${input.x}", [])
    assert desugar_calls("${a.output | b.output}", _ids()) == (
        "${a.output | b.output}",
        [],
    )
    assert desugar_calls("plain text, no spans", _ids()) == ("plain text, no spans", [])


def test_default_rhs_call_not_desugared():
    # the desugar handles calls in COALESCE-operand position, not a `:-` default RHS
    # (deferred to the named form): `${ x :- f() }` is left for the binding grammar.
    s, calls = desugar_calls("${ x:-foo }", _ids())
    assert calls == []
    assert s == "${ x:-foo }"


def test_empty_args_call():
    s, calls = desugar_calls("${ now() }", _ids())
    assert s == "${__call_0.output}"
    assert calls == [InlineCall(id="__call_0", callee="now", args={})]


def test_unbalanced_parens_fall_through():
    # an unbalanced call paren is NOT a call — left verbatim for the downstream parser.
    s, calls = desugar_calls("${ f(x=1 }", _ids())
    assert calls == []
    assert s == "${ f(x=1 }"


def test_positional_arg_is_loud():
    with pytest.raises(ExpressionError, match="keyword"):
        desugar_calls("${ f(30) }", _ids())


def test_call_in_arg_coalesce_position():
    # a call as a coalesce operand INSIDE an arg value desugars too (inner-first).
    s, calls = desugar_calls("${ f(note=${a | g(x=1)}) }", _ids())
    assert s == "${__call_1.output}"
    assert calls == [
        InlineCall(id="__call_0", callee="g", args={"x": 1}),
        # operand whitespace is preserved on rejoin (harmless — parse_binding strips it).
        InlineCall(id="__call_1", callee="f", args={"note": "${a |__call_0.output}"}),
    ]


# --------------------------------------------------------------------------- #
# STEP 2 — `desugar_inline_calls` + the loader wiring (end-to-end load + run).
# CODE-only children (Ollama-free), resolver-free via in-file `defs:`.
# --------------------------------------------------------------------------- #

from agent_composer.compose import LoadError, load_flow, run_flow  # noqa: E402

# An in-file `enrich` def that echoes its `topic` — the callee for the inline calls.
_ENRICH_DEF = """
defs:
  enrich:
    input:
      topic: str
    nodes:
      x:
        kind: code
        code: tests.engine._compose_codefns:echo
        input:
          topic: ${input.topic}
        output: str
    output: ${x.output}
"""


def test_inline_call_in_node_input_runs():
    text = f"""
id: ic1
name: ic1
input:
  topic: str
{_ENRICH_DEF}
nodes:
  use:
    kind: code
    code: tests.engine._compose_codefns:echo
    input:
      topic: call(enrich, topic=${{input.topic}})
    output: str
output: ${{use.output}}
"""
    loaded = load_flow(text)
    assert "__call_0" in loaded.compiled.nodes  # the inline call became a synth node
    result = run_flow(loaded, {"topic": "ACME"})
    assert result.status == "succeeded"
    assert result.output == "ACME"


def test_inline_call_literal_arg_runs():
    # a bare literal arg (string) flows like the named form's `input: {topic: HARD}`.
    text = f"""
id: ic_lit
name: ic_lit
input:
  topic: str
{_ENRICH_DEF}
nodes:
  use:
    kind: code
    code: tests.engine._compose_codefns:echo
    input:
      topic: call(enrich, topic="HARDCODED")
    output: str
output: ${{use.output}}
"""
    result = run_flow(load_flow(text), {"topic": "ACME"})
    assert result.output == "HARDCODED"


def test_inline_call_fan_in_infers_edges():
    text = f"""
id: ic_fanin
name: ic_fanin
input:
  topic: str
{_ENRICH_DEF}
nodes:
  y:
    kind: code
    code: tests.engine._compose_codefns:echo
    input:
      topic: ${{input.topic}}
    output: str
  use:
    kind: code
    code: tests.engine._compose_codefns:echo
    input:
      topic: call(enrich, topic=${{y.output}})
    output: str
output: ${{use.output}}
"""
    loaded = load_flow(text)
    edges = {(e.from_, e.to) for e in loaded.compiled.edges}
    assert ("y", "__call_0") in edges          # y -> synth call (the fan-in)
    assert ("__call_0", "use") in edges        # synth call -> consumer
    result = run_flow(loaded, {"topic": "ACME"})
    assert result.status == "succeeded"
    assert result.output == "ACME"


def test_nested_inline_calls_run():
    text = """
id: ic_nested
name: ic_nested
input:
  topic: str
defs:
  inner:
    input:
      topic: str
    nodes:
      x:
        kind: code
        code: tests.engine._compose_codefns:echo
        input:
          topic: ${input.topic}
        output: str
    output: ${x.output}
  outer:
    input:
      topic: str
    nodes:
      x:
        kind: code
        code: tests.engine._compose_codefns:echo
        input:
          topic: ${input.topic}
        output: str
    output: ${x.output}
nodes:
  use:
    kind: code
    code: tests.engine._compose_codefns:echo
    input:
      topic: call(outer, topic=call(inner, topic=${input.topic}))
    output: str
output: ${use.output}
"""
    loaded = load_flow(text)
    assert "__call_0" in loaded.compiled.nodes  # inner (minted first)
    assert "__call_1" in loaded.compiled.nodes  # outer
    result = run_flow(loaded, {"topic": "ACME"})
    assert result.status == "succeeded"
    assert result.output == "ACME"


def test_inline_call_in_flow_output_runs():
    text = f"""
id: ic_out
name: ic_out
input:
  topic: str
{_ENRICH_DEF}
nodes:
  seed:
    kind: code
    code: tests.engine._compose_codefns:echo
    input:
      topic: ${{input.topic}}
    output: str
output: call(enrich, topic=${{seed.output}})
"""
    loaded = load_flow(text)
    assert "__call_0" in loaded.compiled.nodes
    result = run_flow(loaded, {"topic": "ACME"})
    assert result.status == "succeeded"
    assert result.output == "ACME"


def test_inline_call_resolves_through_external_resolver():
    external = """
id: ext_flow
name: ext_flow
input:
  topic: str
nodes:
  x:
    kind: code
    code: tests.engine._compose_codefns:echo
    input:
      topic: ${input.topic}
    output: str
output: ${x.output}
"""
    text = """
id: ic_ext
name: ic_ext
input:
  topic: str
uses:
  ext_flow: ext_flow
nodes:
  use:
    kind: code
    code: tests.engine._compose_codefns:echo
    input:
      topic: call(ext_flow, topic=${input.topic})
    output: str
output: ${use.output}
"""

    def resolver(flow_id, version=None):
        assert flow_id == "ext_flow"
        return load_flow(external)

    loaded = load_flow(text, child_resolver=resolver)
    result = run_flow(loaded, {"topic": "ACME"})
    assert result.status == "succeeded"
    assert result.output == "ACME"


# --------------------------------------------------------------------------- #
# STEP 3 — guards: `${item}` capture + reserved synth-id prefix + malformed call.
# --------------------------------------------------------------------------- #


def test_inline_call_capturing_item_is_loud():
    # an inline call lifts to a top-level synth node with no map-element scope, so an
    # arg reading ${item} is rejected — use a named `call` node instead.
    text = """
id: ic_item
name: ic_item
input:
  topics: list[str]
defs:
  one:
    input:
      topic: str
    nodes:
      x:
        kind: code
        code: tests.engine._compose_codefns:echo
        input:
          topic: ${input.topic}
        output: str
    output: ${x.output}
  wrap:
    input:
      t: str
    nodes:
      x:
        kind: code
        code: tests.engine._compose_codefns:echo
        input:
          topic: ${input.t}
        output: str
    output: ${x.output}
nodes:
  each:
    kind: map
    call: one
    over: ${input.topics}
    input:
      topic: call(wrap, t=${item})
output: ${each.output}
"""
    with pytest.raises(LoadError) as exc:
        load_flow(text)
    msg = str(exc.value)
    assert "item" in msg
    assert "named `call`" in msg


def test_user_node_with_synth_prefix_is_loud():
    text = """
id: ic_collide
name: ic_collide
input:
  topic: str
nodes:
  __call_0:
    kind: code
    code: tests.engine._compose_codefns:echo
    input:
      topic: ${input.topic}
    output: str
output: ${__call_0.output}
"""
    with pytest.raises(LoadError) as exc:
        load_flow(text)
    msg = str(exc.value)
    assert "__call_" in msg
    assert "reserved" in msg.lower()


def test_inline_call_positional_arg_is_loud_and_located():
    text = f"""
id: ic_pos
name: ic_pos
input:
  topic: str
{_ENRICH_DEF}
nodes:
  use:
    kind: code
    code: tests.engine._compose_codefns:echo
    input:
      topic: call(enrich, 30)
    output: str
output: ${{use.output}}
"""
    with pytest.raises(LoadError) as exc:
        load_flow(text)
    assert "keyword" in str(exc.value)
    assert exc.value.line is not None  # located at the host node


def test_inline_call_seed_21_loads():
    from pathlib import Path

    seeds = Path(__file__).resolve().parents[2] / "tests" / "seeds"
    loaded = load_flow((seeds / "21-inline-call.yaml").read_text())
    # the inline call in `summary`'s input desugared into a synth call node.
    assert any(nid.startswith("__call_") for nid in loaded.compiled.nodes)


# --------------------------------------------------------------------------- #
# STEP 4 — review hardening (adversarial-review follow-ups): named-form parity,
# located outputs errors, and the in-scope binding positions / paths that were
# implemented but untested (TOOL args:, mapped-call over:, defs body, multi-site
# ids, unknown callee, e06 over a synth binding).
# --------------------------------------------------------------------------- #

from agent_composer.compose import desugar_inline_calls  # noqa: E402
from agent_composer.compose.parser import ToolDescriptor  # noqa: E402


def test_quoted_interpolated_arg_strips_quotes_like_named_form():
    # a quoted arg with interpolation `"hi ${name}"` unwraps to the template `hi ${name}`
    # (quotes stripped), matching the named form's YAML quoted scalar — no literal quotes.
    text = f"""
id: ic_qi
name: ic_qi
input:
  name: str
{_ENRICH_DEF}
nodes:
  use:
    kind: code
    code: tests.engine._compose_codefns:echo
    input:
      topic: call(enrich, topic="hi ${{input.name}}")
    output: str
output: ${{use.output}}
"""
    result = run_flow(load_flow(text), {"name": "ACME"})
    assert result.status == "succeeded"
    assert result.output == "hi ACME"  # NOT '"hi ACME"' — no spurious quote chars


def test_bare_bool_word_arg_is_a_string_not_yaml_coerced():
    # deliberate: inline bare literals are a YAML-1.1 SUBSET — `yes`/`on`/`off`/`no` stay
    # strings (no boolean coercion), avoiding the YAML bool footgun. (A named call's YAML
    # `inputs:` map WOULD coerce `yes` -> True; the inline form intentionally does not.)
    _, calls = desugar_calls("${ f(flag=yes) }", _ids())
    assert calls[0].args == {"flag": "yes"}


def test_malformed_inline_call_in_outputs_is_located():
    text = f"""
id: ic_outloc
name: ic_outloc
input:
  topic: str
{_ENRICH_DEF}
nodes:
  seed:
    kind: code
    code: tests.engine._compose_codefns:echo
    input:
      topic: ${{input.topic}}
    output: str
output: call(enrich, 30)
"""
    with pytest.raises(LoadError) as exc:
        load_flow(text)
    assert "keyword" in str(exc.value)
    assert exc.value.line is not None  # located at the `outputs:` section line


def test_inline_call_in_tool_args_desugars():
    # the ToolDescriptor `args:` branch (distinct from `inputs:`) desugars too. Unit-level
    # (a real TOOL run needs a registered tool); asserts the synth node + rewrite.
    descriptors = {
        "t": ToolDescriptor(id="t", tool_id="some_tool", args={"q": "call(enrich, x=${input.k})"})
    }
    new_descriptors, _, _ = desugar_inline_calls(descriptors, "${t.output}")
    assert new_descriptors["t"].args["q"] == "${__call_0.output}"
    synth = new_descriptors["__call_0"]
    assert synth.call == "enrich"
    assert synth.inputs == {"x": "${input.k}"}
    assert synth.over is None


def test_inline_call_in_mapped_call_over_runs():
    text = """
id: ic_over
name: ic_over
input:
  topics: list[str]
defs:
  one:
    input:
      topic: str
    nodes:
      x:
        kind: code
        code: tests.engine._compose_codefns:echo
        input:
          topic: ${input.topic}
        output: str
    output: ${x.output}
  producelist:
    input:
      xs: list[str]
    nodes:
      x:
        kind: code
        code: tests.engine._compose_codefns:echo
        input:
          topic: ${input.xs}
        output: list[str]
    output: ${x.output}
nodes:
  each:
    kind: map
    call: one
    over: call(producelist, xs=${input.topics})
    input:
      topic: ${item}
output: ${each.output}
"""
    loaded = load_flow(text)
    edges = {(e.from_, e.to) for e in loaded.compiled.edges}
    assert ("__call_0", "each") in edges  # the synth-over producer -> the mapped call
    result = run_flow(loaded, {"topics": ["A", "B"]})
    assert result.status == "succeeded"
    assert result.output == ["A", "B"]


def test_two_host_sites_mint_unique_ids_and_both_run():
    text = f"""
id: ic_two
name: ic_two
input:
  topic: str
{_ENRICH_DEF}
nodes:
  a:
    kind: code
    code: tests.engine._compose_codefns:echo
    input:
      topic: call(enrich, topic=${{input.topic}})
    output: str
  b:
    kind: code
    code: tests.engine._compose_codefns:echo
    input:
      topic: call(enrich, topic=${{input.topic}})
    output: str
output:
  ra: ${{a.output}}
  rb: ${{b.output}}
"""
    loaded = load_flow(text)
    assert {"__call_0", "__call_1"} <= set(loaded.compiled.nodes)  # distinct, no collision
    result = run_flow(loaded, {"topic": "ACME"})
    assert result.status == "succeeded"
    assert result.output == {"ra": "ACME", "rb": "ACME"}


def test_inline_call_inside_defs_body_runs():
    # the desugar runs for def bodies too (shared _assemble); the def's synth ids live in
    # their own namespace. Here `enrich`'s own node hosts an inline call to `helper`.
    text = """
id: ic_defbody
name: ic_defbody
input:
  topic: str
defs:
  helper:
    input:
      topic: str
    nodes:
      x:
        kind: code
        code: tests.engine._compose_codefns:echo
        input:
          topic: ${input.topic}
        output: str
    output: ${x.output}
  enrich:
    input:
      topic: str
    nodes:
      y:
        kind: code
        code: tests.engine._compose_codefns:echo
        input:
          topic: call(helper, topic=${input.topic})
        output: str
    output: ${y.output}
nodes:
  go:
    kind: call
    call: enrich
    input:
      topic: ${input.topic}
output: ${go.output}
"""
    result = run_flow(load_flow(text), {"topic": "ACME"})
    assert result.status == "succeeded"
    assert result.output == "ACME"


def test_inline_call_unknown_callee_is_loud():
    text = """
id: ic_unk
name: ic_unk
input:
  topic: str
nodes:
  use:
    kind: code
    code: tests.engine._compose_codefns:echo
    input:
      topic: call(nope, x=${input.topic})
    output: str
output: ${use.output}
"""
    with pytest.raises(LoadError) as exc:
        load_flow(text)
    msg = str(exc.value)
    assert "nope" in msg
    assert "uses:" in msg


def test_inline_call_e06_type_mismatch_is_loud():
    # a synth call binding flows through the same cross-flow type check as a named call.
    text = """
id: ic_e06
name: ic_e06
input:
  topic: str
defs:
  enrich:
    input:
      topic: str
    nodes:
      x:
        kind: code
        code: tests.engine._compose_codefns:echo
        input:
          topic: ${input.topic}
        output: str
    output: ${x.output}
nodes:
  num:
    kind: code
    code: tests.engine._compose_codefns:echo
    input:
      topic: ${input.topic}
    output: int
  use:
    kind: code
    code: tests.engine._compose_codefns:echo
    input:
      topic: call(enrich, topic=${num.output})
    output: str
output: ${use.output}
"""
    with pytest.raises(LoadError) as exc:
        load_flow(text)
    msg = str(exc.value)
    assert "__call_0" in msg
    assert "child expects" in msg  # the cross-flow type-check message
