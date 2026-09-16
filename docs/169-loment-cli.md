# 169 · Loment CLI：命令面与观感

> 版本 `0.1.4-pre1`（显示名 **Loment 0.1.4 Pre1**）
> · 实现 `loment/tools/lomcli.lomt`（**Loment 自己写的**）· 判据 `tools/loment_cli_test.py`
> · 上游：`docs/148`（工具链）、`docs/162`（发行包）、`docs/159`（去 Python 自举）

## 0. 一句话

`loment` 命令从 9 条扩到 **38 条**，并且**命令面本身是用 Loment 写的** ——
一个文件 `loment/tools/lomcli.lomt`（约 2300 行），链成 `bin/loment-cli`，
bash 与 batch 两个启动器**各加一行转发**就完事。

## 1. 为什么写在 Loment 里，而不是启动器的 shell 里

启动器有**两份**：`bin/loment` 是 POSIX shell，`bin/loment.cmd` 是 batch（Windows 上没有
bash 可用，用户双击 / 从 cmd 里敲走的是这一份）。命令要是写在启动器里，就得**写两遍并
保持同步** —— 而 batch 写 30 多条命令是灾难。

写成一个 Loment 程序则只有一份实现。这不是新路子：`lomfmt` / `lomdoc` / `lomelf` /
`lomstatus` / `lompkg` 已经是这个形状（docs/159 Stage 3），本文件只是把**命令面**也搬了过来。

顺带白拿两件事：

- **它进得了自举链**：同一个文件由 stage1 编出来，参考实现与自举镜产出的 IR **逐字节相同**
  （判据里有这条）。也就是说"装包要编出 CLI"这件事不依赖 Python。
- **两个平台的 CLI 是同一个二进制行为**：同一份源码，ELF 与 PE 各链一遍，命令输出一致。

## 2. 命令面（38 条）

`loment help` 打总览（分区 + 对齐 + 上色），`loment help <命令>` 打单条，
`loment commands` 每行一个名字（给补全用）。

| 分区 | 命令 |
|---|---|
| 编译与运行 | `ir` `check` `build` `run` `version` `lsp` |
| 源码工具 | `fmt` `doc` `skill` |
| 读源码 | `cat` `stat` `count` `fns` `tokens` `grep` `todo` `hash` |
| 工程 | `ls` `tree` `new` `examples` `example` |
| 语言速查 | `syntax` `builtins` `types` `keywords` `caps` `codes` `explain` `cheat` |
| 本机 | `tools` `where` `env` `doctor` `about` `color` `commands` `help` |

前两组（9 条）由**启动器自己**处理并转发给对应的工具（`ir`/`check`/`build`/`run` →
`loment-driver` + `loment-lomelf`，`fmt` → `loment-fmt`，…）。其余 29 条落在 `loment-cli` 里。

**`lib` / `pkg` 故意不在这张表里。** 库系统（docs/168）目前只在**源码仓库**侧可用
（`python tools/loment.py lib ...`），发行包里没有 Python 也就没有它 —— 列在 `help` 里
而敲下去只会得到"未知命令"，那是骗人。判据
`test_every_catalog_command_is_actually_dispatchable` 专门守这条（见 §5）。

**`lompi` 也不在这张表里，而且理由不同 —— 这是条硬边界。** `lompi` 是 Loment 库的包管理器，
**随 Loment 一起安装**（装完 `loment`，`lompi` 就在 PATH 上，不需要单独装），但它
**不属于 Loment 官方工具**：它不编 Loment、不读源码树、是**另一个命令**。

所以：`loment help` / `loment commands` 里**不出现它**，`loment <任何东西>` 也**不转发**给它 ——
`loment` 的命令面只描述 `loment` 自己。要用 lompi 就直接敲 `lompi`，它有自己的用法与文档。
**不要**因为"它随包一起装"就把它挂进这张表：装在一起 ≠ 是同一件工具的部件。

## 3. 观感（这一节是需求，不是装饰）

- **分区**：六个分区各带标题，不是一坨平铺的列表。
- **对齐**：命令名与参数各占固定列。**列宽按显示宽度算，不按字节数** —— CJK 一个字符占
  两列，按字节补必然歪（`bin 目录` 是 10 字节但只占 7 列）。
- **上色**：默认开。`--no-color` / `-C` 关（位置任意），`loment color [on|off]` 看/改当前策略。
- **全部输出纯 ASCII**（2026-09-15 用户实测报的乱码之后定的，见 §3a）。连 `✓` `✗` `→`
  这些**装饰**也算 —— 它们同样是非 ASCII，936 控制台下一样乱码，所以一律换成 `[ok]`
  `[--]` `MISSING` `->`。原先这里写的是"装饰用 Unicode、坏掉也不影响读"，**那个取舍是错的**：
  坏掉的不是装饰，是整行 —— 中文 Windows 的控制台会把整段 UTF-8 按 GBK 解。
- **错误走 stderr 且是红的**，不与非零退出码打架。

**诚实边界**：**没有 TTY 探测** —— 管道里的输出同样带颜色，要干净就加 `--no-color`
（PE 上没有 `ioctl`，ELF 上做得到但两边行为会分叉，所以索性不做，宁可一致）。

