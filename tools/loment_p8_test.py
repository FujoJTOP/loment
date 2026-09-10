#!/usr/bin/env python3
# loment_p8_test.py — P8 自举自检 (M79–, docs/150)
#
# 运行: python tools/loment_p8_test.py   (退出码 0 = 全绿)
# M79: Loment 版 lexer 的 token 流必须与 Python 版 (lomc.lex) 一致。

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomc  # noqa: E402
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LEXER = ROOT / "loment" / "selfhost" / "lexer.lomt"
TESTS: list[tuple[str, object]] = []
KIND = {"ident": 0, "number": 1, "string": 2, "punct": 3, "eof": 4}


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _clang() -> str | None:
    p = shutil.which("clang")
    if p:
        return p
    fallback = r"C:\Program Files\LLVM\bin\clang.exe"
    return fallback if Path(fallback).exists() else None


DRIVER = """#include <stdio.h>
#include <stdlib.h>
extern unsigned int lex(char *src, unsigned int len, unsigned char *out);
int main(int argc, char **argv) {
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 2;
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    char *buf = malloc((size_t)n + 1);
    if (fread(buf, 1, (size_t)n, f) != (size_t)n) return 2;
    unsigned char *out = malloc(20 * ((size_t)n + 8));
    unsigned int cnt = lex(buf, (unsigned int)n, out);
    printf("%u\\n", cnt);
    for (unsigned int i = 0; i < cnt; i++) {
        unsigned char *p = out + 20 * i;
        unsigned int kind = *(unsigned int *)(p + 0), st = *(unsigned int *)(p + 4),
                     ln = *(unsigned int *)(p + 8), line = *(unsigned int *)(p + 12),
                     col = *(unsigned int *)(p + 16);
        printf("%u %u %u %u %u\\n", kind, st, ln, line, col);
    }
    return 0;
}
"""


