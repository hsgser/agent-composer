# Types

> This is the type system the engine implements today — the `typesys` package
> (`values.py` + `types.py` + `pool.py`). It is the **leaf** of the layer ladder
> (`events ← typesys ← nodes ← compile ← compose`): nothing engine-internal sits
> below it, and everything above depends on it. For the authoring surface (the
> `type:` / `typedefs:` YAML), see [Flow syntax → Types](syntax.md#types).

## Why a flow has a type system?

A flow is a function: typed values flow in, a typed value flows out, and every
node in between produces a typed value the next node reads. Two forces make the
typing non-negotiable:

- **Durability.** A run can pause (wait for a human, a timer) and resume later —
  possibly in a different process. To do that the engine serializes the whole
  variable pool to JSON and reads it back. JSON loses information: `1` and `1.0`
  come back the same, an enum tag is just a string. So every value carries its
  own type tag, and the round-trip decodes each value back to *exactly* the type
  it had. An int stays an int; a float stays a float.
- **Loud failure at the boundary.** A node is pure — it returns a plain value.
  The engine checks that value against the node's declared output type at the
  moment it is written to the pool, so a node that produces the wrong shape fails
  *there*, with a clear error, instead of corrupting something three nodes later.

The type system is three files, each a clean layer:

```
   types.py   ──  authoring surface: parse a `type:` string / `typedefs:` block
      │              into a runtime `Type`  (parse_type, resolve_type, read_typedefs)
      ▼
   values.py  ──  the leaf: the closed ValueKind vocab, the TypedValue containers,
      │              the structural `Type`, and the write-boundary check
      ▼
   pool.py    ──  VariablePool: the run's typed memory + the `${...}` resolver
```

## `ValueKind` — the closed vocabulary

Every value is one of a fixed, closed set of kinds. The enum's **`.value` is the
one Python-surface spelling** — it is simultaneously the author-written `type:`
token, the serialized discriminator tag, and the displayed name. There is no
second "engine name" to translate between: it is `str`, never `string`.

```
scalars   None  str  int  float  bool  date  datetime  object  file
lists     list[Any]  list[str]  list[int]  list[float]  list[bool]  list[object]
```

`date` / `datetime` are stored as ISO-8601 strings and are **never auto-inferred**
(a string stays `str` unless a slot explicitly declares `date`). `file` is a
**reserved placeholder** — never inferred, not yet authorable — so real file
handling can be added later without a schema migration.

## `TypedValue` — a self-describing, losslessly-serializable container

Each value is wrapped in a frozen pydantic model that pins its `ValueKind` as a
`Literal` discriminator — one subclass per kind (`IntegerValue`, `NumberValue`,
`ListStringValue`, …). The union of all of them is a **discriminated union**:

```python
AnyValue = Annotated[Union[NoneValue, StringValue, IntegerValue, ...],
                     Field(discriminator="kind")]
```

The discriminator is the whole trick. On a JSON round-trip pydantic reads the
`kind` tag and decodes each value back into the right subclass — so `{"kind":
"int", "value": 1}` decodes to `IntegerValue(1)`, not a float, even though JSON
cannot tell `1` from `1.0`. **That losslessness is the primitive durable
checkpoint/resume is built on** — the pool serializes through this union
(`pool.dumps()` / `pool.loads()`).

A node's own code never touches a `TypedValue`: `.to_object()` hands back the
plain Python value (an `int`, a `dict`, a `list`), and `.text` is the string form.
Wrapping happens only at the pool's write boundary.

## Two constructors: infer vs. check

There are exactly two ways a raw Python value becomes a `TypedValue`.

```
build_value(v)               →  wrap v in its NATURAL type (infer)
build_value_as(declared, v)  →  wrap v as `declared`, RAISE on a mismatch (check)
```

- **`build_value`** infers the kind from the Python value. The one subtlety:
  `bool` is checked before `int` (a Python `bool` *is* an `int`), and an empty
  list infers as `list[Any]`. `file` is never inferred — only an explicit
  `FileRef` produces a `FileValue`.
- **`build_value_as`** is the **write-boundary check**. It takes the node's
  declared type and validates the value against it, raising `TypeCheckError` on
  any mismatch. The only coercion is **widening**: an `int` may fill a `float`
  slot. This is what `VariablePool.set(node_id, value, declared)` calls, so a
  node returning the wrong type fails loudly at the write, not silently
  downstream.

## `Type` — structure beyond the bare tag

A `ValueKind` says how a value *stores*; a `Type` says what it must *be*. `Type`
is a frozen dataclass whose `kind` is the storage tag, with optional fields that
refine the check:

| Form | `Type` shape |
|------|--------------|
| scalar / flat list | `kind` only (`Type.scalar(kind)`) |
| **record** | `kind=OBJECT`, `fields={name: Type}`, `required={names}` |
| **variant** (enum) | `kind=STRING`, `tags={labels}` |
| **typed list** | `kind=LIST_*`, `element=<Type>` |
| **Optional[X]** | X's `Type` with `nullable=True` (accepts `None`) |

Records are **closed**: every required field must be present and no unknown field
is allowed. A variant value must be one of its tags. `Optional[X]` (nullable) and
a `= default` (omission-fill) are orthogonal — nullable accepts a present `None`;
a default fills an *absent* input.

## `types.py` — from authoring string to runtime `Type`

An author writes a type as a string (`list[Signal]`, `Optional[date]`) or a
`typedefs:` block. `types.py` bridges that surface to a runtime `Type`:

```
"list[Signal]"  ──parse_type──▶  TypeExpr AST  ──resolve_type(registry)──▶  Type
```

- **`parse_type`** parses the string via Python's own `ast` (eval mode), so the
  vocabulary *is* Python typing: scalars, `list[X]`, `Optional[X]`, `dict[K,V]`
  (resolved to a lenient `object` — key/value typing is deferred), `Literal[...]`
  (an inline enum), and a bare PascalCase name (a registry reference). `Union[...]`
  is **rejected** with a message pointing at the supported alternative — model a
  tagged union as a discriminated record `{tag: Literal[...], ...}` routed by
  `case ... on tag`. The legacy spellings (`string`/`integer`/`number`/`boolean`)
  no longer parse.
- **`resolve_type`** resolves a `TypeExpr` against the per-flow **`TypeRegistry`**
  (`name -> RecordDef | VariantDef | AliasDef`) into a leaf `Type`, expanding
  records field-by-field and following aliases transitively (cycle-guarded).
- **`read_typedefs`** builds the registry from the raw `typedefs:` mapping: a
  `dict` value is a **record**, a `Literal[...]` string is an **enum**, any other
  string is a transparent **alias**. Names must be PascalCase and must not shadow
  a scalar or a typing constructor. Alias cycles are rejected eagerly; record
  cycles are caught lazily during resolution.

## `VariablePool` — the run's typed memory

The pool is the runtime state primitive — the "memory" the engine
([StateManager](engine.md#statemanager--the-memory-the-pool)) owns. Its shape is
deliberately narrow:

- **A node produces exactly ONE value.** `store[node_id]` is a single
  `TypedValue`; "multiple outputs" are fields of one `object`. Writes are
  **write-once** and typed (`set` runs the `build_value_as` check when a declared
  type is given).
- **Three read namespaces**, all via `${...}`:

  | Reference | Resolves to |
  |-----------|-------------|
  | `${input.X}` | field `X` of the flow's input (the synthesized START node's record) |
  | `${node.output[.path]}` | node `node`'s value (`.output` is a syntactic discriminator the resolver skips), dotting into an object |
  | `${system.X}` | a host-injected ambient (run id / clock / tenant); reserved, run-global |

  An unrecognized head resolves to `None` — the missing-ref-is-falsy contract the
  `${...}` engine relies on (see [Expressions](expressions.md)). Because values
  are typed *objects*, not stringified, `${x.output.ratio}` traverses **into** an
  object output.
- **Lossless serialization.** `dumps()` / `loads()` round-trip the whole pool
  through the `AnyValue` union — the state half of a durable checkpoint.

## Why this shape

- **Durable by construction.** The discriminated `AnyValue` union is the one
  mechanism that makes a checkpoint lossless; everything durable rides on it.
- **One vocabulary.** `ValueKind.value` is the author token, the wire tag, and the
  display name at once — no translation layer to drift.
- **Fail at the boundary.** Nodes stay pure (plain values in, plain values out);
  the single write-boundary check (`build_value_as`) is where a wrong type is
  caught, loudly and early.
- **A leaf with no cycles.** `values.py` imports nothing internal; `types.py` and
  `pool.py` build on it. The whole package sits below `nodes`, so a node can be
  typed without the type system knowing anything about nodes.
