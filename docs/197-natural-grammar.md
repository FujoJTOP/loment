# 197 · `natural` —— Loment 自己的自然语言写法

> 上游：`docs/188`（`choose write grammar` 与"表层语法只让拼法、不让语义"那条框）·
> `docs/179`（多语法前端）· `docs/186`/`docs/187`（C / Python 翻译器）·
> `docs/189` §4.1（新工具要有 Loment 孪生）· `docs/182` §1.9（"读 L1 源的入口"清单）。
>
> 代码：`tools/nltrans.py`（翻译器）· `loment/nltrans/Sample.nl` + `Sample.lomt`
> （最小面那一对，同一个程序）· `loment/nltrans/Surface.nl` + `Surface.lomt`
> （**完整面**那一对）· `loment/nltrans/surface_lib.lomt`（被 `use` 引进来的那份库）·
> `tools/loment_nltrans_test.py`（判据，19/19）。

## 0. 一句话

用户 2026-09-20 的要求：**为 Loment 发明一门全新的语法，贴近人类自然语言，
自然语言为一等公民**（点了 `say` / `talk` / `paint` 三个动词）。落下来的就是这一门：
**一个动词起头的句子 = 一条语句**，`choose write grammar natural` 或后缀 `.nl` 认它。

```rust
program tour

use json

remember LIMIT as 3

a Point has x as a whole number and y as a whole number
a Kind is either Small or Big carrying a whole number
a Sizer can size giving a count
a Point can be a Sizer
    to size giving a count
        give back the x of self plus the y of self
    end
end
a disk space called slots covers 0 to 4, and it can be taken back

to score with k as a Kind giving a count
    when k looks like a Kind that is Big carrying w
        give back w times 2
    end
    when anything else
        give back 1
    end
end

to _start
    let p be a Point with x as 4 and y as 5
    let xs be [1, 2, 3]
    say "score "
    say the number score of a Kind that is Big carrying LIMIT
    say " size "
    say the number the size of p
    paint 42 at 0 in the changeable run of xs
    talk to the machine 60 with the item 0 of xs, 0, 0
end
```

## 1. 它与那六门**不同类** —— 这一条决定了后面全部推论

`docs/188` §7.1 那六门（C / Python / Java / C# / C++ / Go）是**别人已经存在的语言**，
所以那六门里的每一条子集线都是**碰上的**："能表达的就转，表达不出来的就报错"。

这一门是**我们自己发明的**。同一个原则（只让拼法与形状）仍然成立，但推力反过来了：

| | 六门 | 这一门 |
|---|---|---|
| 子集线从哪来 | **碰上的**（源语言的语义形状） | **画出来的**（设计决定） |
| 谁在说话 | 那个语言 | **Loment**（`language: "loment"`） |
| 报错的性质 | "这一版还没做" | "**这一版不收**"（是设计，不是缺口） |

**⇒ 它是第一个"从零画子集"的样本**，所以文档要把每一条线**为什么画在那里**写清楚。

### 1.1 2026-09-22：砍掉"过度复杂"那一层（用户点出）

用户这一天的原话是 **「自然语法存在过度复杂的问题」**。先量了，再动手 ——
量出来的三处，与它们的根因：

| 量出来的 | 数 | 根因 |
|---|---|---|
| **每个运算符两种写法** | 15 个运算符 × 2 | 词形（`plus`）与符号形（`+`）**都收** |
| **类型短语比它要替换的东西更长** | `a whole number`(14 字符) 对 `i64`(3)；共 12 种 | 给每个类型都造了一个英文短语 |
| **"取一个东西"有五种壳** | `the x of p` / `ask p for size` / `item 0 of xs` / `the length of xs` / `the run of xs` | 一个 Loment 构造配一个句子 |

**根因一句话**：我做的是**与 Loment 表面的一一对应**，于是这一门是"Loment 的复杂度
**加上**一层句子开销"。而自然语言绑定只有在**严格更小**时才划算 —— 我把它做成了平行的一套。
那一串标识符冲突（`a`、`and`、`called`）不是运气差，是这个设计的**症状**：它拿了
三十几个普通英文词当结构，于是普通名字一路撞上去。

**砍法（用户选的是"去重、不减能力"）**：

