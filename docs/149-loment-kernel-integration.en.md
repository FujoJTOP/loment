<!-- translated-from: docs/149-loment-kernel-integration.md -->
<!-- source-sha256: 96a09bce945c2a8c72bd8b4e27590ff406459f29fe08010e8df0bcbfbd3e1bd6 -->

# 149 · Loment kernel integration (P7, M67–M78)

> Status: **implemented and passing the gate** (2026-09-09) · Self-test: `tools/loment_p7_test.py` 8/8
> Gate: `ci.py --static-only` 8/8 · In one line: **Loment programs run in FujoOS user space.**
>
> **Handover to the kernel line**: what the kernel side still has to do (10 items), the cross-line ABI
> convention, the list of artifacts it can consume directly, and the division-of-labour discipline between the
> two lines — see **`docs/155-loment-kernel-handoff.md`** (aimed at a kernel agent who has not followed the
> Loment line).

## 0. The run chain (the core of M67)

```
.lomt --(lomentc --emit-llvm)--> .ll
      --(clang --target=x86_64-unknown-linux-gnu -nostdlib -static
              -fuse-ld=lld -Wl,-e,_start -Wl,-Ttext=0x400000)--> ELF
      --(fujorun pack)--> FUJOMULT 容器
      --(QEMU -kernel kernel/fujo-kernel.bin -append fujo.run=<name>)--> 串口
```

One command: `python tools/loment_boot.py loment/examples/user_hello.lomt`.

Key constraints (measured):

- **The link address must land in the user region** (`-Ttext=0x400000`): the default 0x201260 gives `#PF
  err=0x5` (the kernel's user pages start at 0x400000).
- **The entry `_start` takes zero parameters**: the same shape as `sdk/linux/m30_linux.c`; the kernel delivers
  the stack per the Linux ABI.
- New builtins: `syscall4(nr,a0..a2)`, `syscall6(nr,a0..a4)` (rax/rdi/rsi/rdx/r10/r8), `str_ptr`,
  `ptr_add/ptr_sub`, the `ptr as u64` conversion, and `as` conversion of integer literals.

## 1. Milestones

| # | Milestone | Delivered | Criterion/evidence |
|---|---|---|---|
| M67 | first Loment user program | `user_hello.lomt`: `write(1)` + `exit(60)` | serial `M67 RESULT: PASS loment-user` ✅ |
| M68 | AHCI driver | `ahci.lomt`: PI→port count, CAP.NCS, 64-bit flag, GHC.AE set | mock register block `6 31 1 1` ✅ partial (the real-hardware register block is supplied by the kernel side) |
| M69 | FUI widget | `fuc_node.lomt` packs 64B per the Node layout | **byte-identical** to `lom/fuc.lom`'s `NODE_FMT` ✅ |
| M70 | interrupt handling | `interrupt fn` → `x86_intrcc void @f(ptr byval([8 x i8]))` | IR shape assertion ✅ partial (installing the IDT belongs to the kernel side) |
| M71 | kernel module ABI | zero-parameter entry + argv on the stack + load at 0x400000 | `_start` shape + ELF entry assertion ✅ partial |
| M72 | syscall layer | `tools/loment_syscalls.py` generates 46 wrappers from `lom/fuai.lom` | kernel dispatch **46/46** coverage ✅ |
| M73 | allocator | `allocator.lomt`: fixed block pool + first-fit + reuse | alloc/free/reuse `1 1 1 1` ✅ |
| M74 | scheduling hooks | ABI convention (domain id / task id read-only) | document + static shape; **not wired to the kernel scheduler** ✅ partial |
| M75 | Loment debugger | `loment dbg FILE --fn fib / --addr 0x...` | `fn fib: 21 条指令, 源行 7..16` [21 instructions, source lines 7..16] ✅ |
| M76 | boot probe | `bootprobe.lomt`: `uname(63)` → print sysname | serial `M76 RESULT: PASS bootprobe` ✅ |
| M77 | kernel self-check | `selfcheck.lomt`: getpid/time/getrandom/arithmetic | serial `M77 RESULT: PASS selfcheck` ✅ |
| M78 | all-Loment demo | `all_loment.lomt`: multiple modules + generics + decimal printing | serial `M78 RESULT: PASS all-loment` ✅ |

## 2. M72: from a single source of truth to the syscall layer

`lom/fuai.lom` is the sole authority for the 46 FUAI primitives. `loment_syscalls.py` compiles it into
`loment/build/fuai_syscalls.lomt` (46 `pub fn fuai_<name>(a0..a4) -> i64`), and `lom_audit` does a
**byte-for-byte reconciliation** at every gate; the test also asserts that every opcode can be found in the
dispatch of `kernel/src/syscall.rs` (currently 46/46).

This joins L0 (the interface's single source) to L1 (the language): change one place in `lom/fuai.lom`, and the
wrapper layer and the specs of the two implementations are all checked at once.

## 3. M69: the formal-object side and the kernel side share one layout

`fuc_node.lomt` hand-writes the Node packing, and the test packs the same set of fields with `lom/build/fuc.py`'s
`NODE_STRUCT` (generated from `lom/fuc.lom`) and **compares byte for byte**. This proves that "writing kernel
structures in Loment" does not rest on aligning by hand: the three sides (the `.lom` single source / the L1
program / the kernel decoder) come from one source.

## 4. Uncovered boundaries (honest list)

- **The kernel-side wiring for M68/M70/M71/M74 is not done**: the real-hardware AHCI register block, IDT
  installation, the kernel module loader and the scheduling hooks all require changing `kernel/src/*`, which
  conflicts with the concurrent development line, so this stage delivers only the part that is "correct in shape
  + host-verifiable".
- **M37/M39/M40/M42 (the kernel side of capability domains) are still partial**: revocation semantics,
  `capability.rs` alignment, the A1–A4 run-time assertions and trust-adaptive domain width likewise depend on
  kernel-side changes.
- A user program can today talk to the kernel only through the Linux ABI (write/exit/uname/…); **the FujoOS
  native syscall surface (0x8xxx) is not yet open from user space** (the kernel's existing implementation is
  aimed at in-kernel calls).
- `loment_boot.py` depends on a built `kernel/fujo-kernel.bin`; while the kernel source is being modified
  concurrently the test can be skewed by a stale image (the test prints SKIP rather than a fake PASS).
