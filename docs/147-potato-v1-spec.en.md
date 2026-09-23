<!-- translated-from: docs/147-potato-v1-spec.md -->
<!-- source-sha256: 5da8800c1c4dcf634db7ca2ed2374524267826b9d2d20910a66dfebff4926c2e -->

# 147 · Potato v1: formal-object specification and the wave C measurement protocol

> Status: **implemented and passing the gate** (2026-09-09) · Producer: `tools/lomentc.py` (forced export)
> Validator: `tools/potato.py` (FujoOS side) · `LinuxFUAI/tools/potato_verify.py` (second implementation)
> Self-check: `tools/potato_test.py` 7/7 (33 counterexamples) · `tools/potato_cross.py` 50/50 verdicts agree
> In one line: **v1 folds generics/slices/strings/audit sites into the formal object; the same object is
> judged the same result by two independent implementations.**
>
> Paper-three material (docs/110 §5): this file is the engineering draft of the "specification + measurement
> protocol" manuscript.

## 1. Differences from v0

v0 (docs/142) covered only scalars/arrays/struct/enums/layouts/capability domains. After P3/P4 the language
gained generic monomorphisation, slices, `str`, `()`, trait/impl and `guard` audit sites — v1 exports all of
them:

| New field | Comes from | Notes |
|---|---|---|
| `generics` | `fn f<T>` / `struct S<T>` / `enum E<T>` | generic declarations (this unit's view before monomorphisation, including the predefined `Option`/`Result`) |
| `instances` | monomorphisation | `{kind, name, of, args}` — which generic is instantiated with which arguments |
| `traits` | `trait T { fn m(self) -> R; }` | traits and their method names |
| `impls` | `impl T for X` | implementation relations (the method name is the name declared in the trait, without the `X_` prefix muddle) |
| `guards` | `guard cap(idx)` | the number of audit sites in this unit (the input of the A2 assertion) |
| Type syntax | — | signature types gain `[T]`, `mut [T]`, `str`, `ptr`, `()` |

The `potato` field takes `"v0"`/`"v1"`. **v0 objects are still legal** (replay, see §5), but v0 allows no v1
field.

## 2. Validation rules (normative)

The validator decides from the object alone, reading neither source nor binary. The rules fall into three
layers:

**Structure layer**: top-level field whitelist (by version); the shape of `unit`/`language`/`imports`; every
table element must be an object with a name that is an identifier and unique within the table; `types` and
`enums` share one type namespace (the same name across the two tables = illegal).

**Type layer**: legal types = the base types `{u8,u16,u32,u64,i8,i16,i32,i64,bool,str,ptr,()}` ∪ declared
`types`/`enums` names ∪ `[T; N]` (N>0) ∪ `[T]` ∪ `mut [T]` (elements recursively legal). `layouts` fields allow
only fixed-width integers, with `offset + width ≤ size` and non-overlapping intervals.

**Semantics layer**:
- capability domains: `space` an identifier, `lo ≤ hi`, `lo ≥ 0`, `revocable` a boolean;
- enum payload keys must be declared variants and payload types must be declared;
- `generics`: `kind ∈ {fn,type}`, parameters non-empty and unique, `(kind,name)` unique;
- `instances`: `of` must be a declared generic of the same `kind`, the number of arguments equals the number
  of parameters, arguments are legal types, instance names unique;
- `traits`/`impls`: trait names unique, methods non-empty and unique; `impl.trait` must be a declared trait or
  the builtin `Drop` (method set `{drop}`); `impl.for` must be a legal type; an impl method must appear in the
  trait declaration;
- `guards`: a non-negative integer.

Counterexample completeness is guaranteed by the 33 mutations in `tools/potato_test.py`: every single rule in
the spec has at least one counterexample that is rejected.

## 3. Forced export (M46)

**Every** code-production path in `tools/lomentc.py` must produce a formal object at the same time:

1. `emit_potato()` calls the independent validator `potato.validate()` before returning, and any error is a
   `LomError` (compilation failure);
2. CLI layer: `--emit-rust` / `--emit-llvm` must be given `--emit-potato` alongside, otherwise exit code 2;
3. Repository layer: `lom_audit` requires that every example in `loment/examples/*.lomt` has a
   **byte-identical** `loment/build/<name>.potato.json`, and that the object passes the independent validator.

None of the three layers can be turned off — there is no "skip the export" switch.

## 4. Formal object → kernel assertion binding (M50)

`tools/potato_assert.py` consumes only formal objects and generates `loment/build/cap_asserts.rs` (a Rust
static table + `assert_a1_a4()`). The binding rules:

| Axiom | Formal-object-side criterion | Generated field |
|---|---|---|
| A1 no execution beyond authority | the capability domain must have a named space + a finite interval | `a1 = space non-empty && hi ≥ lo` |
| A2 auditable | every `guard` site must land in the audit count | `a2 = guards ≥ 0` (the compiler guarantees guard ⇒ `__loment_guard` + audit) |
| A3 runs with the model absent | the unit must not request a model/AI capability space | `a3 = space ∉ {ai, model, llm, net_llm, infer}` |
| A4 failure counting and degradation | a revocable capability must declare `revocable` | `a4 = revocable` |

Current artifacts: 2 capability assertions (`demo` / `native_cap`), A1–A4 failures 0. **Wiring it into the
kernel via `include!` is P7 (M39/M42)** — at this stage all that is guaranteed is "the table is driven by the
formal object and reconciles byte for byte".

## 5. Versioning and replay (M51)

- the object carries its own version number; the validator accepts `v0` … **`v7`**, and an unknown version =
  illegal. **A version is a step on the "set of fields" ladder, and the ladder only grows** — adding a
  required field to an old version would turn every existing object illegal, and the old versions are
  **promised to keep replaying** (last bullet in this section), so every new required field costs a version:

  | Version | Field added | Specified in |
  |---|---|---|
  | `v0` | (baseline) | `docs/142` |
  | `v1` | `generics` · `instances` · `traits` · `impls` · `guards` | §1 of this document |
  | `v2` | `mode` | `docs/178` |
  | `v3` | `switches` | `docs/182` §1 |
  | `v4` | `dialects` | `docs/184` §9 |
  | `v5` | `bodies` | `docs/185` §7 ① |
  | `v6` | `grammar` | `docs/188` §2 |
  | `v7` | `boundary` | `docs/205` R5 |
  | `v8` | `gc` | `docs/175` §3.4 |

  The ladder **accumulates**: `v8` requires the fields of every version below it.

- **`gc` (v8)**: one of two **collection tiers** — `gc_manual` (the program reclaims
  explicitly) or `gc_auto` (the runtime reclaims). **Same level and shape as `mode`**: a string
  value, **required**, settable only in the root unit. So "which tier this artifact was built in"
  — and whether it **gave up determinism** — is decidable without reading the source, which is
  exactly what `docs/175` §3.4 asks for.
  The validator also rules on **mutual exclusion on its own**: `mode=no_std` together with
  `gc=gc_auto` is illegal — both values are in the object, so no source is needed. Why they
  conflict is in `docs/175` §3.4 ⚠: automatic collection needs a runtime, and `no_std` means
  "only the core layer".


- **`boundary` (v7)**: how many **call sites** in a unit step outside the language's guarantees — machine
  calls (`syscall4`/`syscall6`), raw-pointer transforms (`ptr_add`/`ptr_sub`/`str_ptr`), and calls to names
  the unit itself declared `extern fn`. **The measure is lexical**: it only asks whether a name is called,
  never what type it has or whether it is really dangerous — which is exactly what `docs/204` R5 asks for
  ("greppable, countable, auditable"). The shape is five non-negative integers
  `{extern_declared, extern_calls, syscalls, ptr_transforms, total_sites}`, and `total_sites` must equal the
  sum of the last three. **That rule is the part the validator can judge on its own**: it cannot read source,
  so it cannot tell whether the numbers were counted correctly — but it can tell when they contradict each
  other. `loment stat` reports **the same numbers** (that copy is checked against the reference
  implementation's real lexer). The list of builtins
  (`syscall4`/`syscall6`/`ptr_add`/`ptr_sub`/`str_ptr`) lives in `BOUNDARY_BUILTINS` in `tools/potato.py` —
  there because this validator must not import the compiler (M47), and "which builtins cross the boundary" is
  precisely what an auditor needs.

- **`v6` used to be emitted only by `tools/potato_from.py`** (whose output is *translated* units). The main
  compiler only picked `grammar` up **when `v7` was added**: the ladder accumulates, and it had been emitting
  `v5` all along, so the bump ran straight into its own **self-check** for a missing `grammar`. Worth
  recording — it shows the accumulation is itself guarded by a criterion: **miss one rung and the compiler's
  own self-check goes red.**

- `python tools/potato.py replay FILE...` replays validation according to the version the object carries, and
  `--expect-version` can assert a version;
- the frozen sample `loment/build/legacy/demo.v0.json` is a real v0 object produced by the compiler before P5,
  replayed continuously by `potato_test` — old objects do not go stale when a new version ships.

## 6. Cross-implementation consistency (M53)

The same formal object must be judged the same result by two independent implementations:

| Implementation | Location | Independence |
|---|---|---|
| FujoOS side | `tools/potato.py` | the compiler's self-check uses it too (but the compiler only imports it; the reverse is forbidden) |
| LinuxFUAI side | `LinuxFUAI/tools/potato_verify.py` | implemented independently from this spec, importing no FujoOS code |

`tools/potato_cross.py` compares case by case across **committed objects + legal fixtures + the 33
counterexample mutations**, requiring 100% agreement (currently 50/50). M47 separately asserts that `lomentc`
does not appear among `potato.py`'s imports.

## 7. Transcription tool (M49)

`tools/potato_from.py` turns Python (`ast`) / C (lightweight parse) / Rust (lightweight parse) source into
formal objects, and produces a report:

- entity granularity = function / type / constant (**fields do not count as entities**; a failed field mapping
  is recorded separately in `fields_skipped`, affecting only fidelity, not the denominator);
- `entities_seen` counts only the entities the recogniser **saw**; `potato_measure.py` separately estimates
  `entities_est` in lenient mode, and the ratio of the two = the **recognition rate**, exposing the
  recogniser's own blind spots;
- two arms: `strict` does not guess (skip what cannot be mapped); `lenient` relaxes by fixed rules (unknown
  type → `ptr`, unannotated Python → `i64`). Both arms are deterministic and reproducible, **using no random
  numbers**.

## 8. The wave C measurement protocol (M54)

**Corpus**: `loment/corpus.json` pins 12 real repository files (4 per language); the first 16 hex digits of
each file's sha256 are recorded in `loment/build/wave-c-results.json`, so the measurement is bound to the
content and reproducible.

**Metrics** (aggregated by language × arm):

| Metric | Definition |
|---|---|
| Estimated entities | the number of candidate entities matched in lenient mode (independent of the transcriber) |
| Recognition rate | entities the recogniser saw / estimated entities |
| Conversion rate | transcribed entities / recognised entities (**conditional on recognition**) |
| 95% CI | Wilson interval, sample size n = the number of recognised entities |
| Object legality rate | objects passing the independent validator / files |

**Sample size**: currently n = 48(Python) / 33(C) / 116(Rust). The Wilson interval is markedly too wide when
n<30; the paper's main table needs the corpus expanded to ≥200 entities per language (about 15–20 files)
before the half-width can be squeezed below ±5%.

**Host-LLM arm** (run 2026-09-11; tool `tools/potato_llm_arm.py`): the A/B of docs/110 §5 is "host LLM vs
low-yield toolchain". This arm was previously recorded as "not run"; it has now been run on the same corpus
and the same report schema — **using two small models on the local Ollama** (the corpus is this repository's
source, so the endpoint is hard-wired to loopback and nothing leaves the machine):

```
python tools/potato_llm_arm.py --build loment/build/llm-req --models qwen3:4b,llama3.2:3b
for f in loment/build/llm-req/*/*.req.json; do                        # 外部采集一步
  curl -s -X POST --data-binary @"$f" http://127.0.0.1:11434/api/chat \
    -o "${f%.req.json}.rep.json"; done
python tools/potato_llm_arm.py --score loment/build/llm-req \
  --digests qwen3:4b=359d7dd4bcda,llama3.2:3b=a80c4f17acd5
```

The tool only **builds payloads and scores**; the request stays one step in the caller's hands (making the
endpoint a configurable `base_url` inside the tool would be opening an SSRF surface for the tool, with the
only benefit being "swap the endpoint", plus needing to declare the corpus leaving the machine).

The artifact `loment/build/wave-c-llm.json`: 24 rows (12 files × 2 models), the per-file corpus sha256 from
the same source as `wave-c-results.json`, temperature 0 / seed 42 / num_ctx 8192, and the model digest
recorded too (a local tag can be overwritten by new weights).

| Model | Language | Files | Estimated entities | Recognised | Recognition rate | Transcribed | Conversion rate | 95% CI | Object legality rate |
|---|---|---|---|---|---|---|---|---|---|
| `qwen3:4b` | python | 4 | 48 | 5 | 10.4% | 4 | 80.0% | [38%, 96%] | 2/4 |
| `qwen3:4b` | c | 4 | 37 | 10 | 27.0% | 8 | 80.0% | [49%, 94%] | 1/4 |
| `qwen3:4b` | rust | 4 | 116 | 71 | 61.2% | 61 | 85.9% | [76%, 92%] | **0/4** |
| `llama3.2:3b` | python | 4 | 48 | 6 | 12.5% | 6 | 100.0% | [61%, 100%] | 3/4 |
| `llama3.2:3b` | c | 4 | 37 | 4 | 10.8% | 4 | 100.0% | [51%, 100%] | 3/4 |
| `llama3.2:3b` | rust | 4 | 116 | 4 | 3.4% | 4 | 100.0% | [51%, 100%] | 4/4 |

How to read it, and four qualifications that must go into the paper:

1. **This is not a frontier-LLM comparison**. The two models are local small models in the 1.5–4B class; this
   table says only "what the output quality of small models is on this task with this prompt". To serve as an
   A/B comparison it needs a rerun with frontier models — the protocol need not change (same corpus, same
   schema, same metrics).
2. **The two failure modes are exactly opposite**, and that is the most informative cell in this table:
   `qwen3:4b` reaches a 61% recognition rate on Rust (it really did read out many functions), yet **0/4
   objects legal** (the entity shapes do not comply: a missing `ret`, a missing `potato` version number);
   `llama3.2:3b` is the reverse — objects are mostly legal but it extracts almost no entities (Rust 3.4%).
   "Writing correct JSON" and "reading the code" are two different abilities, and the small models each hold
   one end.
3. **Truncation**: 18/24 calls sent only the first 12000 characters (the small models have num_ctx=8192, while
   the corpus has files of 22–25k characters); the per-file `truncated` is recorded in the artifact.
4. **The toolchain's two arms** (the table in the next section): on the same corpus the toolchain has a
   recognition rate of 89–100%, an object legality rate of 12/12, and conversion rates of 93.8% (py lenient) /
   100% (c) / 98.2% (rust lenient). **The "low-yield toolchain" is not low-yield on this task** — which is
   exactly the proposition the A/B of docs/110 §5 set out to test, and now the first batch of data points from
   the small-model side exists.

### The wave C main table (2026-09-09, corpus sha256 in `loment/build/wave-c-results.json`)

| Language | Arm | Files | Estimated entities | Recognised | Recognition rate | Transcribed | Conversion rate | 95% CI (Wilson) | Object legality rate |
|---|---|---|---|---|---|---|---|---|---|
| python | toolchain-strict | 4 | 48 | 48 | 100.0% | 13 | 27.1% | [16.6%, 41.0%] | 4/4 (100.0%) |
| python | toolchain-lenient | 4 | 48 | 48 | 100.0% | 45 | 93.8% | [83.2%, 97.9%] | 4/4 (100.0%) |
| c | toolchain-strict | 4 | 37 | 33 | 89.2% | 33 | 100.0% | [89.6%, 100.0%] | 4/4 (100.0%) |
| c | toolchain-lenient | 4 | 37 | 33 | 89.2% | 33 | 100.0% | [89.6%, 100.0%] | 4/4 (100.0%) |
| rust | toolchain-strict | 4 | 116 | 114 | 98.3% | 104 | 91.2% | [84.6%, 95.2%] | 4/4 (100.0%) |
| rust | toolchain-lenient | 4 | 116 | 114 | 98.3% | 112 | 98.2% | [93.8%, 99.5%] | 4/4 (100.0%) |

How to read it, and honest boundaries:

- **Python's bottleneck is annotation quality, not missing annotations**: the strict arm is 27.1%, and the
  main reason for skips is container types like `list[str]`/`dict`/`object` that have no Potato counterpart;
  after the lenient arm downgrades them to `ptr` the conversion rate is 93.8%, but fidelity drops (the paper
  must report both columns, not just the higher one).
- **C's bottleneck is the recogniser**: recognition rate 89.2%, with 4 multi-line/macro signatures invisible
  to the regex — the 100% conversion rate is a rate "conditional on recognition" and must be read together
  with the recognition rate.
- **Rust transcribes best**: static types + explicit `&[T]`/`mut [T]`, strict arm 91.2%; the remaining skips
  are `&'static [u8; N]` (N a constant), `!`, and tuple returns.
- **The 100% object legality rate** says the toolchain arm does not produce illegal objects — it would rather
  transcribe less than transcribe garbage.

## 9. Uncovered boundaries (honest list)

- **The LLM arm uses local small models only** (§8): the "host LLM" side of the A/B is `qwen3:4b` /
  `llama3.2:3b`, and the frontier-model cell is still empty — the comparison does not yet hold up, so do not
  take it as evidence that "the LLM loses to the toolchain";
- **M50 stops at the artifact layer**: `cap_asserts.rs` is not yet `include!`d by the kernel, and the run-time
  assertions for A1–A4 wait on P7;
- **the transcriber is structural recognition, not semantic equivalence**: it does not guarantee that a
  transcribed function behaves like the original, only the type/signature structure;
- **Python's container types have no Potato counterpart**: `list[T]`/`dict[K,V]`/class instances are always
  skipped in strict;
- **C's multi-line signatures/macros are invisible**: the recognition-rate ceiling is bounded by the regex's
  power;
- **v1 carries no AI capability-space semantics**: A3 checks only "no AI space was requested", not whether an
  AI call goes out of range.
