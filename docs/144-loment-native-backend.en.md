<!-- translated-from: docs/144-loment-native-backend.md -->
<!-- source-sha256: 6397a2932ed4e5eef1949d46c8c866ea3a389cac1d215b3c549df4e8665b97dd -->

# 144 · An extra-long task: the Loment native backend → self-hosting

> Status: **in progress** (started 2026-09-08) · Starting point: docs/143 §7 v2/v3 · Toolchain: clang 22.1.8 (`C:\Program Files\LLVM\bin\clang.exe`)
> Big picture: M0–M5 of this file have been folded into the **100-milestone plan of docs/145** (the native backend = M23–M34 of P3);
> this file is kept as the trade-off and evidence record for the native-backend special effort.
> In one line: **Stop making Loment walk on borrowed Rust — emit executable code directly, and in the end write itself with itself.**

## 1. Why this one

L1 v1 can already run end to end (write → transpile to Rust → rustc → run → export Potato), but "transpiling to
Rust" means: the semantics are constrained by Rust, there is no way off the Rust toolchain, and no way to
customise for FujoOS bare-metal targets. docs/143 §7 lists the **v2 native backend** and **v3 self-hosting** as
the remaining real work — the only problem in the whole language project that has not yet been solved by
routing through something else.

## 2. Milestones

| Milestone | Content | Criterion | Status |
|---|---|---|---|
| **M0** | LLVM IR backend (scalar subset: integer/boolean, arithmetic/bitwise/comparison, if/while/for, calls, constants) | `.lomt` → `.ll` → clang → executable; output **value-for-value identical** to the Rust path | ✅ |
| M1 | aggregate-type IR: struct (GEP), fixed-length arrays, payload-free/payload-carrying enums (tagged union) | demo's `Blk`/`Shape`/array functions produce the same result on the native path | ✅ |
| M2 | bare-metal target: `--target x86_64-unknown-none`, no libc, custom entry and linker script | the generated `.o` can be consumed by the FujoOS linker script and booted | partial (the `.o` is produced, the linking flow is not wired up) |
| M3 | run-time representation of capability declarations: `capability` compiled into a kernel-checkable domain description | aligns with the domain model of `kernel/src/capability.rs` | to do |
| M4 | self-hosting: write the Loment compiler in Loment (lexer/parser/type checking/IR generation) | the Loment-version compiler compiles itself and produces the same `.ll` | to do |
| M5 | switch the default backend, demote the Rust transpilation path to a control | all gates + regressions pass on the native path | to do |

## 3. M0's trade-offs (ponytail bookkeeping)

- **Route through LLVM IR, do not write a machine-code backend**: register allocation / instruction selection
  go to clang, M0 does only "structured emission" (alloca/load/store + basic blocks), and optimisation goes to
  `clang -O1`. Writing a machine-code backend is a job for after self-hosting.
- **`&&` / `||` do not short-circuit yet**: M0 uses `and`/`or i1`, with no short-circuit semantics. The only
  thing in the current language with a side effect is a function call, and a call appearing in the right-hand
  operand of a logical operator will be evaluated — noted in docs; fixed with phi from M1 on.
- **Emit `unreachable` when the end of a function is unreachable**: the language allows "not all paths
  return", and M0 adds no checking rule for this (YAGNI); the price is that execution falling into that block
  is UB.
- **Signed/unsigned instruction choice follows the declared type**: `icmp slt/ult`, `sdiv/udiv`, `ashr/lshr`,
  `srem/urem`.
- **Division-by-zero/overflow semantics differ from the Rust path** (LLVM is UB, Rust panics): M0 does not
  handle it, and a rule must be fixed before M2.

## 4. Verification method

```bash
# 1) 生成 IR
python tools/lomentc.py loment/examples/native.lomt --emit-llvm loment/build/native.ll
# 2) 与 C 驱动一起编译成原生可执行
"C:\Program Files\LLVM\bin\clang.exe" -O1 -o loment/build/native_exe.exe \
    loment/build/native_driver.c loment/build/native.ll
# 3) 运行，输出须与 Rust 路径一致
./loment/build/native_exe.exe
```

Criterion: the same set of functions produces **value-for-value identical** output on the two paths (Rust
transpilation / LLVM IR).

### M0 measured (2026-09-08)

```
python tools/lomentc.py loment/examples/native.lomt --emit-llvm loment/build/native.ll   # 3911 B
clang -O1 -o loment/build/native_exe.exe loment/build/native_driver.c loment/build/native.ll
./loment/build/native_exe.exe
55 21 8 45 205 1 21
```

The output of seven functions (fib / gcd / popcount / sum_range / mask_low / in_domain / scaled) on the native
path is **value-for-value identical** to the Rust transpilation path. IR shape: parameters and locals are
uniformly `alloca`'d in the entry block (avoiding repeated allocation inside loops), `icmp ult/slt` is chosen
by declared signedness, constants are inlined during generation (no global variables). `clang` reports
`overriding the module target triple` once — the IR declares no triple, so clang decides it, and this is
expected.

Self-check: `lomentc_test` contains 4 M0 tests (IR determinism/key instructions, signedness-driven instruction
choice, rejection of aggregate types, `--check` drift detection).

## 5. Risks and stop-loss

- **Biggest risk**: the backend swallows the time for the papers and language maintenance. → Hard gate: at the
  end of every milestone the whole gate must be green (`ci.py --static-only` 4/4 + kernel build + one
  regression); the papers' priority is unchanged.
- **Self-hosting risk**: the language still has no strings/files/dynamic memory, and these capabilities must
  be added before M4; if any of M1–M3 stalls, M4 slides automatically and is not forced.
- **Stop-loss line**: if M2 cannot get the artifacts into the FujoOS linking flow, fall back to the "IR
  backend + host executable" positioning and stop claiming it can replace the Rust path.
