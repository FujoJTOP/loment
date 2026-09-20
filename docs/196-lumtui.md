# 196 · LumtUI —— Loment 的 GUI 库

> 上游：`docs/138`（FUI 那套 UI）/ `lom/fuc.lom`（`.fuc` 二进制布局，L0）/
> `kernel/src/fui/fuc.rs`（内核侧的读）/ `tools/fuic.py`（编译那一步）/
> `docs/143`（语言）/ `docs/187`+`docs/188`（Python 写法与 `choose write grammar`）。
>
> 代码：`loment/lib/lumtui*.lomt`（6 个模块）· `loment/examples/lumtui_demo.lomt`
> （示例）· `tools/loment_lumtui_test.py`（判据，6/6）。

## 0. 一句话

**LumtUI 是 Loment 的 GUI 库。** 它把 FujoOS 那条 UI 线（FUI）的三块合到一起，
补上 FUI 当年缺的那一块（**任意字体**），并且**其中一块是用 Python 写法写的**。

```
lument/lib/lumtui.lomt          词汇表: 控件 kind / 旗标 / 尺寸模式 / 对齐 / 锚点 / 色调
lument/lib/lumtui_doc.lomt      文档模型: 建 -> 树 -> .fuc 字节 -> 校验回读
lument/lib/lumtui_layout.lomt   布局: 设计尺寸 -> 绝对像素; 命中测试; Tab 焦点序
lument/lib/lumtui_font.lomt     字体: SFNT/TTF 解析 + 光栅化 + 内置 5x7 兜底
lument/lib/lumtui_paint.lomt    绘制: ARGB32 帧缓冲; 圆角; 文字
lument/lib/lumtui_math.lomt     **Python 写法**: 定点 / 缓动 / 颜色 / 整数三角 / 命中算术
```

## 1. 它合并了 FUI 的什么

| FUI 里在哪 | 是什么 | LumtUI 里在哪 |
|---|---|---|
| `lom/fuc.lom` | `.fuc` 的二进制布局：48B 头 + 64B 节点 + 字符串表 + 令牌表 | `lumtui_doc.lomt` **逐字节照做**（判据钉住） |
| `tools/fuic.py` | 界面文档 -> 内核可载入的字节 | `lumtui_doc_emit` |
| `kernel/src/fui/fuc.rs` | magic/version/边界/FNV 四道校验 + 零拷贝节点视图 | `lumtui_fuc_valid` 与一整套 `lumtui_fuc_*` 访问器 |
| 内核的布局/命中/焦点 | 把设计尺寸算成像素、点得中哪一个、Tab 走到谁 | `lumtui_layout.lomt` |
| 内核的绘制 | 圆角、底色、文字 | `lumtui_paint.lomt` |
| `kernel/src/graphics.rs` 的 `FONT` | 5x7 点阵字体（写死的） | 同一份数据当**兜底**；正路是 `lumtui_font.lomt` 读真字体 |

**一条刻意保留的分界**：布局与绘制吃的是**载入侧**那份（平铺的 `.fuc` 字节），
不是"可写的文档对象"。这正是 FUI 那条"编译期文档 vs 载入期只读视图"的分界 ——
于是 LumtUI 的布局/绘制与内核读同一份二进制时是**同一个语义**，不必再造一套。

## 2. 任意字体（FUI 当年缺的那一块）

`lumtui_font.lomt` 自己解析 SFNT，不借任何东西：

* **表**：偏移表 / 表目录（大端）、`head`（upem、loca 格式）、`hhea`（升降部、
  numberOfHMetrics）、`maxp`、`hmtx`、`cmap`（format 4 与 12）、`loca`/`glyf`。
* **轮廓**：简单字形（含带 repeat 的旗标、短/长坐标）与**复合字形**（分量递归 +
  变换叠加 + XY 偏移）。嵌套复合也成立 —— 分量的点直接追加到同一条折线上。
* **光栅化**：坐标缩到 **1/16 像素**定点，每行 **8 条子扫描线**，按 **nonzero 绕数**
  判内部，按 1/16 像素的**精确分数**累进覆盖。**抗锯齿；不做 hinting。**
