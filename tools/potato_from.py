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


def from_python(src: str, name: str, mode: str = "strict") -> tuple[dict, Report]:
    rep = Report(name, "python", mode)
    doc = _blank(_ident(Path(name).stem), "python")
    tree = ast.parse(src)
    known: set[str] = set()
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
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id.isupper() and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, int) and not isinstance(node.value.value, bool):
            doc["consts"].append({"name": node.targets[0].id, "type": "i64",
                                  "value": node.value.value})
            rep.ok += 1
    return _finish(doc, rep)


# ---------------------------------------------------------------- C (轻量解析)

_C_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_C_STRUCT = re.compile(r"\bstruct\s+([A-Za-z_]\w*)\s*\{([^}]*)\}", re.S)
_C_FIELD = re.compile(r"^\s*(.+?)\s+([A-Za-z_]\w*)\s*(\[\s*\d+\s*\])?\s*;", re.M)
_C_FN = re.compile(r"^[ \t]*(?:static\s+|inline\s+|const\s+)*"
                   r"([A-Za-z_][\w \t\*]*?)\s+([A-Za-z_]\w*)\s*\(([^;{)]*)\)\s*[;{]",
                   re.M)
_C_KEYWORDS = {"if", "while", "for", "switch", "return", "sizeof", "do", "else"}


def _c_type(t: str, mode: str, known: set[str]) -> str | None:
    t = re.sub(r"\s+", " ", t.strip())
    if t.endswith("[]"):
        t = t[:-2] + " *"
    if t in C_TYPES:
        return C_TYPES[t]
    if t.endswith("*"):
        base = re.sub(r"\s+", " ", t[:-1].strip())
        return "str" if base == "char" else "ptr"
    if t in known:
        return t
    return "ptr" if mode == "lenient" else None


def from_c(src: str, name: str, mode: str = "strict") -> tuple[dict, Report]:
    rep = Report(name, "c", mode)
    doc = _blank(_ident(Path(name).stem), "c")
    body = _C_COMMENT.sub(" ", src)
    body = re.sub(r"^[ \t]*#.*$", "", body, flags=re.M)
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
        doc["functions"].append({"name": fn, "params": ps, "ret": ret, "abi": "c"})
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

LANGS = {"python": from_python, "c": from_c, "rust": from_rust}
EXT = {".py": "python", ".c": "c", ".h": "c", ".rs": "rust"}


def transcribe(path: Path, lang: str = "auto", mode: str = "strict"):
    if lang == "auto":
        lang = EXT.get(path.suffix, "")
        if not lang:
            raise ValueError(f"无法从扩展名判断语言: {path}")
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
