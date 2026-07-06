"""Tests for the `ac chat` CLI — exercised through typer's `CliRunner`.

The turn-loop / suspend-resume behaviour is covered by the scripted end-to-end test;
this module only asserts the subcommand is wired and its help renders its options.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

import agent_composer.llm_clients as llm_clients_mod
from agent_composer.cli import app

runner = CliRunner()


def test_chat_help():
    r = runner.invoke(app, ["chat", "--help"])
    assert r.exit_code == 0
    assert "--workspace" in r.output


def test_chat_two_turns(tmp_path, monkeypatch):
    # A fake chat model: the plain agent mode calls `model.invoke(msgs).content`.
    class _M:
        def bind_tools(self, t):
            return self

        def invoke(self, msgs):
            class R:
                content = "hello back"

            return R()

    # The AGENT node resolves its model via `agent_composer.llm_clients.model_from_config`
    # (the engine's `_default_llm` re-imports it lazily) — patch it to the fake.
    monkeypatch.setattr(llm_clients_mod, "model_from_config", lambda cfg: _M())
    # `--provider openai` makes `_ensure_provider_keys` pick openai for the flow's agent
    # (which sets no provider); satisfy its key check with a dummy env var (no product change).
    monkeypatch.setenv("OPENAI_API_KEY", "x")

    # Use the plain example flow (mode: plain) so no tools/keys are needed for the drive test.
    # Its code fold ref is `examples.chat_fns:fold_turn`; run from repo root so it imports.
    flow = Path("examples/chat.yaml")
    # two user turns then EOF (CliRunner closes stdin -> loop breaks)
    r = runner.invoke(app, ["chat", str(flow), "--provider", "openai"], input="hi\nagain\n")
    assert r.exit_code == 0
    # The assistant reply is rendered as Markdown each turn; two turns -> it appears.
    assert "hello back" in r.output
    assert "session ended" in r.output
