#!/usr/bin/env python3
# potato_from.py — Python/C/Rust 源码 -> Potato 形式对象 转写器 (M49, docs/147)
#
# 定位 (docs/140 §9): 源语言的语法属于源语言, 表示属于 Potato。
# 本工具是"低收益工具链"臂: 只做结构识别, 不做语义等价证明。strict 臂不猜测 ——
# 映射不出类型的实体记入 report.skipped; lenient 臂按固定规则放宽 (未知->ptr, 未标注->i64),
# 因此转换率更高但保真度更低。两臂都确定性可复现, 绝不随机。
#
# 用法:
#   python tools/potato_from.py FILE [--lang auto|python|c|rust] [--mode strict|lenient]
#                                  [--json OUT.json] [--report OUT.report.json]
#   python tools/potato_from.py --corpus MANIFEST.json --out-dir DIR
#
# 退出码: 0 = 全部转写成功 / 1 = 有文件转写失败 / 2 = 用法错误。

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import potato  # noqa: E402

# ---- 源语言类型 -> Potato 类型
PY_TYPES = {"int": "i64", "bool": "bool", "str": "str", "bytes": "ptr", "None": "()"}
C_TYPES = {
    "uint8_t": "u8", "uint16_t": "u16", "uint32_t": "u32", "uint64_t": "u64",
    "int8_t": "i8", "int16_t": "i16", "int32_t": "i32", "int64_t": "i64",
    "unsigned char": "u8", "unsigned short": "u16", "unsigned int": "u32",
    "unsigned long": "u64", "signed char": "i8", "short": "i16", "int": "i32",
    # **`unsigned` / `signed` 单独写就是 `unsigned int` / `signed int`**（C 标准这么定，
    # 不是缩写习惯）。原先这里没有它们，于是 `unsigned f(...)` 的返回类型"无映射"被跳过 ——
    # 而那是最常见的 C 写法之一。2026-09-17 用一份 C 装 `.lomt` 实测撞到的：
    # 5 个函数里 4 个因此被丢，而**报告还被工具扔了**，看起来像"只认出 1 个"。
    # `char` 的符号性在 C 里是实现定义的；x86-64 Linux 上是 i8，照这个来。
    "unsigned": "u32", "signed": "i32", "char": "i8",
    "long": "i64", "size_t": "u64", "ssize_t": "i64", "bool": "bool",
    "_Bool": "bool", "void": "()", "char *": "str", "const char *": "str",
    "void *": "ptr", "uintptr_t": "u64", "intptr_t": "i64",
    # 内核常见 typedef 简写 (结构识别: 与标准名同义)
    "u8": "u8", "u16": "u16", "u32": "u32", "u64": "u64",
    "i8": "i8", "i16": "i16", "i32": "i32", "i64": "i64",
}
RS_TYPES = {
    "u8": "u8", "u16": "u16", "u32": "u32", "u64": "u64",
    "i8": "i8", "i16": "i16", "i32": "i32", "i64": "i64",
    "bool": "bool", "()": "()", "usize": "u64", "isize": "i64",
}
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

GO_TYPES = {
    # `int`/`uint` 在 Go 里是**平台相关**的 (至少 32 位; amd64 上是 64 位)。
    # 这里按**本仓唯一支持的目标** (x86-64) 读 —— 不假装它是精确的:
    # 要精确就别用 `int`, 用 `int32`/`int64`。这条与 C 那边的 `int -> i32` 不同,
    # 是**因为两种语言的规定不同**, 不是为了整齐。
    "int": "i64", "int8": "i8", "int16": "i16", "int32": "i32", "int64": "i64",
    "uint": "u64", "uint8": "u8", "uint16": "u16", "uint32": "u32", "uint64": "u64",
    "byte": "u8", "rune": "i32", "bool": "bool", "uintptr": "u64",
    # Go 的 `string` 就是「指针 + 长度」—— 与 Loment 的 `str` 同形, 所以映 `str`,
    # **不**映 `ptr`。(它不是 C 字符串, 于是过不了 FFI 第 1 阶段那道闸门 —— 那是对的,
    # 见 docs/179 §3。)
    "string": "str",
    # float 不在 Loment 的类型里 —— 显式写出来, 让报错是"无映射"而不是"未知类型"。
    "float32": None, "float64": None, "complex64": None, "complex128": None,
}
#: `func name(...) T {` —— Go 的函数定义。`func` 这个关键字在 C/Rust/Python 里都不出现,
#: 所以它是一条**很干净**的判据。
_GO_FN = re.compile(r"^[ \t]*func[ \t]+([A-Za-z_]\w*)[ \t]*\(([^)]*)\)[ \t]*([^{;\n]*?)[ \t]*\{",
                    re.M)
_GO_STRUCT = re.compile(r"^[ \t]*type[ \t]+([A-Za-z_]\w*)[ \t]+struct[ \t]*\{(.*?)^[ \t]*\}",
                        re.M | re.S)
#: `//export name` —— 只有这样的 Go 函数才是 **C ABI** (cgo 的约定)。
#: 普通 Go 函数走 Go 自己那套调用约定 (与 Rust 的普通 `pub fn` 同理), 照 C ABI 调就是错编。
_GO_EXPORT = re.compile(r"^[ \t]*//[ \t]*export[ \t]+([A-Za-z_]\w*)", re.M)


def _go_type(t: str, mode: str, known: set[str]) -> str | None:
    t = re.sub(r"\s+", " ", t.strip())
    if not t:
        return "()"
    if t.startswith("[]"):
        # 切片是 (ptr, len, cap) 三个字 —— Loment 没有对应形态。**不猜成 ptr**:
        # 猜成 ptr 会丢长度, 调用方按什么切? 报"无映射"让上游决定。
        return None
    if t.endswith("...T") or t.startswith("..."):
        return None                          # 变参
    n = 0
    while t.endswith("*"):
        t = t[:-1].strip()
        n += 1
    if n:
        return "ptr"                         # `*T` 一律 ptr (Go 没有别的指针形态好用)
    if t in known:
        return t
    return GO_TYPES.get(t) if t in GO_TYPES else None


def from_go(src: str, name: str, mode: str = "strict") -> tuple[dict, Report]:
    rep = Report(name, "go", mode)
    doc = _blank(_ident(Path(name).stem), "go")
    # **注释要在取完 `//export` 之后再剥** —— 它本身就是一条注释。
    exported = set(_GO_EXPORT.findall(src))
    body = _C_COMMENT.sub(" ", src)
    known: set[str] = set()
    for m in _GO_STRUCT.finditer(body):
        sname, inner = m.group(1), m.group(2)
        fields = []
        for line in inner.splitlines():
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            parts = line.split()
            if len(parts) < 2 or parts[0].startswith("//"):
                continue
            fname = parts[0]
            if not IDENT_RE.match(fname):
                continue
            t = _go_type(" ".join(parts[1:]), mode, known)
            if t is None:
                rep.skip_field(sname, fname, f"字段类型 {' '.join(parts[1:])!r} 无映射")
                continue
            fields.append({"name": fname, "type": t})
        if not fields:
            rep.skip("type", sname, "无可用字段")
            continue
        doc["types"].append({"name": sname, "fields": fields})
        known.add(sname)
        rep.ok += 1
    for m in _GO_FN.finditer(body):
        fn, params, ret = m.group(1), m.group(2), m.group(3).strip()
        # 只有 `//export` 过的才是 C ABI —— 与 Rust 那侧的 `extern "C"` 同一个道理。
        abi = "c" if fn in exported else "go"
        if ret.startswith("("):
            rep.skip("fn", fn, "多返回值 —— Potato 是单返回")
            continue
        rt = _go_type(ret, mode, known)
        if rt is None:
            rep.skip("fn", fn, f"返回类型 {ret!r} 无映射")
            continue
        ps, bad = [], None
        for raw in [p.strip() for p in params.split(",") if p.strip()]:
            parts = raw.split()
            if len(parts) != 2:
                # `a, b int` 这种分组形参、以及 `f func(int)` 那种类型参数 ——
                # 都在这一档。报出来, 不猜。
                bad = f"形参 {raw!r} 不是 `名字 类型` 两段"
                break
            pn, pt = parts
            if not IDENT_RE.match(pn):
                bad = f"形参名 {pn!r} 非法"
                break
            t = _go_type(pt, mode, known)
            if t is None:
                bad = f"形参类型 {pt!r} 无映射"
                break
            ps.append({"name": pn, "type": t})
        if bad:
            rep.skip("fn", fn, bad)
            continue
        doc["functions"].append({"name": fn, "params": ps, "ret": rt, "abi": abi})
        rep.ok += 1
    return _finish(doc, rep)


