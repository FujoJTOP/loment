# 150 · Loment 自举（P8，M79–M88）

> 状态: **进行中**（2026-09-10）· 已完成: M79/M82（标量+控制流+除法+指针/位域内建子集）· 自检: `tools/loment_p8_test.py` 5/5
> M82 逐字节一致: 38 个示例中 33 个（10 个 `ir_*.lomt` 锚点 + `toolchain`/`native_bits`/`native_mem`/`native_str`/`bytes`/`native`/`ahci`/`fuc_node`/`allocator`/`mathutil`/`user_hello`/`bootprobe`）
> **自举四阶段全部能编译自身**：`lexer` / `parser` / `checker` / `codegen` 四个 `.lomt` 的 `.ll` 与
> `lomentc --emit-llvm` **逐字节一致**（26028B / 115464B / 83148B / 557828B，差异行均为 0）
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

**未覆盖**（相对 38 个示例文件，逐字节一致 33 个）：其余内建（`str_len`/`str_eq`/`str_byte`/
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
| 20 | `if blk_free(cur) && sz >= need` 的标签号整体错位（`L7_sc_rhs` vs `L4_sc_rhs`） | `if` 分支**先占**标签再算条件，而条件里的 `&&`/除法自己也要占标签 → 改为先算条件再占（`lomentc` 的 If 同序；同类的 `while` 本来就是先占） |
| 21 | `const HDR` 被当成变量 load（`%t2 = load i32, ptr %HDR.addr`） | 没做**常量内联**：补 `find_const` + `tok_int`（十六进制按**十进制**内联，与 `lomentc` 一致）+ `set_dec_val` |
| 22 | `-> ` 省略返回类型的函数被当成有返回类型 | `pub fn pool_init(...)` 无 `-> T` → 返回类型必须缺省为 `()`；用哨兵 `UNIT=0xFFFFFFFF` 表示，`emit_ty`/`emitted_name`/`is_unit_tok` 一起处理 |
| 23 | `return 0;`（返回 `ptr`）写成 `ret ptr 0` | `lomentc` 的 `expr(IntLit, want="ptr")` 写 `null` → 返回位单独判断 |
| 24 | 大文件下状态块越界 | `alloc(8192)` + 每函数形参类型步长 128B + `emit_dec` 每次 `alloc(16)` 漏堆 → 状态块扩到 49152、步长改 64B、译码缓冲改用状态块内的**共享 scratch**（`emit_dec`/`set_dec_val`/`set_temp_val` 不再动 bump 堆） |
| 25 | 字段访问的类型算成了整个 struct（`add { i32, i32 }`） | `expr_type` 缺 `FieldAccess` 分支（`p.a` 被当成 `p` 的 `Pair` 类型）→ 补"字段类型"分支（与 `lomentc.expr_type` 同义） |

**聚合类型（M82 第 2 步，进行中）**：`struct` 表（名字 + 字段下标/类型，状态块 1024 起、每项 80B）、
`emit_ty` 的 struct 名 → `{ i32, i32 }`、字段访问 `p.a` → `getelementptr inbounds <struct>, ptr %p.addr, i32 0, i32 <idx>`
+ `load`、`str` → `{ ptr, i64 }`。字段仅支持标量（与 `_ll_type` 的限制一致）。
已解锁 `mathutil.lomt`（`for` 变量 + 常量内联 + struct 参数取字段）。

**字符串（M82 第 3 步，本轮到 22/37）**：
- **字面量全局**：`@.str.<fn>.<n> = private unnamed_addr constant [N x i8] c"\XX…"`（大写十六进制、逐字节转义，
  转义表同 `lomc._ESCAPES`；N 是**解码后**的 UTF-8 字节数）。全局必须落在横幅/运行时/堆全局之后、
  函数之前，而输出是顺序写的 → 用**预扫描**（`emit_str_globals`）：逐函数、逐出现，
  与表达式求值顺序一致（同一 token 流的线性扫描）。注意 `strings_needed` 只看**函数体内**的
  kind=2 token —— `use "…"` 也是 kind=2，但它不是字面量（这个坑让 3 个已通过文件回归过）。
- **字面量表达式**：`getelementptr inbounds [N x i8], ptr @.str.f.k, i64 0, i64 0` + 两次
  `insertvalue` 组成 `{ ptr, i64 }`。
- **`str_*` 内建**：`str_len`（`extractvalue …, 1` + `trunc i64→i32`）、`str_ptr`（`extractvalue …, 0`）、
  `str_byte`（`extractvalue 0` + GEP + `load i8` + `zext`）、`str_eq`（先比长度，再
  `call i32 @__loment_memcmp` + `phi i1`，落在 `L_seq/L_sneq/L_send` 三块）。
