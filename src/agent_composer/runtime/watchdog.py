"""A kind-blind runtime watchdog: log a node whose `run()` overruns a soft time budget.

In-process execution cannot **kill** a runaway node — Python has no safe way to stop a
thread, and a `signal`/`alarm` only fires on the main thread while node bodies run on
workers. So a runaway inline `code:` body (or any wedged node) would otherwise hang
`ac run` **silently**. This watchdog cannot recover the worker either, but it turns the
silent hang into a **diagnosed** one: a background timer logs a warning naming the node
once it passes the budget. A real, *killable* runtime (a child process with resource
limits + a wall timeout) is a deferred design — see the code-node design docs.

It times **every** node (it never branches on `NodeKind`), so it lives in `runtime/` and
keeps the engine core kind-blind — the kind-census ratchet stays at 0. A legitimately
slow node (e.g. a long LLM call) will also be logged; the message is informational, not
an error, and never fails the run.
"""

import logging
import threading
from contextlib import contextmanager

logger = logging.getLogger("agent_composer.runtime.watchdog")

# Soft budget (seconds) after which a still-running node is logged, once. Hardcoded until
# the error/limits config seam lands; a per-node `limits:` surface is deferred.
DEFAULT_BUDGET_SECONDS = 30.0


@contextmanager
def node_watchdog(node_id: str, budget: float = DEFAULT_BUDGET_SECONDS):
    """Log a warning if the wrapped node body runs longer than `budget` seconds.

    A single-shot `threading.Timer` fires the warning from its **own** thread — so it
    reports even while the node pins the caller's thread (the in-process runaway case) —
    and is cancelled the instant the body finishes, so a fast node pays only a
    create-then-cancel. It only **detects**: it cannot stop the node.

    Args:
        node_id (`str`): the node whose `run()` is being timed (for the log message).
        budget (`float`): seconds before a still-running node is logged.
    """
    timer = threading.Timer(
        budget,
        lambda: logger.warning(
            "node %r has been running for over %.0fs and may be stuck; in-process code "
            "cannot be killed, so interrupt the run if this is a runaway",
            node_id,
            budget,
        ),
    )
    timer.daemon = True
    timer.start()
    try:
        yield
    finally:
        timer.cancel()
