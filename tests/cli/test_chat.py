"""Tests for the `ac chat` CLI — exercised through typer's `CliRunner`.

The turn-loop / suspend-resume behaviour is covered by the scripted end-to-end test;
this module only asserts the subcommand is wired and its help renders its options.
"""

from __future__ import annotations

import sys
from pathlib import Path

from typer.testing import CliRunner

import agent_composer.llm_clients as llm_clients_mod
from agent_composer.cli import app

runner = CliRunner()


class _FakeModel:
    """A stand-in chat model: plain agent mode calls `model.invoke(msgs).content`."""

    def bind_tools(self, tools):
        return self

    def invoke(self, msgs):
        class R:
            content = "hello back"

        return R()



def test_chat_help():
    r = runner.invoke(app, ["chat", "--help"])
    assert r.exit_code == 0
    assert "--workspace" in r.output


def test_chat_two_turns(tmp_path, monkeypatch):
    # The AGENT node resolves its model via `agent_composer.llm_clients.model_from_config`
    # (the engine's `_default_llm` re-imports it lazily) — patch it to the fake.
    monkeypatch.setattr(llm_clients_mod, "model_from_config", lambda cfg: _FakeModel())
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


def test_chat_resolves_cwd_local_fold_module(tmp_path, monkeypatch):
    """`ac chat` must put the cwd on `sys.path` so a chat flow's `code: module:fn` fold
    ref resolves without the caller exporting PYTHONPATH — mirroring `ac run`.

    Reproduces the integration gap the fold-import fix closes: a flow whose CODE fold lives
    in a cwd-local module (not the installed package, not the repo root pytest already put on
    the path). Without `_ensure_cwd_importable()`, the run fails to import the fold, the turn
    loop never yields a reply, and the user sees only "session ended".
    """
    monkeypatch.setattr(llm_clients_mod, "model_from_config", lambda cfg: _FakeModel())
    monkeypatch.setenv("OPENAI_API_KEY", "x")

    # A cwd-local fold module + a chat flow that references it by bare module name.
    (tmp_path / "localfold.py").write_text(
        "def fold_turn(inputs):\n"
        "    grown = inputs['transcript'] + '\\n' + inputs['message'] + '\\n' + inputs['reply']\n"
        "    return {'transcript': grown, 'exited': bool(inputs['exited'])}\n"
    )
    (tmp_path / "mychat.yaml").write_text(
        (Path("examples/chat.yaml").read_text()).replace(
            "examples.chat_fns:fold_turn", "localfold:fold_turn"
        )
    )

    # Run from tmp_path; it is NOT on sys.path until the command adds it.
    monkeypatch.chdir(tmp_path)
    assert str(tmp_path) not in sys.path
    try:
        r = runner.invoke(app, ["chat", "mychat.yaml", "--provider", "openai"], input="hi\n")
        assert r.exit_code == 0
        assert "hello back" in r.output  # the fold imported and the turn produced a reply
    finally:
        # The command prepends cwd to sys.path (idempotent); undo so it doesn't leak.
        if str(tmp_path) in sys.path:
            sys.path.remove(str(tmp_path))
        sys.modules.pop("localfold", None)

