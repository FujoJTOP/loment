# 150 · Loment 自举（P8，M79–M88）

> 状态: **进行中**（2026-09-09）· 已完成: M79 · 部分: M80（子集）· 自检: `tools/loment_p8_test.py` 2/2
> 门禁: `ci.py --static-only` 10/10

## M79 · Loment 版 lexer ✅

`loment/selfhost/lexer.lomt`：用 Loment 写的词法器，输入字节缓冲，输出 20B/token 记录
（`kind|start|len|line|col`）。规则与 `tools/lomc.py` 的 `lex()` 一致：

- 空白 / `//` 行注释 / `/* */` 块注释；
- 字符串（含 `\` 转义，span 含引号）；
- 十进制与 `0x` 十六进制数；
- 标识符（`[A-Za-z_][A-Za-z0-9_]*`）；
- 单字符 punct 集 `{}()[]:;=@,.+-*/%<>!&|^?`；
- 末尾 `eof`。

**判据（与 Python 版 token 流一致）**：`loment_p8_test.py` 用 IR 路径编译 lexer + C 驱动，
对 4 个真实文件（含中文注释、含 lexer 自身源码）逐 token 比较
`(kind, start, len, line, col)`——**全部一致**。

实现过程中修掉的两个真 bug（都属于编译器，不是 lexer）：

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | `else if` 解析失败 | 语法只支持 `else { }` | parser 支持 `else if` 链 |
| 2 | `&&`/`||` 嵌套时 LLVM 报 `PHI node entries do not match predecessors` | 短路降级把 phi 前驱写死成 `rhs_l`，右操作数自己也可能产生新块 | `_Ir` 跟踪 `cur_label`，phi 用真实前驱；每个函数显式 `entry:` 标签 |

第 2 个 bug 是自举逼出来的：手写示例里的 `&&` 都很简单，只有把真实程序（lexer 的
多重条件）交给后端才暴露。修完后 11 条双路径示例输出与修改前逐值一致（见 docs/145 P8 证据）。

## M80 · Loment 版 parser（子集）✅

`loment/selfhost/parser.lomt`：消费 M79 的 token 记录，产出规范 AST dump，
由 `loment_p8_test` 与 Python 版（`lomentc` 的 AST）**逐字符**比较。

**覆盖**：`module` / `fn` 签名（参数类型、返回类型）/ 函数体
（`let`（含初始化）、`x = e` 赋值、`return`、`if/else`（含 `else if` 链）、`while`、表达式语句）
/ 表达式（整型、`true`/`false`、标识符、调用、一元 `!`/`-`、二元全部运算符并**按
`lomentc.PRECEDENCE` 结合**、括号、`a.b`、`a[i]`、`x as T`）。

**判据**：`(module m (fn f (p x u32) -> u32 ( (ret (id x)(bin * (id TWO))))))` 这样的
dump 在 5 个真实文件上（mathutil / bytes / ahci / allocator / fuc_node）与 Python 版
**逐字符一致**。

**未覆盖**（子集边界，测试会 SKIP 并打印原因）：字符串字面量、十六进制字面量、
`match`/`for`、结构体字面量、枚举路径与构造、方法调用、`guard`、切片、`?`。

实现要点：

- Loment 无元组返回 → `(i << 32) | o` 打包「token 游标 + 输出游标」，函数式传递；
- 表达式 dump 采用「左操作数先输出」的线性形式（`(id a)(bin + (id b))`），
  可无歧义还原树，且天然匹配逐 token 下降的解析器；
- 多字符运算符（`==`/`->`/`&&`…）在词法上是两个 token，用 `op_code`/`op_toks` 识别与步进。

## M81 · Loment 版检查器（子集）✅

`loment/selfhost/checker.lomt`：消费 M79 token 流，做**符号表 + 类型/调用检查**，
输出错误记录（`code | token`）。与 Python 版（`lomentc.check`）对照的口径是**错误码集合**：

| 码 | 规则 | Loment 侧实现 |
|---|---|---|
| `E-DUP` (1) | 顶层名字重复（fn/struct/enum/const） | 符号表线性查重 |
| `E-TYPE` (2) | 类型未声明（参数/返回/const/let） | 基类型表 + 声明表；`[T]`/`[T; N]`/`mut [T]` 用 `skip_type` 整体跨过 |
| `E-UNKNOWN-FN` (3) | 调用未声明的函数 | 符号表 + 内建名单（20 个内建） |
| `E-ARITY` (4) | 实参个数不符 | 顶层逗号计数（识别 `(`/`[` 嵌套） |

**判据**：`loment_p8_test::test_m81_*` —— 6 个负例文件全部被两边拒绝，且 Loment 的
错误码集合 ⊆ Python 的；4 个单编译单元正例（`selfhost/pos/ok.lomt`、mathutil、bytes、native）
两边都接受。

**子集边界**：不解析 `use` 导入（因此只对照单编译单元文件）；不做表达式类型推导、
不做借用/移动检查、不做穷尽性检查（那些仍由 Python 版负责）。

## M82 · Loment 版 IR 生成（常量/参数返回子集）✅

`loment/selfhost/codegen.lomt`：读取 M79 token 流，直接生成 LLVM IR 文本。判据是
**逐字节**：`loment_p8_test::test_m82_*` 把 Loment 版输出与 `lomentc --emit-llvm` 的结果
做字符串相等比较（目标文件 `loment/selfhost/ir_const.lomt`，5 个函数：字面量返回、
参数返回，覆盖 u32/u64/i16/bool）。

**覆盖**：函数签名（参数/返回类型映射 i1/i8/i16/i32/i64）、入口块、参数 alloca 与 store、
`return <字面量|参数>;`、`%tN` 编号（从 1 起，与 Python 版一致）、头部注释（含注释里
写的是 **Loment 类型名**而非 LLVM 类型这个细节）。

**未覆盖**：二元/一元表达式、函数调用（它们的值要先算指令再内联到行里，需要
"两阶段值缓冲"设计，已在下面记录）、`as`、除法（需跳转块 + 运行时）、`let`/`if`/`while`、
聚合类型与能力域、无 libc 运行时与 DWARF 元数据。

调试过程中踩到并修掉的四个真问题（都是"读代码看不出来、跑起来才现形"的）：

| # | 现象 | 根因 |
|---|---|---|
| 1 | `emit` 实参个数不符 | 与 `lexer.lomt` 的私有 `emit(7 参)` 重名 → 入口改名 `emit_module` |
| 2 | 输出在 `ret i32 ` 处截断 | 值缓冲用了 `emit_mem`，而它会更新**输出游标** → 游标被写坏；改用不碰游标的 `copy_mem` |
| 3 | 返回值打印成 `\x03` | 长度写到了值缓冲开头（应为缓冲**之前** 4 字节） |
| 4 | `ret i32   %t0 = load ...` 乱序 | 单阶段发射无法把指令放到行首 → 改成两阶段：先 `expr` 写指令+值缓冲，再写 `ret` 行 |

## M83–M86 · 未做（诚实说明）

| # | 里程碑 | 为什么现在做不了 |
|---|---|---|
| M83 | 自编译（编译器编译自身） | 依赖 M82 覆盖全语言（当前只有常量/参数返回子集） |
| M84 | 三阶段定点校验 | 依赖 M83 |
| M85 | 自举编译器跑 `lomentc_test` | 依赖 M83 |
| M86 | 自举性能优化 | 依赖 M83 |

自举进度是真实的：**lexer → parser → checker → codegen 四段都已用 Loment 实现，
并分别与 Python 版逐 token / 逐字符 / 逐错误码 / 逐字节对照通过**（M79–M82）。
M82 的子集边界清楚：把"值先算指令再内联"的两阶段设计推广到二元表达式与调用，
是把它推到全语言的第一步。

## M87 · 引导脚本 ✅

`python tools/loment_bootstrap.py` —— 一条命令走完自举前端：

1. 重新生成 lexer/parser/checker 的形式对象并与磁盘**逐字节**核对；
2. 跑 M79/M80/M81 三项对照；
3. 打印报告（`--json` 可机器读）。

## M88 · 发布校验和 ✅ 部分

`python tools/loment_release.py --checksums loment/build/SHA256SUMS` 产出
**101 行** sha256 清单（与 `release-manifest.json` 同源、换行无关）。
**未做**：git tag 未创建/未推送（分支与并发开发线共用，打 tag 与推送需作者确认）。