JAVA_TYPES = {
    "byte": "i8", "short": "i16", "int": "i32", "long": "i64",
    # `void` 映 `"()"` —— 与 C 那边**同一个口径** (`C_TYPES["void"]`), 发射时就没有 `-> T`。
    # 2026-09-17 补: 原先 JAVA_TYPES 没有 `void`, 于是**每一个 setter**(真实 Java 里占
    # 相当比例)都报"返回类型 'void' 无映射" —— 那句提示是**错的**: void 完全表示得了,
    # 它只是没被加进来。注意这些方法仍然是 `abi: "java"`, 照样不会发 `extern fn`。
    "void": "()",
    # Java 的 `char` 是 **16 位无符号** (不是 C 的 8 位) —— 映 `u16`, 别照 C 的习惯映 i8。
    "char": "u16", "boolean": "bool",
    "String": "str",
    # float/double 不在 Loment 的类型里。
    "float": None, "double": None,
    # **装箱类型不映** (Integer/Long/Boolean…): 它们**可以为 null**, 而 Loment 的整型
    # 不能。映成 i32 就把"可能没有值"这件事丢了 —— 那正是这套表示层最不该丢的东西。
    # 报"无映射"让人显式处理, 而不是给一个看起来能用的 i32。
    "Integer": None, "Long": None, "Short": None, "Byte": None, "Boolean": None,
    "Character": None, "Float": None, "Double": None,
    "Object": None, "List": None, "Map": None,
}
_JAVA_CLASS = re.compile(r"(?:^|\n)[ \t]*(?:public[ \t]+)?(?:final[ \t]+|abstract[ \t]+)*"
                         r"class[ \t]+([A-Za-z_]\w*)[^{;]*\{")
#: 字段 / 方法 / 常量。三者的区别只在修饰符与尾部形状, 所以分开写更好读。
_JAVA_FIELD = re.compile(r"^[ \t]*(?:(?:public|private|protected|static|final|transient"
                         r"|volatile)[ \t]+)*([A-Za-z_][\w.]*(?:<[^>]*>)?(?:\[\])*)[ \t]+"
                         r"([A-Za-z_]\w*)[ \t]*(?:=[^;]*)?;[ \t]*$")
_JAVA_METHOD = re.compile(r"^[ \t]*(?:(?:public|private|protected|static|final|abstract"
                          r"|native|synchronized|default)[ \t]+)*"
                          r"(?:<[^>]+>[ \t]+)?([A-Za-z_][\w.]*(?:<[^>]*>)?(?:\[\])*)[ \t]+"
                          r"([A-Za-z_]\w*)[ \t]*\(([^)]*)\)")
_JAVA_CONST = re.compile(r"^[ \t]*(?:(?:public|private|protected)[ \t]+)?static[ \t]+final[ \t]+"
                         r"([A-Za-z_][\w.]*)[ \t]+([A-Za-z_]\w*)[ \t]*=[ \t]*(-?\d+)[ \t]*;")
_JAVA_ENUM = re.compile(r"(?:^|\n)[ \t]*(?:public[ \t]+)?(?:final[ \t]+|static[ \t]+)*"
                        r"enum[ \t]+([A-Za-z_]\w*)[^{;]*\{")
_STR_LIT = re.compile(r'"(?:[^"\\\n]|\\.)*"')


def _block_end(text: str, open_idx: int) -> int:
    """`{` 的下标 -> 配对 `}` 的下标; 找不到返回 -1。

    调用方**必须先把注释与字符串字面量剥掉** —— 否则 `"{"` 会把配对算错。
    """
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _java_members(body: str) -> list[str]:
    """类体里**顶层**的成员声明 (跳过方法体、内部类那些嵌套块)。

    做法是走一遍花括号配平: 深度 0 上遇到 `;` 或遇到一个完整的 `{...}` 就收一个成员。
    不这么做的话, 方法体里的局部变量会被当成字段 —— 那是**静默把声明抽错**。
    """
    out, cur, depth, start = [], [], 0, None
    i = 0
    while i < len(body):
        c = body[i]
        if c == "{":
            depth += 1
            if depth == 1:
                start = i
        elif c == "}":
            depth -= 1
            if depth == 0 and start is not None:
                cur.append(body[start:i + 1])
                out.append("".join(cur))
                cur, start = [], None
        elif c == ";" and depth == 0:
            cur.append(c)
            out.append("".join(cur))
            cur = []
        elif depth == 0:
            cur.append(c)
        i += 1
    return [" ".join(m.split()) for m in out]


def _java_type(t: str, mode: str, known: set[str]) -> str | None:
    t = re.sub(r"\s+", " ", t.strip())
    if not t:
        return None
    if t.endswith("[]"):
        base = _java_type(t[:-2], mode, known)
        # Java 数组是**动态长度**的 —— 与 Loment 的切片 `[T]` 同义 (不是定长 `[T; N]`)。
        return f"[{base}]" if base else None
    if t in known:
        return t
    if "<" in t:                       # 泛型: 元素类型被擦除, 表示层记不住
        return None
    return JAVA_TYPES.get(t) if t in JAVA_TYPES else None


