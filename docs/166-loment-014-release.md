# 166 · Loment 0.1.4 Alpha —— Early Use Vision

> 版本：`0.1.4-alpha` · 显示名 `Loment 0.1.4 Alpha` · 对外 tag `v0.1.4-alpha`（见 §7）
> 上一版：`0.1.3.4-alpha`（tag `v0.1.3.4-alpha`，M96 冻结面快照）
> 命名口径：**三段一源** —— 标识符（连字符）/ 显示名（空格）/ tag（git ref 不许带空格），
> 唯一真源是 `tools/loment_release.py` 的 `RELEASE` / `RELEASE_NAME`。
> **"Early Use Vision" 是这一版的题材，不进版本串** —— 题材进 tag 会把"哪一版"和"讲什么"
> 混进同一个 git ref（`v0.1.4-early-use-vision`），也让版本比较多出一种渲染。

## 1. 这一版讲什么

**从"能自举"走到"能被早期使用者用起来"。**

语言与编译器在 `0.1.3.4` 就完成了自举闭环（M79–M88：lexer/parser/checker/codegen 全用 Loment 写、
三阶段定点）。但**用它的路上还留着解释器** —— 工具链与 L0 生成器都还是 Python。
0.1.4 把这条路走完：用户侧工具链（`lomfmt` / `lomdoc` / `loment_lsp` / `lompkg`）与
**L0 生成器**（`lomc`）全部有了 Loment 实现，判据统一是"**与 Python 版逐字节相同**"。

换句话说，这一版的主题是**"用得起"**，不是"功能更多"：语言面没有新语法。

## 2. 变更（自 `0.1.3.4-alpha`）

| 变更 | 判据 |
|---|---|
| 包管理器 `lompkg` 去 Python（拓扑序 / sha256 / 环检测 / 锁往返） | 审计主张 **C17** `loment_pkg_test` 3/3 |
| **L0 生成器 `lomc` 去 Python**（rust/c/python/json 四后端） | 审计主张 **C18** `loment_lomc_test` 4/4（12 份发射逐字节） |
| **M59 收口**：DWARF 补上变量信息（行表之外） | `loment_tools_test` 18/18 |
| 自举 checker：**复合类型表按函数清零**（原先整单元累积，大单元写穿） | `loment_rule_parity` 63/63 · 假阳性 0 |
| `--debug` + `match` 的非法 IR（多行 `switch` 被逐行挂 `!dbg`） | `loment_tools_test` 的 `--debug` 语料编译 |
| 工作区指令订正：台账原语不是 `ledger=33562`（实际是 `qual_feed`/`qual_seq`） | `CLAUDE.md` / `AGENTS.md` |

三个**自举链的既有 bug** 是这一轮移植逼出来的（`lomc.lomt` 是迄今最大的自举单元：357 个 `let`、
108 个 `fn`）。**两个已修、两个仍开放**，账在 `docs/150` 的 M81/M82 后置修订，不主张清单见 `docs/160 §2`。

## 3. 兼容性（相对 `0.1.3.4-alpha`）

- **语言面没有破坏性变更**：语法、类型规则、诊断码 E001–E017、单元装载、发射符号约定（跨线 ABI）
  与 0.1.3.4 相同 —— 仍按 `docs/158` 的冻结面。**用 Loment 写的东西不用改。**
- **生成物零改动**：`lom/build/` 的 12 个 L0 工件一个字节都没动 —— **内核线不用跟**。
- **自举种子变了**：`loment/build/selfhost_driver.ll` 1 630 342 → **1 630 422 B**（checker 修了
  类型表清零点，IR 跟着变）。从种子起头重建工具链的人要换新种子。
- **发行包/源码包文件名随版本变**：`loment-0.1.4-alpha-*`（见 `docs/162` / `docs/164`）。

## 4. 判据与验证

发布检查单（`docs/151` M95）五条：

```bash
python tools/ci.py --static-only        # ① 全绿（本机红的是 LinuxFUAI 缺位/内核镜像/VS Code 路径）
python tools/loment_release.py --check  # ② 工件 sha256 一致
python tools/loment_manual.py --check   # ③ 手册与编译器同版本戳（戳=编译器 sha256 前 12 位）
python tools/loment_audit.py --json     # ④ 审计包：主张逐条 + 不主张清单
```

审计结果：**17/18 通过**，唯一红是 **C14**（本机 `...Microsoft VS Code\Code.exe` 是坏挂载点，
`os.stat` 抛 `WinError 649`）—— 环境项，非回归。

## 5. 不主张

完整 10 条见 `docs/160 §2`。其中**两条是本次新发现、且仍开放**的自举链缺口：

1. 自举 checker 对**把关键字当标识符**（`let fn: u64 = ...`）有**假阳性** —— 参考实现接受，
   自举侧误拒（比"保守少报"更糟）。本次改的是变量名，检查器没动。
2. 自举 codegen 的 `operand_type()` 在"运算符右侧是带 `as` 的括号表达式"时取错类型，
   发射**非法 IR**（`(48 + (x % 10) as u32) as u8` → clang 拒）。本次在源码侧绕开，codegen 没动。

两条都写进了 `docs/150` 的 M81/M82 后置修订，带最小重现。

## 6. 这一版**不是**什么

- **不是 1.0**：`docs/151` M96 的冻结判据是"自举定点 + 全门禁绿 + 手册定稿"，而 M68/M70/M71/M74
  四项内核侧接入仍未完成 —— 所以版本串**保留 `-alpha`**，稳定信号与 0.1.3.4 一致。
- **不是功能版**：没有新语法、没有新内建。
- **没有第三方复核**（M100）：冻结面内的判据都是**自证**的（见 `docs/160 §2` 第 1 条）。

## 7. 关于 tag

本提交**只落版本号与文档，没有打 tag** —— tag 是"发布"这个动作本身（M88：打 tag + 校验和），
而上面 §6 的冻结判据还没凑齐，且 `docs/151` M100 仍是"未达"。要发就单独走一次：

```bash
git tag -a v0.1.4-alpha -m "Loment 0.1.4 Alpha (Early Use Vision)" && git push origin v0.1.4-alpha
```

在此之前，审计报告里的 `tag:` 会显示 `(HEAD 上没有 tag)` —— 那是当前状态，不是缺漏。
