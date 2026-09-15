#!/usr/bin/env python3
# loment.py — Loment 工具链统一入口 (P6, docs/148)
#
#   loment fmt    FILE...          # M55 格式化
#   loment doc    FILE             # M58 API 文档
#   loment diag   FILE             # M64 诊断分类 + 修复建议
#   loment ir     FILE [--objdump] # M60 IR / 机器码
#   loment test   FILE             # M61 内建测试框架 (test_* 函数)
#   loment bench  FILE [--n N]     # M62 基准 (Rust 路径 vs IR 路径)
#   loment cov    FILE [--call F]  # M63 IR 级块覆盖
#   loment build  DIR [--out DIR]  # M65/M66 增量构建 + 缓存 (见 loment_build.py)
#   loment pkg    ...              # M57 包管理 (见 lompkg.py)
#   loment lsp                     # M56 语言服务 (见 loment_lsp.py)
#
# 退出码: 子命令语义, 0 = 成功。
# 外部工具一律用参数列表调用 (shell=False), 可执行文件经 shutil.which 解析。

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomc  # noqa: E402  (LomError: 把解析/装载期的诊断与内部故障分开)
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
C_TYPES = {"u8": "unsigned char", "u16": "unsigned short", "u32": "unsigned int",
           "u64": "unsigned long long", "i8": "signed char", "i16": "short",
           "i32": "int", "i64": "long long", "bool": "_Bool", "()": "void"}


def _load(file: str):
    p = Path(file)
    mod = lomentc.load(p)
    deps = lomentc.resolve_deps(mod, ROOT, p.parent, entry=p)
    errs = lomentc.check(mod, deps=deps)
    if errs:
        for e in errs:
            print("  " + e, file=sys.stderr)
        raise SystemExit(1)
    return p, mod, deps


def _picked(mod: lomentc.Module, prefix: str) -> list[lomentc.Func]:
    return [f for f in mod.funcs
            if f.name.startswith(prefix) and not f.params and f.ret in ("bool", "u32")]


def cmd_fmt(a) -> int:
    import lomfmt
    return lomfmt.main(a.files)


def cmd_doc(a) -> int:
    import lomdoc
    return lomdoc.main([a.file] + (["--out", a.out] if a.out else []))


def cmd_diag(a) -> int:
    import loment_diag
    return loment_diag.main([a.file] + (["--json"] if a.json else []))


def cmd_ir(a) -> int:
    p, mod, deps = _load(a.file)
    text = lomentc.emit_llvm(mod, ROOT, deps)
    if not a.objdump:
        sys.stdout.write(text)
        return 0
    with tempfile.TemporaryDirectory() as td:
        binp = Path(td) / "m.bin"
        # 机器码由仓库自己的原生后端出（不再经 clang），再用 llvm-objdump 反汇编看。
        # 原生后端要用户态入口，纯函数片段没有 —— 只为这个**调试视图**补一个空 `_start`。
        src = text
        if "_start" not in text:
            src = text + "\ndefine void @_start() {\nentry:\n  ret void\n}\n"
        import lomelf
        binp.write_bytes(lomelf.compile_ll(src)[0])
        d = subprocess.run(
            [shutil.which("llvm-objdump") or r"C:\Program Files\LLVM\bin\llvm-objdump.exe",
             "-d", str(binp)],
            capture_output=True, text=True, shell=False)
        sys.stdout.write(text + "\n; ==== 机器码 (lomelf 出的 ELF, llvm-objdump -d) ====\n" + d.stdout)
    return 0


