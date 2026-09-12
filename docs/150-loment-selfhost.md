# 150 · Loment 自举（P8，M79–M88）

> 状态: **M79–M84 已完成**（2026-09-11）· 自检: `tools/loment_p8_test.py` **7/7**
> M82 逐字节一致: **目标 39/39**（10 个 `ir_*.lomt` 锚点 + `toolchain`/`native_bits`/`native_mem`/
> `native_str`/`native_gen`/`native_trait`/`native_res`/`native_brk`/`bytes`/`native`/`ahci`/
> `fuc_node`/`allocator`/`mathutil`/`user_hello`/`bootprobe`/`demo`/`all_loment`/`selfcheck`/
> `lexer`/`parser`/`checker`/`codegen`/`driver` …）。
> 唯一非目标 `native_raii.lomt`：参考实现自己就报 `native: inb 暂未在 IR 后端实现`。
> **自举四阶段全部能编译自身**，且有一个**能独立跑的驱动**（`loment/selfhost/driver.lomt`）：
> 它编译自己的单元与 `lomentc --emit-llvm` 逐字节相同（973238B），用自己的产物再链一次仍相同。
> 门禁: `ci.py` 静态项 8 PASS（另 4 项因 `LinuxFUAI/` 私有库缺位而失败，非 Loment 回归）

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

