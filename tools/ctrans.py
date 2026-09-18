#!/usr/bin/env python3
"""ctrans.py — **C 子集 -> Loment** 翻译器（`docs/186` Stage A）。

    C 源码 --ctrans--> Loment 源码

## 它在这条链上的位置

    任意源语法 --potato_from--> Potato (带 functions[i].body) --lomt_from --impl--> .lomt
                                                                   ^
                                                            本模块在这里干活

`potato_from` 只记"有什么"（签名），正文**原样**存进 `functions[i].body`；翻它是这一步。
`lomt_from` 认这个字段，把带正文的函数发成真的 `pub fn … { … }`，其余照旧发 `pub extern fn`。

## 为什么正文要**原样**过一手 Potato（而不是在 `potato_from` 里就翻掉）

`docs/179` §2 把"表示层记『有什么』、不记『怎么算』"这条界线划得很清楚，但那一条说的是
**别把实现藏进表示层**。把**源语言的原文**原样带过去不是藏 —— 它没有一个比特的解释，
读的人拿到的就是那份 C。**谁去翻它是下游的事**，于是同一个对象可以被翻成 Loment、
也可以被翻成别的，而对象本身不变。

## 子集 —— 以及**不支持的怎么处理**

支持的（Stage A，就这些）：

    类型      int -> i32   unsigned/unsigned int -> u32   long -> i64   void -> ()
    函数      <类型> <名>(<类型> <名>, …) { … }
    语句      int x = e;   int x;   x = e;   return e;   return;
              if (e) S [else S]   while (e) S   for (init; cond; step) S   e;
    表达式    整数（十/十六进制）  标识符  调用
              一元 - ! ~      二元 || && | ^ & == != < > <= >= << >> + - * / %

**不支持的一律报错退出，不静默丢**（`docs/167` 那条纪律）。跳过一个函数在这里**不安全**：
`lomt_from --impl` 发出的是**真实现**，少一个就是"这个程序少算了一步"，而它照样能编过。

## 这一节最要紧的东西：C 的 `int` 与 Loment 的 `bool` 不是一回事

C 里 `&&` `||` `!` `<` `>` `==` 这些**出的是 `int`**（0/1），而且任何整数都能当条件用
（`if (x)`）；Loment 是 Rust 的严格子集，`&&`/`||`/`!` **出 `bool`**、比较**出 `bool`**，
而条件**只收 `bool`**。于是两个方向都要补：

| 场合 | C | Loment |
|---|---|---|
| `int x = a && b;` | 直接存 | `let x: i32 = ((a != 0) && (b != 0)) as i32;` |
| `if (x)`（x 是 int） | 合法 | `if x != 0 {` |
| `!x`（x 是 int） | 按 int 取反 | `x == 0` |

所以本模块不是"改几个关键字"，而是**带方向的翻译**：`_ex(node, want)` 里 `want` 是
`"int"` 还是 `"bool"`，两个方向各有一条转换。

**一处刻意的行为差异**：C 的 `&&`/`||` 是**短路**的。上面的改法保住了短路 ——
`&&` 在 Loment 里也短路，而两侧各自是纯表达式求值，没有可见的副作用差异。
这一点是对的不是碰巧。

## 两条与 C 不同的**语义**选择（写下来，因为它们不是翻译，是决定）

1. **`int x;`（只声明不给值）补零值。** C 读未初始化变量是未定义行为；Loment 要的是
   确定的程序。零值是最接近"什么都没发生"的确定选择。**不是**想把 C 的 UB 变成 0。
2. **`for (int i = …)` 的变量改名外提。** Loment **没有裸块**（实测
   `15:5 期望表达式，得到 '{'`），所以 C 的 for 作用域没法用一层 `{ }` 圈起来。
   改名是安全的：C 的 for 作用域本来就在循环结束处结束，外面看不到那个 `i`。
3. **`static` / `inline` 收下并丢掉。** 丢掉是**保义**的：`static` 是内部链接、
   `inline` 是内联建议，而这里翻的是**整个单元的全部函数** —— 没有第二个翻译单元能再
   定义同名函数，所以"内部链接"在这个语境里没有可分辨的差别。其余说明符
   （`const` `extern` `volatile` `register` `typedef`）**拒绝**，它们都真的改语义。

用法:

    python tools/ctrans.py SRC.c [--out OUT.lomt]
退出码: 0 = 成功 / 1 = 用到子集外的东西 / 2 = 用法或读取错误。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

#: C 类型 -> Loment 类型。**只有这几个** —— 其余报错，不猜。
_TYPES = {
    "int": "i32",
    "unsigned": "u32",
    "unsigned int": "u32",
    "signed": "i32",
    "signed int": "i32",
    "long": "i64",
    "long int": "i64",
    "unsigned long": "u64",
    "unsigned long int": "u64",
    "void": "()",
}

#: C 的二元运算符 -> (Loment 运算符, **操作数按什么算**, 结果是不是 bool)。
#: 操作数 `"bool"` 说的是 C 这边把两侧当条件看（`&&` `||`），于是操作数要转成 bool。
#: 结果 `True` 说的是"这个表达式在 C 里其实出 int，进整数场合要补 `as i32`"。
_BIN = {
    "*": ("*", "int", False), "/": ("/", "int", False), "%": ("%", "int", False),
    "+": ("+", "int", False), "-": ("-", "int", False),
    "<<": ("<<", "int", False), ">>": (">>", "int", False),
    "<": ("<", "int", True), ">": (">", "int", True),
    "<=": ("<=", "int", True), ">=": (">=", "int", True),
    "==": ("==", "int", True), "!=": ("!=", "int", True),
    "&": ("&", "int", False), "^": ("^", "int", False), "|": ("|", "int", False),
    "&&": ("&&", "bool", True), "||": ("||", "bool", True),
}

#: 优先级表，**低到高**。C 的那张表，只留子集里有的。`_prec` 返回 `下标 + 1`。
_PREC = [
    ["||"], ["&&"], ["|"], ["^"], ["&"], ["==", "!="],
    ["<", ">", "<=", ">="], ["<<", ">>"], ["+", "-"], ["*", "/", "%"],
]
_PRECOF = {op: i + 1 for i, lvl in enumerate(_PREC) for op in lvl}

_KW = {"if", "else", "while", "for", "return", "break", "continue", "do", "switch",
       "case", "default", "goto", "sizeof"}

#: 类型词（能拼进 `_TYPES` 的那些）
_TYPE_WORDS = {"int", "unsigned", "signed", "long", "void"}
#: 能出现在"类型位置"但本子集一律拒绝的。**必须认得它们才能给出对的报错** ——
#: 不认的话 `static int f()` 会报"期望类型名"，而真正的原因是"不支持存储类"。
_SPEC_WORDS = _TYPE_WORDS | {"static", "const", "extern", "register", "volatile",
                             "typedef", "struct", "enum", "union", "char", "short",
                             "float", "double", "inline", "_Bool"}
#: 见文件头 §语义选择 3：`static` / `inline` **收下并丢掉**，其余说明符拒绝。
_LINKAGE = {"static", "inline"}
_BAD_SPEC = {"const", "extern", "register", "volatile", "typedef"}
_AGG = {"struct", "enum", "union"}


class Unsupported(Exception):
    """用到了子集外的东西。**必须报出来** —— 这里静默跳过 = 产物少算一步却照样能编。"""


class CError(Exception):
    """这份 C 解析不过。与 `Unsupported` 分开：一个是"没实现"，一个是"你自己写错了"。"""


# ---------------------------------------------------------------- 分词

_TOKEN = re.compile(r"""
      (?P<ws>\s+)
    | (?P<lc>//[^\n]*)
    | (?P<bc>/\*.*?\*/)
    | (?P<id>[A-Za-z_]\w*)
    | (?P<num>0[xX][0-9a-fA-F]+|\d+)
    | (?P<op><<=|>>=|\+=|-=|\*=|/=|%=|&=|\|=|\^=|\.\.\.|<<|>>|<=|>=|==|!=|&&|\|\||\+\+|--|[-+*/%&|^~!<>=();,{}\[\]?:])
""", re.X | re.S)

#: 本子集不收的运算符：**必须**在分词那一步**整个**认出来再点名拒掉。
#: 漏一个的后果是它被**切成两半**：`+=` 变成 `+` `=`，于是 `a += 1` 报的是
#: "`a` 后面期望 `=` 或 `(`，得到 '+'" —— 指的**不是**真正的原因（用了复合赋值）。
_REJECT_OP = {"<<=", ">>=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^="}


def tokens(src: str) -> list[tuple[str, str, int]]:
    """C 源码 -> `[(种类, 文本, 行)]`。种类 ∈ {id, num, op}。注释与空白丢掉。"""
    out: list[tuple[str, str, int]] = []
    line = 1
    i = 0
    while i < len(src):
        m = _TOKEN.match(src, i)
        if not m:
            raise CError(f"第 {line} 行: 认不出的字符 {src[i]!r}")
        kind = m.lastgroup
        text = m.group()
        if kind in ("ws", "lc", "bc"):
            line += text.count("\n")
            i = m.end()
            continue
        if kind == "op":
            if text == "...":
                raise Unsupported(f"第 {line} 行: 不支持变参 `...`")
            # `++i` / `i++` 都会落到这里（`++` 是单独一个 token）
            if text in ("++", "--"):
                raise Unsupported(f"第 {line} 行: 不支持自增/自减 `{text}`"
                                  f"（写成 `x = x + 1;`）")
            if text in _REJECT_OP:
                raise Unsupported(f"第 {line} 行: 不支持 `{text}`")
        out.append((kind if kind in ("id", "num") else "op", text, line))
        i = m.end()
    return out


# ---------------------------------------------------------------- 语法树


class Lit:
    __slots__ = ("v", "line")

    def __init__(self, v: int, line: int) -> None:
        self.v, self.line = v, line


class Var:
    __slots__ = ("name", "line")

    def __init__(self, name: str, line: int) -> None:
        self.name, self.line = name, line


class Call:
    __slots__ = ("name", "args", "line")

    def __init__(self, name: str, args: list, line: int) -> None:
        self.name, self.args, self.line = name, args, line


class Un:
    __slots__ = ("op", "e", "line")

    def __init__(self, op: str, e, line: int) -> None:
        self.op, self.e, self.line = op, e, line


class Bin:
    __slots__ = ("op", "l", "r", "line")

    def __init__(self, op: str, l, r, line: int) -> None:
        self.op, self.l, self.r, self.line = op, l, r, line


class Decl:
    __slots__ = ("ty", "name", "init", "line")

    def __init__(self, ty: str, name: str, init, line: int) -> None:
        self.ty, self.name, self.init, self.line = ty, name, init, line


class Assign:
    __slots__ = ("name", "e", "line")

    def __init__(self, name: str, e, line: int) -> None:
        self.name, self.e, self.line = name, e, line


class Return:
    __slots__ = ("e", "line")

    def __init__(self, e, line: int) -> None:
        self.e, self.line = e, line


class If:
    __slots__ = ("cond", "then", "els", "line")

    def __init__(self, cond, then: list, els, line: int) -> None:
        self.cond, self.then, self.els, self.line = cond, then, els, line


class While:
    __slots__ = ("cond", "body", "line")

    def __init__(self, cond, body: list, line: int) -> None:
        self.cond, self.body, self.line = cond, body, line


class For:
    __slots__ = ("init", "cond", "step", "body", "line")

    def __init__(self, init, cond, step, body: list, line: int) -> None:
        self.init, self.cond, self.step, self.body, self.line = init, cond, step, body, line


class ExprStmt:
    __slots__ = ("e", "line")

    def __init__(self, e, line: int) -> None:
        self.e, self.line = e, line


class Fn:
    __slots__ = ("ret", "name", "params", "body", "line")

    def __init__(self, ret: str, name: str, params: list, body: list, line: int) -> None:
        self.ret, self.name, self.params, self.body, self.line = ret, name, params, body, line


# ---------------------------------------------------------------- 解析


class Parser:
    """C 子集的递归下降。**只有这一层** —— 不做预处理、不做类型检查。"""

    def __init__(self, toks: list[tuple[str, str, int]]) -> None:
        self.t = toks
        self.i = 0

    def peek(self, k: int = 0) -> tuple[str, str, int]:
        j = self.i + k
        if j < len(self.t):
            return self.t[j]
        return ("eof", "", self.t[-1][2] if self.t else 1)

    def at(self, text: str, k: int = 0) -> bool:
        return self.peek(k)[1] == text

    def want(self, text: str) -> tuple[str, str, int]:
        t = self.peek()
        if t[1] != text:
            raise CError(f"第 {t[2]} 行: 期望 `{text}`，得到 {t[1] or '<结束>'!r}")
        self.i += 1
        return t

    def ident(self) -> tuple[str, int]:
        t = self.peek()
        if t[0] != "id":
            raise CError(f"第 {t[2]} 行: 期望标识符，得到 {t[1] or '<结束>'!r}")
        self.i += 1
        return t[1], t[2]

    # ---- 声明说明符
    def spec(self) -> tuple[str, str, int]:
        """吃 `[unsigned] int` / `void` / … -> `(Loment 类型, 变量名, 行)`。"""
        words: list[str] = []
        line = self.peek()[2]
        while self.peek()[0] == "id" and self.peek()[1] in _SPEC_WORDS:
            w = self.peek()[1]
            if w in _BAD_SPEC:
                raise Unsupported(f"第 {self.peek()[2]} 行: 不支持存储类/限定符 `{w}`")
            if w in _LINKAGE:
                # 收下并丢掉。**丢掉是保义的**：`static` 是内部链接、`inline` 是内联建议，
                # 而这里翻的是**整个单元的全部函数** —— 没有第二个翻译单元能再定义同名函数，
                # 于是"内部链接"在这个语境里没有可分辨的差别。见文件头 §语义选择 3。
                self.i += 1
                continue
            if w in _AGG:
                raise Unsupported(f"第 {self.peek()[2]} 行: 不支持聚合/枚举类型 `{w}`"
                                  f"（Stage A 只做整数标量）")
            words.append(w)
            self.i += 1
        if not words:
            t = self.peek()
            raise CError(f"第 {t[2]} 行: 期望类型名，得到 {t[1] or '<结束>'!r}")
        raw = re.sub(r"\s+", " ", " ".join(words)).strip()
        if raw not in _TYPES:
            raise Unsupported(f"第 {line} 行: 类型 `{raw}` 不在 Stage A 子集里")
        name, _ = self.ident()
        # **不在这里判 `(`** —— 函数**定义**的 `(` 就紧跟名字，那是正常形状。
        # 函数指针（`int (*fp)(int)`）会因为 `(` 出现在要标识符的位置被 `ident()` 拒掉。
        if self.at("*") or self.at("["):
            raise Unsupported(f"第 {line} 行: 不支持指针/数组声明 `{name}`")
        return _TYPES[raw], name, line

    # ---- 表达式
    def expr(self) -> object:
        return self.binary(1)

    def binary(self, minp: int) -> object:
        left = self.unary()
        while True:
            t = self.peek()
            # **只有这些**。别把 `<<` 也算进来 —— 它是正经的二元运算符；
            # `<<=` 那种复合赋值在 `tokens()` 那一步就已经被点名拒掉了。
            if t[0] == "op" and t[1] in ("?", "=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^="):
                # **点名**这些，别让它们掉进"期望 `;`"那种指不到点子的报错里。
                # 赋值表达式（`a = (b = c)`）与三元都是 C 的表达式，而 Loment 的 `=` 是语句。
                raise Unsupported(f"第 {t[2]} 行: 不支持表达式里的 `{t[1]}`"
                                  + ("（三元 `?:` 改写成 if/else；赋值改写成单独一条语句）"
                                     if t[1] == "?" else
                                     "（Loment 的赋值是语句，不是表达式）"))
            if t[0] != "op" or t[1] not in _BIN:
                return left
            p = _PRECOF[t[1]]
            if p < minp:
                return left
            self.i += 1
            right = self.binary(p + 1)  # 左结合
            left = Bin(t[1], left, right, t[2])

    def unary(self) -> object:
        t = self.peek()
        if t[0] == "op" and t[1] in ("-", "!", "~"):
            self.i += 1
            return Un(t[1], self.unary(), t[2])
        if t[0] == "op" and t[1] in ("+", "*", "&"):
            raise Unsupported(f"第 {t[2]} 行: 不支持一元 `{t[1]}`"
                              f"（取地址/解引用不在 Stage A 子集里）")
        return self.primary()

    def primary(self) -> object:
        t = self.peek()
        if t[1] == "(":
            self.i += 1
            e = self.expr()
            self.want(")")
            return e
        if t[0] == "num":
            self.i += 1
            return Lit(int(t[1], 0), t[2])
        if t[0] == "id":
            if t[1] in _KW:
                raise Unsupported(f"第 {t[2]} 行: 表达式里不支持关键字 `{t[1]}`")
            self.i += 1
            if self.at("("):
                self.i += 1
                args = []
                if not self.at(")"):
                    args.append(self.expr())
                    while self.at(","):
                        self.i += 1
                        args.append(self.expr())
                self.want(")")
                return Call(t[1], args, t[2])
            return Var(t[1], t[2])
        raise CError(f"第 {t[2]} 行: 期望表达式，得到 {t[1] or '<结束>'!r}")

    # ---- 语句
    def stmt(self) -> object:
        t = self.peek()
        if t[1] == "{":
            raise Unsupported(f"第 {t[2]} 行: 不支持裸块 `{{ }}`"
                              f"（Loment 没有块语句；把里面的声明写到外层）")
        if t[1] == "if":
            self.i += 1
            self.want("(")
            cond = self.expr()
            self.want(")")
            then = self.block_or_stmt()
            els = None
            if self.at("else"):
                self.i += 1
                els = self.block_or_stmt()
            return If(cond, then, els, t[2])
        if t[1] == "while":
            self.i += 1
            self.want("(")
            cond = self.expr()
            self.want(")")
            return While(cond, self.block_or_stmt(), t[2])
        if t[1] == "for":
            return self.for_()
        if t[1] in ("do", "switch", "goto", "case", "default"):
            raise Unsupported(f"第 {t[2]} 行: 不支持 `{t[1]}`")
        if t[1] == "break":
            raise Unsupported(f"第 {t[2]} 行: 不支持 `break`"
                              f"（Loment 没有；改写条件或提前 return）")
        if t[1] == "continue":
            raise Unsupported(f"第 {t[2]} 行: 不支持 `continue`"
                              f"（Loment 没有；用一个 did 标志改写）")
        if t[1] == "return":
            self.i += 1
            if self.at(";"):
                self.i += 1
                return Return(None, t[2])
            e = self.expr()
            self.want(";")
            return Return(e, t[2])
        return self.simple()

    def block_or_stmt(self) -> list:
        """`{ … }` 或单条语句 —— 两种都返回**语句列表**。"""
        if self.at("{"):
            return self.block()
        return [self.stmt()]

    def block(self) -> list:
        self.want("{")
        out = []
        while not self.at("}"):
            if self.peek()[0] == "eof":
                raise CError(f"第 {self.peek()[2]} 行: 块没闭合")
            out.append(self.stmt())
        self.want("}")
        return out

    def simple(self, semi: bool = True) -> object:
        """`int x [= e];` / `x = e;` / `f(...);`

        `semi=False` 给 `for` 的**步进**用 —— 它后面是 `)` 不是 `;`。
        """
        t = self.peek()
        if t[0] == "id" and t[1] in _SPEC_WORDS:
            ty, name, line = self.spec()
            if self.at(";"):
                self.i += 1
                return Decl(ty, name, None, line)
            self.want("=")
            e = self.expr()
            if semi:
                self.want(";")
            return Decl(ty, name, e, line)
        if t[0] != "id":
            raise CError(f"第 {t[2]} 行: 期望语句，得到 {t[1] or '<结束>'!r}")
        if t[1] in _KW:
            raise Unsupported(f"第 {t[2]} 行: 这里不支持 `{t[1]}`")
        name, line = self.ident()
        if self.at("="):
            self.i += 1
            e = self.expr()
            if semi:
                self.want(";")
            return Assign(name, e, line)
        if self.at("("):
            self.i += 1
            args = []
            if not self.at(")"):
                args.append(self.expr())
                while self.at(","):
                    self.i += 1
                    args.append(self.expr())
            self.want(")")
            if semi:
                self.want(";")
            return ExprStmt(Call(name, args, line), line)
        raise CError(f"第 {self.peek()[2]} 行: `{name}` 后面期望 `=` 或 `(`，"
                     f"得到 {self.peek()[1]!r}")

    def for_(self) -> object:
        t = self.peek()
        self.i += 1
        self.want("(")
        init = None
        if self.at(";"):
            self.i += 1
        else:
            init = self.simple()
        cond = None
        if not self.at(";"):
            cond = self.expr()
        self.want(";")
        step = None
        if not self.at(")"):
            step = self.simple(semi=False)
        self.want(")")
        return For(init, cond, step, self.block_or_stmt(), t[2])

    # ---- 顶层
    def unit(self) -> list:
        out = []
        while self.peek()[0] != "eof":
            if self.at("#"):
                raise Unsupported(f"第 {self.peek()[2]} 行: 不支持预处理指令")
            ret, name, line = self.spec()
            if not self.at("("):
                raise Unsupported(f"第 {line} 行: 不支持全局变量 `{name}`")
            self.i += 1
            params = []
            if not self.at(")"):
                if self.at("void") and self.at(")", 1):
                    self.i += 1
                else:
                    params.append(self.spec())
                    while self.at(","):
                        self.i += 1
                        params.append(self.spec())
            self.want(")")
            if self.at(";"):
                raise Unsupported(f"第 {line} 行: 函数 `{name}` 只有声明没有体"
                                  f"（Stage A 翻的是有体的函数）")
            out.append(Fn(ret, name, params, self.block(), line))
        return out


# ---------------------------------------------------------------- 发射

#: Loment 的保留字 —— 生成的标识符不能撞上。
_LOMENT_KW = {
    "module", "use", "fn", "let", "if", "else", "while", "return", "pub", "extern",
    "const", "struct", "enum", "capability", "guard", "excluded", "revocable",
    "trait", "impl", "match", "as", "true", "false", "mut", "addin", "choose",
    "command", "foruse", "comefor", "byuse", "std", "dispatch", "self",
}


def _safe(name: str) -> str:
    return name + "_c" if name in _LOMENT_KW else name


class Emitter:
    """C 语法树 -> Loment 源码（一个函数一份 Emitter）。"""

    def __init__(self, fns: dict[str, str]) -> None:
        #: 本单元**所有**函数的返回类型。调用点的类型靠它 —— 子集里没有跨单元调用。
        self.fns = fns
        #: C 名字 -> 发出去的名字。`for` 的改名外提就靠这张表（见文件头 §语义选择 2）。
        self.vars: dict[str, str] = {}
        self.n = 0
        self.lines: list[str] = []

    def fresh(self, base: str) -> str:
        while True:
            self.n += 1
            out = f"{_safe(base)}__{self.n}"
            if out not in self.vars.values():
                return out

    def out(self, s: str, depth: int) -> None:
        self.lines.append("    " * (depth + 1) + s)

    def gap(self) -> None:
        """控制流前面留一个空行 —— **但别在开头留**（`{` 后面直接一个空行很难看），
        也别连着留两个。"""
        if not self.lines or self.lines[-1] == "" or self.lines[-1].endswith("{"):
            return
        self.lines.append("")

    # ---- 类型：只有 `int` / `bool` / `void` 三档
    def ty_of(self, e: object) -> str:
        if isinstance(e, (Lit, Var)):
            return "int"
        if isinstance(e, Call):
            r = self.fns.get(e.name)
            if r is None:
                raise Unsupported(
                    f"第 {e.line} 行: 调用了本单元没有的函数 `{e.name}`"
                    f"（Stage A 不跨单元 —— 要调外部函数请留着 `pub extern fn` 那条路）")
            return "void" if r == "()" else "int"
        if isinstance(e, Un):
            return "bool" if e.op == "!" else "int"
        if isinstance(e, Bin):
            return "bool" if _BIN[e.op][2] else "int"
        raise AssertionError(type(e))

    def ex(self, e: object, want: str) -> str:
        """表达式，**在 `want` 这个上下文里**的写法（必要时补转换）。"""
        got = self.ty_of(e)
        if got == "void":
            raise Unsupported(f"第 {e.line} 行: 这里用了一个不返回值的调用")
        raw = self.raw(e)
        if got == want:
            return raw
        if got == "int" and want == "bool":
            return f"{raw} != 0"      # C 的 `if (x)`：非零即真
        if got == "bool" and want == "int":
            return f"({raw}) as i32"  # C 的 `int x = (a < b);`：bool -> 0/1
        raise AssertionError((got, want))

    def raw(self, e: object) -> str:
        """表达式在**它自己的类型**下的写法。括号一律加上 —— 别让读者去猜优先级。"""
        if isinstance(e, Lit):
            return str(e.v)
        if isinstance(e, Var):
            if e.name not in self.vars:
                raise Unsupported(f"第 {e.line} 行: 用了没声明过的 `{e.name}`")
            return self.vars[e.name]
        if isinstance(e, Call):
            return f"{e.name}({', '.join(self.ex(a, 'int') for a in e.args)})"
        if isinstance(e, Un):
            if e.op == "!":
                # Loment 的 `!` 只收 bool，而 C 的 `!x` 收 int —— 直接写成 `x == 0`
                return f"({self.ex(e.e, 'int')} == 0)"
            return f"({e.op}{self.ex(e.e, 'int')})"
        if isinstance(e, Bin):
            op, want, _b = _BIN[e.op]
            return f"({self.ex(e.l, want)} {op} {self.ex(e.r, want)})"
        raise AssertionError(type(e))

    # ---- 语句
    def stmts(self, body: list, depth: int) -> None:
        for s in body:
            self.stmt(s, depth)

    def stmt(self, s: object, depth: int) -> None:
        if isinstance(s, Decl):
            if s.ty == "()":
                raise Unsupported(f"第 {s.line} 行: 不能声明 `void` 变量")
            self.vars[s.name] = _safe(s.name)
            if s.init is None:
                # 见文件头 §语义选择 1：C 的未初始化在这里变成确定的零值
                self.out(f"let {_safe(s.name)}: {s.ty} = 0;", depth)
            else:
                self.out(f"let {_safe(s.name)}: {s.ty} = {self.ex(s.init, 'int')};", depth)
            return
        if isinstance(s, Assign):
            if s.name not in self.vars:
                raise Unsupported(f"第 {s.line} 行: 赋值给没声明过的 `{s.name}`")
            self.out(f"{self.vars[s.name]} = {self.ex(s.e, 'int')};", depth)
            return
        if isinstance(s, Return):
            if s.e is None:
                self.out("return;", depth)
            else:
                self.out(f"return {self.ex(s.e, 'int')};", depth)
            return
        if isinstance(s, If):
            self.gap()
            self.out(f"if {self.ex(s.cond, 'bool')} {{", depth)
            self.stmts(s.then, depth + 1)
            if s.els is None:
                self.out("}", depth)
            else:
                self.out("} else {", depth)
                self.stmts(s.els, depth + 1)
                self.out("}", depth)
            return
        if isinstance(s, While):
            self.gap()
            self.out(f"while {self.ex(s.cond, 'bool')} {{", depth)
            self.stmts(s.body, depth + 1)
            self.out("}", depth)
            return
        if isinstance(s, For):
            self.for_(s, depth)
            return
        if isinstance(s, ExprStmt):
            if not isinstance(s.e, Call):
                raise Unsupported(f"第 {s.line} 行: 表达式语句只收函数调用")
            self.out(f"{self.raw(s.e)};", depth)
            return
        raise AssertionError(type(s))

    def for_(self, s: For, depth: int) -> None:
        """`for (init; cond; step) body` -> `init; while cond { body; step; }`。

        **Loment 没有裸块**，所以 `for (int i = …)` 里那个 `i` 不能靠一层 `{ }` 圈住 ——
        只能改名外提（见文件头 §语义选择 2）。改名是安全的：C 的 for 作用域本来就在
        循环结束处结束，外面看不到它。

        初值是**声明**时改名；初值是**赋值**（`for (i = 3; …)`）时原地发出，名字不动 ——
        那种写法用的是外面的变量，本来就该影响外面。
        """
        self.gap()
        saved: tuple[str, str] | None = None
        if isinstance(s.init, Decl):
            if s.init.ty == "()":
                raise Unsupported(f"第 {s.init.line} 行: 不能声明 `void` 变量")
            old = self.vars.get(s.init.name)
            new = self.fresh(s.init.name)
            self.vars[s.init.name] = new
            if s.init.init is None:
                self.out(f"let {new}: {s.init.ty} = 0;", depth)
            else:
                self.out(f"let {new}: {s.init.ty} = {self.ex(s.init.init, 'int')};", depth)
            saved = (s.init.name, old) if old is not None else None
        elif s.init is not None:
            self.stmt(s.init, depth)

        cond = self.ex(s.cond, "bool") if s.cond is not None else "true"
        self.out(f"while {cond} {{", depth)
        self.stmts(s.body, depth + 1)
        if s.step is not None:
            self.stmt(s.step, depth + 1)  # 步进在体**之后**
        self.out("}", depth)

        # 循环结束：那个名字的作用域也结束（C 的 for 语义）。有遮蔽就把外层的还回去。
        if isinstance(s.init, Decl):
            if saved is None:
                del self.vars[s.init.name]
            else:
                self.vars[saved[0]] = saved[1]

    def emit_fn(self, f: Fn) -> str:
        self.vars = {n: _safe(n) for (_t, n, _l) in f.params}
        self.n = 0
        self.lines = []
        ps = ", ".join(f"{_safe(n)}: {t}" for (t, n, _l) in f.params)
        head = f"pub fn {f.name}({ps})"
        if f.ret != "()":
            head += f" -> {f.ret}"
        self.lines.append(head + " {")
        self.stmts(f.body, 0)
        self.lines.append("}")
        return "\n".join(self.lines)

    #: `for` 的初值**与**循环里用了同一个名字时的遮蔽，靠 `vars` 的存/取还原 —
    #: 见 `for_` 结尾（不需要额外的栈）。


def parse(src: str) -> list:
    return Parser(tokens(src)).unit()


def translate(src: str, keep: set[str] | None = None,
              externs: dict[str, str] | None = None) -> str:
    """C 源码 -> Loment 源码（**只有函数**，`module` 头由调用方加）。

    * `keep` 给了就只翻这些函数 —— 一份 C 里可能既有"要翻译成 Loment"的函数，
      也有"留着外部链接（`pub extern fn`）"的函数。
    * `externs` 是**不在这份源码里、但调用点要认的**函数：`名字 -> Loment 返回类型`。
      没有它的话，被翻译的代码一调到 `pub extern fn` 那种函数就会报"本单元没有"。
    """
    fns = parse(src)
    rets = {f.name: f.ret for f in fns}
    if externs:
        rets.update(externs)
    seen: set[str] = set()
    for f in fns:
        if f.name in seen:
            raise Unsupported(f"第 {f.line} 行: 函数 `{f.name}` 重名"
                              f"（Loment 没有重载，名字必须精确）")
        seen.add(f.name)
    out = []
    for f in fns:
        if keep is not None and f.name not in keep:
            continue
        out.append(Emitter(rets).emit_fn(f))
    return "\n\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ctrans", description="C 子集 -> Loment")
    ap.add_argument("path")
    ap.add_argument("--out", metavar="PATH")
    a = ap.parse_args(argv)
    try:
        text = translate(Path(a.path).read_text(encoding="utf-8"))
    except Unsupported as e:
        print(f"[ERR] 子集外: {e}", file=sys.stderr)
        return 1
    except CError as e:
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
