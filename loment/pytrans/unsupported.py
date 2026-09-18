"""unsupported.py —— **反例语料**：`and` 当值用（`docs/187` §一处收得住的差）。

它存在的唯一理由是让"不静默丢"这条纪律**可被判据钉住**。

Python 的 `and` / `or` 返回的是**操作数**，不是布尔：

    1 and 2     == 2
    0 or 5      == 5

而 Loment 的 `&&` / `||` 出的是 `bool`。所以在**条件位置**两者一致（只问真假），
可以翻；在**值位置**不一致（Python 可能给出任何 int），**必须报错**。

照翻成 `&&` 的话，`pick(1, 2)` 会在 Python 里给 2、在 Loment 里给 1 —— 两边都编得过，
只有数不一样。这正是本仓反复要消灭的形状（`docs/167`），比拒绝坏得多。
"""


def pick(a: int, b: int) -> int:
    return a and b


def main() -> int:
    return pick(1, 2)