* **兜底**：一个字体文件都没有时走内置 5x7 点阵 —— 那份数据**从
  `kernel/src/graphics.rs` 的 `FONT` 转出来**（不是手抄），所以与 FujoOS 界面上的字
  是同一套。

### 2.1 与 FreeType 的对照（判据的一部分）

| 量 | 对照物 | 结果 |
|---|---|---|
| 码点 -> 字形号有没有 | FreeType（经 Pillow） | 64 个码点一致 |
| `hmtx` 步进（**字体单位**，整数） | `getlength(ch, size=upem)` | **精确相等** |
| 升部 / 降部 | `getmetrics()` | 一致（±1 像素） |
| 字形包围盒 | mask 的**墨迹盒** `getbbox()` | 64 个字形 ±2 像素 |
| 墨迹总量（覆盖值之和） | mask 的像素和 | 64 个字形 ±25% |

**容差是写死的、有理由的**：本库不做 hinting，FreeType 做 —— 逐像素比会红得没有
信息量。比"形状对不对"（包围盒 + 墨迹量）才是有信息的。**注意比的是 mask 的墨迹盒，
不是 `font.getbbox`** —— 后者给的是**步进盒**（`getbbox('W')` 宽 30，那正是 'W' 的
步进），拿它当"字形有多宽"会把本库这边**不留白**的那份正确当成偏差。

## 3. 三条边界（写清楚，免得当它是万能）

1. **CFF / `OTTO` 容器不支持**：那种字体没有 `glyf`/`loca`，轮廓在 `CFF ` 表里、
  是三次贝塞尔 —— 另一套解码器。`lumtui_font_load` **拒绝**它（返回 0），不静默画空。
2. **不做 hinting、不做 kerning、不做复杂文种整形**：一个码点一个字形，不做连字、
   不做 RTL。中日韩的字形**能画**（只要 `cmap` 里有）。
3. **不换行、不做 min/max 约束**：布局那一步没有 wrap，`.fuc` 的记录里也没有
   min/max 那两格 —— 要加就得先动 L0，而 L0 是对外契约（见 `CLAUDE.md`）。

## 4. **Python 表层语法用不了这份库 —— 实测上报**

用户这次的要求里有一条"用 Python 语法写，语法用不了就上报"。**结论：写不了**，
而且不是"写起来别扭"，是**表达不出来**。下面每一条都有最小复现。

### 4.0 先说清楚"上报表"的口径

`loment/lib/lumtui_math.lomt` 是**真的**用 Python 写法写的（文件头
`choose write grammar python`，`lomentc.load` 的前门在进程内把它翻成 Loment），
判据 `test_python_grammar_module_matches_python` 逐条对过 16 个值。
所以下面说的不是"没试"，是**试过之后量出来的边界**。

### 4.1 正文子集只收 `int` / `bool`

`tools/pytrans.py` 的 `_TYPES = {"int": "i64", "bool": "bool", "None": "()"}`。
指针、`str`、结构体名**都不是注解**：

```python
choose write grammar python

def f(p: bytes) -> int:        # 参数 p: 注解 `bytes` 不在子集里（只收 ['None','bool','int']）
    return 0
```

⇒ **任何碰内存的函数都写不出来**，于是"文档模型 / 字体 / 光栅化 / 绘制"这四块
（LumtUI 的 5/6）**没有一行能落在这门拼法里**。落到这里的只有"纯标量、无状态、
不碰内存"的算术 —— 也就是 `lumtui_math.lomt`。

### 4.2 内建当值用不了，当语句却能过（**同一件事两个答案**）

```python
def g() -> int:
    store8(0, 0, 1)          # 过 —— 表达式语句那条路走 raw()，不查 fns
    return alloc(16)         # 报：调用了本单元没有的函数 `alloc`
```

同一个内建、两种结论。根因是 `pytrans.Emitter` 里 `ty_of()`（要类型，查 `self.fns`）
与 `raw()`（不要类型，不查）对"这个调用认不认得"给了**两个答案**。按"平名字空间 +
一切经名字解析"的口径，`raw()` 那条路**少一道闸**。

### 4.3 `~` 发不出来（**这一条我按 bug 报**）

```python
def f(a: int) -> int:
    return ~a
```

