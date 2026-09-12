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

# 错误码口径: **单一真源**在 loment_diag.RULES (E001–E013); checker.lomt 的 E_* 常量
# 用的是同一张表的数字部分, 所以这里直接借 loment_diag.classify 分类参考实现的消息,
# 两边不会各自维护一份模式表而悄悄漂移。
import loment_diag  # noqa: E402

E_DUP, E_TYPE, E_FN, E_ARITY = 13, 2, 2, 3

# 还没搬到自举 checker 的规则: 文件名 -> 缺口说明。这些负例只要求 `⊆` (自举版可以少报),
# 其余负例要求码集**完全相等**。每在 checker.lomt 里补一条, 就删掉这里对应的一行 ——
# 这张表的价值就是"允许少报"的范围**有界、可数、只减不增**。
# 现在整张表的规模由 `tools/loment_rule_parity.py` 测出: 32/60 规则等价 (批次 1 = 声明级
# 规则, 批次 2 第一批 = let/return/赋值/if/while/for 的类型比对)。
# 曾经登记过 `unknown_let.lomt` (let 初始化的类型比对) —— 批次 2 的 return 比对落地后
# 两边码集相等, 于是这一行按表的约定删掉了。
RULE_GAPS: dict[str, str] = {}


def _classify_codes(errs: list[str]) -> list[int]:
    """把参考实现的错误消息按 loment_diag 的口径归类成数字码集。"""
    out: list[int] = []
    for e in errs:
        code, _title, _hint = loment_diag.classify(e)
        if code != "E999":
            out.append(int(code[1:]))
    return sorted(set(out))


def _py_codes(src: Path) -> list[int]:
    """Python 侧把错误消息归类成同一套错误码 (与 checker.lomt 对照)。"""
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    return _classify_codes(lomentc.check(mod, deps=deps))


