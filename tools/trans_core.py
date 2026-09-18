"""trans_core.py — **花括号族（C / C++ / Java / C#）的共享前端核**（`docs/188` §7.1）。

    源语言写法 --<某门的方言表>--> 本模块 --> Loment 源码

## 为什么是"一份解析器 + 方言表"

这四门的**形状**是一样的：`<类型> <名>(<参数>) { … }`、分号结句、条件带括号、
同一张运算符优先级。**真正不同的只有几样** —— 它们全进 `Dialect`，不进 `if`：

    类型拼法表 · 声明起始词 · 收下丢掉的修饰词 · 出现就拒的修饰词 · 聚合类型词
    运算符表 · 优先级表 · **两个方向的强制转换** · 整型默认宽度 · 保留字避让后缀

四份独立的翻译器 = 四倍重复，而**同一件事写四遍必然漂**。

## 这一格最会咬人的地方：`bool` 与 `int` 的强制转换

| | `&&` 出什么 | `if (x)` 收什么 | 要不要补转换 |
|---|---|---|---|
| **C / C++** | `int` | 标量即可 | **两个方向都要** |
| **Java / C#** | `boolean` | 只要 `boolean` | **一个都不要** |

判据是那条一般的：**源语言的规则能表达出来的就转，表达不出来的就报错**
（`docs/187`）。C 的 `&&` 出 `int`，而 `(bool) as i32` **能**表达那个意思 ⇒ 转；
Java 的 `&&` 出 `boolean`，`int x = (a<b)` 在 Java 里本来就是**类型错** ⇒ 不转、报错。
**同一处的差，两族结论相反，而判据是同一条。**

## 子集（四门共用）

    类型      `Dialect.types` 里那些；其余报错，不猜
    函数      <类型> <名>(<类型> <名>, …) { … }
    语句      int x = e;   int x;   x = e;   return e;   return;
              if (e) S [else S]   while (e) S   for (init; cond; step) S   e;
    表达式    整数（十/十六进制）  标识符  调用
              一元 - ! ~      二元 || && | ^ & == != < > <= >= << >> + - * / %

**不支持的一律报错退出，不静默丢**（`docs/167`）。跳过一个函数在这里**不安全**：
`lomt_from --impl` 发出的是**真实现**，少一个就是"这个程序少算了一步"，而它照样能编过。

## 两条不是翻译、是**决定**的东西

1. **`int x;`（只声明不给值）补零值。** C 读未初始化变量是**未定义行为**；Loment 要的是
   确定的程序。零值是最接近"什么都没发生"的确定选择。**不是**想把 C 的 UB 变成 0。
2. **`for (int i = …)` 的变量改名外提。** Loment **没有裸块**
   （实测 `15:5 期望表达式，得到 '{'`），所以源语言的 for 作用域没法用一层 `{ }` 圈起来。
   改名是安全的：那个作用域本来就在循环结束处结束，外面看不到它。

用法：各门的模块给一张 `Dialect`，然后 `translate(src, D)` / `main(D, "C")`。
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

#: 这一族的**共享**东西 —— 三张表与语言无关（C / C++ / Java / C# 的表达式优先级
#: 是同一张，多字符运算符也是同一批），所以放在这儿而不是方言表里。
#: 优先级表，**低到高**。`precof` 返回 `下标 + 1`。
_PREC = [
    ["||"], ["&&"], ["|"], ["^"], ["&"], ["==", "!="],
    ["<", ">", "<=", ">="], ["<<", ">>"], ["+", "-"], ["*", "/", "%"],
]
_PRECOF = {op: i + 1 for i, lvl in enumerate(_PREC) for op in lvl}

#: 运算符 -> (Loment 运算符, 操作数按什么算, 结果是不是 bool)。
#: 操作数 `"bool"` 说的是这一族把两侧当条件看（`&&` `||`），于是操作数要转成 bool。
#: 结果 `True` 说的是"它在源语言里其实出整数，进整数场合要补转换"。
#: **这一张四门共用** —— 差别不在运算符表，在下面那两个 `coerce_*` 开关上。
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

#: 语句关键字 —— 四门共用（`do` / `goto` / `sizeof` 在 Java/C# 里不是关键字，
#: 但它们出现在那些语言里也多半是写错了，早拒比晚拒好）。
_KW = {"if", "else", "while", "for", "return", "break", "continue", "do", "switch",
       "case", "default", "goto", "sizeof"}

# ---------------------------------------------------------------- 方言表
#
# **一份解析器 + 方言表**（`docs/188` §7.1）。这一族（C / C++ / Java / C#）的**形状**
# 是一样的：`<类型> <名>(<参数>) { … }`、分号结句、条件带括号。真正不同的只有下面
# 这几个字段 —— 所以它们进表，不进 if。


@dataclass(frozen=True)
class Dialect:
    """一种"花括号 + 分号"语言的差异面。**只放真正不同的东西。**"""

    name: str
    #: 类型拼法 -> Loment 类型
    types: dict
    #: 能拼进 `types` 的类型词
    type_words: frozenset
    #: 能出现在"类型位置"但本子集一律拒绝的。**必须认得它们才能给出对的报错** ——
    #: 不认的话 `static int f()` 会报"期望类型名"，而真正的原因是"不支持存储类"。
    spec_words: frozenset
    #: 收下并丢掉的修饰词（见 §语义选择 3 那条判据）
    linkage: frozenset
    #: 出现就拒的修饰词
    bad_spec: frozenset
    #: 聚合/枚举类型词（本子集不收）
    agg: frozenset
    #: 语句关键字（出现在表达式里就拒）
    kw: frozenset
    #: 二元运算符 -> (Loment 运算符, 操作数按什么算, 结果是不是 bool)。
    #: 操作数 `"bool"` = 这一族把两侧当条件看；结果 `True` = 它其实出整数，
    #: 进整数场合要补转换。
    bin: dict
    #: 优先级表，**低到高**。`precof` 返回 `下标 + 1`。
    precof: dict
    #: **两个方向的强制转换**（见 `Emitter.ex`）。这一格是这一族里**最会咬人**的：
    #:   C / C++  —— `&&` 出 `int`，`if (x)` 收标量      ⇒ **两个方向都要**
    #:   Java / C# —— `&&` 出 `boolean`，条件只收 `boolean` ⇒ **一个都不要**
    #: 同一处差，两条判据（"能表达的就转，表达不出来的就报错"）给出相反结论。
    coerce_int_to_bool: bool = True
    coerce_bool_to_int: bool = True
    #: 布尔的整型投影用哪个宽度（`bool -> int` 的 `as <x>`）。**粗粒度的兜底** ——
    #: 真需求是"按上下文的具体整型"，现在传不进来，所以取这一族的默认宽度。
    int_default: str = "i32"
    #: 撞上 Loment 保留字时给名字加的后缀。**每门不同** —— 两门用同一个后缀的话，
    #: 将来把两个单元合起来会莫名其妙撞名。
    safe_suffix: str = "_x"
    #: **哪些修饰词标志着"这是一条常量声明"**。Java 的 `static final`、C# 的 `const`、
    #: C++ 的 `constexpr` —— 它们在顶层出现时**不发成函数也不发成全局变量**，而是
    #: 由 `potato_from` 收进 `consts`（`_JAVA_CONST` 那一条）、由 `lomt_from` 发成
    #: `pub const`。翻译器只要**跳过**那条声明，并且认得那个名字。
    #: **空集 = 这一族不收顶层常量**（C 就是 —— `potato_from` 也不收它的全局量）。
    const_words: frozenset = frozenset()
    #: **这一族特有的源码预处理**（`src -> src`），在分词**之前**跑。
    #:   Java / C# —— 抹掉 `class` 外壳（函数住在类里，而解析器看的是顶层）
    #:   C / C++   —— 没有
    #: **必须保持行号与偏移**（换成等长空白，别抽出来拼一拼）—— 报错里的行号
    #: 就是源里的行号，漂了就指不到点子。
    pre: object = None

    def safe(self, name: str) -> str:
        """撞上 Loment 保留字就加后缀。

        **不做成模块级函数** —— 后缀是这一门的，模块级就没有"门"这个概念了。
        """
        return name + self.safe_suffix if name in _LOMENT_KW else name


class Unsupported(Exception):
    """用到了子集外的东西。**必须报出来** —— 这里静默跳过 = 产物少算一步却照样能编。"""


class CError(Exception):
    """这份 C 解析不过。与 `Unsupported` 分开：一个是"没实现"，一个是"你自己写错了"。"""


# ---------------------------------------------------------------- 外壳剥离
#
# Java 与 C# 的函数**不住在顶层**：它们在 `class X { … }` 里，C# 还多一层
# `namespace N { … }`。而共享 parser 只认顶层的 `<类型> <名>(…) { … }` —— 所以分词
# **之前**先把那两层外壳抹掉。
#
# **抹法是"换成等长空白"**（与 `potato_from._blank_keep_off` 同一个手法），不是把成员
# 抽出来拼一拼：长度、行号、偏移**一字不变**，于是报错里的行号**就是源里的行号**。
# 抽出来重排的话行号会漂，而"报错行号指到别处"正是 `docs/179` §6.5 记过的那种
# 把人引向错方向的东西。


def blank_keep_off(m: "re.Match[str]") -> str:
    """换成**等长**空白，且**保留换行** —— 行号与偏移都不动。"""
    return "".join("\n" if ch == "\n" else " " for ch in m.group())


def match_brace(text: str, open_idx: int) -> int:
    """`{` 的下标 -> 配对 `}` 的下标；数不配平返回 -1。

    调用方**必须先把注释与字符串字面量抹掉** —— 否则 `/* } */` 或 `"}"` 会把配对算错。
    """
    d = 0
    for i in range(open_idx, len(text)):
        if text[i] == "{":
            d += 1
        elif text[i] == "}":
            d -= 1
            if d == 0:
                return i
    return -1


#: 扫外壳之前要抹成等长空白的东西：注释**与字符串字面量**。
#: 只抹注释是不够的 —— `String s = "class X {";` 会让扫描把那行当成一个 `class` 头，
#: 于是外壳从字符串里开始切，切出一份**错得看不出来**的源。（`jtrans` 原先只抹了注释。）
_SHELL_NOISE = re.compile(r"//[^\n]*|/\*.*?\*/|\"(?:[^\"\\\n]|\\.)*\"", re.S)

def strip_shells(src: str, heads: "list", line0: int = 1) -> str:
    """抹掉 `heads` 点名的外壳，壳里的成员就成了顶层。**不动行号。**

    `heads` 每项是 `(名字, 正则, 透明, 有体)`：

    * **透明**（`namespace`）—— 壳里还会有壳，抹完**继续进去**找（命名空间能套）
    * **不透明**（`class`）—— 壳里就是成员，**不再进去**：嵌套类是另一回事，留给方言表
      的 `agg` 去拒（`docs/188` §7.1 的 Java 就是这么定的）
    * **有体 = False** —— 这一条没有身体（C# 10 的文件级 `namespace Foo;`），
      正则负责一路匹配到 `;`，抹成空白就行

    正则应把**尾部那个 `{` 也吃进去**（`[^{;]*\\{` 这种），于是 `{` 的下标就是
    `m.end() - 1` —— 顺带把"头与 `{` 之间夹了个 `;`"的怪写法排除掉了。
    """
    sniff = _SHELL_NOISE.sub(blank_keep_off, src)
    out = list(src)

    def walk(lo: int, hi: int) -> None:
        i = lo
        while i < hi:
            for what, rx, transparent, braced in heads:
                m = rx.match(sniff, i)
                if not m:
                    continue
                if braced:
                    j = m.end() - 1                      # 尾巴那个 `{`
                    k = match_brace(sniff, j)
                    if k < 0 or k >= hi:
                        raise CError(f"第 {line0 + src[:m.start()].count(chr(10))} 行: "
                                     f"`{what}` 的花括号不配平")
                    if transparent:
                        walk(j + 1, k)
                else:
                    k = m.end() - 1                      # 尾巴那个 `;`
                for p in range(m.start(), (m.end() if not braced else j + 1)):
                    if out[p] != "\n":
                        out[p] = " "
                if braced and out[k] != "\n":
                    out[k] = " "
                i = k + 1
                break
            else:
                i += 1

    walk(0, len(sniff))
    return "".join(out)


# ---------------------------------------------------------------- 分词

_TOKEN = re.compile(r"""
      (?P<ws>\s+)
    | (?P<lc>//[^\n]*)
    | (?P<bc>/\*.*?\*/)
    | (?P<id>[A-Za-z_]\w*)
    | (?P<num>0[xX][0-9a-fA-F]+|\d+)
    | (?P<op>>>=|>>>|<<=|>>=|\+=|-=|\*=|/=|%=|&=|\|=|\^=|\.\.\.|<<|>>|<=|>=|==|!=|&&|\|\||\+\+|--|[-+*/%&|^~!<>=();,{}\[\]?:])
""", re.X | re.S)

#: 本子集不收的运算符：**必须**在分词那一步**整个**认出来再点名拒掉。
#: 漏一个的后果是它被**切成两半**：`+=` 变成 `+` `=`，于是 `a += 1` 报的是
#: "`a` 后面期望 `=` 或 `(`，得到 '+'" —— 指的**不是**真正的原因（用了复合赋值）。
_REJECT_OP = {"<<=", ">>=", ">>>=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^="}

#: **点名拒掉**、理由要单独说的运算符。`>>>` 是 Java 的逻辑右移 —— 本语言的 `>>`
#: 是**算术**的（实测 `-8 >> 1 == -4`），所以 `>>>` 不能直接映过去。要么 `(a as u32) >> n`，
#: 要么不收；现在**不收**（那是个能表达出来的差，但先别急着补 —— 没语料逼它）。
_REJECT_NAMED = {"...": "变参 `...`", "++": "自增/自减", "--": "自增/自减",
                  ">>>": "逻辑右移 `>>>`（本语言的 `>>` 是算术的；要它得写 `(a as u32) >> n`）"}


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
            if text in _REJECT_NAMED:
                raise Unsupported(f"第 {line} 行: 不支持{_REJECT_NAMED[text]}`{text}`")
            if text in _REJECT_OP:
                raise Unsupported(f"第 {line} 行: 不支持 `{text}`"
                                  f"（写成 `x = x + 1;` 那种单独一条语句）")
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
    """花括号族的递归下降。**只有这一层** —— 不做预处理、不做类型检查。

    语言差异全部走 `self.d`（方言表），这里一行 `if <语言>` 都不该有。
    """

    def __init__(self, toks: list[tuple[str, str, int]], d: "Dialect") -> None:
        self.t = toks
        self.i = 0
        self.d = d

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
        """吃 `[unsigned] int` / `void` / … -> `(Loment 类型, 变量名, 行)`。

        **顺手把"这一条声明里出现过哪些词"记在 `self.last_spec` 上** ——
        `unit()` 要靠它判"这是不是一条常量声明"（见 `Dialect.const_words`）。
        """
        words: list[str] = []
        seen: set[str] = set()
        self.last_spec = seen
        line = self.peek()[2]
        while self.peek()[0] == "id" and self.peek()[1] in self.d.spec_words:
            w = self.peek()[1]
            seen.add(w)
            if w in self.d.bad_spec:
                raise Unsupported(f"第 {self.peek()[2]} 行: 不支持存储类/限定符 `{w}`")
            if w in self.d.linkage:
                # 收下并丢掉。**丢掉是保义的**：`static` 是内部链接、`inline` 是内联建议，
                # 而这里翻的是**整个单元的全部函数** —— 没有第二个翻译单元能再定义同名函数，
                # 于是"内部链接"在这个语境里没有可分辨的差别。见文件头 §语义选择 3。
                self.i += 1
                continue
            if w in self.d.agg:
                raise Unsupported(f"第 {self.peek()[2]} 行: 不支持聚合/枚举类型 `{w}`"
                                  f"（Stage A 只做整数标量）")
            words.append(w)
            self.i += 1
        if not words:
            t = self.peek()
            raise CError(f"第 {t[2]} 行: 期望类型名，得到 {t[1] or '<结束>'!r}")
        raw = re.sub(r"\s+", " ", " ".join(words)).strip()
        if raw not in self.d.types:
            raise Unsupported(f"第 {line} 行: 类型 `{raw}` 不在 Stage A 子集里")
        name, _ = self.ident()
        # **不在这里判 `(`** —— 函数**定义**的 `(` 就紧跟名字，那是正常形状。
        # 函数指针（`int (*fp)(int)`）会因为 `(` 出现在要标识符的位置被 `ident()` 拒掉。
        if self.at("*") or self.at("["):
            raise Unsupported(f"第 {line} 行: 不支持指针/数组声明 `{name}`")
        return self.d.types[raw], name, line

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
            if t[0] != "op" or t[1] not in self.d.bin:
                return left
            p = self.d.precof[t[1]]
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
            if t[1] in self.d.kw:
                raise Unsupported(f"第 {t[2]} 行: 表达式里不支持关键字 `{t[1]}`")
            self.i += 1
            if self.at("."):
                # 属性/字段访问（Java 的 `System.exit`、`this.w`、C++ 的 `ns::f`）。
                # **点名**它 —— 不点的话会掉进"`System` 后面期望 `=` 或 `(`"，指不到点子。
                raise Unsupported(f"第 {t[2]} 行: 不支持属性/字段访问 `{t[1]}.…`"
                                  f"（本子集只有标量与自由函数）")
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
        if t[0] == "id" and t[1] in self.d.spec_words:
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
        if t[1] in self.d.kw:
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
                # **顶层常量声明**（Java 的 `static final`、C# 的 `const`、C++ 的
                # `constexpr`）：它**不发成函数也不发成全局变量** —— `potato_from`
                # 已经把它收进 `consts`（`_JAVA_CONST` 那一条），`lomt_from` 会发
                # `pub const`。所以翻译器**跳过**它，只要认得那个名字。
                #
                # **`const_words` 为空就是这一族不收**（C 是 —— `potato_from` 也不收
                # 它的全局量，跳过会造出一个下游认不出的名字）。所以这一格是**方言
                # 说了算**，不是"看见 `=` 就跳"。
                if (self.d.const_words & self.last_spec) and self.at("="):
                    self.i += 1
                    v = self.peek()
                    neg = False
                    if self.at("-") and self.peek(1)[0] == "num":
                        neg = True
                        self.i += 1
                        v = self.peek()
                    if v[0] != "num":
                        raise Unsupported(
                            f"第 {v[2]} 行: 顶层常量 `{name}` 的值不是整数字面量"
                            f"（`potato_from` 只收得来整数常量）")
                    self.i += 1
                    self.want(";")
                    continue
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

#: Loment 的保留字 —— 生成的标识符不能撞上。**这一张四门共用**（保留字是本语言的）。
_LOMENT_KW = {
    "module", "use", "fn", "let", "if", "else", "while", "return", "pub", "extern",
    "const", "struct", "enum", "capability", "guard", "excluded", "revocable",
    "trait", "impl", "match", "as", "true", "false", "mut", "addin", "choose",
    "command", "foruse", "comefor", "byuse", "std", "dispatch", "self",
}


class Emitter:
    """语法树 -> Loment 源码（一个函数一份 Emitter）。

    与 `Parser` 一样：语言差异全走 `self.d`，这里不该有 `if <语言>`。
    """

    def __init__(self, fns: dict[str, str], d: "Dialect",
                 consts: dict[str, str] | None = None) -> None:
        #: 本单元**所有**函数的返回类型。调用点的类型靠它 —— 子集里没有跨单元调用。
        self.fns = fns
        self.d = d
        #: **模块常量的名字**。`pub const` 由 `lomt_from` 从 Potato 的 `consts` 发，
        #: 翻译器只要**认得那些名字**（顶层声明本身在解析时被跳过）。不给这张表的话
        #: 函数体里一引用常量就报"用了没声明过的"。
        self.consts: dict[str, str] = dict(consts or {})
        #: C 名字 -> 发出去的名字。`for` 的改名外提就靠这张表（见文件头 §语义选择 2）。
        self.vars: dict[str, str] = {}
        self.n = 0
        self.lines: list[str] = []

    def fresh(self, base: str) -> str:
        while True:
            self.n += 1
            out = f"{self.d.safe(base)}__{self.n}"
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
            return "bool" if self.d.bin[e.op][2] else "int"
        raise AssertionError(type(e))

    def const_name(self, n: str) -> bool:
        """这是不是一个**模块常量**（而不是局部名）。"""
        return n in self.consts

    def ex(self, e: object, want: str) -> str:
        """表达式，**在 `want` 这个上下文里**的写法（必要时补转换）。

        **两条转换要不要补，是方言表说了算** —— 这是这一族里最会咬人的一格：
        C / C++ 的 `&&` 出 `int`、`if (x)` 收标量，两个方向都要补；Java / C# 的
        `&&` 出 `boolean`、条件只收 `boolean`，**一个都不用**。同一处的差别，
        按"能表达的就转、表达不出来的就报错"那条判据，两族结论相反。
        """
        got = self.ty_of(e)
        if got == "void":
            raise Unsupported(f"第 {e.line} 行: 这里用了一个不返回值的调用")
        raw = self.raw(e)
        if got == want:
            return raw
        if got == "int" and want == "bool":
            if not self.d.coerce_int_to_bool:
                raise Unsupported(
                    f"第 {e.line} 行: 这里要的是**条件**，给的是整数。"
                    f"{self.d.name} 的 `&&`/`||` 出 boolean、条件也只收 boolean，"
                    f"所以这里本来就该是个布尔表达式（多半写错了）")
            return f"{raw} != 0"      # `if (x)`：非零即真
        if got == "bool" and want == "int":
            if not self.d.coerce_bool_to_int:
                raise Unsupported(
                    f"第 {e.line} 行: 这里要的是**整数**，给的是布尔。"
                    f"{self.d.name} 的布尔与整数**不是一回事**（不像 C 那样能互相顶），"
                    f"所以这里本来就该是个整数表达式（多半写错了）")
            # `int x = (a < b);`：bool -> 0/1
            return f"({raw}) as {self.d.int_default}"
        raise AssertionError((got, want))

    def raw(self, e: object) -> str:
        """表达式在**它自己的类型**下的写法。括号一律加上 —— 别让读者去猜优先级。"""
        if isinstance(e, Lit):
            return str(e.v)
        if isinstance(e, Var):
            if self.const_name(e.name):
                return e.name          # 模块常量名照抄 —— `pub const` 在同层
            if e.name not in self.vars:
                raise Unsupported(
                    f"第 {e.line} 行: 用了没声明过的 `{e.name}`"
                    f"（顶层常量声明在解析时被跳过，`pub const` 由 `lomt_from` 从 Potato "
                    f"的 `consts` 发 —— 名字对不上的话就是那一步没收它）")
            return self.vars[e.name]
        if isinstance(e, Call):
            return f"{e.name}({', '.join(self.ex(a, 'int') for a in e.args)})"
        if isinstance(e, Un):
            if e.op == "!":
                # Loment 的 `!` 只收 bool，而 C 的 `!x` 收 int —— 直接写成 `x == 0`
                return f"({self.ex(e.e, 'int')} == 0)"
            return f"({e.op}{self.ex(e.e, 'int')})"
        if isinstance(e, Bin):
            op, want, _b = self.d.bin[e.op]
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
            self.vars[s.name] = self.d.safe(s.name)
            if s.init is None:
                # 见文件头 §语义选择 1：C 的未初始化在这里变成确定的零值
                self.out(f"let {self.d.safe(s.name)}: {s.ty} = 0;", depth)
            else:
                self.out(f"let {self.d.safe(s.name)}: {s.ty} = {self.ex(s.init, 'int')};", depth)
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
        self.vars = {n: self.d.safe(n) for (_t, n, _l) in f.params}
        self.n = 0
        self.lines = []
        ps = ", ".join(f"{self.d.safe(n)}: {t}" for (t, n, _l) in f.params)
        head = f"pub fn {f.name}({ps})"
        if f.ret != "()":
            head += f" -> {f.ret}"
        self.lines.append(head + " {")
        self.stmts(f.body, 0)
        self.lines.append("}")
        return "\n".join(self.lines)

    #: `for` 的初值**与**循环里用了同一个名字时的遮蔽，靠 `vars` 的存/取还原 —
    #: 见 `for_` 结尾（不需要额外的栈）。


def parse(src: str, d: "Dialect") -> list:
    return Parser(tokens(_pre(src, d)), d).unit()


def _pre(src: str, d: "Dialect") -> str:
    """这一族的源码预处理（见 `Dialect.pre`）。没给就是原样。"""
    return d.pre(src) if d.pre else src


def translate(src: str, d: "Dialect", keep: set[str] | None = None,
              externs: dict[str, str] | None = None,
              consts: dict[str, str] | None = None) -> str:
    """C 源码 -> Loment 源码（**只有函数**，`module` 头由调用方加）。

    * `keep` 给了就只翻这些函数 —— 一份 C 里可能既有"要翻译成 Loment"的函数，
      也有"留着外部链接（`pub extern fn`）"的函数。
    * `externs` 是**不在这份源码里、但调用点要认的**函数：`名字 -> Loment 返回类型`。
      没有它的话，被翻译的代码一调到 `pub extern fn` 那种函数就会报"本单元没有"。
    """
    fns = Parser(tokens(_pre(src, d)), d).unit()
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
        out.append(Emitter(rets, d, consts).emit_fn(f))
    return "\n\n".join(out) + "\n"


def main(d: "Dialect", src_lang: str, argv: list[str] | None = None) -> int:
    """`path -> Loment` 的命令行。**各门复用这一个** —— 别各自抄一遍。

    `src_lang` 只用来写帮助与报错里的"这门语言叫什么"。
    """
    ap = argparse.ArgumentParser(prog=f"{d.name}trans",
                                 description=f"{src_lang} 写法 -> Loment")
    ap.add_argument("path")
    ap.add_argument("--out", metavar="PATH")
    a = ap.parse_args(argv)
    try:
        text = translate(Path(a.path).read_text(encoding="utf-8"), d)
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


