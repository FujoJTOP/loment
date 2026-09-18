"""loopend.py —— **不同意集**：`for` 循环**结束之后**循环变量是几。

    for i in range(3):
        total = total + i
    return total + i

* **CPython**：`i` 停在**最后一个取到的值** `2`。
* **本语言**：`for i in range(…)` 的降级式（`docs/187` 文件头 §`for` 的定义）是

      let i = a;  while i < b { <body>;  i = i + 1; }

  所以循环走完 `i` 已经加到了 `b`，**它是 `b` 而不是 `b - 1`**。

**这一条不是疏漏，是定义。** 本语言没有 `for`，`for i in range(…)` 是**拼法**，
它的意思由那条降级式定下来。写下来是因为它是个**看得见的行为差异**：
从 Python 过来的人会在这里拿到不一样的数。

判据与 `intdiv.py` 同一个形状：算出来的数**必须与 CPython 的不同**，
否则这份语料只是在摆样子。

期望：`walk(3) = (0+1+2) + 3 = 6`、`walk(4) = (0+1+2+3) + 4 = 10` ⇒ **16**。
CPython 会给 `5 + 9 = 14`。
"""


def walk(n: int) -> int:
    total = 0
    for i in range(n):
        total = total + i
    return total + i


def main() -> int:
    return (walk(3) + walk(4)) % 200