`pytrans` 把 `ast.Invert` 直接映射成 `"~"`，而 **Loment 的词法器没有 `~`**：

```
lomc.LomError: 7:13: 非法字符 '~'
```

也就是说：**这份 Python 写法**能过翻译器、过不了词法器 —— 而这一门是"全有或全无"
（`lomentc.load` 的前门要么整份翻出来，要么拒）。语言自己给的出路是**按宽度**写
`a ^ -1`（i32）或 `a ^ 255`（u8）（`loment check` 对原生写法就是这么提示的），
翻译器没走那条。**建议**：`_BIN` 旁边按 `Invert` 特判成 `... ^ -1`，或直接
`Unsupported` 拒掉 —— 两条都比"发一份编不过的源"好。

### 4.4 `choose write grammar` 写错词**静默退回**

```python
choose language python      # 想写这个
```

`potato_from.read_grammar_decl` 实测（这是一张表，不是推的）：

| 文件头写的 | 解析出什么 |
|---|---|
| `choose write grammar python` | `python` ✓ |
| `choose write grammar py` | `python` ✓（别名）|
| `choose language python` | **`loment`**（`declared=False`）|
| `choose grammar python` | **`loment`** |
| `grammar python` | **`loment`** |
| `choose write grammars python` | **`loment`** |

后四种**一声不响**，文件被当 Loment 读，用户看到的是 `未知顶层关键字 'def'` 之类
**指向别处**的错。`docs/188` §1 说"写错是四种报错之一"（名字不在表里 / 写在
`module` 之后 / 写两次 / 后面没写名字）—— **漏了"关键词本身写错"这一种**。
而 §2 那条"兜底从'猜'变'拒绝'"正是为这种情况写的：现在它既不猜也不拒，
是**换了一个猜法**（当成 Loment）。

### 4.5 宽度不对齐：一个 `i64` 的岛

表层语法只收 `int`（= `i64`），而原生库从头到尾按 `u32` 算。**每个调用点都要 `as`**：

```
7: lumtui_clamp 实参类型 u32，期望 i64
```

这是"只收 int"直接变成的**调用方负担**。更要命的是热路径：`lumtui_paint` 里
颜色拆通道是**每个像素**一次，用它就得每像素一次 `as` —— 所以那一份按 `u32`
**重写了一遍**（`lumtui_ca`/`cr`/`cg`/`cb`/`luma`），而名字也只好跟着改。

### 4.6 平名字空间：同一件事只能有一个名字

`lomentc.check` 那条"单元级唯一性"要求**整个程序**里的顶层符号唯一。于是
`lumtui_math`（Python 写法）与 `lumtui_paint`（原生写法）**不能各有一个
`lumtui_ink_on`** —— 后者只好叫 `lumtui_ink_for`。这条不是 Python 语法特有的，
但"把一件事拆成两个写法不同的模块"时它先咬人。

### 4.7 顺带两条**原生**拼法上的（不是 Python 语法，但这次都踩了）

* **`guard` 是保留字，不能当变量名**：`let guard: u32 = 0;` 之后写 `guard = guard + 1;`
  会被当成能力域守卫语句，报 `期望 ident（能力名），得到 '='` —— **指向的那行还差着几行**。
  与 `mut`（`docs/143` §6.15）同一族：**上下文关键字也占名字**。
  本文档里那几处一律改叫 `grd`。
* **`return;` 不合法，void 函数也一样**（§6.3）。所以"提前退出"只能靠改返回类型来表达
  —— `lumtui_blend_px` / `lumtui_fill_rect` / `lumtui_fill_round` 这几个本来什么都不返回的
  函数，现在都写成 `-> u32` + `return 0;`。那条建议（"声明成 `-> u32` 然后 `return 0;`"）
  能用，但那是**绕过**，不是表达。
* **`as` 只作用于整型/布尔**：`ptr as u64` ✓、`u64 as ptr` ✓、**`ptr as u32` ✗**
  （报 `as 只能作用于整型/布尔 (得到 ptr)`）。这个拒绝是对的（u32 装不下指针），
  但话里没说"要转就用 `u64`"。LumtUI 的绘制上下文因此把指针存成两格 `u32`
  （`lumtui_st64`/`lumtui_ld64`）。

