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


CHECKER = ROOT / "loment" / "selfhost" / "checker.lomt"
NEG = ROOT / "loment" / "selfhost" / "neg"
CHECKER_DRIVER = """#include <stdio.h>
#include <stdlib.h>
extern unsigned int lex(char *src, unsigned int len, unsigned char *out);
extern unsigned int check(char *src, unsigned char *toks, unsigned char *errs);
int main(int argc, char **argv) {
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 2;
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    char *buf = malloc((size_t)n + 8);
    if (fread(buf, 1, (size_t)n, f) != (size_t)n) return 2;
    buf[n] = 0;
    unsigned char *toks = malloc(20 * ((size_t)n + 16));
    unsigned char *errs = malloc(1024);
    lex(buf, (unsigned int)n, toks);
    unsigned int m = check(buf, toks, errs);
    printf("%u", m);
    for (unsigned int i = 0; i < m; i++) {
        unsigned int code = *(unsigned int *)(errs + 8 * i);
        unsigned int tk = *(unsigned int *)(errs + 8 * i + 4);
        unsigned int st = *(unsigned int *)(toks + 20 * tk + 4);
        unsigned int ln = *(unsigned int *)(toks + 20 * tk + 8);
        printf(" %u@%u:%.*s", code, tk, (int)(ln > 24 ? 24 : ln), buf + st);
    }
    printf("\\n");
    return 0;
}
"""

# 错误码口径: 与 checker.lomt 的 E_* 常量一致
E_DUP, E_TYPE, E_FN, E_ARITY = 1, 2, 3, 4
PY_RULES = [
    (E_DUP, r"重复定义|重名"),
    (E_TYPE, r"未声明"),
    (E_FN, r"未定义的函数"),
    (E_ARITY, r"需要 \d+ 个实参"),
]


def _py_codes(src: Path) -> list[int]:
    """Python 侧把错误消息归类成同一套错误码 (与 checker.lomt 对照)。"""
    import re
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    out: list[int] = []
    for e in errs:
        for code, pat in PY_RULES:
            if re.search(pat, e):
                out.append(code)
                break
    return sorted(set(out))


