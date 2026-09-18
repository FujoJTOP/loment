"""scope.py —— **函数级作用域 vs 块级作用域**（`docs/187` 第三条语义差）。

这一门里最不起眼、也最容易翻错的一处：Python 的名字是**函数级**的。

    if c:
        x = 10
    else:
        x = 20
    return x          # 合法 —— 而 Loment 的 `let` 是**块级**的

照直翻会得到 `if c { let x: i64 = 10; } else { let x: i64 = 20; } return x;`，
**Loment 编不过**（块外没有 `x`）。所以声明的位置要按"这个名字**最外层**在哪一层被赋值"
来定：最外层就是函数体那一层的就地 `let`（可读），更深的**上提到函数头**。

另一件只有 Python 才有的：**`for` 的目标在循环之后还活着**。

    for i in range(3):
        total = total + i
    return x + total + i      # `i` 是 2（循环结束时的值）

所以 `for` 的目标**不用改名**（与 C 那门相反 —— C 的 `for (int i = …)` 是新作用域，
那边必须改名外提）。这一份语料把这两条一起钉住。

`main()` 期望 **40**：`f(1) = 10 + 3 + 2 = 15`、`f(0) = 20 + 3 + 2 = 25`。
"""


def walk(c: int) -> int:
    """`x` 在两条分支里各赋一次，循环之后才用 —— 上提那一条就靠它。"""
    if c:
        x = 10
    else:
        x = 20
    total = 0
    for i in range(3):
        total = total + i
    return x + total + i


def main() -> int:
    return walk(1) + walk(0)