### 4.8 结论

**能表达的就转、表达不出来的就报错**（`docs/187` 那条判据）在 LumtUI 上给出的结论是：

> 一个 GUI 库要指针、要结构体、要字符串、要按宽度的整型 —— **这四样一样都不在
> Python 写法今天的正文子集里**。所以"整个 LumtUI 用 Python 语法写"这句话今天
> 不成立，不是取舍问题，是**可达面**问题。能落在那门拼法里的是"标量计算核"
> （`lumtui_math.lomt`，6/6 判据里那 16 项），其余 5 个模块只能是原生拼法。

4.3 / 4.4 / 4.2 三条是**独立于这个结论的缺陷**：它们不是因为"子集小"，
而是**该报的没报 / 报错的地方不对 / 认不认给两个答案**。

### 4.9 顺带发现（**与 Python 语法无关，但比上面几条都严重**）：依赖模块的正文错误不报

写这份库时被 `loment_std_test::test_std_modules_are_checkable` 与 `loment_p8_test`
各抓了一次**同一类**错：`let bits: u32 = <i64 表达式>`、`return <ptr>;` 而函数声明 `-> u32`。
奇怪的是**我自己的探针一路绿** —— 我的探针都拿 `main.lomt` 当入口、把库里那几个模块
当**依赖**。于是做了一次最小复现：

```
deps/badlib/badlib.lomt:   module badlib
                           pub fn f(p: ptr) -> u32 { return p; }      // 明显的类型错

main.lomt:                 module main
                           use badlib
                           fn _start() { let x: u32 = f(alloc(8)); ... }
```

**实测结果**：

```
A) 把坏库当**依赖**检查 : clean  <- 没报
B) 把坏库当**入口**检查 : ['4: return 类型 ptr，函数 f 声明 u32']
```

⇒ **`lomentc.check` 只报"入口那一份"正文里的类型错，`use` 进来的模块的正文不查**
（或者查了不往上报）。后果是：**一个库可以带着正文类型错发布，而每一个使用它的
程序 `check` 都是绿的。**

这次是 `loment_std_test` 那条"每个 `loment/lib/*.lomt` **自己**要是合法单元"
（它逐个把库文件当入口查）把它抓住了 —— 也就是说，**当前唯一能发现这类错的东西，
恰好是这条判据的特殊形状**。判据换个形状（比如只拿一个 app 去 `use` 那个库）
就看不见了。

**建议**：`check` 应当把依赖的正文一并查（报错时标出是在哪个模块里）。
在修之前，**给库写判据时要用"库文件自己当入口"那个形状** —— LumtUI 的
`tools/loment_lumtui_test.py` 里新增的
`test_each_lib_module_is_checkable_as_its_own_entry` 就是把那个形状钉在自己身上。

### 4.10 顺带发现：**自举驱动的三类分歧**（`loment_p8_test` 抓到的）

`test_m85_selfhosted_driver_compiles_corpus` 要求"自举驱动造出的 IR 与参考**逐字节相同**"。
LumtUI 的 7 个文件里，**最初有 3 个不一致、1 个被驱动直接拒**。逐类记下来。

#### (a) 不带括号的链式 `as`（**驱动侧误解析**）

```rust
fn cvt(p: ptr) -> i64 { return f(p) as i32 as i64; }   // 不一致
fn cvt(p: ptr) -> i64 { return (f(p) as i32) as i64; } // 一致
fn cvt(p: ptr) -> i64 { let s: i32 = f(p) as i32; return s as i64; }  // 一致
```

驱动把第二个 `as` 当成**标识符**：它发出来的是 `%as.addr` / `%i64.addr` 两条 load，
还把 3 个实参的调用发成 5 个实参。参考侧是对的（`sext i32 … to i64`）。
**规避**：链式转换加括号（LumtUI 里 20 处）。

#### (b) `-` 右边那个**整数字面量**的类型（**驱动侧定错，发出非法 IR**）

