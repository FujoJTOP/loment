#!/usr/bin/env python3
"""jtrans.py — **Java 写法 -> Loment**（`docs/188` §7.1 六门里的第三门）。

     Java 写法 --jtrans--> Loment

机器在 `tools/trans_core.py`（花括号族的共享核），本模块给它一张 **Java 的方言表**
外加一处**这一族里只有 Java（和 C#）才有的预处理**：把 `class` 外壳抹掉。

## 这一门**比 C 那门简单**，因为语义本来就贴着

`docs/186` §5 说 C 那门最要紧的是"`int` 与 `bool` 不是一回事"。**Java 里那处缝不存在**：

| | C | Java |
|---|---|---|
| `&&` `\|\|` `!` 出什么 | `int`（0/1） | **`boolean`** |
| `if (x)` 收什么 | 标量即可 | **只收 `boolean`** |
| `int x = (a < b);` | 合法 | **类型错** |
| `int` 的宽度 | 平台相关 | **恒为 32 位** ✓ 与 `i32` 一致 |
| `/` 与 `%` | 向零截断 | **向零截断** ✓ 一致 |
| `>>` | 实现定义（x86 上是算术） | **算术** ✓ 与 Loment 一致（实测 `-8 >> 1 == -4`） |

⇒ **这门一个方向的强制转换都不用补**（`coerce_int_to_bool=False`、
`coerce_bool_to_int=False`）。方言表里那两个开关正好是判据"能表达的就转、
表达不出来的就报错"的另一半：Java 的 `int x = (a < b);` 在 Java 里**本来就编不过**，
所以遇到它报错是对的，不该悄悄补一个 `as i32`。

## Java 独有的两处

### ① `class` 外壳要抹掉（`_unwrap`）

Java 的函数住在 `class X { … }` 里，而共享 parser 看的是**顶层**的
`<类型> <名>(…) { … }`。所以先把外壳抹掉：把 `class X … {` 那段头**换成等长空白**
（保留换行），配对的 `}` 也换成空白。

**为什么是"等长空白"而不是"抽出来拼一拼"**：行号与偏移**不变**，于是报错里的
行号就是源里的行号。（同 `potato_from._blank_keep_off` 那个手法。）

**`interface` / `enum` / 嵌套类一律拒** —— 它们不是"外壳"，是另一回事。

### ② `>>>` 是**逻辑**右移，本语言的 `>>` 是**算术**的

所以 `>>>` 不能直接映过去。`trans_core` 里把它**点名拒掉**，理由写在报错里
（要它得写 `(a as u32) >> n`）。**先不收** —— 那是个能表达出来的差，但还没有语料逼它。

## 子集

Java 的类型：`int`→`i32`、`long`→`i64`、`short`→`i16`、`byte`→`i8`、`char`→`u16`、
`boolean`→`bool`、`void`→`()`。**`float` / `double` / `String` 拒**（本语言没有浮点与字符串值）。

修饰词：`public` / `private` / `protected` / `static` / `final` / `synchronized` /
`abstract` **收下并丢掉**（这里翻的是整个单元的全部函数，可见性在这个语境里没有
可分辨的差别）；`native` / `transient` / `volatile` / `strictfp` / `package` / `import`
**拒** —— 它们都真的改语义或本子集表示不了。

用法:

    python tools/jtrans.py SRC.java [--out OUT.lomt]
退出码: 0 = 成功 / 1 = 用到子集外的东西 / 2 = 用法或读取错误。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trans_core import (  # noqa: E402
    CError, Dialect, Unsupported, _BIN, _KW, _PRECOF, main as _main,
    translate as _translate,
)

# ---------------------------------------------------------------- class 外壳

_CLASS_HEAD = re.compile(r"(?<![\w.])(?:public\s+|final\s+|abstract\s+|strictfp\s+)*"
                         r"class\s+([A-Za-z_]\w*)")
#: 长度对齐地抹掉的东西 —— 注释先抹成空白，配花括号才不会数错。
_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)


def _blank_keep_off(m: "re.Match[str]") -> str:
    """换成**等长**空白，且**保留换行** —— 行号与偏移都不动。"""
    return "".join("\n" if ch == "\n" else " " for ch in m.group())


def _match_brace(text: str, open_idx: int) -> int:
    """`{` 的下标 -> 配对 `}` 的下标；找不到返回 -1。

    调用方**必须先把注释抹掉** —— 否则 `/* } */` 会把配对算错。
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


