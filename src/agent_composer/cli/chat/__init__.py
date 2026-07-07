"""`ac chat` — the interactive REPL, dogfooded as an ordinary Agent Composer flow.

A CLI-host concern, not engine core. This package bundles the three pieces of the
chat surface: the turn-taking flow (`chat.yaml`) with its deterministic transcript
fold (`fns.py`), the workspace-confined flow-op tools the composer assistant calls
(`tools.py`), and the Typer subcommand that drives the per-turn suspend/resume
(`command.py`). The engine sees only a LOOP-per-turn flow; nothing here changes core.
"""
