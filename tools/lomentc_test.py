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
def test_demo_parses_and_checks():
    mod = lomentc.load(DEMO)
    deps = lomentc.resolve_deps(mod, ROOT, DEMO.parent, entry=DEMO)
    assert lomentc.check(mod, deps=deps) == [], lomentc.check(mod, deps=deps)
    assert [f.name for f in mod.funcs] == [
        "fib", "gcd", "popcount", "in_domain", "blk_end", "mask_low", "has_flag",
        "sum_array", "fill_incr", "color_code", "sum_range", "quadruple", "max_blocks",
        "shape_area",
    ]
    assert mod.imports == ["loment/examples/mathutil.lomt"]
    assert [c.name for c in mod.caps] == ["blk_write"]
    assert [s.name for s in mod.structs] == ["Blk"]
    assert [e.name for e in mod.enums] == ["Color", "Shape"]
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
    assert doc["potato"] == "v0" and doc["unit"] == "demo" and doc["language"] == "loment"
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
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            assert lomentc.main([str(DEMO), "--emit-rust", str(out)]) == 0
            assert lomentc.main([str(DEMO), "--emit-rust", str(out), "--check"]) == 0
            out.write_text("// drifted\n", encoding="utf-8")
            assert lomentc.main([str(DEMO), "--emit-rust", str(out), "--check"]) == 1


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
def test_llvm_rejects_aggregate_signature():
    """聚合参数/返回值暂不支持 (ABI 未定), 必须明确报错而非静默错编。"""
    try:
        lomentc.emit_llvm(
            parse("module m\nstruct S { a: u32 }\nfn f(s: S) -> u32 { return s.a; }\n"), ROOT
        )
    except lomc.LomError as ex:
        assert "聚合参数" in ex.msg, ex.msg
    else:
        raise AssertionError("聚合参数应被拒绝")


@test
def test_llvm_check_mode():
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "n.ll"
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            assert lomentc.main([str(NATIVE), "--emit-llvm", str(out)]) == 0
            assert lomentc.main([str(NATIVE), "--emit-llvm", str(out), "--check"]) == 0
            out.write_text("; drift\n", encoding="utf-8")
            assert lomentc.main([str(NATIVE), "--emit-llvm", str(out), "--check"]) == 1


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
    assert "call i32 @memcmp" in ir, "str_eq 降级"
    assert "zext i8" in ir, "str_byte 降级"


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
