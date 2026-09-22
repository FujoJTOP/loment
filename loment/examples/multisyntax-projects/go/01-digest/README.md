# `go/01-digest` —— 确定性伪随机流 + 校验和工具箱（Go 语法）

**用 Go 的拼法写的 Loment。** `digest.lomt` 首行 `choose write grammar go` 说的是
"用 Go 的读法读这份文件"；它的语义仍然是 Loment 的（`docs/188` §0：**表层语法只决定
拼法与形状，不决定语义**）。宿主 `main.lomt` 是原生 Loment，只负责调 `entry()` 与退出。

> 文件里带着 `package main`（在 `choose` 行的后面）：那是 Go 的**包声明**，真 Go 编译
> 需要它；Loment 侧会把它跳过去。对对照组时只抹掉**第一行**（`choose …`），剩下的
> 就是一份合法 Go 源。

## 十九个函数，各钉一类写法

| 函数 | 钉住什么 |
|---|---|
| `lcg_next` | 一步线性同余；**不回绕**（乘子与状态都选在 int32 正区间内） |
| `lcg_bytesum` | `for cond` 循环 + 跨函数调用 + 状态当参数传 + **`+=`**（Go 里 `+=` 是语句） |
| `lcg_xorfold` | **`^=`** 复合赋值 + `^` 与 `%` 混在同一表达式里 |
| `mix16` | 移位 / 异或 / 乘法连做，每步 `& 65535` 压回 16 位 |
| `crc8_step` | 内层定次数循环 + `if/else` 两分支 + 一串位运算括号 |
| `crc8_stream` | **嵌套循环**（外层 n 步，内层在 `crc8_step` 里的 8 步） |
| `fletcher8` | 一个循环里**同时**推两个累加变量（`b` 依赖 `a` 的当轮值） |
| `bit_reverse16` | **三截 `for`**（`for i := …; …; i++`，翻译器把 `i` 外提改名）+ **`i++`** |
| `count_bits` | Kernighan 的 `n & (n-1)`；循环条件是"值不为零" |
| `gray_code` | 纯表达式 `x ^ (x >> 1)`，不循环 |
| `hamming16` | 两个函数之间的调用（异或后数 1） |
| `fold_recursive` | **递归** + 两条**提前 `return`** |
| `gcd` | `while` 里两个变量互相赋值 + 负数取正 |
| `seed_of` | 一个函数里串起两个别的函数（`gcd` 与 `mix16`） |
| `hex_digit_sum` | `>> 4` 与 `& 15` 配对的取位循环 |
| `is_prime` | 返回 **`bool`**；`n/d` 探上界（**除法不溢出**） |
| `count_primes` | 外层循环里 **`bool` 只当条件**（Go 里 bool 不能当整数累加） |
| `sum_squares` | 纯表达式里的除法乘法结合 `n*(n+1)*(2n+1)/6` |
| `entry` | **每一步都参与**最后的取余 —— 任一算法错了退出码就变 |

## 这一门的独特之处（值得记）

**Go 没有隐式数值转换**，所以 Go 源码里本来就到处写 `int32(x)` —— 而那个写法**正好**
是 Loment 的 `as`。六门里只有 Go 是"源码里已经把转换写好了"的那一门：**既不用像
C/C++ 那样去猜哪里补转换，也没有 C#/Java 那种"两边都收"的模糊面**。

**Go 里 `i++` 与 `x += e` 是语句、没有值**（`y = i++` 在 Go 里编不过），所以它们能
**一字不差**地搬成 `i = i + 1` / `x = x + e`。而在 C / Java / C# 里它们是**表达式**、
有值，映射不过去，那几门一律拒 —— 同一处写法，结论相反，差别在**源语言**那边。

**每一处都用 `int32` 而不是 `int`**：Go 的 `int` 宽度随平台（x86-64 上是 64 位），
写死 `int32` 才与 Loment 的 `i32` 同宽，没有平台歧义。

**也没有踩溢出的坑**：`state < 65536` 且乘子 25173，`state*25173 < 65536*25173 < 2^31`，
加常数 13849 后仍在正区间 —— 全程没有一次超过 int32。所以这里没有 `c/01-algo` 的
`isqrt` 那种"两边对溢出做了不同选择、判据因此不可比"的坑（对比上级 README 的
「语料的纪律」）。

