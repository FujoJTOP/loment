# 232 · LibL：标准库的 libc 面，判据是**与真 glibc 逐字节对跑**

> 状态: **四张脸已落地**（2026-10-11）· 目录: `lompi/store/std/0.1.0/` 的 `string` `ctype` `stdlib` `stdio`
> 判据: `loment_libl_test`（2/2；**185 项与系统 glibc 2.43 逐字节一致**）· 入口: `use libl` 与 `use std`
> 上游: `docs/199`（std 可达与平名字冲突）· `docs/180`（std 核的分层与三条约定）·
> `docs/183` V1（静态 libc）· `docs/219`（世界端口）· `docs/188`（不猜，报错）
> 一句话: **这个标准库叫 LibL；它对 libc 的那张脸，函数名、参数序、返回值语义都取 libc 的，
> 而判据不拿 Python 当对照物 —— 拿 libc 自己。**

---

## 0. 一句话展开

`lompi/store/std/0.1.0/` 里原先有 **127 个模块、1356 个 `pub fn`**，而它们**一个 libc 名字都没有**
（实测：`strlen` `strcmp` `printf` `atoi` `qsort` `malloc` … 24 个常见名字，命中 0）。
那不是遗漏 —— 那是刻意的形状：**长度显式**（`text_cmp(p, ao, al, q, bo, bl)`）、
**偏移寻址**（`mem_copy(dst, doff, src, so, n)`）、**没有 NUL 串、没有变参**。

这一轮给这个包补上**另一张脸**：libc 那张。

|  | `lompi/store/std` 原有 | LibL 这一轮加的 |
|---|---|---|
| 形状 | 这门语言自己的（长度显式、偏移） | **libc 的**（NUL 串、参数序照抄） |
| 名字 | `char_is_digit` `text_cmp` `sort_heap` | **`isdigit` `strcmp` `qsort`** |
| 给谁 | 写 Loment 的人 | **会 C 的人**（"不必学新东西"） |
| 判据的对照物 | Python（`loment_std_test`） | **真 glibc**（`loment_libl_test`） |

两张脸**指同一批字节**：`ctype.lomt` 的 `isdigit` 直接调 `char.lomt` 的 `char_is_digit`，
`memcpy` 直接调 `mem_copy`。所以"同一个字节两个答案"这件事结构上不可能发生。

---

## 1. 名字：LibL 是**库**的名字，`std` 仍然是**包**的名字

用户 2026-10-11 定的形状：**"整合这些 std 模块，成为 LibL，但仍然可以用 std 调用，
这些模块是一个专属名词 std LibL。"**

落成两个入口，指同一个包：

| 入口 | 拉进来什么 | 什么时候用 |
|---|---|---|
| `use std` | **整包**（131 条 `use`，1403 个 `pub fn`） | 老写法，照旧能用 |
| `use libl` | **只拉 libc 那四张脸** + 它们的依赖（`mem` `char`）—— 6 份模块 | 只要 libc 面时 |

`libl` 这一份不是"std 的别名"：`std.lomt` 那个门面把整包拼进**一个单元**，平的发射符号空间里
挤着 1350+ 个顶层名字，编译时间还按模块数**超线性**涨（`docs/199` §1 实测自举侧 n=64 已 187 秒）。
`libl` 只有 6 份模块 —— 面小、编得快、撞名面也小。

**包名仍然是 `std`**：库系统要求"包名与入口文件名一致"（`docs/168`
`test_multi_file_package_survives_materialize`）。所以**包**叫 `std`，**库**叫 LibL。
一个是库系统的键，一个是这门语言里这个库的名字 —— 两者不矛盾。

---

## 2. 四张脸：libc 头 -> LibL 模块

**一个 libc 头对一个模块**（这条映射本身就是"照 libc 实现"的一部分）。

