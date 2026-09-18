#!/usr/bin/env python3
"""loment_interp.py — **编译期子集**解释器（S4.0，`docs/184` §3/§9）。

## 它是干什么的

`comefor` 的宏体是**一段 Loment 程序**，内核要在编译期**跑**它（`docs/184` §0 定的）。
这个文件是那件事的**参考侧**：它走 `lomentc.Parser` 已经产出的 AST，把程序跑出一个值。

自举侧要写一份**逐字节同结果**的镜像（`loment/selfhost/interp.lomt`）—— 所以这里定的是
**规范**，不是"一个实现"。两边不一致就是 S4.0 红。

## 编译期子集（S4.0 版，刻意小）

宏体要干的事是"读 token、算、吐 token"，所以只需要：

| | 收 |
|---|---|
| 类型 | `u64`（**整数一律 u64 语义**）、`bool`、`ptr`、`str`（字面量，只读）、`[T; N]` |
| 语句 | `let` / 赋值 / `if`/`else` / `while` / `for i in lo..hi` / `return` / 表达式语句 |
| 表达式 | 整/布/串字面量、标识符、二元、一元、调用、下标、数组字面量、`as` |
| 项 | `fn` / `const` |
| 内建 | `alloc` `free` `load8` `store8` `load32` `store32` `str_len` `str_byte` `str_ptr` |

**不收**（记着，不是忘了）：struct / enum / match / trait / impl / 泛型 / capability / guard /
`?` / 方法调用 / 字段访问。**都能以后加，但 S4.0 一条都不需要。**

## 值模型

* 整数一律 **u64 语义**：运算后 `& (2**64-1)`。`as` 到更窄的宽度就按那个宽度掩码。
  （这条是**规范**：镜像那一侧也必须这么算。）
* `bool` 就是整数 0/1。
* `ptr` 是**宿主内存**里的字节偏移（不是真实地址 —— 真实地址两个实现不可能相同，
  而判据要的是同结果）。
* `str` 是 Python `str`（只读）。
* 数组是 Python `list`。

## 入口与输出

语料程序必须恰好有一个 `fn main() -> u64`。解释器把它的返回值按**规范形式**打到 stdout：

    OK <十进制>

出错时打 `ERR <种类>` 并以 1 退出 —— **两个实现要连错误的种类都一致**。

    python tools/loment_interp.py loment/ct/<程序>.lomt
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402
import lomc  # noqa: E402

MASK64 = (1 << 64) - 1
#: 宿主内存大小 —— 与自举侧同值（`docs/184` §3 的一致性包含它）。
HEAP = 1 << 20


class InterpError(Exception):
    """规范化的解释期错误。`kind` 是两个实现必须一致的**种类**。"""

    def __init__(self, kind: str, line: int = 0):
        super().__init__(f"{kind} @{line}")
        self.kind = kind
        self.line = line


class _Ret(Exception):
    def __init__(self, value):
        self.value = value


class Interp:
    def __init__(self, mod: lomentc.Module):
        self.funcs = {f.name: f for f in mod.funcs if not f.extern}
        self.consts = {c.name: c.value for c in mod.consts}
        self.mem = bytearray(HEAP)
        self.top = 16          # 前 16 字节留空 —— 0 当"空指针"
        self.freed: list[int] = []
        self.depth = 0

    # ---------------------------------------------------------------- 内存
    def _alloc(self, n: int) -> int:
        if self.top + n > HEAP:
            raise InterpError("oom")
        p = self.top
        self.top += (n + 7) & ~7
        return p

    def _chk(self, p: int, n: int) -> None:
        if p < 0 or p + n > HEAP:
            raise InterpError("oob")

    # ---------------------------------------------------------------- 表达式
    def ev(self, e, env: dict):
        t = type(e).__name__
        if t == "IntLit":
            return e.value & MASK64
        if t == "BoolLit":
            return 1 if e.value else 0
        if t == "StrLit":
            return e.value
        if t == "Ident":
            if e.name in env:
                return env[e.name]
            if e.name in self.consts:
                return self.consts[e.name] & MASK64
            raise InterpError("undef", e.line)
        if t == "Un":
            v = self.ev(e.expr, env)
            if e.op == "-":
                return (-v) & MASK64
            if e.op == "!":
                return 0 if v else 1
            raise InterpError("badun", e.line)
        if t == "Bin":
            return self._bin(e, env)
        if t == "Cast":
            v = self.ev(e.expr, env)
            if not isinstance(v, int):
                raise InterpError("badcast", e.line)
            w = _WIDTHS.get(e.type)
            return v & ((1 << w) - 1) if w else (v & MASK64)
        if t == "Call":
            return self._call(e, env)
        if t == "ArrayLit":
            return [self.ev(x, env) for x in e.items]
        if t == "Index":
            a = self.ev(e.obj, env)
            i = self.ev(e.idx, env)
            if not isinstance(a, list):
                raise InterpError("notarray", e.line)
            if i < 0 or i >= len(a):
                raise InterpError("oob", e.line)
            return a[i]
        raise InterpError(f"unsupported:{t}", getattr(e, "line", 0))

    def _bin(self, e, env: dict):
        op = e.op
        # 短路
        if op == "&&":
            return 1 if (self.ev(e.left, env) and self.ev(e.right, env)) else 0
        if op == "||":
            return 1 if (self.ev(e.left, env) or self.ev(e.right, env)) else 0
        a, b = self.ev(e.left, env), self.ev(e.right, env)
        if isinstance(a, str) and isinstance(b, str) and op == "==":
            return 1 if a == b else 0
        if not isinstance(a, int) or not isinstance(b, int):
            raise InterpError("badoperand", e.line)
        if op == "+":
            return (a + b) & MASK64
        if op == "-":
            return (a - b) & MASK64
        if op == "*":
            return (a * b) & MASK64
        if op == "/":
            if b == 0:
                raise InterpError("divzero", e.line)
            return (a // b) & MASK64
        if op == "%":
            if b == 0:
                raise InterpError("divzero", e.line)
            return (a % b) & MASK64
        if op == "&":
            return a & b
        if op == "|":
            return a | b
        if op == "^":
            return a ^ b
        if op == "<<":
            return (a << (b & 63)) & MASK64
        if op == ">>":
            return (a >> (b & 63)) & MASK64
        if op == "==":
            return 1 if a == b else 0
        if op == "!=":
            return 1 if a != b else 0
        if op == "<":
            return 1 if a < b else 0
        if op == "<=":
            return 1 if a <= b else 0
        if op == ">":
            return 1 if a > b else 0
        if op == ">=":
            return 1 if a >= b else 0
        raise InterpError(f"badop:{op}", e.line)

    # ---------------------------------------------------------------- 调用
    def _call(self, e, env: dict):
        n = e.name
        if n in self.funcs:
            f = self.funcs[n]
            if len(e.args) != len(f.params):
                raise InterpError("arity", e.line)
            args = [self.ev(a, env) for a in e.args]
            return self.call_fn(f, args)
        args = [self.ev(a, env) for a in e.args]
        return self._builtin(n, args, e.line)

    def _builtin(self, n: str, a: list, line: int):
        if n == "alloc":
            return self._alloc(_u(a[0]))
        if n == "free":
            self.freed.append(_u(a[0]))
            return 0
        # **基址 + 偏移是两件事**：`load8(p, off)` 读的是 `mem[p + off]`。
        # 这四条原本只用 `a[0]`（漏了偏移）—— 而 `loment/ct/mem.lomt` 恰好只用 `off = 0`，
        # 于是 §9 那条"两个解释器逐字节同结果"一直是绿的，分歧**藏着**。
        # 自举侧本来就对，错的是这一侧；`loment/ct/offsets.lomt` 是补上的钉子。
        if n == "load8":
            p = a[0] + a[1]
            self._chk(p, 1)
            return self.mem[p]
        if n == "store8":
            p = a[0] + a[1]
            self._chk(p, 1)
            self.mem[p] = a[2] & 0xFF
            return 0
        if n == "load32":
            p = a[0] + a[1]
            self._chk(p, 4)
            return int.from_bytes(self.mem[p:p + 4], "little")
        if n == "store32":
            p = a[0] + a[1]
            self._chk(p, 4)
            self.mem[p:p + 4] = (a[2] & 0xFFFFFFFF).to_bytes(4, "little")
            return 0
        if n == "str_len":
            return len(a[0]) if isinstance(a[0], str) else 0
        if n == "str_byte":
            s = a[0]
            i = a[1]
            if not isinstance(s, str) or i >= len(s):
                raise InterpError("oob", line)
            return ord(s[i]) & 0xFF
        if n == "str_ptr":
            # `str` 在宿主内存里没有实体 —— 返回 0，两个实现同规则。
            return 0
        raise InterpError(f"nobuiltin:{n}", line)

    def call_fn(self, f, args: list):
        # **显式深度上限**，不靠 Python 的 `RecursionError` —— 那个上限是实现细节（默认 1000
        # 上下，还随栈大小变），两个实现不可能靠它对齐。自举侧同值同语义。
        self.depth += 1
        if self.depth > MAX_DEPTH:
            self.depth -= 1
            raise InterpError("depth")
        try:
            env = dict(zip([p.name for p in f.params], args))
            try:
                self.exec_body(f.body, env)
            except _Ret as r:
                return r.value
            return 0    # 无 return 落到末尾 -> 0（与"必须有值"的检查器不冲突：这里只求值）
        finally:
            self.depth -= 1

    # ---------------------------------------------------------------- 语句
    def exec_body(self, stmts: list, env: dict) -> None:
        for s in stmts:
            self.exec_stmt(s, env)

    def exec_stmt(self, s, env: dict) -> None:
        t = type(s).__name__
        if t == "Let":
            env[s.name] = self.ev(s.expr, env) if s.expr is not None else 0
            return
        if t == "Assign":
            v = self.ev(s.expr, env)
            tg = s.target
            if type(tg).__name__ == "Ident":
                if tg.name not in env:
                    raise InterpError("undef", s.line)
                env[tg.name] = v
                return
            if type(tg).__name__ == "Index":
                a = self.ev(tg.obj, env)
                i = self.ev(tg.idx, env)
                if not isinstance(a, list):
                    raise InterpError("notarray", s.line)
                if i < 0 or i >= len(a):
                    raise InterpError("oob", s.line)
                a[i] = v
                return
            raise InterpError("badtarget", s.line)
        if t == "If":
            if self.ev(s.cond, env):
                self.exec_body(s.then, env)
            else:
                self.exec_body(s.otherwise, env)
            return
        if t == "While":
            guard = 0
            while self.ev(s.cond, env):
                guard += 1
                if guard > LIMIT:
                    raise InterpError("spin", s.line)
                self.exec_body(s.body, env)
            return
        if t == "For":
            lo, hi = _u(self.ev(s.lo, env)), _u(self.ev(s.hi, env))
            i = lo
            while i < hi:
                env[s.var] = i
                self.exec_body(s.body, env)
                i += 1
                if i - lo > LIMIT:
                    raise InterpError("spin", s.line)
            return
        if t == "Return":
            raise _Ret(self.ev(s.expr, env) if s.expr is not None else 0)
        if t == "ExprStmt":
            self.ev(s.expr, env)
            return
        raise InterpError(f"unsupported-stmt:{t}", getattr(s, "line", 0))


#: 循环上限 —— 两个实现同值。宏体不该长跑；跑飞了要**报出来**，不是挂着。
LIMIT = 1 << 22

#: 调用深度上限 —— 两个实现同值（见 `call_fn` 那条注释：不能靠 `RecursionError`）。
MAX_DEPTH = 256
_WIDTHS = {"u8": 8, "u16": 16, "u32": 32, "u64": 64,
           "i8": 8, "i16": 16, "i32": 32, "i64": 64, "bool": 1, "ptr": 64}


def _u(x) -> int:
    """取无符号值（bool 也是它）。"""
    return x & MASK64 if isinstance(x, int) else 0


def run(path: Path) -> tuple[int, str]:
    """返回 `(退出码, 输出行)`。规范形式见文件头。"""
    try:
        mod = lomentc.load(path)
    except lomc.LomError as e:
        return 2, f"PARSE {e}"
    entry = next((f for f in mod.funcs if f.name == "main" and not f.extern), None)
    if entry is None:
        return 2, "ERR noentry"
    it = Interp(mod)
    try:
        v = it.call_fn(entry, [])
    except InterpError as e:
        return 1, f"ERR {e.kind}"
    except RecursionError:
        return 1, "ERR depth"
    return 0, f"OK {_u(v)}"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("用法: loment_interp.py <程序.lomt>", file=sys.stderr)
        return 2
    rc, out = run(Path(argv[1]))
    print(out)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
