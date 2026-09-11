# 155 · Loment → 内核 交接单（给内核线 DSH）

> 生成时间: 2026-09-10 · 作者: Loment 线（zcode）
> 读者: **开发 FujoOS 内核的 agent** —— 假设你没跟过 Loment 线，这份单子让你不用读完全部文档就能接活。
> 上游文档: `docs/145`（100 里程碑）· `docs/149`（内核集成设计）· `docs/146`（能力域语义）· `docs/147`（Potato v1）· `docs/154`（状态矩阵）
> 本文的一句话: **Loment 侧该做的都做完了，有 10 个里程碑卡在"内核侧未接"；这份单子列出内核侧具体要做什么。**

## 1. 你需要的 30 秒背景

**Loment** 是 FujoOS 自研的底层语言（100 里程碑计划见 `docs/145`），分四层：

| 层 | 是什么 | 权威文件 |
|---|---|---|
| L0 接口层 | `.lom` 单源 → 生成 Rust/C/Python/JSON | `lom/*.lom` + `tools/lomc.py` |
| L1 语言 | `tools/lomentc.py`，双后端（转译 Rust / 原生 LLVM IR） | `docs/143` |
| 表示层 Potato | 每个编译单元强制导出形式对象（含能力域） | `tools/potato.py` + `docs/147` |
| 自举 | 用 Loment 重写编译器四段（lexer/parser/checker/codegen） | `loment/selfhost/` + `docs/150` |

**关键事实：Loment 程序已经能在 FujoOS 用户态跑起来**（M67，串口 `M67 RESULT: PASS loment-user`），
链路是 `.lomt → LLVM IR → ELF → fujorun 容器 → QEMU -append fujo.run=<name> → 串口`，
一条命令复现：`python tools/loment_boot.py loment/examples/user_hello.lomt`。

**当前进度**（`docs/154`，从 `docs/145` 自动生成）：完成 **73** / 部分 **17** / 未达 **2** / 未开始 **8**。
自举四段（`loment/selfhost/*.lomt`）已能在 Loment 版 codegen 下生成与参考实现**逐字节相同**的 IR。

## 2. TL;DR：内核侧要做的 10 项

全部是"Loment 侧就绪、内核侧未接"。括号里是账本编号。

| # | 要做的事 | 判据 |
|---|---|---|
| A1 | 把 `loment/build/cap_asserts.rs` 的 A1–A4 断言表接进内核（`include!` 或按字段读），并让它在启动时自检 | A1–A4 由形式对象驱动，内核侧断言失败要能报出 unit/name（M50） |
| A2 | 用同一套域模型做**双向 diff**：`kernel/src/capability.rs` 的域 ↔ Loment 形式对象的 `caps` | 双向 diff **0 差异**（M39） |
| A3 | 实现"撤销"：被撤销的域再调用要返回拒绝（域表已带 `revocable` 位） | 撤销后调用返回拒绝（M37） |
| A4 | 信任自适应域宽接口：域宽随质量台账变化**可观测** | 域宽变化在内核侧可观测（M42） |
| A5 | A1–A4 断言的 Loment 侧表达（`inv_run` 自检）——**与 A1 同一件事的内核侧闭环** | `inv_run` 自检通过（M40） |
| B1 | 内核模块加载器：装载 Loment 编译出的 ELF（入口 `_start` 零参、基址 0x400000） | Loment ↔ Rust **双向调用**通过（M71） |
| B2 | 调度器钩子：上下文切换时把域 id / 任务 id 以只读方式暴露给 Loment 侧 | 上下文切换计数正确（M74） |
| C1 | 安装 IDT，把 Loment 写的 `interrupt fn` 挂上去（ABI 见 §3） | QEMU 中触发中断并从中断处理返回（M33） |
| C2 | 键盘中断路径用 Loment 处理函数**实际生效**（不只是 IR 形状对） | 键盘中断路径生效（M70） |
| D1 | 给 AHCI 驱动提供**真机寄存器块**（现在是 mock：`6 31 1 1`） | 读扇区成功（M68） |

> 账本里 A1–A5 的原始措辞见 `docs/145` 的 P4/P7 表；上面是"内核侧动作"的直译。
> 这 10 项做完，`docs/154` 里对应的 `部分/未开始` 会变成 `✅`。

## 3. 已经定死、**不要**单方面改的接口

