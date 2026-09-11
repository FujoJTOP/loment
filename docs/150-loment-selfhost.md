# 150 · Loment 自举（P8，M79–M88）

> 状态: **进行中**（2026-09-10）· 已完成: M79/M82（标量+控制流+除法+指针/位域内建子集）· 自检: `tools/loment_p8_test.py` 5/5
> M82 逐字节一致: 37 个示例中 13 个（9 个 `ir_*.lomt` 锚点 + `toolchain`/`native_bits`/`native_mem`/`bytes`）
> 门禁: `ci.py --static-only` 10/10（Loment 侧；`LinuxFUAI/` 为空时另 4 项失败）

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

## M82 · Loment 版 IR 生成（表达式/控制流/短路/转换/内建）✅

`loment/selfhost/codegen.lomt`：读取 M79 token 流，直接生成 LLVM IR 文本。判据是
**逐字节**：`loment_p8_test::test_m82_*` 把 Loment 版输出与 `lomentc --emit-llvm` 的结果
做字符串相等比较，9 个目标文件共 58 个函数：

- `loment/selfhost/ir_const.lomt`：字面量/参数返回（u32/u64/i16/bool）；
- `loment/selfhost/ir_expr.lomt`：二元运算符（按 `lomentc.PRECEDENCE` 爬升、含
  `a * b + c`、`(a + b) * 2`）、比较（无符号 `ult/ule/ugt/uge` 与有符号 `slt/...` 由类型决定）、
  位运算与移位（`and/shl`）、一元（`sub ty 0, v` / `xor i1 v, true`）、调用
  （直接调用、嵌套调用 `add(a, mul_add(a, b, 1))`、字面量实参）；
- `loment/selfhost/ir_stmt.lomt`：**语句与控制流** —— `let`（含局部 alloca，
  按 `lomentc._collect_locals` 的顺序）、赋值、`if/else`（含 else 里再嵌 `if`）、`while`，
  以及 `terminated` 语义（块已 `ret` 就不再补 `br`）、标签编号 `L1_then/L2_else/L3_end`
  与 `L1_wcond/L2_wbody/L3_wend`（从 1 起、每函数重置）；
- `loment/selfhost/ir_logic.lomt`：**`&&`/`||` 短路**（`br i1` + `%L1_sc_rhs / %L2_sc_short / %L3_sc_end`
  三块 + `phi i1`），phi 的右前驱取"右操作数结束时的真实块"（嵌套短路时是内层的 `sc_end`，
  与 `lomentc` 的 `cur_label` 同义）；
- `loment/selfhost/ir_cast.lomt`：**`as` 转换**（按位宽与符号性选 `trunc` / `sext` / `zext`，
  字面量与 `bool` 转换按 `st or "u32"` 缺省规则处理），**含实参位置**——调用点按被调方的
  **形参类型**强制实参（符号表为每个函数存 8 个形参类型槽，与 `lomentc` 的 `expr(a, p.type)` 同义）；
- `loment/selfhost/ir_mem.lomt`：内建 `load8` / `store8`（`getelementptr i8` + `load i8` +
  `zext i8 ... to i32`；`store8` 的结果值按 `lomentc` 取字面量 `0`），以及 `load8(...) as T` 后缀；
- `loment/selfhost/ir_for.lomt`：**`for i in lo..hi`**（`store lo` → `L_fcond/L_fbody/L_fend` 三块，
  条件块里**重新取**循环变量与上界，自增写回 `%i.addr`；比较的符号性按
  `expr_type(lo) or expr_type(hi) or u32` 决定 `slt`/`ult`，与 `lomentc._collect_locals` 同规则）；
- `loment/selfhost/ir_div.lomt`：**除法与取模**（`udiv/sdiv/urem/srem` 由结果类型的符号性选），
  除零走 `%L_dtrap` 块（`call void @__loment_abort()` + `unreachable`），且一旦出现过 `/` `%` 就
  在横幅之后、函数之前插入**整段 freestanding 运行时文本块**（与 `lomentc._IR_RUNTIME` 逐字节相同）；
