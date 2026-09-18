#!/usr/bin/env python3
# potato.py — Potato 形式对象校验器 (docs/142 v0, docs/147 v1)
#
# 判据 (docs/140 §9.4): 给定一个形式对象, 独立校验器仅凭它即可判定
# 能力域 / 契约 / 布局是否合法 —— 无需读源码, 无需读二进制。
# M47: 本文件不得 import 编译器 (lomentc) —— 独立性由 tools/potato_test.py 断言。
#
# 用法:
#   python tools/potato.py validate obj.json [obj2.json ...]
#   python tools/potato.py show obj.json
#
# 退出码: 0 = 全部合法 / 1 = 有非法项 / 2 = 用法或读取错误。

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

INT_TYPES = ("u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64")
TYPES = INT_TYPES + ("bool", "str", "ptr", "()")
# 编译器内建 trait (不要求在源中声明): Drop = RAII 析构 (M16)
BUILTIN_TRAITS = {"Drop": {"drop"}}
WIDTH = {"u8": 1, "u16": 2, "u32": 4, "u64": 8, "i8": 1, "i16": 2, "i32": 4, "i64": 8}
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# v0 顶层字段; v1 = v0 + 泛型/实例/trait/impl (M45); v2 = v1 + 项目模式 (docs/143 §3.2);
# v3 = v2 + **开关取值** (docs/182 §1)
TOP_KEYS_V0 = {"potato", "unit", "language", "imports", "capabilities", "functions", "layouts",
               "consts", "enums", "types", "excluded"}
TOP_KEYS_V1 = TOP_KEYS_V0 | {"traits", "impls", "generics", "instances", "guards"}
TOP_KEYS_V2 = TOP_KEYS_V1 | {"mode"}
TOP_KEYS_V3 = TOP_KEYS_V2 | {"switches"}
VERSIONS = ("v0", "v1", "v2", "v3")
#: `mode` 的取值 = `choose` 的两个模式名 (docs/143 §3.2)。**只有这两个** ——
#: 拼错的模式名在编译器那边是 E022, 在对象里就是这里报错。
MODES = ("std", "no_std")
#: 函数级的**可选** `abi` (docs/179 §2)。取值 = 源语言那一侧的调用约定:
#:   `c`      = 平台 C ABI (System V / Win64) —— 可以发成 L1 的 `extern fn` (docs/173 §2)
#:   其余     = 不是平台 C ABI, **不能**发 `extern fn`; 要调它得走别的路 (进程桥等)
#:
#: 加一个源语言就要在这里加一个名字 (`go` / `java`) —— **它不只是"记个名字"**:
#: 每一种都是在说"这一族的函数不能用 C ABI 调", 而 `lomt_from` 据此把它们挡在产物外。
ABIS = ("c", "rust", "python", "go", "java")


def _is_array_type(t: object) -> bool:
    return isinstance(t, str) and t.startswith("[") and t.endswith("]") and ";" in t


def _is_slice_type(t: object) -> bool:
    """切片 [T] / 可变切片 mut [T] (M3/M4)。"""
    if not isinstance(t, str):
        return False
    body = t[4:] if t.startswith("mut ") else t
    return body.startswith("[") and body.endswith("]") and ";" not in body


def _slice_elem(t: str) -> str:
    body = t[4:] if t.startswith("mut ") else t
    return body[1:-1].strip()


def _type_ok(t: object, known: set[str]) -> bool:
    """基类型 / 已声明 struct / 数组 [T; N] / 切片 [T] / 可变切片 mut [T]。"""
    if not isinstance(t, str):
        return False
    if t in known:
        return True
    if _is_slice_type(t):
        return _type_ok(_slice_elem(t), known)
    if _is_array_type(t):
        try:
            elem = t[1:t.rindex(";")].strip()
            n = int(t[t.rindex(";") + 1:-1].strip())
        except ValueError:
            return False
        return n > 0 and _type_ok(elem, known)
    return False


def _req(obj: dict, key: str, where: str, errs: list[str]):
    if key not in obj:
        errs.append(f"{where}: 缺字段 {key}")
        return None
    return obj[key]