@test
def test_m81_cross_module_dup_is_rejected():
    """M81/单元级唯一性: **跨模块**同名顶层符号必须被两边一致地拒。

    单元的发射符号是**平的** —— 内核线按名字找入口 (`_start` / `timer_isr`, docs/155 §3),
    所以私有符号不能靠 mangling 变成模块限定名; 代价是"一个单元里顶层名字必须唯一":
    否则后端发出两条 `define @helper` (非法 IR), 调用点还会解析到同一个函数 (静默错编)。
    以前参考实现只查"入口 vs 依赖的 pub", 依赖之间的**私有**重名一路静默; 自举 checker
    因为不分模块反而早就报了 —— 这条钉住两边一致 (口径 E-DUP)。
    """
    if not _clang():
        print("      SKIP: 无 clang")
        return
    entry = ROOT / "loment" / "selfhost" / "neg_across" / "entry.lomt"
    mod = lomentc.load(entry)
    deps = lomentc.resolve_deps(mod, ROOT, entry.parent, entry=entry)
    py = _classify_codes(lomentc.check(mod, deps=deps))
    assert py == [E_DUP], f"参考实现没按 E-DUP (E013) 报跨模块重名: {py}"
    with tempfile.TemporaryDirectory() as td:
        exe = _build_checker(td)
        unit = Path(td) / "u.lomt"
        unit.write_text(_unit_text(entry), encoding="utf-8", newline="\n")
        got, det = _loment_codes(exe, unit)
        assert sorted(set(got)) == [E_DUP], f"自举 checker 的码不对: {got} {det}"
        print(f"      跨模块重名: 两边都报 E-DUP (参考消息 + 自举码 {sorted(set(got))})")


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
    """M81: Loment 版检查器与 Python 版的判定一致 (负例拒绝 + 正例接受, **码集相等**)。

    码值取自项目的统一口径 `loment_diag.RULES` (E001–E013)。以前只断言 `⊆` ——
    那允许自举版"少报"(更宽松就等于放过真正该拒的程序): 实测 `let x: u32 = true;`
    参考实现拒、自举版放行。现在要求**相等** —— "自举 checker 与参考等价"是
    "脱离 Python"的第一道门 (谁在当规范的执行者)。还没补到位的规则在 `RULE_GAPS`
    里如实登记, 每补一条删一行。
    """
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
        exact = 0
        for f in neg:
            want, (got, det) = _py_codes(f), _loment_codes(exe, f)
            assert want, f"{f.name}: Python 未报错"
            assert got, f"{f.name}: Loment 未报错"
            if f.name in RULE_GAPS:
                assert set(got) <= set(want), f"{f.name}: Loment {got} ⊄ Python {want} [{det}]"
                continue
            assert sorted(set(got)) == want, \
                f"{f.name}: 码集不等 Loment {sorted(set(got))} vs Python {want} [{det}]"
            exact += 1
        for f in pos:
            want, (got, det) = _py_codes(f), _loment_codes(exe, f)
            assert want == [] and got == [], f"{f.name}: 正例被拒 (py={want} loment={got}) [{det}]"
        print(f"      负例码集: {exact}/{len(neg)} 完全相等" + (
            f"; 登记缺口 {len(RULE_GAPS)} 个: {sorted(RULE_GAPS)}" if RULE_GAPS else ""))


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
        # 非目标: 参考实现的 IR 后端自己也发不出来 (native: inb 未实现), 它本就不是单元的
        # 合法形状 —— 所以这条不是"checker 的缺口", 而是"这份文件不进单元语料"。
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
def test_m85_driver_checks_before_emitting():
    """M85: 同一个自举二进制**先检查再发射** —— 负例被拒、正例放行。

    checker 的覆盖面到 40/40 单元之后才敢打开这道闸门 (在这之前它会把合法程序判错)。
    判据:
      * `selfhost/neg/*.lomt` 必须非零退出、带诊断、且**不产出 IR**;
      * `selfhost/pos/*.lomt` 必须零退出且产物与参考逐字节相同。

    这条把 M81 的"错误码集合一致"从"夹具驱动 checker"升级成"**编译器自己**判"。
    """
    if not _clang() or not _wsl():
        print("      SKIP: 无 clang/WSL")
        return
    neg = sorted((ROOT / "loment" / "selfhost" / "neg").glob("*.lomt"))
    pos = sorted((ROOT / "loment" / "selfhost" / "pos").glob("*.lomt"))
    assert neg and pos, "缺负例/正例语料"
    with tempfile.TemporaryDirectory() as td:
        mod = lomentc.load(DRIVER_LOMT)
        deps = lomentc.resolve_deps(mod, ROOT, DRIVER_LOMT.parent, entry=DRIVER_LOMT)
        elf = _build_linux_elf(lomentc.emit_llvm(mod, ROOT, deps), td, "fujocs_gate")
        for f in neg:
            rel = f.relative_to(ROOT).as_posix()
            rc, out, err = _run_driver_raw(elf, rel, td, f"neg_{f.stem}")
            assert rc != 0, f"{f.name}: 负例没被拒 (exit {rc})"
            assert "静态检查未通过" in err, f"{f.name}: 没报诊断: {err[:200]}"
            assert "@" in err and "line" in err, f"{f.name}: 诊断格式不对: {err[:200]}"
            assert out.strip() == "", f"{f.name}: 被拒时不该产出 IR"
        # 单元级负例: 跨模块同名 —— 驱动要自己装载完这两个文件才发现, 也必须拒
        rel = "loment/selfhost/neg_across/entry.lomt"
        rc, out, err = _run_driver_raw(elf, rel, td, "neg_across")
        assert rc != 0, f"跨模块重名没被拒 (exit {rc})"
        assert "静态检查未通过" in err, f"没报诊断: {err[:200]}"
        assert out.strip() == "", "被拒时不该产出 IR"
        for f in pos:
            rel = f.relative_to(ROOT).as_posix()
            unsupported = _unsupported(f)     # 参考实现的 IR 后端能不能发这个文件
            rc, out, err = _run_driver_raw(elf, rel, td, f"pos_{f.stem}")
            # 正例的判据是"**检查阶段**放行"; 能不能发 IR 取决于它是不是 IR 后端的合法目标
            # (pos/ok.lomt 是给 checker 写的正例, 含 IR 后端不支持的类型, 这不是闸门的事)
            assert "静态检查未通过" not in err, f"{f.name}: 正例被 check 拒了: {err[:200]}"
            if unsupported is None:
                m = lomentc.load(f)
                d = lomentc.resolve_deps(m, ROOT, f.parent, entry=f)
                assert rc == 0, f"{f.name}: 正例退出码 {rc}: {err[:200]}"
                assert out == lomentc.emit_llvm(m, ROOT, d), f"{f.name}: 正例产物与参考不一致"
        print(f"      驱动闸门: 负例 {len(neg)}/{len(neg)} 被拒, 正例 {len(pos)}/{len(pos)} 过检")


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