- **`syscall4`/`syscall6`**：`call i64 asm sideeffect "syscall", "={ax},{ax},{di},{si},{dx}[,{r10},{r8}],~{cx},~{r11},~{memory}"(…)`；
  实参按各自类型求值后原样内联为 `i64 <val>`（与 `lomentc` 的 `expr(a,"u64")` 同形）。

**值栈搬到 8192**（`blen/btxt` 基址 16/20 → 8192/8196）：原先值栈与 struct 表（1024..6144）重叠，
深嵌套（如 6 个 syscall 实参）会踩表；现在各表依次为 struct 1024 / 值栈 8192 / 函数表 12288 /
当前函数参数表 16384 / 局部表 20480 / 每函数形参类型 24576（+i*64），scratch 在 656。

新增真 bug：
| # | 现象 | 根因 |
|---|---|---|
| 26 | `str_ptr(s) as u64` 生成 `trunc ptr … to i32` | 内建分支把 `as` 的类型 token 取成了 `as` 本身（应取其后一个）；且**无条件**加转换 |
| 27 | `p as u64`（ptr→u64）生成 `zext ptr … to i64` | `apply_cast` 缺 `ptr→u64/i64` 的 `ptrtoint` 分支（`apply_cast_b` 有，token 版没有） |
| 28 | `let r: i64 = …; if r >= 0` 生成 `icmp uge i32` | `expr_type` 的变量分支只查**参数表**，没查局部表 → 局部的类型丢失（应为 `sge i64`） |
| 29 | 嵌套调用的实参类型错位（`take5(p, q, r, load32(p,n), n)` 里 `q` 变成 `i32`） | 实参类型槽是**一张共享数组**，内层调用把外层已记好的槽覆盖了 → 改为按**值栈层**索引（`720+(lvl+1+a)*4`）。这个 bug 只在"实参里有调用"时现形 |
| 30 | `str_byte(s, i)` 的指令顺序反了 | Python 是"取 arg0 → `extractvalue` → 取 arg1"，我先算完两个实参才 `extractvalue` |
| 31 | 调用实参上限只有 4 | `while … && a < 4` → 自举 lexer 的 `emit(7 参)` 被截断；改为 8（形参类型槽本来就是 8） |
| 32 | 字符串字面量里出现 ` D A` | 驱动按**原始字节**读文件，而夹具写临时单元用了文本模式（CRLF）→ 写 `newline="
"`；`lomentc.load` 用 `read_text`（通用换行），两边必须一致 |
| 33 | `0x100000000` 被截成 0 | 字面量走 u32 算术；改为**长乘 16 加**转十进制（数字缓冲 672..704），十进制原样去前导 0 —— 镜像 Python 大整数 |
| 34 | `(r / 4294967296) as u32` 打成 `trunc i32 → i32` | `apply_cast` 的 from 侧传 0（未知）时打印用 `i32` 但位宽按 64 算；现在传真实 `expr_type`，且 0 一律按 u32（= `st or "u32"`） |
| 35 | `x as i64` 参与运算时类型退化成 i32 | `expr_type` 不认识 `as` 后缀 → 补"字面量/标识符/调用/括号 + `as T`"四种后缀识别 |

**切片与数组（M3/M4/M24）**：类型映射 `[T]`/`mut [T]` → `{ ptr, i64 }`（注意 `[T; N]` 走数组分支，
判定要看 `[` 后第 2 个 token 是不是 `;`）、`[T; N]` → `[N x T]`；`&a` / `&mut a` 取切片 =
`getelementptr inbounds <数组>, ptr %a.addr, i32 0, i32 0` + 两次 `insertvalue`（长度来自数组字面量）；
切片下标**读** = `extractvalue 0` + GEP(元素) + `load`，数组下标读 = GEP(数组,0,i) + `load`；
下标**写**（M4 可变切片）= `extractvalue 0` + 下标 + GEP + 求值 + `store`（顺序与 `lomentc` 一致）；
数组字面量 `[1,2,3,4]` = 逐元素 GEP + 求值 + store；`slice_len` = 切片取 `extractvalue 1` 再 `trunc`，
数组则用字面量长度。**`let` 的类型位置改成 `skip_type`**（类型可能是 `[T]`/`[T; N]`）；
**形参扫描也必须用 `skip_type`** —— 原来按"3 个 token 一个参数"步进，遇到 `xs: [u32]` 会多出一个幻影参数
（`i64 %u32`），函数表的形参类型扫描同样中招。

