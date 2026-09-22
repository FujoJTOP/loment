# `java/01-intseq` —— 整数序列与数论工具箱（Java 语法）

**用 Java 的拼法写的 Loment。** `intseq.lomt` 首行 `choose write grammar java` 说的是
"用 Java 的读法读这份文件"；它的语义仍然是 Loment 的（`docs/188` §0：**表层语法只决定
拼法与形状，不决定语义**）。宿主 `main.lomt` 是原生 Loment，只负责调 `entry()` 与退出。

整份 `intseq.lomt` 是一个 `class IntSeq { … }` 外壳 —— 那一层会被翻译器**抹成等长空白**
（行号不变），里面的 `static` 方法于是成了顶层函数。类名 `IntSeq` 在这个语境里只是外壳
的名字（判据里对照组要一个与文件名同名的 `public class`，所以那边叫 `IntSeq.java`）。

EXPECTED: 240

## 二十个方法，各钉一类写法

| 方法 | 钉住什么 |
|---|---|
| `gcd` | `while` 里**改形参**、两变量互相赋值 |
| `lcm` | **跨方法调用** gcd；先除后乘以免中间量过大 |
| `isqrt` | 二分搜索 + **溢出**那条纪律（见下）：`mid <= n / mid` 而非 `mid * mid <= n` |
| `powMod` | **递归 + 平方**，两层提前 `return` |
| `fib` | `for` 循环 + **双累加器交换**（子集无数组） |
| `factorial` | 单函数**自递归**，底部提前 `return` |
| `binomial` | `for` 循环 + **改形参**（对称化简）+ 逐项乘除保持精确 |
| `digitSum` | 取余整除 |
| `digitRoot` | **真·嵌套循环**（外层重算、内层数位和） |
| `reverseInt` | 累积乘法（子集无数组、无字符串） |
| `isPalindrome` | **跨方法调用** reverseInt + 提前 `return` |
| `popcount` | Kernighan 那一招 `n & (n - 1)` |
| `trailingZeros` | **位运算与循环条件混用**（`(n & 1) == 0` 要加括号） |
| `gray` | 纯位运算表达式 `n ^ (n >> 1)`，不循环 |
| `isPrime` | 提前 `return`；上界用 `d <= n / d`（除法不溢出） |
| `countPrimes` | `for` 外层 + 跨方法调用 isPrime，把 `1`/`0` 当整数累加 |
| `coprimePairs` | **显式双层嵌套循环**（外层 `i`、内层 `j`）+ 内层调 gcd + 计数累加 |
| `collatzSteps` | 单函数自递归，两条分支各一次调用 |
| `triangular` | 纯表达式（除法与乘法的结合），不循环 |
| `hamming` | 两个函数之间的一次调用 |
| `entry` | **每一项都参与**最后的求和取模 —— 任一算法错了退出码就变 |

## 期望值怎么推出来的（**不抄对照组**）

`entry()` 把十九个结果**逐项相加**后取模 256。逐项：

```
g  = gcd(1071, 462)       = 21
      1071 = 462*2 + 147；462 = 147*3 + 21；147 = 21*7 + 0
l  = lcm(21, 6)           = 42       （21/gcd(21,6)*6 = 21/3*6 = 7*6）
s  = isqrt(1000000)       = 1000     （1000^2 = 1000000）
pm = powMod(7, 13, 1000)  = 407
      7^13 = 96889010407；96889010407 mod 1000 = 407
      逐次：7→49→343→401→807→649→543→801→607→249→743→201→407（省去逐步取模）
fb = fib(20)              = 6765
      0,1,1,2,3,5,8,13,21,34,55,89,144,233,377,610,987,1597,2584,4181,6765
fa = factorial(10)        = 3628800
bc = binomial(20, 10)     = 184756
ds = digitSum(987654)     = 39       （9+8+7+6+5+4）
dr = digitRoot(987654)    = 3        （987654 → 39 → 12 → 3）
rv = reverseInt(12345)    = 54321
pa = isPalindrome(12321)  = 1        （reverseInt 回去还是 12321）
pc = popcount(255)        = 8
tz = trailingZeros(40)    = 3        （40 = 0b101000）
gr = gray(10)             = 15       （10 ^ (10 >> 1) = 10 ^ 5 = 0b1111）
pr = countPrimes(100)     = 25
cp = coprimePairs(10)     = 63
      [1,10]×[1,10] 里 gcd(i,j)==1 的对数，按 i 数：
      i=1:10  i=2:5  i=3:7  i=4:5  i=5:8  i=6:3  i=7:9  i=8:5  i=9:7  i=10:4
      10+5+7+5+8+3+9+5+7+4 = 63
co = collatzSteps(27)     = 111
tr = triangular(20)       = 210      （20*21/2）
hm = hamming(7, 1)        = 2        （popcount(7 ^ 1) = popcount(6) = 2）
```

