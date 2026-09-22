# `cpp/01-eca` —— 一维元胞自动机工具箱（C++ 语法）

**用 C++ 的拼法写的 Loment。** `eca.lomt` 首行 `choose write grammar cpp` 说的是
"用 C++ 的读法读这份文件"；它的语义仍然是 Loment 的（`docs/188` §0：**表层语法只决定
拼法与形状，不决定语义**）。宿主 `main.lomt` 是原生 Loment，只负责调 `entry()` 与退出。

**主题**：一条 32 格的二进制带子塞在一个 `unsigned int` 里（bit i = 第 i 格）。
元胞自动机的每一步，看每个格子的左邻/自身/右邻三位，按"规则号"的哪一位决定下一代
这一格是 0 还是 1。于是这一整份从种子带子造起、到量它、到演化它，全程是
**位运算 + 循环 + 递归** —— 同一主题下自然内聚。

## 二十二个函数，各钉一类写法

| 函数 | 钉住什么 |
|---|---|
| `bit_at` | `>>` 的**右边是变量**；`& 1` 取一格 |
| `bit_on` | **`bool` 返回 + `true` / `false` 字面量**（不是 `1`/`0`） |
| `put_bit` | 置位 `\|` / 清位 `- mask`；掩码在 `unsigned int` 上左移（`1 << 31` 是 UB，这里不给它机会）；**两次提前 `return`** |
| `rule_lookup` | 两层**提前 `return`**（越界给 0，不用数组）；`>>` 按变量取位 |
| `apply_rule` | 三次乘法拼下标，**跨函数调用** `rule_lookup` |
| `step` | `while` 里串起 `bit_at` / `apply_rule` / `put_bit`；层内 `if` 夹边界 |
| `evolve` | 迭代版：`while` 走代 |
| `evolve_rec` | **递归**版：自己调自己，`steps <= 0` 是出口 |
| `popcount` | Kernighan 那一招 `n & (n - 1)` |
| `parity` | `^` 折位；全程在 `unsigned int` 上做，不跟有符号混 |
| `transitions` | 相邻位不同就 +1（`!=` 出 `bool`，当条件用） |
| `count_changes` | **跨函数的嵌套循环**：外层走代、`transitions` 里还有一层 |
| `weighted_mass` | **字面的 `for` 套 `for`**（外层走代、内层扫 32 格） |
| `longest_run` | 循环里记最大（**没有 `continue`** 的写法） |
| `run_count` | `in_run` 标志当替身（**没有 `continue`**） |
| `reverse_bits` | `<<1` 与 `\|最低位` 在一个循环里 |
| `is_symmetric` | **循环里提前 `return false`**，全过 `return true`（`bool` 返回） |
| `highest_bit` | 全 0 时提前 `return -1`（**负数**的来源） |
| `gray_code` | 一个纯表达式 `v ^ (v >> 1)` |
| `single_seed` / `block_seed` | 用循环一位一位造种子（不走 `(1 << n) - 1`，避开有符号 UB） |
| `entry` | **每一步都参与**最后的取余 —— 任一算法错了退出码就变 |

## 期望值怎么推出来的（**不抄对照组**）

两个种子：
* `center = single_seed(16)` = 第 16 格置位 = **65536**
* `block  = block_seed(12)` = 低 12 位全 1 = **4095**

### ① 规则 90，8 代：`p90 = evolve(90, center, 8)`

规则 90 是 `next[i] = 左邻 XOR 右邻`（零边界）。从**单个细胞**出发它就是
**帕斯卡三角模 2**：第 t 代在格 `16 - t + 2k` 有 1 ⟺ `C(t,k)` 是奇数。

```
g0 {16}
g1 {15,17}
g2 {14,18}
g3 {13,15,17,19}
g4 {12,20}
g5 {11,13,19,21}
g6 {10,14,18,22}
g7 {9,11,13,15,17,19,21,23}
g8 {8,24}         C(8,k) 为奇只在 k=0 与 k=8  -> 格 16-8+0=8、16-8+16=24
```

⇒ `p90` = bit8 + bit24 = 256 + 16777216 = **16777472**（0x01000100）
⇒ `popcount(p90) = 2`

### ② 规则 30，递归 6 代：`p30 = evolve_rec(30, center, 6)`

规则 30 是 `next[i] = 左邻 XOR (自身 OR 右邻)`（零边界）。它的 8 项表：

```
(l c r)  000 001 010 011 100 101 110 111
 结果     0   1   1   1   1   0   0   0      （0b00011110 = 30）
```

```
g0 {16}
g1 {15,16,17}
g2 {14,15,18}
g3 {13,14,16,17,18,19}
g4 {12,13,16,20}
g5 {11,12,14,15,16,17,19,20,21}
g6 {10,11,14,19,22}
```