def validate(doc: object) -> list[str]:
    errs: list[str] = []
    if not isinstance(doc, dict):
        return ["根节点必须是对象"]
    ver = doc.get("potato")
    if ver not in VERSIONS:
        errs.append(f"potato 版本必须是 {VERSIONS} 之一，得到 {ver!r}")
        ver = "v0"
    top = {"v0": TOP_KEYS_V0, "v1": TOP_KEYS_V1, "v2": TOP_KEYS_V2, "v3": TOP_KEYS_V3}[ver]
    for k in doc:
        if k not in top:
            errs.append(f"未知顶层字段 {k!r}（{ver} 不允许扩展字段）")
    if ver in ("v1", "v2", "v3"):
        for k in ("traits", "impls", "generics", "instances"):
            if not isinstance(doc.get(k), list):
                errs.append(f"{ver}: 缺字段 {k}（必须是数组，可为空）")
        g = doc.get("guards")
        if not isinstance(g, int) or isinstance(g, bool) or g < 0:
            errs.append(f"{ver}: guards 必须是非负整数，得到 {g!r}")
    if ver in ("v2", "v3"):
        # **必填** (docs/175 §8 的判据: 从对象里删掉该字段, 校验器必须红)。这就是
        # 必须升版本而不是往旧版里加字段的原因 —— 要求必填会让既有的旧版对象全变非法,
        # 而旧版是**承诺过能回放**的 (docs/147 §5, 冻结样本 demo.v0.json 一直在跑)。
        m = doc.get("mode")
        if m not in MODES:
            errs.append(f"{ver}: mode 必须是 {MODES} 之一，得到 {m!r}")
    if ver == "v3":
        # **必填, 可为空数组** (docs/182 §1)。与 `mode` 同一条纪律: 不存在"缺这项"的形态,
        # 所以"这份单元是在什么开关状态下编的"是**可回放**的。
        # 用户 2026-09-17: **"开关的取值是要进 Potato 的"**。
        sw = doc.get("switches")
        if not isinstance(sw, list):
            errs.append(f"{ver}: switches 必须是数组（可为空），得到 {sw!r}")
        else:
            seen_s: set[str] = set()
            for j, s in enumerate(sw):
                w = f"switches[{j}]"
                if not isinstance(s, dict):
                    errs.append(f"{w}: 必须是对象")
                    continue
                nm = s.get("name")
                if not isinstance(nm, str) or not IDENT_RE.match(nm):
                    errs.append(f"{w}.name 非法: {nm!r}")
                elif nm in seen_s:
                    errs.append(f"{w}.name 重复: {nm}")
                else:
                    seen_s.add(nm)
                if not isinstance(s.get("on"), bool):
                    errs.append(f"{w}.on 必须是布尔（开关**只能**是开或关，没有第三态）")
    unit = _req(doc, "unit", "根", errs)
    if unit is not None and (not isinstance(unit, str) or not IDENT_RE.match(unit)):
        errs.append(f"unit 必须是标识符，得到 {unit!r}")
    _req(doc, "language", "根", errs)

    # ---- 导入的编译单元名
    imports = _req(doc, "imports", "根", errs)
    if isinstance(imports, list):
        for i, x in enumerate(imports):
            if not isinstance(x, str) or not IDENT_RE.match(x):
                errs.append(f"imports[{i}]: 必须是标识符，得到 {x!r}")
    elif imports is not None:
        errs.append("imports 必须是字符串数组")

    # 先收集 L1 声明的类型名: 函数签名可以引用它们
    declared_types: set[str] = set()
    for key in ("types", "enums"):
        if isinstance(doc.get(key), list):
            for t in doc[key]:
                if isinstance(t, dict) and isinstance(t.get("name"), str):
                    declared_types.add(t["name"])
    allowed_types = set(TYPES) | declared_types

    # ---- 能力域
    caps = _req(doc, "capabilities", "根", errs)
    seen_cap: set[str] = set()
    if isinstance(caps, list):
        for i, c in enumerate(caps):
            w = f"capabilities[{i}]"
            if not isinstance(c, dict):
                errs.append(f"{w}: 必须是对象")
                continue
            name = c.get("name")
            if not isinstance(name, str) or not IDENT_RE.match(name):
                errs.append(f"{w}.name 非法: {name!r}")
            elif name in seen_cap:
                errs.append(f"{w}.name 重复: {name}")
            else:
                seen_cap.add(name)
            dom = c.get("domain")
            if not isinstance(dom, dict):
                errs.append(f"{w}.domain 必须是对象")
            else:
                space = dom.get("space")
                lo, hi = dom.get("lo"), dom.get("hi")
                if not isinstance(space, str) or not IDENT_RE.match(space):
                    errs.append(f"{w}.domain.space 非法: {space!r}")
                if not isinstance(lo, int) or not isinstance(hi, int):
                    errs.append(f"{w}.domain.lo/hi 必须是整数")
                elif lo < 0 or lo > hi:
                    errs.append(f"{w}.domain 区间非法: [{lo}..{hi}]")
            if not isinstance(c.get("revocable"), bool):
                errs.append(f"{w}.revocable 必须是布尔")
    elif caps is not None:
        errs.append("capabilities 必须是数组")

    # ---- 函数签名
    funcs = _req(doc, "functions", "根", errs)
    seen_fn: set[str] = set()
    if isinstance(funcs, list):
        for i, f in enumerate(funcs):
            w = f"functions[{i}]"
            if not isinstance(f, dict):
                errs.append(f"{w}: 必须是对象")
                continue
            name = f.get("name")
            if not isinstance(name, str) or not IDENT_RE.match(name):
                errs.append(f"{w}.name 非法: {name!r}")
            elif name in seen_fn:
                errs.append(f"{w}.name 重复: {name}")
            else:
                seen_fn.add(name)
            if not _type_ok(f.get("ret"), allowed_types):
                errs.append(f"{w}.ret 非法类型: {f.get('ret')!r}")
            params = f.get("params")
            if not isinstance(params, list):
                errs.append(f"{w}.params 必须是数组")
                continue
            seen_p: set[str] = set()
            for j, p in enumerate(params):
                pw = f"{w}.params[{j}]"
                if not isinstance(p, dict) or not _type_ok(p.get("type"), allowed_types):
                    errs.append(f"{pw}: 参数类型非法")
                    continue
                pn = p.get("name")
                if not isinstance(pn, str) or not IDENT_RE.match(pn):
                    errs.append(f"{pw}.name 非法: {pn!r}")
                elif pn in seen_p:
                    errs.append(f"{pw}.name 重复: {pn}")
                else:
                    seen_p.add(pn)
            # `abi` 是**可选**字段 (docs/179 §2): 外源模块的调用约定。省略 = 未声明。
            # **可选是刻意的** —— 必填会逼着升 v3 (docs/178 §1 那条理由), 而这一个字段
            # 不值得动契约版本: 不认识它的消费者忽略它就是对的 (它们本来也不判 ABI)。
            # 只有"声明了"才校验取值, 免得拼错一个 ABI 名一路静默到链接期。
            abi = f.get("abi")
            if abi is not None and abi not in ABIS:
                errs.append(f"{w}.abi 非法: {abi!r} (可选, 给了就必须是 {ABIS} 之一)")
    elif funcs is not None:
        errs.append("functions 必须是数组")

    # ---- 布局
    layouts = _req(doc, "layouts", "根", errs)
    seen_lay: set[str] = set()
    if isinstance(layouts, list):
        for i, r in enumerate(layouts):
            w = f"layouts[{i}]"
            if not isinstance(r, dict):
                errs.append(f"{w}: 必须是对象")
                continue
            name = r.get("name")
            if not isinstance(name, str) or not IDENT_RE.match(name):
                errs.append(f"{w}.name 非法: {name!r}")
            elif name in seen_lay:
                errs.append(f"{w}.name 重复: {name}")
            else:
                seen_lay.add(name)
            size = r.get("size")
            if not isinstance(size, int) or size <= 0:
                errs.append(f"{w}.size 必须是正整数，得到 {size!r}")
                size = 0
            if r.get("endian") not in ("little", "big"):
                errs.append(f"{w}.endian 必须是 little/big")
            if not isinstance(r.get("packed"), bool):
                errs.append(f"{w}.packed 必须是布尔")
            fields = r.get("fields")
            if not isinstance(fields, list):
                errs.append(f"{w}.fields 必须是数组")
                continue
            occ: list[tuple[int, int, str]] = []
            seen_f: set[str] = set()
            for j, fd in enumerate(fields):
                fw = f"{w}.fields[{j}]"
                if not isinstance(fd, dict):
                    errs.append(f"{fw}: 必须是对象")
                    continue
                fn = fd.get("name")
                ft = fd.get("type")
                off = fd.get("offset")
                if not isinstance(fn, str) or not IDENT_RE.match(fn):
                    errs.append(f"{fw}.name 非法: {fn!r}")
                elif fn in seen_f:
                    errs.append(f"{fw}.name 重复: {fn}")
                else:
                    seen_f.add(fn)
                if ft not in WIDTH:
                    errs.append(f"{fw}.type 非法或非定宽: {ft!r}")
                    continue
                if not isinstance(off, int) or off < 0:
                    errs.append(f"{fw}.offset 必须是非负整数")
                    continue
                if off + WIDTH[ft] > size:
                    errs.append(f"{fw} 越界: @{off}+{WIDTH[ft]} > size={size}")
                occ.append((off, off + WIDTH[ft], str(fn)))
            occ.sort()
            for (o1, e1, n1), (o2, e2, n2) in zip(occ, occ[1:]):
                if o2 < e1:
                    errs.append(f"{w} 字段 {n1}@{o1}..{e1} 与 {n2}@{o2}..{e2} 重叠")
    elif layouts is not None:
        errs.append("layouts 必须是数组")

    # ---- 枚举 (无载荷变体)
    enums = _req(doc, "enums", "根", errs)
    seen_en: set[str] = set()
    if isinstance(enums, list):
        for i, e in enumerate(enums):
            w = f"enums[{i}]"
            if not isinstance(e, dict):
                errs.append(f"{w}: 必须是对象")
                continue
            name = e.get("name")
            if not isinstance(name, str) or not IDENT_RE.match(name):
                errs.append(f"{w}.name 非法: {name!r}")
            elif name in seen_en:
                errs.append(f"{w}.name 重复: {name}")
            elif name in TYPES:
                errs.append(f"{w}.name 与基类型同名: {name}")
            else:
                seen_en.add(name)
            vs = e.get("variants")
            if not isinstance(vs, list) or not vs:
                errs.append(f"{w}.variants 必须是非空数组")
                continue
            seen_v: set[str] = set()
            for j, v in enumerate(vs):
                if not isinstance(v, str) or not IDENT_RE.match(v):
                    errs.append(f"{w}.variants[{j}] 非法: {v!r}")
                elif v in seen_v:
                    errs.append(f"{w}.variants[{j}] 重复: {v}")
                else:
                    seen_v.add(v)
            pl = e.get("payloads")
            if pl is not None:
                if not isinstance(pl, dict):
                    errs.append(f"{w}.payloads 必须是对象")
                else:
                    for vn, pt in pl.items():
                        if vn not in seen_v:
                            errs.append(f"{w}.payloads 键 {vn!r} 不是已声明变体")
                        if not _type_ok(pt, allowed_types):
                            errs.append(f"{w}.payloads[{vn}] 类型未声明: {pt!r}")
    elif enums is not None:
        errs.append("enums 必须是数组")

    # ---- 常量 (仅整型)
    consts = _req(doc, "consts", "根", errs)
    seen_c: set[str] = set()
    if isinstance(consts, list):
        for i, c in enumerate(consts):
            w = f"consts[{i}]"
            if not isinstance(c, dict):
                errs.append(f"{w}: 必须是对象")
                continue
            name = c.get("name")
            if not isinstance(name, str) or not IDENT_RE.match(name):
                errs.append(f"{w}.name 非法: {name!r}")
            elif name in seen_c:
                errs.append(f"{w}.name 重复: {name}")
            else:
                seen_c.add(name)
            if c.get("type") not in INT_TYPES:
                errs.append(f"{w}.type 必须是整型: {c.get('type')!r}")
            if not isinstance(c.get("value"), int):
                errs.append(f"{w}.value 必须是整数")
    elif consts is not None:
        errs.append("consts 必须是数组")

    # ---- L1 原生类型 (struct)
    types = _req(doc, "types", "根", errs)
    seen_ty: set[str] = set()
    if isinstance(types, list):
        for i, t in enumerate(types):
            w = f"types[{i}]"
            if not isinstance(t, dict):
                errs.append(f"{w}: 必须是对象")
                continue
            name = t.get("name")
            if not isinstance(name, str) or not IDENT_RE.match(name):
                errs.append(f"{w}.name 非法: {name!r}")
            elif name in seen_ty:
                errs.append(f"{w}.name 重复: {name}")
            elif name in TYPES:
                errs.append(f"{w}.name 与基类型同名: {name}")
            else:
                seen_ty.add(name)
            fields = t.get("fields")
            if not isinstance(fields, list) or not fields:
                errs.append(f"{w}.fields 必须是非空数组")
                continue
            seen_f: set[str] = set()
            for j, fd in enumerate(fields):
                fw = f"{w}.fields[{j}]"
                if not isinstance(fd, dict):
                    errs.append(f"{fw}: 必须是对象")
                    continue
                fn = fd.get("name")
                if not isinstance(fn, str) or not IDENT_RE.match(fn):
                    errs.append(f"{fw}.name 非法: {fn!r}")
                elif fn in seen_f:
                    errs.append(f"{fw}.name 重复: {fn}")
                else:
                    seen_f.add(fn)
                ft = fd.get("type")
                if not _type_ok(ft, allowed_types):
                    errs.append(f"{fw}.type 非法或未声明: {ft!r}")
    elif types is not None:
        errs.append("types 必须是数组")
    # 类型引用完整性: **上面那条 `_type_ok` 已经查过了** (行 377, 用的是 `allowed_types`)。
    #
    # 这里原先还有一遍"再查一次"的循环, 用的是 `fd["type"] not in declared` —— **裸字符串
    # 相等**。它与上一条查的是同一批字段, 只有两处不同, 而两处都是错的:
    #   * 它不认复合类型 —— `[u64; 8]` / `[i32]` 过了 `_type_ok` 却过不了它, 于是
    #     结构体里放一个数组字段, 整份对象被判**非法**, `lomt_from` 直接 `[ERR]` 退出
    #     (比 skip 更坏: 一个字段的问题毁掉整个模块)。2026-09-17 由 Java/Rust 两个 agent
    #     **各自独立**撞到 (`int[]` 字段与 `[u64; 8]` 字段)。
    #   * `declared` 是 `TYPES | seen_ty`, **不含 enums** —— 而 `allowed_types` 含。
    #     于是"字段类型是一个已声明的枚举"被误报未声明。
    # 结论: 这一遍**只可能误报, 不可能多抓** (它查的字段集合是上一条的子集)。删掉它,
    # 顺带消掉"两份判据各自漂移"的可能 —— 那种漂移就是这个 bug 的来源。

    # 类型名跨表唯一 (types / enums 共用一个命名空间)
    for n in sorted(seen_ty & seen_en):
        errs.append(f"类型名 {n} 同时出现在 types 与 enums")

    # ---- 出界声明
    excl = _req(doc, "excluded", "根", errs)
    if isinstance(excl, list):
        for i, x in enumerate(excl):
            if not isinstance(x, str) or not x.strip():
                errs.append(f"excluded[{i}]: 必须是非空字符串")
    elif excl is not None:
        errs.append("excluded 必须是字符串数组")

    # ---- M45: 泛型声明 / 单态化实例 / trait / impl (仅 v1)
    gen_by: dict[tuple[str, str], list[str]] = {}
    for i, g in enumerate(doc.get("generics") or []):
        w = f"generics[{i}]"
        if not isinstance(g, dict):
            errs.append(f"{w}: 必须是对象")
            continue
        kind, name = g.get("kind"), g.get("name")
        if kind not in ("fn", "type"):
            errs.append(f"{w}.kind 必须是 fn/type: {kind!r}")
        if not isinstance(name, str) or not IDENT_RE.match(name):
            errs.append(f"{w}.name 非法: {name!r}")
            continue
        ps = g.get("params")
        if not isinstance(ps, list) or not ps:
            errs.append(f"{w}.params 必须是非空数组")
            continue
        seen_t: set[str] = set()
        for j, p in enumerate(ps):
            if not isinstance(p, str) or not IDENT_RE.match(p):
                errs.append(f"{w}.params[{j}] 非法: {p!r}")
            elif p in seen_t:
                errs.append(f"{w}.params[{j}] 重复: {p}")
            else:
                seen_t.add(p)
        if (kind, name) in gen_by:
            errs.append(f"{w}: 泛型声明重复 {kind} {name}")
        else:
            gen_by[(kind, name)] = list(ps)

    seen_i: set[str] = set()
    for i, inst in enumerate(doc.get("instances") or []):
        w = f"instances[{i}]"
        if not isinstance(inst, dict):
            errs.append(f"{w}: 必须是对象")
            continue
        kind, name, of = inst.get("kind"), inst.get("name"), inst.get("of")
        if kind not in ("fn", "type"):
            errs.append(f"{w}.kind 必须是 fn/type: {kind!r}")
        if not isinstance(name, str) or not IDENT_RE.match(name):
            errs.append(f"{w}.name 非法: {name!r}")
        elif name in seen_i:
            errs.append(f"{w}.name 重复: {name}")
        else:
            seen_i.add(name)
        if not isinstance(of, str) or (kind, of) not in gen_by:
            errs.append(f"{w}.of 不是已声明的泛型: {of!r}")
            continue
        args = inst.get("args")
        if not isinstance(args, list):
            errs.append(f"{w}.args 必须是数组")
            continue
        if len(args) != len(gen_by[(kind, of)]):
            errs.append(f"{w}.args 数量 {len(args)} 与泛型参数 {len(gen_by[(kind, of)])} 不符")
        for j, a in enumerate(args):
            if not _type_ok(a, allowed_types):
                errs.append(f"{w}.args[{j}] 非法类型: {a!r}")

    tr_methods: dict[str, set[str]] = dict(BUILTIN_TRAITS)
    for i, t in enumerate(doc.get("traits") or []):
        w = f"traits[{i}]"
        if not isinstance(t, dict):
            errs.append(f"{w}: 必须是对象")
            continue
        name = t.get("name")
        if not isinstance(name, str) or not IDENT_RE.match(name):
            errs.append(f"{w}.name 非法: {name!r}")
            continue
        if name in tr_methods:
            errs.append(f"{w}.name 重复: {name}")
            continue
        ms = t.get("methods")
        if not isinstance(ms, list) or not ms:
            errs.append(f"{w}.methods 必须是非空数组")
            continue
        seen_m: set[str] = set()
        for j, m in enumerate(ms):
            if not isinstance(m, str) or not IDENT_RE.match(m):
                errs.append(f"{w}.methods[{j}] 非法: {m!r}")
            elif m in seen_m:
                errs.append(f"{w}.methods[{j}] 重复: {m}")
            else:
                seen_m.add(m)
        tr_methods[name] = seen_m

    for i, im in enumerate(doc.get("impls") or []):
        w = f"impls[{i}]"
        if not isinstance(im, dict):
            errs.append(f"{w}: 必须是对象")
            continue
        tr, ty = im.get("trait"), im.get("for")
        if not isinstance(tr, str) or tr not in tr_methods:
            errs.append(f"{w}.trait 不是已声明的 trait: {tr!r}")
        if not _type_ok(ty, allowed_types):
            errs.append(f"{w}.for 非法类型: {ty!r}")
        ms = im.get("methods")
        if not isinstance(ms, list) or not ms:
            errs.append(f"{w}.methods 必须是非空数组")
            continue
        seen_m: set[str] = set()
        for j, m in enumerate(ms):
            if not isinstance(m, str) or not IDENT_RE.match(m):
                errs.append(f"{w}.methods[{j}] 非法: {m!r}")
            elif m in seen_m:
                errs.append(f"{w}.methods[{j}] 重复: {m}")
            else:
                seen_m.add(m)
                if tr in tr_methods and m not in tr_methods[tr]:
                    errs.append(f"{w}.methods[{j}] {m!r} 未在 trait {tr} 中声明")
    return errs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="potato", description="Potato 形式对象校验器")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate")
    v.add_argument("files", nargs="+")
    s = sub.add_parser("show")
    s.add_argument("file")
    r = sub.add_parser("replay", help="M51: 按对象自带版本回放校验")
    r.add_argument("files", nargs="+")
    r.add_argument("--expect-version", choices=VERSIONS, default=None)
    a = ap.parse_args(argv)

    if a.cmd == "replay":
        bad = 0
        for f in a.files:
            try:
                doc = json.loads(Path(f).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                print(f"[ERR] {f}: {e}", file=sys.stderr)
                return 2
            ver = doc.get("potato")
            errs = validate(doc)
            want = a.expect_version
            mismatch = want is not None and ver != want
            if errs or mismatch:
                bad += 1
                why = f"版本 {ver!r} != 期望 {want!r}" if mismatch else errs[0]
                print(f"[INVALID] {f}: 版本={ver!r} {why}")
            else:
                print(f"[OK] {f}: 版本={ver!r} 回放通过"
                      f"{' (legacy)' if ver != VERSIONS[-1] else ''}")
        return 1 if bad else 0

    if a.cmd == "show":
        doc = json.loads(Path(a.file).read_text(encoding="utf-8"))
        print(f"unit={doc.get('unit')} lang={doc.get('language')} "
              f"caps={len(doc.get('capabilities', []))} fns={len(doc.get('functions', []))} "
              f"layouts={len(doc.get('layouts', []))}")
        for c in doc.get("capabilities", []):
            d = c["domain"]
            print(f"  cap  {c['name']}: {d['space']}[{d['lo']}..{d['hi']}]"
                  f"{' revocable' if c['revocable'] else ''}")
        for f in doc.get("functions", []):
            args = ", ".join(f"{p['name']}: {p['type']}" for p in f["params"])
            print(f"  fn   {f['name']}({args}) -> {f['ret']}")
        for r in doc.get("layouts", []):
            print(f"  rec  {r['name']} size={r['size']} fields={len(r['fields'])}")
        for c in doc.get("consts", []):
            print(f"  const {c['name']}: {c['type']} = {c['value']}")
        for t in doc.get("types", []):
            print(f"  type {t['name']} fields={len(t['fields'])}")
        for g in doc.get("generics", []):
            print(f"  gen  {g['kind']} {g['name']}<{', '.join(g['params'])}>")
        for i in doc.get("instances", []):
            print(f"  inst {i['kind']} {i['name']} = {i['of']}<{', '.join(i['args'])}>")
        for t in doc.get("traits", []):
            print(f"  trait {t['name']} methods={','.join(t['methods'])}")
        for i in doc.get("impls", []):
            print(f"  impl {i['trait']} for {i['for']} methods={','.join(i['methods'])}")
        for x in doc.get("excluded", []):
            print(f"  excl {x}")
        return 0

    bad = 0
    for f in a.files:
        try:
            doc = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"[ERR] {f}: {e}", file=sys.stderr)
            return 2
        errs = validate(doc)
        if errs:
            bad += 1
            print(f"[INVALID] {f}: {len(errs)} 项")
            for e in errs:
                print("  " + e)
        else:
            print(f"[OK] {f}: 形式对象合法")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
