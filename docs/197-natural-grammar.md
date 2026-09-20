# 197 · `natural` —— Loment 自己的自然语言写法

> 上游：`docs/188`（`choose write grammar` 与"表层语法只让拼法、不让语义"那条框）·
> `docs/179`（多语法前端）· `docs/186`/`docs/187`（C / Python 翻译器）·
> `docs/189` §4.1（新工具要有 Loment 孪生）· `docs/182` §1.9（"读 L1 源的入口"清单）。
>
> 代码：`tools/nltrans.py`（翻译器）· `loment/nltrans/Sample.nl` + `Sample.lomt`
> （同一程序的两种拼法）· `tools/loment_nltrans_test.py`（判据，12/12）。

## 0. 一句话

用户 2026-09-20 的要求是：**为 Loment 发明一门全新的语法，贴近人类自然语言**
（点了 `say` / `talk` / `paint` 三个动词）。落下来的就是这一门：
**一个动词起头的句子 = 一条语句**。

```rust
program sample

remember LIMIT as 3

to add with a as a whole number and b as a whole number giving a whole number
    give back a plus b
end

to _start
    say "hello "
    say the number add of 2, 3
    paint 42 at 0 in some_buffer
    talk to the machine 60 with 0, 0, 0
end
```

`choose write grammar natural` 或后缀 `.nl` 认这一门。

## 1. 它与那六门**不同类** —— 这一条决定了后面全部推论

`docs/188` §7.1 那六门（C / Python / Java / C# / C++ / Go）是**别人已经存在的语言**。
所以那六门里的每一条子集线都是**碰上的**："能表达的就转，表达不出来的就报错"。

这一门是**我们自己发明的**。同一个原则（只让拼法与形状）仍然成立，但推力反过来了：

| | 六门 | 这一门 |
|---|---|---|
| 子集线从哪来 | **碰上的**（源语言的语义形状） | **画出来的**（设计决定） |
| 谁在说话 | 那个语言 | **Loment**（`language: "loment"`） |
| 报错的性质 | "这一版还没做" | "**这一版不收**"（是设计，不是缺口） |

**⇒ 它是第一个"从零画子集"的样本**，所以文档要把每一条线**为什么画在那里**写清楚。

## 2. 形状：动词起头

自然语言的骨架是"动词 + 宾语"，语法就是这句话的实现。**语句以换行为界**
（自然语言里没有分号），块用 `end` 收尾（缩进只是给人看的，不进语法）。

| 句子 | 落成的 Loment |
|---|---|
| `program tour` | `module tour`（第一句，必须有） |
| `remember LIMIT as 3` | `pub const LIMIT: i64 = 3;` |
| `to add with a as a whole number and b as a whole number giving a whole number` … `end` | `pub fn add(a: i64, b: i64) -> i64 { … }` |
| `let n be 5` | `let n: i64 = 5;` |
| `let b be 200 as a byte` | `let b: u8 = 200;` |
| `let fb be a buffer of 1024 bytes` | `let fb: ptr = alloc(1024);` |
| `set n to 6` | `n = 6;` |
| `when n is above 3` … `otherwise when …` … `otherwise` … `end` | `if … { } else if … { } else { }` |
| `while n is above 0` … `end` | `while … { }` |
| `for i from 0 to 10` … `end` | `for i in 0..10 { }`（上界**不含**） |
| `give back n times 2` | `return (n * 2);` |
| `do f of 3` | `f(3);` |
| **`say "hi"`** | `syscall4(1, 1, str_ptr("hi") as u64, str_len("hi") as u64);` |
| **`say the number n`** | `nl_write_num(1, n as i64);`（辅助函数**只在用到时才发**） |
| **`talk to the machine 60 with n, 0, 0`** | `syscall4(60 as u64, n as u64, 0 as u64, 0 as u64);` |
| **`paint 7 at n in fb`** | `store8(fb, n as u32, 7 as u8);` |

加粗那四句就是用户点名的那几个动词。

