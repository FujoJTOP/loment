# `python/01-numtheory` —— 整数数论工具箱（Python 拼法）

**用 Python 的拼法写的 Loment。** `numtheory.lomt` 首行 `choose write grammar python` 说的是
"用 Python 的读法读这份文件"；它的语义仍然是 Loment 的（`docs/188` §0：**表层语法只决定拼法与
形状，不决定语义**）。宿主 `main.lomt` 是原生 Loment，只负责调 `entry()` 与退出。

模块暴露 `def entry() -> int:`，返回整个项目的答案（退出码 = `entry() & 255`）。

## 项目做什么

二十一个整数数论/位运算函数，围着同一个主题：**整数的整除结构、数位、位模式**。它们互相调用
（`lcm`→`gcd`、`count_primes`→`is_prime`、`digital_root`→`digit_sum`、
`is_palindromic`→`digit_reverse`），`entry()` 把每个函数的结果折成一个数交给宿主 ——
**每一步都参与**最后的取余，所以任一算法错了退出码就变。

三条模块常量（`MODULUS` / `BASE` / `EXPONENT`）是刻意留的：它们钉住"模块级全大写常量在函数体里
当名字用"这条路（与 `loment/pytrans/policy.py` 同一处形状）。

## 每个函数钉住什么

| 函数 | 钉住什么 |
|---|---|
| `gcd` | `while` 里**同时改两个局部量**、一个 `%` 一个赋值互相接棒 |
| `lcm` | **跨函数调用** + 一次整除；先除后乘（中间值不放大） |
| `isqrt` | 二分搜索 + **不放大中间值**的探测（`mid <= n // mid`，不是 `mid * mid <= n`） |
| `powmod` | **递归** + 平方；两条**提前 `return`**；用模块常量当实参 |
| `mulmod` | **加倍—相加**：位运算（`% 2` / `// 2`）与循环混用，每步先取模 |
| `mod_inverse` | 穷举 + 提前 `return`；**刻意不用扩展欧几里得**（那会把系数弄成负数，见下） |
| `is_prime` | 试除法：`while` + **内层提前 `return`**；返回 1/0 而不是 bool |
| `count_primes` | **外层循环里调 `is_prime`** —— 把 int 返回值当整数累加 |
| `smallest_factor` | 提前 `return`；探测写 `d <= n // d`（除法不溢出） |
| `phi` | **嵌套 `while`**：外层扫因子、内层把该因子的幂吃干净 |
| `divisor_sum` | `for i in range(...)` 降级（钉 `for` → `while` 那条定义） |
| `divisor_count` | 与 `divisor_sum` 同骨架，钉"计数那一支" |
| `popcount` | `n & (n - 1)` 那一招（Kernighan），位运算与循环条件混用 |
| `parity` | 异或折叠：把每一位折进一个 `^` 累加器 |
| `reverse_bits8` | 固定 8 轮，移位与按位或拼在一个表达式里 |
| `digit_sum` | `% 10` 取末位、`// 10` 砍末位 |
| `digit_reverse` | 累积乘法（`r * 10 + 末位`），不借数组 |
| `digital_root` | **自递归**，末尾只有一条出口 |
| `is_palindromic` | 跨函数调用 + 两处提前 `return` |
| `collatz_len` | 单函数**自递归**，两条分支各一次调用（`collatz_len(27)` 递归深度 111） |
| `triangular` | 纯表达式（乘除结合），不循环 |
| `entry` | 把上面每一个结果折成一个数 —— **每一步都参与**最后的取余 |

## 期望值怎么推出来的（**不抄任何输出**）

每个输入都刻意挑得小，读到结果就能对照（见下「为什么走同意集」）。

```
g  = gcd(1071,462)         = 21
l  = lcm(21,6)             = 42
s  = isqrt(1000000)        = 1000          -> s % 100  = 0
p  = 3^13 % 1000           = 1594323%1000  = 323   -> p % 100 = 23
mm = mulmod(12345,6789,1000) = 83810205%1000 = 205 -> mm % 100 = 5
mi = mod_inverse(3,97)     = 65            （3*65 = 195 = 2*97 + 1）
pr = count_primes(100)     = 25
sf = smallest_factor(91)   = 7             （91 = 7 * 13）
ph = phi(36)               = 12            （36*(1-1/2)*(1-1/3) = 36*1/2*2/3）
ds = divisor_sum(28)       = 56            （1+2+4+7+14+28；28 是完全数）
dc = divisor_count(36)     = 9             （1,2,3,4,6,9,12,18,36）
pc = popcount(255)         = 8
pa = parity(182)           = 1             （182 = 1011 0110，5 个 1 ⇒ 奇）
rb = reverse_bits8(1)      = 128           -> rb // 8 = 16
dr = digital_root(9875)    = 2             （9+8+7+5=29 -> 2+9=11 -> 1+1=2）
dg = digit_sum(987654)     = 39
dv = digit_reverse(12345)  = 54321         -> dv % 100 = 21
ip = is_palindromic(12321) = 1
cl = collatz_len(27)       = 111
tr = triangular(20)        = 210           -> tr // 10 = 21
```

求和（每一项都是上面缩过的那个）：

```
21 + 42 + 0 + 23 + 5 + 65 + 25 + 7 + 12 + 56 + 9 + 8 + 1 + 16
   + 2 + 39 + 21 + 1 + 111 + 21
= 485
485 % 256 = 229
```

⇒ **期望值 = 229**：

```
EXPECTED: 229
```

（判据 `loment_multisyntax_projects_test` 从这一行读走期望值 —— 它要的是**写语料的人
独立推出来的**那个数，所以这里不能抄对照组，也不写成别的花样。）

