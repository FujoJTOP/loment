#!/usr/bin/env python3
"""nltrans.py — **自然语言写法 -> Loment**（`docs/197`）。

     自然语言写法 --nltrans--> Loment

## 它是什么，不是什么

`docs/188` §0 把"表层语法"这件事框正了：**只让拼法与形状，不让语义**。
这一门是那条规矩的**极端样本** —— 前面六门（C / Python / Java / C# / C++ / Go）是
**别人已经存在的语言**，翻译器只能"能表达的就转，表达不出来的就报错"；而这一门
是**我们自己发明的**，所以那条子集线是**画出来的**，不是**碰上的**：

* 凡是画进子集的句子，翻出来的 Loment 与手写的一模一样（判据钉着"逐字节"）；
* 凡是没画的，**在词法/解析那一步就拒**，并说清这一版不收什么。

## 形状：**一个动词起头的句子 = 一条语句**

自然语言的骨架是"动词 + 宾语"，所以这一门的语法就是这句话的实现：

| 句子 | 意思 |
|---|---|
| `program tour` | 模块名（第一句，必须有） |
| `remember LIMIT as 3` | 常量 |
| `to add with a as a whole number and b as a whole number giving a whole number` … `end` | 函数 |
| `let n be 5` / `let n be 5 as a byte` | 变量 |
| `let fb be a buffer of 1024 bytes` | 分配 |
| `set n to 6` | 赋值 |
| `when n is above 3` … `otherwise` … `end` | 条件 |
| `while n is above 0` … `end` | 循环 |
| `for i from 0 to 10` … `end` | 计数循环 |
| `give back n times 2` | 返回 |
| `do f of 3` | 只要副作用的一次调用 |
| **`say "hi"`** | 把一段文本写到标准输出 |
| **`say the number n`** | 把一个数写成十进制再输出 |
| **`talk to the machine 60 with n, 0, 0`** | 裸 syscall：`syscall4(n, a, b, c)` |
| **`paint 255 at 0 in fb`** | 往缓冲区里写一个字节：`store8(fb, 0, 255 as u8)` |

加粗那四句是用户点名的那几个动词（`say` / `talk` / `paint`）。

## 三条**不是翻译、是决定**的东西

1. **语句以换行为界**（`end` 收块）—— 自然语言里没有分号。所以词法器要产出换行记号，
   并让"行尾是运算符/逗号"的那些行**续行**（见 `_CONTINUE`）。
2. **`say` 分两句**：`say <文本>` 与 `say the number <数>`。不合并成一句是因为
   合并就要**猜表达式的类型**，而本语言（与 Loment 一样）**没有类型推断** ——
   猜错的表现是"把一段文本按数去打"，那种错两边都编得过。分开写是**让作者说清楚**。
3. **`let` 的类型是"查"出来的，不是"推"出来的**（`Parser.type_of`）。查得到的有四类：
   句子里写了的 `as <类型>`；字面量；**声明过的名字**（模块常量、形参、前面的 `let`）；
   以及**运算符的定则**（比较与 `and`/`or` 出 `bool`、其余同型则同型）。
   查不到就**报错**，不是"默认按 i64 算"—— 后者会把一个真的类型错推后到别处炸。

   **唯一故意不查的是"调用"**（`let c be label of 8` 要写 `as a whole number`）：
   那要读**另一个函数的声明**，而读出来的是一条**事实**、不是一个**法则** ——
   别人改了那个函数的返回类型，这一句就该跟着变，而变了之后**两边都编得过**。
   运算符那几条不一样：对每个程序它们都只有一个答案。

## 一处**踩出来的坑**（写进代码里的理由）

发出来的 `say the number` 那个辅助函数（`nl_write_num`）里，`0` **必须**先落成一个
带类型的局部量（`let zero: i64 = 0;`），不能直接在表达式里写 `0 - v`：

    实测（安装版 0.1.4 的自举驱动）：`let a: i64 = 0 - 12345;` 之后 `a as u64` 给的是
    4294954951（= 2^32 - 12345），而参考实现为同一句发的是 `sub i64 0, 12345`。
    也就是说**无后缀字面量在自举驱动里被当成了 u32**，`i64` 上下文里的取负于是 32 位回绕。

取负这件事在**运行期的 i64 变量**上是好的（实测 `let zero: i64 = 0; zero - v` 对
`v = -12345` 给 12345），所以辅助函数走"带类型的零"这条路，而不去碰字面量运算。
**这条不是绕过一个翻译器的限制，是绕过一个编译器的 bug** —— 两边都编得过、只有一边
算错，是本项目的红线；上面那个复现留给工具链那一侧（见 `docs/197` §7）。

用法:

    python tools/nltrans.py SRC.nl [--out OUT.lomt]
退出码: 0 = 成功 / 1 = 用到子集外的东西 / 2 = 用法或读取错误。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


# ---------------------------------------------------------------- 异常

class NaturalError(Exception):
    """**写法本身就不对**（缺 `end`、句子读不通、类型短语拼错…）。

    与 `Unsupported` 分开：那个是"这一句读得通、但这一版不翻"（`docs/188` §5 第 2 条
    "子集外 —— 还是报错，不是声明能救的"）。分开的理由与 `docs/179` §6.5 那条一样：
    **错要指在错的地方**，而"你写错了"与"我还没做"是两件事。
    """


class Unsupported(Exception):
    """**读得通、翻不出来** —— 子集外。消息里必须点名是哪一处、以及为什么不收。"""


# ---------------------------------------------------------------- 词法

#: 运算符的词形与符号形**同一处**（"拼法可以有别名"那条纪律也适用于运算符）。
#: 值一律是 Loment 的运算符。多词的那些在解析器里按**最长匹配**处理。
_WORD_OPS = {
    "plus": "+", "minus": "-", "times": "*", "over": "/", "modulo": "%",
    "and": "&&", "or": "||",
    "is": "==", "is not": "!=", "is above": ">", "is below": "<",
    "is at least": ">=", "is at most": "<=",
    "shifted left by": "<<", "shifted right by": ">>",
}

#: 符号形的运算符（一个记号就是一个 token）。
_SYMBOL_OPS = {"+", "-", "*", "/", "%", "&", "|", "^", "<<", ">>",
               "==", "!=", "<", "<=", ">", ">=", "&&", "||", "!"}

#: 自然语言的类型短语 -> Loment 类型。`a` / `an` 可省。
_TYPE_WORDS = {
    "whole number": "i64",
    "count": "u32",
    "byte": "u8",
    "truth": "bool",
    "text": "str",
    "buffer": "ptr",
    "nothing": "()",
}

#: `<N>-bit whole number` / `<N>-bit count` 的两种基名。
_TYPE_BASE = {"whole number": "i", "count": "u"}

#: Loment 的类型名**直接写也行**（要跟别人说同一件事时，缩写比绕口的短语省事）。
_TYPE_DIRECT = {"i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64",
                "bool", "str", "ptr", "()"}

#: 内建函数（`.claude/skills/loment/SKILL.md` §3 那张表，**全部，没有别的**）。
#: 收进来的用处只有一条：**调用点报错时能分清"你名字写错了"与"这一版不收"**。
_BUILTINS = {
    "str_len": "u32", "str_byte": "u32", "str_eq": "bool", "str_concat": "str",
    "str_ptr": "ptr", "alloc": "ptr", "free": "u32", "load8": "u32",
    "store8": "u32", "ptr_add": "ptr", "ptr_sub": "ptr", "slice_len": "u32",
    "panic": "u32", "atomic_add": "u32", "get_bits": "u8", "set_bits": "u8",
    "inb": "u32", "outb": "u32", "syscall4": "i64", "syscall6": "i64",
}

#: **行尾是这些词/符号时，换行不算句界**（一句话写到下一行是常态：函数头、
#: 参数表、算式都可能换行）。
#:
#: 少一个的症状是"明明是合法的一句，却在中间报'这句读不通'"；**多一个更坏** ——
#: 它把两句**悄悄粘成一句**。这一格是踩出来的：第一版把 **`a` / `an`** 也放了进来
#: （想着 `as a byte` 那种类型短语），于是
#:
#:     let x be a          <- `a` 是个**形参名**
#:     let y be b
#:
#: 两行被粘成一句 —— 而报错落在第二行上，说的是"这里该断了却还有 `let`"，
#: 与真正的原因（第一版多收了 `a`）隔着一层。**现在只收"不可能是名字"的那些**：
#: 运算符（含多词运算符的每一个词）、标点、以及结构关键字。
_CONTINUE = set(_SYMBOL_OPS) | {"(", ",", "the", "be", "as", "of", "with",
                                "in", "from", "to", "giving"}
_CONTINUE |= {w for op in _WORD_OPS for w in op.split()}


class Tok:
    __slots__ = ("kind", "text", "line", "pos")

    def __init__(self, kind: str, text: str, line: int, pos: int):
        self.kind, self.text, self.line, self.pos = kind, text, line, pos

    def __repr__(self) -> str:  # pragma: no cover - 只在调试时看到
        return f"{self.kind}:{self.text}@{self.line}"


def _is_id_start(ch: str) -> bool:
    return ch.isalpha() or ch == "_"


def _is_id(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def tokenize(src: str) -> list[Tok]:
    """源文本 -> 记号流。**只有 `nl` 是句界**，缩进不进语法（缩进是给人看的）。"""
    out: list[Tok] = []
    i, line, n = 0, 1, len(src)
    while i < n:
        ch = src[i]
        if ch == "\n":
            out.append(Tok("nl", "\n", line, i))
            line += 1
            i += 1
            continue
        if ch in " \t\r":
            i += 1
            continue
        if src.startswith("//", i):
            while i < n and src[i] != "\n":
                i += 1
            continue
        if _is_id_start(ch):
            j = i
            while j < n and _is_id(src[j]):
                j += 1
            word = src[i:j]
            if word == "note":          # `note ...` 是自然语言的注释
                while j < n and src[j] != "\n":
                    j += 1
                i = j
                continue
            out.append(Tok("id", word, line, i))
            i = j
            continue
        if ch.isdigit():
            j = i
            if src.startswith("0x", i) or src.startswith("0X", i):
                j = i + 2
                while j < n and src[j] in "0123456789abcdefABCDEF":
                    j += 1
            else:
                while j < n and src[j].isdigit():
                    j += 1
            out.append(Tok("num", src[i:j], line, i))
            i = j
            continue
        if ch == '"':
            j = i + 1
            while j < n and src[j] != '"':
                if src[j] == "\\":
                    j += 1
                if j < n and src[j] == "\n":
                    raise NaturalError(f"第 {line} 行: 字符串没有收尾的 `\"`")
                j += 1
            if j >= n:
                raise NaturalError(f"第 {line} 行: 字符串没有收尾的 `\"`")
            out.append(Tok("str", src[i:j + 1], line, i))
            i = j + 1
            continue
        two = src[i:i + 2]
        if two in ("<<", ">>", "==", "!=", "<=", ">=", "&&", "||"):
            out.append(Tok("sym", two, line, i))
            i += 2
            continue
        if ch in "+-*/%&|^!<>(),.{};":
            out.append(Tok("sym", ch, line, i))
            i += 1
            continue
        raise Unsupported(f"第 {line} 行: 认不出的字符 `{ch}`"
                          f"（`docs/197` §边界：这一版只收动词起头的句子）")
    out.append(Tok("nl", "\n", line, n))

    # ---- 续行：上一个是运算符/逗号/连接词时，这个换行不算句界
    merged: list[Tok] = []
    for t in out:
        if t.kind == "nl":
            if not merged or merged[-1].kind == "nl":
                continue                      # 空行/连续换行 -> 合成一个
            if merged[-1].text in _CONTINUE:
                continue
        merged.append(t)
    return merged


# ---------------------------------------------------------------- 语法树

class Fn:
    """一个函数：名字、形参、返回类型、**原文**（`body`）、原文里的起始行。"""

    __slots__ = ("name", "params", "ret", "body", "line", "stmts")

    def __init__(self, name: str, params: list[tuple[str, str]], ret: str,
                 body: str, line: int, stmts: list):
        self.name, self.params, self.ret = name, params, ret
        self.body, self.line, self.stmts = body, line, stmts


class Const:
    __slots__ = ("name", "ty", "value", "line")

    def __init__(self, name: str, ty: str, value: int, line: int):
        self.name, self.ty, self.value, self.line = name, ty, value, line


class Program:
    __slots__ = ("unit", "consts", "fns")

    def __init__(self, unit: str, consts: list[Const], fns: list[Fn]):
        self.unit, self.consts, self.fns = unit, consts, fns


# ---------------------------------------------------------------- 解析器

class Parser:
    def __init__(self, toks: list[Tok], src: str, what: str = "这份源",
                 consts: dict[str, str] | None = None,
                 calls: dict[str, str] | None = None,
                 strict_types: bool = True):
        self.t, self.src, self.what = toks, src, what
        self.i = 0
        #: **函数名 -> 返回类型**（= 内建表 + 外部声明 + 本文件各 `to … giving`）。
        #: 它让 `let a be sum_to of LIMIT` 不必再补 `as` —— 那是**查声明**，不是推断。
        self.calls: dict[str, str] = dict(_BUILTINS)
        self.calls.update(calls or {})
        #: 第一遍（收声明表的那遍）为 `False`：那时表还没齐，说不出类型的 `let`
        #: 先放过，由第二遍来报。
        self.strict_types = strict_types
        #: **已知名字的类型**：模块常量（`consts` 那份表 / 本文件里的 `remember`）、
        #: 形参、已经读过的 `let`。它只服务一件事：`let v be n` 里那个 `n` 的
        #: 类型是**查出来的**，不是猜的。
        #:
        #: 常量要从外面喂进来（`lomt_from` 走 `translate` 时手上只有**函数正文**，
        #: 常量在对象里）—— 这正是 `translate` 那个 `consts` 形参的用处，
        #: 与 `pytrans` 收它是同一个理由。
        self.tenv: dict[str, str] = dict(consts or {})
        self.const_names: set[str] = set(consts or {})

    # ---- 记号流的小工具

    def cur(self) -> Tok:
        # **夹住下标**：句尾那个哨兵换行被吃掉之后 `self.i` 会走到 `len(t)`，
        # 而这时"当前记号"的正确含义是**最后一个**（哨兵）—— 夹住的话，所有
        # `self.cur().kind == "nl"` 的判据在文件末尾都自然成立，不必每处都先查边界。
        return self.t[self.i if self.i < len(self.t) else len(self.t) - 1]

    def peek(self, k: int = 1) -> Tok:
        j = self.i + k
        return self.t[j] if j < len(self.t) else self.t[-1]

    def at(self, *words: str) -> bool:
        return self.cur().kind != "nl" and self.cur().text in words

    def word_at(self, k: int, word: str) -> bool:
        t = self.peek(k)
        return t.kind == "id" and t.text == word

    def eat(self, word: str) -> bool:
        if self.at(word):
            self.i += 1
            return True
        return False

    def need(self, word: str, why: str = "") -> Tok:
        """**要求并吃掉**这个词。吃是它的一半职责 —— 只查不吃的话，每个调用点都得
        自己再补一句 `self.i += 1`，漏一处就是"卡在原地"（写第一版时正是这么卡的）。"""
        if not self.at(word):
            self.err(f"这里要写 `{word}`{('（' + why + '）') if why else ''}")
        t = self.t[self.i]
        self.i += 1
        return t

    def eat_word(self, *words: str) -> str | None:
        """吃一个**词**（不是符号）—— 多词形式按最长匹配，`is not` 优先于 `is`。"""
        best, bestk = None, 0
        for k, w in enumerate(words):
            parts = w.split()
            if all(self.word_at(j, p) for j, p in enumerate(parts)):
                if len(parts) > bestk:
                    best, bestk = w, len(parts)
        if best is None:
            return None
        self.i += bestk
        return best

    def err(self, msg: str) -> None:
        raise NaturalError(f"第 {self.cur().line} 行: {msg}")

    def end_sentence(self) -> None:
        if self.cur().kind == "nl":
            self.i += 1
            return
        if self._at_eof() or self.at("end", "otherwise"):
            return
        self.err(f"这一句到这里该断了，却还有 `{self.cur().text}`"
                 f"（自然语言写法**一句一行**，没有分号）")

    # ---- 程序

    def program(self, need_program: bool) -> Program:
        unit, consts, fns = "", [], []
        seen_program = False
        while not self._at_eof():
            if self.cur().kind == "nl":
                self.i += 1
                continue
            if self.at("program"):
                if seen_program:
                    self.err("`program` 只能写一次")
                self.i += 1
                name = self.cur()
                if name.kind != "id":
                    self.err("`program` 后面要写模块名")
                unit, seen_program = name.text, True
                self.i += 1
            elif self.at("remember"):
                consts.append(self.constant())
            elif self.at("to"):
                fns.append(self.function())
                continue                      # `function` 自己吃掉了句尾
            elif self.at("use"):
                self.err("`use` 这一版还不收 —— 见 `docs/197` §边界"
                         "（翻译器只出函数，导入要由调用方声明）")
            else:
                self.err(f"顶层不认识的句子 `{self.cur().text}`"
                         f"（顶层只有 `program` / `remember` / `to`）")
            self.end_sentence()
        if need_program and not seen_program:
            raise NaturalError("第一句必须是 `program <模块名>` —— 没有它这一份就不知"
                               "道自己叫什么（`docs/197` §2）")
        return Program(unit, consts, fns)

    def _at_eof(self) -> bool:
        return self.cur().kind == "nl" and self.i >= len(self.t) - 1

    def constant(self) -> Const:
        line = self.cur().line
        self.need("remember")
        name = self.cur()
        if name.kind != "id":
            self.err("`remember` 后面要写常量名（**全大写**是习惯）")
        self.i += 1
        self.need("as", "自然语言写法里常量是 `remember 名字 as 值`")
        ty = "i64"
        if self.word_at(0, "a") or self.word_at(0, "an"):
            save = self.i
            self.i += 1
            ty = self.type_phrase()
            if ty is None:
                self.i = save
                ty = "i64"
        val = self.cur()
        if val.kind != "num":
            self.err("常量只能是整数字面量（`docs/188` §7.1 那条边界："
                     "L1 常量只收整型）")
        self.i += 1
        i = int(val.text, 0)
        if ty.startswith("u") and i < 0:      # pragma: no cover - 词法器不出负数
            raise Unsupported(f"第 {line} 行: 常量 {val.text} 是负的，装不进 {ty}")
        # 常量进类型环境 —— 后面的函数体里 `let e be LIMIT times 10` 要查得到它
        self.tenv[name.text] = ty
        self.const_names.add(name.text)
        return Const(name.text, ty, i, line)

    # ---- 函数

    def function(self) -> Fn:
        start = self.cur()
        # 类型环境**按函数清**（常量那几条留着）—— 上一支的形参不许漏到这一支
        self.tenv = {k: v for k, v in self.tenv.items() if k in self.const_names}
        self.need("to")
        name = self.cur()
        if name.kind != "id":
            self.err("`to` 后面要写函数名")
        self.i += 1
        params: list[tuple[str, str]] = []
        if self.at("with"):
            self.i += 1
            while True:
                pname = self.cur()
                if pname.kind != "id":
                    self.err("形参要写成 `名字 as <类型>`")
                self.i += 1
                self.need("as", "形参**必须**写类型 —— 这一门没有类型推断")
                ty = self.type_phrase()
                if ty is None:
                    self.err("认不出这个类型短语（`docs/197` §3 那张表）")
                params.append((pname.text, ty))
                self.tenv[pname.text] = ty
                if not self.eat("and"):
                    break
        ret = "()"
        if self.at("giving"):
            self.i += 1
            r = self.type_phrase()
            if r is None:
                self.err("`giving` 后面要写返回类型；不返回值就整句不写 `giving`")
            ret = r
        self.end_sentence()
        body = self.block({"end"})
        self.need("end", f"函数 `{name.text}` 要用 `end` 收尾")
        text = self.src[start.pos:self.cur().pos].strip()
        return Fn(name.text, params, ret, text, start.line, body)

    def type_phrase(self) -> str | None:
        """读一个类型短语（`a` / `an` 可省）。读不出来返回 `None`，**不动下标**。"""
        save = self.i
        if self.at("a") or self.at("an"):
            if self.peek().kind == "id":
                self.i += 1
        t = self.cur()
        if t.kind == "num":                   # `<N>-bit whole number` / `<N>-bit count`
            if self.peek().kind == "sym" and self.peek().text == "-" \
                    and self.word_at(2, "bit"):
                n = t.text
                self.i += 3
                words = []
                for _ in range(2):
                    if self.cur().kind == "id":
                        words.append(self.cur().text)
                        self.i += 1
                base = _TYPE_BASE.get(" ".join(words))
                if base and n in ("8", "16", "32", "64"):
                    return base + n
            self.i = save
            return None
        if t.kind == "id":
            if t.text in _TYPE_DIRECT:
                self.i += 1
                return t.text
            # **两个词的那几条要先看**（`whole number` / `whole number` 是一条），
            # 而且**两个记号都必须是词** —— 这一格是踩出来的：第一版写成
            # `" ".join(x.text for x in self.t[i:i+2] if x.kind == "id")`，
            # 那个 `if` 会**跳过换行**，于是 `giving a truth` 里那个**单词**的
            # `truth` 被当成"两个词"，`self.i += 2` 顺手把**句尾的换行**一起吃了 ——
            # 下一句的 `give` 于是落在"这一句还没断"的位置上，报的是一句
            # 指不到点子的"该断了却还有 `give`"。
            nxt = self.peek(1)
            if nxt.kind == "id":
                two = f"{t.text} {nxt.text}"
                if two in _TYPE_WORDS:
                    self.i += 2
                    return _TYPE_WORDS[two]
            if t.text in _TYPE_WORDS:         # `text` / `byte` / `truth` / `buffer`
                self.i += 1
                return _TYPE_WORDS[t.text]
        self.i = save
        return None

    # ---- 语句块

    def block(self, stops: set[str]) -> list:
        out: list = []
        while True:
            if self._at_eof():
                raise NaturalError("这份源到这里就断了 —— 有一个块没有 `end` 收尾")
            if self.cur().kind == "nl":
                self.i += 1
                continue
            if self.at(*stops):
                return out
            out.append(self.sentence())
            self.end_sentence()

    def sentence(self) -> tuple:
        t = self.cur()
        if t.kind != "id":
            self.err(f"句子要以动词起头，这里是 `{t.text}`")
        w = t.text
        if w == "let":
            return self.let()
        if w == "set":
            return self.set()
        if w == "say":
            return self.say()
        if w == "talk":
            return self.talk()
        if w == "paint":
            return self.paint()
        if w == "give":
            return self.give_back()
        if w == "do":
            self.i += 1
            return ("do", self.expr(), t.line)
        if w == "when":
            return self.when()
        if w == "while":
            self.i += 1
            cond = self.expr()
            self.end_sentence()
            body = self.block({"end"})
            self.need_end("while")
            return ("while", cond, body, t.line)
        if w == "for":
            return self.forloop()
        if w in ("to", "program", "remember"):
            self.err(f"`{w}` 是**顶层**的句子，不能写在函数体里")
        if w == "end":
            self.err("多了一个 `end`")
        self.err(f"不认识的句子 `{w}` —— 这一门是**动词起头**的"
                 f"（`docs/197` §2 那张表）")

    def need_end(self, what: str) -> None:
        self.need("end", f"`{what}` 那一块要用 `end` 收尾")

    def let(self) -> tuple:
        line = self.cur().line
        self.need("let")
        name = self.cur()
        if name.kind != "id":
            self.err("`let` 后面要写变量名")
        self.i += 1
        self.need("be", "自然语言写法里变量是 `let 名字 be 值`")
        # `let fb be a buffer of 1024 bytes` —— 分配是**另一种**东西（不是"值"）
        if self.word_at(0, "a") and self.word_at(1, "buffer") and self.word_at(2, "of"):
            self.i += 3
            n = self.expr()
            self.need("bytes", "写成 `let 名字 be a buffer of <字节数> bytes`")
            self.tenv[name.text] = "ptr"
            return ("letbuf", name.text, n, line)
        e = self.expr()
        if e[0] == "cast":                    # `let b be 200 as a byte`
            self.tenv[name.text] = e[2]
            return ("let", name.text, e[1], e[2], line)
        ty = self.type_of(e)
        if ty == "()" and self.strict_types:
            # 调用了一个**不返回值**的函数。不拦的话会发出 `let x: () = f();` ——
            # 而那要到编译那一刻才炸，且报的是"类型 `()`"，与作者写下的那句话
            # 隔着一层。这里拦得住，因为声明表就在手上。
            self.err(f"`{name.text}` 右边那个调用**不返回值**（`giving` 那一句没写）"
                     f"—— 它不能拿来当一个值")
        if ty is None and not self.strict_types:
            # 第一遍：声明表还没收齐，先放过（`self.tenv[name]` 记成"不知道"，
            # 后面靠它的 `let` 也只会在第二遍里被报出来）
            return ("let", name.text, e, "i64", line)
        if ty is None:
            self.err(
                f"说不出 `{name.text}` 是什么类型 —— 补一句 `as a whole number` 之类。\n"
                f"  （`docs/197` §3：类型是**查**出来的，不是推出来的 —— 字面量、"
                f"声明过的名字、以及**运算符的定则**都查得到；**调用**查不到，"
                f"因为那要读另一个函数的声明，而读错了属于「两边都编得过」的那类错）")
        self.tenv[name.text] = ty
        return ("let", name.text, e, ty, line)

    #: **运算符的定则**（成对出现的类型 -> 结果类型）。这里只放**所有语言都同意**的
    #: 那几条：比较出真理值、`and`/`or` 出真理值、其余同型则同型。它是**语言法则**
    #: （对每个程序只有一个答案），与"读另一个函数的返回类型"不是一回事 —— 后者是
    #: **事实**，而事实会变（改了别的函数、这一句就该跟着变），错的那一边照样编得过。
    def type_of(self, e: tuple) -> str | None:
        k = e[0]
        if k == "num":
            return "i64"
        if k == "str":
            return "str"
        if k == "bool":
            return "bool"
        if k == "cast":
            return e[2]
        if k == "name":
            return self.tenv.get(e[1])
        if k == "un":
            if e[1] == "!":
                return "bool"
            return self.type_of(e[2])
        if k == "bin":
            op = e[1]
            if op in ("&&", "||") or op in ("==", "!=", "<", "<=", ">", ">="):
                return "bool"
            if op in ("<<", ">>"):            # 左移右移：取左边那个类型
                return self.type_of(e[2])
            l, r = self.type_of(e[2]), self.type_of(e[3])
            return l if (l is not None and l == r) else None
        if k == "call":
            # **查被调函数的声明**（内建表 / 外部声明 / 本文件那几张 `to … giving`）。
            # 这仍然是"查"，不是"推"：答案写在别处，而**那个声明就是那个答案**。
            return self.calls.get(e[1])
        # 取字段：这一版没有结构体，够不到
        return None

    def set(self) -> tuple:
        line = self.cur().line
        self.need("set")
        name = self.cur()
        if name.kind != "id":
            self.err("`set` 后面要写变量名")
        self.i += 1
        self.need("to", "自然语言写法里赋值是 `set 名字 to 值`")
        return ("set", name.text, self.expr(), line)

    def say(self) -> tuple:
        line = self.cur().line
        self.need("say")
        if self.word_at(0, "the") and self.word_at(1, "number"):
            self.i += 2
            return ("saynum", self.expr(), line)
        return ("say", self.expr(), line)

    def talk(self) -> tuple:
        line = self.cur().line
        self.need("talk")
        self.need("to", "这一句是 `talk to the machine <号> with <a>, <b>, <c>`")
        self.need("the")
        self.need("machine", "这一版只有一台机器可以对话：`the machine`（= 裸 syscall）")
        nr = self.expr()
        self.need("with", "`talk to the machine <号>` 后面要写 `with <a>, <b>, <c>`")
        args = []
        while True:
            args.append(self.expr())
            if not self.eat(","):
                break
        if len(args) != 3:
            raise Unsupported(
                f"第 {line} 行: `talk` 要**三个**参数（`syscall4(号, a, b, c)`），"
                f"这里给出 {len(args)} 个。\n"
                f"  如果参数里还套了一个调用（比如 `f of 7`），它会把后面的逗号**吃掉**"
                f" —— 把嵌套的那个调用括起来：`with (f of 7), 0, 0`")
        return ("talk", nr, args[0], args[1], args[2], line)

    def paint(self) -> tuple:
        line = self.cur().line
        self.need("paint")
        v = self.expr()
        self.need("at", "这一句是 `paint <值> at <下标> in <缓冲区>`")
        idx = self.expr()
        self.need("in")
        buf = self.expr()
        return ("paint", v, idx, buf, line)

    def give_back(self) -> tuple:
        line = self.cur().line
        self.need("give")
        self.need("back", "返回是 `give back <表达式>`")
        return ("ret", self.expr(), line)

    def when(self) -> tuple:
        line = self.cur().line
        self.need("when")
        arms = [(self.expr(), None)]
        self.end_sentence()
        arms[0] = (arms[0][0], self.block({"otherwise", "end"}))
        else_body = None
        while self.at("otherwise"):
            self.i += 1
            if self.at("when"):
                self.i += 1
                cond = self.expr()
                self.end_sentence()
                arms.append((cond, self.block({"otherwise", "end"})))
            else:
                self.end_sentence()
                else_body = self.block({"end"})
                break
        self.need_end("when")
        return ("when", arms, else_body, line)

    def forloop(self) -> tuple:
        line = self.cur().line
        self.need("for")
        name = self.cur()
        if name.kind != "id":
            self.err("`for` 后面要写循环变量的名字")
        self.i += 1
        self.need("from", "这一句是 `for <名字> from <下界> to <上界>`（上界**不含**）")
        lo = self.expr()
        self.need("to")
        hi = self.expr()
        self.end_sentence()
        body = self.block({"end"})
        self.need_end("for")
        return ("for", name.text, lo, hi, body, line)

    # ---- 表达式（优先级同 Loment / Rust）

    def expr(self) -> tuple:
        return self.e_or()

    def e_or(self):
        return self._bin(self.e_and, ("||",), ("or",))

    def e_and(self):
        return self._bin(self.e_cmp, ("&&",), ("and",))

    def e_cmp(self):
        l = self.e_bitor()
        while True:
            if self.cur().kind == "sym" and self.cur().text in ("==", "!=", "<",
                                                                "<=", ">", ">="):
                op = self.cur().text
                self.i += 1
                l = ("bin", op, l, self.e_bitor())
                continue
            w = self.eat_word("is not", "is above", "is below", "is at least",
                              "is at most", "is")
            if w is None:
                return l
            l = ("bin", _WORD_OPS[w], l, self.e_bitor())

    def e_bitor(self):
        return self._bin(self.e_bitxor, ("|",))

    def e_bitxor(self):
        return self._bin(self.e_bitand, ("^",))

    def e_bitand(self):
        return self._bin(self.e_shift, ("&",))

    def e_shift(self):
        l = self.e_add()
        while True:
            if self.cur().kind == "sym" and self.cur().text in ("<<", ">>"):
                op = self.cur().text
                self.i += 1
                l = ("bin", op, l, self.e_add())
                continue
            w = self.eat_word("shifted left by", "shifted right by")
            if w is None:
                return l
            l = ("bin", _WORD_OPS[w], l, self.e_add())

    def e_add(self):
        return self._bin(self.e_mul, ("+", "-"), ("plus", "minus"))

    def e_mul(self):
        return self._bin(self.e_unary, ("*", "/", "%"),
                         ("times", "over", "modulo"))

    def _bin(self, sub, syms: tuple, words: tuple = ()):
        l = sub()
        while True:
            if self.cur().kind == "sym" and self.cur().text in syms:
                op = self.cur().text
                self.i += 1
                l = ("bin", op, l, sub())
                continue
            if words:
                w = self.eat_word(*words)
                if w is not None:
                    l = ("bin", _WORD_OPS[w], l, sub())
                    continue
            return l

    def e_unary(self):
        if self.cur().kind == "sym" and self.cur().text == "-":
            self.i += 1
            return ("un", "-", self.e_unary())
        if self.cur().kind == "sym" and self.cur().text == "!":
            self.i += 1
            return ("un", "!", self.e_unary())
        if self.at("not"):
            self.i += 1
            return ("un", "!", self.e_unary())
        return self.e_postfix()

    def e_postfix(self):
        e = self.primary()
        while self.at("as"):
            self.i += 1
            ty = self.type_phrase()
            if ty is None:
                self.err("`as` 后面要写类型（`docs/197` §3 那张表）")
            e = ("cast", e, ty)
        return e

    def primary(self):
        t = self.cur()
        if t.kind == "num":
            self.i += 1
            return ("num", t.text)
        if t.kind == "str":
            self.i += 1
            return ("str", t.text)
        if t.kind == "sym" and t.text == "(":
            self.i += 1
            e = self.expr()
            self.need(")", "括号没关上")
            return e
        if t.kind == "id" and t.text in ("true", "false"):
            self.i += 1
            return ("bool", t.text)
        if t.kind == "id" and t.text == "the":
            self.i += 1
            f = self.cur()
            if f.kind != "id":
                self.err("`the` 后面要写字段名（`the <字段> of <东西>`）")
            self.i += 1
            self.need("of", "取字段是 `the <字段> of <东西>`")
            base = self.e_postfix()
            return ("field", base, f.text)
        if t.kind == "id":
            self.i += 1
            if self.at("of"):                 # `f of a, b`
                self.i += 1
                args = [self.expr()]
                while self.eat(","):
                    args.append(self.expr())
                return ("call", t.text, args, t.line)
            # **没有实参的调用就写名字本身**（`say the number run`）——
            # 自然语言里不会为了一个空参数表再加一层壳。分辨靠**声明表**：
            # 名字在这个单元里是个函数、又不是任何一个局部量 ⇒ 它是一次调用。
            # 局部量优先，所以"变量与函数同名"时取变量（写出来是什么就是什么）。
            if t.text in self.calls and t.text not in self.tenv:
                return ("call", t.text, [], t.line)
            return ("name", t.text, t.line)
        self.err(f"这里要写一个值，得到 `{t.text}`")


# ---------------------------------------------------------------- 发射

#: `say the number` 用的十进制输出。**只在用到时才发**（没用到就不进产物 ——
#: 产物要能与人手写的那份**逐字节**比，多一个函数就比不上了）。
#:
#: `let zero: i64 = 0;` 那一句**不是啰嗦**，是必须的：见文件头那条"踩出来的坑"。
_WRITE_NUM = '''fn nl_write_num(fd: u64, v: i64) {
    let buf: ptr = alloc(24);
    let start: u32 = 0;
    let x: u64 = 0;
    let zero: i64 = 0;
    if v < zero {
        store8(buf, 0, 45 as u8);
        start = 1;
        x = (zero - v) as u64;
    } else {
        x = v as u64;
    }
    let n: u32 = start;
    if x == 0 {
        store8(buf, n, 48 as u8);
        n = n + 1;
    }
    while x > 0 {
        store8(buf, n, (48 + ((x % 10) as u32)) as u8);
        x = x / 10;
        n = n + 1;
    }
    let i: u32 = 0;
    while i < (n - start) / 2 {
        let lo: u8 = load8(buf, start + i) as u8;
        let hi: u8 = load8(buf, n - 1 - i) as u8;
        store8(buf, start + i, hi);
        store8(buf, n - 1 - i, lo);
        i = i + 1;
    }
    syscall4(1, fd, buf as u64, n as u64);
}
'''

_IND = "    "


class Emitter:
    def __init__(self, rets: dict[str, str], consts: dict[str, str], path: str):
        self.rets, self.consts, self.path = rets, consts, path
        self.used_write_num = False
        self.cur_fn = ""

    # ---- 表达式

    def ex(self, e: tuple) -> str:
        k = e[0]
        if k == "num":
            return e[1]
        if k == "str":
            return e[1]
        if k == "bool":
            return e[1]
        if k == "name":
            return e[1]
        if k == "bin":
            return f"({self.ex(e[2])} {e[1]} {self.ex(e[3])})"
        if k == "un":
            return f"({e[1]}{self.ex(e[2])})"
        if k == "cast":
            return f"({self.ex(e[1])} as {e[2]})"
        if k == "field":
            return f"({self.ex(e[1])}.{e[2]})"
        if k == "call":
            return f"{e[1]}({', '.join(self.ex(a) for a in e[2])})"
        raise AssertionError(f"发射器不认识这个节点 {k!r}")  # pragma: no cover

    # ---- 语句

    def stmts(self, body: list, depth: int) -> list[str]:
        pad = _IND * depth
        out: list[str] = []
        for s in body:
            k = s[0]
            if k == "let":
                out.append(f"{pad}let {s[1]}: {s[3]} = {self.ex(s[2])};")
            elif k == "letbuf":
                out.append(f"{pad}let {s[1]}: ptr = alloc({self.ex(s[2])});")
            elif k == "set":
                out.append(f"{pad}{s[1]} = {self.ex(s[2])};")
            elif k == "ret":
                out.append(f"{pad}return {self.ex(s[1])};")
            elif k == "do":
                out.append(f"{pad}{self.ex(s[1])};")
            elif k == "say":
                v = self.ex(s[1])
                out.append(f"{pad}syscall4(1, 1, str_ptr({v}) as u64, "
                           f"str_len({v}) as u64);")
            elif k == "saynum":
                # 补 `as i64` 也是**降级**（与 `talk` / `paint` 那两处同一条理由）：
                # "把这个数写出来"与它是 u8 还是 u32 无关 —— 而辅助函数收的是 i64。
                # **恒等转换是合法的**（实测 `let x: i64 = a as i64;` 过检查），
                # 所以不必先判断宽度。
                self.used_write_num = True
                out.append(f"{pad}nl_write_num(1, {self.ex(s[1])} as i64);")
            elif k == "talk":
                # **四个实参一律补 `as u64`**：`syscall4` 的形参就是 u64，而这一句的
                # 意思**就是** `syscall4` —— 补转换属于"降级"，不属于"翻译"
                # （与 `paint` 那个 `as u8` 同一条理由）。不补的话，传一个 i64
                # 变量进来会在检查时报 E1，而那与作者想说的那句话无关。
                out.append(f"{pad}syscall4({self.ex(s[1])} as u64, "
                           f"{self.ex(s[2])} as u64, {self.ex(s[3])} as u64, "
                           f"{self.ex(s[4])} as u64);")
            elif k == "paint":
                # `store8` 的下标是 u32、值是 u8 —— 两处都补，理由同上（降级，不是翻译）。
                out.append(f"{pad}store8({self.ex(s[3])}, {self.ex(s[2])} as u32, "
                           f"{self.ex(s[1])} as u8);")
            elif k == "while":
                out.append(f"{pad}while {self.ex(s[1])} {{")
                out += self.stmts(s[2], depth + 1)
                out.append(f"{pad}}}")
            elif k == "for":
                out.append(f"{pad}for {s[1]} in {self.ex(s[2])}..{self.ex(s[3])} {{")
                out += self.stmts(s[4], depth + 1)
                out.append(f"{pad}}}")
            elif k == "when":
                for j, (cond, arm) in enumerate(s[1]):
                    head = "if" if j == 0 else "} else if"
                    out.append(f"{pad}{head} {self.ex(cond)} {{")
                    out += self.stmts(arm, depth + 1)
                if s[2] is not None:
                    out.append(f"{pad}}} else {{")
                    out += self.stmts(s[2], depth + 1)
                out.append(f"{pad}}}")
            else:  # pragma: no cover - 解析器只会产出上面那些
                raise AssertionError(f"发射器不认识这个语句 {k!r}")
        return out

    def fn(self, f: Fn) -> str:
        self.cur_fn = f.name
        sig = ", ".join(f"{n}: {t}" for n, t in f.params)
        head = f"pub fn {f.name}({sig})"
        if f.ret != "()":
            head += f" -> {f.ret}"
        body = self.stmts(f.stmts, 1)
        if not body:
            return head + " {\n}"
        return head + " {\n" + "\n".join(body) + "\n}"


# ---------------------------------------------------------------- 入口

def parse_program(src: str, need_program: bool = True,
                  consts: dict[str, str] | None = None,
                  externs: dict[str, str] | None = None) -> Program:
    """一份**自然语言写法**的源 -> 程序。`potato_from.from_natural` 走这一条。

    `consts` 只在**手上没有 `remember` 那几句**时才要给（`translate` 那条路：
    它拿到的是一串函数正文，常量在 Potato 对象里）；`externs` 同理。

    ## 为什么**读两遍**

    第一遍只为收"函数名 -> 返回类型"这张表，第二遍拿它把类型查全。两遍用的**同一份
    记号流**（第一遍只读不动它），差别只在 `strict_types`：
    `let a be sum_to of LIMIT` 在第一遍时 `sum_to` 的返回类型还不知道，第二遍就知道了。

    不多读这一遍的话，每个"let 一个调用结果"都得写成
    `let a be (sum_to of LIMIT) as a whole number` —— 而**少写就报错**，报的还是
    "调用定不了类型"。那条路是把成本转嫁给用户，而这里**读两遍就够了**。
    """
    toks = tokenize(src)
    seed = dict(externs or {})
    first = Parser(toks, src, consts=consts, calls=seed,
                   strict_types=False).program(need_program)
    calls = dict(seed)
    calls.update({f.name: f.ret for f in first.fns})
    return Parser(toks, src, consts=consts, calls=calls).program(need_program)


def _check_names(prog: Program) -> None:
    """**点名**两种写错：`set` 一个没声明过的名字、调用一个不存在的函数。

    分成两步（先把全部名字收齐再查）是因为单遍扫会把"先用在先、声明在后"判成错的
    —— 那种假红比不查还坏。
    """
    fns = {f.name for f in prog.fns}
    builtin = set(_BUILTINS)
    for f in prog.fns:
        declared = {n for n, _ in f.params}
        for s in _walk(f.stmts):
            if s[0] in ("let", "letbuf"):
                declared.add(s[1])
        for s in _walk(f.stmts):
            if s[0] == "set" and s[1] not in declared:
                # **行号取 `s[-1]`**：`("set", 名字, 表达式, 行号)` 的第三格是表达式 ——
                # 写成 `s[2]` 会把**一个语法树节点**印进"第 … 行"里（实测过）。
                raise Unsupported(
                    f"第 {s[-1]} 行: `set {s[1]} to …`，可 `{s[1]}` 在这个函数里"
                    f"**没有声明过**（`let` 一句都没有）—— 名字写错了，"
                    f"或者这一条该是 `let`")
        for name, line in _calls(f.stmts):
            if name in fns or name in builtin or name == "nl_write_num":
                continue
            raise Unsupported(
                f"第 {line} 行: 调用 `{name}` —— 这一份里没有这个函数"
                f"（内建表见 `.claude/skills/loment/SKILL.md` §3；"
                f"这一版不收 `use`，所以外部函数也调不到）")


def _walk(body: list):
    for s in body:
        yield s
        if s[0] == "when":
            for _c, arm in s[1]:
                yield from _walk(arm)
            if s[2] is not None:
                yield from _walk(s[2])
        elif s[0] == "while":
            yield from _walk(s[2])
        elif s[0] == "for":
            yield from _walk(s[4])


def _calls(body: list):
    """语句里出现的**调用点** `(名字, 行号)`。"""
    out: list[tuple[str, int]] = []
    for s in _walk(body):
        for e, line in _exprs(s):
            out += _calls_in_expr(e, line)
    return out


def _exprs(s: tuple):
    k = s[0]
    if k in ("let", "set"):
        yield s[2], s[-1]
    elif k == "letbuf":
        yield s[2], s[3]
    elif k in ("ret", "do", "say"):
        yield s[1], s[-1]
    elif k == "saynum":
        yield s[1], s[2]
    elif k in ("talk",):
        for e in s[1:5]:
            yield e, s[5]
    elif k == "paint":
        for e in s[1:4]:
            yield e, s[4]
    elif k == "while":
        yield s[1], s[3]
    elif k == "for":
        yield s[2], s[5]
        yield s[3], s[5]
    elif k == "when":
        for c, _arm in s[1]:
            yield c, s[3]


def _calls_in_expr(e: tuple, line: int):
    k = e[0]
    if k == "call":
        yield e[1], line
        for a in e[2]:
            yield from _calls_in_expr(a, line)
    elif k == "bin":
        yield from _calls_in_expr(e[2], line)
        yield from _calls_in_expr(e[3], line)
    elif k in ("un", "cast"):
        yield from _calls_in_expr(e[-1] if k == "un" else e[1], line)
    elif k == "field":
        yield from _calls_in_expr(e[1], line)


def translate(src: str, keep: set[str] | None = None,
              externs: dict[str, str] | None = None,
              consts: dict[str, str] | None = None) -> str:
    """自然语言源码 -> Loment 源码（**只有函数**，`module` 头由调用方加）。

    * `keep` 给了就只翻这些函数 —— 与 `ctrans` / `gotrans` 同一个约定（`lomt_from`
      按它把"要实现的"与"留作 `extern` 的"分开）。
    * `consts` 是模块常量表（`name -> 类型`），由调用方给。这一门**不需要**它来定类型
      （`let` 的类型是写出来的），收下来只为了与另外几门**同一个签名**。
    """
    prog = parse_program(src, need_program=False, consts=consts,
                          externs=externs)
    _check_names(prog)
    rets = {f.name: f.ret for f in prog.fns}
    if externs:
        rets.update(externs)
    seen: set[str] = set()
    for f in prog.fns:
        if f.name in seen:
            raise Unsupported(f"第 {f.line} 行: 函数 `{f.name}` 重名"
                              f"（Loment 没有重载，名字必须精确）")
        seen.add(f.name)
    em = Emitter(rets, consts or {}, "translate")
    out = [em.fn(f) for f in prog.fns if keep is None or f.name in keep]
    text = "\n\n".join(out)
    if text:
        text += "\n"
    if em.used_write_num:
        # **它排在前面**：产物是"一份完整的实现单元"，辅助函数放在被它服务的函数
        # 之前读起来顺；位置也是**确定的**（判据要与手写的那份逐字节比）。
        text = _WRITE_NUM + "\n" + text
    return text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="nltrans",
        description="自然语言写法 -> Loment (docs/197)。出的是**函数**，module 头由调用方加。")
    ap.add_argument("path", help="自然语言写法的源 (.nl)")
    ap.add_argument("--out", metavar="PATH", help="写到文件（默认 stdout）")
    a = ap.parse_args(argv)
    p = Path(a.path)
    try:
        src = p.read_text(encoding="utf-8")
    except OSError as e:
        print(f"nltrans: 读不了 {p}: {e}", file=sys.stderr)
        return 2
    try:
        text = translate(src)
    except Unsupported as e:
        print(f"nltrans: 子集外: {e}", file=sys.stderr)
        return 1
    except NaturalError as e:
        print(f"nltrans: 写法不对: {e}", file=sys.stderr)
        return 1
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