def from_java(src: str, name: str, mode: str = "strict") -> tuple[dict, Report]:
    """Java 源码 -> 形式对象。

    **Java 的调用约定不是 C ABI** —— JVM 方法是 JVM 的, `native` 方法走 JNI
    (符号名还是 `Java_<类>_<方法>`, 且头两个参数是 `JNIEnv*`/`jobject`)。
    所以这一族与 Python 一样进 `abi: "java"`, **一条都不会被发成 `extern fn`** ——
    那不是缺陷, 是事实 (docs/173 §4: 运行时那一族走**进程桥**)。

    那它有什么用: **把数据结构带过来**。类 -> `types`、`static final` 常量 -> `consts`,
    于是 Loment 侧知道对面那块内存长什么样、桥那头交换的字节怎么解。链接型的语言
    (C/Rust/Go) 给的是**能直接调的函数**, 运行型的给的是**数据形状 + 走桥的签名**。
    """
    rep = Report(name, "java", mode)
    doc = _blank(_ident(Path(name).stem), "java")
    body = _C_COMMENT.sub(" ", _STR_LIT.sub('""', src))
    known: set[str] = set()
    # ---- Java 的 `enum Name { A, B, C }` —— **不是 class**, 所以上面那圈抓不到它。
    # 原先 Java 的枚举**静默消失** (与 C 那边同一个口子)。
    # 变体可以带构造实参 (`B(1)`) 与体 (`C { … }`), 这里只取**名字**: Potato 的 enums
    # 只有变体名。带参/带体的那些在下面按"保真度损失"记一笔, 不假装它们与裸变体一样。
    for m in _JAVA_ENUM.finditer(body):
        ename = m.group(1)
        open_idx = body.rindex("{", m.start(), m.end())
        end = _block_end(body, open_idx)
        if end < 0:
            rep.skip("type", ename, "枚举体配平不了 (花括号不配对)")
            continue
        inner = body[open_idx + 1:end]
        vs, lossy = [], False
        for raw in inner.split(","):
            raw = raw.strip().split()[0] if raw.strip() else ""
            raw = raw.split("(")[0].split("{")[0].strip()
            if not raw:
                continue
            if not IDENT_RE.match(raw):
                continue
            vs.append(raw)
        if "(" in inner or "{" in inner:
            lossy = True
        if not vs:
            rep.skip("type", ename, "枚举无变体")
            continue
        doc["enums"].append({"name": ename, "variants": vs})
        # **枚举名要进 `known`** —— 否则类里一个 `private State s;` 字段查不到这个名字,
        # 被当成"无映射"丢掉。2026-09-17 实测: 产物里明明有 `pub enum State`, 而同一个
        # 文件里引用它的字段却没了 (三个 agent 里 Java 那位报的第三条)。
        # 这一圈**排在类那一圈前面**, 所以同文件内"先声明枚举、后引用它"是通的。
        # **类与类之间的先后依赖仍然不通** (A 引用后声明的 B): 那要预扫一遍名字, 而预扫
        # 会把"字段没抽出来、整类被丢掉"的名字也放进去, 于是引用它的字段过不了校验器的
        # "未声明"。宁可维持现状: 少映射要**出声** (`skip_field`), 不是静默。
        known.add(ename)
        if lossy:
            rep.skip_field(ename, "<变体实参/枚举体>", "只取了变体名 (Potato 的 enums 没有值)")
        rep.ok += 1
    for m in _JAVA_CLASS.finditer(body):
        cname = m.group(1)
        open_idx = body.rindex("{", m.start(), m.end())
        end = _block_end(body, open_idx)
        if end < 0:
            rep.skip("type", cname, "类体配平不了 (花括号不配对)")
            continue
        inner = body[open_idx + 1:end]
        fields, consts, methods = [], [], []
        for mem in _java_members(inner):
            mc = _JAVA_CONST.match(mem)
            if mc:
                consts.append((mc.group(1), mc.group(2), int(mc.group(3))))
                continue
            # **方法要在字段之前判**: 抽象/接口/native 方法**以 `;` 结尾**却带参数表
            # (`public native int j_native(int a);`)。按"以分号结尾 = 字段"先判的话,
            # 它们会掉进字段那一支然后被丢掉 —— 静默丢, 而且丢的恰好是**唯一那类
            # 与外部实现对接的方法**(2026-09-17 实测: Java 的 j_native 一直抽不出来)。
            mm = _JAVA_METHOD.match(mem)
            if mm and "(" in mem and IDENT_RE.match(mm.group(2)):
                methods.append((mm.group(1), mm.group(2), mm.group(3)))
                continue
            if mem.rstrip().endswith(";"):
                mf = _JAVA_FIELD.match(mem)
                if mf and IDENT_RE.match(mf.group(2)):
                    fields.append((mf.group(1), mf.group(2)))
        # ---- 常量 (类里先取, 因为它们的类型也会进 known 的判断)
        for ty, cn, val in consts:
            if cn in {c["name"] for c in doc["consts"]}:
                continue
            t = _java_type(ty, mode, known)
            if t is None or t not in ("i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64"):
                rep.skip("const", cn, f"常量类型 {ty!r} 不是整型 (Loment 常量只收整型)")
                continue
            doc["consts"].append({"name": cn, "type": t, "value": val})
            rep.ok += 1
        # ---- 类 -> struct
        flds = []
        for fty, fname in fields:
            t = _java_type(fty, mode, known)
            if t is None:
                rep.skip_field(cname, fname, f"字段类型 {fty!r} 无映射")
                continue
            flds.append({"name": fname, "type": t})
        if flds:
            doc["types"].append({"name": cname, "fields": flds})
            known.add(cname)
            rep.ok += 1
        else:
            rep.skip("type", cname, "无可用字段")
        # ---- 方法 -> 函数 (abi=java)
        for rty, mname, params in methods:
            if mname == cname:
                rep.skip("fn", mname, "构造器")
                continue
            rt = _java_type(rty, mode, known)
            if rt is None:
                rep.skip("fn", mname, f"返回类型 {rty!r} 无映射")
                continue
            ps, bad = [], None
            for raw in [p.strip() for p in params.split(",") if p.strip()]:
                parts = raw.split()
                if len(parts) < 2:
                    bad = f"形参 {raw!r} 不是 `类型 名字`"
                    break
                pn = parts[-1]
                pt = " ".join(parts[:-1])
                if not IDENT_RE.match(pn):
                    bad = f"形参名 {pn!r} 非法"
                    break
                t = _java_type(pt, mode, known)
                if t is None:
                    bad = f"形参类型 {pt!r} 无映射"
                    break
                ps.append({"name": pn, "type": t})
            if bad:
                rep.skip("fn", mname, bad)
                continue
            doc["functions"].append({"name": mname, "params": ps, "ret": rt, "abi": "java"})
            rep.ok += 1
    return _finish(doc, rep)


# ---------------------------------------------------------------- 语法识别 (按内容)
#
# docs/175 §5 那条: "**非 Loment 源语法的 `.lomt` 文件**" —— 后缀说的是"这是 Loment 的
# 源文件", 而那种文件里装的东西可以是**别的语法**。只按后缀判的话, 一份写着 C 的 `.lomt`
# 会走进 Loment 解析器, 得到 `1:1: 期望 module（文件必须以 module 开头），得到 'int'`
# —— 一条**把人引向错方向**的建议: 它会让你去改那一行的写法, 而那份 C 的语法本来就对,
# 只是它不是 Loment (2026-09-17 用一份 C 装 `.lomt` 实测到的)。
#
# **判据顺序就是优先级**, 先认最专有的特征:
#   ① `module <名字>` / `capability`·`guard`·`excluded` —— Loment 独有
#   ② `def` / `import` / `class`                        —— Python
#   ③ `fn` / `#[…]`                                     —— Rust
#   ④ `#include` 之类预处理指令                          —— C
#   ⑤ 函数定义的**形状** `<类型> <名字>(…)`              —— 放在最后, 因为它是启发式
#
# **它不假装完备**: 认不出就返回空串, 调用方据此报"请用 `--lang` 指明",
# 而不是猜一个再去编 (猜错比认不出更坏 —— 见 lomelf 头注那条"把静默错编改成报错退出")。
_LOMENT_MODULE = re.compile(r"^[ \t]*module[ \t]+[A-Za-z_]\w*", re.M)
_LOMENT_OWN = re.compile(r"^[ \t]*(?:pub[ \t]+)?(?:capability|guard)\b|^[ \t]*excluded[ \t]+\"",
                         re.M)
#: **`class` 在 Python 与 Java 里都出现**, 所以两条判据按**结尾字符**分开:
#: Python 的类是 `class X:` / `class X(Base):`(冒号), Java 的类是 `class X {`(花括号)。
#: 只认"class"这个词会把两种语言混成一种 —— 那是最容易犯、也最难发现的一类错。
#: **`import` 这条要长成 Python 的样子**: `import java.util.List;` 也是 import ——
#: 与 Python 撞车, 而 Python 排在 Java 前面, 于是一份**带 import 的真 Java 文件在内容
#: 兜底时被判成 python**, 接着 `ast.parse` 在 `package a.b;` 上**抛一坨 traceback**。
#: 判别点是**行尾分号**: Python 的 import 语句不以 `;` 结尾 (写了也是罕见病),
#: Java 的必然以 `;` 结尾。所以这里用 `[^\n;]*$` 把带分号的那行排除掉。
#: 2026-09-17 实测撞到 (三个 agent 写语料, Java 那一份一个 `import` 都不敢写)。
_PY_DEF = re.compile(r"^[ \t]*(?:async[ \t]+)?def[ \t]+\w+"
                     r"|^[ \t]*class[ \t]+\w+[^\n{]*:[ \t]*$"
                     r"|^[ \t]*(?:import|from)[ \t]+\w[^\n;]*$", re.M)
