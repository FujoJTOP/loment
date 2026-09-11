#!/usr/bin/env python3
# loment_tools_test.py — P6 工具链自检 (M55–M66, docs/148)
#
# 运行: python tools/loment_tools_test.py   (退出码 0 = 全绿)
# 说明: 需要 rustc/clang 的用例在工具缺失时打印 SKIP 并计为通过 (环境问题不算回归);
#       纯静态判定不依赖外部工具。

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomc  # noqa: E402
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "loment" / "examples"
TOOLCHAIN = EX / "toolchain.lomt"
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _clang() -> str | None:
    return shutil.which("clang") or (
        r"C:\Program Files\LLVM\bin\clang.exe"
        if Path(r"C:\Program Files\LLVM\bin\clang.exe").exists() else None)


def _rustc() -> str | None:
    return shutil.which("rustc")


# ---------------------------------------------------------------- 单态化金标 (docs/156)

@test
def test_mono_trace_naming_rule():
    """单态化的命名规则不漂: base + "_" + "_".join(实参), 且全语料零违规。"""
    import mono_trace
    r = mono_trace.main(["--check"])
    assert r == 0, "mono_trace --check 失败 (命名规则漂了, 见 docs/156 §1)"


# ---------------------------------------------------------------- M55 格式化

@test
def test_m55_fmt_idempotent_and_semantics():
    import lomfmt
    for p in sorted(EX.glob("*.lomt")):
        src = p.read_text(encoding="utf-8")
        f1 = lomfmt.format_source(src)
        assert lomfmt.format_source(f1) == f1, f"{p.name} 非幂等"
        tmp = p.with_name("._fmt_" + p.name)
        tmp.write_text(f1, encoding="utf-8")
        try:
            m1 = lomentc.load(p)
            d1 = lomentc.emit_potato(m1, ROOT, lomentc.resolve_deps(m1, ROOT, p.parent, entry=p))
            m2 = lomentc.load(tmp)
            d2 = lomentc.emit_potato(m2, ROOT, lomentc.resolve_deps(m2, ROOT, tmp.parent, entry=tmp))
            assert d1 == d2, f"{p.name} 格式化改变了语义"
        finally:
            tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------- M56 LSP

@test
def test_m56_lsp_three_capabilities():
    import loment_lsp
    uri = TOOLCHAIN.resolve().as_uri()
    docs: dict[str, str] = {}
    init = loment_lsp.handle({"id": 1, "method": "initialize", "params": {}}, docs)
    caps = init[0]["result"]["capabilities"]
    assert caps["definitionProvider"] and caps["completionProvider"]
    text = TOOLCHAIN.read_text(encoding="utf-8")
    msgs = loment_lsp.handle({"method": "textDocument/didOpen",
                              "params": {"textDocument": {"uri": uri, "text": text}}}, docs)
    assert msgs[0]["method"] == "textDocument/publishDiagnostics"
    assert msgs[0]["params"]["diagnostics"] == []
    items = loment_lsp.handle({"id": 2, "method": "textDocument/completion",
                               "params": {"textDocument": {"uri": uri}}}, docs)[0]
    labels = {i["label"] for i in items["result"]["items"]}
    assert {"fn", "u32", "fib", "gcd"} <= labels, sorted(labels)[:10]
    line = next(i for i, s in enumerate(text.splitlines()) if s.strip().startswith("fn "))
    col = text.splitlines()[line].index("fn") + 3
    res = loment_lsp.handle({"id": 3, "method": "textDocument/definition",
                             "params": {"textDocument": {"uri": uri},
                                        "position": {"line": line, "character": col}}}, docs)[0]
    assert res["result"]["range"]["start"]["line"] == line
    bad = "module m\nfn f() -> u32 { return g(1); }\n"
    d = loment_lsp.handle({"method": "textDocument/didOpen",
                           "params": {"textDocument": {"uri": "file:///bad.lomt", "text": bad}}},
                          docs)[0]
    assert d["params"]["diagnostics"], "错误文件应有诊断"


