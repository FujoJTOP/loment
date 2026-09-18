#!/usr/bin/env python3
"""cstrans.py — **C# 写法 -> Loment**（`docs/188` §7.1 六门里的第四门）。

     C# 写法 --cstrans--> Loment

机器在 `tools/trans_core.py`（花括号族的共享核），本模块给它一张 **C# 的方言表**
外加**两层外壳**的抹除：`namespace N { … }` 与 `class X { … }`。

## 这一门与 Java 几乎是同一张表 —— 只有一格真不同

`&&` / `||` 出 `bool`、条件只收 `bool`、`/` 与 `%` 向零截断、`>>` 是算术的，
`int` 恒 32 位 —— 与 Java 一样，所以两个方向的强制转换**都不用补**
（`coerce_int_to_bool=False`、`coerce_bool_to_int=False`），理由与 `jtrans` 同一句：

> C# 的 `int x = (a < b);` 在 C# 里**本来就编不过** —— 遇到它报错才对，
> 不该悄悄补一个 `as i32`。

**真不同的是 `byte`**：

| | Java | C# |
|---|---|---|
| `byte` 的取值 | **-128..127（有符号）** | **0..255（无符号）** |
| 映成 | `i8` | **`u8`** |

同一族里两门**唯一**不在同一个格子上的类型。C# 那边无符号的一侧还有
`ushort` / `uint` / `ulong`，Java 一个都没有（Java 只有 `char` 是无符号的）。

## 两层外壳：`namespace` 与 `class`

C# 的函数比 Java 又多住一层。两种命名空间写法都要接：

    namespace Foo { class Bar { … } }          // 花括号形
    namespace Foo;                             // C# 10 的文件级（没有体）

抹法是**换成等长空白**（`trans_core.strip_shells`），行号一字不动。**命名空间是
"透明"壳**（里面还会有壳，抹完继续进去找），**`class` 是不透明的**（里面就是成员；
嵌套类是另一回事，留给方言表的 `agg` 去拒）。

## `>>>` 与 Java 同一处

C# 11 起也有 `>>>`（无符号右移），而本语言的 `>>` 是**算术**的 —— 所以它与 Java
一样被共享核**点名拒掉**，理由里带出路（`(a as u32) >> n`）。

## 子集

C# 的类型：`int`→`i32`、`long`→`i64`、`short`→`i16`、`sbyte`→`i8`、`byte`→`u8`、
`ushort`→`u16`、`uint`→`u32`、`ulong`→`u64`、`char`→`u16`、`bool`→`bool`、`void`→`()`。
**`float` / `double` / `decimal` / `string` / `object` 拒**（本语言没有浮点与字符串值）。

修饰词：`public` / `private` / `protected` / `internal` / `static` / `sealed` / `const`
**收下并丢掉**；`abstract` / `virtual` / `override` / `partial` / `unsafe` / `readonly` /
`volatile` / `extern` / `ref` / `out` / `in` / `params` / `this` **拒** —— 它们都真的改语义，
或者本子集表示不了。

## 一条**已知边界**：不做整数宽度跟踪

共享的发射器只分 `int` / `bool` / `void` 三档（`Emitter.ty_of`），**看不出 `u8` 与 `i32`
的区别**。于是 `int f(byte b) { return b; }` 那种**混宽度**的单元翻出来的 Loment 是
`pub fn f(b: u8) -> i32 { return b; }` —— 它在 `lomentc.check` 上会**报错**
（实测：`return 类型 u8，函数 bucket 声明 i32`），**不是静默算错**。

那是这一版的边界、不是漏洞：**响亮地失败**是条红线，"少算一步却照样编过"才是。
宽度跟踪（C# 的小整数算术本来就会提升到 `int`）留到下一版。

用法:

    python tools/cstrans.py SRC.cs [--out OUT.lomt]
退出码: 0 = 成功 / 1 = 用到子集外的东西 / 2 = 用法或读取错误。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trans_core import (  # noqa: E402
    CError, Dialect, Unsupported, _BIN, _KW, _PRECOF, main as _main,
    strip_shells, translate as _translate,
)

# ---------------------------------------------------------------- 两层外壳
#
# **尾巴那个 `{` 吃进正则**（`[^{;]*\{`），于是 `{` 的下标就是 `m.end() - 1` ——
# 顺带把两种"没有体"的写法排除掉：`namespace Foo;`（文件级，下面单独一条）与
# `class Foo;`（本来就不合法）。
#
# 修饰词**只列愿意丢掉的那些**（`linkage` 里除 `const` 外的那批）。`abstract` /
# `unsafe` / `partial` **故意不列** —— 它们不在这里被抹掉，于是会落到 `spec()` 的
# `bad_spec` 上、报出"不支持存储类/限定符"，而不是被静默吞掉。
_MOD = r"(?:public\s+|private\s+|protected\s+|internal\s+|static\s+|sealed\s+)*"

#: `class X { … }` —— **不透明**壳：里面就是成员。
_CS_CLASS_HEAD = re.compile(r"(?<![\w.])" + _MOD + r"class\s+[A-Za-z_]\w*[^{;]*\{")
#: `namespace A.B { … }` —— **透明**壳：里面还会有壳（命名空间能套）。
_CS_NS_HEAD = re.compile(r"(?<![\w.])(?:global\s+)?" + _MOD
                         + r"namespace\s+[A-Za-z_][\w.]*\s*\{")
#: C# 10 的**文件级** `namespace Foo;` —— 没有体，一路抹到 `;` 就行。
_CS_NS_DECL = re.compile(r"(?<![\w.])(?:global\s+)?" + _MOD
                         + r"namespace\s+[A-Za-z_][\w.]*\s*;")
#: `using System;` / `using static System.Math;` / `using IO = System.IO;`。
#: **只抹指令那条形状，不抹 `using` 语句** —— `using (var f = …) { … }` 是另一回事
#: （它管释放），所以那条正则要求 `using` 后面**直接**跟命名空间、并以 `;` 收尾；
#: 语句那条走不到这里，落到解析器上由方言表的 `kw` 点名拒掉。
#:
#: 抹掉是保义的：这一层只翻函数，而命名空间影响的是**名字解析** —— 真用了
#: `Math.Abs(x)` 那种，翻译器会在"属性访问"那一关报出来（本子集只有标量与自由函数）。
_CS_USING = re.compile(r"(?<![\w.])using[ \t]+(?:static[ \t]+)?"
                       r"(?:[A-Za-z_]\w*[ \t]*=[ \t]*)?[A-Za-z_][\w.]*[ \t]*;")

#: `(名字, 正则, 透明, 有体)` —— 见 `trans_core.strip_shells`。
_CS_SHELLS = [
    ("using 指令", _CS_USING, False, False),
    ("文件级 namespace", _CS_NS_DECL, False, False),
    ("namespace", _CS_NS_HEAD, True, True),
    ("class", _CS_CLASS_HEAD, False, True),
]


def _unwrap(src: str) -> str:
    """抹掉 `namespace` 与 `class` 两层壳，成员就成了顶层。**不动行号。**"""
    return strip_shells(src, _CS_SHELLS)


#: C# 的方言表。
CSHARP = Dialect(
    name="csharp",
    types={
        "sbyte": "i8",
        # **C# 的 `byte` 是 0..255（无符号）** —— Java 的 `byte` 是 -128..127（有符号）。
        # 这是这一族里两门**唯一**没落在同一格的类型（见文件头那张表）。
        "byte": "u8",
        "short": "i16", "ushort": "u16",
        "int": "i32", "uint": "u32",
        "long": "i64", "ulong": "u64",
        # C# 的 `char` 是一个 UTF-16 码元 —— 16 位**无符号**整数，不是一个字符串类型
        "char": "u16",
        "bool": "bool",
        "void": "()",
    },
    type_words=frozenset({
        "sbyte", "byte", "short", "ushort", "int", "uint", "long", "ulong",
        "char", "bool", "void",
    }),
    #: 能出现在"类型位置"的**全部**词 —— 类型 + 愿意丢掉的修饰词 + 出现就拒的 +
    #: 本子集表示不了的类型 + 聚合/枚举。**认得它们才报得出对的错**：
    #: 不认的话 `readonly int x` 会报"期望类型名"，而真正的原因是"不支持那个限定符"。
    spec_words=frozenset({
        "sbyte", "byte", "short", "ushort", "int", "uint", "long", "ulong",
        "char", "bool", "void",
        # 收下并丢掉的修饰词
        "public", "private", "protected", "internal", "static", "sealed", "const",
        # 出现就拒的
        "abstract", "virtual", "override", "partial", "unsafe", "readonly",
        "volatile", "extern", "ref", "out", "in", "params", "this", "base",
        "delegate", "event", "operator", "async", "fixed", "stackalloc",
        # 本子集表示不了的类型
        "string", "object", "decimal", "float", "double", "nint", "nuint",
        "var", "dynamic",
        # 聚合/枚举 —— 也放进来，这样报的是**具体**那句（见方言表的 `agg`）
        "class", "struct", "interface", "record", "enum", "namespace",
    }),
    linkage=frozenset({"public", "private", "protected", "internal", "static",
                       "sealed", "const"}),
    bad_spec=frozenset({"abstract", "virtual", "override", "partial", "unsafe",
                        "readonly", "volatile", "extern", "ref", "out", "in",
                        "params", "this", "base", "delegate", "event", "operator",
                        "async", "fixed", "stackalloc"}),
    #: 它们不是"外壳"（外壳已经被 `_unwrap` 抹掉了），是另一回事。
    agg=frozenset({"class", "struct", "interface", "record", "enum", "namespace"}),
    #: 与 Java 那份同源：C# 的 `switch`/`try`/`foreach`/`lock`/`using` 之类
    #: 在表达式位置出现必然是写错了或子集外，早点名比"期望 `;`"强。
    kw=frozenset(_KW | {"new", "this", "base", "throw", "try", "catch", "finally",
                        "foreach", "lock", "using", "checked", "unchecked", "unsafe",
                        "fixed", "stackalloc", "typeof", "nameof", "is", "as",
                        "await", "async", "yield", "delegate", "event", "operator",
                        "in", "out", "ref", "params", "var"}),
    bin=dict(_BIN),
    precof=dict(_PRECOF),
    # **两个方向都不补** —— 与 Java 同一个理由（见文件头）。
    coerce_int_to_bool=False,
    coerce_bool_to_int=False,
    int_default="i32",
    safe_suffix="_cs",
    #: 函数住在 `namespace` + `class` 里，而解析器看的是顶层 —— 见文件头。
    pre=_unwrap,
    #: **`const <类型> NAME = <整数>;` 是顶层常量**（C# 的 `const` 隐含 `static`）——
    #: `potato_from` 的 `_CS_CONST` 已经把它收进 `consts`，`lomt_from` 会发成
    #: `pub const`。所以翻译器跳过那条声明，只要认得那个名字。
    const_words=frozenset({"const"}),
)


def translate(src: str, keep: set[str] | None = None,
              externs: dict[str, str] | None = None,
              consts: dict[str, str] | None = None) -> str:
    """C# 写法 -> Loment 源码（**只有函数**，`module` 头由调用方加）。"""
    return _translate(src, CSHARP, keep=keep, externs=externs, consts=consts)


def parse(src: str) -> list:
    """只用来看语法树（判据用）。"""
    from trans_core import parse as _parse
    return _parse(src, CSHARP)


def main(argv: list[str] | None = None) -> int:
    return _main(CSHARP, "C#", argv)


if __name__ == "__main__":
    sys.exit(main())