### 3a. 为什么必须纯 ASCII（这一条是硬约束，不是口味）

Windows 上 PE 把字节**直接写进控制台**，而控制台按**当前代码页**解 —— 中文 Windows 是
**936(GBK)**，于是 UTF-8 的中文被解成乱码：

```
"源码统计" 的 UTF-8 字节 e6 ba 90 e7 a0 81 ... 按 GBK 解出来是  婧愮爜缁熻
```

**垫片没有 `WriteConsoleW`**，所以程序这边没有任何补救手段（不能"告诉控制台用 UTF-8"）。
ASCII 是唯一**在任何代码页下都解码成同一个结果**的集合 —— 这就是全部理由。

代价要如实说：**帮助与速查页从中文变成了英文**。这不是"顺手国际化"，是被 936 逼的。
仓库的文档、指南、注释照旧是中文（那些是**读文件**，走的是 UTF-8 通路，与控制台无关）。

**还剩一条路不在这条的覆盖里**：`loment skill --print` 是**由启动器 `cat`/`type` 一个文件**，
而那份指南是中文 —— 在 936 控制台上同样会乱码。它没改，因为把它改成英文等于把指南的语言
换掉（那是**读文件**的东西，走 UTF-8 通路没有问题；只有"把它的字节倒进控制台"这一步会）。
要走这条路就用 `loment skill`（只打**路径**，让 agent 去读文件），或者先把控制台切到
UTF-8（`chcp 65001`）。

**这条有判据守**：`test_every_command_outputs_pure_ascii` 逐条跑**每一条**命令，检查
stdout+stderr 全是 ASCII —— 不是抽查，是全扫。加一个新命令而忘了这条，它会立刻红。

## 3b. 自定义命令：`loment foo` → `loment-foo`

**官方命令 38 条，但命令面不止 38 条。** 用户（或用户装的软件）可以在 `PATH` 上放一个叫
`loment-<名字>` 的可执行文件，于是 `loment <名字> ...` 就能用 —— 与 `git` 的做法一样：

```bash
# PATH 上有 loment-git，就有了 `loment git`
loment git status        # 转发给 loment-git，参数原样
```

规则（**两个启动器必须一致**，判据在 `loment_cli_test`）：

| 情形 | 行为 |
|---|---|
| `loment <名字>` 且 `PATH` 上有 `loment-<名字>` | **原样转发**：`shift` 掉名字，后面的参数一个不动、退出码原样带出 |
| `loment <名字>` 但 PATH 上没有 | 交回官方 CLI —— 那就是"未知命令"（红字 + 退出 2） |
| `loment <官方命令>` | **永远走官方实现**，PATH 上有同名的 `loment-<官方命令>` 也顶不掉 |

三条设计取舍，记下来免得下次有人"优化"掉：

- **转发而非委托给 CLI**：官方 CLI 是一份**编译好的** Loment 程序，它只能看见自己那 38 条。
  要"能长出新命令"就必须在**启动器**这一层做 —— 那是唯一看得见 `PATH` 的地方。
- **官方优先**：否则装个 `loment-version` 就能把版本号换了，`doctor` 与判据全都失去意义。
  这个顺序也让"注册自定义命令"永远不会破坏已有脚本。
- **没有注册表**：约定就是**文件名**。没有配置文件、没有中心目录 —— 装了就生效，卸了就没了。
  （与那条"配置是 Loment 源码"的口味一致：能用文件系统表达的不引入新格式。）

## 4. 实现要点

- **只用 8 个跨平台 syscall**：`read` `write` `close` `brk` `exit` `getdents64` `openat`
  `newfstatat`（与 docs/167 §5 同一份清单）。所以同一份源码在 ELF 与 PE 上行为一致。
- **自定位靠 `argv[0]`**，不读环境变量：`bin/` 目录、`share/loment/` 都是从自己所在路径
  算出来的 —— `doctor` / `where` / `env` / `examples` / `skill` 全靠这条。
- **单块 brk 内存 + 命名偏移**（与 `lompkg.lomt` 同法）。Loment 没有可变全局，也没有
  out 参数，所以跨函数共享的状态一律靠这块内存。
- **PS 上 `newfstatat` 只填 `st_mode`**（实测 `st_size` 恒为 0），所以**文件大小一律靠
  "读到底数出来"**，不看 stat。
- **argv 是 NUL 分隔的**，不能用 `strlen` 数长度（那样只会得到 `argv[0]` 的长度）。
  真实字节数记在全局格里，`arg_at`/`split_argv` 都读它。
- **全局开关要"紧凑掉"**：`loment --no-color grep P F` 里的 `--no-color` 会在分发前从
  argv 里就地左移删除，这样所有"按下标取参数"的命令逻辑都不用改。

## 5. 踩出来的坑（都进了上表或判据）

1. **落出非 void 函数 → SIGILL**。`ls_dir` 为了能用 `return 0;` 早退被标成 `-> u32`，
   但正常路径没有 `return` —— checker 放行，**运行期直接崩**（测出来是 `Illegal
   instruction`）。凡是 `-> T` 的函数，末尾必须有一条 `return`。