求和：

```
21 + 42 + 1000 + 407 + 6765 + 3628800 + 184756 + 39 + 3 + 54321 + 1
   + 8 + 3 + 15 + 25 + 63 + 111 + 210 + 2
= 3876592
3876592 mod 256 = **240**      （256 * 15142 = 3876352；3876592 - 3876352 = 240）
```

⇒ **退出码 = 240**（`entry() & 255`）。

## 跑

```bash
# Loment 侧（前方为仓根）：前门把 Java 写法翻成 Loment，再编成 ELF，在 WSL 里跑
python .tmp-recon/runlomt.py loment/examples/multisyntax-projects/java/01-intseq/main.lomt   # RC=240

# 只过检查（不跑）
python tools/lomentc.py --check loment/examples/multisyntax-projects/java/01-intseq/main.lomt

# 想看看翻出来的 Loment（调试用）
python .tmp-recon/front.py loment/examples/multisyntax-projects/java/01-intseq/intseq.lomt

# 对照组（真 Java）：把首行抹成等长空白存成 IntSeq.java，配一个 Harness
javac -d <临时目录> IntSeq.java Harness.java && java -cp <临时目录> Harness   # 也是 240
```

对照组夹具（`Harness.java`）：

```java
public class Harness {
    public static void main(String[] a) {
        System.exit(IntSeq.entry() % 256);
    }
}
```

## 这一门撞到的子集边界（**都是照缝改，不是绕**）

写作时逐条撞到、按子集画的线改了写法的：

1. **没有字段/全局量**，连 `static final` 常量都省了 —— 全部用局部量。
2. **没有数组** —— `fib` 用双累加器交换、`reverseInt` 用累积乘法、数位用取余整除拆，
   一律标量。Java 的 `int[] xs` 在这门拼法里被 `trans_core` 点名拒。
3. **没有 `switch` / `break` / `continue`** —— `isPrime`、`powMod`、`collatzSteps` 的
   分支全部写成 `if` / `else` 链 + 提前 `return`。
4. **没有 `~`（按位取反）** —— 本语言一元运算符只有 `-` 与 `!`。要写反码得用
   `v ^ -1`（全宽）或 `v ^ 255`（收窄到 8 位）；这份语料没用到，但知道这条路。
5. **没有 `>>>`（逻辑右移）** —— 本语言的 `>>` 是**算术**的，与 Java 的 `>>` 一致，
   所以只用 `>>`；`>>>` 会被**点名拒**并给出路 `(a as u32) >> n`。
6. **没有字符串** —— 本语言没有字符串值，I/O 全交给宿主。

## 与 C 那门共用的一条纪律：**别踩整数溢出**

`s`（`isqrt`）与 `isPrime` 的上界探测都用**除法**（`mid <= n / mid`、`d <= n / d`）而不是
乘法。乘法在 n 过 8.6 万时会溢出有符号 32 位 —— C 那门踩过，`clang -O1` 与 Loment 对
"溢出"这件事本就没有共同答案（那边 UB、这边回绕），三方会给出三个不同的数。改除法后
三方一致。本语言与 Java 都是 32 位回绕，所以只要**不溢出**，"数学上应为多少"就对得上。
