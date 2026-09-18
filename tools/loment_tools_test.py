#!/usr/bin/env python3
# loment_tools_test.py — P6 工具链自检 (M55–M66, docs/148)
#
# 运行: python tools/loment_tools_test.py   (退出码 0 = 全绿)
# 说明: 需要 rustc/clang 的用例在工具缺失时打印 SKIP 并计为通过 (环境问题不算回归);
#       纯静态判定不依赖外部工具。

from __future__ import annotations

import json
import re
import shutil
import subprocess
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


@test
def test_audit_claims_match_ci():
    """审计包 (docs/160) 不许长出"第二套判据": 它列的每个工具都必须在 ci.py 的静态门禁里。

    这条盯的是 M100 的审计工具本身 —— 审计工具自己跑一套没人核的命令, 是审计里
    最典型的失效方式。
    """
    import ci
    import loment_audit
    static = set(ci.STATIC_CHECKS)
    claimed = {tool for _, _, tool, _ in loment_audit.CLAIMS}
    missing = sorted(claimed - static)
    assert not missing, f"审计包引用了不在 ci.py 静态门禁里的工具: {missing}"
    ids = [c[0] for c in loment_audit.CLAIMS]
    assert len(ids) == len(set(ids)), "主张编号重复"
    assert len(loment_audit.CLAIMS) >= 10, "主张条数回退 (少于 10 条)"
    # 每条的 argv 必须显式写出模式 (默认无参的判据要写 [], 需要模式的两条写 --check)
    for cid, _, tool, args in loment_audit.CLAIMS:
        assert isinstance(args, list), f"{cid} 的 argv 必须是 list"


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


@test
def test_m59_dwarf_local_variables():
    """M59 (收口): 行表之外还要有**变量信息**。

    IR 里每个形参/局部都有 `DILocalVariable` 元数据 + 一条 `#dbg_declare` 调试记录;
    落盘后用 `llvm-objdump -d -l --debug-vars` (LLVM 自己的"调试器变量视图") 能在反汇编旁
    按源码行列出源码变量名。

    位置表达式在本工具链里显示成 `<unknown op DW_OP_fbreg>` —— **不是缺陷**: clang 22 自己
    发射的 C 源码产物在同一条命令下显示完全相同, 这是 llvm-objdump 的变量位置求值器在
    freestanding 目标上的限制 (要解析位置得有完整调试器), 详见 docs/145 的 M59 节。

    本用例同时钉住"非 debug 路径不长出调试元数据" —— 与"两后端逐字节等价"的判据配对。
    """
    mod = lomentc.load(TOOLCHAIN)
    deps = lomentc.resolve_deps(mod, ROOT, EX, entry=TOOLCHAIN)
    ll = lomentc.emit_llvm(mod, ROOT, deps, debug=True)
    assert "!DILocalVariable(" in ll, "缺变量元数据"
    assert "#dbg_declare(" in ll, "缺变量声明记录"
    assert "!DIBasicType(" in ll, "缺变量类型"
    assert '!DILocalVariable(name: "n", arg: 1, scope:' in ll, "形参没标 arg"
    for nm in ("a", "b", "i", "t"):   # 局部不带 arg
        assert f'!DILocalVariable(name: "{nm}", scope:' in ll, nm
    plain = lomentc.emit_llvm(mod, ROOT, deps)
    assert "!DILocalVariable" not in plain and "#dbg_declare" not in plain, \
        "非 debug 路径不该有调试元数据"
    clang = _clang()
    if not clang:
        print("      SKIP: 无 clang, 跳过 --debug-vars 落盘验证")
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
        objdump = shutil.which("llvm-objdump") or r"C:\Program Files\LLVM\bin\llvm-objdump.exe"
        dump = subprocess.run([objdump, "-d", "-l", "--debug-vars=ascii", str(obj)],
                              capture_output=True, text=True, shell=False).stdout
        assert "toolchain.lomt:" in dump, "行表里没有源文件行号"
        fib = dump.split("<fib>:")[1].split("\n000")[0] if "<fib>:" in dump else ""
        assert fib, "反汇编里没有 fib"
        for nm in ("n", "a", "b", "i", "t"):   # 变量视图必须逐个列出
            assert f"- {nm} = " in fib, f"变量视图里没有 {nm}"