def cmd_test(a) -> int:
    p, mod, deps = _load(a.file)
    tests = [f for f in mod.funcs if f.name.startswith("test_") and not f.params
             and f.ret == "bool"]
    if not tests:
        print("[ERR] 没有 test_*() -> bool 函数", file=sys.stderr)
        return 2
    rust = lomentc.emit_rust(mod, ROOT, deps)
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "m.rs").write_text(rust, encoding="utf-8")
        harness = ["include!(\"m.rs\");", "fn main() {",
                   "    let mut pass = 0u32; let mut total = 0u32;"]
        for f in tests:
            harness += ["    total += 1;",
                        f"    if {f.name}() {{ pass += 1; println!(\"PASS {f.name}\"); }}",
                        f"    else {{ println!(\"FAIL {f.name}\"); }}"]
        harness += ["    println!(\"RESULT: {}/{} PASS\", pass, total);",
                    "    if pass != total { std::process::exit(1); }", "}"]
        (Path(td) / "main.rs").write_text("\n".join(harness) + "\n", encoding="utf-8")
        exe = Path(td) / "t.exe"
        r = subprocess.run(
            [shutil.which("rustc") or "rustc", "-O", "-o", str(exe),
             str(Path(td) / "main.rs")],
            capture_output=True, text=True, shell=False)
        if r.returncode:
            print(r.stderr, file=sys.stderr)
            return 1
        run = subprocess.run([shutil.which(str(exe)) or str(exe)],
                             capture_output=True, text=True, shell=False)
        print(run.stdout.strip())
        return run.returncode


def cmd_bench(a) -> int:
    p, mod, deps = _load(a.file)
    benches = _picked(mod, "bench_")
    if not benches:
        print("[ERR] 没有 bench_*() 函数", file=sys.stderr)
        return 2
    rust = lomentc.emit_rust(mod, ROOT, deps)
    ir = lomentc.emit_llvm(mod, ROOT, deps)
    rows = []
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "m.rs").write_text(rust, encoding="utf-8")
        h = ["include!(\"m.rs\");", "fn main() {",
             f"    let n = {a.n}u32;", "    let mut acc = 0u32;"]
        for f in benches:
            h += ["    let t = std::time::Instant::now();",
                  f"    for _ in 0..n {{ acc = acc.wrapping_add({f.name}()); "
                  "std::hint::black_box(acc); }",
                  f"    println!(\"RUST {f.name} {{}} {{}}\", t.elapsed().as_nanos(), acc);"]
        h += ["}"]
        (Path(td) / "main.rs").write_text("\n".join(h) + "\n", encoding="utf-8")
        exe = Path(td) / "b.exe"
        r = subprocess.run(
            [shutil.which("rustc") or "rustc", "-O", "-o", str(exe),
             str(Path(td) / "main.rs")],
            capture_output=True, text=True, shell=False)
        if r.returncode:
            print(r.stderr, file=sys.stderr)
            return 1
        rust_out = subprocess.run([shutil.which(str(exe)) or str(exe)],
                                  capture_output=True, text=True, shell=False).stdout
        for line in rust_out.splitlines():
            _, name, ns, _acc = line.split()
            rows.append([name, int(ns), None])
        (Path(td) / "m.ll").write_text(ir, encoding="utf-8")
        c = ["#include <stdio.h>", "#include <time.h>"]
        for f in benches:
            c.append(f"extern {C_TYPES.get(f.ret, 'unsigned int')} {f.name}(void);")
        c += ["int main(void) {", f"    unsigned long n = {a.n}UL;",
              "    volatile unsigned long acc = 0;"]
        for f in benches:
            c += ["    {",
                  "    struct timespec ts0, ts1;",
                  "    timespec_get(&ts0, TIME_UTC);",
                  f"    for (unsigned long i = 0; i < n; i++) acc += {f.name}();",
                  "    timespec_get(&ts1, TIME_UTC);",
                  "    double ns = (double)(ts1.tv_sec - ts0.tv_sec) * 1e9",
                  "              + (double)(ts1.tv_nsec - ts0.tv_nsec);",
                  f"    printf(\"IR {f.name} %.0f %lu\\n\", ns, acc);",
                  "    }"]
        c += ["    return 0;", "}"]
        (Path(td) / "drv.c").write_text("\n".join(c) + "\n", encoding="utf-8")
        exe2 = Path(td) / "b2.exe"
        r = subprocess.run(
            [shutil.which("clang") or r"C:\Program Files\LLVM\bin\clang.exe",
             "-O1", "-o", str(exe2), str(Path(td) / "drv.c"), str(Path(td) / "m.ll")],
            capture_output=True, text=True, shell=False)
        if r.returncode:
            print(r.stderr, file=sys.stderr)
            return 1
        ir_out = subprocess.run([shutil.which(str(exe2)) or str(exe2)],
                                capture_output=True, text=True, shell=False).stdout
        for line in ir_out.splitlines():
            _, name, ns, _acc = line.split()
            for row in rows:
                if row[0] == name:
                    row[2] = int(float(ns))
    print("| 函数 | Rust 路径 (ns) | IR 路径 (ns) | 比值 |")
    print("|---|---|---|---|")
    for name, rns, irns in rows:
        ratio = f"{irns / rns:.2f}x" if rns and irns else "-"
        print(f"| `{name}` | {rns} | {irns} | {ratio} |")
    return 0