```rust
fn f() -> i64 { return 0 - 1; }                       // 不一致
fn f() -> i64 { return -1; }                          // 不一致
fn f() -> i64 { let o: i64 = 1; return 0 - o; }        // 一致
fn f() -> i64 { let d: i64 = 0; return d - 1; }        // 一致
fn f() -> i64 { return (0 - a + b - 1) / b; }          // 不一致（左边是中间结果）
```

驱动的产物是：

```
G  %t11 = sub i32 %t10, 1        <- i32
G  ...
W  %t11 = sub i64 %t10, 1        <- 参考: i64
```

而那个 i32 值**接着被 store 进一个 i64 槽**（`sub i32 0, 1` → `store i64`）——
**那不是"两种合理实现"，是非法 IR**。规律：减法的**左操作数**是个带类型的变量时
两边一致；左操作数是**字面量或中间结果**时驱动按 i32 定。
**规避**：拆成带类型的局部量（LumtUI 里两处：`lumtui_floordiv` 与 `lumtui_edge_cross`）。

#### (c) 路径形式与名字形式混用时**依赖的发射顺序**（现象确定，根因未追）

把 `use "loment/lib/num.lomt"` 与几个名字形式的 `use` 混在一份源里时，驱动与参考
**对同名函数的发射顺序不同**（第一处分歧落在 `; num_dec_len -> u32` vs
`; lumtui_version -> str` —— 是**顺序**，不是内容）。改成 `use num` 之后顺序就对上了。
没有做更小的复现，所以这一条只记**现象**，不写结论。

#### 这三条与 4.9 的关系

4.9 那条（`check` 不查依赖正文）与这三条（驱动产物分歧）的**共同形状**是：
**"拿一个 app 去 `use` 那个库"这种形状，量不到库自己的问题。**
LumtUI 能被打得住，靠的是两条**别的形状**的判据：
`loment_std_test`（库文件自己当入口去 check）与 `loment_p8_test`（逐文件与参考比 IR）。


## 5. 判据（`tools/loment_lumtui_test.py`，6/6）

**三把不同的尺子，因为库里三块东西的"对"不是同一个意思：**

| 判据 | 对照物 | 量的是什么 |
|---|---|---|
| `test_fuc_bytes_match_lom_single_source` | **`lom/fuc.lom`**（`lomc` 现场生成 Python 布局） | 库发出来的 `.fuc` 与 L0 的记录布局**逐字节相同**（288 字节）—— 量"有没有重述 L0" |
| `test_layout_matches_independent_expectation` | **在判据文件里独立推出来的** 27 项 | 固定/百分比/弹性/对齐/间距、命中、Tab 序。每一数的来历都写在注释里 |
| `test_font_metrics_match_freetype` | **FreeType**（经 Pillow） | 64 个码点的字形有无 / 步进（字体单位，精确）/ 升降部 |
| `test_glyph_raster_matches_freetype` | **FreeType** | 64 个字形：包围盒 ±2 像素、墨迹 ±25% |
| `test_python_grammar_module_matches_python` | **Python** | `lumtui_math` 16 项 |
| `test_demo_runs_and_renders` | 记号（不是整幅） | 示例跑得起来、帧尺寸对、`.fuc` 过校验 |

**为什么最后一条只对记号**：整幅 ASCII 全等会把"渲染对不对"变成"像素全等" ——
改一次调色板就红一次，而它并不说明什么错。

无 WSL / 无系统 TTF / 无 Pillow 时**逐条 SKIP**（退出码 0），与 `loment_std_test` 同一条纪律。

## 6. 这次实测出来的四个真 bug（都修了，都留着注释）

写这份库的过程里，判据抓出来的**不是笔误**，是四个"看着对、量出来不对"的形状。
把它们记下来，因为**四个都是判据而不是眼睛发现的**：

1. **`on` 那一位按 `== 1` 判**（`lumtui_on`）。glyf 的旗标字节还带着别的位，在曲线
   上的点拿到的常是 17/35/51 而不是 1。于是**整条轮廓被当成全由控制点组成**，
   每个字形被拉成一圈圆滑的曲线：'N' 的斜笔整条消失（包围盒还对、墨迹只有 0.68 倍）。
   **而 'A' 因为它的在曲线点恰好旗标就是 1，看着完全正常** —— 那才是它藏得住的原因。