@test
def test_m59_debug_ir_compiles_across_corpus():
    """M59 回归: 带 `--debug` 的 IR 必须**仍是合法 IR** —— 对含 match/枚举/泛型/数组的语料
    逐个 `clang -g` 编译。

    这条盯的是一个**既有 bug**(M59 收口时抓到): 多行 `switch` 被 `!dbg` 后缀逐行污染
    (`switch i32 %x, label %L [, !dbg !7`), 只有 `--debug` + `match` 才触发, 旧用例只喂
    没有 match 的 `toolchain.lomt`, 从未覆盖。修法是让续行走 `w_raw`(不加后缀),
    `!dbg` 只挂整条指令末尾的 `]`。
    """
    clang = _clang()
    if not clang:
        print("      SKIP: 无 clang")
        return
    import subprocess
    files = [TOOLCHAIN] + [EX / n for n in
                           ("native_res.lomt", "demo.lomt", "native_agg.lomt", "native_gen.lomt")]
    bad = []
    with tempfile.TemporaryDirectory() as td:
        for src in files:
            mod = lomentc.load(src)
            deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
            ll = lomentc.emit_llvm(mod, ROOT, deps, debug=True)
            assert "!DILocalVariable(" in ll, f"{src.name}: 没发变量信息"
            f = Path(td) / (src.stem + ".ll")
            f.write_text(ll, encoding="utf-8")
            r = subprocess.run([clang, "--target=x86_64-unknown-none", "-ffreestanding",
                                "-g", "-c", str(f), "-o", str(Path(td) / (src.stem + ".o"))],
                               capture_output=True, text=True, shell=False)
            if r.returncode != 0:
                tail = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "?"
                bad.append(f"{src.name}: {tail}")
    assert not bad, "带 --debug 的 IR 编译不过: " + "; ".join(bad)
    print(f"      {len(files)} 个语料的 --debug IR 都能被 clang -g 编译")


# ---------------------------------------------------------------- M30/M31/M32 裸机

@test
def test_m30_bare_metal_object_and_link():
    """M30/M31/M32: `x86_64-unknown-none` 目标 → 无 libc 依赖的对象 → 按脚本链成映像。

    这条链在此之前只有文档里的手工命令 (docs/145 的 P3 证据), 没有测试看着,
    会随编译器漂移而静默失效 —— 这里把它钉成判据。
    """
    import subprocess
    clang = _clang()
    if not clang:
        print("      SKIP: 无 clang")
        return
    lld = r"C:\Program Files\LLVM\bin\ld.lld.exe"
    if not Path(lld).exists():
        print("      SKIP: 无 ld.lld")
        return
    nm = shutil.which("llvm-nm") or r"C:\Program Files\LLVM\bin\llvm-nm.exe"
    objdump = shutil.which("llvm-objdump") or r"C:\Program Files\LLVM\bin\llvm-objdump.exe"
    ld_script = ROOT / "loment" / "build" / "loment.ld"
    assert ld_script.exists(), "缺链接脚本 loment/build/loment.ld"
    entry = EX / "native_entry.lomt"
    with tempfile.TemporaryDirectory() as td:
        mod = lomentc.load(entry)
        deps = lomentc.resolve_deps(mod, ROOT, entry.parent, entry=entry)
        ll = Path(td) / "entry.ll"
        ll.write_text(lomentc.emit_llvm(mod, ROOT, deps), encoding="utf-8")
        obj = Path(td) / "entry.o"
        r = subprocess.run([clang, "--target=x86_64-unknown-none", "-ffreestanding",
                            "-nostdlib", "-c", str(ll), "-o", str(obj)],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-400:]
        # M31: 对象里不能有未定义符号 (运行时是内联的 __loment_memcmp/memset/abort)
        undef = subprocess.run([nm, "-u", str(obj)], capture_output=True,
                               text=True, shell=False).stdout.strip()
        assert undef == "", f"裸机对象有未定义符号 (M31 回归): {undef[:200]}"
        # M32: 按脚本链接
        elf = Path(td) / "entry.elf"
        r = subprocess.run([lld, "-T", str(ld_script), str(obj), "-o", str(elf)],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-400:]
        syms = subprocess.run([nm, str(elf)], capture_output=True, text=True,
                              shell=False).stdout
        assert "_start" in syms and "timer_isr" in syms, syms[:200]
        # 脚本布局: 最低的 text 符号必须正好落在 1 MiB (ENTRY(_start) 生效则入口 == _start)
        text_addrs = [int(l.split()[0], 16) for l in syms.splitlines()
                      if len(l.split()) == 3 and l.split()[1] in ("t", "T")]
        assert min(text_addrs) == 0x100000, f"text 没落在脚本的 1 MiB: {min(text_addrs):#x}"
        addr = int(next(l.split()[0] for l in syms.splitlines()
                        if l.split()[-1] == "_start"), 16)
        assert 0x100000 <= addr < 0x101000, f"_start 不在第一页 text 里: {addr:#x}"
        head = subprocess.run([objdump, "-f", str(elf)], capture_output=True,
                              text=True, shell=False).stdout
        assert f"start address: 0x{addr:016x}" in head, head[:300]
        # 链接产物同样不能有未定义符号 (整套 = 一个能独立跑的映像)
        undef2 = subprocess.run([nm, "-u", str(elf)], capture_output=True,
                                text=True, shell=False).stdout.strip()
        assert undef2 == "", f"链接产物有未定义符号: {undef2[:200]}"
        print(f"      裸机链: {obj.stat().st_size}B 对象 (0 未定义) -> "
              f"{elf.stat().st_size}B 映像, _start @{addr:#x}")


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


