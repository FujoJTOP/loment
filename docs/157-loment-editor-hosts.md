# 157 · 让编辑器认识 Loment（宿主清单 · 能做与做不到）

> 起因: 用户要求"让 ClaudeCode/ZCode 内置的编辑器认识 Loment"。
> 结论先说: **两个宿主的语言集都是构建期固定的, 用户侧没有注册点** —— 因此
> ① 编辑 Loment 用 VS Code（已完整支持）; ② 在**展示**场合用一条围栏约定让内置查看器着色;
> ③ 真正的修法是宿主方加语言, 请求写在 §4（可直接转交）。

## 1. 宿主逐个查（2026-09-11 实测）

| 宿主 | 显示引擎 | 语言集 | 用户侧能加语言吗 |
|---|---|---|---|
| **VS Code** | TextMate + Oniguruma | 扩展自带 | ✅ **已做**: `editors/vscode/` 两份语法 + LSP + 命令（docs/148 §0.1） |
| **ZCode 内置查看器** | **Shiki** (+ `@pierre/diffs` 做 diff) | 构建期固定（每种语言一个 asset chunk，如 `ada-D610HJ2J.js`） | ❌ 见下 |
| **Claude Code（TUI, `claude.exe` 2.1.266）** | **highlight.js**（终端渲染） | 编译进二进制 | ❌ 二进制里没有注册入口 |
| **Claude 桌面应用的内置查看器** | 未定位（MSIX 包 `Packages\Claude_pzs8sxrjxfjjc`，本体在受 ACL 保护的 `Program Files\WindowsApps`） | 未知 | **未验证**（没去动它） |

ZCode 侧的证据（都是只读检查）：

- 配置面只有两项：`~/.zcode/v2/config.json` → `provider`、`~/.zcode/cli/config.json` → `plugins`；
  `resources/config/default.json` 只有 feedback/community URL。**没有任何 editor/language/theme 键。**
- 插件清单的组件字段是 `commands` / `skills` / `hooks` / `mcpServers` / `agents`；
  官方配置指南明确写着 `channels` / `lspServers` / `outputStyles` / `settings`
  **"recorded but not executed"** —— 没有 `languages` 这一类。
- 高亮调用点：`createHighlighter({ langs: PU, themes })`（`PU` = 构建期语言表），
  取用是 `getOrCreate(lang, theme)` —— **按语言 id 从这张表里查**，查不到就没有语法。

Claude Code 侧的证据：`claude.exe` 里 `tmLanguage` / `shiki` / `prismjs` / `createHighlighter`
命中数全是 **0**，`highlight.js` 有命中（含它自己的安全警告文案）⇒ 它用 highlight.js，
而 highlight.js 的语言同样是打包时注册的。

## 2. 为什么不能"直接改 app 包"

ZCode 的 `app.asar` 头里**每个文件都带 SHA256 完整性哈希**：

```
"plaintext.js":{"size":318,"offset":"126512746","integrity":{"algorithm":"SHA256","hash":"..."}}
```

往里塞一份语法会被完整性校验拒掉（这也是 Electron 防篡改的设计），而且任何一次应用
升级都会覆盖。**不做这种改动。**

## 3. 现在就能做到的两件事

### 3.1 编辑用 VS Code（真支持）

`editors/vscode/`：两套 TextMate 语法（`.lomt` / `.lom`）+ LSP（补全/跳转/诊断/格式化）
+ 6 个命令；打包 `tools/vscode_ext.py`，体检 `--doctor`，验收 `vscode_ext_test` 6/6。
高亮本身用 VS Code 同款引擎逐 token 核过（docs/148 §0.1）。

### 3.2 展示用 `rust` 围栏（内置查看器立刻着色）

**Loment 是 Rust 的严格子集**，而两个内置引擎都认识 Rust。所以**展示**时（聊天回复、
`docs/*.md` 里的示例、issue/PR）用 ` ```rust ` 围栏，关键词/类型/字符串/注释/函数名这些
就会正确着色；只有 `capability` / `guard` / `excluded` / `revocable` 会当普通标识符。
GitHub 的 markdown 渲染同理（Linguist 也没有 loment），所以仓库文档也受益。

这条约定写进了工作区指令 `AGENTS.md`，任何在本仓库工作的 agent 都照做。
**它不是把 Loment 说成 Rust**：文件本身永远是 `.lomt`/`.lom`，只是给查看器一个
它能认的提示 —— 与"给终端设个配色"是同一类事。

## 4. 给宿主方的请求（可直接转交）

**要什么**：把下面两份 TextMate 语法加进内置高亮器的语言表（ZCode 是 Shiki 的
bundled languages；Claude 侧是 highlight.js 的注册表）。

| 文件 | scopeName | 扩展名 → 语言 id |
|---|---|---|
| `editors/vscode/syntaxes/loment.tmLanguage.json` | `source.loment` | `.lomt` → `loment` |
| `editors/vscode/syntaxes/lom.tmLanguage.json` | `source.lom` | `.lom` → `lom` |

**为什么便宜**：Shiki 本来就是 TextMate 引擎（`oniguruma` + `tmLanguage`），
这两份文件不用改一行就能当它的 LanguageRegistration（补上 `name`/`aliases`/`extensions`）；
`@pierre/diffs` 的高亮组件接受自定义 `langs`。所以这是**配置改动, 不是引擎改动**。

**验收**：打开任一 `.lomt`（例如 `loment/examples/demo.lomt`），`module`/`fn`/`u32`/`str`/
注释/字符串/数字/`capability`/`=>`/`?` 有颜色；`.lom`（如 `lom/fuc.lom`）同理。
等价的无头判据：用 Shiki/TextMate 跑一遍语法，统计"无 scope 占比" ——
本仓库实测 `.lom` 0.0%/0.1%，`.lomt` 3.4%–20.9%（其余是表达式里的裸标识符，正常）。