# ---------------------------------------------------------------- M57 包管理

@test
def test_m57_pkg_resolve_lock_verify_cycle():
    import lompkg
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        for name, dep in (("base", None), ("mid", "base"), ("top", "mid")):
            d = base / name
            (d / "src").mkdir(parents=True)
            (d / "src" / "m.lomt").write_text(f"module {name}\nfn f() -> u32 {{ return 1; }}\n",
                                              encoding="utf-8")
            deps = {} if dep is None else {dep: {"path": f"../{dep}"}}
            (d / "pkg.json").write_text(json.dumps({"name": name, "version": "0.1.0",
                                                    "deps": deps}), encoding="utf-8")
        top = base / "top" / "pkg.json"
        pkgs = lompkg.resolve(top)
        assert [p["name"] for p in pkgs] == ["base", "mid", "top"], pkgs
        lock = base / "pkg.lock"
        assert lompkg.main(["resolve", str(top), "--lock", str(lock), "--write"]) == 0
        assert lompkg.main(["verify", str(top), "--lock", str(lock)]) == 0
        (base / "base" / "src" / "m.lomt").write_text("module base\nfn f() -> u32 { return 2; }\n",
                                                      encoding="utf-8")
        assert lompkg.main(["verify", str(top), "--lock", str(lock)]) == 1
        (base / "base" / "pkg.json").write_text(json.dumps(
            {"name": "base", "version": "0.1.0", "deps": {"top": {"path": "../top"}}}),
            encoding="utf-8")
        try:
            lompkg.resolve(top)
            assert False, "应当检测到依赖环"
        except ValueError as e:
            assert "环" in str(e)


# ---------------------------------------------------------------- M58 文档

@test
def test_m58_doc_from_lomt():
    import lomdoc
    text = lomdoc.render(lomentc.load(TOOLCHAIN), TOOLCHAIN.read_text(encoding="utf-8"),
                         str(TOOLCHAIN))
    for needle in ("# API: `toolchain`", "### `fn fib(n: u32) -> u32`",
                   "### `fn gcd(a: u32, b: u32) -> u32`", "斐波那契"):
        assert needle in text, needle
    cap_doc = lomdoc.render(lomentc.load(EX / "native_cap.lomt"),
                            (EX / "native_cap.lomt").read_text(encoding="utf-8"), "x")
    assert "## 能力域" in cap_doc and "`blk_write`" in cap_doc


# ---------------------------------------------------------------- M59 DWARF

