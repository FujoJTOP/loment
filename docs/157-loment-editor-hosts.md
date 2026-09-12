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

## 3.3 让 **Windows 自己**认识 Loment（文件类型注册，2026-09-12）

用户侧的抱怨是"打开方式里只有一次性的'仅一次'" —— 那是 Windows 对一个**没注册过的**
扩展名的默认表现（对话框只给一次性的候选）。修法是把 `.lomt`/`.lom` 注册成真正的
文件类型（**只写 HKCU，不需要管理员**）：

```bash
python tools/loment_filetype.py --status                # 看现状 (只读)
python tools/loment_filetype.py --register --dry-run    # 打印将要写的注册表项
python tools/loment_filetype.py --register              # 写 (自动找 VS Code; 可用 --editor 指定)
python tools/loment_filetype.py --unregister            # 撤销 (只删本工具建的键)
python tools/loment_filetype.py --emit-icon             # 重新生成 editors/loment.ico (需 Pillow)
```

写了什么（`EXT_MAP` 是单一真源，门禁核对的就是它）：

| 键 | 作用 |
|---|---|
| `HKCU\Software\Classes\.lomt` 默认值 = `Loment.Source` | 扩展名 → ProgID（没有 `UserChoice` 时 Windows 就用它） |
| `…\.lomt\OpenWithProgids` = `Loment.Source` | **"打开方式"里常驻**（这就是"不再只有仅一次"的那一条） |
| `…\Explorer\FileExts\.lomt\OpenWithProgids` | 双保险：Explorer 的"更多应用"也看这里 |
| `HKCU\Software\Classes\Loment.Source` | 类型名 `Loment 源文件` + `Content Type` + `PerceivedType` |
| `…\Loment.Source\DefaultIcon` = `editors/loment.ico,0` | 资源管理器里的 Loment 图标 |
| `…\Loment.Source\shell\open\command` = `"<Code.exe>" "%1"` | 双击进 VS Code（扩展已装 ⇒ 有高亮/LSP） |

`.lom` 同样一套（ProgID `Loment.L0`，类型名 `Loment L0 声明文件`）。

**实测证据**（2026-09-12，本机）：注册后 `HKCR\.lomt` 合并视图显示 `Loment.Source` +
`Loment 源文件`；`cmd /c start "" x.lomt` 后 **VS Code 窗口标题 2 秒内变成
`x.lomt - Visual Studio Code`**（即 Windows 通过本 ProgID 解析并启动了编辑器），
文件同时进了 `RecentDocs\.lomt`。

**两个坑（写在这里省得再撞）**：

1. `FileExts\.lomt\UserChoice` 在 Win10+ 带**哈希保护**，程序改不了 —— "设成默认"这一步
   只能由人在"打开方式"里点一次"始终"。好在本工具把扩展名默认值写成了我们的 ProgID，
   所以**没设 UserChoice 时 Windows 也会用我们**（实测双击即可）。
2. `assoc`/`ftype` 这两个**老命令不读 `HKCU\Software\Classes`**，会报"没有文件关联"——
   这是它们的限制，不是注册失败。要核对请用 `reg query "HKCR\.lomt"` 或本工具的 `--status`。

撤销干净：`--unregister` 删掉本工具建的键与值（不动别人的），之后"打开方式"回到注册前的样子。

## 3.4 在 Windows 上编译/运行一个 `.lomt`（**不用 Python**，2026-09-12）

编辑器解决了"看"，这一步解决"跑"：

```powershell
powershell -File scripts/lomc.ps1 loment/examples/user_hello.lomt          # 只编译
powershell -File scripts/lomc.ps1 loment/examples/user_hello.lomt -Run     # 编译并运行
powershell -File scripts/lomc.ps1 <file.lomt> -OutDir loment/build
```

只用三样东西：**种子**（`loment/build/selfhost_driver.ll`，参考实现发射的驱动 IR，已提交）
+ **clang**（地基语言）+ **WSL**（执行 Linux ELF）。stage1 缓存在 `loment/build/stage1.elf`，
种子变了才重建。产物 = `<名字>.ll` + `<名字>.elf`（x86_64 Linux 静态可执行）。

**实测**（2026-09-12 本机，全程无 Python）：

```
[1/4] clang(seed) -> stage1        [2/4] stage1 compiles ... -> user_hello.ll
[3/4] clang link -> user_hello.elf [4/4] running (WSL): M67 RESULT: PASS loment-user
[lomc] exit code 0 · IR 1.6KB -> ELF 1.5KB
```

多单元（`use` 由驱动自己解析）同样通过：`all_loment.lomt` → `M78 sum=42 double=84 max=84
fib=55 / M78 RESULT: PASS all-loment`。

**边界**：目标平台是 **Linux ELF**（Loment 程序走 Linux 系统调用），所以 `-Run` 用 WSL；
不做 Windows 原生目标（那需要另一套运行时 ABI）。种子重建才需要 Python
（`tools/loment_seed.py --emit`）—— 平时不需要。

**门禁**：`loment_seed --script-ok` 现在检查**两个启动脚本**
（`loment/bootstrap.sh` 与 `scripts/lomc.ps1`）：命令位置不许出现
python/perl/ruby/node/cargo/rustc/gcc，且必须是 LF。注释里的提法不算（脚本自己会用
`python tools/loment_seed.py --emit` 这句提示重建种子）。

**踩过的坑**：`.ps1` 里写中文在 Windows PowerShell 5.1 下会因"无 BOM 的 UTF-8 按 ANSI 读"
变成乱码并导致**解析错误** —— 所以 `scripts/lomc.ps1` 是纯 ASCII 的，中文说明留在这里。

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