2. **`let mut x` 里的 `mut` 不是关键字**，是普通标识符（`loment/selfhost/checker.lomt`
   里就有个变量真叫 `mut`）。写 `let mut len: u32 = ...` 等于"声明一个叫 `mut` 的变量、
   后面再跟个 `len`"，是真语法错。本地变量不需要 `mut`。
3. **遮蔽参数会让自举镜多出一个 alloca** —— IR 与参考不再逐字节相同。
   `dispatch` 里原本 `let argc = strip_flags(...)` 遮蔽了同名参数，比出来差一条
   `%argc.addr = alloca i32`；改名即可。
4. **`ptr` 不会隐式变成 `str`**，也没有"由 ptr+len 造 str"的内建。算出来的文本（路径、
   版本号）一律走 `wbuf`，字面量才走 `wstr`。
5. **目录要先读完整层再递归**：`getdents64` 缓冲是全局共享的，边读边递归会被子目录的读
   覆盖（`lompkg.lomt` 当初踩过，docs/168 §5）。每层有各自的按深度切片的缓冲。
6. **缓冲别名**：`read_arg` 拿 `M_P1` 当路径缓冲，而 `grep` 的 pattern 也在 `M_P1` ——
   于是 pattern 被文件名覆盖，**搜什么都搜不到**。判据里的 `grep` 用例就是钉这个的。
7. **`--no-color` 曾经把命令本身吃掉**：全局开关扫描到就把 `argv[1]` 当命令，于是
   `loment help --no-color` 变成了"解释 `--no-color` 这条命令"。改成先紧凑再分发。
8. **dispatch 把剥离前的 `argc` 传给了命令**：`loment grep PAT --no-color` 时命令拿到旧的
   计数，索引算到缓冲外，报"打不开 "。改成传剥离后的。
9. **结尾空行不算一行**：文件以 `\n` 结尾时，行遍历会把最后那个空段也数成一行。
   按 `cat -n` 的惯例改为只在 `ls < n` 时收尾。

## 6. 判据（32 条，`tools/loment_cli_test.py`，进门禁）

| 组 | 判的 |
|---|---|
| 自举 | 链出的二进制可跑；**自举镜编 lomcli 的 IR 与参考逐字节相同**（走 `loment_dist.build_stage1`，与打包同一条路） |
| 目录 | 命令 ≥ 30 条；`help` 提到每一条；**每一条都真能分发**（不是"查得到、敲了说未知命令"） |
| 观感 | `about` 默认带 ANSI、`--no-color` 之后一条不剩；开关位置任意时不吞命令 |
| **纯 ASCII** | **每一条命令**的 stdout+stderr 都必须是纯 ASCII（936 控制台下非 ASCII 必乱码，见 §3a） |
| 真算 | `hash` == `hashlib.sha256`；`stat` 的字节数/行数与 Python 自己数的相同；`cat` 行数与 `nl` 同口径；`grep` 行号正确且"没命中退 1 / 缺参数退 2"；`count`/`fns`/`tokens` 对已知文件 |
| 文件 | 目录标记 `/`、`tree` 缩进体现层级；`new` 写出的骨架**过参考实现的 check + emit**；`new` 不覆盖已存在文件 |
| 自定位 | `version` 读 `share/loment/version`；`doctor` 缺组件红退 1、齐了绿退 0（**有分辨力**）；`where` 解析路径、不认识的名字退 2 |
| 防漂移 | 两个启动器按名处理的命令集**互相一致**且**都在目录里**；两个启动器都是纯 ASCII；兜底是**转发**给 loment-cli 而不是各写一份 usage |

**证伪过**（把判据自己弄坏，确认它会红）：给 batch 启动器塞一个目录里没有的命令 → 抓住；
把 bash 兜底从"转发"改回"自己打 usage" → 抓住；往启动器里加一个中文字符 → 抓住；
目录门槛调高一条 → 抓住。另外 §5 的第 3、7、8、9 条都是**判据先红、修完才绿**的。

## 7. 复现

```bash
python tools/loment_cli_test.py                     # 32 条判据 (本机原生跑生成的 PE)
python tools/loment_dist.py --emit                  # 四个产物; bin/loment-cli 从 lomcli.lomt 编出来
loment help                                         # 装完之后看总览
loment commands                                     # 拿命令名列表
loment --no-color codes                             # 不要转义序列
```

## 8. 还没做的（诚实清单）

- **单条命令的手册页只覆盖了 13 条**（`ir`/`check`/`build`/`run`/`fmt`/`grep`/`new`/
  `ls`/`tree`/`color`/`explain`/`skill`/`help`）。其余命令 `help <名>` 会给一句"没有更详细
  的手册页"并指回总览 —— 不装样子。
- **没有 TTY 探测**（§3）。
- **`tokens` 是词法层近似**（按字节扫标识符/数字/字符串/注释），不是真词法器；
  要精确的词法结果得用 `lomc`。
- **`grep` 是子串匹配，不是正则**。
- **`tree` 深度上限 16 层**、单目录最多记 256 项（超了静默截断）—— 护栏，不是特性。
