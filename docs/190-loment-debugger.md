# 190 · Loment 调试器 —— DAP 适配器与 `ptrace` 后端

> 2026-09-18。上游：`docs/148`（`loment dbg` 的源码级符号化，M75）· `docs/155`/`docs/157`
> （编辑器集成）· `docs/159`（无 Python 自举）。
>
> 用户要的是一句话：**「做调试器」**。`docs/148` 那份只是调试器的**地基**
> （地址 ↔ 源行），没有进程控制、没有断点、没有单步。这份文档记的是把地基接成一个
> 真调试器的过程 —— 以及路上撞到的**四个真 bug**，其中三个是"看着能用、一走就错"。

## 0. 做出来的东西

| 层 | 文件 | 是什么 |
|---|---|---|
| 协议 | `tools/loment_dap.py` | DAP 适配器（stdin/stdout 的 `Content-Length` 帧） |
| 后端 | 同上 | `ptrace` 的 ctypes 封装 + 行表 + 目标进程 |
| 编辑器 | `editors/vscode/src/debug-cmd.js` | 纯 Node：怎么起适配器（三层检测） |
| 编辑器 | `editors/vscode/package.json` | `debuggers` / `breakpoints` 贡献 + 三项设置 |
| 编辑器 | `editors/vscode/src/extension.js` | `DebugAdapterDescriptorFactory` —— F5 从这里进 |
| 判据 | `tools/loment_dap_test.py` | 无头跑**四次真会话**，比对行号与输出 |
| 判据 | `tools/vscode_ext_test.py` | 无头跑一次 `activate()`，看该注册的都注册了 |

**它跑在哪**：后端是 `ptrace`，所以**适配器必须跑在 Linux 侧** —— Windows 上走
`wsl -e python3 <仓库>/tools/loment_dap.py`。被调试的 ELF 要拷到 Linux 原生路径
（`/tmp/`）再 `execve`：DrvFs（`/mnt/...`）上不能直接执行。

**编译谁做**：适配器自己 `import lomentc` 现编，`--emit-llvm(debug=True)` → `clang -g`
（Windows 侧的 `clang.exe` / `llvm-objdump.exe`，因为只有它那边有 LLVM）→ 链接。
所以**调试用的 ELF 与 `loment dbg` 看的是同一条链路**，不会出现"调试器看到的行号和
`dbg` 给的不一样"。

## 1. 四个真 bug —— 都是"能停，但一往下走就错"

这一节是本文档的主要价值。四条都是**实测撞出来的**，不是推的。

### 1.1 `ptrace` 的返回值被截成 32 位（ctypes 的默认 `restype`）

**症状**：断点能落上、能停在那一行、`stackTrace` 也对；一按 `continue`，
被调试程序**段错误**（`signal 11`）。

**根因**：`ctypes.CDLL` 的 `restype` 默认是 `c_int`（32 位），而 `ptrace` 返回 `long`。
`PTRACE_PEEKTEXT` 读的是**一个机器字 8 字节**，被截成 4 字节再符号延展；而
`PTRACE_POKETEXT` 写回去的是**完整 8 字节**。于是每设一个断点：

```
本来:  [原 8 字节]
peek:  [低4字节] -> 截断 -> 符号延展成 [低4字节 | 0x00000000 或 0xFFFFFFFF]
poke:  写回 [低4字节 | 那儿原本的 4 字节没了]
```

**断点后面那 4 个字节被静默改写**。`0xCC` 还在，所以断点照样响；但那 4 个字节上的
指令已经坏了 —— 单步/继续过去就是段错误。

**修法**（`Ptrace.__init__`）：

```python
self.libc.ptrace.restype = ctypes.c_long
self.libc.ptrace.argtypes = [ctypes.c_ulong, ctypes.c_ulong,
                             ctypes.c_void_p, ctypes.c_void_p]
```

**这条值得记的地方**：它**不报错**。`c_int` 截断在 ctypes 里是合法行为，没有警告；
而症状（段错误）出现在**别处**（被调试程序里），第一眼看上去像编译器或链接的毛病。
一个"能停但不能继续"的调试器，比一个根本起不来的调试器难查得多。

### 1.2 `wait_stop` 与 `_resume` 对"停在哪儿"各有一套说法

**症状**：`continue` 之后**又停回同一个断点**，一遍一遍（看着像死循环，其实每次都真的停）。

**根因**：`ptrace` 命中 `int3` 时 `rip` 指向断点**后一格**。`wait_stop` 为了报行号把
`rip` 拨回了断点地址；而 `_resume` 是按"`rip - 1` 才是断点"找的 —— **它不认得自己拨回去
的那一格**。于是断点从没被撤掉，`0xCC` 还在原地，一继续立刻又咬自己一次。

**修法**：把"此刻停在哪个地址"变成**一个字段**（`Target.stop_addr`），
`wait_stop` 填它、`_resume`/`_frame` 读它，两边不再各算各的：