2. **字体 y 与图像 y 反了，复合字形的 `dy` 漏了一次取负**。'eacute' 的尖音符偏移恰好是
   `(243, 0)`，dy 为 0 ⇒ **看着完全正常**；只有 'Aring' 那种 dy 非零的（263）才把圈
   画到 A **里面**去。判据里那一条"高 23 vs 28"就是它。
3. **折线缓冲没在换字形时清空**。症状：一串数字全报**同一个** 22×25 的包围盒 ——
   到第二个字的时候缓冲里已经不止一个字形了。
4. **点阵兜底写位图用 64 作步长、混叠时按 6 读**。症状：背景、圆角、焦点环全对，
   **只有字变成稀稀拉拉几笔**。

**加一条不是 bug 但同样隐蔽的**：`lumtui_ink_for` 原来返回 `16777215`
（`0x00FFFFFF`）—— "看起来是白"，而 **alpha 是 0**，于是到混合那一步被整片丢掉。
症状是"按钮上的字一个都不出来，而别的都对"。

这五条的共同形状是：**一个数不对，而它旁边所有东西都对**。挑"看起来相关"的判据
一条都抓不到它们 —— 这正是 `docs/176` §7.3 那条的同一个病。

## 7. 怎么用

```rust
module myapp

use lumtui
use lumtui_doc
use lumtui_layout
use lumtui_paint

fn _start() {
    let arena: ptr = alloc(lumtui_arena_bytes());
    let d: ptr = lumtui_doc_init(arena, 320, 200);
    let card: u32 = lumtui_doc_add(d, LUMTUI_K_CARD, 1);
    let btn: u32 = lumtui_doc_add(d, LUMTUI_K_BUTTON, 2);
    let _c: u32 = lumtui_doc_add_child(d, card, btn);
    lumtui_set_text(d, btn, "Run");
    lumtui_set_bg(d, btn, lumtui_pack_rgba(76, 141, 246, 255));
    lumtui_set_size(d, btn, LUMTUI_M_FIXED, 72, LUMTUI_M_FIXED, 24);

    let blob: ptr = alloc(lumtui_emit_bytes(d));
    let n: u32 = lumtui_doc_emit(d, blob, lumtui_emit_bytes(d));   // -> .fuc 字节
    let rects: ptr = alloc(lumtui_fuc_count(blob) * LUMTUI_RECT_BYTES);
    let _l: u32 = lumtui_layout(blob, rects, 0, 0, 320, 200);       // -> 绝对像素
    // 画进一块 320x200 的 ARGB32 帧缓冲 (由调用方给)
    // let ctx = ...; lumtui_paint_doc(fb, 320, 200, blob, rects, ctx);
}
```

要**任意字体**：把字体文件读进内存（`openat`/`read`），
`lumtui_font_load(data, n, frec)`，再把 `(data, frec, scratch)` 交给
`lumtui_ctx_new` 当画字上下文；不传就自动落到内置 5x7 点阵。

`loment/examples/lumtui_demo.lomt` 是**能直接跑**的完整例子（不读字体文件，
所以输出确定），它把整块帧缓冲按亮度打成 ASCII —— 想看一眼 LumtUI 长什么样跑它。

## 8. 没做的（说清楚，免得当成遗漏）

* **动画**：`lumtui_math` 里有缓动曲线与定点插值，但**没有时间轴** —— 谁推进时间、
  什么时候重绘，是调用方的事。`.fuc` 头的 `anim_off`/`anim_count` 仍然按 v1 保留着。
* **输入事件**：有命中测试、有 Tab 序，但**没有事件对象**（按下/抬起/拖动/文本输入）。
* **滚动与裁剪**：`LUMTUI_F_CLIP` 这个旗标**有位置、没有实现** —— 绘制不做裁剪区。
* **与内核那份 `.fuc` 的互读**：记录布局是照 L0 做的（判据钉死），但**词汇表
  （kind/tone/flag 的编号）是本库自己的** —— FUI 那份在 `ui/fui_spec.json`，
  它**不在本仓**（FujoOS 侧的资产）。所以"拿内核查去看 LumtUI 发的字节"这件事
  **还没验过**，两边要对账得先拿到那份 spec。