| libc 头 | LibL 模块 | 提供的名字 | 个数 |
|---|---|---|---|
| `<string.h>`（+ POSIX `strtok_r`） | [`string.lomt`](../lompi/store/std/0.1.0/string.lomt) | `strlen` `strcpy` `strncpy` `strcat` `strncat` `strcmp` `strncmp` `strchr` `strrchr` `strstr` `strspn` `strcspn` `strpbrk` `strtok_r` `memchr` `memcmp` `memcpy` `memmove` `memset` | 19 |
| `<ctype.h>` | [`ctype.lomt`](../lompi/store/std/0.1.0/ctype.lomt) | `isdigit` `isupper` `islower` `isalpha` `isalnum` `isspace` `isxdigit` `isblank` `iscntrl` `isprint` `isgraph` `ispunct` `toupper` `tolower` | 14 |
| `<stdlib.h>`（能做的那一半） | [`stdlib.lomt`](../lompi/store/std/0.1.0/stdlib.lomt) | `atoi` `atol` `strtol` `strtoul` `abs` `labs` `llabs` `qsort` `bsearch` | 9 |
| `<stdio.h>`（格式化那一半） | [`stdio.lomt`](../lompi/store/std/0.1.0/stdio.lomt) | `snprintf` `sprintf` | 2 |

加两个**不是 libc 名字**的：`cstr_of`（§3.1）与两个便利档 `qsort_u32` / `bsearch_u32`。
合计 —— LibL 发出 **44 个 libc 名字 + 3 个自己的名字**。

---

## 3. 语言逼出来的四处偏离（**每一处都写在这里，不藏着**）

照 libc 实现，但语言不是 C。四处必须偏离，另有一处必须**补**。

### 3.1 补：`cstr_of` —— 因为本语言的字面量**不带 NUL**

这一条是动手时最先撞上的，也是最要紧的。

```rust
// "abc" 的 IR：
@.str._start.0 = private unnamed_addr constant [3 x i8] c"\61\62\63"
```

**三个字节，没有第四个。** 所以 `strlen(str_ptr("abc"))` 会**读过头** —— 实测第一版
探针里 `strlen` 对空串返回 **17**（一路读到偶然的零字节才停）。

C 里没有这个问题（`"abc"` 在 C 里天生带 NUL），所以 **libc 不需要这个函数** ——
它不是我们漏抄，是两种语言的 `str` **不是同一个东西**：Loment 的带长度，C 的不带。

```rust
pub fn cstr_of(dst: ptr, s: str) -> ptr   // 拷 str_len(s) 字节 + 补一个 NUL
```

仓里同一件事已经做过一次：`loment/lib/proc.lomt` 的 `pc_cstr`（发 syscall 前的私有助手，
注释原话"系统调用要的是 C 字符串"）。LibL 把它提成公开的 —— 因为**每一个**用 LibL
的 NUL 串函数的人都要它。

### 3.2 变参：`snprintf` 多一个 `nargs`

libc 的 `printf` 靠**可变参数**；本语言没有 `...`。所以：

```rust
snprintf(buf, cap, fmt, args: ptr, nargs: u32) -> u32
```

`args` 指向一个 **u64 数组**（第 k 个实参在偏移 `k * 8`）。转换说明符**自己**说明该实参
怎么解释 —— 这一点与 libc 完全一样（`%d` 与 `%s` 的读法本来就由格式串决定）。

`nargs` 是 libc **没有**的（它数不出来）。少给实参时 libc 读越界垃圾，这里**停止转换**
并把已产出的部分收尾。**这是唯一一处签名上的偏离。**

### 3.3 函数指针：`qsort` 的第四个参数是 `kind`，不是比较函数

本语言**没有函数指针**（`TYPES` 里没有 `fn` 类型，`lomentc.py:29-30`）。所以：

```rust
pub enum qsort_kind { qk_u32, qk_i64, qk_bytes, qk_cstr }
pub fn qsort(base: ptr, n: u32, width: u32, kind: qsort_kind)
```