**trait 静态派发（M8）**：注册期识别 `impl`/`trait` 块 —— `trait` 声明里的 `fn` 只有签名（整条跳过，
不然会被当成空函数发射），`impl X for Y` 的方法登记为 kind=2 并记住接收者类型；发射时名字变成
`<接收者类型>_<方法>`（注释行与 `define` 都要改），**第 0 个形参 `self` 的类型取接收者类型**
（`self` 没有类型注解，原来会解析成 `)` 变成 i64）。方法调用 `x.m(args)` 在 `expr_atom` 里优先于
字段访问处理：接收者按第一形参类型求值，其余实参按被调方形参类型强制，最后
`call <ret> @<接收者类型>_m(<接收者> x, …)`。变量名的输出统一走 `emit_name_tok`（`self` → `__self`），
覆盖形参表/alloca/store/load/字段与下标访问等全部名字位置。解锁 `native_trait.lomt`。

**枚举与 `match`（M25/M26）+ struct/数组字面量（M23/M24）**：枚举表（name | nvariants | has_payload |
8×(variant, payload)，状态块 40960 起 80B/项）；`E::V` → 无载荷枚举写字面量下标、带载荷枚举只写
tag（`insertvalue {i32,i64} undef, i32 idx, 0`）；`E::V(x)` → 求值 → tag → `sext/zext` 到 i64 →
载荷 insertvalue；`match` → `switch i32 <tag>, label %L_mwild [ ... ]` + 逐臂
`extractvalue …, 1` → `trunc` → `store %bind.addr` → 臂体 → `br %L_mend`；
struct 字面量 `Blk { off: 3, len: 5 }` 与数组字面量 `[1,2,3,4]` 都是逐字段/元素 GEP + 求值 + store。
解锁 `native_agg.lomt`。

**这三个坑值得单独记（都是"多字符运算符其实是多个 token"）**：`::` 是 **两个 `:` token**、
`=>` 是 **两个 token（`=` 与 `>`）**、`->` 同理（返回类型在 `)` 之后第 3 个位置）。凡是按"一个运算符一个 token"
写偏移，遇到它们就会静默错位 —— `match` 的臂扫描因此几乎全错（通配臂判定从没命中）。
另外两个：臂遍要显式以 match 体的 `}` 终止（否则最后一个臂之后还会多跑一轮，把后续语句吞进臂体）；
通配臂的发射顺序是**标签 → 臂体 → 跳转**（我一开始写成标签 → 跳转 → 臂体，多出一条 `br`）。

**能力域（P4：M35/M36/M38）**：`capability blk_write : disk[0..4] revocable` 扫成域表
（name_tok | space_tok | lo | hi | revocable，状态块 6144 起 24B/项）；`guard NAME(expr);` 降级为
`zext i32→i64` → `icmp uge/ule`（lo/hi 是编译期常量）→ `and i1` → `br` 到 `%L_gok`/`%L_gtrap`
（trap 走 `__loment_abort`）→ 通过分支在 `@__loment_audit[cap_id]` 上加一。模块级还要插两个块：
`@__loment_audit`（有 `guard` 时）与 `@__loment_caps`（有 `capability` 时，space 名取
**FNV-1a 32 位**），顺序是 **运行时 → audit → caps → 堆全局 → 字符串全局 → 函数**。
解锁 `native_cap.lomt`（2408B 零差异）。

**中断处理函数（M33）**：`interrupt fn f() { … }` → 注释写 `; f -> interrupt (x86_intrcc)`，
签名是 `define x86_intrcc void @f(ptr byval([8 x i8]) %__frame)`（**无参数**，只列局部 alloca）。
注意 `fn` 的前一个 token 是 `interrupt`，用 `base - 1` 判定。

**自举阶段的现状（本轮最大成果）**：`lexer.lomt`、`parser.lomt`、`codegen.lomt` 三个自举阶段都能被
Loment 版 codegen 编译出与 `lomentc --emit-llvm` **逐字节相同**的 IR（lexer 26028B / parser 115464B /
codegen 557823B，差异行均为 0）—— 即"用 Loment 写的编译器生成自己的 IR 与参考实现完全一致"。
`checker.lomt` 的最后一块是 9 实参调用（`declare(...)`）：调用实参上限与形参类型槽都只有 8，
第 9 个实参被整条丢掉 → 上限提到 16、步长改 128B（函数数上限 128）。至此 **M82 的自编译判据全部达成**：
用 Loment 写的四段编译器生成自己的 IR 与参考实现逐字节相同 —— M83（三阶段定点）的前置条件已满足。

**依赖装载的边界（已落实为可核对的夹具）**：`lomentc.emit_llvm` 吃的是 `prepare()` 之后的
**依赖拼接单元**（`mods = deps + [mod]`）。测试夹具 `_unit_text()` 按 `resolve_deps` 的路径规则
取依赖、被依赖者在前拼接，并**断言模块名序列与 `lomentc.resolve_deps` 完全一致**
（规则一漂移就失败）。因此 Loment 版 codegen 的输入是"已装载的单元"，与 M80/M81 自举阶段的
边界一致；把 `resolve_deps` 搬进 Loment（需要文件 I/O）仍是 M83 之前的待办。

