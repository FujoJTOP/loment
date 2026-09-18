#!/usr/bin/env python3
"""pytrans.py — **Python 子集 -> Loment** 翻译器（`docs/187`，Stage A 的第二门）。

    Python 源码 --pytrans--> Loment 源码

## 为什么这一门比 C 那门难 —— 三处**语义**差，不是写法差

`docs/186` 里 C 那门只有一处真差（`int` vs `bool`）。Python 这一门有三处，而且每一处
**错了都照样编得过**，只是结果不对：

| | Python | Loment | 怎么办 |
|---|---|---|---|
| **`and` / `or`** | 返回**操作数**（`1 and 2 == 2`） | `&&` / `\|\|` 出 `bool` | **只在条件位置收**；当值用就报错（见下） |
| **`//` 与 `%`** | **向下取整**（`-7 % 2 == 1`） | **向零截断**（实测 `-7 % 2 == -1`） | 发一对辅助函数 `__py_mod` / `__py_floordiv` |
| **作用域** | **函数级**（`if` 里赋的名，外面看得见） | `let` 是**块级** | 声明**上提到函数头**（见下） |

第三条最不起眼、也最容易翻错：Python 里

    if c:
        x = 1
    return x        # 合法（c 为真时）

直译成 `if c { let x: i64 = 1; }` 之后再 `return x;` —— **Loment 编不过**（块外没声明）。
所以声明的位置要按"这个名字**最外层**在那个深度被赋值"来定：最外层就是函数体的用
`let`（可读），更深的**上提到函数头**（正确）。上提那份给零值初始化 —— 见 §语义选择。

**`//` 与 `%` 为什么要发辅助函数**：`-7 // 2` Python 说 `-4`、Loment 说 `-3`。这不是
"不用负数就没事"能糊过去的：`%` 在取模、分桶、哈希里到处都是，而符号取决于输入。
恒等式（`b != 0` 时成立）：

    py_mod(a, b)      = ((a % b) + b) % b          # Loment 的 `%` 是截断的
    py_floordiv(a, b) = (a - py_mod(a, b)) / b

两个方向都验过（`-7/2`、`7/-2`、`-7/-2`、`7/2`，见 `loment/pytrans/floordiv.py` 的语料）。

## 一处**收得住**的差：`and` / `or` 当值用

    if a and b:            # 条件位置 —— 只问真假，两边一致，收
        ...
    x = a and b            # 值位置 —— Python 给的是**操作数**（可能是任何 int）
                           #            而 Loment 的 `&&` 只能给 bool。**报错，不猜。**

条件位置是**保义**的：`a and b` 同真 iff `(a != 0) && (b != 0)`，而且**短路也保住**了。

## 子集

    模块级    NAME = <整数>            （常量, 全大写才算 —— 与 potato_from 同一条判据）
              def f(a: int, b: int) -> int: …
    语句      赋值/注解赋值/增量赋值   if/elif/else   while   for i in range(…)   return   f(…)
    表达式    整数  名字  调用  一元 - + ~ not   二元 算术/位/比较   布尔 and/or（仅条件）

**不收的一律报错**（`docs/167`）：`break` / `continue` / `lambda` / 推导式 / `try` /
`with` / 导入 / 元组解包 / 链式比较 / `**` / `/`（真除，出浮点）/ `str`·`list`·`dict` /
`for … else` / `while … else`。理由与 C 那门一样：**跳过 = 产出一份少算一步却照样能编的单元**。

## 语义选择（不是翻译，是决定）

1. **上提的声明白零值。** Python 里"没赋过就读"会 `NameError`，Loment 拿到的是 0。
   这是**行为差异**，不是等价翻译 —— 写下来是因为它改的是行为。
2. **`int` 映 `i64`**，与 `potato_from.PY_TYPES` 一致。Python 的 `int` 是任意精度，
   `i64` 是有界的 —— 溢出行为的差别**没有处理**（溢出变成回绕）。
3. **局部变量的类型从**它**第一次赋值的右值推**（比较/布尔出 `bool`，其余 `i64`）。
   Python 没有声明，所以要有一个"谁来定"的规则；这条是可读且确定的。

用法:

    python tools/pytrans.py SRC.py [--out OUT.lomt]
退出码: 0 = 成功 / 1 = 用到子集外的东西 / 2 = 用法或读取错误。
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

#: Python 注解 -> Loment 类型。与 `potato_from.PY_TYPES` **同表**（`int` 映 `i64`）——
#: 两处对不上的话，`lomt_from` 发的签名与这里发出来的对不上，而那是**静默**的。
_TYPES = {"int": "i64", "bool": "bool", "None": "()"}

#: 二元运算符 -> Loment 运算符。**`/` 与 `**` 不在**（真除出浮点、乘方没有对应）。
_BIN = {
    ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.FloorDiv: None, ast.Mod: None,
    ast.LShift: "<<", ast.RShift: ">>", ast.BitOr: "|", ast.BitAnd: "&", ast.BitXor: "^",
}
_CMP = {ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=",
        ast.Gt: ">", ast.GtE: ">="}

#: 辅助函数名。**故意长得不像用户会写的** —— 撞上了会报错，不静默改语义（见 `_emit_helpers`）。
_PY_MOD = "__py_mod"
_PY_FLOORDIV = "__py_floordiv"


class Unsupported(Exception):
    """用到了子集外的东西。**必须报出来** —— 静默跳过 = 产物少算一步却照样能编。"""


class PyError(Exception):
    """这份 Python 本身有问题（语法/名字）。与 `Unsupported` 分开。"""


# ---------------------------------------------------------------- 发射

#: Loment 保留字 —— 生成的标识符不能撞上。
_LOMENT_KW = {
    "module", "use", "fn", "let", "if", "else", "while", "return", "pub", "extern",
    "const", "struct", "enum", "capability", "guard", "excluded", "revocable",
    "trait", "impl", "match", "as", "true", "false", "mut", "addin", "choose",
    "command", "foruse", "comefor", "byuse", "std", "dispatch", "self",
}


def _safe(name: str) -> str:
    return name + "_py" if name in _LOMENT_KW else name


def _ty_of_annotation(node, where: str) -> str:
    """参数/返回注解 -> Loment 类型。没有注解**报错**（Python 不写注解就没有类型可用，
    而 Loment 必须有 —— 猜一个宽度是把作者的意思改掉）。"""
    if node is None:
        raise Unsupported(f"{where}: 没有类型注解（Loment 必须有类型；"
                          f"写 `: int` 或 `-> int`）")
    txt = ast.unparse(node)
    if txt not in _TYPES:
        raise Unsupported(f"{where}: 注解 `{txt}` 不在子集里（只收 {sorted(_TYPES)}）")
    return _TYPES[txt]


class Fn:
    """一个函数翻出来的东西 + 它用到/产生的状态。"""

    def __init__(self, name: str) -> None:
        self.name = name
        #: 本函数用到的辅助函数（`//` `%` 会往里加）。出单元头的时候按这个发。
        self.helpers: set[str] = set()


class Emitter:
    """一个 `def` -> 一段 Loment 源码。"""

    def __init__(self, fns: dict[str, tuple[list[str], str]],
                 consts: dict[str, str] | None = None) -> None:
        #: 本单元所有函数的 `(参数类型表, 返回类型)`。调用点的类型靠它。
        self.fns = fns
        #: 模块常量的名字 -> 类型。生成的 `pub const` 是模块级的，函数体里当名字用得到。
        self.consts: dict[str, str] = dict(consts or {})
        #: 名字 -> 类型（`bool` / `i64`）。**推导出来的**，见文件头 §语义选择 3。
        self.vars: dict[str, str] = {}
        #: 上提的名字（最外层赋值发生在嵌套块里）—— 这些在函数头声明一次。
        self.hoisted: set[str] = set()
        self.helpers: set[str] = set()
        self.lines: list[str] = []
        self.depth = 0
        #: `for` 的计次变量编号（`__t1`、`__t2`…）—— 每个循环一个，嵌套也不撞。
        self.n_tmp = 0

    # ---- 行
    def out(self, s: str) -> None:
        self.lines.append("    " * (self.depth + 1) + s)

    def gap(self) -> None:
        if self.lines and self.lines[-1] != "" and not self.lines[-1].endswith("{"):
            self.lines.append("")

    # ---- 作用域预扫：谁最外层在哪个深度被赋值
    def _scan(self, body: list, depth: int, first: dict) -> None:
        """走一遍函数体，记下每个名字**最外层**被赋值的**深度与那一条语句**。

        深度 0（就是函数体那一层）的可以就地 `let`；更深的必须上提
        （Python 是函数级作用域，而 Loment 的 `let` 是块级 —— 见文件头那条）。
        **那一条语句也要留着** —— 上提时要靠它推类型（`x = (a < b)` 上提的是 `bool`，
        照发 `let x: i64 = 0;` 就把类型改掉了）。
        """
        def note(n: str, st) -> None:
            if n in self.consts:
                raise Unsupported(
                    f"第 {getattr(st, 'lineno', '?')} 行: 给模块常量 `{n}` 赋值"
                    f"（Python 里那是改模块变量，而 `pub const` 改不了 —— "
                    f"要么改名，要么把它做成函数参数）")
            if n not in first or depth < first[n][0]:
                first[n] = (depth, st)

        for st in body:
            if isinstance(st, ast.Assign):
                for tg in st.targets:
                    if isinstance(tg, ast.Name):
                        note(tg.id, st)
                    elif isinstance(tg, (ast.Tuple, ast.List)):
                        raise Unsupported(
                            f"第 {st.lineno} 行: 不支持元组/列表解包赋值"
                            f"（拆成几条单独赋值，或用一个临时变量）")
                self._scan_expr(st.value, note)
            elif isinstance(st, ast.AnnAssign):
                if not isinstance(st.target, ast.Name):
                    raise Unsupported(f"第 {st.lineno} 行: 注解赋值的目标不是名字")
                note(st.target.id, st)
                self._scan_expr(st.value, note)
            elif isinstance(st, ast.AugAssign):
                if not isinstance(st.target, ast.Name):
                    raise Unsupported(f"第 {st.lineno} 行: 增量赋值的目标不是名字")
                note(st.target.id, st)
                self._scan_expr(st.value, note)
            elif isinstance(st, ast.For):
                if not isinstance(st.target, ast.Name):
                    raise Unsupported(f"第 {st.lineno} 行: `for` 的目标不是单个名字")
                note(st.target.id, st)
                self._scan(st.body, depth + 1, first)
                if st.orelse:
                    raise Unsupported(f"第 {st.lineno} 行: 不支持 `for … else`")
            elif isinstance(st, ast.While):
                self._scan_expr(st.test, note)
                self._scan(st.body, depth + 1, first)
                if st.orelse:
                    raise Unsupported(f"第 {st.lineno} 行: 不支持 `while … else`")
            elif isinstance(st, ast.If):
                self._scan(st.body, depth + 1, first)
                self._scan(st.orelse, depth + 1, first)
            elif isinstance(st, ast.Return):
                if st.value is not None:
                    self._scan_expr(st.value, note)
            elif isinstance(st, ast.Expr):
                self._scan_expr(st.value, note)
            elif isinstance(st, ast.Pass):
                pass
            else:
                raise Unsupported(f"第 {st.lineno} 行: 不支持语句 `{type(st).__name__}`"
                                  f"（{_stmt_name(st)}）")

    def _scan_expr(self, node, note) -> None:
        """表达式里出现的名字**只记不声明** —— 但 `for` 的目标藏在表达式之外，
        所以这里只需要把嵌套的 `for`/推导式之类拦住。"""
        for sub in ast.walk(node):
            if isinstance(sub, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                raise Unsupported(f"第 {sub.lineno} 行: 不支持推导式")
            if isinstance(sub, ast.Lambda):
                raise Unsupported(f"第 {sub.lineno} 行: 不支持 `lambda`")

    # ---- 局部变量的类型：第一次赋值的右值说了算
    def _decl_type(self, st) -> str:
        if isinstance(st, ast.AnnAssign):
            return _ty_of_annotation(st.annotation, f"第 {st.lineno} 行")
        v = st.value
        if isinstance(v, ast.Constant) and isinstance(v.value, bool):
            return "bool"
        return self.ty_of(v)

    def ty_of(self, e) -> str:
        """表达式在 Loment 里是什么类型（`bool` / `i64` / `()`）—— **不看上下文**。"""
        if isinstance(e, ast.Constant):
            if isinstance(e.value, bool):
                return "bool"
            if isinstance(e.value, int):
                return "i64"
            raise Unsupported(f"第 {e.lineno} 行: 不支持常量 {e.value!r}"
                              f"（只收整数与 True/False）")
        if isinstance(e, ast.Name):
            if e.id in self.consts:
                return self.consts[e.id]
            if e.id not in self.vars:
                raise PyError(f"第 {e.lineno} 行: 用了没赋过值的 `{e.id}`")
            return self.vars[e.id]
        if isinstance(e, ast.Call):
            if e.func.id == _PY_MOD or e.func.id == _PY_FLOORDIV:
                return "i64"
            r = self.fns.get(e.func.id)
            if r is None:
                raise Unsupported(
                    f"第 {e.lineno} 行: 调用了本单元没有的函数 `{e.func.id}`"
                    f"（Stage A 不跨单元 —— 要调外部函数请留着 `pub extern fn` 那条路）")
            return r[1]
        if isinstance(e, ast.UnaryOp):
            if isinstance(e.op, ast.Not):
                return "bool"          # Python 的 `not` **永远**出 bool（与 `and` 不同）
            return "i64"
        if isinstance(e, ast.BinOp):
            if isinstance(e.op, ast.FloorDiv):
                self.helpers.add(_PY_FLOORDIV)
                return "i64"
            if isinstance(e.op, ast.Mod):
                self.helpers.add(_PY_MOD)
                return "i64"
            return "i64"
        if isinstance(e, (ast.Compare, ast.BoolOp)):
            return "bool"
        raise Unsupported(f"第 {e.lineno} 行: 不支持表达式 `{type(e).__name__}`"
                          f"（{_expr_name(e)}）")

    # ---- 表达式：`want` 是**这个位置要的类型**，一路显式往下传
    #
    # **不要把它挂在实例上** —— 嵌套（`if (a and b) + 1`）时外层会串到内层，
    # 而 `BoolOp` 那条规则正是看这个值的。传参是唯一不会串味的写法。
    def ex(self, e, want: str) -> str:
        got = self.ty_of(e)
        if got == "()":
            raise Unsupported(f"第 {e.lineno} 行: 这里用了一个不返回值的调用")
        raw = self.raw(e, want)
        if got == want:
            return raw
        if got == "i64" and want == "bool":
            return f"{raw} != 0"        # Python 的 `if x`：非零即真
        if got == "bool" and want == "i64":
            return f"({raw}) as i64"    # `x = (a < b)`：bool -> 0/1
        raise AssertionError((got, want))

    def raw(self, e, want: str) -> str:
        if isinstance(e, ast.Constant):
            if isinstance(e.value, bool):
                return "true" if e.value else "false"
            return str(e.value)
        if isinstance(e, ast.Name):
            if e.id in self.consts:
                return e.id          # 模块常量名照抄 —— 生成的 `pub const` 就在同一个 scope
            if e.id not in self.vars:
                raise PyError(f"第 {e.lineno} 行: 用了没赋过值的 `{e.id}`")
            return _safe(e.id)
        if isinstance(e, ast.Call):
            f = e.func
            if not isinstance(f, ast.Name):
                raise Unsupported(f"第 {e.lineno} 行: 只收直接函数调用")
            if e.keywords:
                raise Unsupported(f"第 {e.lineno} 行: 不支持关键字实参")
            # `__py_mod` 之类的辅助函数收 i64；用户函数按它的签名收
            args = ", ".join(self.ex(a, "i64") for a in e.args)
            return f"{_safe(f.id)}({args})"
        if isinstance(e, ast.UnaryOp):
            if isinstance(e.op, ast.Not):
                # Python 的 `not` **永远**出 bool，而且收任何真值。
                return f"!{self.ex(e.operand, 'bool')}"
            if isinstance(e.op, (ast.USub, ast.UAdd, ast.Invert)):
                op = {ast.USub: "-", ast.UAdd: "", ast.Invert: "~"}[type(e.op)]
                return f"({op}{self.ex(e.operand, 'i64')})"
            raise Unsupported(f"第 {e.lineno} 行: 不支持一元 `{type(e.op).__name__}`")
        if isinstance(e, ast.BinOp):
            if isinstance(e.op, ast.FloorDiv):
                self.helpers.add(_PY_FLOORDIV)
                return f"{_PY_FLOORDIV}({self.ex(e.left, 'i64')}, {self.ex(e.right, 'i64')})"
            if isinstance(e.op, ast.Mod):
                self.helpers.add(_PY_MOD)
                return f"{_PY_MOD}({self.ex(e.left, 'i64')}, {self.ex(e.right, 'i64')})"
            if isinstance(e.op, ast.Pow):
                raise Unsupported(f"第 {e.lineno} 行: 不支持 `**`"
                                  f"（Loment 没有乘方；写成一个循环或调用）")
            if isinstance(e.op, ast.Div):
                raise Unsupported(f"第 {e.lineno} 行: 不支持 `/`"
                                  f"（Python 的真除出浮点，Loment 没有浮点；用 `//`）")
            op = _BIN.get(type(e.op))
            if op is None:
                raise Unsupported(f"第 {e.lineno} 行: 不支持二元 `{type(e.op).__name__}`")
            return f"({self.ex(e.left, 'i64')} {op} {self.ex(e.right, 'i64')})"
        if isinstance(e, ast.Compare):
            if len(e.ops) != 1:
                # 链式比较在 Python 里等价于 `a<b and b<c`，中间的 `b` **只求值一次**。
                # 照抄成 `(a<b) && (b<c)` 会把 `b` 求两次 —— `b` 是调用时结果就不一样了。
                raise Unsupported(f"第 {e.lineno} 行: 不支持链式比较"
                                  f"（`a < b < c` 里中间那个只求值一次，"
                                  f"拆成 `a < b and b < c` 显式写）")
            op = _CMP.get(type(e.ops[0]))
            if op is None:
                raise Unsupported(f"第 {e.lineno} 行: 不支持比较 `{type(e.ops[0]).__name__}`")
            return f"({self.ex(e.left, 'i64')} {op} {self.ex(e.comparators[0], 'i64')})"
        if isinstance(e, ast.BoolOp):
            if want != "bool":
                raise Unsupported(
                    f"第 {e.lineno} 行: `{'and' if isinstance(e.op, ast.And) else 'or'}` "
                    f"只能用在**条件位置** —— Python 的 `and`/`or` 返回的是**操作数**"
                    f"（`1 and 2` 是 `2`），而 Loment 的 `&&`/`||` 出 bool。"
                    f"当值用请显式写成 `if c {{ 1 }} else {{ 0 }}` 那种意思清楚的形式")
            op = "&&" if isinstance(e.op, ast.And) else "||"
            parts = [self.ex(v, "bool") for v in e.values]
            return "(" + f" {op} ".join(parts) + ")"
        raise Unsupported(f"第 {e.lineno} 行: 不支持表达式 `{type(e).__name__}`"
                          f"（{_expr_name(e)}）")

    # ---- 语句
    def stmts(self, body: list) -> None:
        for st in body:
            self.stmt(st)

    def stmt(self, st) -> None:
        if isinstance(st, ast.Assign):
            if len(st.targets) != 1 or not isinstance(st.targets[0], ast.Name):
                raise Unsupported(f"第 {st.lineno} 行: 赋值的目标只能是单个名字")
            self._bind(st.targets[0].id, st.value, st, st)
            return
        if isinstance(st, ast.AnnAssign):
            if st.value is None:
                raise Unsupported(f"第 {st.lineno} 行: 只注解不给值的赋值"
                                  f"（Loment 的 `let` 要初值）")
            self._bind(st.target.id, st.value, st, st)
            return
        if isinstance(st, ast.AugAssign):
            if not isinstance(st.target, ast.Name):
                raise Unsupported(f"第 {st.lineno} 行: 增量赋值的目标不是名字")
            n = st.target.id
            if n not in self.vars:
                raise PyError(f"第 {st.lineno} 行: `{n}` 没赋过值就用 `{type(st.op).__name__}=`")
            # `x += e` -> `x = x + e`（Loment 没有复合赋值）。`//=` `%=` 也要走辅助函数。
            fake = ast.BinOp(left=ast.Name(id=n, ctx=ast.Load()),
                             op=st.op, right=st.value)
            fake.lineno = st.lineno
            fake.col_offset = st.col_offset
            self.out(f"{_safe(n)} = {self.ex(fake, self.vars[n])};")
            return
        if isinstance(st, ast.Return):
            self.out("return;" if st.value is None else
                     f"return {self.ex(st.value, self.ret)};")
            return
        if isinstance(st, ast.If):
            self.gap()
            self.out(f"if {self.ex(st.test, 'bool')} {{")
            self.depth += 1
            self.stmts(st.body)
            self.depth -= 1
            if st.orelse:
                if len(st.orelse) == 1 and isinstance(st.orelse[0], ast.If):
                    # `elif` —— Loment 有 `else if`，保持那个形状（比嵌一层好读）
                    self.out("} else if "
                             f"{self.ex(st.orelse[0].test, 'bool')} {{")
                    self.depth += 1
                    self.stmts(st.orelse[0].body)
                    self.depth -= 1
                    rest = st.orelse[0].orelse
                    while len(rest) == 1 and isinstance(rest[0], ast.If):
                        self.out(f"}} else if {self.ex(rest[0].test, 'bool')} {{")
                        self.depth += 1
                        self.stmts(rest[0].body)
                        self.depth -= 1
                        rest = rest[0].orelse
                    if rest:
                        self.out("} else {")
                        self.depth += 1
                        self.stmts(rest)
                        self.depth -= 1
                    self.out("}")
                else:
                    self.out("} else {")
                    self.depth += 1
                    self.stmts(st.orelse)
                    self.depth -= 1
                    self.out("}")
            else:
                self.out("}")
            return
        if isinstance(st, ast.While):
            self.gap()
            self.out(f"while {self.ex(st.test, 'bool')} {{")
            self.depth += 1
            self.stmts(st.body)
            self.depth -= 1
            self.out("}")
            return
        if isinstance(st, ast.For):
            self._for(st)
            return
        if isinstance(st, ast.Expr):
            if isinstance(st.value, ast.Constant) and isinstance(st.value.value, str):
                # 函数的**文档字符串**在 `emit_fn` 里已经被摘出来发成 `///` 了
                # （只认第一条）。**出现在这里的**是一句散落的字符串字面量 ——
                # 它在 Python 里什么都不做，多半是写错了，报出来比丢掉好。
                raise Unsupported(f"第 {st.lineno} 行: 这里有一句散落的字符串"
                                  f"（只有函数第一句是文档字符串；"
                                  f"要写注释用 `#` 或 `\"\"\"` 挪到函数开头）")
            if not isinstance(st.value, ast.Call):
                raise Unsupported(f"第 {st.lineno} 行: 表达式语句只收函数调用")
            self.out(f"{self.raw(st.value, 'i64')};")
            return
        if isinstance(st, ast.Pass):
            return
        raise Unsupported(f"第 {st.lineno} 行: 不支持语句 `{type(st).__name__}`"
                          f"（{_stmt_name(st)}）")

    def _bind(self, name: str, value, st, decl_st) -> None:
        """赋值/声明一条。

        名字**已经在 `self.vars` 里**（形参、或已上提到函数头）就是纯赋值；
        否则这是它的第一次出现，就地 `let`（类型按右值推 —— 见文件头 §语义选择 3）。
        """
        sname = _safe(name)
        if name in self.vars:
            self.out(f"{sname} = {self.ex(value, self.vars[name])};")
            return
        ty = self._decl_type(decl_st)
        self.vars[name] = ty
        self.out(f"let {sname}: {ty} = {self.ex(value, ty)};")

    def _for(self, st: ast.For) -> None:
        """`for i in range(…)` -> 一个**计次变量**推着 `i` 走。

        ```
        let i: i64 = 0;
        let __t1: i64 = <start>;
        while __t1 < <stop> {
            i = __t1;              // 循环变量每一轮从这里取
            <body>
            __t1 = __t1 + 1;
        }
        ```

        **为什么不直译成 `while i < n { …; i = i + 1; }`** —— 那样循环结束时 `i` 是
        `n`，而 Python 的 `for` 结束时 `i` 停在**最后一个取到的值**（`n-1`）。
        `scope.py` 那份语料里 `return x + total + i` 正好把这个差值抓出来了
        （直译给 42，Python 给 40）—— **两边都编得过，只有数不一样**，正是这一门
        最该防的形状。计次变量推着走就没有这个差：最后一轮 `i` 取到 `stop-1` 之后
        `__t` 才加一，而 `i` 不再被改。

        **不用改名**（与 C 那门不同）：Python 的 `for` 目标**写进当前函数作用域**，
        没有新作用域 —— 就地用那个名字就是对的。（C 的 `for (int i = …)` 相反，
        那是新作用域，所以那边要改名外提。）

        只收 `range(n)` 与 `range(a, b)`：**带步进的 `range` 一律拒绝** —— 负步进、
        空区间的边界都是要单独想清楚的东西，先不做，别猜。
        """
        if not isinstance(st.target, ast.Name):
            raise Unsupported(f"第 {st.lineno} 行: `for` 的目标不是单个名字")
        it = st.iter
        if not (isinstance(it, ast.Call) and isinstance(it.func, ast.Name)
                and it.func.id == "range"):
            raise Unsupported(f"第 {st.lineno} 行: `for` 只收 `range(…)`"
                              f"（Python 的迭代协议在这里没有对应）")
        if it.keywords or not (1 <= len(it.args) <= 2):
            raise Unsupported(f"第 {st.lineno} 行: 只收 `range(n)` 与 `range(a, b)`"
                              f"（带步进的 `range` 先不做）")
        n = st.target.id
        sname = _safe(n)
        if len(it.args) == 1:
            start, stop = "0", self.ex(it.args[0], "i64")
        else:
            start, stop = self.ex(it.args[0], "i64"), self.ex(it.args[1], "i64")
        self.gap()
        if n not in self.vars:
            # 空区间时 `i` 一次都不被赋 —— 这里给个确定的初值（见文件头 §语义选择 1）
            self.vars[n] = "i64"
            self.out(f"let {sname}: i64 = 0;")
        self.n_tmp += 1
        tmp = f"__t{self.n_tmp}"
        self.out(f"let {tmp}: i64 = {start};")
        self.out(f"while {tmp} < {stop} {{")
        self.depth += 1
        self.out(f"{sname} = {tmp};")
        self.stmts(st.body)
        self.out(f"{tmp} = {tmp} + 1;")
        self.depth -= 1
        self.out("}")

    # ---- 整个函数
    def emit_fn(self, node: ast.FunctionDef) -> str:
        self.vars = {}
        self.hoisted = set()
        self.lines = []
        self.depth = 0
        self.n_tmp = 0
        self.ret = "()"
        if node.decorator_list:
            raise Unsupported(f"第 {node.lineno} 行: 不支持装饰器")
        if node.args.vararg or node.args.kwarg or node.args.posonlyargs:
            raise Unsupported(f"第 {node.lineno} 行: 不支持变参/位置参数")
        if node.args.kwonlyargs:
            raise Unsupported(f"第 {node.lineno} 行: 不支持 keyword-only 参数")
        if node.args.defaults or node.args.kw_defaults:
            raise Unsupported(f"第 {node.lineno} 行: 不支持默认参数"
                              f"（Loment 没有重载，写两个函数名）")
        ps = []
        for a in node.args.args:
            t = _ty_of_annotation(a.annotation, f"第 {node.lineno} 行 参数 {a.arg}")
            self.vars[a.arg] = t
            ps.append(f"{_safe(a.arg)}: {t}")
        self.ret = _ty_of_annotation(node.returns, f"第 {node.lineno} 行 返回类型")

        # **文档字符串 -> Loment 的 `///` 文档注释**（`Main.lomt` 那种写法）。
        # 不是丢掉它 —— Python 的 docstring 是 `__doc__`，而 Loment 有它自己的文档注释
        # 位置，转过去信息不丢。**先摘掉再预扫** —— 摘下来的那句不在 `body` 里，
        # 所以 `_scan` 不会把它当成"一条语句"。
        body = list(node.body)
        doc_lines: list[str] = []
        if body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            doc_lines = ["/// " + ln for ln in
                         body[0].value.value.strip().splitlines()]
            body = body[1:]

        # 作用域预扫：最外层赋值在嵌套块里的名字要上提到函数头（见文件头 §语义选择）
        first: dict = {}
        self._scan(body, 0, first)
        self.hoisted = {n for n, (d, _st) in first.items() if d > 0 and n not in self.vars}

        self.lines += [ln.rstrip() for ln in doc_lines]
        head = f"pub fn {node.name}({', '.join(ps)})"
        if self.ret != "()":
            head += f" -> {self.ret}"
        self.lines.append(head + " {")
        # 上提的声明：**一个一行、带类型**。零初值是刻意的（见文件头 §语义选择 1）。
        #
        # **类型要真推**：`x = (a < b)` 上提的应该是 `bool`，照发 `let x: i64 = 0;`
        # 就把类型改掉了（而且后面的 `x = (a < b);` 会变成 bool 赋给 i64，编不过）。
        # 推不出来（右值里有别的新名字）就落到 `i64` —— 那是最常见的一档。
        hoist_ty: dict[str, str] = {}
        for n in self.hoisted:
            try:
                ty = self._decl_type(first[n][1])
            except (Unsupported, PyError, AssertionError):
                ty = "i64"
            if ty == "()":
                ty = "i64"
            hoist_ty[n] = ty
            self.vars[n] = ty
        for n in sorted(self.hoisted):
            zero = "false" if hoist_ty[n] == "bool" else "0"
            self.out(f"let {_safe(n)}: {hoist_ty[n]} = {zero};")
        if self.hoisted:
            self.lines.append("")
        self.stmts(body)
        self.lines.append("}")
        return "\n".join(self.lines)


def _stmt_name(st) -> str:
    return {"Break": "Loment 没有 `break`；改写条件",
            "Continue": "Loment 没有 `continue`；用一个 did 标志改写",
            "Try": "没有异常", "With": "没有上下文管理器",
            "Import": "没有模块", "ImportFrom": "没有模块",
            "Global": "没有全局变量", "Nonlocal": "没有嵌套函数",
            "Delete": "没有 `del`",
            "Raise": "没有异常",
            "Assert": "没有 `assert`（写成 `if not c { return …; }`）",
            "FunctionDef": "不支持嵌套函数",
            "ClassDef": "不支持类",
            "Match": "没有 `match`（写成 if/elif 链）",
            }.get(type(st).__name__, "")


def _expr_name(e) -> str:
    return {"List": "没有列表", "Dict": "没有字典", "Set": "没有集合",
            "Tuple": "没有元组", "Subscript": "没有下标（先做标量）",
            "Attribute": "没有属性访问", "JoinedStr": "没有 f-string",
            "IfExp": "没有三元（写成 if/else）",
            "Await": "没有异步", "Yield": "没有生成器",
            }.get(type(e).__name__, "")


# ---------------------------------------------------------------- 辅助函数

#: `//` 与 `%` 的适配层（`docs/187` §为什么）。**只在用到时发** —— 确定性，没有死代码。
#:
#: 恒等式（`b != 0`）：`py_mod = ((a % b) + b) % b`，`py_floordiv = (a - py_mod)/b`。
#: `loment/pytrans/floordiv.py` 那份语料的期望值就是照这个推的，四个符号组合都验过。
_HELPERS = {
    _PY_MOD: """/// Python 的 `a % b`（**向下取整**）—— Loment 的 `%` 是向零截断的