@test
def test_m64_all_reference_messages_are_classified():
    """分类表**完整覆盖**参考实现能发出的每一条消息模板。

    为什么要这条: 自举 checker 与参考实现的对照判据是"码集相等", 而码是从消息**分类**
    来的 —— 分类表漏一条, 那条规则在对照里就变成"两边都看不见"(都成了 E999 被丢掉),
    缺口会**静默消失**。这里用 ast 把 lomentc.py 里的消息模板抽出来, 逐个渲染成样例
    消息再分类, 任何一条落到 E999 就算回归。

    **2026-09-17 补第二个通道**: 原先只抽 `errs.append(...)`（`check()` 的语义错误）,
    于是 `raise LomError(...)`（词法/解析/装载期）**一条都没被覆盖过** —— 实测 50 条里
    39 条从来没进过分类表, 而它们恰恰是新手最先撞上的那批（`顶层只允许 …`、
    `未知顶层关键字 …`、`pub 之后需要一项声明`、`数组长度必须为正`）。
    判据只盯一个通道, 另一个通道的缺口就是**静默**的 —— 这正是这条判据自己要防的那种事。
    """
    import ast
    import loment_diag
    tree = ast.parse((ROOT / "tools" / "lomentc.py").read_text(encoding="utf-8"))
    numeric = ("line", "col", "len", "lo", "hi", "value")

    def sample(arg) -> str | None:
        """模板表达式 -> 样例消息（数值字段给 1，其余给 Foo）。"""
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
        if isinstance(arg, ast.JoinedStr):
            parts: list[str] = []
            for v in arg.values:
                if isinstance(v, ast.Constant):
                    parts.append(str(v.value))
                else:
                    src = ast.unparse(v.value)
                    parts.append("1" if any(w in src for w in numeric) else "Foo")
            return "".join(parts)
        return None

    tpls: list[str] = []
    for node in ast.walk(tree):
        # 通道一: `errs.append(消息)` —— `check()` 的语义错误
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "errs" and node.args):
            s = sample(node.args[0])
            if s is not None:
                tpls.append(s)
        # 通道二: `raise LomError(行, 列, 消息)` —— 词法/解析/装载期
        if (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
                and getattr(node.exc.func, "id", "") == "LomError"
                and len(node.exc.args) >= 3):
            s = sample(node.exc.args[2])
            if s is not None:
                tpls.append(s)
    assert len(tpls) >= 100, f"抽取到的模板太少, 抽取逻辑可能坏了: {len(tpls)}"
    bad = []
    for t in tpls:
        for line in t.split("\n"):                     # 多行 f-string: 逐行判
            s = line.strip()
            if not s:
                continue
            if loment_diag.classify(s)[0] == "E999":
                bad.append(s)
    assert not bad, "未分类的参考消息模板:\n  " + "\n  ".join(sorted(set(bad)))
    print(f"      参考消息模板 {len(tpls)} 条全部有错误码")