1. **一个运算符只有一个写法** —— 留词形，符号形点名拒；位运算（`&` `|` `^`）
   没有自然语言说法，所以**只有符号**（它们也只有一个写法）。
2. **类型只留 6 个英文基名**，其余一律照 Loment 写（`i32` / `[i64; 3]` / `[i64]` /
   `mut [i64]` / `Option<i64>` / `Result<i64, str>`）。12 种 → 6 种。
3. **"取一个东西"收成一条规则** `the <什么> of <东西>` —— 字段、方法、下标、长度、
   切片五格同壳；数组那一族（字面量、切片、定长）照 Loment 写，于是这条规则**没有例外**。

**一处刻意的例外**（写在第 2 条的下面）：那 6 个的 Loment 拼法**同时收**，
因为**编译器报错时印的就是它们**（`return 类型 u8，函数声明 i32`）——
得能把读到的那句话原样写回去。这是"两种写法"里唯一一处，理由是**可读回**，不是方便。

**能力一条没减**：`Surface.nl` 那一篇（结构体 / 枚举 / trait / impl / 泛型 / match 与
if let / 能力域 / `use` / 数组切片 / Option·Result / `?`）**改前改后翻出来的 Loment
逐字节相同** —— 砍掉的是**表面**，不是能力。这一条本身就说明砍对了地方。

**改前改后（实测）**：

| | 改前 | 改后 |
|---|---|---|
| **要认的拼法（合计）** | **46** | **22** |
| 符号运算符 | 19 | 3（只剩位运算） |
| 类型短语 | 12 | 6 |
| "取一个东西"的壳 | 5 | 1 |
| 续行词 `_CONTINUE` | 54 | 38 |
| 保留词 `_RESERVED` | 39 | 36 |
| 翻译器 | 2196 行 | 2183 行 |

**最后一行要如实说**：**翻译器的行数几乎没动**。因为砍掉的是"多出来的那条路"，
而每一条路本身只有几行；它从来不是"复杂"的量。**要认的拼法才是** ——
46 → 22 是这一轮的账，而保留词只降 3 个（那些词现在是**唯一**写法，删不得）。

## 2. 语法表（完整面）

### 2.1 顶层（声明）

| 句子 | 落成的 Loment |
|---|---|
| `program tour` | `module tour`（第一句，必须有） |
| `use json` / `use "pack.lomt"` | `use json` / `use "pack.lomt"` |
| `the standard library is [not] available` | `choose std` / `choose no_std` |
| `remember LIMIT as 3` | `pub const LIMIT: i64 = 3;` |
| `a Point has x as a whole number and y as a whole number` | `pub struct Point { x: i64, y: i64 }` |
| `a Kind is either Small or Big carrying a whole number` | `pub enum Kind { Small, Big(u32) }` |
| `a Sizer can size giving a count` | `pub trait Sizer { fn size(self) -> u32; }` |
| `a Point can be a Sizer` … `end` | `impl Sizer for Point { … }` |
| `a disk space called slots covers 0 to 4, and it can be taken back` | `capability slots : disk[0..4] revocable` |
| `leave out "the network"` | `excluded "the network"` |
| `someone else wrote read_at with fd as a count giving a whole number` | `pub extern fn read_at(fd: u32) -> i64;` |
| `to add with a as a whole number and b as a whole number giving a whole number` … `end` | `pub fn add(a: i64, b: i64) -> i64 { … }` |
| `to largest for any T with a as a T and b as a T giving a T` … `end` | `pub fn largest<T>(a: T, b: T) -> T { … }` |

**任何一条声明末尾加 `, only here` 就去掉 `pub`**（常量除外 —— 见 §4.6）。
`remember` 的私有那一种走翻译器自己发（`const NAME: T = v;`），公开的走 Potato 对象。

### 2.2 语句

