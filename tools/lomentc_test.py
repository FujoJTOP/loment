#!/usr/bin/env python3
# lomentc_test.py — L1 Loment 编译器 + Potato v0 校验器自检 (docs/143 §验收)
#
# 运行: python tools/lomentc_test.py   (退出码 0 = 全绿)
# 说明: 编译/运行验证 (rustc) 由 docs/143 §5 记录的命令执行, 本套件只做静态判定。

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomc  # noqa: E402
import lomentc  # noqa: E402
import potato  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "loment" / "examples" / "demo.lomt"

TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def parse(src: str) -> lomentc.Module:
    return lomentc.Parser(lomc.lex(src), src).parse()


def errs(src: str) -> list[str]:
    return lomentc.check(parse(src))


# ---------------------------------------------------------------- 正例

@test
def test_int_literal_as_ptr_is_legal_and_lowers_to_inttoptr():
    """`0 as ptr`（空指针的惯用写法）要过检查，且**发射成 inttoptr**。

    2026-09-15 由 lompi（另一条线用 Loment 写的包管理器）实测抓到：参考实现把整型
    **字面量**转 ptr 判成非法目标（`as 目标类型非法 ptr`），而自举镜一直放行 ——
    于是同一份源码**参考报错、打包版能编**。两条一起修，缺一条都不行：
    只放行不修发射，参考会发出 `zext i32 0 to ptr`，而 zext 产不出指针，那是**非法 IR**。
    """
    NL = chr(10)
    src = NL.join([
        "module m", "",
        "fn z() -> ptr {", "    return 0 as ptr;", "}", "",
        "fn w(v: u64) -> ptr {", "    return v as ptr;", "}", "",
        "fn _start() {", "    syscall4(60, 0, 0, 0);", "}", "",
    ])
    assert errs(src) == [], errs(src)
    ir = lomentc.emit_llvm(parse(src), ROOT, [])
    assert "inttoptr i32 0 to ptr" in ir, ir
    assert "zext i32 0 to ptr" not in ir, ir
    # 变量那种窄写法本来就没坏，别在修字面量时把它碰坏
    assert "inttoptr i64" in ir, ir


@test
def test_demo_parses_and_checks():
    mod = lomentc.load(DEMO)
    deps = lomentc.resolve_deps(mod, ROOT, DEMO.parent, entry=DEMO)
    assert lomentc.check(mod, deps=deps) == [], lomentc.check(mod, deps=deps)
    assert [f.name for f in mod.funcs] == [
        "fib", "gcd", "popcount", "in_domain", "blk_end", "mask_low", "has_flag",
        "sum_array", "fill_incr", "color_code", "sum_range", "quadruple", "max_blocks",
        "shape_area",
    ]
    # demo.lomt 用的是**名字形式** (语料 2026-09-15 迁过去了); L0 的 lom/fujr.lom 仍是路径形式
    assert mod.name_imports == ["mathutil"], mod.name_imports
    assert mod.uses == ["lom/fujr.lom"]
    assert [c.name for c in mod.caps] == ["blk_write"]
    assert [s.name for s in mod.structs] == ["Blk"]
    names = [e.name for e in mod.enums]
    assert "Color" in names and "Shape" in names, names  # 另有预置 Option/Result
    assert [c.name for c in mod.consts] == ["MAX_BLKS"]
    assert mod.excluded == ["network: 本单元不申请任何 net 能力", "usb: 不触碰 USB 子系统"]


@test
def test_transpile_is_deterministic():
    mod = lomentc.load(DEMO)
    assert lomentc.emit_rust(mod, ROOT) == lomentc.emit_rust(mod, ROOT)
    assert lomentc.emit_potato(mod, ROOT) == lomentc.emit_potato(mod, ROOT)


@test
def test_rust_output_shape():
    rs = lomentc.emit_rust(lomentc.load(DEMO), ROOT)
    for needle in (
        "pub fn fib(n: u32) -> u32 {",
        "pub const CAP_BLK_WRITE_SPACE: &str = \"disk\";",
        "pub const CAP_BLK_WRITE_REVOCABLE: bool = true;",
        "pub const HEADER_SIZE: usize = 64;",   # 来自 use 的 L0 布局
        "while (i < n) {",
    ):
        assert needle in rs, f"缺少 {needle!r}"


@test
def test_potato_emits_used_layouts():
    doc = json.loads(lomentc.emit_potato(lomentc.load(DEMO), ROOT))
    assert doc["potato"] == "v1" and doc["unit"] == "demo" and doc["language"] == "loment"
    assert [r["name"] for r in doc["layouts"]] == ["Header", "Section"]
    cap = doc["capabilities"][0]
    assert cap == {"name": "blk_write", "domain": {"space": "disk", "lo": 0, "hi": 4}, "revocable": True}
    assert potato.validate(doc) == []


# ---------------------------------------------------------------- 负例: 语义检查

@test
def test_type_mismatch_rejected():
    e = errs('module m\nfn f() -> u32 { let x: u32 = true; return x; }\n')
    assert any("表达式类型" in x for x in e), e


@test
def test_unknown_function_rejected():
    e = errs('module m\nfn f() -> u32 { return g(1); }\n')
    assert any("未定义的函数" in x for x in e), e


@test
def test_arity_mismatch_rejected():
    e = errs('module m\nfn g(a: u32) -> u32 { return a; }\nfn f() -> u32 { return g(1, 2); }\n')
    assert any("实参" in x for x in e), e


@test
def test_bad_condition_type_rejected():
    e = errs('module m\nfn f() -> u32 { let x: u32 = 1; if x { return 1; } return 0; }\n')
    assert any("条件类型" in x for x in e), e


@test
def test_inverted_capability_domain_rejected():
    e = errs('module m\ncapability c : disk[4..2]\nfn f() -> u32 { return 0; }\n')
    assert any("下界" in x for x in e), e


@test
def test_duplicate_function_rejected():
    e = errs('module m\nfn f() -> u32 { return 0; }\nfn f() -> u32 { return 1; }\n')
    assert any("重复定义" in x for x in e), e


@test
def test_undeclared_variable_rejected():
    e = errs('module m\nfn f() -> u32 { x = 1; return 0; }\n')
    assert any("未声明" in x for x in e), e


@test
def test_return_type_mismatch_rejected():
    e = errs('module m\nfn f() -> u32 { return true; }\n')
    assert any("return 类型" in x for x in e), e


# ---------------------------------------------------------------- 负例: Potato 校验器