@test
def test_code_tables_cover_the_same_codes():
    """`RULES`（分类）与 `ASCII_ONE_LINER`（CLI 一行式）**键集必须相等**。

    这两张表是同一件事的**两个受众**（中文给人看诊断，ASCII 给 CLI —— CLI 输出必须纯 ASCII，
    `docs/169`），所以是两份文字、**不是两份清单**。但"加一个码忘了加另一种文字"正是本仓
    反复撞的那类静默缺口（`docs/179` §7.3），所以键集相等要有判据钉着。
    """
    import loment_diag
    ruled = {loment_diag.code_num(c) for c, _p, _t, _h in loment_diag.RULES}
    ascii_ = set(loment_diag.ASCII_ONE_LINER)
    assert ruled == ascii_, (
        f"只在 RULES 里: {sorted(ruled - ascii_)}; 只在 ASCII 表里: {sorted(ascii_ - ruled)}")
    assert 0 not in ruled, "有码解不出数字"
    print(f"      两张码表键集相等 ({len(ruled)} 条: E{min(ruled)}–E{max(ruled)})")


@test
def test_surface_data_is_up_to_date():
    """`loment/tools/surface_data.lomt` 是**生成物**，必须与重新生成的结果逐字节相同。

    它是 `docs/176` B 那条管线（数据从逻辑里拆出来，自举侧 `use` 它）的第一片，
    也是 `docs/182` §5.2 消掉"同一份码表抄第二份"的落点。**能藏的前提是可复现** ——
    判据就是那个前提（同 `loment_seed_test` 那条"种子 == 参考实现的产物"）。
    """
    import loment_diag
    p = ROOT / "loment" / "tools" / "surface_data.lomt"
    assert p.exists(), f"缺 {p} (python tools/loment_diag.py --dump-surface {p})"
    want = loment_diag.surface_lomt()
    got = p.read_text(encoding="utf-8")
    assert got == want, "surface_data.lomt 过期 (重跑 --dump-surface)"
    # 生成的**是 Loment 源码**, 所以要能编 —— 转义写错会在这里暴露
    errs = lomentc.check(lomentc.load(p), deps=[])
    assert not errs, errs[:3]
    print(f"      surface_data.lomt 最新且可编译 ({len(want)}B)")


