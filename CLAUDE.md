# 仓库工作约定（FujoOS）

> 工作区级指令。Claude Code 读 `<repo>/CLAUDE.md`；内容与 `AGENTS.md` 一致
> （ZCode / DSH 读 AGENTS.md）。改约定时两个文件一起改。

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