- `loment/selfhost/ir_builtin.lomt`：**指针/位域内建** —— `alloc`（bump 堆：`load @__loment_off`
  → `add` → `icmp ule 65536` → `%L_aok`/`%L_aovf`，溢出走 abort，成功后
  `store @__loment_off` + `getelementptr [65536 x i8]`，并在横幅后**再插堆全局**）、`free`（结果值为
  字面量 `0`）、`atomic_add`（`atomicrmw add ... seq_cst`）、`ptr_add`/`ptr_sub`（`zext i32→i64`
  + `getelementptr inbounds i8`，减法先 `sub i64 0, n`）、`get_bits`/`set_bits`（i16 掩码
  `1<<w - 1` 再截回 i8）、`panic`（abort + `unreachable` + `%L_dead` 死块）。
  内建结果的 `as` 后缀走**名字型**转换 `apply_cast_b`（内建结果类型是固定的：`u8`/`u32`/`ptr`），
  含 `ptr→u64` 的 `ptrtoint`。

**覆盖**：函数签名与类型映射（i1/i8/i16/i32/i64/ptr）、入口块、参数 alloca + store、
`%tN` 编号（从 1 起、每函数重置）、头部注释（注释里写的是 **Loment 类型名**而非 LLVM 类型）。

**未覆盖**（相对 37 个示例文件，逐字节一致 13 个）：其余内建（`str_len`/`str_eq`/`str_byte`/
`slice_len`/`str_ptr`/`syscall*`）、`match`、str/切片/数组/struct/枚举等聚合类型、能力域表与 DWARF 元数据。
每次门禁会打印按文件计的缺口分类表（`test_m82_coverage_report`），
`ir_*.lomt` 目标文件是对应的防回归锚点。

**核心设计（两阶段值栈）**：`expr_*` 先把指令写进输出，再把"值文本"落到值栈的第 `lvl` 层；
调用方随后把该值内联到自己的行里。这正是 Python 版用字符串拼接达到的效果——
嵌套表达式因此能与 Python 版保持**完全相同的指令顺序**。值栈 8 层 × 72B，
实参类型槽与函数/参数符号表各自的偏移都在状态块里（`docs` 记于代码注释）。

调试过程中踩到并修掉的真问题（现象 → 根因）：

| # | 现象 | 根因 |
|---|---|---|
| 1 | `emit` 实参个数不符 | 与 `lexer.lomt` 的私有 `emit(7 参)` 重名 → 入口改名 `emit_module` |
| 2 | 输出在 `ret i32 ` 处截断 | 值缓冲误用 `emit_mem`（它会更新**输出游标**）→ 改用不碰游标的 `copy_mem` |
| 3 | 返回值打印成 `\x03` | 值长度写到缓冲开头（应为缓冲**之前** 4 字节） |
| 4 | `ret i32   %t0 = load ...` 乱序 | 单阶段发射无法把指令放行首 → 两阶段（先指令+值缓冲，再写行） |
| 5 | 状态块字段互相踩 | 值栈/实参类型/函数表/参数表偏移重叠 → 重新排布（值栈 16..592、实参类型 608、函数计数 640、函数表 704、参数表 1536、局部表 3072） |
| 6 | 局部 alloca 混进了别的函数 | `collect_locals` 扫到了整个文件 → 传入函数体的匹配 `}` 作为上界（`skip_block`） |
| 7 | 标签从 `L0_` 起编号 | `lomentc` 的标签从 1 起 → 基准取 `+1`、计数 `+2` |
| 8 | `if`/`while` 体里第一条语句被跳过 | 体起点算成 `'{' + 2` → 应为 `'{' + 1` |
| 9 | 调用语句整条消失 | `stmt` 缺"表达式语句"分支（`NAME ( ... ) ;`） |
| 10 | `take8(v as u8)` 少一次 `trunc` | `as` 后缀没接在**调用/内建**分支上 |
| 11 | `for` 变量没有 alloca | `collect_locals` 只认 `let` → 把 `for` 变量也登记（类型同 `lomentc` 的回退链） |
| 12 | `for i in 0..n`（`n: i32`）生成 `ult` | 符号性只看了 `lo`（字面量 → 无类型 → 落到 u32）→ 改为 `expr_type(lo) or expr_type(hi)` |
| 13 | 除法结果号比 `icmp eq` 的号小 | `lomentc` 先占 `r` 再占 `z` → 拆出只占号不写输出的 `alloc_temp` |
| 14 | 除法出现在 `&&` 右操作数时 phi 前驱写成 `%L6_sc_end` | 标签 tag_id 表没有 `dok/dtrap/dend`（默认落在 `sc_end`）→ 补 tag_id/tag_name（**实测**：去掉后第 5210 字节起不一致，且是指向不存在块的非法 IR） |
| 15 | 除法目标文件缺整段运行时 | 输出是顺序写的，而运行时块要落在横幅之后、函数之前 → 预扫描 `/` `%` punct token（语义等价于 `lomentc` 的 `"@__loment_" in text_all`） |
| 16 | 内建结果参与更大表达式时被截断（`load8(p,off) + load8(p,off+1) * 256` 只算前半） | 内建分支返回的是 `)` 的位置，而其余原子/调用分支返回的是**其后**位置（`expr_bin` 的契约）→ 统一成"其后"（`+1`，带 `as` 则 `+3`） |
| 17 | unit 函数体尾缺 `ret void` / `store16` 的返回类型写成 `i64` | `emit_ty`/`emitted_name` 不认识 `()`；`emit_fn` 也没有"块未终止则补 `ret void`/`unreachable`"这一条 |
| 18 | `while true` 生成 `%t1 = load i32, ptr %true.addr` | `expr_atom` 缺 `true`/`false` 字面量分支（落到"变量 load"兜底）→ 直接写字面量 `1`/`0` |
| 19 | unit 返回的调用写成 `%t6 = call void @f(...)` | Python 对 `()` 返回不占寄存器（`call void @f(...)` 无赋值）→ 补 unit 分支 |