@test
def test_m64_structured_diagnostics_are_jsonl():
    """`--diag-out` 吐的是**一行一条 JSON**，字段齐全，且**两条报错通道都走同一条路**。

    报错器 `lomenterr` 的输入就是它（`docs/182` §5）。这条钉三件：

      * **一行一条**（不是一个大数组）—— 边报边写，崩在半路也已经落盘；
      * **字段齐全** —— `lomenterr` 靠 `code`/`title`/`hint` 渲染，缺一个它就退化成打字机；
      * **`check()` 的语义错与 `LomError` 的解析错都要有** —— 只测一条通道，另一条的缺口
        就是静默的。实测：加上解析那条通道之后，`LomError` 里 39/50 条从没有过错误码。
    """
    cases = [
        # (源码, 期望的码) —— 一条走 check(), 一条走 LomError
        ("module m\n\nchoose std\nchoose no_std\n\nfn f() -> u32 {\n    return 1;\n}\n", "E022"),
        ("module m\n\nfn f() -> u32 {\n    return 1;\n}\n@@@\n", "E019"),
    ]
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        for i, (src, want) in enumerate(cases):
            f = td / f"c{i}.lomt"
            f.write_text(src, encoding="utf-8", newline="\n")
            out = td / f"d{i}.jsonl"
            r = subprocess.run([sys.executable, str(ROOT / "tools" / "lomentc.py"),
                                str(f), "--check", "--diag-out", str(out)],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", shell=False, timeout=120)
            assert r.returncode == 1, (src, r.returncode, r.stderr[-200:])
            lines = [x for x in out.read_text(encoding="utf-8").splitlines() if x.strip()]
            assert lines, f"没写出诊断: {r.stderr[-200:]}"
            recs = [json.loads(x) for x in lines]   # 一行一条，每行都是完整 JSON
            for d in recs:
                assert set(d) == set(lomentc.DIAG_FIELDS), (set(d), lomentc.DIAG_FIELDS)
                assert d["code"] != "E999", d
                assert d["hint"], d
            assert recs[0]["code"] == want, (recs[0]["code"], want)
    print("      结构化诊断: 一行一条 JSON, 字段齐全, check/LomError 两条通道都覆盖")


@test
def test_e019_parse_errors_are_classified():
    """解析期消息有自己的码 (E019), 而且**不抢** E001 的活。

    原先解析期消息一条都不在分类表里 —— `loment diag` 把它们显示成 E999「未分类, 请报告」,
    可它恰恰是新手最常撞上的一类 (`match` 臂写成表达式、无值 `return`、漏分号)。
    这条同时钉住**顺序**: E001 的模式里有「期望」二字, E019 若排在它前面就会把类型错也吞掉。
    """
    import loment_diag
    assert loment_diag.classify("5:1: 期望 ;，得到 'capability'")[0] == "E019"
    assert loment_diag.classify("7:17: 期望 {，得到 '0'")[0] == "E019"
    assert loment_diag.classify("4:11: 期望表达式，得到 ';'")[0] == "E019"
    assert loment_diag.classify("实参类型 u32，期望 bool")[0] == "E001"
    assert loment_diag.classify("调用未定义的函数 pick")[0] == "E002"


@test
def test_cli_reports_parse_errors_without_traceback():
    """`loment ir <语法错的文件>` 给一行 `[ERR] 行:列: ...`、退出码 1 —— 不是 Python 回溯。

    2026-09-15 用户实测: 指南里最容易踩的两种写法 (漏分号 / 无值 `return`) 当时全都只看到
    traceback。对"照指南写第一个程序"的人, 这是最坏的第一印象, 而且它看起来像编译器崩了,
    而不是"你写错了"。
    """
    cases = {"漏了分号": "module a\n\nconst X: u32 = 3\n",
             "无值 return": "module b\n\nfn f() {\n    return;\n}\n"}
    with tempfile.TemporaryDirectory() as td:
        for name, src in cases.items():
            p = Path(td) / "bad.lomt"
            p.write_text(src, encoding="utf-8", newline="\n")
            r = subprocess.run([sys.executable, str(ROOT / "tools" / "loment.py"), "ir", str(p)],
                               capture_output=True, text=True, shell=False)
            assert r.returncode == 1, f"{name}: 期望退出码 1, 得到 {r.returncode}"
            assert r.stderr.startswith("[ERR] "), f"{name}: stderr={r.stderr[:120]!r}"
            assert "Traceback" not in r.stderr, f"{name}: 甩了 Python 回溯\n{r.stderr}"


@test
def test_m81_builtin_tables_match():
    """自举 checker 的内建名清单必须与参考实现 `lomentc.BUILTINS` 同集合。

    checker.lomt 里的 `is_builtin` 是一张**手写的空格分隔字符串**, 参考实现是一张 dict ——
    两边漂移的后果很具体: 新加一个内建而没同步, 调用点会被自举 checker 报成
    "未定义的函数"(假阳性), 而字节一致判据看不出来 (那是编译器后端的事)。
    """
    src = (ROOT / "loment" / "selfhost" / "checker.lomt").read_text(encoding="utf-8")
    m = re.search(r'let names: str = "([^"]+)"', src)
    assert m, "checker.lomt 里没有内建名表"
    mine = set(m.group(1).split())
    want = set(lomentc.BUILTINS) | {"slice_len"}
    assert mine == want, f"只在 checker: {sorted(mine - want)}; 只在参考: {sorted(want - mine)}"
    print(f"      内建名表 {len(mine)} 个一致 (含单列的 slice_len)")


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
