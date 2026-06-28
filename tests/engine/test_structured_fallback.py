"""Structured output prompt-injection fallback when a provider lacks native support.

Driven through the REAL capability source (`supports_native_structured`) on the catalog
sentinel pair, proving the fallback path is reachable on a concrete provider/model — not via
a monkeypatch of the gate.
"""

from agent_composer.nodes.agent.structured import generate_structured
from agent_composer.state.segments import Shape, SegmentType


def test_no_native_support_uses_prompt_injection():
    shape = Shape.scalar(SegmentType.INTEGER)

    class _NoNative:
        def invoke(self, msgs):
            text = msgs[-1].content
            assert "schema" in text.lower() or "json" in text.lower()

            class R:
                content = '{"value": 5}'

            return R()

        def with_structured_output(self, schema):
            raise AssertionError("no-native model must not use with_structured_output")

    cfg = {"provider": "vllm", "model": "no-structured-sentinel"}
    assert generate_structured(_NoNative(), [], shape, llm_config=cfg) == 5


def test_fallback_retries_on_unparseable_then_succeeds():
    shape = Shape.scalar(SegmentType.INTEGER)
    calls = {"n": 0}

    class _Flaky:
        def invoke(self, msgs):
            calls["n"] += 1

            class R:
                content = "not json" if calls["n"] == 1 else '{"value": 7}'

            return R()

    cfg = {"provider": "vllm", "model": "no-structured-sentinel"}
    assert generate_structured(_Flaky(), [], shape, max_retries=2, llm_config=cfg) == 7
    assert calls["n"] == 2