| 句子 | 落成的 Loment |
|---|---|
| `let n be 5` / `let b be 200 as a byte` | `let n: i64 = 5;` / `let b: u8 = 200;` |
| `let fb be a buffer of 1024 bytes` | `let fb: ptr = alloc(1024);` |
| `let y be find of x unless it failed` | `let y: i64 = find(x)?;`（`?` **只能**在 let 的右半边） |
| `set n to 6` / `set item 0 of xs to 9` | `n = 6;` / `xs[0] = 9;` |
| `say "hi"` / `say the number n` | 写标准输出（文本 / 十进制数） |
| `talk to the machine 60 with n, 0, 0` | `syscall4(n, a, b, c)` |
| `paint 255 at 0 in fb` | `store8(fb, 0, 255 as u8)` |
| `do f of 3` | `f(3);` |
| `give back n times 2` | `return (n * 2);` |
| `guard the slots space at 2` | `guard slots(2);` |
| `when n is above 3` … `otherwise when …` … `otherwise` … `end` | `if … { } else if … { } else { }` |
| `while n is above 0` … `end` | `while … { }` |
| `for i from 0 to 10` … `end` | `for i in 0..10 { }`（上界**不含**） |
| `when k looks like a Kind that is Big carrying w` … `end`（**一条臂**） | `if let Kind::Big(w) = k { … }` |
| 同上，**两条以上**，或带 `when anything else` | `match k { Kind::Big(w) => { … } … }` |

### 2.3 值

**"取一个东西"只有一种形状：`the <什么> of <东西>`。** 取字段、取方法、取一格、
取长度、取切片**五格同一个壳**（`<什么>` 是字段名 / 方法名 / `item` / `length` / `run`，
由名字自己说了算）：

| 写法 | 落成的 Loment | | 写法 | 落成的 Loment |
|---|---|---|---|---|
| `the x of p`（字段） | `p.x` | | `the size of p`（方法） | `p.size()` |
| `the item 0 of xs` | `xs[0]` | | `the length of xs` | `slice_len(xs)` |
| `the run of xs` | `&xs` | | `the changeable run of xs` | `&mut xs` |

其余的值：

| 写法 | 落成的 Loment | | 写法 | 落成的 Loment |
|---|---|---|---|---|
| `a Point with x as 1 and y as 2` | `Point { x: 1, y: 2 }` | | `[1, 2, 3]`（数组字面量） | `[1, 2, 3]` |
| `a Kind that is Big carrying 5` | `Kind::Big(5)` | | `f of a, b`（调用） | `f(a, b)` |
| `something carrying x` | `Option::Some(x)` | | `nothing to carry` | `Option::None` |
| `a success carrying x` | `Result::Ok(x)` | | `a failure carrying x` | `Result::Err(x)` |

**没有实参的调用就写名字本身**（`say the number run` → `run()`）。
**数组那一族照 Loment 写**（字面量 `[1, 2, 3]`、切片 `[i64]`、定长 `[i64; 3]`）——
它们不是"取一个东西"，所以 `the … of …` 那条规则没有例外。

### 2.4 类型：**6 个英文基名 + 其余照 Loment 写**

| 自然语言（6 个） | Loment | | 直接写 | Loment |
|---|---|---|---|---|
| `a whole number` | `i64` | | `i32` / `u64` / `i8` … | 同名 |
| `a count` | `u32` | | `[i64; 3]` | 定长数组 |
| `a byte` | `u8` | | `[i64]` | 切片 |
| `a truth` | `bool` | | `mut [i64]` | 可改切片 |
| `text` | `str` | | `Option<i64>` / `Result<i64, str>` | 同名 |
| `a buffer` | `ptr` | | `Point`（结构体/枚举名） | 同名 |

`a` / `an` 可省；复数也收（`whole numbers`）。

上面那六个的 Loment 拼法（`i64` / `u32` / `u8` / `bool` / `str` / `ptr`）**同时收** ——
**唯一的一处"两种写法"**，理由不是"方便"，而是**编译器报错时印的就是它们**
（`return 类型 u8，函数声明 i32`）：得能把读到的那句话原样写回去。

### 2.5 运算符：**一个运算符只有一个写法**

| 词形（唯一写法） | Loment | | 词形（唯一写法） | Loment |
|---|---|---|---|---|
| `plus` / `minus` | `+` / `-` | | `is` | `==` |
| `times` / `over` / `modulo` | `*` / `/` / `%` | | `is not` | `!=` |
| `and` / `or` / `not` | `&&` / `\|\|` / `!` | | `is above` / `is below` | `>` / `<` |
| `shifted left by` / `shifted right by` | `<<` / `>>` | | `is at least` / `is at most` | `>=` / `<=` |
| `&` `\|` `^`（**只有符号**） | 位运算 | | 一元 `minus 5` / `not ok` | `(-5)` / `(!ok)` |

