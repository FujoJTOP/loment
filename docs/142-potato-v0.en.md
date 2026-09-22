<!-- translated-from: docs/142-potato-v0.md -->
<!-- source-sha256: 575484f498610d2c42d4370b2f59e84c64126af267565968da132a31fd37cf85 -->

# 142 · Potato v0: formal object and validator

> **v1 is released** (2026-09-09, docs/147): generics/slices/strings/trait/audit sites are folded into the
> formal object, the validator accepts both v0/v1, and v0 objects still replay. This file keeps the v0 spec as
> a historical baseline.
>
> Status: **implemented and passing self-checks** (2026-09-08) · Producer: `tools/lomentc.py --emit-potato`
> Validator: `tools/potato.py` · Self-check: `tools/lomentc_test.py` 18/18
> In one line: **the one formal object through which an agent sees the system — an independent validator can
> decide legality from it alone, reading neither source nor binary.**

## 1. Positioning (the division of labour with Loment)

docs/140 §9 sets the key: **Loment governs "how to write", Potato governs "how to see"**.
Potato is not a language (the positioning revision in docs/110 §1): it has no syntax, only formal objects and
validation rules. The Loment compiler **forces** a Potato formal object out of every compilation unit (for v0,
`--emit-potato`).

> Decision changed: the "implementation stays closed-source" of docs/110 §4 was overridden by a user
> instruction on 2026-09-08 (docs/140 §10), so this implementation and specification are open-sourced together.

## 2. Formal object schema v0

```json
{
  "potato": "v0",
  "unit": "<标识符>",
  "language": "loment",
  "capabilities": [
    {"name": "blk_write",
     "domain": {"space": "disk", "lo": 0, "hi": 4},
     "revocable": true}
  ],
  "functions": [
    {"name": "fib", "params": [{"name": "n", "type": "u32"}], "ret": "u32"}
  ],
  "layouts": [
    {"name": "Header", "size": 64, "endian": "little", "packed": true,
     "fields": [{"name": "magic", "type": "u32", "offset": 0}]}
  ],
  "consts": [
    {"name": "MAX_BLKS", "type": "u32", "value": 8}
  ],
  "types": [
    {"name": "Blk", "fields": [{"name": "off", "type": "u32"}, {"name": "len", "type": "u32"}]}
  ],
  "excluded": ["network: 本单元不申请任何 net 能力"]
}
```

Field sources:

| Field | Comes from | Notes |
|---|---|---|
| `unit` | `module <name>` | compilation unit name |
| `imports` | `use "*.lomt"` | names of imported compilation units (the formal object describes only this unit, it does not copy imported symbols) |
| `capabilities` | `capability name : space[lo..hi] [revocable]` | capability domains and revocation semantics |
| `functions` | `fn` signatures | parameter/return types (may reference types declared in `types`) |
| `layouts` | L0 records brought in by `use "*.lom"` | binary layout (with offsets/sizes/endianness) |
| `types` | L1-native `struct` | language-level data types (no offsets, not a binary layout) |
| `consts` | `const NAME: T = v;` | integer constants |
| `enums` | L1 `enum` | enums and their variants; a variant with a payload additionally has an optional `payloads` (variant name → payload type) |
| `excluded` | `excluded "..."` | explicitly excluded capabilities |

## 3. Validation rules (`tools/potato.py`)

| Rule | Criterion |
|---|---|
| Version | `potato == "v0"`; **any unknown top-level field is rejected** (v0 allows no extension) |
| Imports | `imports`: every entry is a valid identifier |
| Identifiers | `unit` / capability name / function name / parameter name / layout name / field name must match `[A-Za-z_][A-Za-z0-9_]*` |
| Uniqueness | capability names, function names, layout names, field names, parameter names must not repeat |
| Capability domain | `space` is an identifier; `lo`, `hi` are integers with `0 ≤ lo ≤ hi`; `revocable` is a boolean |
| Function signatures | `ret` and parameter `type` ∈ {u8..u64, i8..i64, bool} |
| Layouts | `size > 0`; `endian ∈ {little,big}`; `packed` is a boolean; field types are fixed-width; `offset ≥ 0` and `offset+width ≤ size`; fields must not overlap |
| Native types | `types`: names unique, not colliding with a base type name, fields non-empty and their types declared (may reference other declared types); function signature parameter/return types may reference them |
| Constants | `consts`: names unique, type is an integer type, value is an integer |
| Enums | `enums`: names unique, not colliding with a base type name; variants non-empty and unique; the keys of the optional `payloads` must be declared variants and the types must be declared |
| Excluded | every entry is a non-empty string |

**Criterion (docs/140 §9.4)**: given a formal object, an **independent** validator can decide from it alone
whether the capability domains, contracts and layouts are legal — without reading the source and without
reading the binary. `potato.py` imports no Loment code.

## 4. Usage

```
# 生产: .lomt -> 形式对象
python tools/lomentc.py loment/examples/demo.lomt --emit-potato loment/build/demo.potato.json

# 校验 / 查看
python tools/potato.py validate loment/build/demo.potato.json
python tools/potato.py show     loment/build/demo.potato.json
```

`show` output (measured):

```
unit=demo lang=loment caps=1 fns=4 layouts=2
  cap  blk_write: disk[0..4] revocable
  fn   fib(n: u32) -> u32
  ...
  rec  Header size=64 fields=3
  rec  Section size=32 fields=4
```

## 5. Acceptance criteria

- `potato.py validate` accepts the formal object produced from `demo.lomt` (measured `[OK]`).
- The validator rejects 5 classes of malformed object (unknown field / inverted domain interval / overlapping
  fields / out of range / illegal return type), see `test_potato_rejects_*` in `tools/lomentc_test.py`.
- The `layouts` in the formal object agree with their single source in `lom/*.lom` (the same L0 parse result,
  not maintained a second time).

## 6. Relation to docs/110 / follow-ups

- The positioning of the three papers in docs/110 is unchanged: Potato is still the representation layer of
  **paper three**; this v0 is its **interface draft** (the "freeze the v0 interface early" suggested in
  docs/140 §9.6), containing only the schema + validator, and **does no measurement and publishes no paper**.
- To do: the measurement protocol of wave C (structural-recognition conversion rate / validation pass rate),
  the exclusion semantics of `excluded`, and the binding of Potato formal objects → the kernel A1–A4
  assertions (the docs/59 interface axioms are a candidate semantic layer).