这些是跨线契约，改动必须走 L0 单源（§5），否则两个实现会漂移：

1. **用户程序链接地址 = 0x400000**。默认地址会 `#PF err=0x5`（内核用户页从 0x400000 起）。
   编译命令见 `tools/loment_boot.py`：`-nostdlib -static -fuse-ld=lld -Wl,-e,_start -Wl,-Ttext=0x400000`。
2. **入口 `_start` 零参**，内核按 Linux ABI 交付栈（与 `sdk/linux/m30_linux.c` 同形）。
3. **syscall 编号的唯一真源是 `lom/fuai.lom`**（46 个原语）。不要在内核里另立第二张表 ——
   `tools/loment_syscalls.py --check` 会做逐字节对账，`loment_p7_test` 断言内核 dispatch **46/46** 覆盖。
   `syscall4` 用 rax/rdi/rsi/rdx，`syscall6` 追加 r10/r8。
4. **中断函数 ABI**：Loment 的 `interrupt fn f()` 编译成
   `define x86_intrcc void @f(ptr byval([8 x i8]) %__frame)` —— 帧是 **8 字节 byval 指针**，
   不是内核自定义结构体。内核侧 IDT 的 stub 要按这个形状交付。
5. **域表空间名 = FNV-1a 32 位哈希**（`_fnv1a32(space)`）。内核若要按名字查域，必须用同一算法。
6. **编译期 bump 堆是 65536 字节**（IR 全局 `@__loment_heap` / `@__loment_off`，溢出走 `__loment_abort`）。
   用户态要更大堆请用 M73 的固定块池分配器（`loment/examples/allocator.lomt`），不要指望内核扩堆。
7. **内核不需要提供 libc**：`__loment_memcmp` / `__loment_memset` / `__loment_abort` 由编译器在
   `.ll` 里内联（M31），已用 `llvm-nm` 验证无未定义符号。

## 4. 现成产物清单（可直接消费，不需要你重新生成）

| 路径 | 内容 | 用法 |
|---|---|---|
| `loment/build/cap_asserts.rs` | `CapAssert` 结构 + `CAP_ASSERTS` 静态表（unit/name/space/lo/hi/revocable/guards/a1..a4） | 内核 `include!` 或按字段读；由 `tools/potato_assert.py` 从形式对象生成，**请勿手改** |
| `loment/build/fuai_syscalls.lomt` | 46 个 `pub fn fuai_<name>(a0..a4) -> i64` 包装 | Loment 侧调用面；由 `lom/fuai.lom` 生成 |
| `loment/build/*.potato.json` | 形式对象 v1（`funcs`/`types`/`caps`/`guards`/`imports`…） | 内核校验域/契约的机器可读输入 |
| `loment/build/loment.ld` | 裸机链接脚本（1 MiB 起） | 裸机目标（M30/M32） |
| `lom/build/fuc.py`（由 `lom/fuc.lom` 生成） | `NODE_STRUCT` 等 FUI 布局 | 内核 FUI 解码；M69 已证明与 L1 程序**逐字节一致** |
| IR 全局 `@__loment_caps` | 域描述表：每行 `{ i64 fnv1a(space), i64 lo, i64 hi, i64 revocable }` | **运行时可读的域表**——内核可以直接读它做域检查 |
| IR 全局 `@__loment_audit` | `[16 x i64]` 审计计数数组，`guard` 通过时 `+1` | 运行时可读的审计计数（M38） |
| `tools/loment_boot.py` | `.lomt → ELF → fujorun → QEMU → 串口断言` 一键 | 回归用；内核侧改装载逻辑后请重跑 |
| `tools/loment.py dbg FILE --fn X / --addr 0x…` | Loment 版调试器（符号化） | 排查用户程序问题 |

**只有 `@__loment_caps` / `@__loment_audit` 这两个全局是"运行时接口"**，其余是编译期/工具链产物。

## 5. 分工与纪律（这条最容易出事，请先读）

**Loment 线的开发不在 `D:\Dev\FujoOS`，而在独立工作树 `D:\Dev\FujoOS-FujoLang`**
（clone 出来的，分支 `Fujoos-FujoLang-DEV`，origin = GitHub）。