位运算**只有符号形** —— 自然语言里没有它们的说法，硬造一个词反而是发明黑话。
**其余每一个都只留词形**：`a + b` 不再收（它是"少学一半"的地方，也是"这一门比 Loment
简单"这句话的唯一凭据）。判据钉着**符号形点名拒**。

## 3. 五条**不是翻译、是决定**的东西

### 3.1 `say` 分两句

`say <文本>` 与 `say the number <数>`。**不合并**是因为合并就要**猜表达式的类型**，
而这一门（与 Loment 一样）**没有类型推断** —— 猜错的表现是"把一段文本按数去打"，
而那种错**两边都编得过**。

### 3.2 类型是**查**出来的，不是**推**出来的

`let` 的类型只有这几条来路（`Parser.type_of`），每一条都是**读一处已写下的东西**：

1. 句子里写了的 `as <类型>`；
2. **字面量**（数 -> `i64`、文本 -> `str`、`true`/`false` -> `bool`、列表 -> `[T; N]`）；
3. **声明过的名字**（模块常量、形参、前面的 `let`）；
4. **运算符的定则**（比较与 `and`/`or` 出 `truth`、`?` 拆开一层、其余**同型则同型**）；
5. **被调函数的声明**（内建表 / 外部声明 / 本文件那几张 `to … giving`）；
6. **结构体字段**、**方法声明**、**容器的元素**（`item i of xs` 的类型）。

够不着就**报错**，不是"默认按 i64 算"—— 后者会把一个真的类型错推后到别处炸。

### 3.3 为了第 5 条，源要**读两遍**

第一遍只为收"函数名 -> 返回类型 / 结构体字段 / 变体 / 方法"几张表，第二遍拿它们把类型
查全。两遍用的**同一份记号流**（第一遍只读不动）。

**第一遍对类型名与形状宽松**：Loment 不在乎声明的先后（实测把结构体写在用它的函数
**之后**照样编得过），而单遍解析会在第一遍就炸。所以第一遍"只读不判"，第二遍照旧严格。

### 3.4 降级时**代作者补宽度**

`talk` 的四个实参一律补 `as u64`、`paint` 的下标补 `as u32`、值补 `as u8`、
`say the number` 补 `as i64`。理由：**这些宽度是那一句的意思的一部分**，
不是作者要操心的事（`talk to the machine` 的意思**就是** `syscall4`）。
恒等转换是合法的（实测 `let x: i64 = a as i64;` 过检查），所以不必先判断宽度。

### 3.5 三处**边界踩出来的判断**（都在源码里写着为什么）

1. **换行只有"不可能当名字"的那些才算续行**（`_CONTINUE`）。多收一个比少收一个坏：
   少收是"合法的一句读不通"（响亮），多收是"两句悄悄变一句"。**踩了两次同一个坑**：
   第一版收了 `a`（`let x be a` 里那个 `a` 是形参名），第二版收了 `called`
   （`let other be called`）——两次的报错都落在**下一行**上，指不到点子。
2. **`a` / `an` 只在后面跟着一个已知类型名时才是字面量的开头**。不这么限，
   一个**参数名叫 `a`** 的函数会在 `when a is above b` 上被读成"一个结构体字面量"。
3. **实参只读到"后缀"那一层**：`total of p plus score of k` 读成 `total(p) + score(k)`，
   不是 `total(p + score(k))` —— 自然读法就是前者。想传算式进去就加括号。

## 4. 判据

### 4.1 两对语料，各钉三条

| 判据 | 钉什么 |
|---|---|
| `Sample.nl` -> **47** / `Surface.nl` -> **130 117**（退出 24） | 真编真跑，期望值**推出来**的 |
| `Sample.lomt` / `Surface.lomt` **自己**也跑到同一对数 | 否则"逐字节"退化成"比两份文本" |
| `front_door(X.nl).source == X.lomt` 的**原始字节** | `docs/188` §7.1.1 那条原话 |

