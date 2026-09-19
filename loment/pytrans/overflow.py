"""overflow.py —— **不同意集**：Python 的 `int` 是**任意精度**，而本语言的 `int` 拼法
是 **`i64`**（有界的 64 位）。

    7 * 1000 ** 8

* **CPython**：`7000000000000000000000000`（7e24）—— 任意精度，不回绕。
* **本语言**：`int` 的拼法就是 `i64`，超过 `2^63-1` 的乘积**回绕**成
  `-4420394637261275136`（二进制补码，低位保留）。

## 为什么这一份必须在"不同意集"里

`tools/pytrans.py` 文件头 §决定 1 写得很直白：

> **`int` 映 `i64`**，与 `potato_from.PY_TYPES` 一致。Python 的 `int` 是任意精度、
> `i64` 有界 —— 溢出行为的差别**没有处理**（溢出变成回绕）。

按 `docs/188` §0 那条「**表层语法只决定拼法与形状，不决定语义**」，这正是对本语言的、
而不是对 Python 的：`int` 是"**整数**"在 Python 写法里的**拼法**，本语言的整数是 `i64`。
**翻译器不去模拟 Python 的大整数** —— 想保住 CPython 的语义就得在每次乘法前塞一个
溢出检查并把它抬成无穷精度，那是**在 Python 写法的皮里做 CPython**，方向反了。

（这与 `intdiv.py` / `loopend.py` 那两处不同：那两处是**运算符**的语义（`//` `%` 的
截断方向、`for` 的终值），这一处是**类型的宽度**。三者都不迁就 Python，理由却是三条。）

## 判据

与 `intdiv.py` / `loopend.py` 同一个形状：算出来的数**必须与 CPython 的不同**，
否则这份语料只是在摆样子。

本语言给 **175**、CPython 给 **7**（推到这两个数的过程在判据的 `_overflow_loment` /
`_overflow_cpython` 里）。两个数**必须不同**。

## 它踩的约束（写这份语料时真正撞到的东西）

* 这一门**没有三元表达式**（`x if c else y`），也**没有 `break`/`continue`** ——
  所以 `sign` / `digit_sum` 只能写成 `if` + 提前 return、`while` + 标志位那种形状。
* 局部变量的宽度**由拼法定死**：`int` 一律 `i64`。想写 `u8` / `i32` 在这里**说不出来**
  —— 那一档是 `docs/188` §7.1.1 记的边界（翻译器不做宽度跟踪），只能绕开。
* `**` 不在子集里，所以"连乘溢出"只能写成一个 `for` 循环（也正是这一份要做的事）。
"""

BASE = 1000


def grow(start: int, times: int) -> int:
    """连乘 `times` 次。**本语言是 `i64`，超出就回绕**；`start=7, times=8` 时已经是负数了。"""
    x = start
    for i in range(times):
        x = x * BASE
    return x


def sign(v: int) -> int:
    """`v < 0` 给 1，否则给 0。本语言没有三元，所以写成 `if` + 提前 return。"""
    if v < 0:
        return 1
    return 0


def digit_sum(v: int) -> int:
    """`|v|` 的十进制数位之和。循环 + 条件；本语言没有 `break`，靠循环条件退出。"""
    w = v
    if w < 0:
        w = 0 - w
    total = 0
    while w > 0:
        total = total + w % 10
        w = w // 10
    return total


def main() -> int:
    huge = grow(7, 8)
    s = sign(huge)
    d = digit_sum(huge) % 100
    return s * 100 + d
