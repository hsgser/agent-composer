"""Tests for the `ac chat` CLI — exercised through typer's `CliRunner`.

The turn-loop / suspend-resume behaviour is covered by the scripted end-to-end test;
this module only asserts the subcommand is wired and its help renders its options.
"""

from __future__ import annotations

from typer.testing import CliRunner

from agent_composer.cli import app

runner = CliRunner()


def test_chat_help():
    r = runner.invoke(app, ["chat", "--help"])
    assert r.exit_code == 0
    assert "--workspace" in r.output