/// （实测 `-7 % 2` 在本语言里是 `-1`，Python 是 `1`）。两边符号相同时它俩一样，
/// 所以这个包装只在**符号不同**时才起作用。
fn __py_mod(a: i64, b: i64) -> i64 {
    let m: i64 = a % b;
    if m != 0 && ((m < 0) != (b < 0)) {
        return m + b;
    }
    return m;
}
""",
    _PY_FLOORDIV: """/// Python 的 `a // b`（**向下取整**）。用 `__py_mod` 把余数拉到 Python 那一侧，
/// 再用本语言向零截断的 `/` 除 —— 减掉余数之后正好整除，截断与下取整结果相同。
fn __py_floordiv(a: i64, b: i64) -> i64 {
    return (a - __py_mod(a, b)) / b;
}
""",
}


def _emit_helpers(needed: set[str], user_names: set[str]) -> list[str]:
    """按需发辅助函数。**撞名就报错** —— 静默改语义是最坏的一种。"""
    out = []
    for h in (_PY_MOD, _PY_FLOORDIV):
        if h not in needed:
            continue
        if h in user_names:
            raise Unsupported(f"辅助函数名 `{h}` 与单元里的函数撞名了 —— "
                              f"改掉那个函数名（辅助函数是生成物，不该被用户占用）")
        out.append(_HELPERS[h].rstrip("\n"))
        if h == _PY_FLOORDIV:
            needed.add(_PY_MOD)   # `__py_floordiv` 要调它 —— 但顺序上它在前，已在表里
    return out


# ---------------------------------------------------------------- 入口


def parse(src: str) -> tuple[list, list, str]:
    """`(函数, 常量, 模块文档字符串)`。

    常量是 `NAME = <整数>` 并且名字全大写（与 `potato_from` 同一条判据）。
    模块文档字符串**收下**并原样带走 —— 它转成 `//` 注释发出去，不是丢掉。
    """
    tree = ast.parse(src)
    fns, consts = [], []
    doc = ""
    body = list(tree.body)
    if body and isinstance(body[0], ast.Expr) \
            and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        doc = body[0].value.value
        body = body[1:]
    for node in body:
        if isinstance(node, ast.FunctionDef):
            fns.append(node)
        elif isinstance(node, ast.AsyncFunctionDef):
            raise Unsupported(f"第 {node.lineno} 行: 不支持 async 函数")
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id.isupper():
            c = node.value
            if isinstance(c, ast.Constant) and isinstance(c.value, int) \
                    and not isinstance(c.value, bool):
                consts.append((node.targets[0].id, c.value))
            elif isinstance(c, ast.UnaryOp) and isinstance(c.op, ast.USub) \
                    and isinstance(c.operand, ast.Constant) \
                    and isinstance(c.operand.value, int):
                consts.append((node.targets[0].id, -c.operand.value))
            else:
                raise Unsupported(f"第 {node.lineno} 行: 常量 `{node.targets[0].id}` "
                                  f"的值不是整数字面量")
        else:
            raise Unsupported(f"第 {node.lineno} 行: 模块级只收 `def` 与全大写常量，"
                              f"得到 `{type(node).__name__}`")
    return fns, consts, doc


def translate(src: str, keep: set[str] | None = None,
              consts: dict[str, str] | None = None) -> str:
    """Python 源码 -> Loment 源码（**只有常量、辅助函数与函数**，`module` 头由调用方加）。

    `keep` 给了就只翻这些函数；`consts` 是**调用方那边已知的模块常量**（名字 -> 类型）
    —— `lomt_from --impl` 走这条路时，正文里只有函数（常量在 Potato 的 `consts` 里，
    由它自己发 `pub const`），不给这张表的话函数体里一引用常量就会报"没赋过值"。
    """
    fns, mod_consts, doc = parse(src)
    sig: dict[str, tuple[list[str], str]] = {}
    for f in fns:
        args = [_ty_of_annotation(a.annotation, f"第 {f.lineno} 行 参数 {a.arg}")
                for a in f.args.args]
        sig[f.name] = (args, _ty_of_annotation(f.returns, f"第 {f.lineno} 行 返回类型"))
    seen: set[str] = set()
    for f in fns:
        if f.name in seen:
            raise Unsupported(f"第 {f.lineno} 行: 函数 `{f.name}` 重名"
                              f"（Loment 没有重载，名字必须精确）")
        seen.add(f.name)

    cenv = {n: "i64" for n, _v in mod_consts}
    cenv.update(consts or {})       # 调用方那侧已知的常量（见 docstring）
    body_parts, needed = [], set()
    for f in fns:
        if keep is not None and f.name not in keep:
            continue
        em = Emitter(sig, cenv)
        body_parts.append(em.emit_fn(f))
        needed |= em.helpers

    out: list[str] = []
    if doc:
        # 模块文档字符串 -> `//` 注释（**带走，不是丢掉**）
        out.append("\n".join("// " + ln for ln in doc.strip().splitlines()))
    if mod_consts:
        out.append("// ---- 模块常量")
        out += [f"pub const {n}: i64 = {v};" for n, v in mod_consts]
    helpers = _emit_helpers(needed, seen)
    if helpers:
        out.append("")
        out.append("// ---- `//` `%` 的适配层（Python 是向下取整，本语言的 `/` `%` 是向零截断）")
        out += helpers
    if body_parts:
        out.append("")
        out += body_parts
    return "\n\n".join(x for x in out if x.strip()) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pytrans", description="Python 子集 -> Loment")
    ap.add_argument("path")
    ap.add_argument("--out", metavar="PATH")
    a = ap.parse_args(argv)
    try:
        text = translate(Path(a.path).read_text(encoding="utf-8"))
    except Unsupported as e:
        print(f"[ERR] 子集外: {e}", file=sys.stderr)
        return 1
    except (PyError, SyntaxError) as e:
        print(f"[ERR] 解析失败: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"[ERR] 读取失败: {e}", file=sys.stderr)
        return 2
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8", newline="\n")
        print(f"[OK] {a.path} -> {a.out}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