这不是降级 —— 它把"按什么比"从**运行期的一个地址**变成**产物里的一个常量**，
于是"这个排序按什么比"是不读源码可判的（与 `gc_ladder`、端口表同族，`docs/210` §3）。
**代价如实列**：自定义比较（比如"按第二个字段排"）写不出来，调用方得自己写循环。

### 3.4 模块级可变全局：`strtok` / `rand` / `errno` 落不下来

本语言**没有模块级可变全局**（顶层只收 `const`，`lomentc.py:1626-1738`）。于是：

| libc | 为什么不行 | LibL 给的替代 |
|---|---|---|
| `strtok` | 靠一个模块级静态游标 | **`strtok_r`**（游标由调用方持有）—— POSIX 里本来就有，不是降级 |
| `rand`/`srand` | 模块级静态种子 | 仓里 `rng.lomt` 的形状（`rg_next_u64(state)` —— 状态由调用方持有，种子也由调用方给；它自己的头注就写着"为什么是这一套（不是 `rand()` 那种）"）—— **名字不叫 `rand`** |
| `errno` | 模块级线程局部 | **没有**。`strtol` 越界时饱和到边界值并照常返回，调用方自己比边界（`stdlib.lomt` 头注写明） |

### 3.5 混合宽度：库代码里一次 `ptr == ptr` 都不能有

这一条不是设计，是**实测撞出来的纪律**。参考实现对 `ptr == ptr` 发出**非法 IR**
（`icmp == ptr ...`，clang 当场拒；就是 issue **#205**）。所以：

```rust
// ✗ 编不过（clang: expected icmp predicate）
if s == (0 as ptr) { ... }
// ✓ 同一个判断，合法
if (s as u64) == (0 as u64) { ... }
```

同族的还有"把 u64 右移一个 i32 变量"（§5.3 第 2 条）、以及内联 `0 as ptr` **不能当实参**
（检查器报"实参期望 ptr，得到整型字面量" —— 要先落到一个返回 `ptr` 的函数上）。
三条都写进了库代码的注释里，免得下一个人重踩。

---

## 4. 判据：对照物是 **libc 自己**，不是 Python

用户 2026-10-11 定的口径：**"Libc 怎么实现的 LibL 就怎么实现。"** 这句落在判据上就是
一条：**别拿 Python 当对照物。**

`loment_std_test`（`docs/180`）比的是"Loment 算的 == Python 算的"。那条对 std 核是对的，
但**对"像 libc"这个问题是错的**：`strncpy` 补不补零、`strcmp` 返回符号还是**差值**、
`strchr` 找不找得到结尾那个 NUL、`memmove` 重不重叠安全 —— Python 与 libc 在这些边角上
**根本不是同一个答案**，于是"两处一起错"照样绿（`docs/176` §7.3）。

`tools/loment_libl_test.py` 因此这么做：

1. 在 WSL 里用 `cc` 编一段 C（走**系统 glibc 2.43**），跑一遍；
2. Loment 侧跑**同一组调用**；
3. 两边 **stdout 逐字节比**，第一个不同行当场报出来。

**185 项对齐**，含：`strncpy` 的补零与"源够长时不给 NUL"、`strcmp` 的无符号比较
（`0x80 > 'a'`）、`strchr(s, 0)` 命中结尾、`strstr` 空针返回 `hay`、`strtok_r` 的连续分隔符、
`qsort` 的四种 kind、`snprintf` 的**返回值与截断**（`cap=1` 时只写 NUL 却返回长度 3）。

### 4.1 两处刻意的归一化（不归一化就是拿实现细节当语义）

- **`strcmp` 的返回值**：POSIX 只保证**符号**。判据里比两样 —— **符号**（可移植的那条契约）
  与**差值**（glibc 的实际实现）。两条都过了，才说明"既合契约又合这份实现"。
