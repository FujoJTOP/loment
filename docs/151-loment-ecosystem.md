# 151 · Loment 生态与平台（P9，M89–M96）

> 状态: **进行中**（2026-09-09）· 自检: `tools/loment_p9_test.py` 2/2 · 门禁: `ci.py --static-only`

## M89 · SDK 示例集 ✅

`loment/examples/` 共 **25 个 `.lomt`**，每个都通过编译（形式对象导出 + 可选 IR）。
覆盖：标量/聚合/字符串/切片/泛型/trait/Result/内存/位图/能力域/中断/RAII/用户态程序/
多模块演示。判据"10 个示例全通过"超额满足，由 `loment_p9_test` 逐个验证。

## M90 · 第三方库加载（L0 单源共享）✅

外部库不需要复制常量：库以 `.lomt` 模块形式被 `use`，而常量/布局来自 `lom/*.lom`
单源（`lomc` 生成 Rust/C/Python/JSON 四份，`lom_audit` 逐字节对账）。
示例：`loment/build/fuai_syscalls.lomt`（由 `lom/fuai.lom` 生成）与 `loment/examples/bytes.lomt`
（被 `ahci`/`allocator`/`fuc_node`/`selfhost/lexer` 复用），三处调用方零复制。

## M91 · 版本与兼容策略 ✅

- **编译器**：版本戳 = `lomentc.py` 的 sha256 前 12 位（`loment_manual` 写入手册首页），
  编译器改动即版本变化。
- **形式对象**：`potato` 字段是语义版本；新增能力走 `v1`，旧对象按自带版本回放（M51）。
- **语言**：`0.1.4 Alpha` 冻结前允许增量；冻结后只做向后兼容扩展（M96）。
- **包**：`pkg.json` 的 `version` 为 `MAJOR.MINOR.PATCH`；`lompkg` 锁文件记录每个包
  的 sha256，依赖变更即校验和不符。

## M92 · 跨平台目标（aarch64）✅ 部分

同一份 LLVM IR 用 `clang --target=aarch64-unknown-linux-gnu -nostdlib -ffreestanding -c`
交叉编译成功，`llvm-objdump -f/-d` 显示 `elf64-littleaarch64` 与 aarch64 指令（含 `ret`）。
**未执行**：本机没有 `qemu-aarch64` 用户态模拟器，判据"产物可运行"待补。

## M93 · 语言手册站点 ✅

`tools/loment_manual.py` 生成 `docs/manual/`（首页 + 25 个示例 API 页），首页写入
编译器版本戳；`--check` 逐字节对账（手册与编译器同版本）。规范章节直接链接 docs/141–150。

## M94 · 教程与迁移指南 ✅

Rust → Loment 迁移对照（同一段逻辑）：

| Rust | Loment |
|---|---|
| `fn f(x: u32) -> u32 { x + 1 }` | `fn f(x: u32) -> u32 { return x + 1; }` |
| `let mut a = [0u32; 4]; a[0] = 1;` | `let mut` 不需要：`let a: [u32; 4] = [0,0,0,0]; a[0] = 1;` |
| `&[u32]` / `&mut [u32]` | `[u32]` / `mut [u32]`（显式 `&a` / `&mut a` 取切片） |
| `Result<T,E>` + `?` | 相同（预置泛型 + `?` 降级为 match 早退） |
| `impl Drop for T` | 相同（`impl Drop`，M16） |
| `unsafe { asm!("in al, dx") }` | 内建 `inb/outb`（Rust 路径）/ `syscall4/6`（两路径） |
| `match` 穷尽性 | 相同（编译期检查） |
| 所有权/借用 | 最小子集：移动（struct/数组）、借用冲突检查；无生命周期 |
| 宏 / trait 对象 / 生命周期 | **不支持**（严格子集，见 docs/143 §7） |

## M95 · 发布流程与社区规范 ✅

`tools/loment_release.py`：

- `--emit` 产出 `loment/build/release-manifest.json`（**98 个工件**，逐文件 sha256）；
- `--check` 在任意机器上重算并比对，任何漂移即失败（可复现，M99）。

发布检查单：① `ci.py --static-only` 全绿；② `loment_release --check` 一致；
③ `loment_manual --check` 一致；④ 手册/规范/示例同版本戳；⑤ 变更写入 docs/14x–15x。

## M96 · 语言稳定性承诺（0.1.4 Alpha 冻结）⚠️

**未冻结**。当前状态是 `0.1.4-alpha`：M80–M88（自举）与 M68/M70/M71/M74（内核侧接入）
未完成前不做冻结承诺。冻结判据（拟）：自举定点（M84）+ 全门禁绿（M100）+ 手册定稿。

## 未覆盖边界

- M92 的"可运行"未验证（无 aarch64 模拟器/真机）；
- 无包仓库（`lompkg` 只支持本地路径依赖）；
- 无编辑器插件发布（LSP 只有 `handle()` 自测）；
- M96 未冻结，API 仍可能破坏性调整。