@test
def test_potato_rejects_unknown_key():
    doc = json.loads(lomentc.emit_potato(lomentc.load(DEMO), ROOT))
    doc["extra"] = 1
    assert any("未知顶层字段" in x for x in potato.validate(doc))


@test
def test_potato_rejects_bad_domain():
    doc = json.loads(lomentc.emit_potato(lomentc.load(DEMO), ROOT))
    doc["capabilities"][0]["domain"] = {"space": "disk", "lo": 9, "hi": 1}
    assert any("区间非法" in x for x in potato.validate(doc))


@test
def test_potato_rejects_overlapping_layout():
    doc = json.loads(lomentc.emit_potato(lomentc.load(DEMO), ROOT))
    doc["layouts"][0]["fields"] = [
        {"name": "a", "type": "u32", "offset": 0},
        {"name": "b", "type": "u32", "offset": 2},
    ]
    assert any("重叠" in x for x in potato.validate(doc))


@test
def test_potato_rejects_out_of_bounds():
    doc = json.loads(lomentc.emit_potato(lomentc.load(DEMO), ROOT))
    doc["layouts"][0]["fields"] = [{"name": "a", "type": "u64", "offset": 60}]
    assert any("越界" in x for x in potato.validate(doc))


@test
def test_potato_rejects_bad_signature():
    doc = json.loads(lomentc.emit_potato(lomentc.load(DEMO), ROOT))
    doc["functions"][0]["ret"] = "f32"
    assert any("ret 非法类型" in x for x in potato.validate(doc))


# ---------------------------------------------------------------- --check 漂移

@test
def test_check_mode_detects_drift():
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "demo.rs"
        obj = Path(td) / "demo.json"
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            assert lomentc.main([str(DEMO), "--emit-rust", str(out),
                                 "--emit-potato", str(obj)]) == 0
            assert lomentc.main([str(DEMO), "--emit-rust", str(out),
                                 "--emit-potato", str(obj), "--check"]) == 0
            out.write_text("// drifted\n", encoding="utf-8")
            assert lomentc.main([str(DEMO), "--emit-rust", str(out),
                                 "--emit-potato", str(obj), "--check"]) == 1


@test
def test_struct_literal_missing_field_rejected():
    e = errs('module m\nstruct P { x: u32, y: u32 }\nfn f() -> u32 { let p: P = P { x: 1 }; return p.x; }\n')
    assert any("缺字段" in x for x in e), e


@test
def test_struct_unknown_field_rejected():
    e = errs('module m\nstruct P { x: u32 }\nfn f() -> u32 { let p: P = P { x: 1, z: 2 }; return p.x; }\n')
    assert any("无字段" in x for x in e), e


@test
def test_field_access_on_non_struct_rejected():
    e = errs('module m\nfn f() -> u32 { let x: u32 = 1; return x.y; }\n')
    assert any("非结构体" in x for x in e), e


@test
def test_unknown_type_in_signature_rejected():
    e = errs('module m\nfn f(p: Foo) -> u32 { return 0; }\n')
    assert any("未声明" in x for x in e), e


@test
def test_bitwise_precedence():
    """& 比 | 结合更紧 (与 Rust 一致)。"""
    rs = lomentc.emit_rust(parse('module m\nfn f() -> u32 { return 1 | 2 & 3; }\n'), ROOT)
    assert "(1 | (2 & 3))" in rs, rs


@test
def test_excluded_and_types_in_potato():
    doc = json.loads(lomentc.emit_potato(lomentc.load(DEMO), ROOT))
    assert doc["excluded"] == ["network: 本单元不申请任何 net 能力", "usb: 不触碰 USB 子系统"]
    assert doc["types"] == [
        {"name": "Blk", "fields": [{"name": "off", "type": "u32"}, {"name": "len", "type": "u32"}]}
    ]
    assert potato.validate(doc) == []


@test
def test_const_and_array_codegen():
    rs = lomentc.emit_rust(lomentc.load(DEMO), ROOT)
    assert "pub const MAX_BLKS: u32 = 8;" in rs
    assert "pub fn sum_array(xs: [u32; 4]) -> u32 {" in rs
    assert "xs[(i) as usize]" in rs, "下标应降级为 as usize"


@test
def test_const_in_potato():
    doc = json.loads(lomentc.emit_potato(lomentc.load(DEMO), ROOT))
    assert doc["consts"] == [{"name": "MAX_BLKS", "type": "u32", "value": 8}]
    assert potato.validate(doc) == []


@test
def test_array_literal_type_mismatch_rejected():
    e = errs('module m\nfn f() -> u32 { let a: [u32; 2] = [1, true]; return a[0]; }\n')
    assert any("类型不一致" in x for x in e), e


@test
def test_array_length_mismatch_rejected():
    e = errs('module m\nfn f() -> u32 { let a: [u32; 3] = [1, 2]; return a[0]; }\n')
    assert any("数组长度不符" in x for x in e), e


@test
def test_index_on_non_array_rejected():
    e = errs('module m\nfn f() -> u32 { let x: u32 = 1; return x[0]; }\n')
    assert any("非数组" in x for x in e), e


@test
def test_const_non_integer_rejected():
    e = errs('module m\nconst C: bool = 1;\nfn f() -> u32 { return 0; }\n')
    assert any("必须是整型" in x for x in e), e


@test
def test_potato_rejects_bad_const():
    doc = json.loads(lomentc.emit_potato(lomentc.load(DEMO), ROOT))
    doc["consts"][0]["type"] = "bool"
    assert any("必须是整型" in x for x in potato.validate(doc))


@test
def test_potato_accepts_array_signature():
    doc = json.loads(lomentc.emit_potato(lomentc.load(DEMO), ROOT))
    fn = next(f for f in doc["functions"] if f["name"] == "sum_array")
    assert fn["params"][0]["type"] == "[u32; 4]"
    assert potato.validate(doc) == []


@test
def test_enum_match_and_for_codegen():
    rs = lomentc.emit_rust(lomentc.load(DEMO), ROOT)
    assert "pub enum Color {" in rs
    assert "match c {" in rs and "Color::Red => {" in rs and "_ => {" in rs
    assert "for i in 0..n {" in rs


@test
def test_potato_has_enums():
    doc = json.loads(lomentc.emit_potato(lomentc.load(DEMO), ROOT))
    assert {"name": "Color", "variants": ["Red", "Green", "Blue"]} in doc["enums"]
    assert potato.validate(doc) == []


