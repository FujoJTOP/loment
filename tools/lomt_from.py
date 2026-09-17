#!/usr/bin/env python3
# lomt_from.py — Potato 形式对象 -> L1 (.lomt) **接口单元** (docs/179, docs/175 §5)
#
# 定位 (docs/140 §9): 源语言的语法属于源语言, 表示属于 Potato, L1 是冻结核心的输入。
# 本工具补的是多语法前端那条链的**后半段**:
#
#     任意源语法  --potato_from-->  Potato 形式对象  --lomt_from-->  .lomt
#                                                                     |
#                                                        （进入冻结核心, docs/158）
#
# **为什么只出接口, 不出带体的实现**: Potato 的 schema 里**没有函数体** ——
# 表示层记的是"有什么"(结构 / 能力 / 契约 / 布局), 不是"怎么算" (docs/142 §1、
# docs/147、docs/178)。所以这一版的前端产出**声明单元**: 类型、常量、能力、函数签名。
# 函数一律发 `extern fn` —— 那道接口的实现在源语言那一侧, 调用点走平台 C ABI
# (docs/173 §2)。这不是权宜之计: 一份"把那个模块的接口抄过来"的文件, 语义上本来就是
# FFI 声明, 而且它**真的能用** —— 生成的单元可以直接 `use`, 链接外面编好的目标文件。
#
# **发不出来的东西报错, 不静默丢**: traits / impls / generics / instances / layouts
# 非空 = 这个对象超出了本工具的表示面, 报错退出。与 lomelf、自举驱动一贯的
# "把静默错编改成报错退出" 同一条纪律 (docs/167)。**少发一个函数比发错更坏**:
# 调用点会静默解析到别的东西上。
#
# 用法:
#   python tools/lomt_from.py OBJ.potato.json [--out OUT.lomt]
#   python tools/lomt_from.py SRC.rs --lang rust --out OUT.lomt      # 一步到位
#   python tools/lomt_from.py SRC.c  --lang c    --out OUT.lomt
# 退出码: 0 = 成功 / 1 = 对象里有发不出来的东西 / 2 = 用法或读取错误。

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import potato  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

#: 顶名字必须与 L1 的标识符文法一致 (docs/143)。`potato_from` 已经过一道 `_ident`,
#: 但对象可以是手写的 —— 所以这里再挡一次, 报错而不是产出一份编不过的源码。
def _is_ident(s: object) -> bool:
    return isinstance(s, str) and bool(potato.IDENT_RE.match(s))


#: 超出本工具表示面的字段: 非空就报错 (见文件头)。
_UNSUPPORTED = ("traits", "impls", "generics", "instances")


class NotRepresentable(Exception):
    """对象里有本工具发不出来的东西。**不猜、不跳过** —— 直接说清是哪一项。"""


def _check_representable(doc: dict) -> None:
    for k in _UNSUPPORTED:
        v = doc.get(k)
        if v:
            raise NotRepresentable(
                f"{k} 非空 ({len(v)} 项) —— Potato 记了它, 但 L1 那侧的对应形状还没接上; "
                f"**不静默丢**, 报错退出")
    # layouts 是 `.lom` 的二进制版式 (Header 48B 那种), 属于 L0 单源, 不是 L1 的结构体。
    if doc.get("layouts"):
        raise NotRepresentable(
            f"layouts 非空 ({len(doc['layouts'])} 项) —— 那是 L0 (`lom/*.lom`) 的二进制版式, "
            f"应当由 `lomc`/`lomdoc` 生成, 不经过 L1")
    if doc.get("imports"):
        raise NotRepresentable(
            f"imports 非空 ({len(doc['imports'])} 项) —— Potato 的 imports 是**单元名**, "
            f"而 L1 的 `use` 要么给路径要么按层搜 (docs/143); 名字到路径的对应本工具不知道, "
            f"**不猜**")


def _ty(t: object) -> str:
    if not isinstance(t, str) or not t:
        raise NotRepresentable(f"类型 {t!r} 不是字符串")
    return t


#: FFI 第 1 阶段收的签名面 (docs/173 §3): 标量 + `ptr`。聚合类型与 `str` **按值**传参
#: 会改变调用点代码形状, 一律留到后面 —— 这里照同一条线卡住, 与 `lomentc` 的
#: `_extern_ty_ok` 对齐 (那边是编译器侧的闸门, 这里是前端侧的**同一道**闸门)。
_FFI_SCALARS = ("u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64", "bool", "ptr", "()")


def _ffi_ok(t: object) -> bool:
    return isinstance(t, str) and t in _FFI_SCALARS


def _sig_params(fn: dict) -> str:
    out = []
    for p in fn.get("params") or []:
        n = p.get("name")
        if not _is_ident(n):
            raise NotRepresentable(f"函数 {fn.get('name')!r} 的形参名 {n!r} 不是标识符")
        out.append(f"{n}: {_ty(p.get('type'))}")
    return ", ".join(out)