_RS_FN = re.compile(r"^[ \t]*(?:pub[ \t]+)?(?:unsafe[ \t]+)?(?:async[ \t]+)?"
                    r"(?:extern[ \t]+\"[^\"]*\"[ \t]+)?fn[ \t]+\w+", re.M)
#: Go: `func` 这个关键字在 C/Rust/Python/Java 里都不出现, 是一条很干净的判据。
_GO_DEF = re.compile(r"^[ \t]*func[ \t]+\w+", re.M)
#: **`package` 在 Go 与 Java 里都有** —— 按**有没有分号**分开: Go 是 `package main`,
#: Java 是 `package com.example;`。不分开的话一份 Java 源码会被认成 Go
#: (2026-09-17 实测: 加 Java 之后 detect_lang 对 Java 文件返回 'go')。
_GO_PKG = re.compile(r"^[ \t]*package[ \t]+[A-Za-z_]\w*[ \t]*$", re.M)
#: Java: `class` 前面带可见性/修饰符, 或者 `package a.b.c;` 开头。
#: **`import java.util.List;` 这种也是 Java, 但它与 Python 的 `import x` 撞车** ——
#: 所以 Java 只认 `package ...;` 与 `class`/`interface` 声明, 不认裸 `import`。
#:
#: **`enum` 不算 Java 判据**: `enum X { A, B }` 在 **C、Java、Rust 里长得一模一样**
#: (C 只是多一个行尾分号, 而那一行常常换个写法)。把它当 Java 的特征, 一份带枚举的 C
#: 源码就会被判成 java —— 2026-09-17 实测撞到 (给 C 夹具加了个 `enum Mode` 之后
#: `c_8_detected` 立刻红)。判据只认**专有**的东西, 这个不专有。
#: 代价: 只有枚举、没有 class 的 Java 文件认不出 (用户用 `--lang java` 指明)。
_JAVA_DEF = re.compile(r"^[ \t]*(?:public[ \t]+|final[ \t]+|abstract[ \t]+)*(?:class|interface)"
                       r"[ \t]+\w+[^\n{]*\{"
                       r"|^[ \t]*package[ \t]+[\w.]+[ \t]*;", re.M)
_C_PRE = re.compile(r"^[ \t]*#[ \t]*(?:include|define|ifdef|ifndef|pragma|endif|elif)\b", re.M)
#: 一行只有"类型 + 名字 + 参数表"(行尾可选 `{`) —— C 的函数定义 (K&R 之后)。
#: 排除 `return`/`if`/`while`/`for`/`switch`/`else` 开头, 免得把语句或调用当定义。
_C_FNDEF = re.compile(r"^[ \t]*(?!(?:return|if|while|for|switch|else)\b)"
                      r"[A-Za-z_][\w \t\*]*\*?[A-Za-z_]\w*[ \t]*\([^;{}\"()]*\)[ \t]*\{?[ \t]*$",
                      re.M)
#: 整份 C **一行写完** (`int add(int a, int b) { return a + b; }`) —— 上面那条要求行尾就是
#: `)`, 所以它漏这一种, 而那是最常见的写法之一。
#:
#: **只认 C 的类型关键字开头**, 这是刻意的: Loment 的一行函数长成 `fn f() { return 1; }`,
#: 而 `fn`/`struct`/`enum`/`const`/`capability` 都不在下面这张表里 —— 于是两种语言不会在这里
#: 撞车。(带 `struct` 的 C 定义走 `_C_STRUCT`, 不靠这一条。)
_C_TYPES_LEAD = ("void", "int", "char", "short", "long", "float", "double",
                 "unsigned", "signed", "static", "inline", "size_t")
_C_ONELINE = re.compile(r"^[ \t]*(?:" + "|".join(_C_TYPES_LEAD) + r")\b"
                        r"[^;{}]*\([^;{}]*\)[ \t]*\{.*\}[ \t]*;?[ \t]*$", re.M)


#: 判语法前先把注释去掉 —— 注释里出现 `fn`/`def`/`module` 会把人骗过去。
#: (C 的 `/* */`、`//`; Python 的 `#` 不是 `_C_PRE` 那种 `#include`, 留着无所谓)
_ANY_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
#: **只有结构体、没有函数**的文件也要认得出 —— 而 `struct X {` 在 Rust 与 C 里都有。
#: 按**字段写法**分: Rust 是 `名字: 类型`, C 是 `类型 名字;`。这是唯一可靠的区分点
#: (`pub` 可有可无, 所以不能靠它)。2026-09-17 加多语法判据时撞到: 一份只有
#: `struct T { x: i32 }` 的 Rust 源四种特征全不命中。
_RS_STRUCT_MARK = re.compile(r"^[ \t]*(?:pub[ \t]+)?struct[ \t]+\w+[^{]*\{[^}]*?"
                             r"^\s*\w+[ \t]*:[ \t]*\w", re.M | re.S)
_C_STRUCT_MARK = re.compile(r"^[ \t]*(?:typedef[ \t]+)?struct[ \t]+\w*[^{]*\{[^}]*?"
                            r"\b[A-Za-z_]\w*[ \t]+[A-Za-z_]\w*[ \t]*;", re.M | re.S)


def detect_lang(src: str) -> tuple[str, str]:
    """按**内容**判断源语法。返回 `(语言, 依据)`; 认不出返回 `("", 原因)`。

    语言取值与 `LANGS` 的键一致 (另加 `"loment"` —— 那表示"这本来就是 Loment 源,
    不该走前端")。
    """
    body = _ANY_COMMENT.sub(" ", src)
    if _LOMENT_MODULE.search(body):
        return "loment", "以 `module <名字>` 开头"
    if _LOMENT_OWN.search(body):
        return "loment", "有 `capability` / `guard` / `excluded` —— Loment 独有"
    if _PY_DEF.search(body):
        return "python", "有 `def` / `class` / `import`"
    if _RS_FN.search(body):
        return "rust", "有 `fn`"
    if _GO_DEF.search(body) or _GO_PKG.search(body):
        return "go", "有 `func` / `package`"
    if _JAVA_DEF.search(body):
        return "java", "有 `class` / `interface` / `package …;`"
    if _C_PRE.search(body):
        return "c", "有 `#include` 之类预处理指令"
    # 到这里剩下的多半是"只有声明没有函数"的文件 —— 按结构体字段的写法分。
    if _RS_STRUCT_MARK.search(body):
        return "rust", "有 `struct X { 名字: 类型 }` 形状的字段 (Rust 写法)"
    if _C_STRUCT_MARK.search(body):
        return "c", "有 `struct X { 类型 名字; }` 形状的字段 (C 写法)"
    if _C_FNDEF.search(body):
        return "c", "有 `<类型> <名字>(…)` 形状的函数定义, 且没有 `fn`/`def`"
    if _C_ONELINE.search(body):
        return "c", "有整行写完的 C 函数定义 (`<类型关键字> …(…) { … }`)"
    return "", "已知特征都没命中"


def _ident(name: str, fallback: str = "unit") -> str:
    name = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if not name or not name[0].isalpha() and name[0] != "_":
        name = "_" + name
    return name if IDENT_RE.match(name) else fallback