@test
def test_payload_enum_codegen():
    rs = lomentc.emit_rust(lomentc.load(DEMO), ROOT)
    assert "pub enum Shape {" in rs and "Circle(u32)," in rs and "Empty," in rs
    assert "Shape::Circle(r) => {" in rs and "Shape::Square(a) => {" in rs


@test
def test_payload_binding_is_typed():
    e = errs(
        "module m\nenum S { C(u32) }\nfn f(s: S) -> u32 {\n"
        "    match s {\n        S::C(v) => { let x: bool = v; return 0; }\n    }\n}\n"
    )
    assert any("表达式类型" in x for x in e), e


@test
def test_payload_variant_requires_binding():
    e = errs(
        "module m\nenum S { C(u32) }\nfn f(s: S) -> u32 {\n"
        "    match s {\n        S::C => { return 0; }\n    }\n}\n"
    )
    assert any("需绑定" in x for x in e), e


@test
def test_no_payload_variant_cannot_bind():
    e = errs(
        "module m\nenum S { E }\nfn f(s: S) -> u32 {\n"
        "    match s {\n        S::E(x) => { return 0; }\n    }\n}\n"
    )
    assert any("不能绑定" in x for x in e), e


@test
def test_ctor_on_payloadless_rejected():
    e = errs("module m\nenum S { E }\nfn f() -> S { return S::E(1); }\n")
    assert any("不能带参数" in x for x in e), e


@test
def test_payload_type_mismatch_rejected():
    e = errs("module m\nenum S { C(u32) }\nfn f() -> S { return S::C(true); }\n")
    assert any("载荷类型" in x for x in e), e


@test
def test_unknown_payload_type_rejected():
    e = errs("module m\nenum S { C(Foo) }\nfn f() -> u32 { return 0; }\n")
    assert any("载荷类型 Foo 未声明" in x for x in e), e


@test
def test_potato_payloads_and_bad_payload():
    doc = json.loads(lomentc.emit_potato(lomentc.load(DEMO), ROOT))
    shape = next(e for e in doc["enums"] if e["name"] == "Shape")
    assert shape["payloads"] == {"Circle": "u32", "Square": "u32"}
    assert potato.validate(doc) == []
    shape["payloads"]["Nope"] = "u32"  # 不是已声明的变体
    assert any("不是已声明变体" in x for x in potato.validate(doc))


@test
def test_match_non_exhaustive_rejected():
    e = errs(
        "module m\nenum C { A, B }\nfn f(c: C) -> u32 {\n"
        "    match c {\n        C::A => { return 1; }\n    }\n    return 0;\n}\n"
    )
    assert any("不穷尽" in x for x in e), e


@test
def test_match_unknown_variant_rejected():
    e = errs(
        "module m\nenum C { A }\nfn f(c: C) -> u32 {\n"
        "    match c {\n        C::Z => { return 1; }\n        _ => { return 0; }\n    }\n}\n"
    )
    assert any("无变体" in x for x in e), e


@test
def test_match_on_non_enum_rejected():
    e = errs(
        "module m\nfn f(x: u32) -> u32 {\n"
        "    match x {\n        _ => { return 0; }\n    }\n}\n"
    )
    assert any("不是枚举" in x for x in e), e


@test
def test_duplicate_enum_variant_rejected():
    e = errs("module m\nenum C { A, A }\nfn f(c: C) -> u32 { return 0; }\n")
    assert any("变体 A 重复" in x for x in e), e


@test
def test_for_bounds_type_rejected():
    e = errs(
        "module m\nfn f(b: bool) -> u32 {\n    let s: u32 = 0;\n"
        "    for i in 0..b { s = s + i; }\n    return s;\n}\n"
    )
    assert any("应为整型" in x for x in e), e


@test
def test_potato_rejects_bad_enum():
    doc = json.loads(lomentc.emit_potato(lomentc.load(DEMO), ROOT))
    doc["enums"][0]["variants"] = ["Red", "Red"]
    assert any("重复" in x for x in potato.validate(doc))


@test
def test_import_codegen_and_potato():
    deps = lomentc.resolve_deps(lomentc.load(DEMO), ROOT, DEMO.parent, entry=DEMO)
    assert [d.name for d in deps] == ["mathutil"]
    rs = lomentc.emit_rust(lomentc.load(DEMO), ROOT, deps)
    assert "// ==== 导入模块 mathutil ====" in rs
    assert "pub fn double(x: u32) -> u32 {" in rs and "pub struct Pair {" in rs
    assert rs.count("pub fn double") == 1, "导入模块只应输出一次"
    doc = json.loads(lomentc.emit_potato(lomentc.load(DEMO), ROOT, deps))
    assert doc["imports"] == ["mathutil"]
    assert potato.validate(doc) == []


@test
def test_import_dedup():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "base.lomt").write_text('module base\nfn fb() -> u32 { return 1; }\n', encoding="utf-8")
        (d / "a.lomt").write_text(
            'module a\nuse "base.lomt"\nuse "base.lomt"\nfn fa() -> u32 { return fb(); }\n',
            encoding="utf-8",
        )
        mod = lomentc.load(d / "a.lomt")
        deps = lomentc.resolve_deps(mod, d, d, entry=d / "a.lomt")
        assert [x.name for x in deps] == ["base"]


@test
def test_cycle_import_rejected():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "a.lomt").write_text('module a\nuse "b.lomt"\nfn fa() -> u32 { return 1; }\n', encoding="utf-8")
        (d / "b.lomt").write_text('module b\nuse "a.lomt"\nfn fb() -> u32 { return 2; }\n', encoding="utf-8")
        mod = lomentc.load(d / "a.lomt")
        try:
            lomentc.resolve_deps(mod, d, d, entry=d / "a.lomt")
        except lomc.LomError as ex:
            assert "循环导入" in ex.msg, ex.msg
        else:
            raise AssertionError("循环导入应被拒绝")


@test
def test_name_import_resolves():
    """名字形式 `use mathutil` 落到 loment/examples/mathutil.lomt (搜索根第一条命中)。"""
    assert lomentc.resolve_name("mathutil", ROOT) == ROOT / "loment" / "examples" / "mathutil.lomt"
    mod = parse("module m\nuse mathutil\nfn f() -> u32 { return double(2); }\n")
    deps = lomentc.resolve_deps(mod, ROOT, ROOT, entry=ROOT / "x.lomt")
    assert [x.name for x in deps] == ["mathutil"]