⇒ `p30` = 2^10 + 2^11 + 2^14 + 2^19 + 2^22 = 1024+2048+16384+524288+4194304 = **4738048**
⇒ `parity(p30) = 5 个 1 -> 1`；`run_count(p30, 32) = 4`（`{10,11}` `{14}` `{19}` `{22}` 四段）

### ③ 规则 90，从 `block` 走 5 代的"碎裂总量"：`count_changes(90, block, 5)`

逐代的 `transitions`（相邻位不同的次数）：

```
g0 row=4095  (低 12 位全 1)                       1   （只在 bit11|bit12 处断一下）
g1 row=6145  {0,11,12}                            3
g2 row=15362 {1,10,11,12,13}                      4
g3 row=26117 {0,2,9,10,13,14}                     7
g4 row=65288 {3,8,9,10,11,12,13,14,15}            4
```

⇒ `count_changes = 1+3+4+7+4 = 19`

### ④ 规则 30，从 `block` 走 4 代的"加权质量"：`weighted_mass(30, block, 4)`

每代把第 i 格的 1 记 `i+1` 分：

```
g0 row=4095  低 12 位全 1 -> 1+2+…+12            = 78
g1 row=4097  {0,12}      -> 1+13                 = 14
g2 row=14339 {0,1,11,12,13} -> 1+2+12+13+14      = 42
g3 row=19461 {0,2,10,11,14} -> 1+3+11+12+15      = 42
```

⇒ `weighted_mass = 78+14+42+42 = 176`

### ⑤ 剩下几个（都在 `block` = 低 12 位全 1 上）

```
longest_run(block,12)     = 12          （12 个 1 连成一段）
highest_bit(block)        = 11
reverse_bits(block,12)    = 4095        （12 个 1 反过来还是 12 个 1）-> &15 = 15
gray_code(block)          = 4095 ^ 2047 = 2048 -> %255 = 8
is_symmetric(block,12)    = true        -> bonus += 40
bit_on(center,16)         = true        -> bonus += 20
```

### ⑥ 合起来

```
pc  = popcount(p90)      = 2
pa  = parity(p30)        = 1
tr  = count_changes      = 19
wm  = weighted_mass      = 176
lr  = longest_run        = 12
rc  = run_count(p30,32)  = 4
rl  = reverse_bits & 15  = 15
hb  = highest_bit        = 11
gray                     = 8
bonus                    = 40 + 20 = 60

2+1+19+176+12+4+15+11+8+60 = 308
308 % 256 = **52**
```

EXPECTED: 52

（`exit` 只留低 8 位，所以 `entry()` 本身是 308 时退出码是 52 —— 两边都过一遍
`& 255`，判据比的是这个数，见上级 README「契约」第 3 条。）

**这个推导是另写一遍的**：按上面的算法规格用 Python **独立实现了一份**（拿的是一串
小整数与位运算，**不是**抄 Loment 或 g++ 的输出），算出来也是 308 -> 52。
三方都对到 52 才算过。

## 跑

```bash
# Loment 侧（前方为仓根）
python tools/lomentc.py --check loment/examples/multisyntax-projects/cpp/01-eca/main.lomt
# 判据（含 g++ 对照组与上面那个期望值）
python tools/loment_multisyntax_projects_test.py
```

## 绕过的子集限制（有价值的写法信息）

这一门（`docs/188` §7.1 的 Stage A 子集）很窄，写的时候撞到、并改掉的：

1. **没有 `~`** —— `put_bit` 清位本来该是 `row & ~mask`，被拒；改成
   `row - mask`（那一位本来就是 1，减掉正好落下）。
2. **没有三元 `?:`、没有复合赋值 `+=` `<<=`** —— 一律拆成单独的赋值语句。
3. **没有 `break` / `continue`** —— `run_count` 用 `in_run` 标志把 `continue` 顶掉。
4. **没有 `switch` / `case`** —— 规则表用 `(rule >> idx) & 1` 直接取位，本来也不需要。
5. **没有裸块 `{ }`**（`docs/...`/`trans_core` §语义选择 2）—— 局部变量的作用域只能靠
   函数本身，`for` 的初值会被**改名外提**成 `i__1`。
6. **没有数组/指针/结构体/字符串** —— 一条带子塞进一个 `unsigned int`。
7. **没有预处理指令**（`#include` / `#define`）—— 语料不带 include（判据里也钉着）。
8. **没有 `char`**（符号性由实现决定）—— 全程用 `unsigned int` / `int`。
9. **`1 << 31` 不能写**：`1` 是有符号的，左移 31 位在 C++ 里是 UB。掩码改成
   `unsigned int mask = 1; mask = mask << i;`（左边是无符号变量，移位就有了定义）。

**没有全局变量**：种子、规则、宽度全部走参数传进去。
