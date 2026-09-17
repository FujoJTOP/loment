# 仓库工作约定（FujoOS）

> 工作区级指令。ZCode 读 `<repo>/AGENTS.md`，Claude Code / DSH 也认同名文件。
> 只写"从代码本身看不出来"的约定；判据与进度在 `docs/`。

## 展示 Loment 代码时用 `rust` 作围栏语言

**背景**：Loment 的 `.lomt`（L1）与 `.lom`（L0）**不是任何内置查看器认识的语言**：
ZCode 的内置查看器用 Shiki（语言集构建期固定）、Claude Code 的 TUI 用 highlight.js
（同样固定）、GitHub 的 markdown 用 Linguist（同样没有 loment）。它们都没有用户侧注册点
（调查与证据见 `docs/157`）。

**做法**：在**展示**场合 —— 聊天回复、`docs/*.md` 里的示例、issue/PR 描述 —— 用
` ```rust ` 作围栏。Loment 是 Rust 的**严格子集**，关键词/类型/字符串/注释/函数名这些
着色基本一致；只有 `capability` / `guard` / `excluded` / `revocable` 会显示成普通标识符。

**边界**（别做过头）：

- 这只是**显示层**的提示。**文件本身永远是 `.lomt` / `.lom`**，不要改扩展名、不要按
  Rust 语法去写；
- 引用语言的名字时写 Loment，不要说成 Rust；
- 引用的代码块若是 **Rust/C/Python** 文件，照旧用 `rust`/`c`/`python` —— 区分不出
  "真 Rust" 与"为了着色"时，看内容：源文件路径是 `.rs` 就是 Rust。

**真正的编辑器支持**在 `editors/vscode/`（TextMate 语法 + LSP 补全/跳转/诊断/格式化 +
6 个命令）。要**改** Loment 就用 VS Code；ZCode/Claude 的内置查看器只看不改。

## L0 接口面有"冻结阈值"：`lom/*.lom` 是跨线契约，不是内部实现

**背景**：Loment 自身仍在开发，**实现层的高 churn 是预期的、也是允许的** ——
`tools/lomentc.py`（近 2 天 9 次改动）、checker 行为、IR 细节、自举链路，随你改。
但 `lom/*.lom`（L0 接口层）**不是实现**，它是**别的线要依赖的契约**：

- `FujoOS-compat` 的内核侧已经在依赖它：内容信任台账的原语
  `qual_feed = 33556`（`0x8314`）/ `qual_seq = 33557`（`0x8315`）与准入闸
  `dom_admit = 33555`（`0x8313`）就登记在 `lom/fuai.lom` 里，由
  `lom_spec_emit.py` 生成两份 `spec.json`，并被 `fuai_contract` 门禁逐条核对；
- 事实依据：**`lom/fuai.lom` 自 2026-09-09 建立后从未改动**，而同期编译器改了 9 次 ——
  这条"接口稳、实现动"的分界是当前跨线协作唯一的支点。

**约定（改动前先看这条）**：

1. **改 `lom/*.lom` 视为"对外契约变更"**：要单独一次提交，commit message 写清"谁依赖它、
   变了什么、下游要不要动"；不要在重构编译器时顺手改它。
2. **新增/删除原语 = 契约升版**：内核侧有针对枚举计数的断言
   （如 `lomc_test` 的 `Opcode` 变体数），改动必然触发下游假失败 ——
   预期如此，属于**该看见的信号**，不要靠"把断言放宽"消掉。
3. **不要为了内核方便而改 L0**：优先在内核侧做适配层（`docs/165` 的 L2）；
   反过来同样成立 —— 内核侧不直接吃语言线的生成产物。
4. 若确要变 L0：在 `docs/` 留一行"下游影响"，并知会 compat 线。

**不做**：不在语言冻结前把 Loment 产物提交成内核依赖、不把 Python 引入内核构建链（见 `docs/165`）。

## 改语言面要付双倍的工：手写一条提交、生成一条提交

**背景**：语言面（lexer / parser / checker / codegen + 诊断码 + 内建表）在仓里有**两个实现**
（`tools/lomentc.py` 与 `loment/selfhost/*.lomt`），必须逐字节一致（`docs/158` §5）。
每次改完，`loment/build/selfhost_driver.ll`（46 KB 的自举种子）会被 `--emit` 重生成，
而 SSA 编号会整体位移 —— 实测 `extern fn` 那两次提交里它以 **3858 行占了 diff 的 93%**，
把真正要看的 **153 行手写**淹掉（测量见 `docs/176` §1）。

**约定**：

1. **机械产物单独成一条提交**，消息里写明"只有生成物"。手写那条（源 + 文档 + 判据）与它
   分开，这样 review 只需要看前者。
2. **种子永远重新生成，不手工合并**。它在 `.gitattributes` 里标了 `-diff`，于是两边都改了
   它时是**整file 冲突** —— 那是对的，逼你跑 `python tools/loment_seed.py --emit`
   （`loment_seed_test` 会告诉你它是否过期）。
3. 判据 `loment_seed_test::test_seed_is_marked_generated` 钉住"种子被标成生成物"，
   防止这条纪律悄悄回退。**能藏的前提是它可复现** —— 上面那条"种子 == 参考实现的产物"
   就是那个前提，两条是一对。

## 写 Loment 程序之前，先读那份 agent 指南

**装好 Loment 工具链之后**（`loment version` 能跑），写或改 `.lomt` 之前先读指南：

- 有本仓库时：**`.claude/skills/loment/SKILL.md`**
- 只有安装包时：**`<前缀>/share/loment/skill/SKILL.md`**（同一份）

内建函数表、语法要点、E1–E17 错误码、以及包内命令 `ir/check/build/run/fmt/doc/lsp`
全在里面，**自足**，不依赖本仓库其它文件。

**正本只有一份**（就是那个文件）—— 别处都只是指针或同字节拷贝。复制出第二份必然漂。

**它不是 Claude 专用的**：`.claude/skills/` 只是 Claude Code 的自动发现路径，
其它 agent（ZCode / Codex / Cursor …）按各自约定读不到它，所以安装器会把**指针**
写进它们认的用户级文件（Codex 是 `~/.codex/AGENTS.md`），并设 `LOMENT_SKILL` 环境变量。