def cmd_cov(a) -> int:
    p, mod, deps = _load(a.file)
    fn = next((f for f in mod.funcs if f.name == a.call), None)
    if fn is None:
        print(f"[ERR] 找不到入口函数 {a.call}", file=sys.stderr)
        return 2
    if fn.params or fn.ret not in C_TYPES:
        print(f"[ERR] 覆盖入口必须是零参标量函数 ({fn.ret} {fn.name})", file=sys.stderr)
        return 2
    ir = lomentc.emit_llvm(mod, ROOT, deps, coverage=True)
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "m.ll").write_text(ir, encoding="utf-8")
        drv = f"""#include <stdio.h>
extern unsigned long long __loment_cov[256];
extern const unsigned long long __loment_cov_n;
extern {C_TYPES[fn.ret]} {fn.name}(void);
int main(void) {{
    (void){fn.name}();
    unsigned long long n = __loment_cov_n, hit = 0;
    for (unsigned long long i = 0; i < n; i++) if (__loment_cov[i]) hit++;
    printf("COV %llu/%llu %.1f%%\\n", hit, n, n ? 100.0 * hit / n : 0.0);
    return 0;
}}
"""
        (Path(td) / "drv.c").write_text(drv, encoding="utf-8")
        exe = Path(td) / "cov.exe"
        r = subprocess.run(
            [shutil.which("clang") or r"C:\Program Files\LLVM\bin\clang.exe",
             "-O0", "-o", str(exe), str(Path(td) / "drv.c"), str(Path(td) / "m.ll")],
            capture_output=True, text=True, shell=False)
        if r.returncode:
            print(r.stderr, file=sys.stderr)
            return 1
        out = subprocess.run([shutil.which(str(exe)) or str(exe)],
                             capture_output=True, text=True, shell=False).stdout.strip()
    print(f"{p.name}: {out}")
    return 0