@test
def test_name_import_not_found():
    mod = parse("module m\nuse nosuchmod\nfn f() -> u32 { return 0; }\n")
    try:
        lomentc.resolve_deps(mod, ROOT, ROOT, entry=ROOT / "x.lomt")
    except lomc.LomError as ex:
        assert "名字导入找不到模块" in ex.msg, ex.msg
    else:
        raise AssertionError("找不到的名字导入应报错")


@test
def test_name_import_ambiguous_is_rejected():
    """命中多处必须报错, **不许静默取第一个** —— 否则搜索顺序会变成隐藏语义。"""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        for rel in ("loment/lib", "loment/examples"):
            (d / rel).mkdir(parents=True)
            (d / rel / "dup.lomt").write_text("module dup\n", encoding="utf-8")
        try:
            lomentc.resolve_name("dup", d)
        except lomc.LomError as ex:
            assert "名字导入有歧义" in ex.msg and "lib" in ex.msg and "examples" in ex.msg, ex.msg
        else:
            raise AssertionError("歧义的名字导入应报错")


@test
def test_name_and_path_import_are_equivalent():
    """两种写法装载出**同一串依赖**(名字形式只是路径形式的一层解析)。"""
    src = 'module m\nuse mathutil\nfn f() -> u32 { return double(2); }\n'
    got = [x.name for x in lomentc.resolve_deps(
        parse(src), ROOT, ROOT, entry=ROOT / "x.lomt")]
    want = [x.name for x in lomentc.resolve_deps(
        parse('module m\nuse "loment/examples/mathutil.lomt"\nfn f() -> u32 { return double(2); }\n'),
        ROOT, ROOT, entry=ROOT / "x.lomt")]
    assert got == want == ["mathutil"]


@test
def test_missing_import_rejected():
    mod = parse('module m\nuse "nope/void.lomt"\nfn f() -> u32 { return 0; }\n')
    try:
        lomentc.resolve_deps(mod, ROOT, ROOT, entry=ROOT / "x.lomt")
    except lomc.LomError as ex:
        assert "不存在" in ex.msg, ex.msg
    else:
        raise AssertionError("缺失导入应报错")


@test
def test_collision_with_imported_symbol_rejected():
    deps = lomentc.resolve_deps(lomentc.load(DEMO), ROOT, DEMO.parent, entry=DEMO)
    clash = parse('module m\nfn double(x: u32) -> u32 { return x; }\n')
    e = lomentc.check(clash, deps=deps)
    assert any("重复定义" in x for x in e), e


@test
def test_const_visible_inside_function():
    e = errs("module m\nconst C: u32 = 1;\nfn f() -> u32 { return C; }\n")
    assert e == [], e


NATIVE = ROOT / "loment" / "examples" / "native.lomt"


@test
def test_llvm_backend_m0():
    """M0 原生后端: 标量子集 -> LLVM IR (docs/144)。"""
    mod = lomentc.load(NATIVE)
    ir = lomentc.emit_llvm(mod, ROOT)
    assert lomentc.emit_llvm(mod, ROOT) == ir, "IR 生成不确定"
    for needle in ("define i32 @fib(i32 %n) {", "alloca i32", "icmp ult i32",
                   "L1_wcond:", "br i1", "ret i32"):
        assert needle in ir, f"IR 缺少 {needle!r}"
    assert "mul i32" in ir and ", 3" in ir, "常量应内联到指令"


@test
def test_llvm_signedness_picks_instruction():
    assert "sdiv i32" in lomentc.emit_llvm(parse("module m\nfn f(a: i32, b: i32) -> i32 { return a / b; }\n"), ROOT)
    assert "udiv i32" in lomentc.emit_llvm(parse("module m\nfn f(a: u32, b: u32) -> u32 { return a / b; }\n"), ROOT)


@test
def test_rust_enum_derive_matches_what_we_emit():
    """枚举的 `PartialEq` 只在**载荷也真的可比较**时才发。

    否则 Rust 为枚举生成的 `impl PartialEq` 会要求载荷类型也可比较, 于是把源码里根本
    没写过的 `Span == Span` 编出来, 报 E0369(2026-09-15 用户实测: enum 载荷是 struct 时)。

    钉的是**判据与实际发出的 derive 一致**: 第一版拿"struct 的字段递归算能不能派生"
    当判据, 结论是"Span 可以", 可 struct 那一段根本没发 PartialEq —— 两边对不上, 照旧
    E0369。所以这里比的是生成出来的文本本身。
    """
    src = ("module m\n\nstruct Span {\n    lo: u32,\n    hi: u32,\n}\n\n"
           "enum WithStruct {\n    Empty,\n    Line(Span),\n}\n\n"
           "enum WithScalar {\n    A,\n    B(u32),\n}\n")
    rs = lomentc.emit_rust(parse(src), ROOT)
    assert "#[derive(Clone, Copy, PartialEq)]\npub enum WithScalar" in rs, rs[:400]
    assert "#[derive(Clone, Copy)]\npub enum WithStruct" in rs, rs[:400]
    assert "#[derive(Clone, Copy, PartialEq)]\npub enum WithStruct" not in rs, rs[:400]


@test
def test_native_match_on_call_subject_does_not_crash():
    """被匹配值是**函数调用**时, 原生发射器不许崩。

    2026-09-15 由用户实测报出来(`KeyError: 'v'` @ lomentc.py 的绑定存取), 7 行复现。
    根因不在 match 的发射代码, 而在 `_collect_locals`: 它推被匹配值的类型时给 `expr_type`
    传了**空的 funcs/structs**, 于是"主体是调用"推不出返回类型 -> 枚举查不到 -> 匹配的
    绑定变量根本不会被收集 -> 发射期 `self.vars[bind]` 直接 KeyError(Python 栈回溯,
    不是诊断, 所以 `loment diag` 还说没错误)。同一处对 `for` 的上下界也一样 —— 上下界
    是调用(或字段访问)时, 推断同样落空。
    """
    src = ("module m\n"
           "fn g() -> Result<i64, u32> {\n    return Result::Ok(7);\n}\n"
           "fn m1() -> bool {\n    match g() {\n"
           "        Result_i64_u32::Ok(v) => { return v == 7; }\n"
           "        Result_i64_u32::Err(e) => { return e == 0; }\n    }\n}\n"
           "fn m2() -> bool {\n    if let Result_i64_u32::Ok(v) = g() {\n"
           "        return v == 7;\n    }\n    return false;\n}\n"
           "fn lo() -> u32 {\n    return 1;\n}\n"
           "fn hi() -> u32 {\n    return 3;\n}\n"
           "fn s() -> u32 {\n    let acc: u32 = 0;\n    for i in lo()..hi() {\n"
           "        acc = acc + i;\n    }\n    return acc;\n}\n")
    # 走**和用户完全相同**的那条路: load -> resolve_deps -> check -> emit_llvm
    # (单独 parse 少了装载/准备那几步, 结果不能代表真实行为)
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "m.lomt"
        f.write_text(src, encoding="utf-8", newline="\n")
        mod = lomentc.load(f)
        deps = lomentc.resolve_deps(mod, ROOT, f.parent, entry=f)
        assert lomentc.check(mod, deps=deps) == []
        text = lomentc.emit_llvm(mod, ROOT, deps)
    # 绑定与循环变量**真的落到了 IR**(收集到了 -> 有 alloca)
    assert "%v.addr = alloca i64" in text, text[:400]
    assert "%i.addr = alloca i32" in text, text[:400]
    assert "icmp slt i32" in lomentc.emit_llvm(parse("module m\nfn f(a: i32, b: i32) -> bool { return a < b; }\n"), ROOT)
    assert "lshr i32" in lomentc.emit_llvm(parse("module m\nfn f(a: u32, b: u32) -> u32 { return a >> b; }\n"), ROOT)