原因：共享工作树的**当前分支会被别人切走**。实测踩坑：提交时分支已被切到 `FujoDEV-V2`，
于是 `git push origin Fujoos-FujoLang-DEV` 报 "Everything up-to-date"，提交其实没推上去
（要用 `git push origin HEAD:Fujoos-FujoLang-DEV` 才落到正确分支）。所以两条线各自一个工作树。

给内核线的四条纪律：

1. **不要动这些路径**（Loment 线产物）：`loment/`、`lom/`、`tools/loment*.py`、`tools/potato*.py`、
   `tools/lom*.py`、`docs/14?-loment-*.md`、`docs/15?-loment-*.md`。要改接口请走下面的第 2 条。
2. **接口改动一律先落 L0 单源** `lom/*.lom`，然后跑 `python tools/lomc.py … ` 或
   `python tools/loment_syscalls.py --emit loment/build/fuai_syscalls.lomt`；生成物由门禁对账，
   别手改 `kernel/src/fui/fuc_gen.rs`、`loment/build/fuai_syscalls.lomt` 这类文件。
3. **提交只含自己的路径 + 提交后立刻推**（并发线出现过 `git reset origin/<branch>` 丢掉别人提交）。
   只提交自己的 hunk 要**整 hunk 过滤**，并在干净 worktree 里验一遍提交态。
4. **门禁口径**：`python tools/ci.py --static-only`。Loment 侧 7 项应当全绿
   （`lomentc_test` / `potato_test` / `loment_tools_test` / `loment_p7_test` / `loment_p8_test` /
   `loment_p9_test` / `loment_status`）。另外 4 项（`lom_audit` / `potato_cross` /
   `fuai_contract_check` / `lomc_test` 的两条）依赖**另一个私有库 `LinuxFUAI/`**——
   它不在仓库里，缺它时这 4 项红是**预期行为，不是回归**。

## 6. 判据对照表（做完怎么证明）

| 账本 | 内核侧做完的标志 | 复现命令 |
|---|---|---|
| M50 / M40 | `CAP_ASSERTS` 接进内核且启动自检通过；`inv_run` 自检通过 | `python tools/potato_assert.py --check` + 内核串口输出 |
| M39 | 内核域模型 ↔ 形式对象 `caps` 双向 diff 0 差异 | 需要一个 diff 脚本/测试；Loment 侧已有 `caps` 字段与 A1–A4 |
| M37 | 撤销后调用返回拒绝 | 用户态程序 + 串口断言 |
| M42 | 域宽随质量台账变化可观测 | 同上 |
| M71 | Loment ↔ Rust 双向调用通过 | `python tools/loment_boot.py loment/examples/native_entry.lomt` |
| M74 | 上下文切换计数正确 | 内核计数器 + 串口 |
| M33 / M70 | QEMU 中中断触发并返回；键盘路径生效 | `loment_p7_test` 现有 8 项 + 新增中断用例 |
| M68 | 真机寄存器块下读扇区成功 | 替换 mock 块后跑 AHCI 用例 |

## 7. 需要 Loment 线配合时怎么办

不要在内核里绕过去。把需求写成三句话发到 Loment 线（或直接落 `lom/*.lom`）：

1. **要什么原语/接口**（例如"要一个 `fuai_map_page(addr, len, prot)`"）；
2. **为什么内核侧绕不过去**（一句话）；
3. **判据**（怎么验证它生效）。

Loment 线收到后会：改 `lom/*.lom` 单源 → 重新生成包装与 spec → 跑 `lom_audit` 逐字节对账 →
更新 `docs/149`（内核集成）与 `docs/154`（状态矩阵）。

## 8. 诚实边界（别把话说满）

- 上面 10 项是**内核侧的具体动作**，但内核怎么实现（IDT 怎么建、调度器钩子挂哪、加载器放哪个目录）
  是**内核线的设计自由**，这份单子只钉"跨线 ABI 形状"与"判据"。
- `docs/154` 的 100 项口径里，还有 3 项需要外部条件（M48 的 LLM 臂要模型凭据、M92 aarch64 要模拟器、
  M100 要外部审计），以及 3 项天生只能"部分"（M30 链接流程、M56 LSP 无编辑器宿主、M59 DWARF 无变量信息）。
  这些**不在这份交接单范围内**。
- 自举线（M83–M86）也还在推进中，但它属于**编译器自己编译自己**，与内核接入正交，不阻塞上面 10 项。