class Report:
    """转写报告: 实体 = 函数/类型/常量 (字段不计实体, 单独记保真度损失)。"""

    def __init__(self, path: str, language: str, mode: str):
        self.path, self.language, self.mode = path, language, mode
        self.ok = 0
        self.skipped: list[dict] = []
        self.field_notes: list[dict] = []
        self.validate_errors: list[str] = []

    def skip(self, kind: str, name: str, why: str) -> None:
        self.skipped.append({"kind": kind, "name": name, "why": why})

    def skip_field(self, owner: str, field: str, why: str) -> None:
        """类型内字段映射失败: 不计入实体分母, 只记保真度损失。"""
        self.field_notes.append({"owner": owner, "field": field, "why": why})

    @property
    def seen(self) -> int:
        return self.ok + len(self.skipped)

    def as_dict(self, obj_ok: bool) -> dict:
        return {
            "path": self.path, "language": self.language, "mode": self.mode,
            "entities_seen": self.seen, "entities_ok": self.ok,
            "conversion_rate": round(self.ok / self.seen, 4) if self.seen else 0.0,
            "skipped": self.skipped, "fields_skipped": len(self.field_notes),
            "field_notes": self.field_notes, "object_valid": obj_ok,
            "validate_errors": self.validate_errors,
        }


def _finish(doc: dict, rep: Report) -> tuple[dict, Report]:
    errs = potato.validate(doc)
    rep.validate_errors = errs
    return doc, rep


def _blank(unit: str, language: str) -> dict:
    """一份空的**合法**对象。

    停在 **v1** 而不是跟着编译器升到 v2 (`docs/178`): v2 的 `mode` 是"这个 Loment 程序
    用哪个运行模式", 而这里的对象描述的是**外源模块**的结构 —— 它不是一个 Loment 程序,
    给它填 `std` 就是**替它声称**一件源里没有的事。v1 仍然合法 (校验器收 v0/v1/v2),
    "外源结构对象不声称程序模式"这条边界因此留在数据里。

    **`guards: 0` 不能漏**(2026-09-17 修): v1 要求 `guards` 是**非负整数**, 而原先这里
    没有这个键 —— 于是 `potato_from` 发出的每一份对象都是**非法 v1**, `validate_errors`
    里一直挂着"N 项校验错误"。这是"没有判据盯着"的典型: 那个字段被**报到报告里**,
    但从来没有任何测试断言报告是干净的。补判据见 `loment_multisyntax_test`。
    """
    return {
        "potato": "v1", "unit": unit, "language": language, "imports": [],
        "capabilities": [], "functions": [], "layouts": [], "consts": [], "enums": [],
        "types": [], "traits": [], "impls": [], "generics": [], "instances": [],
        "guards": 0, "excluded": [],
    }


# ---------------------------------------------------------------- Python (ast)

def _py_type(node, mode: str) -> str | None:
    if node is None:
        return "i64" if mode == "lenient" else None
    txt = ast.unparse(node) if hasattr(ast, "unparse") else ""
    if txt in PY_TYPES:
        return PY_TYPES[txt]
    return "ptr" if mode == "lenient" else None  # lenient: 未知类型降级为指针


#: 前端**自己**折叠整数表达式, 与 C enum 那处同一口径 (`_C_ENUM` 也要算出值来)。
#: 只收"所有叶子都是整数字面量"的表达式 —— 有一个叶子是名字 (如 `1 << SHIFT`)
#: 就**报出来**, 不猜它的值。
#: `ast.Div` **不在**表里: 它产生 float, 折出来就不是整数常量了。
_PY_FOLD_OPS = (ast.Add, ast.Sub, ast.Mult, ast.FloorDiv, ast.Mod,
                ast.LShift, ast.RShift, ast.BitOr, ast.BitAnd, ast.BitXor)
_PY_INT_TYPES = {"i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64"}


def _py_int_literal(node) -> int | None:
    """整数字面量, **含** `-5` / `1 << 4` 这类显然可折叠的形式。折不出来返回 None。"""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, int) and not isinstance(node.value, bool):
            return node.value
        return None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _py_int_literal(node.operand)
        if v is None:
            return None
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp) and isinstance(node.op, _PY_FOLD_OPS):
        a, b = _py_int_literal(node.left), _py_int_literal(node.right)
        if a is None or b is None:
            return None
        try:
            if isinstance(node.op, ast.Add):
                return a + b
            if isinstance(node.op, ast.Sub):
                return a - b
            if isinstance(node.op, ast.Mult):
                return a * b
            if isinstance(node.op, ast.FloorDiv):
                return a // b
            if isinstance(node.op, ast.Mod):
                return a % b
            if isinstance(node.op, ast.LShift):
                return a << b
            if isinstance(node.op, ast.RShift):
                return a >> b
            if isinstance(node.op, ast.BitOr):
                return a | b
            if isinstance(node.op, ast.BitAnd):
                return a & b
            return a ^ b
        except (ZeroDivisionError, ValueError, OverflowError):
            return None                       # 除零 / 负位移 / 位移过大 -> 当折不出来
    return None


def _py_const(name: str, value, ann, mode: str) -> tuple[dict | None, str]:
    """模块级 `NAME = <整数>` 或 `NAME: int = <整数>` -> 一条常量。

    第二个返回值非空时**调用方一定要出声** —— 这条函数的存在理由就是原先那三种写法
    **一声不响地消失** (2026-09-17 加多语法语料时实测):

      * `LOW = -5`      —— `-5` 是 `UnaryOp`, 不是 `Constant`
      * `MAX: int = 8`  —— `AnnAssign`, 而这是**最 Pythonic 的写法**
      * `SHIFT = 1 << 4` —— `BinOp`

    产物里少三个常量、汇总行一个数都不变、`[skip]` 一行都没有 —— 不看产物根本发现不了。
    **注解写了非整型就报出来**, 不能照值发 `i64`: 那会把作者声明的类型改掉。
    """
    v = _py_int_literal(value)
    if v is None:
        return None, "模块级常量的值不是整数字面量"
    if ann is not None:
        t = _py_type(ann, mode)
        if t not in _PY_INT_TYPES:
            return None, f"常量注解 {t or '无映射'} 不是整型"
    return {"name": name, "type": "i64", "value": v}, ""


def from_python(src: str, name: str, mode: str = "strict") -> tuple[dict, Report]:
    rep = Report(name, "python", mode)
    doc = _blank(_ident(Path(name).stem), "python")
    tree = ast.parse(src)
    known: set[str] = set()

    def _put_const(cname: str, cval, cann) -> None:
        c, why = _py_const(cname, cval, cann, mode)
        if c:
            doc["consts"].append(c)
            rep.ok += 1
        else:
            rep.skip("const", cname, why)

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            fields = []
            for st in node.body:
                if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                    t = _py_type(st.annotation, mode)
                    if t is None:
                        rep.skip_field(node.name, st.target.id, "无可用类型映射")
                        continue
                    fields.append({"name": st.target.id, "type": t})
            if not fields:
                rep.skip("type", node.name, "无注解字段")
                continue
            doc["types"].append({"name": node.name, "fields": fields})
            known.add(node.name)
            rep.ok += 1
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.args.vararg or node.args.kwarg or node.args.posonlyargs:
                rep.skip("fn", node.name, "变参/位置参数")
                continue
            params, bad = [], None
            for a in list(node.args.args) + list(node.args.kwonlyargs):
                t = _py_type(a.annotation, mode)
                if t is None:
                    bad = f"参数 {a.arg} 无类型映射"
                    break
                params.append({"name": a.arg, "type": t})
            if bad:
                rep.skip("fn", node.name, bad)
                continue
            ret = _py_type(node.returns, mode)
            if ret is None:
                rep.skip("fn", node.name, "返回类型无映射")
                continue
            #: Python 的调用约定是自己那套 (CPython C-API / 解释器), 不是平台 C ABI ——
            #: 记下来, 让下游知道它**不能**直接发 `extern fn`。要调 Python 走进程桥
            #: (`loment/lib/proc.lomt`, docs/173 §4)。
            doc["functions"].append({"name": node.name, "params": params, "ret": ret,
                                     "abi": "python"})
            rep.ok += 1
        # 模块级常量两种写法都收。**`isupper()` 是"这是不是常量"的判据**, 放在这里
        # 而不是 `_py_const` 里 —— 模块级小写赋值是变量, 不是"没转成功的常量", 报它
        # 只会淹掉真正的丢失。
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id.isupper():
            _put_const(node.targets[0].id, node.value, None)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id.isupper():
            _put_const(node.target.id, node.value, node.annotation)
    return _finish(doc, rep)