### 2.1 表达式：词形与符号形是**同一件事**

运算符两套拼法，**翻出来必须逐字节相同**（判据钉着）：

| 词形 | 符号形 | | 词形 | 符号形 |
|---|---|---|---|---|
| `plus` / `minus` | `+` / `-` | | `is` | `==` |
| `times` / `over` / `modulo` | `*` / `/` / `%` | | `is not` | `!=` |
| `and` / `or` / `not` | `&&` / `\|\|` / `!` | | `is above` / `is below` | `>` / `<` |
| `shifted left by` / `shifted right by` | `<<` / `>>` | | `is at least` / `is at most` | `>=` / `<=` |

位运算（`&` `\|` `^` `<<` `>>`）**只有符号形** —— 自然语言里没有它们的说法，
硬造一个词反而是发明黑话。

另外两条取名字的写法：

* **调用**：`add of 2, 3`、`gcd of 48, 18`；**没有实参就直接写名字**（`say the number run`）。
* **取字段**：`the cents of e`（`the` 是它的标记；这一版还没有结构体，但语法留着了）。

### 2.2 类型短语

| 自然语言 | Loment | | 自然语言 | Loment |
|---|---|---|---|---|
| `a whole number` | `i64` | | `a truth` | `bool` |
| `a 32-bit whole number` | `i32` | | `text` | `str` |
| `a byte` | `u8` | | `a buffer` | `ptr` |
| `a count` / `a 64-bit count` | `u32` / `u64` | | `nothing` | `()` |

`a` / `an` 可省；`i64` / `u8` 这些**直接写也收**（要跟别人说同一件事时，缩写省事）。

## 3. 五条**不是翻译、是决定**的东西

### 3.1 `say` 分两句

`say <文本>` 与 `say the number <数>`。**不合并**是因为合并就要**猜表达式的类型**，
而这一门（与 Loment 一样）**没有类型推断** —— 猜错的表现是"把一段文本按数去打"，
而那种错**两边都编得过**。分开写是让作者说清楚，代价是多打三个词。

### 3.2 类型是**查**出来的，不是**推**出来的

`let` 的类型只有四条来路（`Parser.type_of`）：

1. 句子里写了的 `as <类型>`；
2. **字面量**（数 -> `i64`、文本 -> `str`、`true`/`false` -> `bool`）；
3. **声明过的名字**（模块常量、形参、前面的 `let`）—— 查表；
4. **运算符的定则**（比较与 `and`/`or` 出 `truth`、其余**同型则同型**）—— 查法则；
5. **被调函数的声明**（内建表 / 外部声明 / 本文件那几张 `to … giving`）—— 查声明。

够不着就**报错**，不是"默认按 i64 算"—— 后者会把一个真的类型错推后到别处炸。

**唯一不定的是"取字段"**（这一版没有结构体，够不到）。

### 3.3 为了第 5 条，源要**读两遍**

第一遍只为收"函数名 -> 返回类型"那张表，第二遍拿它把类型查全。两遍用**同一份记号流**
（第一遍只读不动），差别只在 `strict_types`。

不多读这一遍的话，每个"let 一个调用结果"都得写成
`let a be (sum_to of LIMIT) as a whole number` —— 而少写就报错。那条路是**把成本转嫁给
用户**，而这里读两遍就够了。

### 3.4 降级时**代作者补宽度**

`talk` 的四个实参一律补 `as u64`、`paint` 的下标补 `as u32`、值补 `as u8`、
`say the number` 补 `as i64`。理由：**这些宽度是那一句的意思的一部分，不是作者要操心的事**
（`talk to the machine` 的意思**就是** `syscall4`）。恒等转换是合法的（实测
`let x: i64 = a as i64;` 过检查），所以不必先判断宽度。

### 3.5 换行只有**不可能当名字**的那些才算续行

`_CONTINUE` 里只放运算符（含多词运算符的每一个词）、标点、结构关键字。
**`a` / `an` 不在里面** —— 它是踩出来的：

```
let x be a          <- `a` 是个**形参名**
let y be b
```

