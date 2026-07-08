# Wiring a new authorable leaf node kind

The Node subclass (from `node.py.template`) is only half the job — a kind also
needs a parser descriptor, a `_KIND_SPECS` entry, and a `build` branch so a flow
author can write `kind: xxx` in YAML. This is the file-by-file checklist for an
**authorable leaf** kind (one a flow names directly, like `code`/`tool`).

> Internal-only kinds (`START`/`END`/`CALL`/`MAP`) are *synthesized* by the loader,
> not authored — they skip `_KIND_SPECS` and the leaf `build` branch and expand at
> runtime instead. This checklist is for a leaf you want authors to write.

Use `code` (the simplest real leaf) as the reference implementation throughout.
Build bottom-up by dependency, **one test green before the next step** (CLAUDE.md).

## 1. `nodes/base.py` — add the closed-vocabulary tag

Add a member to the `NodeKind` enum — the single source of the tag. It is a closed
vocabulary (no registry/metaclass); the engine core does NOT dispatch on it, so a new
leaf needs no engine change — its behavior is entirely in its own `run` (and, for a
spawner, the node-owned growth traits/hooks: `binds_per_item`, `bind_reserved`,
`iter_boundary_records`, `grow_depth_delta`, `grow_restamps_self`, `is_loop`, `needs_llm`).

```python
class NodeKind(str, Enum):
    ...
    XXX = "xxx"
```

## 2. `nodes/xxx/` — the Node subclass + charter

- `nodes/xxx/node.py` — copy `node.py.template`, rename `XxxNode`, set
  `kind = NodeKind.XXX`, implement `run`.
- `nodes/xxx/__init__.py` — copy `__init__.py.template` (re-export only; the
  package charter per the `structure` skill).

## 3. `compose/parser.py` — descriptor + spec entry

The parser turns a YAML node block into a typed descriptor; `_KIND_SPECS` declares
which flat fields the kind allows (an illegal field is a loud error).

- Add an `XxxDescriptor` dataclass next to `CodeDescriptor` (~line 222). Keep the
  common tail (`node_name` / `depends_on` / `runs_after`); add your kind's fields.
- Add it to the `NodeDescriptor` union (~line 335).
- Add a `_KIND_SPECS` entry (~line 351): `(class, required_fields, allowed_fields)`.

```python
@dataclass(frozen=True)
class XxxDescriptor:
    """kind=xxx — <one line>."""
    id: str
    param: str
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: Any = None
    asserts: list[str] = field(default_factory=list)
    node_name: Optional[str] = None
    depends_on: list[str] = field(default_factory=list)
    runs_after: list[str] = field(default_factory=list)

# in _KIND_SPECS:
"xxx": (XxxDescriptor, frozenset({"param"}),
        frozenset({"param", "inputs", "outputs", "asserts"})),
```

## 4. `compose/build.py` — build the node + stamp wiring

`build` maps a descriptor to a constructed node, then stamps the node-side `params`
(its signature) and the flow-owned `wiring` (the `${...}` sources) from `inputs`.

- Import the node (~line 35): `from agent_composer.nodes.xxx import XxxNode`.
- Add an `elif` branch (~line 168) mapping `XxxDescriptor` → `XxxNode(...)`.
- The default `params`/`wiring` stamp from `desc.inputs` (~line 199) covers most
  kinds; only `tool` (`args:`) and `wait` (`until:`) override it.

```python
elif isinstance(desc, XxxDescriptor):
    node = XxxNode(desc.id, param=desc.param, title=desc.node_name)
```

## 5. `compose/__init__.py` — re-export the descriptor

If anything outside `compose/` references `XxxDescriptor`, add it to the imports +
`__all__` (~lines 37 / 63), mirroring `CodeDescriptor`.

## 6. `tests/engine/test_xxx.py` — prove it

Copy `test_node.py.template`. At minimum: a direct `run()` unit test and one
`load_flow` + `run_flow` end-to-end. Run:

```bash
PYTHONPATH=src pytest tests/engine/test_xxx.py -q
```

## 7. GitHub issue + docs

Close the tracking GitHub issue when the work lands, referencing the commit/PR
(CLAUDE.md "Zeroth rule"). If the kind is part of the authoring surface, add it to
`docs/syntax.md` and the `composing-agents` skill.

---

### Quick map

| Touchpoint | File | Anchor |
|------------|------|--------|
| Enum tag | `nodes/base.py` | `class NodeKind` |
| Node impl | `nodes/xxx/node.py` + `__init__.py` | new package |
| Descriptor + spec | `compose/parser.py` | `CodeDescriptor`, `NodeDescriptor`, `_KIND_SPECS` |
| Build branch | `compose/build.py` | the `isinstance(desc, ...)` chain |
| Re-export | `compose/__init__.py` | imports + `__all__` |
| Test | `tests/engine/test_xxx.py` | new file |
| Issue/docs | GitHub issue, `docs/syntax.md` | — |