`Surface.*` 是**完整面的答卷**：一篇里同时用到结构体、枚举、trait/impl（`ask p for size`）、
泛型、`match` 与 `if let`、能力域与守卫、数组与切片、`use` 进来的库函数、
`only here` 的私有常量、`choose no_std`。

### 4.1.1 这条"逐字节"属于哪一族（别把它当成新立的形状）

仓里现在有**两处逐字节**，它们**形状相同、对照面不同**，别混：

| | 对照面 | 在哪 |
|---|---|---|
| **对上游** | 同一个翻译器的**另一个实现**产出的 Loment | `docs/189` 第 18/20 格（`trans_core` 与 `lomtrans --lang python`）：`pytrans` 与 `lomtrans` 两边逐字节 |
| **对本门** | 同一个程序的**手写 Loment 版** | 这里：`front_door(Sample.nl)` 与 `Sample.lomt` 逐字节 |

两处都是"**同一串字节**"，所以形状一样 —— 将来给这一门补 Loment 孪生时，判据就是
"`nltrans.py` 与 `nltrans.lomt` 对同一份源给同一串字节"，与第 18/20 格**同一个模子**。

**真正与六门不同族的是"端到端"那一条**：六门是"跑出的数 == **那份源语言自己的编译器**
跑出的数"；这一门没有对面那个编译器（它不是别人的语言），所以换成"跑出的数 ==
**独立推出来的期望值**"。

### 4.2 其余九条

| 判据 | 钉什么 |
|---|---|
| `test_every_declaration_shape_lowers_onto_the_right_loment` | 六种声明各落在哪个 Loment 形状上，**逐字** |
| `test_match_and_if_let_are_told_apart_by_the_number_of_arms` | 一条臂 -> `if let`；两条/带兜底 -> `match` |
| `test_the_values_and_the_containers_lower_onto_their_forms` | 结构体/枚举/列表/下标/切片/长度/方法 |
| `test_optional_and_result_are_types_values_and_question_mark_only` | 那三样收，第四样**响亮地拒** |
| `test_the_generic_return_type_is_not_looked_up` | 泛型返回不进声明表；补 `as` 之后成立 |
| `test_out_of_subset_is_loud_and_points_at_the_line` | 十余档，各报各的 |
| `test_line_number_of_a_defect_is_the_file_line_not_the_body_line` | **夹具自证能分辨两种读法**（6 ≠ 4） |
| `test_content_detection_needs_both_signals` | 内容识别要`program <名>`+`give back`两条一起 |
| `test_word_operators_…` / `test_a_call_with_no_arguments_…` / `test_the_verbs_…` | 拼法别名 / 无参调用 / 三个动词落点 |

## 5. 登记处（`docs/188` §7.1 那张表）

| 登记处 | 加了什么 |
|---|---|
| `potato.GRAMMARS` | `"natural"`（**出厂锁的规范名表**） |
| `potato_from.GRAMMAR_ALIASES` | `nl` / `natural` / `lument` -> `natural` |
| `potato_from.LANGS` + `EXT` | `from_natural` + `.nl` |
| `potato_from.detect_lang` | **两条一起**才算命中（整行 `program <名字>` + `give back`） |
| `lomt_from._TOOLS` | 一门一行（`nltrans` / `Unsupported NaturalError` / 工具路径 / 子集提示） |
| `loment_diag.LANG_CARDS` + `LANG_EDGE_EN` + `LANG_ABI_EN` | 三张表各一条 |
| `loment_release.GLOBS` **与** `lomrel.lomt` | `tools/nltrans.py` + 判据 + `loment/nltrans/*.nl` + `*.lomt`（**两处位置对齐**） |
| `loment_publish.py` 的 `paths` | `tools/nltrans.py` |
| `tools/ci.py` 的 `STATIC_CHECKS` | `loment_nltrans_test` |
| `.gitattributes` | `*.nl text eol=lf` |
| `loment_grammar_test.LOCKED_ALIASES` | 三条 —— **这一笔就是"改锁"** |

### 5.1 规范名为什么是 `natural` 而不是 `lument`