第一版把 `a` 当"类型短语里那个冠词"收进了续行表，于是这两行**被粘成一句**，
而报错落在第二行上（"该断了却还有 `let`"），与真正的原因隔着一层。
**多收一个比少收一个坏**：少收是"合法的一句读不通"（响亮），多收是"两句悄悄变一句"。

## 4. 判据：**同一个程序、两种拼法、同一串字节**

这一门**没有"对面那个编译器"**（它不是别人的语言），所以对照组换成**同一程序的
Loment 写法**，判据是 `docs/188` §7.1.1 那条的**原话**：

> **两份源码翻出来的 Loment 逐行完全相同** —— 比的是**同一个字符串**，不是"差不多"。

| 判据 | 钉什么 |
|---|---|
| `test_two_spellings_one_program_translate_the_same` | `front_door(Sample.nl)` 的产物与 `Sample.lomt` 的**原始字节**一个不差 |
| `test_the_natural_program_runs_to_the_derived_number` | 真编真跑 -> **47**，而且标准输出是 `sample: 47 painted 42` |
| `test_the_loment_twin_runs_to_the_same_number_by_itself` | 孪生那份**自己**也跑到 47（否则上一条退化成"比两份文本"） |
| `test_out_of_subset_is_loud_and_points_at_the_line` | 11 档子集外各报各的 |
| `test_line_number_of_a_defect_is_the_file_line_not_the_body_line` | 正文拼起来之后行号仍指回原文件（**夹具自证能分辨两种读法**：6 ≠ 4） |

**期望值是推出来的**：`sum_to(3)=3`、`gcd(48,18)=6`、`label(8)=1`、`tally(100)=7`、
`LIMIT*10=30` ⇒ **47**（`Sample.lomt` 的头注里写着同一份推导）。

### 4.1 一处**刻意的不对称**

`loment/nltrans/Sample.lomt` 头上那三行注释**是产物的一部分**
（`lomt_from.emit_lomt` 的生成头），所以它在那份文件里。这不是"测试写死了实现" ——
**这一条判据的对象就是"前端交给编译器的那串字节"**，生成头本来就在里面。
谁改了生成头，这条就红。

## 5. 登记处（`docs/188` §7.1 那张表 + `feedback` 里那七处）

| 登记处 | 加了什么 |
|---|---|
| `potato.GRAMMARS` | `"natural"`（**出厂锁的规范名表**） |
| `potato_from.GRAMMAR_ALIASES` | `nl` / `natural` / `lument` -> `natural` |
| `potato_from.LANGS` + `EXT` | `from_natural` + `.nl` |
| `potato_from.detect_lang` | **两条一起**才算命中（整行 `program <名字>` + `give back`） |
| `lomt_from._TOOLS` | 一门一行（`nltrans` / `Unsupported NaturalError` / 工具路径 / 子集提示） |
| `loment_diag.LANG_CARDS` + `LANG_EDGE_EN` + `LANG_ABI_EN` | 三张表各一条 |
| `loment_release.GLOBS` **与** `lomrel.lomt` | `tools/nltrans.py` + `tools/loment_nltrans_test.py` + `loment/nltrans/*.nl` + `*.lomt`（**两处插入位置对齐**） |
| `loment_publish.py` 的 `paths` | `tools/nltrans.py` |
| `tools/ci.py` 的 `STATIC_CHECKS` | `loment_nltrans_test` |
| `.gitattributes` | `*.nl text eol=lf` |
| `loment_grammar_test.LOCKED_ALIASES` | 三条 —— **这一笔就是"改锁"** |

### 5.1 规范名为什么是 `natural` 而不是 `lument`

这次设计的委托名是 "Lument"，所以 `lument` 是个自然的候选。**没选它**，因为
`lument` 与 `loment` **只差一个字母**，而 `grammar loment` 是**合法的**（= 原生写法）。
近邻词当规范名的代价是"少打一个字母就静默换成另一种读法"，
而**静默换读法**正是这一门最不该有的失败。`lument` 仍然收（源侧宽松），
但不进对象 —— 对象侧只许 `potato.GRAMMARS` 里那几个。