```python
def _bp_addr(self):
    if self.stop_addr is not None and self.stop_addr in self.bps:
        return self.stop_addr
    rip = self.pt.getregs(self.pid).rip
    return rip - 1 if (rip - 1) in self.bps else None
```

**还有一条连着的**：撤点之后**必须真单步一条指令再装回去**。只撤不单步就继续，
那条真指令会被执行、但**断点已经不在**了 —— 之后走到这里再也不响（一个"绿着但永不触发"
的断点，`docs/167` 那种静默）。所以 `_leave_breakpoint()` 的顺序是
**撤点 → `rip` 拨回 → 单步执行掉它 → 装回去**，不能反（先装回去再单步，取到的就是 `0xCC`）。

### 1.3 `llvm-objdump -l` 只给一部分指令标行号

**症状**：单步之后帧上时报第 1 行（一个假装的兜底），而真值是 13。

**根因**：`llvm-objdump -l` 把源位置打成 `; <路径>:<行>` 的注释行，**不是每条指令都有**。
函数的**序言**（`subq`、把参数存栈那几条）和结尾的填充（`nopw`）就都没有。于是：

* `_start` 的序言落到**上一个函数** `exit` 的最后一个标注上（第 17 行）—— 报得牛头不对马嘴；
* `write_str` 的序言一条标注都没有 —— `at()` 返回 `None`，帧退回 `line: 1`。

**修法**（`LineTable`）：

1. **在每个函数标签处把"当前源行"清空** —— 不让上一个函数的标注漏到下一个函数的序言里；
2. **按函数边界补缺口**：函数内部哪条指令没行号，取同一函数里**最近的那条有行号的**
   指令的行；
3. **两张表分工**：`rows` 只放**真被标注过**的地址（断点只落在这些上面），
   `at_map` 放补过之后的全部指令（只给"我现在停在哪一行"用）。
   —— 补行**不能**让"这一行能不能设断点"变松，否则断点会落在没有代码的行上。

顺带把 `fn_at` 接上了 `llvm-objdump` 的符号标签（`0000000000201210 <write_str>:`）——
之前它退回**文件基名**，于是调用栈上写的是 `user_hello.lomt` 而不是 `_start` / `write_str`。

### 1.4 `activate()` 里的 `ReferenceError` —— 一整条链一个判据都没跑到

这条不在调试器里，是**做调试器时顺手撞出来的**：`extension.js` 的 `activate()` 末尾有
一句 `if (root)`，而 `root` 在那个作用域里**没有定义**。JavaScript 到那一行抛
`ReferenceError`，于是它**后面**的任务提供者、语言服务、调试适配器**全都没注册** ——
用户在编辑器里看到的只是「按了没反应 / 没有用于调试 Loment 的扩展」。

而当时的判据**全绿**：清单结构合法、语法正则都编得过、`build-cmd.js` 的六个分支都对。
**它们一条都没跑过 `activate`。**

**修法**：`tools/vscode_ext_test.py` 里加 `test_vscode_extension_activates` —— 用一个
假的 `vscode` 模块（`Module._load` 拦下来）**真跑一次 `activate()`**，然后断言：

* 清单里声明的**每一条命令**都真的 `registerCommand` 了（少一条 = 用户点下去
  "command not found"，而清单看着完全正常）；
* `loment` 的**任务提供者**与**调试适配器工厂**都注册了，且类型名与清单里的
  `taskDefinitions[].type` / `debuggers[].type` **一致**（不一致时 F5 照样弹那个框）；
* 任务提供者给出的两条任务里，「编译」挂着 `TaskGroup.Build`（`Ctrl+Shift+B` 靠它挑）。

**验过它会红**：把 HEAD 里那份坏的 `extension.js` 喂给同一个脚手架，
拿到的是 `ReferenceError: root is not defined`。判据测过自己会红，才算判据。

## 2. 单步：按**行**走，不是按指令

`stepIn` / `next` / `stepOut` 三个动作都走"单步到行（或函数）变了为止"，上限
`_STEP_CAP = 4096` 条；**走满了就停在原地报出来，不假装**。

为什么不能只单步一条指令：`user_hello.lomt:22` 那句 `write_str(1, msg)` 编译成
**四条**（`movq/movq/movl/callq`）。只单步一条，行号没变 —— 用户按 F11 看到指针
纹丝不动，会以为调试器坏了。

三者的区别（**各自都是真的会坏的**）：

| 动作 | 停在哪 | 判据钉的数 |
|---|---|---|
| `stepIn` | 源行变了 —— **钻进**被调函数 | 22 → **13**（`write_str` 的函数体） |
| `next` | 源行变了 **且**没停在别人的函数里 | 22 → **23**（`exit(0);`） |
| `stepOut` | 函数变了（回到调用者） | 没单独钉（见下） |