@test
def test_llvm_aggregates_m23_m26():
    """M23–M26: struct / 数组 / 枚举 / match 的 IR 形态。"""
    ir = lomentc.emit_llvm(lomentc.load(ROOT / "loment" / "examples" / "native_agg.lomt"), ROOT)
    assert "alloca { i32, i32 }" in ir, "struct alloca"
    assert "getelementptr inbounds { i32, i32 }" in ir, "struct GEP"
    assert "alloca [4 x i32]" in ir, "数组 alloca"
    assert "alloca { i32, i64 }" in ir, "枚举 tagged union"
    assert "insertvalue { i32, i64 }" in ir and "extractvalue { i32, i64 }" in ir
    assert "switch i32" in ir, "match 降级"
    assert "phi i1" in ir, "M27 短路"


@test
def test_llvm_accepts_aggregate_signature():
    """聚合参数在原生路径可用 (LLVM 结构体按值); C ABI 不保证, 故勿从 C 直接调用。"""
    ir = lomentc.emit_llvm(
        parse("module m\nstruct S { a: u32 }\nfn f(s: S) -> u32 { return s.a; }\n"), ROOT
    )
    assert "define i32 @f({ i32 } %s)" in ir, ir


@test
def test_llvm_check_mode():
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "n.ll"
        obj = Path(td) / "n.json"
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            assert lomentc.main([str(NATIVE), "--emit-llvm", str(out),
                                 "--emit-potato", str(obj)]) == 0
            assert lomentc.main([str(NATIVE), "--emit-llvm", str(out),
                                 "--emit-potato", str(obj), "--check"]) == 0
            out.write_text("; drift\n", encoding="utf-8")
            assert lomentc.main([str(NATIVE), "--emit-llvm", str(out),
                                 "--emit-potato", str(obj), "--check"]) == 1


@test
def test_str_builtins_m1_m2():
    """M1/M2: str 字面量 + str_len/str_eq/str_byte 的双后端降级。"""
    mod = lomentc.load(ROOT / "loment" / "examples" / "native_str.lomt")
    rs = lomentc.emit_rust(mod, ROOT)
    assert '.len() as u32' in rs and 'as_bytes()[' in rs
    assert '"abc"' in rs
    ir = lomentc.emit_llvm(mod, ROOT)
    assert "constant [3 x i8]" in ir, "字符串常量"
    assert "insertvalue { ptr, i64 }" in ir, "str 值构造"
    assert "call i32 @__loment_memcmp" in ir, "str_eq 降级 (自带运行时)"
    assert "zext i8" in ir, "str_byte 降级"


@test
def test_unit_wide_unique_names():
    """跨模块同名顶层符号 -> 编译错误 (发射符号是平的, 见 docs/150 缺口①)。

    单元里两个模块各声明一个私有 `helper` 时, 后端会发出两条 `define @helper` (非法 IR),
    调用点还会解析到同一个函数 (静默错编) —— 必须编译期拒。同一模块**内部**的重名照旧
    由原规则报 (别被这条取代)。
    """
    entry = ROOT / "loment" / "selfhost" / "neg_across" / "entry.lomt"
    mod = lomentc.load(entry)
    deps = lomentc.resolve_deps(mod, ROOT, entry.parent, entry=entry)
    unit_errs = lomentc.check(mod, deps=deps)
    assert any("重名" in e for e in unit_errs), unit_errs
    e2 = errs('module m\nfn f() -> u32 { return 1; }\nfn f() -> u32 { return 2; }\n')
    assert any("重复定义" in x for x in e2), e2


@test
def test_m2_concat_dual_path_runs_equal():
    """M2/M28: `str_concat` 的两条路径**真的都跑一遍**, 输出逐行相同。

    在这之前"双路径逐值一致"只有文档里的手工命令 (docs/143 §5), 没有自动化判据。
    这条把它钉住: Rust 侧用 rustc 编 `test_*() -> bool` 探针, IR 侧用 clang 编 C 驱动
    调同名函数, 两边打印的 `名字 0/1` 清单必须一字不差, 且全是 1。
    """
    import shutil
    import subprocess
    rustc = shutil.which("rustc")
    clang = shutil.which("clang") or (
        r"C:\Program Files\LLVM\bin\clang.exe"
        if Path(r"C:\Program Files\LLVM\bin\clang.exe").exists() else None)
    if not rustc or not clang:
        print("      SKIP: 无 rustc/clang")
        return
    p = ROOT / "loment" / "examples" / "native_concat.lomt"
    mod = lomentc.load(p)
    deps = lomentc.resolve_deps(mod, ROOT, p.parent, entry=p)
    assert not lomentc.check(mod, deps=deps), lomentc.check(mod, deps=deps)[:2]
    probes = [f for f in mod.funcs
              if f.name.startswith("test_") and not f.params and f.ret == "bool"]
    assert len(probes) >= 5, f"native_concat 的探针太少: {[f.name for f in probes]}"
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "m.rs").write_text(lomentc.emit_rust(mod, ROOT, deps), encoding="utf-8")
        h = ['include!("m.rs");', "fn main() {"]
        for f in probes:
            h.append(f'    println!("{f.name} {{}}", if {f.name}() {{ 1 }} else {{ 0 }});')
        h.append("}")
        (Path(td) / "main.rs").write_text("\n".join(h) + "\n", encoding="utf-8")
        exe = Path(td) / "r.exe"
        r = subprocess.run([rustc, "-O", "-o", str(exe), str(Path(td) / "main.rs")],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-600:]
        rust_out = subprocess.run([str(exe)], capture_output=True, text=True,
                                  shell=False).stdout
        (Path(td) / "m.ll").write_text(lomentc.emit_llvm(mod, ROOT, deps), encoding="utf-8")
        c = ["#include <stdio.h>"]
        for f in probes:
            c.append(f"extern _Bool {f.name}(void);")
        c.append("int main(void) {")
        for f in probes:
            c.append(f'    printf("{f.name} %d\\n", (int){f.name}());')
        c += ["    return 0;", "}"]
        (Path(td) / "drv.c").write_text("\n".join(c) + "\n", encoding="utf-8")
        exe2 = Path(td) / "i.exe"
        r = subprocess.run([clang, "-O1", "-o", str(exe2), str(Path(td) / "drv.c"),
                            str(Path(td) / "m.ll")], capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-600:]
        ir_out = subprocess.run([str(exe2)], capture_output=True, text=True,
                                shell=False).stdout
    assert rust_out == ir_out, f"双路径输出不同:\nRUST\n{rust_out}\nIR\n{ir_out}"
    assert rust_out.count(" 1") == len(probes), f"有探针不通过:\n{rust_out}"
    print(f"      双路径一致: {len(probes)} 个探针全 1 (rustc 与 clang 输出逐行相同)")