# ---------------------------------------------------------------- C (轻量解析)

_C_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_C_STRUCT = re.compile(r"\bstruct\s+([A-Za-z_]\w*)\s*\{([^}]*)\}", re.S)
#: C 的 `enum Name { A, B = 3, C };`。C **没有 enum 类型名以外的身份** ——
#: 变体名是模块级常量, 所以两处都要看:
#:   * 不带值的 -> `enums`(Potato 的 enums 只有变体名)
#:   * **带值的 -> `consts`** —— 值没法塞进 enums 的 schema, 而它常常是**协议常量**,
#:     丢值比丢名严重得多 (2026-09-17 补: 原先 C 的 enum 谁都不收, **静默消失**)。
_C_ENUM = re.compile(r"\benum\s+([A-Za-z_]\w*)\s*\{([^}]*)\}", re.S)
#: 字段 = `类型 名字;`。**不锚行首** —— 原先锚了 `^\s*`, 于是 `struct P { int a; int b; };`
#: 这种**一行写完的结构体**只抽得到第一个字段 (整个 `int a; int b;` 是一行, `.+?` 只吃一段),
#: 而第二个字段**静默消失**。一行写 struct 在 C 里很常见 (2026-09-17 加多语法判据时撞到)。
_C_FIELD = re.compile(r"([^;{}]+?)\s+([A-Za-z_]\w*)\s*(\[\s*\d+\s*\])?\s*;")
_C_FN = re.compile(r"^[ \t]*(?:static\s+|inline\s+|const\s+)*"
                   r"([A-Za-z_][\w \t\*]*?)\s+([A-Za-z_]\w*)\s*\(([^;{)]*)\)\s*[;{]",
                   re.M)
_C_KEYWORDS = {"if", "while", "for", "switch", "return", "sizeof", "do", "else"}


def _c_type(t: str, mode: str, known: set[str]) -> str | None:
    t = re.sub(r"\s+", " ", t.strip())
    if t.endswith("[]"):
        t = t[:-2] + " *"
    # `enum X` **在 C 里就是一个整型** (标准这么定) —— 映 i32。不认它的话, 凡是收
    # `enum X` 形参的函数**整个被跳过**, 而那在真实 C 里到处都是 (2026-09-17 补)。
    if t.startswith("enum "):
        return "i32"
    if t in C_TYPES:
        return C_TYPES[t]
    if t.endswith("*"):
        base = re.sub(r"\s+", " ", t[:-1].strip())
        return "str" if base == "char" else "ptr"
    if t in known:
        return t
    return "ptr" if mode == "lenient" else None


def _blank_keep_off(m: "re.Match[str]") -> str:
    """把一段注释/预处理指令换成**等长**的空白，且**保留其中的换行**。

    这是为 `functions[i].body` 服务的（`docs/186`）：抓正文要按**下标**回原文里切，
    而下标只有在 `body` 与 `src` 逐字节等长时才有意义。原先的 `sub(" ", …)` 把一整段
    多行注释压成一个空格 —— 长度与行号**双双失真**，那样切出来的"正文"是别处的字节。
    """
    return "".join("\n" if ch == "\n" else " " for ch in m.group())


def from_c(src: str, name: str, mode: str = "strict") -> tuple[dict, Report]:
    rep = Report(name, "c", mode)
    doc = _blank(_ident(Path(name).stem), "c")
    body = _C_COMMENT.sub(_blank_keep_off, src)
    body = re.sub(r"^[ \t]*#.*$", _blank_keep_off, body, flags=re.M)
    known: set[str] = set()
    for m in _C_STRUCT.finditer(body):
        sname, inner = m.group(1), m.group(2)
        fields = []
        for fm in _C_FIELD.finditer(inner):
            ft, fn, arr = fm.group(1), fm.group(2), fm.group(3)
            if fn in ("if", "while", "for", "return"):
                continue
            t = _c_type(ft, mode, known)
            if arr and t:
                t = f"[{t}; {arr[1:-1].strip()}]"
            if t is None:
                rep.skip_field(sname, fn, f"字段类型 {ft!r} 无映射")
                continue
            fields.append({"name": fn, "type": t})
        if not fields:
            rep.skip("type", sname, "无可用字段")
            continue
        doc["types"].append({"name": sname, "fields": fields})
        known.add(sname)
        rep.ok += 1
    # ---- C 的 enum。**整个枚举要么进 `enums`、要么全进 `consts`**, 不拆开 ——
    # 拆开(把带值的那个单拎出来当 const)会造出一个**看着少了一个变体**的枚举, 那是误导。
    # 判据是"有没有任何一个变体带显式值": 有 -> 全按 C 语义算出值、发芽成 consts;
    # 没有 -> 就是一个普通枚举, 进 `enums`。
    for m in _C_ENUM.finditer(body):
        ename, inner = m.group(1), m.group(2)
        members, cur, next_v = [], None, 0
        for raw in inner.split(","):
            raw = raw.strip()
            if not raw:
                continue
            if "=" in raw:
                vn, vv = raw.split("=", 1)
                vn, vv = vn.strip(), vv.strip()
                if not IDENT_RE.match(vn) or not re.fullmatch(r"-?\d+", vv):
                    cur = None
                    rep.skip("const", raw, "枚举变体不是 `名字` 或 `名字 = 整数`")
                    continue
                next_v = int(vv)
            else:
                vn = raw
                if not IDENT_RE.match(vn):
                    cur = None
                    rep.skip("const", raw, "枚举变体名非法")
                    continue
            members.append((vn, next_v))
            next_v += 1
        if not members:
            rep.skip("type", ename, "枚举无变体")
            continue
        if all(v == i for i, (_n, v) in enumerate(members)):
            doc["enums"].append({"name": ename, "variants": [n for n, _v in members]})
        else:
            for vn, vv in members:
                if vn in {c["name"] for c in doc["consts"]}:
                    continue
                doc["consts"].append({"name": vn, "type": "i32", "value": vv})
        rep.ok += 1
    for m in _C_FN.finditer(body):
        rt, fn, params = m.group(1), m.group(2), m.group(3)
        if fn in _C_KEYWORDS or rt.strip().split()[-1] in _C_KEYWORDS:
            continue
        if "..." in params:
            rep.skip("fn", fn, "变参")
            continue
        ret = _c_type(rt, mode, known)
        if ret is None:
            rep.skip("fn", fn, f"返回类型 {rt!r} 无映射")
            continue
        ps, bad = [], None
        for j, raw in enumerate([p.strip() for p in params.split(",") if p.strip()]):
            if raw == "void":
                continue
            parts = raw.replace("*", " * ").split()
            if len(parts) < 2:
                bad = f"参数 {raw!r} 解析失败"
                break
            pname = parts[-1]
            t = _c_type(" ".join(parts[:-1]), mode, known)
            if t is None:
                bad = f"参数类型 {raw!r} 无映射"
                break
            ps.append({"name": _ident(pname, f"a{j}"), "type": t})
        if bad:
            rep.skip("fn", fn, bad)
            continue
        #: C 的函数**按定义**就是 C ABI (`_c_bare` 只收裸函数, 不含 `static` 之类),
        #: 所以这里不是猜。带结构体参数的那些会在 `lomt_from` 那侧被拒 (第 1 阶段只收标量)。
        ent: dict = {"name": fn, "params": ps, "ret": ret, "abi": "c"}
        # ---- 正文（`docs/186`）：有体的函数把**原文**带上，没有的（声明）不带这个字段。
        #
        # **可选子字段**，与 `abi` 同一条先例（`docs/179` §3.1）：省略合法、给了必须认。
        # 不认识它的消费者忽略它就是对的 —— 它们本来也只按签名用这份对象（接口单元那条路）。
        #
        # 存的是**整段函数原文**（签名 + 体），不是只存 `{…}`：签名那边 Potato 记的是
        # **映射后的** Loment 类型名（`i32`），从 `i32` 反推回 C 的拼法是另一张表，
        # 而原文本来就在手边。另一个理由更要紧 —— **原文是保真的**：`unsigned` 与
        # `unsigned int` 在 Potato 里都是 `u32`，回推必然丢掉用户写的那个拼法。
        if m.end() > 0 and body[m.end() - 1] == "{":
            close = _block_end(body, m.end() - 1)
            if close < 0:
                rep.skip("fn", fn, "花括号不配平（原文到这里就断了）")
                continue
            ent["body"] = src[m.start():close + 1].strip()
        doc["functions"].append(ent)
        rep.ok += 1
    return _finish(doc, rep)