def _build_checker(td: str) -> Path:
    mod = lomentc.load(CHECKER)
    deps = lomentc.resolve_deps(mod, ROOT, CHECKER.parent, entry=CHECKER)
    assert not lomentc.check(mod, deps=deps), lomentc.check(mod, deps=deps)[:2]
    ll = Path(td) / "checker.ll"
    ll.write_text(lomentc.emit_llvm(mod, ROOT, deps), encoding="utf-8")
    c = Path(td) / "cdrv.c"
    c.write_text(CHECKER_DRIVER, encoding="utf-8")
    exe = Path(td) / "checker.exe"
    r = subprocess.run(
        [shutil.which("clang") or r"C:\Program Files\LLVM\bin\clang.exe",
         "-O1", "-o", str(exe), str(c), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    return exe


def _loment_codes(exe: Path, src: Path) -> list[int]:
    """返回 (错误码列表, 明细字符串) —— 明细用于失败时定位。"""
    out = subprocess.run([shutil.which(str(exe)) or str(exe), str(src)],
                         capture_output=True, text=True, shell=False)
    assert out.returncode == 0, out.stderr
    parts = out.stdout.split()
    n = int(parts[0])
    detail = parts[1:1 + n]
    codes = sorted({int(x.split("@")[0]) for x in detail})
    return codes, " ".join(detail)


@test
def test_m81_loment_checker_matches_python():
    """M81: Loment 版检查器与 Python 版的判定一致 (负例拒绝 + 正例接受, 错误码对照)。"""
    if not _clang():
        print("      SKIP: 无 clang")
        return
    neg = sorted(NEG.glob("*.lomt"))
    assert len(neg) >= 6, len(neg)
    # 正例只取"单编译单元"文件: Loment 版检查器不解析 use 导入 (见 docs/150 边界)
    pos = [ROOT / "loment" / "selfhost" / "pos" / "ok.lomt",
           ROOT / "loment" / "examples" / "mathutil.lomt",
           ROOT / "loment" / "examples" / "bytes.lomt",
           ROOT / "loment" / "examples" / "native.lomt"]
    with tempfile.TemporaryDirectory() as td:
        exe = _build_checker(td)
        for f in neg:
            want, (got, det) = _py_codes(f), _loment_codes(exe, f)
            assert want, f"{f.name}: Python 未报错"
            assert got, f"{f.name}: Loment 未报错"
            assert set(got) <= set(want), f"{f.name}: Loment {got} ⊄ Python {want} [{det}]"
        for f in pos:
            want, (got, det) = _py_codes(f), _loment_codes(exe, f)
            assert want == [] and got == [], f"{f.name}: 正例被拒 (py={want} loment={got}) [{det}]"


CODEGEN = ROOT / "loment" / "selfhost" / "codegen.lomt"
IR_TARGET = ROOT / "loment" / "selfhost" / "ir_const.lomt"
CODEGEN_DRIVER = """#include <stdio.h>
#include <stdlib.h>
extern unsigned int lex(char *src, unsigned int len, unsigned char *out);
extern unsigned int emit_module(char *src, unsigned char *toks, char *out);
int main(int argc, char **argv) {
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 2;
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    char *buf = malloc((size_t)n + 8);
    if (fread(buf, 1, (size_t)n, f) != (size_t)n) return 2;
    buf[n] = 0;
    unsigned char *toks = malloc(20 * ((size_t)n + 16));
    char *out = malloc((size_t)n * 8 + 8192);
    lex(buf, (unsigned int)n, toks);
    unsigned int m = emit_module(buf, toks, out);
    printf("%.*s", (int)m, out);
    return 0;
}
"""


def _build_codegen(td: str) -> Path:
    mod = lomentc.load(CODEGEN)
    deps = lomentc.resolve_deps(mod, ROOT, CODEGEN.parent, entry=CODEGEN)
    assert not lomentc.check(mod, deps=deps), lomentc.check(mod, deps=deps)[:2]
    ll = Path(td) / "codegen.ll"
    ll.write_text(lomentc.emit_llvm(mod, ROOT, deps), encoding="utf-8")
    c = Path(td) / "gdrv.c"
    c.write_text(CODEGEN_DRIVER, encoding="utf-8")
    exe = Path(td) / "codegen.exe"
    r = subprocess.run(
        [shutil.which("clang") or r"C:\Program Files\LLVM\bin\clang.exe",
         "-O1", "-o", str(exe), str(c), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    return exe


@test
def test_m85_checker_accepts_corpus_units():
    """M85 后半: 自举 checker 在**拼接单元**上的覆盖面 (driver 视角, docs/150)。

    M81 的判据是"负例集判定一致", 那是在单文件上跑; 要当"闸门"还得能**放行合法程序**。
    这一条把覆盖面钉住: 41 个单元的拼接体 (依赖 + 本文件 + 预置枚举, 与驱动器装载的同一份)
    里, 除两个已登记缺口外必须**一条诊断都没有**。

    两个缺口都是登记过的, 且方向相反 —— 缺口清单和覆盖面一起钉: 缺口被修好时这条会
    提醒更新清单 (而不是让它悄悄过期)。
    """
    if not _clang():
        print("      SKIP: 无 clang")
        return
    gaps = {
        # impl/trait 块: checker 不建模, `impl T for Y { fn m(&self) }` 会误读签名
        "native_trait.lomt",
        # 非目标: 参考实现的 IR 后端自己也发不出来 (native: inb 未实现), 它不是单元的合法形状
        "native_raii.lomt",
    }
    with tempfile.TemporaryDirectory() as td:
        exe = _build_checker(td)
        ok = []
        for target in sorted(list((ROOT / "loment" / "examples").glob("*.lomt"))
                             + list((ROOT / "loment" / "selfhost").glob("*.lomt"))):
            unit = Path(td) / f"u_{target.stem}.lomt"
            unit.write_text(_unit_text(target), encoding="utf-8", newline="\n")
            codes, det = _loment_codes(exe, unit)
            if target.name in gaps:
                assert codes, f"{target.name}: 缺口已消失 —— 请从 gaps 里删掉它"
                continue
            assert not codes, f"{target.name}: 单元上有假报 {codes} {det[:100]}"
            ok.append(target.name)
        print(f"      checker 放行单元: {len(ok)}/{len(ok)} 无诊断 (已登记缺口 {len(gaps)} 个)")


@test
def test_m82_loment_codegen_byte_identical():
    """M82(子集): Loment 版 codegen 的 .ll 与 Python 版逐字节一致 (常量/参数返回 + 表达式)。"""
    if not _clang():
        print("      SKIP: 无 clang")
        return
    with tempfile.TemporaryDirectory() as td:
        exe = _build_codegen(td)
        for target in (IR_TARGET, ROOT / "loment" / "selfhost" / "ir_expr.lomt",
                       ROOT / "loment" / "selfhost" / "ir_stmt.lomt",
                       ROOT / "loment" / "selfhost" / "ir_logic.lomt",
                       ROOT / "loment" / "selfhost" / "ir_cast.lomt",
                       ROOT / "loment" / "selfhost" / "ir_mem.lomt",
                       ROOT / "loment" / "selfhost" / "ir_for.lomt",
                       ROOT / "loment" / "selfhost" / "ir_div.lomt",
                       ROOT / "loment" / "selfhost" / "ir_builtin.lomt",
                       ROOT / "loment" / "selfhost" / "ir_call5.lomt"):
            mod = lomentc.load(target)
            deps = lomentc.resolve_deps(mod, ROOT, target.parent, entry=target)
            want = lomentc.emit_llvm(mod, ROOT, deps)
            got = _run_codegen(exe, target, td)
            if got != want:
                i = next((k for k in range(min(len(got), len(want))) if got[k] != want[k]), None)
                a = max(0, (i or 0) - 60)
                raise AssertionError(
                    f"{target.name} 首个差异 @{i}:\n"
                    f" loment {got[a:(i or 0) + 80]!r}\n python {want[a:(i or 0) + 80]!r}")


def _dep_paths(target: Path) -> list[Path]:
    """按 lomentc.resolve_deps 的规则取依赖文件 (被依赖者在前), 并与参考实现的模块名序列核对。

    Loment 版 codegen 只吃**单个编译单元**(不解析 `use`) —— 依赖装载由驱动/夹具负责,
    与 M80/M81 自举阶段的边界一致。这里把"参考实现解析出的模块序"当判据钉死:
    路径规则一旦与 lomentc 漂移, 名字序列就对不上, 测试会直接失败。
    """
    mod = lomentc.load(target)
    deps = lomentc.resolve_deps(mod, ROOT, target.parent, entry=target)
    paths: list[Path] = []
    seen: set[Path] = set()

    def visit(m, cur_base: Path) -> None:
        for imp in m.imports:
            p = Path(imp)
            cand = p if p.is_absolute() else None
            if cand is None or not cand.exists():
                for base_try in (ROOT, cur_base):
                    q = base_try / imp
                    if q.exists():
                        cand = q
                        break
            if cand is None or not cand.exists():
                raise FileNotFoundError(imp)
            rp = cand.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            sub = lomentc.load(rp)
            visit(sub, rp.parent)
            paths.append(rp)

    visit(mod, target.parent)
    names = [lomentc.load(p).name for p in paths]
    assert names == [m.name for m in deps], f"依赖序与 lomentc 不一致: {names} vs {[m.name for m in deps]}"
    return paths


def _unit_text(target: Path) -> str:
    """依赖按序拼接 + 本单元 (与 lomentc.emit_llvm 的 `mods = deps + [mod]` 同序)。

    还要镜像 lomentc.load 的**预置枚举注入**: `Option`/`Result` 缺失时由加载器补进
    `mod.enums`。原生后端按声明发射聚合类型 (`Result<u32,u32>` -> `{ i32, i64 }`),
    所以这份声明对被编译单元必须是可见的 —— 否则枚举查不到, 只能退化成 i64。
    """
    text = ("".join(p.read_text(encoding="utf-8") + "\n" for p in _dep_paths(target))
            + target.read_text(encoding="utf-8"))
    # 判据要用**注入前**的模块 (lomentc.load 返回值里已经有它们了, 拿它判断永远为真)
    raw = target.read_text(encoding="utf-8")
    have = {e.name for e in lomentc.Parser(lomc.lex(raw), raw).parse().enums}
    if "Option" not in have or "Result" not in have:
        text += "\n" + lomentc._PRELUDE
    return text


def _run_codegen(exe: Path, target: Path, td: str) -> str:
    """在"装载好的单元"上跑 Loment 版 codegen (无依赖时就是原文件)。"""
    try:
        text = _unit_text(target)
    except Exception:  # noqa: BLE001
        text = target.read_text(encoding="utf-8")
    unit = Path(td) / f"unit_{target.stem}.lomt"
    # 必须写 LF: 驱动按原始字节读文件, 而 lomentc.load 用 read_text (通用换行) ——
    # CRLF 会让字符串字面量里多出 \0D (自举 codegen 就是这么抓到的)
    unit.write_text(text, encoding="utf-8", newline="\n")
    try:
        return subprocess.run([shutil.which(str(exe)) or str(exe), str(unit)],
                              capture_output=True, text=True, timeout=30, shell=False).stdout
    except subprocess.TimeoutExpired:
        return ""


def _build_from_ll(ll: Path, td: str, name: str) -> Path:
    """用给定的 .ll + 同一个 C 驱动链出可执行文件 —— 自举多阶段复用 (M83/M84)。"""
    c = Path(td) / f"{name}_drv.c"
    c.write_text(CODEGEN_DRIVER, encoding="utf-8")
    exe = Path(td) / f"{name}.exe"
    r = subprocess.run(
        [shutil.which("clang") or r"C:\Program Files\LLVM\bin\clang.exe",
         "-O1", "-o", str(exe), str(c), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    return exe


DRIVER_LOMT = ROOT / "loment" / "selfhost" / "driver.lomt"


def _wsl() -> bool:
    if not shutil.which("wsl"):
        return False
    try:
        return subprocess.run(["wsl", "-e", "true"], capture_output=True,
                              text=True, timeout=60, shell=False).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _wsl_path(p: Path) -> str:
    """Windows 路径 -> WSL 里的 /mnt/<drive>/..."""
    s = str(p.resolve()).replace("\\", "/")
    return "/mnt/" + s[0].lower() + s[2:]


def _build_linux_elf(ll_text: str, td: str, name: str) -> Path:
    """IR -> x86_64 Linux ELF (无 libc, `_start` 即入口) —— 从 Windows 交叉编译。"""
    ll = Path(td) / f"{name}.ll"
    ll.write_text(ll_text, encoding="utf-8")
    elf = Path(td) / f"{name}.elf"
    r = subprocess.run(
        [shutil.which("clang") or r"C:\Program Files\LLVM\bin\clang.exe",
         "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fuse-ld=lld", "-o", str(elf), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-500:]
    return elf


def _run_driver_raw(elf: Path, relpath: str, td: str, name: str) -> tuple[int, str, str]:
    """跑自举驱动, 返回 (退出码, stdout 文本, stderr 文本)。"""
    got = Path(td) / f"{name}.out.ll"
    script = (f"cp {_wsl_path(elf)} /tmp/{name} && chmod +x /tmp/{name} && "
              f"cd {_wsl_path(ROOT)} && /tmp/{name} {relpath} > {_wsl_path(got)}")
    r = subprocess.run(["wsl", "-e", "bash", "-lc", script],
                       capture_output=True, text=True, timeout=300, shell=False)
    text = got.read_text(encoding="utf-8") if got.exists() else ""
    return r.returncode, text, r.stderr


def _run_driver(elf: Path, relpath: str, td: str, name: str) -> str:
    """跑自举驱动并要求成功 (cd 到仓库根, 把入口路径交给它 —— 它自己解析 use)。"""
    rc, text, err = _run_driver_raw(elf, relpath, td, name)
    assert rc == 0, f"驱动退出 {rc}: {err[-400:]}"
    return text


#: 语料里"参考实现的 IR 后端发不出来"的文件 (非目标), 按名字跳过。
def _unsupported(target: Path) -> str | None:
    mod = lomentc.load(target)
    deps = lomentc.resolve_deps(mod, ROOT, target.parent, entry=target)
    try:
        lomentc.emit_llvm(mod, ROOT, deps)
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {e}"
    return None


@test
def test_m83_selfhosted_driver_compiles_itself():
    """M83: 自举驱动是**一个能独立跑的可执行文件**, 且它能编译自己。

    在这之前, 自举链的每一环都是"被 C 驱动调用的函数" —— 没有能独立跑的编译器。
    `driver.lomt` 用 brk 向内核要内存、从 `/proc/self/cmdline` 拿入口路径、
    自己递归解析 `use`、往 stdout 吐 IR。

    判据 (全部在 WSL 里执行):
      1. 参考实现发射 driver.lomt 的单元 -> 链成 ELF -> 跑它 -> 产物与参考逐字节相同;
      2. 用它自己的产物再链一个 ELF (ELF2) -> ELF2 跑同一入口, 产物与 ELF1 相同 (定点);
      3. 同一驱动对别的入口也与参考逐字节相同 (驱动不是"只会编译自己")。
    """
    if not _clang():
        print("      SKIP: 无 clang")
        return
    if not _wsl():
        print("      SKIP: 无 WSL, 交叉产物未执行 (M83 部分)")
        return
    self_rel = DRIVER_LOMT.relative_to(ROOT).as_posix()
    with tempfile.TemporaryDirectory() as td:
        # 1. 参考发射 -> ELF1
        mod = lomentc.load(DRIVER_LOMT)
        deps = lomentc.resolve_deps(mod, ROOT, DRIVER_LOMT.parent, entry=DRIVER_LOMT)
        want = lomentc.emit_llvm(mod, ROOT, deps)
        elf1 = _build_linux_elf(want, td, "fujocs1")
        got1 = _run_driver(elf1, self_rel, td, "fujocs1")
        bad = next((k for k in range(min(len(got1), len(want))) if got1[k] != want[k]), None)
        assert got1 == want, (
            f"驱动产物与参考不一致: want {len(want)}B got {len(got1)}B @{bad}")
        # 2. 用驱动自己的产物再链一个 -> 定点
        elf2 = _build_linux_elf(got1, td, "fujocs2")
        got2 = _run_driver(elf2, self_rel, td, "fujocs2")
        assert got2 == got1, "M84: 自举驱动的第 2 阶段产物与第 1 阶段不一致"
        # 3. 同一个二进制对别的入口也与参考一致
        for rel in ("loment/examples/native_res.lomt", "loment/examples/demo.lomt"):
            target = ROOT / rel
            m2 = lomentc.load(target)
            d2 = lomentc.resolve_deps(m2, ROOT, target.parent, entry=target)
            assert _run_driver(elf1, rel, td, f"u_{target.stem}") == lomentc.emit_llvm(m2, ROOT, d2), \
                f"驱动在 {rel} 上与参考不一致"
        print(f"      自举驱动: {len(want)}B 自身单元 -> ELF -> 逐字节相同; 二阶段定点成立; 另 2 例一致")


@test
def test_m85_selfhosted_driver_compiles_corpus():
    """M85(核心): 自举驱动**自己做全部装载**, 按路径把整个语料编译一遍。

    入口路径来自 `/proc/self/cmdline`, `use` 递归解析 (依赖先写), 缺 Option/Result
    时注入预置枚举 —— 这些原本都在夹具里 (`_unit_text`)。判据 = 每个可发射的
    `.lomt` 都产出与参考**逐字节相同**的 IR, 一个二进制、一个入口路径。
    """
    if not _clang() or not _wsl():
        print("      SKIP: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as td:
        mod = lomentc.load(DRIVER_LOMT)
        deps = lomentc.resolve_deps(mod, ROOT, DRIVER_LOMT.parent, entry=DRIVER_LOMT)
        elf = _build_linux_elf(lomentc.emit_llvm(mod, ROOT, deps), td, "fujocs85")
        ok, skip = [], []
        for target in sorted(list((ROOT / "loment" / "examples").glob("*.lomt"))
                             + list((ROOT / "loment" / "selfhost").glob("*.lomt"))):
            why = _unsupported(target)
            if why:
                skip.append((target.name, why))
                continue
            m = lomentc.load(target)
            d = lomentc.resolve_deps(m, ROOT, target.parent, entry=target)
            want = lomentc.emit_llvm(m, ROOT, d)
            rel = target.relative_to(ROOT).as_posix()
            got = _run_driver(elf, rel, td, f"m85_{target.stem}")
            bad = next((k for k in range(min(len(got), len(want))) if got[k] != want[k]), None)
            assert got == want, (
                f"{rel}: 驱动产物与参考不一致 (want {len(want)}B got {len(got)}B @{bad})")
            ok.append(target.name)
        for name, why in skip:
            print(f"      非目标: {name} ({why})")
        print(f"      自举驱动按路径编译语料: {len(ok)}/{len(ok)} 逐字节一致")


@test
def test_m83_m84_self_compile_and_fixed_point():
    """M83/M84: 自举编译器编译自身 -> 可运行二进制; 三阶段产物逐字节相同 (定点)。

    stage1 = 由 **Python 版** lomentc 编译 Loment 版 codegen 得到的可执行文件;
    stage2 = 由 **stage1 自己产出的 IR** 链出的可执行文件 (M83: 编译器编译自己的产出可运行);
    stage3 = 由 stage2 的产出链出。M84 判据 = 第 2/3 阶段产物逐字节相同。
    """
    if not _clang():
        print("      SKIP: 无 clang")
        return
    with tempfile.TemporaryDirectory() as td:
        exe1 = _build_codegen(td)                      # stage1
        s1 = _run_codegen(exe1, CODEGEN, td)           # stage1 产出的 codegen.lomt 的 IR
        mod = lomentc.load(CODEGEN)
        deps = lomentc.resolve_deps(mod, ROOT, CODEGEN.parent, entry=CODEGEN)
        assert s1 == lomentc.emit_llvm(mod, ROOT, deps), "stage1 产物与参考不一致 (M82 回归)"
        ll1 = Path(td) / "s1.ll"
        ll1.write_text(s1, encoding="utf-8")
        exe2 = _build_from_ll(ll1, td, "stage2")       # M83
        s2 = _run_codegen(exe2, CODEGEN, td)
        assert s2 == s1, "M84: 第 2 阶段产物与第 1 阶段不一致 (未定点)"
        ll2 = Path(td) / "s2.ll"
        ll2.write_text(s2, encoding="utf-8")
        exe3 = _build_from_ll(ll2, td, "stage3")       # 三阶段
        s3 = _run_codegen(exe3, CODEGEN, td)
        assert s3 == s2, "M84: 第 3 阶段产物与第 2 阶段不一致 (未定点)"
        # 定点不能是巧合: 第 2 阶段对别的单元也要与参考一致
        for target in (ROOT / "loment" / "selfhost" / "checker.lomt",
                       ROOT / "loment" / "selfhost" / "ir_div.lomt"):
            m2 = lomentc.load(target)
            d2 = lomentc.resolve_deps(m2, ROOT, target.parent, entry=target)
            assert _run_codegen(exe2, target, td) == lomentc.emit_llvm(m2, ROOT, d2), \
                f"stage2 在 {target.name} 上与参考不一致"
        print(f"      定点: stage1 == stage2 == stage3 ({len(s1)}B); stage2 对 checker/ir_div 亦一致")


@test
def test_m82_coverage_report():
    """M82 进度表: 对全部示例跑 Loment 版 codegen 并与 Python 版逐字节比对。

    已知可通过的目标文件必须继续通过(防回归); 其余示例的差异数作为"M82 彻底完成"
    的进度分母打印出来 —— 覆盖到全部示例 = M82 完成 (见 docs/150 剩余清单)。
    """
    if not _clang():
        print("      SKIP: 无 clang")
        return
    known = ["ir_const.lomt", "ir_expr.lomt", "ir_stmt.lomt", "ir_logic.lomt",
             "ir_cast.lomt", "ir_mem.lomt", "ir_for.lomt", "ir_div.lomt", "ir_builtin.lomt",
             "ir_call5.lomt",
             # 预置枚举 + `?` 早退 + `if let` 三条路径的回归闸 (不放进列表就会静默退化)
             "native_res.lomt",
             # 整数->指针 (M83 给托管驱动补的那一步) 与自举驱动自身
             "native_brk.lomt", "driver.lomt",
             # M2 的 str_concat (堆拼接 + 新运行时常量 + 标签表)
             "native_concat.lomt"]
    with tempfile.TemporaryDirectory() as td:
        exe = _build_codegen(td)
        ok, diff, unsupported = [], [], []
        for target in sorted(list((ROOT / "loment" / "examples").glob("*.lomt"))
                             + list((ROOT / "loment" / "selfhost").glob("*.lomt"))):
            try:
                mod = lomentc.load(target)
                deps = lomentc.resolve_deps(mod, ROOT, target.parent, entry=target)
                want = lomentc.emit_llvm(mod, ROOT, deps)
            except Exception as e:  # noqa: BLE001  参考实现的 IR 后端本身就不发这个示例
                unsupported.append((target.name, f"{type(e).__name__}: {e}"))
                continue
            try:
                got = _run_codegen(exe, target, td)
            except Exception:  # noqa: BLE001
                got = ""
            (ok if got == want else diff).append(target.name)
    missing = [k for k in known if k not in ok]
    assert not missing, f"已知可通过的目标文件回归失败: {missing}"
    total = len(ok) + len(diff)
    print(f"      目标覆盖 {len(ok)}/{total} 字节一致" + (f"; 待补: {', '.join(diff)}" if diff else ""))
    for name, why in unsupported:
        print(f"      非目标: {name} (参考实现自己就发不出来 -> {why})")
    if diff:
        print("      缺口分类 (按文件计):")
        for feature, hits in _gap_breakdown(diff).items():
            print(f"        {feature}: {len(hits)}")
    return


def _gap_breakdown(diff: list[str]) -> dict[str, list[str]]:
    """按"该文件需要哪些尚未实现的后端特性"给待补文件分类 (M82 工作list)。"""
    import re
    feats = {
        "除法/取模 (trap+运行时)": r"[^\w\s]/[^\w\s=]|%",
        "内建 alloc/free/str_*/atomic/位域": r"\b(alloc|free|str_len|str_eq|str_byte|slice_len|str_ptr|ptr_add|ptr_sub|atomic_add|get_bits|set_bits)\b",
        "syscall 内联汇编": r"\bsyscall[46]\b",
        "for 循环": r"\bfor\b",
        "match/枚举": r"\bmatch\b|\benum\b",
        "聚合/切片/字符串类型": r"\bstruct\b|\[[^\]]*\]|\bstr\b",
        "能力域/guard": r"\bguard\b|\bcapability\b|\bexcluded\b",
    }
    dirs = [ROOT / "loment" / "examples", ROOT / "loment" / "selfhost"]
    src_of = {}
    for d in dirs:
        for p in d.glob("*.lomt"):
            src_of[p.name] = p
    out: dict[str, list[str]] = {}
    for name in diff:
        p = src_of.get(name)
        if p is None:
            continue
        text = re.sub(r"//[^\n]*", "", p.read_text(encoding="utf-8"))
        for feat, pat in feats.items():
            if re.search(pat, text):
                out.setdefault(feat, []).append(name)
    return dict(sorted(out.items(), key=lambda kv: -len(kv[1])))


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