@test
def test_str_type_errors():
    e = errs('module m\nfn f() -> u32 { return str_len(1); }\n')
    assert any("内建 str_len" in x for x in e), e
    e = errs('module m\nfn f() -> bool { return str_eq("a"); }\n')
    assert any("实参" in x for x in e), e
    e = errs('module m\nfn f() -> u32 { return str_byte("a", true); }\n')
    assert any("实参类型" in x for x in e), e


@test
def test_slice_m3():
    """M3: 只读切片 [T] + &array + slice_len + 下标。"""
    mod = lomentc.load(ROOT / "loment" / "examples" / "native_slice.lomt")
    rs = lomentc.emit_rust(mod, ROOT)
    assert "xs: &[u32]" in rs, "切片参数"
    assert "(&a)" in rs, "& 取切片"
    assert ".len() as u32" in rs, "slice_len 降级"
    ir = lomentc.emit_llvm(mod, ROOT)
    assert "{ ptr, i64 }" in ir and "insertvalue { ptr, i64 }" in ir
    assert "extractvalue { ptr, i64 }" in ir


@test
def test_slice_type_errors():
    e = errs("module m\nfn f() -> u32 { let x: u32 = 1; return slice_len(&x); }\n")
    assert any("& 只能作用于数组" in x for x in e), e
    e = errs("module m\nfn f() -> u32 { let x: u32 = 1; return slice_len(x); }\n")
    assert any("不是数组或切片" in x for x in e), e


@test
def test_mut_slice_m4():
    """M4: 可变切片 mut [T] + &mut array + 经切片写回。"""
    mod = lomentc.load(ROOT / "loment" / "examples" / "native_mut.lomt")
    rs = lomentc.emit_rust(mod, ROOT)
    assert "xs: &mut [u32]" in rs, "可变切片参数"
    assert "(&mut a)" in rs, "&mut 取切片"
    assert "xs[(i) as usize] = " in rs, "经切片写回"
    ir = lomentc.emit_llvm(mod, ROOT)
    assert "store i32" in ir and "getelementptr inbounds i32" in ir


@test
def test_mut_slice_errors():
    e = errs(
        "module m\nfn w(xs: mut [u32]) -> u32 { xs[0] = 1; return xs[0]; }\n"
        "fn f() -> u32 { let a: [u32; 2] = [0, 0]; return w(&a); }\n"
    )
    assert any("要求可变切片" in x for x in e), e
    e = errs(
        "module m\nfn r(xs: [u32]) -> u32 { xs[0] = 1; return xs[0]; }\n"
    )
    assert any("只读切片不能写" in x for x in e), e


@test
def test_m11_three_directory_project():
    """M11: 三目录工程 —— c 导入 b 导入 a, 跨目录路径解析。"""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "a").mkdir()
        (d / "b").mkdir()
        (d / "c").mkdir()
        (d / "a" / "base.lomt").write_text(
            "module base\npub const K: u32 = 3;\npub fn triple(x: u32) -> u32 { return x * K; }\n",
            encoding="utf-8")
        (d / "b" / "mid.lomt").write_text(
            'module mid\nuse "a/base.lomt"\npub fn six(x: u32) -> u32 { return triple(x) * 2; }\n',
            encoding="utf-8")
        (d / "c" / "top.lomt").write_text(
            'module top\nuse "b/mid.lomt"\nfn run(x: u32) -> u32 { return six(x); }\n',
            encoding="utf-8")
        top = lomentc.load(d / "c" / "top.lomt")
        deps = lomentc.resolve_deps(top, d, top_dir := d / "c", entry=d / "c" / "top.lomt")
        assert [x.name for x in deps] == ["base", "mid"], [x.name for x in deps]
        assert lomentc.check(top, deps=deps) == [], lomentc.check(top, deps=deps)
        rs = lomentc.emit_rust(top, d, deps)
        assert "pub fn triple(x: u32) -> u32" in rs and "pub fn six(x: u32) -> u32" in rs


@test
def test_m5_borrow_check():
    """M5: 同一次调用里不得既借又可变借 / 可变借两次。"""
    ok = 'module m\nfn f(a: [u32], b: [u32]) -> u32 { return 0; }\n' \
         'fn g() -> u32 { let a: [u32; 2] = [1, 2]; return f(&a, &a); }\n'
    assert errs(ok) == [], errs(ok)
    bad = 'module m\nfn f(a: mut [u32], b: [u32]) -> u32 { return 0; }\n' \
          'fn g() -> u32 { let a: [u32; 2] = [1, 2]; return f(&mut a, &a); }\n'
    assert any("既被可变借用又被借用" in x for x in errs(bad)), errs(bad)
    bad2 = 'module m\nfn f(a: mut [u32], b: mut [u32]) -> u32 { return 0; }\n' \
           'fn g() -> u32 { let a: [u32; 2] = [1, 2]; return f(&mut a, &mut a); }\n'
    assert any("可变借用两次" in x for x in errs(bad2)), errs(bad2)