# ---------------------------------------------------------------- Rust (轻量解析)

_RS_STRUCT = re.compile(r"\b(?:pub\s+)?struct\s+([A-Za-z_]\w*)\s*\{([^}]*)\}", re.S)
_RS_ENUM = re.compile(r"\b(?:pub\s+)?enum\s+([A-Za-z_]\w*)\s*\{([^}]*)\}", re.S)
# `extern "C" fn` 里 `extern "C"` 夹在修饰符和 `fn` 之间 —— 原先的模式要求 `fn` 紧跟修饰符,
# 于是**恰恰是那些真有 C ABI 的函数被漏掉**(2026-09-17 撞出来的)。捕获那个 ABI 串 (§5)。
_RS_FN = re.compile(r"^[ \t]*(?:pub\s+)?(?:const\s+)?(?:unsafe\s+)?(?:async\s+)?"
                    r"(?:extern\s+\"([^\"]*)\"\s+)?fn\s+"
                    r"([A-Za-z_]\w*)\s*(<[^>]*>)?\s*\(([^{)]*)\)\s*(?:->\s*([^{]+?))?\s*\{",
                    re.M)
#: Rust 的 `pub const NAME: TYPE = VALUE;`。**必须要求名字后面有冒号** ——
#: `const fn f()` 也是 `const` 开头, 靠那个冒号把它挡在外面 (它由 `_RS_FN` 接走)。
#: `static` **不算常量** (可变状态), 不收。
_RS_CONST = re.compile(r"^[ \t]*(?:pub(?:[ \t]*\([^)]*\))?[ \t]+)?const[ \t]+"
                       r"([A-Za-z_]\w*)[ \t]*:[ \t]*([^=;]+?)[ \t]*=[ \t]*([^;]+);", re.M)
_RS_INT_RANGE = {
    "i8": (-128, 127), "i16": (-32768, 32767),
    "i32": (-(2 ** 31), 2 ** 31 - 1), "i64": (-(2 ** 63), 2 ** 63 - 1),
    "u8": (0, 255), "u16": (0, 65535), "u32": (0, 2 ** 32 - 1), "u64": (0, 2 ** 64 - 1),
}


def _rs_int_literal(txt: str) -> int | None:
    """Rust 整数字面量: `8` / `0xFF` / `0b1010` / `1_000` / `8u32`。

    **只认字面量, 不认表达式** —— `_RS_CONST` 那处实测的 14 个常量全是字面量。折不出来
    就返回 None, 由调用方**报出来**: 这条函数不假装能算 Rust 的常量表达式。
    """
    t = txt.strip().replace("_", "")
    t = re.sub(r"(?:u|i)(?:8|16|32|64|128|size)$", "", t)   # 后缀 8u32 / 8usize
    try:
        return int(t, 0)
    except ValueError:
        return None


def _rs_const(cname: str, ctype: str, cval: str, mode: str,
              known: set[str]) -> tuple[dict | None, str]:
    """一条 Rust 常量。第二个返回值非空时**调用方一定要出声**。

    2026-09-17 补: 原先 `from_rust` 只有 struct/enum/fn 三条循环, **常量一条都不抽** ——
    语料里 5 个文件共 14 个 `pub const` 既不进产物也**不报 `[skip]`**, 汇总行一个数都不变。
    """
    v = _rs_int_literal(cval)
    if v is None:
        return None, f"常量值 {cval.strip()!r} 不是整数字面量"
    t = _rs_type(ctype, mode, known)
    if t not in _RS_INT_RANGE:
        return None, f"常量类型 {ctype.strip()!r} 不是整型"
    lo, hi = _RS_INT_RANGE[t]
    if not lo <= v <= hi:
        return None, f"常量值 {v} 超出 {t} 的范围"
    return {"name": cname, "type": t, "value": v}, ""


def _rs_type(t: str, mode: str, known: set[str]) -> str | None:
    t = re.sub(r"&'\w+\s*", "&", t.strip())  # 去生命周期
    if t.startswith("&mut [") and t.endswith("]"):
        inner = _rs_type(t[6:-1], mode, known)
        return f"mut [{inner}]" if inner else None
    if t.startswith("&[") and t.endswith("]"):
        inner = _rs_type(t[2:-1], mode, known)
        return f"[{inner}]" if inner else None
    if t in ("&str", "str"):
        return "str"
    if t.startswith("&mut ") or t.startswith("&"):
        return "ptr" if mode == "lenient" else None
    if t.startswith("*const ") or t.startswith("*mut "):
        return "ptr"
    if t.startswith("[") and t.endswith("]"):
        if ";" in t:
            elem, n = t[1:-1].rsplit(";", 1)
            elem, n = elem.strip(), n.strip()
            if not n.isdigit():
                return "ptr" if mode == "lenient" else None
            et = _rs_type(elem, mode, known)
            return f"[{et}; {n}]" if et else None
        et = _rs_type(t[1:-1], mode, known)
        return f"[{et}]" if et else None
    if t in RS_TYPES:
        return RS_TYPES[t]
    if t in known:
        return t
    return "ptr" if mode == "lenient" else None


