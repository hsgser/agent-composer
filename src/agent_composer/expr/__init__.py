"""Expression + condition evaluation (`${...}` references, `when:` clauses) and the
`${...}` binding-value template language.

A leaf both `nodes` (runtime resolve/bind) and `compile` (the compile-time
reference walk) may import:
`expressions` is the `when:`/`asserts:` evaluator + pool resolution; `template` is the
binding-value parser/evaluator (parse → AST → typed value | stringified embed).

Knows about:   `state` (the pool, in `expressions`).
Never imports: `nodes`, `compile`, `runtime`, `suspension` (they import IT).
"""

from agent_composer.expr.expressions import (
    ExpressionError,
    evaluate_when,
    first_failing_assert,
    resolve_reference,
)
from agent_composer.expr.template import (
    InlineCall,
    RequiredError,
    binding_co_skips,
    binding_refs,
    desugar_calls,
    eval_binding,
    eval_template,
    expr_refs_of,
    parse_binding,
    prompt_refs,
    render_template_record,
    scan_template,
)

__all__ = [
    "ExpressionError",
    "InlineCall",
    "RequiredError",
    "binding_co_skips",
    "binding_refs",
    "desugar_calls",
    "eval_binding",
    "eval_template",
    "evaluate_when",
    "expr_refs_of",
    "first_failing_assert",
    "parse_binding",
    "prompt_refs",
    "render_template_record",
    "resolve_reference",
    "scan_template",
]
