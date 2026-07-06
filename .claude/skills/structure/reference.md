# Reference — package charter & layout

## Charter template (copy into a new `__init__.py`)

```python
"""<one line: what this package IS>.

<2–4 sentences: the single responsibility it owns, and why it's its own package.>

Knows about:   <lower-level/peer packages it may import, by name>
Never imports: <higher-level packages that import IT; plus forbidden deps>
"""

# re-exports only — no logic, no inline classes
from <pkg>.<submodule> import <PublicName>

__all__ = ["<PublicName>"]
```

## Real example (from this repo)

`src/agent_composer/typesys/__init__.py` — the typed value system + variable pool,
the engine's leaf layer (it imports nothing else in the engine; `nodes`,
`compile`, `compose`, and `runtime` import *it*, never the reverse):

```python
"""Typed runtime state: the typed value system + the variable pool."""

from agent_composer.typesys.pool import VariablePool
from agent_composer.typesys.values import TypedValue, build_value
# … (the full module re-exports the typed value + type surface)

__all__ = ["TypedValue", "VariablePool", "build_value"]
```

The worked top-level charter — with the full leaf-to-root layer diagram and the
"nothing here imports a DB or a server" boundary — is
`src/agent_composer/__init__.py`. Read it to see the dependency direction written
down for the whole engine.

## Where does it go? (one-glance table)

| The thing you're adding                          | Goes in                       |
|--------------------------------------------------|-------------------------------|
| Public behavior of one entity/kind               | that entity's own module      |
| Dataclass / base type / enum / registry shared across a package's submodules | `common.py`        |
| Pure stateless helper function                   | `utils.py`                    |
| A whole new distinct responsibility (>1 file)    | new package (write charter)   |
| Something an existing package already owns        | that package (don't make a new one) |

## Import-direction smell test

- Importing *upward* (leaf reaches into a root package) → wrong package, or needs a seam.
- Two packages import each other → extract shared contract to `common.py`/a leaf, or merge.
- `from x import y` written *inside a function* to dodge an error → hidden cycle; fix the structure.