@test
def test_m59_dwarf_line_table():
    mod = lomentc.load(TOOLCHAIN)
    deps = lomentc.resolve_deps(mod, ROOT, EX, entry=TOOLCHAIN)
    ll = lomentc.emit_llvm(mod, ROOT, deps, debug=True)
    assert "!llvm.dbg.cu" in ll and "!DISubprogram" in ll and "!DILocation" in ll
    assert any(l.startswith("define ") and "!dbg !" in l for l in ll.splitlines())
    assert ll.count("!dbg !") > 20, ll.count("!dbg !")
    clang = _clang()
    if not clang:
        print("      SKIP: 无 clang, 跳过 DWARF 落盘验证")
        return
    import subprocess
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "m.ll"
        f.write_text(ll, encoding="utf-8")
        obj = Path(td) / "m.o"
        r = subprocess.run([clang, "--target=x86_64-unknown-none", "-ffreestanding",
                            "-g", "-c", str(f), "-o", str(obj)],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr
        dump = subprocess.run(
            [shutil.which("llvm-objdump") or r"C:\Program Files\LLVM\bin\llvm-objdump.exe",
             "-d", "-l", str(obj)], capture_output=True, text=True, shell=False).stdout
        assert "toolchain.lomt:" in dump, "行表里没有源文件行号"


# ---------------------------------------------------------------- M60–M63 CLI

@test
def test_m60_ir_viewer():
    import loment
    assert loment.main(["ir", str(TOOLCHAIN)]) == 0
    if _clang():
        assert loment.main(["ir", str(TOOLCHAIN), "--objdump"]) == 0


@test
def test_m61_test_framework():
    import loment
    assert loment.main(["test", str(TOOLCHAIN)]) == 0


@test
def test_m62_bench_table():
    import loment
    assert loment.main(["bench", str(TOOLCHAIN), "--n", "20000"]) == 0


@test
def test_m63_coverage():
    import loment
    assert loment.main(["cov", str(TOOLCHAIN), "--call", "cov_main"]) == 0


# ---------------------------------------------------------------- M64 诊断

SNIPPETS = [
    ("类型不匹配", "module m\nfn f() -> u32 { let x: u32 = true; return x; }\n"),
    ("未声明符号", "module m\nfn f() -> u32 { return g(1); }\n"),
    ("实参数量", "module m\nfn g(a: u32) -> u32 { return a; }\n"
                 "fn f() -> u32 { return g(1, 2); }\n"),
    ("能力域", "module m\nfn f() -> u32 { guard c(0); return 0; }\n"),
    ("出界冲突", 'module m\ncapability c : disk[0..1]\nexcluded "disk: none"\n'
                 "fn f() -> u32 { guard c(0); return 0; }\n"),
    ("移动后使用", "module m\nstruct S { a: u32 }\n"
                   "fn f() -> u32 { let s: S = S { a: 1 }; let t: S = s; return s.a; }\n"),
    ("借用冲突", "module m\nfn g(a: mut [u32], b: [u32]) -> u32 { return 0; }\n"
                 "fn f() -> u32 { let a: [u32; 2] = [1, 2]; return g(&mut a, &a); }\n"),
    ("match 非法", "module m\nenum E { A, B }\nfn f(e: E) -> u32 { match e { E::A => { return 1; } } "
                   "return 0; }\n"),
    ("结构体字段", "module m\nstruct S { a: u32, b: u32 }\n"
                   "fn f() -> u32 { let s: S = S { a: 1 }; return s.a; }\n"),
    ("? 误用", "module m\nfn h() -> u32 { return 1; }\n"
               "fn f() -> u32 { return h()?; }\n"),
    ("只读切片写入", "module m\nfn f(xs: [u32]) -> u32 { xs[0] = 1; return 0; }\n"),
    ("悬垂借用", "module m\nfn f() -> [u32] { let a: [u32; 2] = [1, 2]; return &a; }\n"),
    ("重名", "module m\nfn f() -> u32 { return 1; }\nfn f() -> u32 { return 2; }\n"),
]


@test
def test_m64_diagnostics_have_codes_and_hints():
    import loment_diag
    codes = set()
    for name, src in SNIPPETS:
        errs = lomentc.check(lomentc.Parser(lomc.lex(src), src).parse())
        assert errs, f"{name}: 应当报错"
        diags = loment_diag.diagnose(errs)
        for d in diags:
            assert d["code"] != "E999", f"{name}: 未分类 {d['message']}"
            assert d["hint"], f"{name}: 缺建议"
            codes.add(d["code"])
    assert len(codes) >= 10, sorted(codes)


# ---------------------------------------------------------------- M65/M66 构建

@test
def test_m65_m66_incremental_and_cache():
    import time
    import loment_build
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "out"
        t0 = time.perf_counter()
        assert loment_build.main([str(EX), "--out", str(out)]) == 0
        cold = time.perf_counter() - t0
        t1 = time.perf_counter()
        assert loment_build.main([str(EX), "--out", str(out)]) == 0
        hot = time.perf_counter() - t1
        recs = json.loads((out / ".loment-cache.json").read_text(encoding="utf-8"))["records"]
        assert len(recs) >= 15, len(recs)
        assert hot < cold, (cold, hot)
        # 改一个文件: 只有它需要重编
        victim = out.parent / "victim.lomt"
        victim.write_text((EX / "mathutil.lomt").read_text(encoding="utf-8"), encoding="utf-8")
        assert loment_build.main([str(out.parent), "--out", str(out)]) == 0


def main() -> int:
    failed = []
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, e))
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\nloment_tools_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