def _unwrap(src: str) -> str:
    """抹掉 `class X … { … }` 的**外壳**，成员就成了顶层。**不动行号。**

    只抹**最外层**那一个（`class` 出现在深度 0 时）—— 嵌套类是另一回事，留给
    `agg` 去拒。`interface` / `enum` 不在这里处理：它们由方言表拒掉。
    """
    sniff = _COMMENT.sub(_blank_keep_off, src)
    out = list(src)
    depth = 0
    i = 0
    while i < len(sniff):
        ch = sniff[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif depth == 0:
            m = _CLASS_HEAD.match(sniff, i)
            if m:
                j = sniff.find("{", m.end())
                if j < 0:
                    raise CError(f"第 {src[:i].count(chr(10)) + 1} 行: `class` 没有体")
                k = _match_brace(sniff, j)
                if k < 0:
                    raise CError(f"第 {src[:i].count(chr(10)) + 1} 行: `class` 的花括号不配平")
                # 头（含 `{`）与尾 `}` 都换成空白 —— 里面的成员原样留下
                for p in range(m.start(), j + 1):
                    if out[p] != "\n":
                        out[p] = " "
                if out[k] != "\n":
                    out[k] = " "
                i = k + 1
                continue
        i += 1
    return "".join(out)


#: Java 的方言表。
JAVA = Dialect(
    name="java",
    types={
        "int": "i32",
        "long": "i64",
        "short": "i16",
        "byte": "i8",
        # Java 的 `char` 是 **16 位无符号**整数（不是一个字符串类型）—— 所以它映 `u16`
        "char": "u16",
        "boolean": "bool",
        "void": "()",
    },
    type_words=frozenset({"int", "long", "short", "byte", "char", "boolean", "void"}),
    spec_words=frozenset({
        "int", "long", "short", "byte", "char", "boolean", "void",
        # 收下并丢掉的修饰词
        "public", "private", "protected", "static", "final", "synchronized", "abstract",
        # 出现就拒的
        "native", "transient", "volatile", "strictfp", "package", "import",
        # 本子集表示不了的类型 —— **认得它们才报得出"这一族的哪个类型不支持"**
        "float", "double", "String", "Object", "var",
    }),
    linkage=frozenset({"public", "private", "protected", "static", "final",
                       "synchronized", "abstract"}),
    bad_spec=frozenset({"native", "transient", "volatile", "strictfp",
                        "package", "import"}),
    #: `enum` / `interface` / 残留的 `class` —— 它们不是"外壳"，是另一回事。
    agg=frozenset({"enum", "interface", "record", "class", "extends", "implements"}),
    #: `_KW` 那批 + Java 自己的语句关键字。不点出来的话 `new Foo()` / `try { }`
    #: 会被当成标识符一路走到"期望 `;`"那种指不到点子的错。
    kw=frozenset(_KW | {"new", "this", "super", "throw", "throws", "try", "catch",
                        "finally", "instanceof", "assert", "yield", "var",
                        "package", "import", "extends", "implements", "synchronized"}),
    bin=dict(_BIN),
    precof=dict(_PRECOF),
    # **这一门两个方向都不补** —— Java 的 `&&` 出 `boolean`、条件只收 `boolean`，
    # 而 `int x = (a < b);` 在 Java 里本来就是类型错。（见文件头那张表。）
    coerce_int_to_bool=False,
    coerce_bool_to_int=False,
    int_default="i32",
    safe_suffix="_j",
    #: Java 的函数住在 `class` 里，而解析器看的是顶层 —— 见文件头 §①。
    pre=_unwrap,
    #: **`static final <类型> NAME = <整数>;` 是顶层常量** —— `potato_from._JAVA_CONST`
    #: 已经把它收进 `consts`，`lomt_from` 会发成 `pub const`。所以翻译器跳过那条
    #: 声明，只要认得那个名字。**标志词是 `final`**（Java 的常量必须写它）。
    const_words=frozenset({"final"}),
)


def translate(src: str, keep: set[str] | None = None,
              externs: dict[str, str] | None = None,
              consts: dict[str, str] | None = None) -> str:
    """Java 写法 -> Loment 源码（**只有函数**，`module` 头由调用方加）。"""
    return _translate(src, JAVA, keep=keep, externs=externs, consts=consts)


def parse(src: str) -> list:
    """只用来看语法树（判据用）。"""
    from trans_core import parse as _parse
    return _parse(src, JAVA)


def main(argv: list[str] | None = None) -> int:
    return _main(JAVA, "Java", argv)


if __name__ == "__main__":
    sys.exit(main())