def emit_lomt(doc: dict) -> tuple[str, list[tuple[str, str]]]:
    """Potato 形式对象 -> (L1 接口单元源码, 跳过的项)。

    **确定性**: 同一份对象永远出同一串字节 (所以生成物能进判据)。
    第二项是 `(名字, 原因)` —— 调用方**必须报出来** (见 `main`)。
    """
    _check_representable(doc)
    if not _is_ident(doc.get("unit")):
        raise NotRepresentable(f"unit {doc.get('unit')!r} 不是标识符 (L1 的 module 名要求它)")

    skipped: list[tuple[str, str]] = []
    ver = doc.get("potato")
    lines: list[str] = [
        "// 由 tools/lomt_from.py 从 Potato 形式对象生成 —— 请勿手改。",
        "// 它是**接口单元**: 函数是 `extern fn` (实现在源语言那一侧,",
        "// 调用点走平台 C ABI, docs/173 §2)。",
        f"// 源对象: unit={doc.get('unit')} language={doc.get('language')} potato={ver}",
        "",
        f"module {doc['unit']}",
    ]

    # ---- 能力域
    caps = doc.get("capabilities") or []
    if caps:
        lines.append("")
        lines.append("// ---- 能力域 (docs/146)")
        for c in caps:
            n = c.get("name")
            if not _is_ident(n):
                raise NotRepresentable(f"能力名 {n!r} 不是标识符")
            d = c.get("domain") or {}
            lo, hi = d.get("lo"), d.get("hi")
            if not isinstance(lo, int) or not isinstance(hi, int) or isinstance(lo, bool):
                raise NotRepresentable(f"能力 {n} 的域 lo/hi 必须是整数, 得到 {lo!r}/{hi!r}")
            space = d.get("space")
            if not _is_ident(space):
                raise NotRepresentable(f"能力 {n} 的空间名 {space!r} 不是标识符")
            tail = " revocable" if c.get("revocable") else ""
            lines.append(f"capability {n} : {space}[{lo}..{hi}]{tail}")

    # ---- 常量
    consts = doc.get("consts") or []
    if consts:
        lines.append("")
        lines.append("// ---- 常量")
        for c in consts:
            n = c.get("name")
            if not _is_ident(n):
                raise NotRepresentable(f"常量名 {n!r} 不是标识符")
            v = c.get("value")
            if not isinstance(v, int) or isinstance(v, bool):
                raise NotRepresentable(f"常量 {n} 的值 {v!r} 不是整数 (L1 常量只收整型)")
            lines.append(f"pub const {n}: {_ty(c.get('type'))} = {v};")

    # ---- 结构体
    for s in doc.get("types") or []:
        n = s.get("name")
        if not _is_ident(n):
            raise NotRepresentable(f"结构体名 {n!r} 不是标识符")
        fields = s.get("fields") or []
        if not fields:
            raise NotRepresentable(f"结构体 {n} 没有字段 (L1 不收空结构体)")
        lines.append("")
        lines.append(f"pub struct {n} {{")
        seen: set[str] = set()
        for f in fields:
            fn = f.get("name")
            if not _is_ident(fn):
                raise NotRepresentable(f"结构体 {n} 的字段名 {fn!r} 不是标识符")
            if fn in seen:
                raise NotRepresentable(f"结构体 {n} 的字段 {fn} 重复")
            seen.add(fn)
            lines.append(f"    {fn}: {_ty(f.get('type'))},")
        lines.append("}")

    # ---- 枚举
    for e in doc.get("enums") or []:
        n = e.get("name")
        if not _is_ident(n):
            raise NotRepresentable(f"枚举名 {n!r} 不是标识符")
        vs = e.get("variants") or []
        if not vs:
            raise NotRepresentable(f"枚举 {n} 没有变体 (L1 不收空枚举)")
        payloads = e.get("payloads") or {}
        lines.append("")
        lines.append(f"pub enum {n} {{")
        seen = set()
        for v in vs:
            if not _is_ident(v):
                raise NotRepresentable(f"枚举 {n} 的变体名 {v!r} 不是标识符")
            if v in seen:
                raise NotRepresentable(f"枚举 {n} 的变体 {v} 重复")
            seen.add(v)
            pt = payloads.get(v)
            lines.append(f"    {v}({_ty(pt)})," if pt else f"    {v},")
        lines.append("}")

    # ---- 函数 (一律 extern: 实现在源语言那一侧)
    # 先攒进 fn_lines: **一条都没发出来时不留空的分节标题** —— 一个只有标题的
    # "// ---- 外部函数" 会让读者以为下面本该有东西 (而原因是被跳过了, 那个报在 stderr)。
    fns = doc.get("functions") or []
    fn_lines: list[str] = []
    if fns:
        names: set[str] = set()
        for f in fns:
            n = f.get("name")
            if not _is_ident(n):
                raise NotRepresentable(f"函数名 {n!r} 不是标识符")
            if n in names:
                raise NotRepresentable(f"函数 {n} 重复")
            names.add(n)
            # **两条闸门, 缺一不可**。少发一个函数在这里是**安全的** —— Loment 没有重载,
            # 名字是精确的, 所以调用点会得到"未声明的函数"(E002) 而不是静默绑到别人身上。
            # 但**必须报出来** (见 `main`), 不能沉默。
            abi = f.get("abi")
            if abi != "c":
                skipped.append((n, f"调用约定不是 C ABI"
                                   + (f" (abi={abi})" if abi else " (对象里没记 ABI)")))
                continue
            bad = [p.get("name") for p in (f.get("params") or [])
                   if not _ffi_ok(p.get("type"))]
            if not _ffi_ok(f.get("ret") or "()") or bad:
                skipped.append((n, "签名超出 FFI 第 1 阶段 (只收标量与 ptr, docs/173 §3)"
                                   + (f"; 参数 {bad}" if bad else "")))
                continue
            r = f.get("ret")
            head = f"pub extern fn {n}({_sig_params(f)})"
            if r and r != "()":
                head += f" -> {_ty(r)}"
            fn_lines.append(head + ";")
        if fn_lines:
            lines.append("")
            lines.append("// ---- 外部函数 (docs/173: 声明在此, 实现在源语言那一侧, C ABI)")
            lines += fn_lines

    # ---- 出界声明
    for x in doc.get("excluded") or []:
        if not isinstance(x, str) or not x:
            raise NotRepresentable(f"excluded 项 {x!r} 不是非空字符串")
        lines.append("")
        lines.append(f'excluded "{x}"')

    return "\n".join(lines) + "\n", skipped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lomt_from",
                                 description="Potato 形式对象 -> L1 接口单元")
    ap.add_argument("path", help=".potato.json, 或源文件 (配 --lang)")
    ap.add_argument("--lang", choices=("auto", "python", "c", "rust"), default=None,
                    help="给了就先把源文件转成形式对象再发 L1")
    ap.add_argument("--mode", choices=("strict", "lenient"), default="strict")
    ap.add_argument("--out", metavar="PATH")
    a = ap.parse_args(argv)

    try:
        if a.lang is not None:
            import potato_from
            # **先说清认成了什么** —— 这就是"检测"这一步: 用户拿一份 `.lomt` 装着 C
            # 过来, 他要看到的是"认出来了", 而不是默默转出一个东西。
            got, why = potato_from.resolve_lang(Path(a.path), a.lang)
            print(f"[detect] {a.path} -> {got or '认不出'}（{why}）", file=sys.stderr)
            if got == "loment":
                print(f"[ERR] 这本来就是 Loment 语法, 不该走前端 —— "
                      f"直接交给编译器: loment check {a.path}", file=sys.stderr)
                return 2
            doc, rep = potato_from.transcribe(Path(a.path), a.lang, a.mode)
            # **转写那一步的跳过项也要报** —— 只报发射那一步等于把"前一步丢的"藏起来。
            # 2026-09-17 实测: 一份 5 函数的 C 只发出 1 个, 而这里一条 `[skip]` 都没有,
            # 看起来像"它只认出 1 个函数"(真相是另 4 个在转写那步因类型没映射被丢)。
            for s in rep.skipped:
                print(f"[skip] {s['name']}: {s['why']}", file=sys.stderr)
        else:
            doc = json.loads(Path(a.path).read_text(encoding="utf-8"))
        # 对象自身先要合法 —— 拿一份非法对象去发 L1 等于把错误往后传。
        errs = potato.validate(doc)
        if errs:
            print(f"[ERR] 形式对象不合法: {errs[0]}", file=sys.stderr)
            return 2
        text, skipped = emit_lomt(doc)
    except NotRepresentable as e:
        print(f"[ERR] 发不出来: {e}", file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError) as e:
        print(f"[ERR] 读取失败: {e}", file=sys.stderr)
        return 2

    # **跳过必须出声**。它们不进产物 (那是对的: 宁缺勿错), 但沉默就等于让用户以为
    # "那个函数本来就不在那儿"。写到 stderr, 不污染 stdout 的源码。
    for n, why in skipped:
        print(f"[skip] {n}: {why}", file=sys.stderr)

    if a.out:
        # `newline="\n"`: 与 loment_build.py 同一处坑 (见那里的注释)
        Path(a.out).write_text(text, encoding="utf-8", newline="\n")
        n_fn = text.count("pub extern fn ")
        print(f"[OK] {a.path} -> {a.out}"
              + (f" (函数 {n_fn} 条, 跳过 {len(skipped)} 条)" if skipped else ""))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