def cmd_dbg(a) -> int:
    """M75: 源码级符号化 —— 地址 <-> 源行 (基于 DWARF 行表)。"""
    import re
    p, mod, deps = _load(a.file)
    ir = lomentc.emit_llvm(mod, ROOT, deps, debug=True)
    with tempfile.TemporaryDirectory() as td:
        ll = Path(td) / "m.ll"
        ll.write_text(ir, encoding="utf-8")
        obj = Path(td) / "m.o"
        r = subprocess.run(
            [shutil.which("clang") or r"C:\Program Files\LLVM\bin\clang.exe",
             "--target=x86_64-unknown-none", "-ffreestanding", "-g", "-c",
             str(ll), "-o", str(obj)], capture_output=True, text=True, shell=False)
        if r.returncode:
            print(r.stderr, file=sys.stderr)
            return 1
        dump = subprocess.run(
            [shutil.which("llvm-objdump") or r"C:\Program Files\LLVM\bin\llvm-objdump.exe",
             "-d", "-l", str(obj)], capture_output=True, text=True, shell=False).stdout
    rows: list[tuple[int, str, str]] = []   # (addr, src, fn)
    cur_fn, cur_src = "?", ""
    for ln in dump.splitlines():
        s = ln.strip()
        m = re.match(r"^([0-9a-f]+) <([^>]+)>:", s)
        if m:
            cur_fn = m.group(2)
            continue
        if s.startswith(";"):
            cur_src = s.lstrip("; ").strip()
            continue
        m = re.match(r"^([0-9a-f]+):", s)
        if m and cur_src:
            rows.append((int(m.group(1), 16), cur_src, cur_fn))
    if a.addr is not None:
        addr = int(a.addr, 0)
        hit = [r for r in rows if r[0] <= addr]
        if not hit:
            print(f"[ERR] 地址 {addr:#x} 无行表条目", file=sys.stderr)
            return 1
        best = max(hit, key=lambda r: r[0])
        print(f"{addr:#x} -> {best[1]} ({best[2]}+{addr - best[0]:#x})")
        return 0
    fn = a.func
    sel = [r for r in rows if r[2] == fn]
    if not sel:
        fns = sorted({r[2] for r in rows if r[2] != "?"})
        print(f"[ERR] 无函数 {fn}; 可用: {', '.join(fns[:8])}", file=sys.stderr)
        return 1
    srcs = []
    for _, s, _ in sel:
        if s not in srcs:
            srcs.append(s)
    lines = [int(x.rsplit(":", 1)[1]) for x in srcs if x.rsplit(":", 1)[-1].isdigit()]
    print(f"fn {fn}: {len(sel)} 条指令, 源行 {min(lines)}..{max(lines)} ({len(srcs)} 个)")
    for s in srcs[:12]:
        print(f"  {s}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment", description="Loment 工具链")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fmt"); f.add_argument("files", nargs="+"); f.set_defaults(fn=cmd_fmt)
    d = sub.add_parser("doc"); d.add_argument("file"); d.add_argument("--out")
    d.set_defaults(fn=cmd_doc)
    g = sub.add_parser("diag"); g.add_argument("file"); g.add_argument("--json", action="store_true")
    g.set_defaults(fn=cmd_diag)
    i = sub.add_parser("ir"); i.add_argument("file"); i.add_argument("--objdump", action="store_true")
    i.set_defaults(fn=cmd_ir)
    t = sub.add_parser("test"); t.add_argument("file"); t.set_defaults(fn=cmd_test)
    b = sub.add_parser("bench"); b.add_argument("file"); b.add_argument("--n", type=int, default=200000)
    b.set_defaults(fn=cmd_bench)
    c = sub.add_parser("cov"); c.add_argument("file"); c.add_argument("--call", default="cov_main")
    c.set_defaults(fn=cmd_cov)
    g = sub.add_parser("dbg"); g.add_argument("file")
    g.add_argument("--fn", dest="func", default=None, help="按函数列出源行")
    g.add_argument("--addr", default=None, help="地址 -> 源行")
    g.set_defaults(fn=cmd_dbg)
    for name in ("build", "pkg", "lsp", "lib"):
        s = sub.add_parser(name)
        s.add_argument("rest", nargs=argparse.REMAINDER)
        s.set_defaults(fn=None, delegate=name)
    a = ap.parse_args(argv)
    if getattr(a, "delegate", None):
        if a.delegate == "pkg":
            import lompkg
            return lompkg.main(a.rest)
        if a.delegate == "lib":
            import lomlib
            return lomlib.main(a.rest)
        if a.delegate == "lsp":
            import loment_lsp
            return loment_lsp.main(a.rest)
        import loment_build
        return loment_build.main(a.rest)
    try:
        return a.fn(a)
    except lomc.LomError as e:
        # 解析期/装载期错误是**正常诊断**, 不是内部故障 —— 不该甩 Python 回溯给用户
        # (2026-09-15 用户实测: match 臂写成表达式、无值 return, 两条都只看到 traceback)。
        # 这类消息目前**没有错误码** (E001–E018 只覆盖 checker 的语义诊断, 见 docs/158 §1)。
        print(f"[ERR] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