## 期望值怎么推出来的（**不抄对照组**）

用 Python 按**同一套算法从零重写一遍**算得（不是运行上面的产物、也不是抄 `go` 的输出）：

### 1. 种子 `seed = seed_of(6, 28, 496)`

```
gcd(6, 28)  = 2            （辗转相除：gcd(28,6)=gcd(6,4)=gcd(4,2)=gcd(2,0)=2）
gcd(2+1, 496) = gcd(3, 496) = 1   （496 = 2^4·31，不被 3 整除）
送入 mix16 的数 = 2 + 1 + 6 + 28 + 496 = 533
```

`mix16(533)`（全在 `& 65535` 下推）：

```
y0 = 533 & 65535                = 533
y1 = (533 ^ (533<<5)) & 65535    = (533 ^ 17056) & 65535   = 16565
y2 = (y1  ^ (y1 >>7)) & 65535    = (16565 ^ 129) & 65535    = 16436
y3 = (y2  * 31) & 65535          = 509516 % 65536           = 50764
y4 = (y3  ^ (y3 <<3)) & 65535    = (50764 ^ 12896) & 65535  = 62508
seed = 62508
```

### 2. 各个量（输入都是 `seed`，`n` 见括号）

LCG 递推：`s ← (s·25173 + 13849) mod 65536`，取低字节 `s & 255`。

| 量 | 算法 | 值 |
|---|---|---|
| `bs` | `lcg_bytesum(seed, 40)`：40 步低字节之和 | **4324** |
| `xf` | `lcg_xorfold(seed, 40)`：40 步低字节异或折叠 | **8** |
| `mx` | `mix16(seed)` | **31769** |
| `cr` | `crc8_stream(seed, 20)`：40 次 `crc8_step`（poly 0x07）后的 CRC 字节 | **169** |
| `fl` | `fletcher8(seed, 20)`：`(b<<8)\|a`，`a,b` 为两个模 255 累加和 | **54226** |
| `br` | `bit_reverse16(seed)`：16 位镜像 | **13359** |
| `cb` | `count_bits(seed)` | **8** |
| `gc` | `gray_code(seed) = seed ^ (seed>>1)` | **36410** |
| `hm` | `hamming16(seed, mx)`：`count_bits(seed ^ mx)` | **6** |
| `fr` | `fold_recursive(seed, 6)`：反复 `mix16` 6 轮 | **54130** |
| `hx` | `hex_digit_sum(seed)`：十六进制各位数字和 | **33** |
| `pc` | `count_primes(30)`：2..30 里的素数个数 | **10** |
| `ss` | `sum_squares(10) = 10·11·21/6` | **385** |

### 3. 求和取余

```
bs + xf + mx + cr + fl + br + cb + gc + hm + fr + hx + pc + ss
= 4324 + 8 + 31769 + 169 + 54226 + 13359 + 8 + 36410 + 6 + 54130 + 33 + 10 + 385
= 194837
194837 % 256 = 21        （761·256 = 194816，余 21）
```

⇒ **期望值 = 21**。

EXPECTED: 21

## 跑

```bash
# 前门（仓根为基准）：看翻译出来的 Loment
python .tmp-recon/front.py loment/examples/multisyntax-projects/go/01-digest/digest.lomt
# Loment 侧：跑出来读退出码
python .tmp-recon/runlomt.py loment/examples/multisyntax-projects/go/01-digest/main.lomt

# 对照组（真 Go）：抹掉第一行、补夹具、构建、跑
#   Sample.go  = digest.lomt 的第一行换成等长空白
#   harness.go = package main; import "os"; func main(){ os.Exit(int(entry() % 256)) }
go build -o side.exe Sample.go harness.go
./side.exe; echo $?
```

## 三方跑出来的数

| 来源 | 命令 | 数 |
|---|---|---|
| 对照组（`go build` 真 Go） | `go build … && ./side.exe; echo $?` | **21** |
| Loment 侧（前门翻 + lomelf + WSL 跑） | `python .tmp-recon/runlomt.py …/main.lomt` | **21** |
| 独立期望值（Python 从零重写） | 见上「期望值怎么推出来的」 | **21** |

三者相等。
