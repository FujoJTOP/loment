# Loment for VS Code

Loment（FUAI-OS 自研底层语言）的 VS Code 扩展：**语法高亮 + 语言服务 + 工具链命令**。

> 设计见 `docs/155-loment-kernel-handoff.md` §编辑器集成；语言与工具链见 `docs/143`/`docs/148`/`docs/150`。

## 能力

| 能力 | 来源 | 说明 |
|---|---|---|
| 语法高亮 | `syntaxes/loment.tmLanguage.json`（`.lomt`）+ `syntaxes/lom.tmLanguage.json`（`.lom`） | 关键字/类型/内建/能力域/注释/字符串/数字/运算符 |
| 补全 | `tools/loment_lsp.py` → `textDocument/completion` | 语言关键字、类型、以及本文件已声明的符号（fn/struct/enum/trait/const/capability） |
| 跳转定义 | 同上 → `textDocument/definition` | 跳到本文件内的声明行 |
| 诊断 | 同上 → `textDocument/publishDiagnostics` | 打开/编辑时跑类型检查，报在"问题"面板 |
| 格式化 | 同上 → `textDocument/formatting`（实现是 `lomfmt`） | 格式化由**语言服务**提供，扩展不自己起进程 |
| 生成 LLVM IR | 命令 → VS Code 任务 `tools/lomentc.py` | 产出 `<buildDir>/<stem>.ll` 并自动打开 |
| **编译成可执行** | 命令 → `loment build FILE`（或 `scripts/lomc.ps1`） | 产出原生可执行文件（Linux ELF） |
| **编译并运行** | 命令 → `loment run FILE`（或 `lomc.ps1 -Run`） | 编译后立刻跑，输出在终端 |
| 静态检查 | 命令 → `tools/loment.py diag` | 输出到终端 |
| 运行 `test_*` | 命令 → `tools/loment.py test` | 输出到终端 |
| 在 FujoOS 里运行 | 命令 → `tools/loment_boot.py` | 需要内核镜像与 QEMU（见 docs/149） |

命令面板里搜 `Loment:` 全部可见；`编译` / `编译并运行` 也在编辑器右键菜单里。

**`Ctrl+Shift+B`（运行生成任务）就是「编译当前文件」** —— 扩展注册了任务提供者，
`编译` 是默认生成任务。

> **F5 不能用来跑 Loment。** F5 在 VS Code 里恒等于「开始调试」，要它不弹
> "没有用于调试 Loment 的扩展"就得有一个**调试器** —— 而 Loment 现在没有。
> `loment dbg`（M75）只是**源码级符号化**（地址 ↔ 源行，走 DWARF 行表），
> 那是调试器的**地基**，不是调试器（没有进程控制 / 断点 / 单步）。
> 想用 F5 跑，只能自己在 `keybindings.json` 里把它改指过去：
>
> ```json
> { "key": "f5", "command": "workbench.action.tasks.runTask", "args": "Loment: 编译并运行" }
> ```

### 「编译」走哪条路

**这不是配置问题，是环境问题**，所以扩展自己检测（`src/build-cmd.js`，纯模块、可无头测）：

| 顺序 | 条件 | 用什么 |
|---|---|---|
| ① | 配了 `loment.toolCommand` | 装好的 Loment 命令：`<cmd> <args> build FILE [-o NAME]` / `run FILE`。**不需要 Python** |
| ② | Windows 且工作区里有 `scripts/lomc.ps1` | 开发树那条路：种子 + clang + WSL，出 `.ll` + `.elf` |
| ③ | 两条都没有 | **明确报错**并告诉你去填哪个设置 —— 不静默（点了没反应是最难查的一种） |

> 注意**仓库侧的 Python 没有"单文件出可执行"的命令**：`loment build DIR` 是**整目录增量构建**
> 且只出 IR。单文件出可执行只有上面那两条（`loment/tools/lomcli.lomt:606`）。

## 依赖

- 扩展**不需要**预装任何 npm 包（打包进 VSIX）；它需要：
- **Python**（PATH 上的 `python`）——语言服务与工具链都是 Python 实现；
- 一个**含 `tools/loment_lsp.py` 的仓库**：扩展从工作区逐级向上查找，也可以用设置
  `loment.serverPath` 直接指定绝对路径。

解释器固定为 `python`。若你只有 `py`/`python3`，改 `src/extension.js` 顶部的
`SERVER_COMMAND` 一处即可（**这是刻意的**：可执行位置只放字面量，配置字符串不参与命令构造）。

**语言服务与「编译」可以都不需要 Python**：前者用 `loment.serverCommand`，后者用
`loment.toolCommand`。

## 设置

| 项 | 默认 | 含义 |
|---|---|---|
| `loment.serverPath` | 空 | `tools/loment_lsp.py` 绝对路径；留空则自动向上查找 |
| `loment.serverCommand` / `serverArgs` | 空 | 改用别的命令起语言服务（**不需要 Python**），例：`wsl` + `["-e", "/home/<you>/.local/share/loment/lsp"]` |
| `loment.toolCommand` / `toolArgs` | 空 | 「编译 / 运行」用哪个 Loment 命令，例：`wsl` + `["-e", "/home/<you>/.local/share/loment/bin/loment"]` |
| `loment.enableLsp` | `true` | 关掉后只留语法高亮与命令 |
| `loment.buildDir` | `loment/build` | `.ll` / `.potato.json` 输出目录（相对工作区根） |

## 安装 / 打包

```bash
# 打包 (在仓库根)
python tools/vscode_ext.py --emit loment/build/loment-vscode.vsix
# 安装
code --install-extension loment/build/loment-vscode.vsix --force
```

打包前需在工作区里跑一次 `npm install --omit=dev`（把 `vscode-languageclient` 装进
`editors/vscode/node_modules`，VSIX 会把它一起打进 `extension/node_modules`）。
`.vsix` 的字节是**确定性**的（固定时间戳），因此可以进校验和清单。

## 无头验证（不需要 GUI）

```bash
python tools/vscode_ext_test.py
```

它做四件事：清单/语法文件结构校验（每个正则都能编译）、`server-path.js` 真的能找到服务、
**对真实语言服务做一次完整 LSP 往返**（initialize → didOpen → 补全 → 跳转 → 格式化）、
以及 `.vsix` 结构校验。