**`next` 的坑**：不能用"在别的源行上设临时断点"那一招 —— 被调函数的**第一行**
也是"别的源行"，于是 `next` 与 `stepIn` 变成一回事（实测就是这样）。所以 `next`
必须知道**函数边界**，而函数边界只能从 `llvm-objdump` 的符号标签拿（§1.3 顺带接上的）。
`tools/loment_dap_test.py::test_next_crosses_a_call` 就钉这一条：两个动作
**必须停在不同行**，且 `next` 落在 **22 + 1**。

**`stepOut` 是老实承认的近似**：真做法是读返回地址、在那儿设临时断点，那要能可靠地
找到帧；这里没有帧信息，就单步到函数变了为止 —— 走满上限就停在原地。文档与代码里
都这么写，不做一个"看着像已经出来了"的假动作。

## 3. 一些刻意的选择

* **不支持的都报 `false`**：`supportsConditionalBreakpoints` / `supportsSetVariable` /
  `supportsEvaluateForHovers` / `supportsFunctionBreakpoints` 全 `false`。
  报 `true` 而没实现，用户点下去就是"没反应"（`docs/167`）。
* **落不上的断点要说**：`verified: false` + 一句原因（"这一行没有对应的指令"）。
  一个"绿点但不生效"的断点比没有更坏 —— 用户会以为程序没走到那里。
* **行表里的路径归一成 `/mnt/d/...`**：DWARF 里存的是**绝对 Windows 路径**
  （`lomentc._difile`），VS Code 给的是 Windows 形式，两边都由 `_norm` 归一，
  不靠拼串去猜。（这条也是修出来的：原先 `loment dbg` 指着一个不存在的文件。）
* **`launch` 里不继续跑**：VS Code 的次序是
  `launch -> setBreakpoints -> setExceptionBreakpoints -> configurationDone`。
  在 `launch` 里就继续的话，等它来设断点时进程已经在跑（甚至跑完了），于是
  `poke` 到一个不存在的进程（实测 `[Errno 3] No such process`）。
  继续的那一下放在 **`configurationDone`**。

## 4. 判据（`tools/loment_dap_test.py`，4 条）

跑的是**四次真会话**（起 `wsl -e python3 tools/loment_dap.py`，按 DAP 走完），
不是模拟：

1. `test_breakpoint_stops_on_the_right_line` —— 断点停在第 22 行，**且那一刻
   `write_str` 一个字都还没打**（这条把"停在语句之前"与"停在语句之后"分开了），
   继续之后那段字**要出现**；
2. `test_step_in_moves_to_another_line` —— `stepIn` 之后行号必须变；
3. `test_next_crosses_a_call` —— `stepIn` 与 `next` 停在**不同**行，且 `next` 落在 23；
4. `test_unplaceable_breakpoint_is_reported` —— 第 7 行（注释）必须 `verified: false`
   并带原因。

**一条必须说清的局限**：没有 WSL 或没有 Windows 侧 clang 时，这四条**全部 SKIP**
（退出码 0）。SKIP 不是通过 —— 在那种机器上这道门禁是**惰性的**。
`tools/ci.py` 里它是独立一条，所以"跑了没跑"在门禁输出里看得见。

## 5. 这一次**不做**的（免得被当成"已经有"）

* **条件断点 / 监视 / 表达式求值**（`evaluate`、hover 看值）—— 没有；
* **变量**：`scopes` 只给了一个 `Registers`，`variables` 列的是**寄存器**，
  不是源级局部变量（要在 DWARF 里读 `DW_TAG_variable` 加偏移，没做）；
* **调用栈只有一帧**：没有栈回溯（`fn_at` 知道当前函数名，但没往上走帧）；
* **多线程**：只报一个线程；
* **`loment dbg` 与它没合流**：一个是源码级符号化（命令行），一个是 DAP；
  行表将来应该只有一份；
* **没在真 VS Code 里按过 F5**：`activate()` 与适配器命令是无头验的，
  清单/工厂/命令都对得上（`test_vscode_extension_activates`），但"F5 真的弹出调试
  工具条"这一步是**人工**的 —— 记在这里，不当它已经验过。

## 6. 下一步

* **`scopes`/`variables` 走到源级**：读 `DW_TAG_variable` + `DW_AT_location`（局部在
  `rbp`/`rsp` 偏移上），这是"调试器好不好用"分界最大的一格；
* **栈回溯**：读 `rbp` 链或 `.eh_frame`（`.eh_frame` 更可靠，`-fomit-frame-pointer`
  时也对）；
* **`stepOut` 走返回地址**：有了帧信息就能真做；
* **条件断点**：后端已经有"断点表"这一层，加一个表达式求值器（可以复用
  `loment_interp`？先探）；
* **行表合并**：`loment dbg` 与适配器各解析一遍 `llvm-objdump`，应该只留一份。