def _build(td: str) -> Path:
    mod = lomentc.load(LEXER)
    deps = lomentc.resolve_deps(mod, ROOT, LEXER.parent, entry=LEXER)
    assert not lomentc.check(mod, deps=deps)
    ll = Path(td) / "lexer.ll"
    ll.write_text(lomentc.emit_llvm(mod, ROOT, deps), encoding="utf-8")
    c = Path(td) / "drv.c"
    c.write_text(DRIVER, encoding="utf-8")
    exe = Path(td) / "lexer.exe"
    r = subprocess.run(
        [shutil.which("clang") or r"C:\Program Files\LLVM\bin\clang.exe",
         "-O1", "-o", str(exe), str(c), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr
    return exe


def _loment_tokens(exe: Path, src: Path) -> list[tuple[int, int, int, int, int]]:
    out = subprocess.run([shutil.which(str(exe)) or str(exe), str(src)],
                         capture_output=True, text=True, shell=False)
    assert out.returncode == 0, out.stderr
    lines = out.stdout.strip().splitlines()
    n = int(lines[0])
    assert len(lines) - 1 == n, (len(lines) - 1, n)
    return [tuple(int(x) for x in ln.split()) for ln in lines[1:]]


def _python_tokens(src: Path) -> list[tuple[int, int, int, int, int]]:
    # 按原始字节解码 (不经换行归一化), 否则 CRLF 检出会让字节偏移整体偏移
    text = src.read_bytes().decode("utf-8")
    line_starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            line_starts.append(i + 1)
    out = []
    for t in lomc.lex(text):
        start = line_starts[t.line - 1] + t.col - 1
        # Loment 版按 UTF-8 字节偏移; 这里换算成字节
        bstart = len(text[:start].encode("utf-8"))
        if t.kind == "eof":
            out.append((4, bstart, 0, t.line, t.col))
            continue
        if t.kind == "string":
            j = start + 1
            while j < len(text) and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append((2, bstart, len(text[start:j + 1].encode("utf-8")), t.line, t.col))
        else:
            out.append((KIND[t.kind], bstart, len(t.val.encode("utf-8")), t.line, t.col))
    return out


@test
def test_m79_loment_lexer_matches_python():
    if not _clang():
        print("      SKIP: 无 clang")
        return
    files = [ROOT / "loment" / "examples" / "toolchain.lomt",
             ROOT / "loment" / "examples" / "all_loment.lomt",
             ROOT / "loment" / "examples" / "native_cap.lomt",
             ROOT / "loment" / "selfhost" / "lexer.lomt"]
    with tempfile.TemporaryDirectory() as td:
        exe = _build(td)
        for f in files:
            got = _loment_tokens(exe, f)
            want = _python_tokens(f)
            assert len(got) == len(want), f"{f.name}: {len(got)} != {len(want)}"
            for i, (g, w) in enumerate(zip(got, want)):
                assert g == w, f"{f.name} token#{i}: Loment {g} != Python {w}"


PARSER = ROOT / "loment" / "selfhost" / "parser.lomt"
PARSER_DRIVER = """#include <stdio.h>
#include <stdlib.h>
extern unsigned int lex(char *src, unsigned int len, unsigned char *out);
extern unsigned int parse(char *src, unsigned char *toks, char *out);
int main(int argc, char **argv) {
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 2;
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    char *buf = malloc((size_t)n + 1);
    if (fread(buf, 1, (size_t)n, f) != (size_t)n) return 2;
    unsigned char *toks = malloc(20 * ((size_t)n + 8));
    char *out = malloc((size_t)n * 4 + 1024);
    lex(buf, (unsigned int)n, toks);
    unsigned int m = parse(buf, toks, out);
    out[m] = 0;
    printf("%.*s\\n", (int)m, out);
    return 0;
}
"""


def _build_parser(td: str) -> Path:
    mod = lomentc.load(PARSER)
    deps = lomentc.resolve_deps(mod, ROOT, PARSER.parent, entry=PARSER)
    assert not lomentc.check(mod, deps=deps)
    ll = Path(td) / "parser.ll"
    ll.write_text(lomentc.emit_llvm(mod, ROOT, deps), encoding="utf-8")
    c = Path(td) / "pdrv.c"
    c.write_text(PARSER_DRIVER, encoding="utf-8")
    exe = Path(td) / "parser.exe"
    r = subprocess.run(
        [shutil.which("clang") or r"C:\Program Files\LLVM\bin\clang.exe",
         "-O1", "-o", str(exe), str(c), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-500:]
    return exe


class Unsupported(Exception):
    pass


def _ex(e) -> str:
    """表达式 -> 规范 dump (与 Loment 版 parser 的约定一致)。"""
    n = type(e).__name__
    if n == "IntLit":
        return f"(int {e.value})"
    if n == "BoolLit":
        return f"(bool {'true' if e.value else 'false'})"
    if n == "Ident":
        return f"(id {e.name})"
    if n == "Call":
        return "(call " + e.name + "".join(" " + _ex(a) for a in e.args) + ")"
    if n == "Bin":
        # 与 Loment 版一致: 左操作数已在前面输出, 这里只包住"运算符 + 右操作数"
        return f"{_ex(e.left)}(bin {e.op} {_ex(e.right)})"
    if n == "Un":
        return f"(un {e.op} {_ex(e.expr)})"
    if n == "Cast":
        return f"{_ex(e.expr)}(cast {e.type})"
    if n == "FieldAccess":
        return f"{_ex(e.obj)}(field {e.name})"
    if n == "Index":
        return f"{_ex(e.obj)}(idx {_ex(e.idx)})"
    raise Unsupported(n)


def _st(s) -> str:
    """语句 -> 规范 dump。"""
    n = type(s).__name__
    if n == "Let":
        base = f"(let {s.name} {s.type}"
        if s.expr is not None:
            base += " " + _ex(s.expr)
        return base + ")"
    if n == "Return":
        return f"(ret {_ex(s.expr)})"
    if n == "ExprStmt":
        return _ex(s.expr)
    if n == "Assign":
        if type(s.target).__name__ != "Ident":
            raise Unsupported("Assign/" + type(s.target).__name__)
        return f"{_ex(s.target)} (set {_ex(s.expr)})"
    if n == "If":
        then = "".join(" " + _st(x) for x in s.then)
        if s.otherwise and type(s.otherwise[0]).__name__ == "If" and len(s.otherwise) == 1:
            els = "(" + _st(s.otherwise[0]) + ")"
        else:
            els = "(" + "".join(" " + _st(x) for x in s.otherwise) + ")"
        return f"(if {_ex(s.cond)} ({then}) {els})"
    if n == "While":
        body = "".join(" " + _st(x) for x in s.body)
        return f"(while {_ex(s.cond)} ({body}))"
    raise Unsupported(n)


def _fn_mod(mod) -> str:
    fns = []
    for f in mod.funcs:
        s = f"(fn {f.name}"
        for p in f.params:
            s += f" (p {p.name} {p.type})"
        if f.ret != "()":
            s += f" -> {f.ret}"
        if f.body:
            s += " (" + "".join(" " + _st(x) for x in f.body) + ")"
        fns.append(s + ")")
    return f"(module {mod.name}" + "".join(" " + x for x in fns) + ")"


def _scan(obj, bad: tuple[str, ...]) -> None:
    import dataclasses
    if isinstance(obj, list):
        for x in obj:
            _scan(x, bad)
    elif dataclasses.is_dataclass(obj):
        if type(obj).__name__ in bad:
            raise Unsupported(type(obj).__name__)
        for f in dataclasses.fields(obj):
            _scan(getattr(obj, f.name), bad)


def _py_dump(src: Path) -> str:
    mod = lomentc.load(src)
    _scan(mod, ("StrLit",))  # 字符串字面量本阶段不覆盖
    if any(t.kind == "number" and t.val.lower().startswith("0x")
           for t in lomc.lex(src.read_text(encoding="utf-8"))):
        raise Unsupported("十六进制字面量 (本阶段不覆盖)")
    return _fn_mod(mod)


@test
def test_m80_loment_parser_ast_dump():
    """M80: module/fn/语句/表达式的 AST dump 与 Python 版逐字符一致。"""
    if not _clang():
        print("      SKIP: 无 clang")
        return
    candidates = [ROOT / "loment" / "examples" / n
                  for n in ("mathutil.lomt", "bytes.lomt", "ahci.lomt", "allocator.lomt",
                            "fuc_node.lomt")]
    files = []
    for f in candidates:
        try:
            _py_dump(f)
            files.append(f)
        except Unsupported as e:
            print(f"      SKIP {f.name}: {e}")
    assert len(files) >= 4, "可用对照文件太少"
    with tempfile.TemporaryDirectory() as td:
        exe = _build_parser(td)
        for f in files:
            got = subprocess.run([shutil.which(str(exe)) or str(exe), str(f)],
                                 capture_output=True, text=True, shell=False).stdout.strip()
            want = _py_dump(f)
            assert got == want, f"{f.name}:\n Loment {got[:200]}\n Python {want[:200]}"


def main() -> int:
    failed = []
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, e))
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\nloment_p8_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