@test
def test_m12_private_symbol_invisible():
    """M12: 导入模块未 pub 的符号不可见。"""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "lib.lomt").write_text(
            "module lib\nfn hidden() -> u32 { return 1; }\npub fn shown() -> u32 { return 2; }\n",
            encoding="utf-8")
        (d / "app.lomt").write_text(
            'module app\nuse "lib.lomt"\nfn f() -> u32 { return hidden(); }\n', encoding="utf-8")
        app = lomentc.load(d / "app.lomt")
        deps = lomentc.resolve_deps(app, d, d, entry=d / "app.lomt")
        e = lomentc.check(app, deps=deps)
        assert any("未定义的函数 hidden" in x for x in e), e
        (d / "app2.lomt").write_text(
            'module app2\nuse "lib.lomt"\nfn f() -> u32 { return shown(); }\n', encoding="utf-8")
        app2 = lomentc.load(d / "app2.lomt")
        deps2 = lomentc.resolve_deps(app2, d, d, entry=d / "app2.lomt")
        assert lomentc.check(app2, deps=deps2) == [], lomentc.check(app2, deps=deps2)


@test
def test_m13_move_semantics():
    """M13: 非 Copy 类型赋值/传参 = 移动; 移动后再用报错; Copy 类型放行。"""
    e = errs('module m\nfn f() -> u32 { let a: [u32; 2] = [1, 2]; let b: [u32; 2] = a; return a[0]; }\n')
    assert any("已被移动" in x for x in e), e
    e = errs("module m\nfn f() -> u32 { let a: [u32; 2] = [1, 2]; let b: [u32; 2] = a; return b[0]; }\n")
    assert e == [], e
    e = errs("module m\nfn f() -> u32 { let x: u32 = 1; let y: u32 = x; return x + y; }\n")
    assert e == [], e


@test
def test_m14_no_alloc_audit():
    """M14: 当前语言按构造即 no-alloc, 分配点恒为空。"""
    assert lomentc.alloc_audit(lomentc.load(ROOT / "loment" / "examples" / "native.lomt")) == []
    assert lomentc.alloc_audit(lomentc.load(DEMO)) == []


MEM = ROOT / "loment" / "examples" / "native_mem.lomt"
RAII = ROOT / "loment" / "examples" / "native_raii.lomt"


@test
def test_m15_alloc_both_backends():
    mod = lomentc.load(MEM)
    rs = lomentc.emit_rust(mod, ROOT)
    assert "__loment_alloc" in rs and "__loment_store8" in rs
    ir = lomentc.emit_llvm(mod, ROOT)
    assert "@__loment_heap = internal global [65536 x i8]" in ir
    assert "getelementptr [65536 x i8]" in ir


@test
def test_m18_wrap_and_divzero():
    """M18: 算术回绕 (Rust 路径需 -O / -C overflow-checks=off); 除零 trap。"""
    ir = lomentc.emit_llvm(lomentc.load(MEM), ROOT)
    assert "add i32" in ir, "IR 原生回绕"
    assert "icmp eq i32" in ir and "call void @__loment_abort()" in ir, "除零 trap"
    rs = lomentc.emit_rust(lomentc.load(MEM), ROOT)
    assert "a + b" in rs


@test
def test_m19_panic():
    rs = lomentc.emit_rust(parse("module m\nfn f(x: u32) -> u32 { return panic(x); }\n"), ROOT)
    assert "panic!(" in rs
    ir = lomentc.emit_llvm(parse("module m\nfn f(x: u32) -> u32 { return panic(x); }\n"), ROOT)
    assert "call void @__loment_abort()" in ir


@test
def test_m20_ports_rust_only():
    rs = lomentc.emit_rust(lomentc.load(RAII), ROOT)
    assert 'asm!("in al, dx"' in rs and 'asm!("out dx, al"' in rs
    try:
        lomentc.emit_llvm(lomentc.load(RAII), ROOT)
    except lomc.LomError as ex:
        assert "inb" in ex.msg or "outb" in ex.msg, ex.msg
    else:
        raise AssertionError("IR 后端应明确拒绝 inb/outb")


@test
def test_m21_atomics():
    rs = lomentc.emit_rust(lomentc.load(MEM), ROOT)
    assert "fetch_add(" in rs and "Ordering::SeqCst" in rs
    ir = lomentc.emit_llvm(lomentc.load(MEM), ROOT)
    assert "atomicrmw add ptr" in ir and "seq_cst" in ir


@test
def test_m16_drop_impl():
    rs = lomentc.emit_rust(lomentc.load(RAII), ROOT)
    assert "impl Drop for Guard {" in rs and "fn drop(&mut self) {" in rs
    assert "#[derive(Clone, Copy)]\npub struct Guard" not in rs, "有析构的类型不得 derive Copy"


@test
def test_m17_dangling_borrow_rejected():
    e = errs("module m\nfn f() -> [u32] { let a: [u32; 2] = [1, 2]; return &a; }\n")
    assert any("悬垂" in x for x in e), e


@test
def test_cast_operator():
    rs = lomentc.emit_rust(parse("module m\nfn f(x: u32) -> u8 { return x as u8; }\n"), ROOT)
    assert "(x) as u8" in rs
    ir = lomentc.emit_llvm(parse("module m\nfn f(x: u32) -> u8 { return x as u8; }\n"), ROOT)
    assert "trunc i32" in ir
    e = errs("module m\nfn f(x: str) -> u8 { return x as u8; }\n")
    assert any("as 只能作用于整型" in x for x in e), e


@test
def test_m22_bitfields():
    """M22: 位域操作内建 (get_bits/set_bits) 双后端。"""
    mod = lomentc.load(ROOT / "loment" / "examples" / "native_bits.lomt")
    rs = lomentc.emit_rust(mod, ROOT)
    assert "1u16 << (" in rs, "掩码构造"
    ir = lomentc.emit_llvm(mod, ROOT)
    assert "lshr i8" in ir and "shl i8" in ir and "xor i8" in ir
    e = errs('module m\nfn f() -> u8 { return get_bits(1, 0); }\n')
    assert any("实参" in x for x in e), e


@test
def test_m31_freestanding_runtime():
    """M31: 自带运行时, 不依赖 libc。"""
    ir = lomentc.emit_llvm(lomentc.load(MEM), ROOT)
    assert "define internal void @__loment_abort()" in ir
    assert "declare void @abort()" not in ir, "不得再依赖 libc abort"
    ir2 = lomentc.emit_llvm(lomentc.load(ROOT / "loment" / "examples" / "native_str.lomt"), ROOT)
    assert "define internal i32 @__loment_memcmp" in ir2
    assert "declare i32 @memcmp" not in ir2, "不得再依赖 libc memcmp"