- **`ctype` 的返回值**：glibc 的 `isdigit` 展开成 `(*__ctype_b_loc())[c] & _ISdigit`，
  返回的是**位掩码**（不是 1）。判据两边都归一化成 0/1 —— 比的是"是不是"，不是 glibc 的
  内部常量。做法是**12 个谓词打包成一个位掩码**，两侧同序生成。
- **返回指针的函数**：两边地址不同，比的是**偏移**（`off(base, p)`，找不到 -1）。
  `bsearch` 那条尤其要小心：C 侧一开始写的是**元素下标**，LibL 给的是**字节偏移** —— 判据
  当场指出 `bs_hit_off=8` vs `2`，改成同一个量才对齐。

---

## 5. 落不下来的（**如实列，不假装有**）

### 5.1 不属于这一版的（各自有明确的原因）

| 那一半 | 函数 | 卡在哪 |
|---|---|---|
| 分配 | `malloc` `calloc` `realloc` `free` | ① libc 那份靠模块级堆句柄，没有地方放；② 仓里 `mall.lomt` 是**调用方给 arena** 的无状态分配器，给不出那个签名；③ 内建 `alloc` 的堆布局（8 字节头）是**编译器私有的**，从库里伸手读它就是把实现细节钉进库（`docs/158` 要防的正是这个） |
| `FILE*` | `fopen` `fread` `fwrite` `fclose` `stdin` `stdout` | 一半是模块级可变全局，一半是**世界面**（`docs/219` §4 的 `world` 类）。要读写用 `lompi/store/host`（`fd`/`syscall`）或内建 `syscall4` |
| 进程 | `exit` `abort` `atexit` | 要运行期 |
| 环境 | `getenv` `setenv` | 世界面 |
| 浮点格式化 | `%f` `%e` `%g` `strtod` | 语言层**没有浮点类型**；仓里是 `f64*.lomt` 的软浮点，接进格式串是独立的一件事 |
| 其他格式 | 精度 `.N`、星号宽度 `*`、`%p`、`%n` | 没做（不是不能做，是这一版没做） |

### 5.2 边界：**这不是 C ABI**

LibL 是**源码库**，不是一份能被 C 对象链进去的 `.a`。C 代码调不到 `LibL::strlen` ——
那要"Loment -> 目标文件/归档"这条路，属于 §6。**别说它"能替换 libc"**：它替换的是
"写 Loment 的人要自己手写 `strcmp`" 这件事。

---

## 6. 顺带撞出来的三个既有缺陷（**都在 LibL 之外**）

写这一轮时撞出来的，**不是 LibL 的 bug**，也不是这一轮修 —— 按
`feedback_test_findings_go_to_issues` 的规矩单独记：

1. **`mem.lomt::mem_move` 按偏移判重叠方向** —— `if doff <= so` 走前向，而偏移是**相对各自
   基址**的。跨基址调用时判断是错的：`mem_move(b + 1, 0, b, 0, 4)` 两边偏移都是 0，
   于是它走前向，而真实地址是 `dst > src`，必须倒着拷 —— 结果是第 2 个字节被写成第 1 个
   还没读的字节（实测 `memmove_b2` 读回 65，glibc 是 66）。**libc 比的是地址。**
   LibL 的 `memmove` 因此自己按指针判方向，不转给它。
2. **`numfmt.lomt::numfmt_hex_into` 发出非法 IR** —— 它把 `u64` 右移一个 **i32 变量**
   （`v >> shift`），参考实现发出 `lshr i64 %v, i32 %shift`，clang 当场拒。这是既有的
   **混宽缺陷**（`docs/180` §4.2 / `docs/188` §7.1.1）落在 std 代码上 —— 一个**谁都没调用**
   的函数，直到有人 `use numfmt` 才把编译打断。探针因此自带了十进制输出。
3. **`ptr == ptr` 发出 `icmp == ptr`**（issue **#205**，已知）。库代码一律比 `u64`（§3.5）。

前两条**没人报过**（第 3 条早有 issue）。

---

## 7. 未来：把收编面与运行时落成 Loment 写