@test
def test_m85_codegen_table_capacity():
    """自举 codegen 的**每函数表容量**必须装得下最大的编译单元。

    这是批次 2 抓到的一次静默错编: 单元长到 246 个函数后, 越过了布局里
    `fk 表`的 192 格 (14848..16384), 于是表尾被后面的参数替换表写穿 —— `fkind`
    读出来是 2, 少数函数被改名成 `<接收者>_<方法>` (`is_lomt_emit_div_mnemonic`),
    逐字节判据只报"两个编译器不一致"。布局现在按 256 个函数重排
    (函数表 12288+i*12, fk 表 15360+i*8, 形参表 24576+i*80, 枚举表 45056+i*80),
    这里把"容量 >= 最大单元的函数数"钉成静态判据: 再长下去会红, 不再悄悄写穿。
    """
    import re as _re
    src = (ROOT / "loment" / "selfhost" / "codegen.lomt").read_text(encoding="utf-8")
    def base(name: str) -> tuple[int, int]:
        m = _re.search(rf"fn {name}\(i: u32\) -> u32 \{{\s*return (\d+) \+ i \* (\d+);", src)
        assert m, f"{name} 的基址表达式没找到"
        return int(m.group(1)), int(m.group(2))
    fn_base, fn_stride = 12288, 12                    # 函数表 (字面量)
    fk_base, fk_stride = base("fk_base")
    enum_base, enum_stride = base("enum_base")
    param_base = 24576
    m = _re.search(r"24576 \+ n \* (\d+)", src)
    assert m, "形参表步长没找到"
    param_stride = int(m.group(1))
    # 容量 = 每张表在"下一张表开始时"之前能放多少个
    cap_fn = (fk_base - fn_base) // fn_stride
    cap_fk = (17408 - fk_base) // fk_stride           # 17408 = 参数替换表基址
    cap_param = (enum_base - param_base) // param_stride
    cap = min(cap_fn, cap_fk, cap_param)
    # 最大单元 = driver.lomt 的整单元 (lexer+codegen+checker+driver)。
    # 用**真实词法器**数 `fn` 标识符 token —— 这正是 codegen 看到的数量 (字符串里的
    # "fn" 是 string token, 不算; 这也是 codegen.lomt 的 tok_is 刚补上的守卫)。
    entry = ROOT / "loment" / "selfhost" / "driver.lomt"
    unit_toks = lomc.lex(_unit_text(entry))
    nfns = sum(1 for tk in unit_toks if tk.kind == "ident" and tk.val == "fn")
    assert nfns <= cap, (f"最大单元有 {nfns} 个函数, 超过 codegen 表容量 {cap} "
                         f"(fn {cap_fn} / fk {cap_fk} / 形参 {cap_param}) —— 请重排布局")
    print(f"      codegen 表容量: {cap} 个函数 (fn {cap_fn}/fk {cap_fk}/形参 {cap_param}), "
          f"最大单元 {nfns} 个")