def from_rust(src: str, name: str, mode: str = "strict") -> tuple[dict, Report]:
    rep = Report(name, "rust", mode)
    doc = _blank(_ident(Path(name).stem), "rust")
    body = re.sub(r"//[^\n]*", "", src)
    known: set[str] = set()
    for m in _RS_STRUCT.finditer(body):
        sname, inner = m.group(1), m.group(2)
        fields = []
        for line in inner.split(","):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                continue
            fn, ft = line.split(":", 1)
            fn, ft = fn.strip(), ft.strip()
            # **`pub x: i32` 里的 `pub` 要剥掉** —— Rust 的结构体字段大多数是 `pub`,
            # 不剥的话 `IDENT_RE.match("pub x")` 失败, 整个字段**静默消失**
            # (2026-09-17 加多语法判据时撞到: `pub struct Pt { pub x: i32, pub y: i32 }`
            # 抽出来 0 个字段)。`pub(crate)` 那种也一起剥。
            fn = re.sub(r"^(?:pub(?:\s*\([^)]*\))?\s+)+", "", fn).strip()
            if not IDENT_RE.match(fn):
                continue
            t = _rs_type(ft, mode, known)
            if t is None:
                rep.skip_field(sname, fn, f"字段类型 {ft!r} 无映射")
                continue
            fields.append({"name": fn, "type": t})
        if not fields:
            rep.skip("type", sname, "无可用字段")
            continue
        doc["types"].append({"name": sname, "fields": fields})
        known.add(sname)
        rep.ok += 1
    for m in _RS_ENUM.finditer(body):
        ename, inner = m.group(1), m.group(2)
        if "(" in inner or "{" in inner:
            rep.skip("type", ename, "带载荷变体")
            continue
        vs = [v.strip() for v in inner.split(",") if IDENT_RE.match(v.strip())]
        if not vs:
            rep.skip("type", ename, "无变体")
            continue
        doc["enums"].append({"name": ename, "variants": vs})
        known.add(ename)
        rep.ok += 1
    for m in _RS_CONST.finditer(body):
        c, why = _rs_const(m.group(1), m.group(2), m.group(3), mode, known)
        if c:
            doc["consts"].append(c)
            rep.ok += 1
        else:
            rep.skip("const", m.group(1), why)
    for m in _RS_FN.finditer(body):
        abi_g, fn, generics, params, ret = (m.group(1), m.group(2), m.group(3),
                                            m.group(4), m.group(5))
        #: 只有 `extern "C"` 才是 C ABI —— **不能拿普通 `pub fn` 当 FFI 声明**:
        #: 它的 ABI 是 Rust 自己的, 照 C ABI 调就是错编。这个判断只有源语言这侧做得了,
        #: 所以 ABI 记进对象 (§5), 由 `lomt_from` 决定能不能发 `extern fn`。
        abi = "c" if (abi_g or "").strip() == "C" else "rust"
        if generics:
            rep.skip("fn", fn, "泛型函数")
            continue
        if "..." in params or "self" in params.split(",")[0].strip():
            rep.skip("fn", fn, "方法/变参")
            continue
        ps, bad = [], None
        for j, raw in enumerate([p.strip() for p in params.split(",") if p.strip()]):
            if ":" not in raw:
                bad = f"参数 {raw!r} 无类型"
                break
            pn, pt = raw.split(":", 1)
            pn, pt = pn.strip(), pt.strip()
            if pn.startswith("mut "):
                pn = pn[4:].strip()
            if not IDENT_RE.match(pn):
                bad = f"参数名 {pn!r} 非法"
                break
            t = _rs_type(pt, mode, known)
            if t is None:
                bad = f"参数类型 {pt!r} 无映射"
                break
            ps.append({"name": pn, "type": t})
        if bad:
            rep.skip("fn", fn, bad)
            continue
        r = _rs_type(ret, mode, known) if ret else "()"
        if r is None:
            rep.skip("fn", fn, f"返回类型 {ret!r} 无映射")
            continue
        doc["functions"].append({"name": fn, "params": ps, "ret": r, "abi": abi})
        rep.ok += 1
    return _finish(doc, rep)


# ---------------------------------------------------------------- CLI

LANGS = {"python": from_python, "c": from_c, "rust": from_rust,
         "go": from_go, "java": from_java}
EXT = {".py": "python", ".c": "c", ".h": "c", ".rs": "rust", ".go": "go", ".java": "java"}
#: `abi` 的取值域 (与 `potato.ABIS` 对齐): `c` = 平台 C ABI, 能发 `extern fn`;
#: 其余都是**运行时那一族**, 走进程桥 (docs/173 §4)。


def resolve_lang(path: Path, lang: str = "auto") -> tuple[str, str]:
    """`(语言, 依据)` —— **后缀优先, 内容兜底**。`transcribe` 与各工具共用这一处,
    免得"判语法"这事在两处各写一遍(那种必然漂)。

    后缀认得就信后缀(显式比猜的可靠); 认不出(`.lomt` 就在这一档)才读内容判。
    """
    if lang != "auto":
        return lang, "调用方指定的"
    by_ext = EXT.get(path.suffix, "")
    if by_ext:
        return by_ext, f"后缀 {path.suffix}"
    src = path.read_text(encoding="utf-8", errors="replace")
    return detect_lang(src)


def transcribe(path: Path, lang: str = "auto", mode: str = "strict"):
    """源文件 -> (形式对象, 报告)。

    `lang="auto"` 时按 `resolve_lang` 的规则定语言 —— 它也就是 docs/175 §5 那条
    "非 Loment 源语法的 `.lomt` 文件"落地的地方。
    """
    lang, why = resolve_lang(path, lang)
    if lang == "loment":
        raise ValueError(
            f"{path} 是 **Loment 语法**（{why}）—— 它不该走多语法前端, "
            f"直接交给编译器: loment check {path}")
    if lang not in LANGS:
        raise ValueError(
            f"定不出源语法（后缀 {path.suffix!r} 不在 {sorted(EXT)}，内容：{why}）"
            f"—— 用 --lang c|rust|python 指明")
    src = path.read_text(encoding="utf-8", errors="replace")
    return LANGS[lang](src, path.name, mode)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="potato_from", description="源码 -> Potato 形式对象")
    ap.add_argument("file", nargs="?", help="源文件")
    ap.add_argument("--lang", choices=("auto", "python", "c", "rust"), default="auto")
    ap.add_argument("--mode", choices=("strict", "lenient"), default="strict")
    ap.add_argument("--json", metavar="OUT")
    ap.add_argument("--report", metavar="OUT")
    ap.add_argument("--corpus", metavar="MANIFEST", help="JSON 清单: [{path, lang?}]")
    ap.add_argument("--out-dir", metavar="DIR", help="--corpus 的输出目录")
    a = ap.parse_args(argv)

    if a.corpus:
        if not a.out_dir:
            print("[ERR] --corpus 需要 --out-dir", file=sys.stderr)
            return 2
        man = json.loads(Path(a.corpus).read_text(encoding="utf-8"))
        out = Path(a.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        reports = []
        for ent in man:
            p = Path(ent["path"])
            try:
                doc, rep = transcribe(p, ent.get("lang", a.lang), a.mode)
            except Exception as e:  # noqa: BLE001
                reports.append({"path": str(p), "language": ent.get("lang", a.lang),
                                "entities_seen": 0, "entities_ok": 0,
                                "conversion_rate": 0.0, "skipped": [],
                                "object_valid": False, "validate_errors": [str(e)]})
                continue
            (out / f"{_ident(p.stem)}.potato.json").write_text(
                json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            reports.append(rep.as_dict(not rep.validate_errors))
        (out / "reports.json").write_text(
            json.dumps(reports, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        ok = sum(1 for r in reports if r["object_valid"])
        print(f"[OK] 转写 {ok}/{len(reports)} 个文件 -> {out}")
        return 0 if ok == len(reports) else 1

    if not a.file:
        print("[ERR] 需要文件或 --corpus", file=sys.stderr)
        return 2
    p = Path(a.file)
    doc, rep = transcribe(p, a.lang, a.mode)
    text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    if a.json:
        Path(a.json).write_text(text, encoding="utf-8")
    if a.report:
        Path(a.report).write_text(
            json.dumps(rep.as_dict(not rep.validate_errors), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
    if not a.json and not a.report:
        sys.stdout.write(text)
    else:
        print(f"[OK] {p} -> {rep.ok}/{rep.seen} 实体 "
              f"(校验错误 {len(rep.validate_errors)})")
    return 0 if not rep.validate_errors else 1


if __name__ == "__main__":
    sys.exit(main())