**未覆盖**（这一节写于 M82 收官前）：其余内建（`str_len`/`str_eq`/`str_byte`/
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
| 32 | 字符串字面量里出现 `DA` | 驱动按**原始字节**读文件，而夹具写临时单元用了文本模式（CRLF）→ 写 `newline="
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

**泛型单态化（M6/M7）**：状态块低区（值栈搬到 8192 后 `16..475` 全空）放**类型参数替换表**
（计数 16，表项 `24+i*8` = `param_tok|value_tok`），`subst_tok` 挂在三类"类型来源"上：
`emit_ty`/`emitted_name` 的入口、`param_type`/`var_type`/`field_type`/`enum_payload_type` 的返回。
类型实参**不需要额外存**——`let p: Pair<u32>` 的类型 token 后面就是 `<` `u32` `>`，
所以 `var_targ`/`push_ty_subst` 直接从标注位置读；要区分"声明的形参"与"使用的实参"（同一个 `Pair` token
两处含义不同），就在 struct/enum 表里各留两个槽（stride 80 的空位 `+72`/`+76`）记下声明处的形参 token。
实例按"首次被用到"的顺序登记在 `64` 起的表里（`72+i*12` = `fn_tok|t1|t2`），非泛型函数发完后逐个发射，
每个实例推入自己的实参映射、名字拼成 `基名_实参`；调用点用 `expr_type(第一个实参)` 推断类型实参、
登记实例、再发 mangled 名与替换后的形参类型。泛型声明本身整条跳过。
`skip_type` 也要会跳过 `<...>`（`let p: Pair<u32>` 的类型解析）；struct/enum 表扫描要跳过 `<T>` 找 `{`。
解锁 `native_gen.lomt` 与 `all_loment.lomt`（多模块+泛型）。

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

## M83 · 自编译（编译器编译自身）✅ · M84 · 三阶段定点 ✅

**判据与证据**（`loment_p8_test::test_m83_m84_self_compile_and_fixed_point`，
也可由 `python tools/loment_bootstrap.py` 一条命令复现）：

| 阶段 | 怎么来的 | 结果 |
|---|---|---|
| stage1 | **Python 版** `lomentc` 编译 `loment/selfhost/codegen.lomt`（连同 Loment 版 lexer）→ `.ll` → clang 链出可执行文件 | 可运行 |
| stage2 | **stage1 自己产出的 `.ll`** → clang 链出（M83：编译器编译自己的产出可运行） | 可运行 |
| stage3 | stage2 的产出 → clang 链出 | 可运行 |
| **M84 定点** | stage1 / stage2 / stage3 各自生成 `codegen.lomt` 的 IR，三者**逐字节相同** | **965680 B，全等** |

定点不是巧合：stage2 对 `checker.lomt`、`ir_div.lomt` 的产出也与参考实现逐字节一致。

### 独立驱动（2026-09-11 补上的一步）

上面三个 stage 都还是"**被 C 驱动调用的函数**"：能编译自己，但没有能独立跑的编译器。
`loment/selfhost/driver.lomt` 把它补齐 —— lexer + codegen 接成**一个 ELF**：

```
clang --target=x86_64-unknown-linux-gnu -nostdlib -ffreestanding -static -fuse-ld=lld \
      -o fujoc-s loment/build/driver.ll
./fujoc-s loment/examples/demo.lomt > demo.ll   # 与 lomentc --emit-llvm 逐字节相同
```

内存向内核要（`brk`），**不用**语言自带的 64 KiB bump 堆：一个 190 KiB 的单元要约 4 MiB
token 表。把那个静态堆调大是错的方向 —— 每个 `alloc` 用户（内核模块尤其）的 `.bss`
都要跟着涨。这也是 M83 顺手补上 `整数 as ptr` 的原因（M67 只做了反方向的 `ptr as u64`）。

判据（`test_m83_selfhosted_driver_compiles_itself`，在 WSL 里执行）：

| 判据 | 结果 |
|---|---|
| 驱动跑自己的入口 → 与参考逐字节相同 | ✅ 1043790B |
| 用**驱动自己的产物**再链一个 ELF → 产物不变 | ✅ 二阶段定点 |
| 同一个二进制对 `native_res.lomt` / `demo.lomt` 也与参考逐字节相同 | ✅ 不是"只会编译自己" |

**自举抓到的六个真 bug**（都是"只有真实程序才暴露"的那类）：

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | 驱动的单元里 `die(1)` 的实参类型变成了 `ptr` | 每函数形参类型表 `24576+i*128` 撞枚举表 `40960`：第 128 个函数正好压上去。`codegen.lomt` 自己的单元只有 118 个函数所以一直没露，驱动把 `bytes`/`lexer` 一起装进来（134 个函数）才暴露 | 步长改 80 |
| 2 | clang 报 `invalid cast opcode for cast from 'i64' to 'i64'` | 同宽异名转型（`i64 as u64`）被写成 `sext i64 %v to i64` —— 同宽时 LLVM 里本来就是同一个类型 | 同宽不再写指令 |
| 3 | clang 报 `unable to create block named 'entry'` | 形参叫 `entry`：LLVM 的块标签与局部值**共用名字空间**，每个函数的第一块都叫 `entry` | 驱动里改名；**语言侧仍是缺口**（`let entry: u32` 会踩到） |
| 4 | `(v / 256) as u8` 的 `trunc` 整个丢了 | `as` 左操作数类型原先取的是**整个转型表达式**的类型（u8），于是被判成"同宽转型" | 新增 `operand_type()`（见下） |
| 5 | 两个模块 import 同一个依赖时它被装了**两遍** | 去重表的长度按值传递，兄弟递归之间不共享：父先装 A（表长变 1），再装 B 时仍按旧表长查找，B 里 import 的 A 查不到 | 表长放进**一格内存**（指针传参）—— Loment 没有 out 参数也没有可变全局 |
| 6 | IR 里的字符串常量写成 `c"\0D\0A"`（CRLF）而不是 `c"\0A"` | 驱动按**原始字节**读源码，而参考实现用 `read_text`（通用换行）；源码里到处是**跨行的字符串字面量**，CR 就原样进了字面量 | 读入后 `strip_cr()`（丢 CR 保 LF） |

第 5、6 条尤其值得记：第 5 条是"没有 out 参数/可变全局时，共享可变状态只能装箱"这个语言事实
第一次咬到真实程序；第 6 条是 docs/150 里那条 CRLF 老坑的**同源变体**（上次是夹具写单元时踩，
这次是驱动器读文件时踩）—— 凡是"把源文本交给编译器"的地方都要问一句：换行归一了吗？

第 3 条是**语言侧的真缺口**，驱动只是绕开了：任何用户写 `let entry: u32` 都会踩到。
要么在生成期把撞名的局部改名，要么把首块标签改掉 —— 留给 M85 后半或冻结前处理。

### M85 前半：驱动自己装载 ✅（2026-09-11）

装载原本在夹具里（`_unit_text`）。现在驱动自己做：

1. 入口路径来自 `/proc/self/cmdline` —— `_start` 的 argc/argv 在栈上，不写内联汇编拿不到，
   而 `/proc` 给了同样的信息（代价是一次 `openat`+`read`）。
2. `use "..."` 递归解析：父文件先读进**自己的槽**（单元缓冲顶部每层一个 `SLOT`），
   再递归处理依赖，最后才把自己补上去 —— 顺序即"依赖在前"，与 `resolve_deps` 一致；
   同一路径只装一次（去重表按字节比较）。
3. **只有 `.lomt` 算依赖**：`lomentc` 的 parser 把 `use "...lom"` 记进 `mod.uses`（L0 声明，
   由 lomc 处理），只有 `.lomt` 进 `mod.imports`。驱动按同一规则过滤 —— 否则 `demo.lomt`
   会把 `lom/fujr.lom` 也拼进单元（多出整整一个模块）。
4. 单元里缺 `Option`/`Result` 时注入预置枚举（判据 = 词法扫 `enum X`，所以**字符串字面量里**
   写着 `enum Option<T>` 不会误判），注入点必须在**单元末尾**（参考实现是把预置枚举 append
   到模块枚举之后）。

判据 `test_m85_selfhosted_driver_compiles_corpus`：同一个二进制、逐个入口路径，
把 `loment/examples/*.lomt` 与 `loment/selfhost/*.lomt` 全部编译一遍并逐字节比对 ——
**40/40 一致**。

**顺带修掉的取型 bug**（第 4 条）：`(v / 256) as u8` 里的 `as` 左操作数
类型，原先把**整个转型表达式**的类型（u8）当成了操作数类型 → `as u8` 被当成同宽转型
整个丢掉。新增 `operand_type()`：括号里按"第一个操作数，或某个**顶层**二元运算符右侧的
操作数"取型（镜像 `expr_type(Bin) = lt or rt`），不看括号内的实参 ——
按 token 逐个看会拿到 `load8(tmp, ..)` 里 `tmp` 的 `ptr` 类型。

### M85 后半 · 缺口①：符号名是平的 —— 已修（**诊断层**），并说明为什么不 mangling

**现象**：驱动同时装 `codegen.lomt` 与 `checker.lomt` 时立刻炸：

```
error: redefinition of global '@.str.find_paren.0'    # 字符串全局名 = @.str.<函数名>.<序号>
error: invalid redefinition of function 'tok_kind'
```

`checker.lomt` 与 `codegen.lomt` 有 **5 个同名私有函数**：`count_args` / `find_paren` /
`skip_type` / `tok_is` / `tok_kind`；其中 `count_args` 与 `skip_type` **语义还不同**
（一个数形参一个数实参；一个跳过 `<T>` 一个不跳），撞名会**静默算错**。当时把 checker 侧五个
都加 `chk_` 前缀绕开（这个改动保留 —— 它顺带澄清了语义差别）。

**2026-09-11 补上规则之后**，这事有了正确答案：**这不是"绕开"，而是语言该报的错。**

- 发射出来的符号名**本来就是平的**。内核线按**名字**找入口（`_start` / `timer_isr` /
  syscall 包装，见 docs/155 §3），所以私有符号不能靠 mangling 变成 `<模块>_<名字>` ——
  平名字就是跨线 ABI，改名会把内核侧的符号查找打断。（这是"不做 mangling"的**理由**，
  不是偷懒。）
- 平名字的代价是：**一个单元里顶层名字必须唯一**。违反时后端会发出两条 `define @helper`
  （非法 IR），而调用点还会解析到同一个函数（**静默错编**）—— 比报错严重得多。
- 所以补的是**编译器规则**：`lomentc.check` 现在跨 `deps + [mod]` 检查顶层同名
  （函数/结构体/枚举/常量，排除由 `load()` 注入每个模块的预置 `Option`/`Result`），
  报 E-DUP 口径的"与模块 X 重名 —— 单元的发射符号是平的 (ABI), 请改名"。
- **两个实现一致**：自举 checker 因为**不分模块**，早就把这类重名报成 E-DUP 了 ——
  所以这一轮是"参考实现补齐到自举版的行为"，顺手把 M81 的判据从"码集 ⊆"钉成"码集 ="
  （`test_m81_cross_module_dup_is_rejected`）。驱动的闸门也覆盖了它：驱动自己装载两个文件
  之后才发现，同样拒（`neg_across/entry.lomt`）。

判据：`lomentc_test` 91/91（新增 `test_unit_wide_unique_names`）、`loment_p8_test` 11/11
（新增跨模块负例 + 驱动闸门用例），语料 41 个目标上**零误报**（都验过）。

### M85 后半 · 缺口②：checker 覆盖面 0/41 → 40/40，闸门已开（2026-09-11 本轮）

上一轮把 `checker.lomt` 接进驱动时它**拒绝一切合法程序**（0/41 单元无诊断）。那一轮只是
把缺口列了出来；这一轮按清单逐个补，现在**40/40 个拼接单元一条诊断都没有** ——
唯一剩下的 `native_raii.lomt` 是**非目标**（参考实现的 IR 后端自己也发不出来，它本就不是
单元的合法形状），不进语料，所以不算 checker 的缺口。**驱动那道闸门因此打开了。**

判据：`loment_p8_test::test_m85_checker_accepts_corpus_units` —— 41 个单元（依赖 + 本文件 +
预置枚举，与驱动器装载的同一份）跑自举 checker，除登记缺口外必须零诊断；缺口一旦被修好，
测试会提醒更新清单（不让它悄悄过期）。

补的九处（都是"token 层线性扫描"这个实现的固有盲区）：

| # | 假报 | 根因 | 修法 |
|---|---|---|---|
| 1 | `3@n:Some` `Ok` `Err`（**每个**单元） | `enum Option<T> { Some(T), None }` 里的变体声明长得像调用 | 第二遍跳过 `enum`/`struct`/`trait` 的声明体 |
| 2 | `3@n:return` | `return (a + b)` 被当成"调用名为 return 的函数" | 关键字表（`chk_is_kw`）：关键字后面就算跟 `(` 也不是调用 |
| 3 | `3@n:blk_write`（guard） | `guard NAME(idx)` 里的域名被当函数 | 前一个 token 是 `guard` 就跳过 |
| 4 | `2@n:T` `2@n:Pair` | 泛型：`fn f<T>` 的 `T` 未登记；`Pair<u32>` 的 `<...>` 没跳过 | 声明处把 tparam 登记成类型名（**不查重**——多个声明各用 `T` 合法）；`chk_skip_type` 跳过 `<...>` |
| 5 | `2@n:Result`（`native_res`） | 预置枚举在**单元末尾**（`lomentc.load` 是 append），而 `-> Result<u32,u32>` 在文件开头 | 加"第零遍"先把类型名收齐再查签名 |
| 6 | `2@n::`（`native_res`） | `if let Result::Ok(v) = r` 里的 `let` 是**模式**不是带类型的绑定 | 前一个 token 是 `if` 就跳过（与 codegen 同一条守卫） |
| 7 | `native_concat` 全篇 `str_concat` | 内建表没跟上（M2 新加的内建只在 `lomentc` 里） | 补进 `is_builtin` 的名字表 |
| 8 | `native_trait` 全篇错位（`2@10:-`） | `fn measure(self) -> u32;` 是**裸 `self`**（不带 `&`），被读成"参数名 self、类型 ;"；而且签名式方法没有函数体，返回类型扫描会一路走到 impl 块的 `{` | `scan_params` 认裸 `self`；返回类型扫描在 `;` 也终止 |
| 9 | `native_trait` 报 E-DUP | 两个 impl 都声明 `measure` —— 平坦符号表里必然撞名（编译器真实的派发名是 `Small_measure`/`Big_measure`） | impl 块**不在检查器子集内**（方法调用一律走 `x.m()`，已被 `.` 那条规则排除；方法体内的调用检查照做） |

**过程中抓到的两个真 bug（都是"崩溃而不是报错"，值得单独记）**：

1. **越过 eof 的游标**：第二遍的声明体跳过正好落在文件尾时，循环尾还做了一次 `i = i + 1`
   → 游标进到词法缓冲**之外**的未初始化字节 → 那里若恰好不是 `eof` 就一路读下去，直到
   读到未映射内存而段错误。症状是"单个 enum 崩溃、两个 enum 正常"，而且**重编译一次结果
   就变**（堆布局运气）—— 这类"布局相关"的崩溃最难认，最后靠"把 `errs`/`toks` 换成 1 MiB
   看它还崩不崩"分离出结论：不是缓冲太小，是游标跑飞了。
2. **跳过之后又前进**：`i = chk_skip_body(...)` 返回的正好是**下一个声明的关键字**，而循环尾
   又 `i = i + 1` → 把那个声明整个略过（`struct B {..}` 后面紧跟 `enum C {..}` 时，
   `C` 的变体就会被当成调用）。修法是"跳过了就不再加一"（用 `skipped` 标志，Loment 没有
   `continue`）。

### 闸门打开：驱动先 check 再发射 ✅

覆盖面到 40/40 之后，`driver.lomt` 把 checker 接上并**先检查后发射**：

```
./fujoc-s loment/selfhost/neg/unknown_fn.lomt    # 退出 1, stderr 报诊断, stdout 无 IR
./fujoc-s loment/examples/demo.lomt > demo.ll    # 退出 0, 产物与参考逐字节相同
```

诊断格式与 p8 的 C 夹具同源（`E<码> @<token> line <行>: <片段>`）。判据
`test_m85_driver_checks_before_emitting`：6 个负例全部非零退出 + 带诊断 + **不产出 IR**；
正例零退出。**语料那 40 个单元的装载测试同时也在守着这件事**——闸门要是误报，那个测试会先红。

**原来的假报清单（2026-09-11 早，13 文件版，已按下表逐条销掉）**：

| 构造 | 现象（错误码@token） | 缺什么 |
|---|---|---|
| 内建函数 | `3@70:load32` `3@140:store32` `3@15:str_concat` | 调用点只查函数表，**内建表没进检查** |
| 枚举构造 | `3@53:Circle` `3@23:Some` `3@27:Err` | 把 `E::V(x)` 当函数调用 |
| 跨模块调用 | `3@358:double`（demo → mathutil） | 检查器**不解析 `use`**（M81 的子集边界），单文件跑必然报 |
| 泛型声明 | `2@39:T` `2@234:Pair` | `fn f<T>` / `Pair<u32>` 的 `<...>` 没跳过，形参与类型都读错位 |
| `guard` | `3@25:blk_write` | 域检查语句被当成函数调用 |
| 方法与 trait | `native_trait.lomt` 报 1/2/4 三种码 | `impl`/`trait` 块、`x.m()` 全不认识 |
| 语句边界 | `3@335:return` `2@20:{`（`native_raii`） | 某处解析错位后把后续 token 当成调用实参 |

清单本身有信息量：它说明"子集检查器"与"能当闸门的检查器"之间差的不是规则条数，
而是**对内建/泛型/枚举/方法/trait 这批构造的建模**。补完之前它只能继续当 M81 那种
"负例集判定"用。

## M86 · 未做（诚实说明）

| # | 里程碑 | 为什么现在做不了 |
|---|---|---|
| M86 | 自举性能优化 | 依赖 M85 收尾（checker 接进驱动后才有稳定的端到端基线） |

## M85 后半 · 还没做完的部分（诚实说明）

| # | 缺口 | 说明 |
|---|---|---|
| ~~符号表全单元扁平~~ | **已修**（见上文缺口①）：单元级名字唯一成了编译规则，两个实现一致报 E-DUP；不做 mangling 是因为平名字就是跨线 ABI（docs/155 §3） |
| `lomentc_test` 的判据还没搬到自举版 | 驱动现在能装载 + 检查 + 发射（40/40 语料、6/6 负例、二阶段定点），但 `lomentc_test` 那 90 条判据有不少是 *Python API 特有*的（Rust 输出形状、错误消息措辞）；要把能映射的那些逐条搬到驱动器上跑，M85 的字面判据才算满足 |

自举进度是真实的：**lexer → parser → checker → codegen 四段都已用 Loment 实现，
并分别与 Python 版逐 token / 逐字符 / 逐错误码 / 逐字节对照通过**（M79–M82），
且**四段都能被 Loment 版 codegen 编译出与参考逐字节相同的 IR**，后端进而达到三阶段定点（M84）。

**M82 已收官**：全部 40 个可发射目标逐字节一致（唯一剩下的 `native_raii.lomt` 参考实现自己就报
`native: inb 暂未在 IR 后端实现`，是非目标）。**M85 前半**（驱动器自己做装载）也已达成 ——
同一个二进制按入口路径把整个语料编译一遍，逐个逐字节一致。**现在剩下的自举长杆只有
"符号作用域"与"checker 的 impl/trait 建模"这两件。**

## M87 · 引导脚本 ✅

`python tools/loment_bootstrap.py` —— 一条命令走完自举前端：

1. 重新生成 lexer/parser/checker 的形式对象并与磁盘**逐字节**核对；
2. 跑 M79/M80/M81 三项对照；
3. 打印报告（`--json` 可机器读）。

## M88 · 发布校验和 ✅ 部分

`python tools/loment_release.py --checksums loment/build/SHA256SUMS` 产出
**125 行** sha256 清单（与 `release-manifest.json` 同源、换行无关）。
**未做**：git tag 未创建/未推送（分支与并发开发线共用，打 tag 与推送需作者确认）。

