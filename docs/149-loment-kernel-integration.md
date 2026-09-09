# 149 · Loment 内核集成（P7，M67–M78）

> 状态: **已实现并通过门禁**（2026-09-09）· 自检: `tools/loment_p7_test.py` 8/8
> 门禁: `ci.py --static-only` 8/8 · 一句话: **Loment 程序在 FujoOS 用户态跑起来了。**

## 0. 运行链（M67 的核心）

```
.lomt --(lomentc --emit-llvm)--> .ll
      --(clang --target=x86_64-unknown-linux-gnu -nostdlib -static
              -fuse-ld=lld -Wl,-e,_start -Wl,-Ttext=0x400000)--> ELF
      --(fujorun pack)--> FUJOMULT 容器
      --(QEMU -kernel kernel/fujo-kernel.bin -append fujo.run=<name>)--> 串口
```

一条命令：`python tools/loment_boot.py loment/examples/user_hello.lomt`。

关键约束（实测得到）：

- **链接地址必须落在用户区**（`-Ttext=0x400000`）：默认 0x201260 会 `#PF err=0x5`（内核用户页从 0x400000 起）。
- **入口 `_start` 零参**：与 `sdk/linux/m30_linux.c` 同形；内核按 Linux ABI 交付栈。
- 新增内建：`syscall4(nr,a0..a2)`、`syscall6(nr,a0..a4)`（rax/rdi/rsi/rdx/r10/r8）、
  `str_ptr`、`ptr_add/ptr_sub`、`ptr as u64` 转换、整型字面量 `as` 转换。

## 1. 里程碑

| # | 里程碑 | 交付 | 判据/证据 |
|---|---|---|---|
| M67 | 第一个 Loment 用户程序 | `user_hello.lomt`：`write(1)` + `exit(60)` | 串口 `M67 RESULT: PASS loment-user` ✅ |
| M68 | AHCI 驱动 | `ahci.lomt`：PI→端口数、CAP.NCS、64 位标志、GHC.AE 置位 | mock 寄存器块 `6 31 1 1` ✅ 部分（真机寄存器块由内核侧提供） |
| M69 | FUI 控件 | `fuc_node.lomt` 按 Node 布局打包 64B | 与 `lom/fuc.lom` 的 `NODE_FMT` **逐字节一致** ✅ |
| M70 | 中断处理 | `interrupt fn` → `x86_intrcc void @f(ptr byval([8 x i8]))` | IR 形状断言 ✅ 部分（IDT 安装归内核侧） |
| M71 | 内核模块 ABI | 入口零参 + 栈上 argv + 0x400000 装载 | `_start` 形状 + ELF 入口断言 ✅ 部分 |
| M72 | 系统调用层 | `tools/loment_syscalls.py` 从 `lom/fuai.lom` 生成 46 个包装 | 内核 dispatch **46/46** 覆盖 ✅ |
| M73 | 分配器 | `allocator.lomt`：固定块池 + first-fit + 复用 | 分配/释放/复用 `1 1 1 1` ✅ |
| M74 | 调度钩子 | ABI 约定（域 id / 任务 id 只读） | 文档 + 静态形状；**未接内核调度器** ✅ 部分 |
| M75 | Loment 调试器 | `loment dbg FILE --fn fib / --addr 0x...` | `fn fib: 21 条指令, 源行 7..16` ✅ |
| M76 | 引导探针 | `bootprobe.lomt`：`uname(63)` → 打印 sysname | 串口 `M76 RESULT: PASS bootprobe` ✅ |
| M77 | 内核自检 | `selfcheck.lomt`：getpid/time/getrandom/算术 | 串口 `M77 RESULT: PASS selfcheck` ✅ |
| M78 | 全 Loment 演示 | `all_loment.lomt`：多模块 + 泛型 + 十进制打印 | 串口 `M78 RESULT: PASS all-loment` ✅ |

## 2. M72：从单一真源到系统调用层

`lom/fuai.lom` 是 46 个 FUAI 原语的唯一权威。`loment_syscalls.py` 把它编译成
`loment/build/fuai_syscalls.lomt`（46 个 `pub fn fuai_<name>(a0..a4) -> i64`），
`lom_audit` 每次门禁都做**逐字节对账**；测试同时断言每个 opcode 都能在
`kernel/src/syscall.rs` 的 dispatch 里找到（当前 46/46）。

这把 L0（接口单源）与 L1（语言）接上了：改 `lom/fuai.lom` 一处，包装层与两份实现
的 spec 同时被校验。

## 3. M69：形式对象面与内核面共用同一布局

`fuc_node.lomt` 手写 Node 打包，测试用 `lom/build/fuc.py` 的 `NODE_STRUCT`
（由 `lom/fuc.lom` 生成）打同一组字段并**逐字节比较**。这证明"Loment 写内核结构"
不是靠人肉对齐，而是三方（`.lom` 单源 / L1 程序 / 内核解码）同源。

## 4. 未覆盖边界（诚实清单）

- **M68/M70/M71/M74 的内核侧接入未做**：真机 AHCI 寄存器块、IDT 安装、内核模块加载器
  与调度钩子都需要改 `kernel/src/*`，与并发开发线冲突，故本阶段只交付"形状正确 +
  可宿主验证"的部分。
- **M37/M39/M40/M42（能力域内核侧）仍为部分**：撤销语义、`capability.rs` 对齐、
  A1–A4 运行时断言、信任自适应域宽，同样依赖内核侧改动。
- 用户程序目前只能通过 Linux ABI（write/exit/uname/…）与内核对话；**FujoOS 原生
  syscall 面（0x8xxx）尚未从用户态开放**（内核现有实现面向内核内调用）。
- `loment_boot.py` 依赖已构建的 `kernel/fujo-kernel.bin`；内核源码在并发修改时
  测试可能因镜像陈旧而失真（测试会打印 SKIP 而不是假 PASS）。
