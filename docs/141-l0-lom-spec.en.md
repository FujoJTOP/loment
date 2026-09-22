<!-- translated-from: docs/141-l0-lom-spec.md -->
<!-- source-sha256: bf443ce41390f68f0dd3585e62ab2d8070bb88c159bc67c66cb5a2ec8dfe04b1 -->

# 141 · L0 interface layer: the `.lom` language specification v0

> Status: **implemented and passing self-check** (2026-09-08) · Implementation: `tools/lomc.py` · Audit: `tools/lom_audit.py`
> Single source: `lom/fuai.lom` (46 primitives) · `lom/fuc.lom` (Header 48B / Node 64B)
> In one line: **declare in one place, generate in many; drift is impossible at generation time, rather than only discovered at run time.**

## 1. Positioning and invariants

L0 is the bottom layer of the three-layer architecture in docs/140: the **interface layer**. It answers "where is an interface fact written", and does not answer "how the kernel is written as a program" (that is L1 / Loment).

Four invariants:

1. **Single source of truth**: `.lom` is authoritative; generated artifacts may not be hand-edited.
2. **The native syntax borrows the flavour of a known language**: the surface syntax takes the Rust flavour (`record` / `enum` / `const`) and invents no new syntax — directly sidestepping the "zero corpus" veto of docs/110 §1 (docs/140 §4).
   (Corrected 2026-09-18: **it no longer claims to be a "strict subset"** — the surface syntax is now more than one, see `docs/188`. What it ever bought was the prior over the **overlap**, not "containment".)
3. **Interception at generation time**: overlap / out of range / duplication / same value are errors at compile time, and are never left to run time.
4. **Deterministic output**: no timestamps, stable declaration order, LF line endings — hence a byte-for-byte `--check` is possible.

Relation to the layers above: the declaration syntax of L1 (Loment) is a superset of `.lom`; the Potato formal object is the serialised form of the `.lom` semantic model (docs/140 §9.4). L0 is the common ancestor of both.

## 2. Syntax

```
module <ident>

meta {                         // 文档级元数据: 字符串/整数, 可嵌套组
  <key> = <string | int>
  <key> { ... }
  "<key-with-dash>" = <string>  // 键可用字符串字面量
}

const <NAME> : <int-type> = <int>

param <name> {                 // 部署参数: 同一参数在不同实现上的取值
  <impl> = <int>
  note = <string>              // 可选: 参数说明
}

enum <Name> : <int-type> {
  <VARIANT> = <int> {
    <key> = <string | int>     // 元数据: sig / fujo / linux / layer / semantics / …
    ...
  }
  ...
}

record <Name> layout(packed, size=<int>, endian=little|big) {
  <field> : <int-type> @ <offset>
  ...
}
```

- Integer types: `u8 u16 u32 u64 i8 i16 i32 i64`; literals support decimal and `0x` hexadecimal, and may carry a leading `-`.
- Comments: `//` and `/* */`; the comma / semicolon between items is optional.
- `record` must give `size=`; `packed` means no implicit alignment is done (gaps must be left explicitly, and the generator fills them with `x`).

## 3. Semantic rules (all enforced at generation time)

| Rule | Criterion |
|---|---|
| Top-level names unique | `const` / `record` / `enum` / `param` share one namespace |
| Enum member names unique | a duplicate name is an error |
| Enum values unique | a duplicate value is an error (FUAI opcodes must be reverse-lookupable) |
| Enum values within the base type's range | writing 256 in a `u8` is an error |
| Record field names unique | — |
| Fields do not overlap | sort by offset, then check pair by pair |
| Fields do not go out of range | `offset + width ≤ size` |
| Constants within the type's range | — |
| A record must fill its declared size | the generator checks back with `struct.calcsize`: the computed size must equal `size` |

## 4. Generation contract

| Target | Artifact | Use |
|---|---|---|
| `--emit-rust` | `pub const` constants and offsets | kernel |
| `--emit-c` | `#define` + typed literals | LinuxFUAI / host tools |
| `--emit-python` | constants + a `struct.Struct` format string | packing/unpacking in `tools/*.py` |
| `--emit-json` | the semantic model (with metadata) | audit / ancestor of the Potato formal object |
| `--check` | writes nothing, only reconciles | CI gate: exits 1 on any drift |

A `record`'s Python format string is assembled automatically from the offsets (gaps filled with `x`), so it passes only if **the layout is captured in full**: the `NODE_FMT = '<HHHHhhHHHHHHHHHHHHHHIIIHHII'` generated from `lom/fuc.lom` is character-for-character identical to the hand-written literal at `tools/fuic.py:416` (the self-check `test_generated_fuc_fmt_matches_fuic` guards it).