@test
def test_m33_interrupt_calling_convention():
    """M33: interrupt fn -> x86_intrcc。"""
    ir = lomentc.emit_llvm(lomentc.load(ROOT / "loment" / "examples" / "native_entry.lomt"), ROOT)
    assert "define x86_intrcc void @timer_isr" in ir
    assert "byval" in ir, "x86_intrcc 帧指针需 byval"


CAP = ROOT / "loment" / "examples" / "native_cap.lomt"


@test
def test_p4_domain_table_and_audit():
    """M35/M38: 域描述表 + 审计钩子。"""
    mod = lomentc.load(CAP)
    rs = lomentc.emit_rust(mod, ROOT)
    assert "pub static CAP_DOMAINS: &[CapDomain]" in rs
    assert 'space: "disk", lo: 0, hi: 4, revocable: true' in rs
    assert "__LOMENT_AUDIT" in rs and "fn __loment_guard(" in rs
    ir = lomentc.emit_llvm(mod, ROOT)
    assert "@__loment_caps = internal constant [1 x { i64, i64, i64, i64 }]" in ir
    assert "@__loment_audit = internal global [16 x i64]" in ir


@test
def test_p4_guard_bounds():
    """M36: 字面量越界编译期拒绝; 域内放行。"""
    ok = "module m\ncapability c : disk[0..4]\nfn f() -> u32 { guard c(2); return 0; }\n"
    assert errs(ok) == [], errs(ok)
    bad = "module m\ncapability c : disk[0..4]\nfn f() -> u32 { guard c(9); return 0; }\n"
    assert any("越界" in x for x in errs(bad)), errs(bad)
    e = errs("module m\nfn f() -> u32 { guard nope(0); return 0; }\n")
    assert any("未声明的能力" in x for x in e), e


@test
def test_m41_excluded_space_enforced():
    src = ('module m\nexcluded "network: 不申请网络"\n'
           'capability net : network[0..1]\nfn f() -> u32 { return 0; }\n')
    assert any("excluded 的空间" in x for x in errs(src)), errs(src)
    ok = ('module m\nexcluded "network: 不申请网络"\n'
          'capability disk : disk[0..1]\nfn f() -> u32 { return 0; }\n')
    assert errs(ok) == [], errs(ok)


@test
def test_m43_capability_fuzz():
    """M43: 随机 (lo, hi, idx) —— 编译期判定必须与域语义一致。"""
    import random

    rnd = random.Random(20260908)  # 固定种子: 可复现的模糊测试 (非加密用途, 故意确定)
    for _ in range(120):
        lo = rnd.randint(0, 8)
        hi = lo + rnd.randint(0, 8)
        idx = rnd.randint(0, 20)
        src = (f"module m\ncapability c : disk[{lo}..{hi}]\n"
               f"fn f() -> u32 {{ guard c({idx}); return 0; }}\n")
        got = errs(src)
        should_reject = not (lo <= idx <= hi)
        assert (len(got) > 0) == should_reject, (lo, hi, idx, got)


def _potato(name: str) -> dict:
    p = ROOT / "loment" / "examples" / f"{name}.lomt"
    mod = lomentc.load(p)
    deps = lomentc.resolve_deps(mod, ROOT, p.parent, entry=p)
    return json.loads(lomentc.emit_potato(mod, ROOT, deps))


@test
def test_m45_form_object_v1_covers_slices_strings_generics():
    """M45: v1 必须导出切片/字符串/泛型/实例/trait/impl, 且全部通过独立校验。"""
    sl = _potato("native_slice")
    assert any(p["type"] == "[u32]" for f in sl["functions"] for p in f["params"]), sl["functions"]
    assert potato.validate(sl) == []
    st = json.loads(lomentc.emit_potato(
        parse('module s\nfn len_of(s: str) -> u32 { return str_len(s); }\n'), ROOT))
    assert any(p["type"] == "str" for f in st["functions"] for p in f["params"])
    assert potato.validate(st) == []
    gen = _potato("native_gen")
    assert {"kind": "fn", "name": "max", "params": ["T"]} in gen["generics"], gen["generics"]
    assert any(i["name"] == "max_u32" and i["of"] == "max" and i["args"] == ["u32"]
               for i in gen["instances"]), gen["instances"]
    assert potato.validate(gen) == []
    tr = _potato("native_trait")
    assert tr["traits"] == [{"name": "Measurable", "methods": ["measure"]}], tr["traits"]
    assert {i["for"] for i in tr["impls"]} == {"Small", "Big"}
    assert all(i["methods"] == ["measure"] for i in tr["impls"]), tr["impls"]
    assert potato.validate(tr) == []
    raii = _potato("native_raii")
    assert raii["impls"] and raii["impls"][0]["trait"] == "Drop"
    assert potato.validate(raii) == []
    entry = _potato("native_entry")
    assert any(f["ret"] == "()" for f in entry["functions"]), entry["functions"]
    assert potato.validate(entry) == []


@test
def test_m45_every_example_exports_valid_v1():
    ex = ROOT / "loment" / "examples"
    names = sorted(p.stem for p in ex.glob("*.lomt"))
    assert len(names) >= 15, names
    for n in names:
        doc = _potato(n)
        assert doc["potato"] == "v1", n
        assert potato.validate(doc) == [], (n, potato.validate(doc))


@test
def test_m46_compiler_self_check_is_mandatory():
    """M46: 形式对象校验失败 => 编译失败 (不可绕过)。"""
    orig = potato.validate
    potato.validate = lambda doc: ["注入错误"]  # noqa: ARG005
    try:
        try:
            lomentc.emit_potato(lomentc.load(DEMO), ROOT)
            assert False, "应当抛 LomError"
        except lomc.LomError as e:
            assert "形式对象自检失败" in str(e), e
    finally:
        potato.validate = orig


@test
def test_m46_cli_requires_potato_with_codegen():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "x.rs"
        assert lomentc.main([str(DEMO), "--emit-rust", str(out)]) == 2
        assert not out.exists()
        obj = Path(d) / "x.json"
        assert lomentc.main([str(DEMO), "--emit-rust", str(out),
                             "--emit-potato", str(obj)]) == 0
        assert out.exists() and obj.exists()
        assert potato.validate(json.loads(obj.read_text(encoding="utf-8"))) == []


def main() -> int:
    failed = []
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, e))
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\nlomentc_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