@test
def test_m85_heap_budget():
    """静态预算: 语言堆里同时活着的 `alloc` 之和必须留在 64 KiB 以内。

    这是**批次 2 期间被抓到的一次真实停机**: checker 加的 `alloc(2048)` 让它和 codegen
    的 `alloc(49152)` 一起越过 64 KiB, `alloc` 的边界检查走 `@__loment_abort`, 在自举
    驱动里表现为一条**非法指令 (SIGILL)** —— 从测试输出上看像"编译器崩了", 而不是
    "堆不够"。两者的分配都是**固定字面量** (与输入无关), 所以这条约束可以在静态检查里
    精确钉住: 改大任何一边的 `alloc` 会在这里立刻变红, 不必等到驱动 SIGILL。

    现在 checker 的缓冲走 `check_arena` (驱动从 `brk` 拿), 所以**驱动路径**的语言堆里
    只剩 codegen 自己; `check()` 那个薄包装仍然从语言堆开一个 arena —— 那是 C 夹具路径。
    两条路径**不再同时存在**, 所以判据是"各自都不越界" (而不是把两者相加), 另加一条:
    驱动 `sys_alloc` 的那块必须装得下 `chk_arena_bytes()`。
    """
    import re as _re
    heap = 65536          # 镜像 lomentc 的 __LOMENT_HEAP (Rust 侧) / @__loment_heap (IR 侧)
    need = 4096           # 要求的余量
    per: list[tuple[str, int]] = []
    for rel in ("loment/selfhost/checker.lomt", "loment/selfhost/codegen.lomt"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        per.append((rel, sum(int(m) for m in _re.findall(r"alloc\((\d+)\)", src))))
    codegen_path = dict(per)["loment/selfhost/codegen.lomt"]
    checker_path = dict(per)["loment/selfhost/checker.lomt"]
    for name, v in per:
        assert v + need <= heap, (
            f"{name} 的语言堆分配 {v}B 越过预算 (堆 {heap}B, 要求留 {need}B) —— "
            f"调小 alloc 或让缓冲改走 brk")
    # 驱动侧: arena 一块从 brk 拿, 必须装得下 checker 声明的 arena 尺寸
    drv = (ROOT / "loment" / "selfhost" / "driver.lomt").read_text(encoding="utf-8")
    m = _re.search(r"chk_arena_bytes\(\)\s*->\s*u32\s*\{\s*return\s+(\d+)", 
                   (ROOT / "loment" / "selfhost" / "checker.lomt").read_text(encoding="utf-8"))
    assert m, "找不到 chk_arena_bytes() 的实现"
    arena = int(m.group(1))
    allocs = [int(x) for x in _re.findall(r"sys_alloc\((\d+)\)", drv)]
    assert allocs, "驱动里没有 sys_alloc"
    assert max(allocs) >= arena, f"驱动的 arena 块 ({max(allocs)}B) 装不下 checker 的 {arena}B"
    print(f"      堆预算: 夹具/checker {checker_path}B · 驱动/codegen {codegen_path}B, "
          f"堆 {heap}B (余量 {heap - max(checker_path, codegen_path)}B); "
          f"driver 的 brk arena {max(allocs)}B >= checker {arena}B")


@test
def test_m85_codegen_arg_arity_is_loud():
    """实参上限 (10) 必须**响亮地失败**, 不能静默截断。

    自举 codegen 的形参类型表步长 80 = 10 槽 x 8 字节 (加宽会撞 40960 的枚举表), 所以
    实参/形参上限是 10。批次 2 里第一次出现 11 个实参的调用时它**静默丢了最后一个**
    (IR 少一个实参), 参考实现照发 -> 逐字节判据报"两个编译器不一致", 但定位成本很高。
    现在超限会 `panic(10)`; 这条测试同时钉住两边: ① 驱动源码里仍有那道闸门;
    ② 语料里没有任何函数超过 10 个形参 (否则把闸门"修好"就等于让它再次静默)。
    """
    src = (ROOT / "loment" / "selfhost" / "codegen.lomt").read_text(encoding="utf-8")
    assert "panic(10);" in src, "自举 codegen 的实参超限闸门不见了"
    assert "a < 10" in src, "实参上限常量变了: 请同步形参表步长与这条测试"
    worst = 0
    worst_fn = ""
    for target in sorted(list((ROOT / "loment" / "examples").glob("*.lomt"))
                         + list((ROOT / "loment" / "selfhost").glob("*.lomt"))):
        try:
            mod = lomentc.load(target)
        except Exception:  # noqa: BLE001
            continue
        for f in mod.funcs:
            if len(f.params) > worst:
                worst, worst_fn = len(f.params), f"{target.name}:{f.name}"
    assert worst <= 10, f"语料里有 {worst} 个形参的函数 ({worst_fn}), 超过 self-hosted codegen 的 10 槽上限"
    print(f"      实参上限: 闸门存在; 语料最大形参数 {worst} ({worst_fn}) <= 10")


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