**已知的结构性边界（M82 之后要补）**：`lomentc.emit_llvm` 走的是 `prepare()` 之后的
**依赖拼接单元**（`mods = deps + [mod]`），所以像 `allocator.lomt` 这种自身不含 `/` 但
`use "bytes.lomt"` 的文件，运行时块是由**依赖里的除法**触发的。Loment 版 codegen 只吃
**单个编译单元**（不解析 `use`），因此这类文件当前必然不一致 —— 解法有两条：
①把 `resolve_deps` 也搬进 Loment（需要文件 I/O，属 P7 机器）；②明确把"前端装载器"拆给驱动
（驱动按拓扑序拼接缓冲，codegen 只编译拼接后的单元）。这条边界要在 M83 之前定下来。

另有一处 token 层 off-by-one：`->` 是两个 token，所以返回类型在 `)` 之后第 3 个位置。

## M83 · 自编译（编译器编译自身）✅ 部分 · M84 · 三阶段定点 ✅

**判据与证据**（`loment_p8_test::test_m83_m84_self_compile_and_fixed_point`，
也可由 `python tools/loment_bootstrap.py` 一条命令复现）：

| 阶段 | 怎么来的 | 结果 |
|---|---|---|
| stage1 | **Python 版** `lomentc` 编译 `loment/selfhost/codegen.lomt`（连同 Loment 版 lexer）→ `.ll` → clang 链出可执行文件 | 可运行 |
| stage2 | **stage1 自己产出的 `.ll`** → clang 链出（M83：编译器编译自己的产出可运行） | 可运行 |
| stage3 | stage2 的产出 → clang 链出 | 可运行 |
| **M84 定点** | stage1 / stage2 / stage3 各自生成 `codegen.lomt` 的 IR，三者**逐字节相同** | **637115 B，全等** |

定点不是巧合：stage2 对 `checker.lomt`、`ir_div.lomt` 的产出也与参考实现逐字节一致。

**诚实边界（M83 记为"部分"的原因）**：自编译覆盖的是 **Loment 版 lexer + IR 后端**。
- 前端 `parser`/`checker` 虽已自举（M80/M81），但**没有接进同一个驱动可执行文件**（当前由 C 驱动
  提供 `main` 与文件读取，Loment 侧提供 `lex` 与 `emit_module`）；
- 后端还**不支持泛型单态化 / trait 静态派发 / `match`+枚举 / 能力域**，所以它还不能编译任意程序
  —— 这正是 M85（自举版跑 `lomentc_test`）还到不了的原因。

## M85–M86 · 未做（诚实说明）

| # | 里程碑 | 为什么现在做不了 |
|---|---|---|
| M85 | 自举编译器跑 `lomentc_test` | 需要自举后端覆盖**全语言**：泛型单态化（参考实现靠 `prepare()` 在 AST 上改名，自举版只吃 token 流，要在 token 层重建实例化命名）、trait 派发、`match`+枚举、能力域；并要把 lexer/parser/checker/codegen 接进同一驱动 |
| M86 | 自举性能优化 | 依赖 M85 |

自举进度是真实的：**lexer → parser → checker → codegen 四段都已用 Loment 实现，
并分别与 Python 版逐 token / 逐字符 / 逐错误码 / 逐字节对照通过**（M79–M82），
且**四段都能被 Loment 版 codegen 编译出与参考逐字节相同的 IR**（见上文各节），
后端进而达到三阶段定点（M84）。
M82 的剩余清单还没有走完：**38 个示例文件里逐字节一致 33 个**（10 个 `ir_*.lomt` 锚点 + `toolchain`/`native_bits`/`native_mem`/`native_str`/`native`/`ahci`/`fuc_node`/`bytes`/`allocator`/`mathutil`/`user_hello`/`bootprobe`），
缺口集中在聚合类型（5：泛型单态化 / trait / RAII / Result+`?`）、`for`（3）、除法（2）、内建（2）、`match`/枚举（2）、`syscall`（1）、能力域（1）。
## M87 · 引导脚本 ✅

`python tools/loment_bootstrap.py` —— 一条命令走完自举前端：

1. 重新生成 lexer/parser/checker 的形式对象并与磁盘**逐字节**核对；
2. 跑 M79/M80/M81 三项对照；
3. 打印报告（`--json` 可机器读）。

## M88 · 发布校验和 ✅ 部分

`python tools/loment_release.py --checksums loment/build/SHA256SUMS` 产出
**101 行** sha256 清单（与 `release-manifest.json` 同源、换行无关）。
**未做**：git tag 未创建/未推送（分支与并发开发线共用，打 tag 与推送需作者确认）。