**已知的结构性边界（M82 之后要补）**：`lomentc.emit_llvm` 走的是 `prepare()` 之后的
**依赖拼接单元**（`mods = deps + [mod]`），所以像 `allocator.lomt` 这种自身不含 `/` 但
`use "bytes.lomt"` 的文件，运行时块是由**依赖里的除法**触发的。Loment 版 codegen 只吃
**单个编译单元**（不解析 `use`），因此这类文件当前必然不一致 —— 解法有两条：
①把 `resolve_deps` 也搬进 Loment（需要文件 I/O，属 P7 机器）；②明确把"前端装载器"拆给驱动
（驱动按拓扑序拼接缓冲，codegen 只编译拼接后的单元）。这条边界要在 M83 之前定下来。

另有一处 token 层 off-by-one：`->` 是两个 token，所以返回类型在 `)` 之后第 3 个位置。

## M83–M86 · 未做（诚实说明）

| # | 里程碑 | 为什么现在做不了 |
|---|---|---|
| M83 | 自编译（编译器编译自身） | 依赖 M82 覆盖全语言（当前只有常量/参数返回子集） |
| M84 | 三阶段定点校验 | 依赖 M83 |
| M85 | 自举编译器跑 `lomentc_test` | 依赖 M83 |
| M86 | 自举性能优化 | 依赖 M83 |

自举进度是真实的：**lexer → parser → checker → codegen 四段都已用 Loment 实现，
并分别与 Python 版逐 token / 逐字符 / 逐错误码 / 逐字节对照通过**（M79–M82）。
M82 的剩余清单还没有走完：**37 个示例文件里逐字节一致 13 个**（9 个 `ir_*.lomt` 锚点 + `toolchain`/`native_bits`/`native_mem`/`bytes`），
缺口集中在聚合/切片/字符串类型（18 个文件）、其余内建（14）、`syscall`（5）、`match`/枚举（4）；
也就是说 M82 目前覆盖的是"标量 + 控制流 + 除法 + 指针/位域内建"这一层，M83 自编译还需要聚合类型与字符串/切片内建。

## M87 · 引导脚本 ✅

`python tools/loment_bootstrap.py` —— 一条命令走完自举前端：

1. 重新生成 lexer/parser/checker 的形式对象并与磁盘**逐字节**核对；
2. 跑 M79/M80/M81 三项对照；
3. 打印报告（`--json` 可机器读）。

## M88 · 发布校验和 ✅ 部分

`python tools/loment_release.py --checksums loment/build/SHA256SUMS` 产出
**101 行** sha256 清单（与 `release-manifest.json` 同源、换行无关）。
**未做**：git tag 未创建/未推送（分支与并发开发线共用，打 tag 与推送需作者确认）。