### 5.2 自举侧那一半**没做**，而且这是**已知的**边界

安装版 `loment` 编不了 `choose write grammar natural`，它说的是：

> 这份源的 `choose write grammar` 自举侧收不了 —— 只收 `choose write grammar loment`
> 这一种拼法 …… 别的拼法/写法要等翻译器的 Loment 孪生（`docs/189` §4.1）

这与六门**是同一条边界**（`docs/188` §8 那条"别的写法那一半"），不是这一门新欠的账。
参考实现（`lomentc` + `lomelf`）那条路**是通的**，判据走的就是它。

## 6. 还没落地的（如实记）

1. **`tools/nltrans.py` 的 Loment 孪生**（`docs/189` §4.1）。它现在是 Python，
   按"0.1.4 之前移除全部非 Loment 代码"那条指令**它也要有孪生**。
   规模要说实话：它是一份**带递归下降 + 类型表 + 发射器**的翻译器（约 1000 行），
   与"六门翻译器还没有孪生"是**同一笔**欠账 —— 七份一起做比一份一份做便宜，
   因为判据的形状一模一样（同一份源喂两个实现、比 stdout 逐字节）。
2. **子集边界本身**（这一版**不收**）：`use`、结构体、枚举、`match`、切片、
   `ptr` 算术、函数指针、`give back` 不带值（Loment 也收不了 —— 见 §7）。
   都在解析那一步**响亮地拒**，消息里说清是"这一版不收"。
3. **`do` 只收调用**：`do paint …` 不是句子（`paint` 是语句不是表达式）。
4. **编辑器入口**（`lomfmt` / `lomdoc` / LSP）仍会看到 `choose write grammar natural`
   那一行 —— 与六门同一条边界（`docs/182` §1.9 那张清单）。

## 7. 写这一门时撞出来的两处**工具链事实**

### 7.1 `return;`（不带值）**不是合法 Loment** —— 而自举驱动会**段错误**

`fn f() { return; }` 在参考实现上是一句干净的 `E19`：

```
[ERR] t.lomt: 5:15: 期望表达式，得到 ';'
```

而**自举驱动（安装版 0.1.4）在那份源上段错误**（退出 139，`Segmentation fault`，
不打任何诊断）。所以这一门的 `give back` **必须带一个值**，
不返回值就只能走到函数末尾 —— 那条纪律是从这里来的。

**"语法错 -> 段错误"本身是另一件事**，记在这里当线索：它不是这一门引入的，
但这一门撞上了它。

### 7.2 **无后缀字面量在自举驱动里被当成 u32**（`i64` 上下文里取负 32 位回绕）

```
let a: i64 = 0 - 12345;
```

* **参考实现**为这一句发的是 `sub i64 0, 12345`（正确）；
* **自举驱动**编出来跑，`a as u64` 给的是 **4294954951**（= 2^32 − 12345）。

同一个形状在**调用点**也成立：`neg(0 - 12345)` 传进去的是 4294954951，
于是函数体里 `let zero: i64 = 0; zero - v` 算出 −4294954951。

**运行期的 `i64` 变量之间没有这个问题**（实测 `zero - v` 对 `v = -12345` 给 12345），
所以 `nl_write_num` 走"带类型的零"这条路：`let zero: i64 = 0; … if v < zero { … }`。

**这条写进 `tools/nltrans.py` 的文件头了**（那是"为什么不这么写"的现场），
它**不是**翻译器的限制，是**编译器的一处真 bug** —— 两边都编得过、只有一边算错，
正是本项目的红线。留给工具链那一侧。

## 8. 一句话的定位

**这一门不是"第六门"，是"第一门我们自己的"。** 六门证明的是
"别人写的东西也能读成 Loment"；这一门证明的是**反过来那一半**：
**Loment 自己也说得出别的样子，而说的仍然是同一件事。**