## 5. Where it has landed (2026-09-08)

| Item | Result |
|---|---|
| `lom/fuai.lom` | 46 primitives + 2 deployment parameters, migrated from `sdk/fuai-spec/spec.json` |
| `lom/fuc.lom` | Header 48B / Node 64B, replacing three separate declarations in fuic.py / fuc.rs / docs/138 |
| Generated artifacts | `lom/build/{fuai,fuc}.{rs,h,py,json}` |
| **Taking over the source of truth (fuc)** | the `tools/fuic.py` packer now uses `NODE_STRUCT` from `lom/build/fuc.py`; `kernel/src/fui/fuc.rs` becomes `pub use super::fuc_gen::*`, with constants from `kernel/src/fui/fuc_gen.rs` |
| **Authority flipped (fuai)** | `sdk/fuai-spec/spec.json` and `LinuxFUAI/spec/spec.json` are now generated by `tools/lom_spec_emit.py` from `lom/fuai.lom` (the two copies are byte-identical, 9790 B) |
| Equivalence evidence | `fuic.py --check` → `desktop.fuc` **byte-identical** (12542 B); `spec.json` is **semantically equivalent as JSON** before and after generation (the only two differences are formatting: trailing whitespace in the hand-written file, and a short note not wrapped); the kernel's `cargo build --release` passes; `fujoregress --only 0` PASS |
| Self-check | `python tools/lomc_test.py` → **19/19** (including 6 kinds of semantic negative case, meta parsing, spec generation consistency, determinism, drift detection) |
| Audit | `python tools/lom_audit.py` → **0 differences** (including the wiring check that "generated artifacts / packers must not fall back to hand-writing") |
| CI gate | `python tools/ci.py --static-only` → **3/3** (lomc_test / lom_audit / fuai_contract) |

**A real defect the audit caught on its first run**: the comment at `kernel/src/capability.rs:68` said "τ_high 46", while the code in the same file and the Fujo-side value in `spec.json` are both **45** (46 is LinuxFUAI's deployment value). Fixed to 45. This is exactly the kind of "stale number" L0 exists to eliminate.

## 6. Acceptance criteria

Current (v0):
- `lomc_test` 15/15; `lom_audit` 0 differences; `ci.py --static-only` 3/3.
- Generated artifacts are literal-for-literal identical to the existing implementation (fuc layout, fuc.rs constants).

Next milestone (v2, coverage):
- ⬜ `ui/fui_spec.json` (the FUI vocabulary) → `lom/fui.lom`, with `fuic.py --emit-rust` taking it from the generated artifact;
- ⬜ the FUJR container format (currently 5 implementations) → `lom/fujr.lom`;
- ⬜ `sdk/contracts/*.json` predicates → `lom/contracts.lom`;
- ⬜ `sdk/rulebook/fidelity.csv` → `lom/rulebook.lom`;
- once coverage is complete, `tools/fuai_contract_check.py` can be retired (`lom_audit.py` is its superset).

## 7. Roadmap

| Phase | Content | Status |
|---|---|---|
| v0 | compiler + 2 schemas + audit + CI gate | ✅ |
| v1 | generated artifacts take over the source of truth (fuc packer / kernel constants + the fuai spec.json authority flip) | ✅ |
| v2 | FUJR container (5 implementations → 1 single source, byte round-trip proof) | ✅ |
| v2 | `ui/fui_spec.json`, contracts predicates, rulebook | outstanding (these three are already single-source + generated, so low priority) |
| v3 | `.lom` becomes the serialisation substrate of the Potato v0 formal object | ✅ see docs/142 |

## 8. Follow-up landing (2026-09-08, second round)

| Item | Result |
|---|---|
| `lom/fujr.lom` | Header 64B / Section 32B / Tag enum; generates `lom/build/fujr.{rs,h,py,json}` |
| Taking over the source of truth (fujr) | `tools/fujopack.py`'s pack/info now use the generated structure; the `"FUJR"` magic number and offsets are no longer hand-written |
| Equivalence evidence | repacking `sdk/build/m31_res.run` with the generated structure → **byte-identical at 16428 B**; `fujopack.pack()` round-trips identically too |
| General generated-artifact gate | `lom_audit.py` gains a `GENERATED` registry: all 13 generated artifacts are reconciled one by one against what the `.lom` generates |
| L1 hookup | `lom_audit.py` also checks that `loment/build/*` agrees with the `.lomt` translation result (docs/143) |