`lument` 与 `loment` **只差一个字母**，而 `grammar loment` 是**合法**的（= 原生写法）。
近邻词当规范名的代价是"少打一个字母就静默换成另一种读法"，
而**静默换读法**正是这一门最不该有的失败。`lument` 仍然收（源侧宽松），
但不进对象 —— 对象侧只许 `potato.GRAMMARS` 里那几个。

### 5.2 对象里只放 `lomt_from` 真正会发的东西

`module` / 能力域 / 常量 / `excluded` / 函数进 Potato 对象；**其余（`choose` / `use` /
结构体 / 枚举 / trait / impl）不能进** —— 不是漏了，是 `lomt_from._check_representable`
明确拒收（`imports` / `types` / `enums` / `traits` / `impls` 非空就报"L1 那侧的对应形状
还没接上"）。它们**由 `translate` 自己发**。

**声明也走 `doc["functions"]` 那条管道**（名字是合成的，正文是那段声明的**原文**）——
那是这一节里唯一能把"行号对齐"做对的地方（`_join_bodies` 按 `body_line` 垫空行）。
不这么做的话两条路会把行号算成两个数，而"错要指在错的地方"是本仓的纪律。

## 6. 这一版**不收**的（每条都有理由）

### 6.1 四条**语言层面**的边界（不是这一门的）

| 不收 | 为什么 |
|---|---|
| `Option` / `Result` 的**形状**（`when x looks like …`） | 判据拿写出来的名字与**单态化名**比（`Option::Some` 对 `Option_u32::Some`），而后者是**编译器的内部拼法** —— 源里写不出来（实测）。它们的**类型、构造、`?`** 都收 |
| **泛型函数拿字面量/常量做实参**（`largest of 7, 9`） | 实测失败：`调用未定义的函数 largest`。**必须**拿声明过类型的变量传（`let a be 7` 之后 `largest of a, b`）。所以语料里那一句才先 `let eight be 8` |
| `break` / `continue` | Loment 没有这两个词（实测 `使用未声明的变量 break`） |
| `for` 遍历切片 | Loment 的 `for` **只有**区间那一种（实测 `for x in xs` 过不了） |

### 6.2 这一门**没做**的

| 不收 | 为什么 |
|---|---|
| `addin`（开关设定单元） | 构建期的东西，没有可判的语料 |
| `comefor` / `byuse`（在源码里定义新语法） | 它们**本身**就是在定义一门语法 —— 与这一门是同一层的事 |
| 结构体的模式（`match` 里看字段） | Loment 的 `match` 只看枚举（E8「不是枚举」） |
| 声明**末尾以外的位置**写 `, only here` | 只在声明那一行收（语句里没有"导出"这回事） |
| 私有常量以外的 `pub` 粒度 | 常量一律模块级：公开的走 `pub const`，加 `, only here` 的走 `const` |

## 7. 写这一门时撞出来的工具链事实

### 7.1 `return;`（不带值）**不是合法 Loment** —— 而自举驱动会**段错误**

参考实现报一句干净的 `E19 期望表达式，得到 ';'`；**安装版 0.1.4 的自举驱动在那份源上
段错误**（退出 139，不打任何诊断）。所以这一门的 `give back` **必须带一个值**。

### 7.2 **无后缀字面量在自举驱动里被当成 u32**（`i64` 上下文里取负 32 位回绕）

```
let a: i64 = 0 - 12345;
```

* **参考实现**为这一句发的是 `sub i64 0, 12345`（正确）；
* **自举驱动**编出来跑，`a as u64` 给 **4294954951**（= 2^32 − 12345）。

同一个形状在**调用点**也成立。**运行期的 `i64` 变量之间没有这个问题**，
所以 `nl_write_num` 走"带类型的零"那条路：`let zero: i64 = 0; … if v < zero { … }`。

**这是编译器的一处真 bug** —— 两边都编得过、只有一边算错，正是本项目的红线。
（另一条独立测到的同族：`-` 右边整数字面量的定型，见 `docs/196` §4.10。）

## 8. 一句话的定位

**这一门不是"第七门"，是"第一门我们自己的"。** 六门证明的是"别人写的东西也能读成
Loment"；这一门证明的是**反过来那一半**：**Loment 自己也说得出别的样子，
而说的仍然是同一件事** —— 句子像人话，产物是同一串字节。
