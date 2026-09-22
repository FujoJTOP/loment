# `c/01-algo` —— 整数算法集（C 语法）

**用 C 的拼法写的 Loment。** `algo.lomt` 首行 `choose write grammar c` 说的是
"用 C 的读法读这份文件"；它的语义仍然是 Loment 的（`docs/188` §0：**表层语法只决定
拼法与形状，不决定语义**）。宿主 `main.lomt` 是原生 Loment，只负责调 `entry()` 与退出。

## 十三个函数，各钉一类写法

| 函数 | 钉住什么 |
|---|---|
| `gcd` / `lcm` | `while` 里**改形参**、两变量互相赋值、跨函数调用 |
| `isqrt` | 二分搜索 + **溢出**那条纪律（见下） |
| `powmod` | **递归** + 两层提前 `return` |
| `digit_sum` / `digit_count` | 取余整除；`n == 0` 那一支是"至少一位"的顶法（子集没有 `do-while`） |
| `reverse_int` | 累积乘法（子集没有数组） |
| `collatz_steps` | 单函数**自递归**，两条分支各一次调用 |
| `popcount` | `n & (n - 1)` 那一招，位运算与循环条件混用 |
| `reverse_bits8` | 移位与或拼在一个表达式里 |
| `is_prime` / `count_primes` | 双层循环 + 内层提前 `return`；外层把 bool 当整数累加 |
| `triangular` | 纯表达式（除法与乘法的结合），不循环 |
| `hamming` | 两个函数之间的一次调用 |
| `entry` | **每一步都参与**最后的取余 —— 任一算法错了退出码就变 |

## 期望值怎么推出来的（**不抄对照组**）

```
g  = gcd(1071,462)   = 21
l  = lcm(21,6)       = 42
s  = isqrt(1000000)  = 1000      -> s % 100 = 0
p  = 3^13 % 1000     = 323       -> p % 100 = 23
d  = digit_sum(987654)    = 39
n  = digit_count(1000000) = 7
r  = reverse_int(12345)   = 54321 -> r % 100 = 21
c  = collatz_steps(27)    = 111
b  = popcount(255)        = 8
v  = reverse_bits8(1)     = 128   -> v / 8 = 16
pr = count_primes(100)    = 25
t  = triangular(20)       = 210   -> t / 10 = 21
h  = hamming(7,1)         = 2     （popcount(6)）

21+42+0+23+39+7+21+111+8+16+25+21+2 = 336
336 % 256 = **80**
```

EXPECTED: 80


## 跑

```bash
# Loment 侧（前方为仓根）
python tools/lomentc.py --print llvm loment/examples/multisyntax-projects/c/01-algo/main.lomt
# 判据（含 clang 对照组与上面那个期望值）
python tools/loment_multisyntax_projects_test.py
```

## 这一份撞出来的东西

**`isqrt` 最初的写法踩了 UB，三方给出三个不同的数。** 详见上级 README
「语料的纪律」那一节 —— `mid * mid <= n` 在 n 过 8.6 万时有符号溢出，
`clang -O1` 给 1000（它拿 UB 优化成了数学解）、本语言给 458753（回绕）、
我独立推的又是 1000。**判据不可比**，改成 `mid <= n / mid` 之后三方一致。

它同时说明这条判据为什么要**三方**：只跟 clang 比会得到一个"通过"，
只跟自己推的比会让失败说不出是哪一边错。