## 三方（三个数必须相等，都过 `& 255`）

| | 值 | 命令 |
|---|---|---|
| control（真 CPython） | **229** | 把模块首行抹成等长空白写成 `.py`，再 `python -c "import runpy,sys;m=runpy.run_path(sys.argv[1]);print(m['entry']() & 255)" <那份 .py>` |
| loment（前门 → lomelf → WSL） | **229** | `python .tmp-recon/runlomt.py loment/examples/multisyntax-projects/python/01-numtheory/main.lomt`（打印 `RC=229`） |
| expected（独立推的） | **229** | 上面那段算术；另用 Python 从零再写一遍算法（见下）得 485 ⇒ 485 % 256 = 229 |

三个数一致 ⇒ **229 / 229 / 229**。

期望值那一路**不是抄输出**：我用与模块不同的写法独立算了一遍（`math.gcd` / `math.isqrt` /
`pow(..., mod)` / `math.lcm` / 位串反转 / 逐位计数 …），逐个值与上表一致，和也是 485。

看翻译出来的 Loment（调试用）：

```bash
python .tmp-recon/front.py loment/examples/multisyntax-projects/python/01-numtheory/numtheory.lomt
# 或走编译器（会在有错时报到行与原因）：
python tools/lomentc.py --check loment/examples/multisyntax-projects/python/01-numtheory/main.lomt
```

## 这一门的语义差，以及我怎么绕的

这一份刻意走**同意集** —— 全非负操作数，于是 CPython 是个有意义的对照。撞到并绕开的有：

1. **`//` 与 `%` 在负号上两边不一样**（`-7 // 2 == -4` vs `-7 / 2 == -3`；`-7 % 2 == 1` vs
   `-7 % 2 == -1`，`loment/pytrans/intdiv.py` 是那条"不同意集"）。**绕法**：每一个
   `//` / `%` 的两个操作数都保证非负 —— 非负时"向下取整"与"向零截断"是同一件事。
   具体地：
   * `mod_inverse` **不用扩展欧几里得**（那会的系数是负的），改成穷举 `i in [1, mod)`；
   * `powmod` / `mulmod` 的 `exp` / `y` 都从非负输入出发、只减半；
   * `phi` 的 `result - result // p`、`x // p` 都是非负数上的整除。
2. **`int` 是 i64（回绕），不是 CPython 的任意精度**（SKILL §「Boundaries」）。这一条**不会报错**，
   只会静默差。**绕法**：所有中间值都刻意留得远小于 2^63 —— 最大的一个中间量在 `mulmod` 里
   是 `x * 2`（`x < 1000`），`isqrt` 的 `lo + hi + 1` 最大也就 2*10^6 量级。
3. **`bool` 不是 `int` 的子类；`and` / `or` 返回**操作数**，Loment 的 `&&` / `||` 出 `bool`**
   （`loment/pytrans/bool_as_int.py`）。**绕法**：判素/回文这类函数**返回字面量 1/0**，
   不写 `return a > b`；`and` / `or` 只出现在**条件位置**（`if` / `while` 的测试），
   不放进整数表达式。翻译器对"整数位置拿到 bool"是**点名拒**的，所以这一条绕不了假。
4. **没有三元 `a if c else b`** —— 用 `if` / `else` 两条 `return` 顶（`is_palindromic`、
   `powmod` 都是这个形状）。
5. **没有 `break` / `continue`**（`loment/pytrans/loopend.py` 那一门）—— 所有循环都用
   条件正常收尾，没有一处靠 `break` 跳出。
6. **`for i in range(a, b)` 降级成 `let i = a; while i < b { …; i = i + 1; }`**
   （pytrans 文件头 §`for` 的**定义**，不是搬运）。两个后果：循环结束后 `i == b`
   （不是 CPython 的 `b - 1`），且**循环体里不许给 `i` 赋值**。`divisor_sum` /
   `divisor_count` 都不依赖 `i` 的终值，也没有给 `i` 赋值。
7. **没有 `~`（按位取反）** —— 本语言的一元运算符只有 `-` 与 `!`。这一份没用到它；
   要"按宽度取反"的地方（`reverse_bits8` 的 `v & 255`、`parity` 的 `v >> 1`）都是正向写法。
8. **`**` 与 `/`（真除）被拒** —— `powmod` 用递归平方而不是 `**`；`triangular` /
   `lcm` 用 `//`。
9. **没有 `while True` + `break`**（子集外，见 5/6）—— `gcd` / `isqrt` 都是"条件为真才转"的
   形状，不靠死循环跳出。

## 这一份撞到的工具链问题

**无。** 整条链（前门翻译 → `lomentc` 检查 → LLVM IR → `lomelf` → WSL 跑）一次通过，
三方一致。翻译出来的 Loment 与手写的那份读起来一样干净（见 `front.py` 的输出）。

一条**顺带观察**（不是 bug，不改任何东西）：

* `python tools/lomentc.py --check <file>` **不带 `--emit-*` 目标**时，最后一行打印的是
  `[OK] …: 生成物与磁盘一致` —— 其实**没有对照物**可比（`--check` 是配合 `--emit-* PATH`
  做"磁盘上的生成物还新不新"的）。不过它**确实**先构造了 rust / potato / llvm 三份表示，
  所以源有错时照样报到行与原因（实测：把 `gcd` 的 `return x` 改成 `return q`，它报
  `第 42 行: 用了没赋过值的 'q'`）。**用它迭代是有效的**，只是那句"与磁盘一致"别当回事。
