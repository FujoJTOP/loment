"""policy.py —— Python 那门的第一份语料（`docs/187`）。

它是 `loment/examples/multilang/02-python/policy.lomt` 的**形状**：阈值判级 + 一个
取整帮手。挑它是因为这一门最常见的几样东西它都凑齐了：

    模块常量     WARN_LEVEL / ALARM_LEVEL —— `pub const`，函数体里当名字用
    位运算       `frame & 0xFFFF`
    分支         `if` + 提前 return
    `//`         Python 向下取整，Loment 向零截断 —— **必须发辅助函数**
    增量赋值     `acc += i * a` —— Loment 没有 `+=`，展开成赋值
    `for range`  Loment 没有 `for`，降级成 `while`

`main()` 的期望值是推出来的（见判据里的 `_policy_expected`）：
`level(1000)=2`、`bucket(-7,2)=-4`、累加 `0+2+4+6=12` ⇒ `2*10 + (-4+20) + 12 = 48`。
"""

#: 读数超过这两个阈值就升一级。改这里不影响调用方。
WARN_LEVEL = 500
ALARM_LEVEL = 900


def level(frame: int) -> int:
    """帧 -> 0（正常）/ 1（警告）/ 2（告警）。取低 16 位读数。"""
    v = frame & 0xFFFF
    if v >= ALARM_LEVEL:
        return 2
    if v >= WARN_LEVEL:
        return 1
    return 0


def bucket(v: int, size: int) -> int:
    """**向下取整**的除法 —— 这是这一门与 C 那门最不一样的地方之一。"""
    return v // size


def main() -> int:
    a = level(1000)
    b = bucket(-7, 2)
    acc = 0
    for i in range(4):
        acc += i * a
    return (a * 10 + (b + 20) + acc) % 200
