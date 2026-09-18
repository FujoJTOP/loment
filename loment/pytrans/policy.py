"""policy.py —— **同意集**：这份 Python 与 Loment 算出同一个数。

它是 `loment/examples/multilang/02-python/policy.lomt` 的形状：阈值判级 + 一个取整
帮手。挑它是因为这一门最常见的几样东西它都凑齐了：

    模块常量     WARN_LEVEL / ALARM_LEVEL —— `pub const`，函数体里当名字用
    位运算       `frame & 0xFFFF`
    分支         `if` + 提前 return
    `//`         映本语言的 `/`（**这里两边一致**，因为操作数非负）
    增量赋值     `acc += i * a` —— Loment 没有 `+=`，展开成赋值
    `for range`  降级成 `while`

**为什么它必须在"同意集"里**：这一条判据拿 **CPython 当对照组**（跑那份 Python、
比它 `main()` 的数）。只有两边算得一样时，"翻译出来的 == CPython 的"才是个有意义的
判据。两边**不一致**的那些写法各有各的语料，见 `intdiv.py` 与 `loopend.py`。

期望值是推出来的（判据里的 `_policy_expected`）：
`level(1000)=2`、`bucket(700,2)=350`、累加 `0+2+4+6=12` ⇒ `2*10 + (350+20) + 12 = 402`，
取 200 的余 = **2**。
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
    """整除。**注意操作数非负** —— 负号上两边不一样，那一课在 `intdiv.py`。"""
    return v // size


def main() -> int:
    a = level(1000)
    b = bucket(700, 2)
    acc = 0
    for i in range(4):
        acc += i * a
    return (a * 10 + (b + 20) + acc) % 200