用户 2026-10-11 的裁决：这一条**进本文档，不是这一版**。

### 7.1 要动的是什么

今天有三处**手写的、不是 Loment 写的**东西：

| 在哪 | 是什么 | 为什么手写 |
|---|---|---|
| `tools/lomelf.py` 的 `emit_conscript` + 自举侧孪生 | `memcpy` `memset` `memcmp` `malloc` `free` 的**机器码字节**（各约 102 字节） | 链接器要发它们，而编译器编不了自己 |
| `tools/lomentc.py` 的 `_IR_HEAP`（4627 B） | `gc_manual` 的分配器（bump + 地址序空闲链 + 前沿回退） | 同上 |
| `tools/lomentc.py` 的 `_IR_RUNTIME`（1465 B） | freestanding 运行时（`__loment_memcmp` 一族） | 同上 |

`docs/219` §6 第 2 条说的是：`malloc` 之所以能从端口表上**划掉**，唯一理由是
**名字的另一端在我们手里**。而今天那"另一端"是一串字节，不是一份能被读、被测、
被判据覆盖的源码。

**落成 Loment 写的**，才有三件今天没有的东西：① 一份实现而不是两份手抄；
② 它进 `std` 的判据网（今天那 102 字节没有行为判据，只有"两侧逐字节相同"）；
③ 端口表上"另一端在我们手里"这句话变成**可读的**。

### 7.2 为什么不能现在就做：鸡生蛋

编译器要编出**它自己的一部分**。三个形态，代价从小到大：

- **A. 编译期注入**：编译器在发产物时，把运行时的 Loment 源码**先编一遍**再拼进去。
  参考实现（Python）做得到；自举侧要能编它自己 —— 而自举侧今天已经在编自己（种子固定点），
  多一层不是原理问题，是**种子大小与编译时间**的问题（`lomrel` 的 4 MiB 归一化缓冲
  今天用在 3469847 B，余量 724457）。
- **B. 预编成一份 `.lomt` 常量表**：运行时用 Loment 写成，构建时编成 IR **文本**，
  存进仓库当生成物（与 `selfhost_driver.ll` 同一个形状）。
  **这条路最稳**：`docs/158` §5 那条"两个实现逐字节一致"照旧成立，而运行时从此是**一份源**。
- **C. 真把它做成可链接的库**：要"Loment -> 目标文件"这条路，今天不存在（CLI 只有
  `ir`/`build`/`pkg`/`lib`，没有 `--emit-obj`）。

### 7.3 与端口表的交点（`docs/219` §5.3 那条**没拍的价**）

`docs/219` §5.3：**完备 ⇔ 没有按名解析 ⇔ 没有动态链接**。所以 `docs/173` 的**阶段 3
（动态库装载）与端口表互斥**。这一条**到今天仍然没有裁决**，到 S2/S3 会撞上。
LibL 不退让：它只是"名字的一端在我们手里"这件事在**源码层**的第一步。

---

## 8. 与其它文档的关系

* `docs/199` —— **上游**：std 可达性、平名字冲突、门面为什么别用。`libl` 这个入口正是
  那条"面小、不撞名、编得快"的推论。
* `docs/180` §3 —— **约定**：输出写进调用方缓冲、输入 `(ptr, 长度)`、找不到返回 `-1`。
  LibL 的 `str*`/`mem*` 遵守它；`cstr_of` 是它在这一层的补丁（§3.1）。
* `docs/183` V1 —— **上游**：静态 libc。本文是它在**源码层**先落的一步（不链二进制）。
* `docs/219` §6 第 2 条 —— **未来那一格的支点**：`malloc` 从表上划掉的理由是"另一端在我们手里"。
* `docs/210` §3 —— `kind` 代替比较函数这个形状的来源（"不读源码可判的数"同族）。
* `docs/188` —— "不猜，报错"：不认识的格式说明符**原样输出** `%` 与那个字符，不静默吞。
