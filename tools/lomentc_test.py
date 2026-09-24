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
    assert doc["potato"] == potato.VERSIONS[-1] and doc["unit"] == "demo" and doc["language"] == "loment"
    # v2 的新字段 (docs/143 §3.2): 没写 `choose` 就是默认 `std` —— 对象里永远显式。
    assert doc["mode"] == "std", doc.get("mode")
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
def test_name_import_reaches_a_module_inside_a_package():
    """名字形式要能命中**包内模块**, 不只是与包同名那个入口 (2026-09-20)。

    包是一个目录, 里面除了入口还有别的模块 (`std/vec.lomt`、`host/fs.lomt`)。只认入口
    的话, 整包只能被"拖进同一个单元"那一种方式消费 —— 而单元的发射符号是**平的**, 模块
    一多就撞名, 且代价按模块数超线性涨。这两件事一起把 std 挡在门外。
    """
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        pkg = proj / "deps" / "geom"
        pkg.mkdir(parents=True)
        (pkg / "geom.lomt").write_text("module geom\nuse \"area.lomt\"\n"
                                       "pub fn g_area(w: u32, h: u32) -> u32 { return ar(w, h); }\n",
                                       encoding="utf-8")
        (pkg / "area.lomt").write_text("module area\npub fn ar(w: u32, h: u32) -> u32 "
                                       "{ return w * h; }\n", encoding="utf-8")
        assert lomentc.resolve_name("area", ROOT, proj) == pkg / "area.lomt"
        # 入口那条路没被新层挤掉: `use geom` 仍然命中 `<包>/<包>.lomt`
        assert lomentc.resolve_name("geom", ROOT, proj) == pkg / "geom.lomt"
        mod = parse("module m\nuse area\nfn f() -> u32 { return ar(3, 4); }\n")
        deps = lomentc.resolve_deps(mod, ROOT, proj, entry=proj / "x.lomt")
        assert [x.name for x in deps] == ["area"]


@test
def test_name_import_package_module_ambiguous_is_rejected():
    """两个包里都有同名模块 -> **报错, 不许取先搜到的那个**。与第 3 层同一条理由:
    搜索顺序一旦变成隐藏语义, 换台机器就换了个模块。"""
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        for name in ("geom", "extra"):
            d = proj / "deps" / name
            d.mkdir(parents=True)
            (d / "area.lomt").write_text(f"module area\npub fn ar() -> u32 {{ return {len(name)}; }}\n",
                                        encoding="utf-8")
        try:
            lomentc.resolve_name("area", ROOT, proj)
        except lomc.LomError as ex:
            assert "名字导入有歧义" in ex.msg and "geom" in ex.msg and "extra" in ex.msg, ex.msg
        else:
            raise AssertionError("包内模块重名应报歧义")


@test
def test_store_modules_resolve_by_name_in_this_checkout():
    """**本仓也是一个 store 消费者**: `lompi/store` 就在仓里, 装出来的前缀才是
    `share/lompi/store`。没有 `<仓根>/lompi/store` 这一条, 在本仓写 `use vec` 一律 E018 ——
    而"本仓能不能用 std"正是它要回答的问题。"""
    store = ROOT / "lompi" / "store"
    assert store.is_dir(), f"仓里没有 {store.relative_to(ROOT)}"
    assert lomentc.resolve_name("vec", ROOT) == store / "std" / "0.1.0" / "vec.lomt"
    assert lomentc.resolve_name("std", ROOT) == store / "std" / "0.1.0" / "std.lomt"
    assert lomentc.resolve_name("fs", ROOT) == store / "host" / "0.1.0" / "fs.lomt"
    assert lomentc.store_roots(ROOT, None) == [store]


@test
def test_store_packages_do_not_shadow_the_built_in_roots():
    """商店里的**包内模块**不许盖住编译器自己的源码。

    这不是假想的: 加包内模块那一层时真撞上了 —— `store/std/0.1.0/interp.lomt` 把
    `loment/selfhost/interp.lomt` 顶掉, 驱动当场编不过 (`CT_HEAP_BYTES` 未解析)。
    四根是编译器自己的源码, 商店里的包是随包发行的库; 后者是**私有名字空间**, 排序上
    必须让位。项目本地 `deps/` 不在此列 —— 那是使用者显式装的, 先于工具链的一切。
    """
    assert lomentc.resolve_name("interp", ROOT) == ROOT / "loment" / "selfhost" / "interp.lomt"
    assert lomentc.resolve_name("mem", ROOT) == ROOT / "loment" / "lib" / "mem.lomt"
    # 商店里确实**存在**同名的包内模块 —— 否则这条判据是空的 (它在测空气)
    assert (ROOT / "lompi" / "store" / "std" / "0.1.0" / "interp.lomt").exists(), \
        "商店里没有 interp.lomt, 这条判据就测不到遮蔽"
    assert (ROOT / "lompi" / "store" / "std" / "0.1.0" / "mem.lomt").exists()


@test
def test_std_package_is_one_unit_without_name_collisions():
    """v. std 的**整包门面必须是一份能装进一个单元**的源码。

    单元的发射符号是平的 (`docs/158` §2), 所以包内任意两个模块的同名顶层声明都是硬错。
    这条把"std 现在装得下"钉住 —— 它同时是给后来人看的棘轮: 往 std 里加模块时撞名,
    门禁当场红, 而不是等到某个用户 `use std` 才发现。

    **只在参考实现这一侧跑**: 自举侧装载同一份语料要按模块数超线性涨 (实测 n=64 已 187 秒,
    128 个模块是几十分钟量级), 拿它当判据会把门禁变成等待。两个实现对"重名要拒"这条**规则**
    的等价性由 `loment_p8_test` 的 `neg_across` 与 `loment_rule_parity` 管; 这里管的是**语料**。
    """
    mod, deps = lomentc.load_unit(ROOT / "lompi" / "store" / "std" / "0.1.0" / "std.lomt", ROOT)
    seen: dict[str, str] = {}
    dups: list[str] = []
    for m in [*deps, mod]:
        decls = ([(f.name, "函数") for f in m.funcs] + [(s.name, "结构体") for s in m.structs]
                 + [(e.name, "枚举") for e in m.enums if not e.from_prelude]
                 + [(c.name, "常量") for c in m.consts])
        for nm, kind in decls:
            if nm in seen and seen[nm] != m.name:
                dups.append(f"{kind} {nm}: {seen[nm]} <-> {m.name}")
            else:
                seen.setdefault(nm, m.name)
    assert not dups, "std 门面装不进一个单元 —— 平名字撞了:\n  " + "\n  ".join(dups)
    assert len(deps) + 1 > 100, f"std 只装进来 {len(deps)+1} 个模块, 门面是不是被改小了"


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
def test_m45_every_example_exports_a_valid_object():
    ex = ROOT / "loment" / "examples"
    names = sorted(p.stem for p in ex.glob("*.lomt"))
    assert len(names) >= 15, names
    for n in names:
        doc = _potato(n)
        assert doc["potato"] == potato.VERSIONS[-1], n
        assert doc["mode"] in potato.MODES, (n, doc.get("mode"))
        assert potato.validate(doc) == [], (n, potato.validate(doc))


@test
def test_switches_elide_code_and_land_in_potato():
    """开关（`docs/182` §1）: **真的驱动代码裁减**, 且取值进 Potato。

    这是把 `choose` 从"承诺"变成"发明"的那一步。在此之前 `mode` 被校验、进对象、被回放,
    **没有任何一行按它分支**（`loment_build.py` 零命中、`lomelf.py` 里的 `mode` 是 POSIX 的）。
    开关的第一个真消费方就是**代码裁减**, 它不依赖任何跨线契约。

    钉七条（`docs/182` §1.8）:
      * 开着: 体摊到顶层, 里面的东西**存在**;
      * 关着: 体**连 token 都不进 parser** —— 体内的语义错**不报**（这才是"关掉 = 不依赖"）;
        反过来, 引用体内才有的东西就成了未定义;
      * 括号不配对**照报**（体可以不解析, 但结构不能塌）;
      * 未定义的开关 / 同名两次 / 超上限 各有错;
      * 取值进 Potato 且**按名字排序**（确定性是判据）。
    """
    def chk(src):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "m.lomt"
            p.write_text(src, encoding="utf-8", newline="\n")
            try:
                mod = lomentc.load(p)
            except lomc.LomError as e:
                # **开关在词法流上落定**（docs/182 §2），所以"块没闭合"是**装载期**的错,
                # 抛出来而不是进 check() 的 errs —— 与解析器其它结构错一致。
                return [e.msg], None
            return lomentc.check(mod, deps=[]), json.loads(lomentc.emit_potato(mod, ROOT))

    BODY = ("set choose feat {\n"
            "    fn helper() -> u32 {\n        return 7;\n    }\n}\n")
    CALL = "fn f() -> u32 {\n    return helper();\n}\n"
    # 开着: helper 存在
    errs, doc = chk("module m\n\n" + BODY + "\nchoose feat\n\n" + CALL)
    assert not errs, errs
    assert doc["potato"] == potato.VERSIONS[-1] and doc["switches"] == [{"name": "feat", "on": True}], doc
    # 关着: 体整个不在了 —— 引用 helper 反而成了未定义
    errs, doc = chk("module m\n\n" + BODY + "\nchoose close feat\n\n" + CALL)
    assert errs and "helper" in errs[0], errs
    # 关着: 体内的**语义错不报**（体没进 parser）
    errs, doc = chk("module m\n\nset choose feat {\n    fn helper() -> u32 {\n"
                    "        return undefined_thing;\n    }\n}\n\nchoose close feat\n\n"
                    "fn f() -> u32 {\n    return 1;\n}\n")
    assert not errs, errs
    assert doc["switches"] == [{"name": "feat", "on": False}], doc["switches"]
    # 括号不配对: 照报
    errs, _ = chk("module m\n\nset choose feat {\n    fn h() -> u32 {\n        return 1;\n"
                  "}\n\nfn f() -> u32 {\n    return 1;\n}\n")
    assert errs and "没闭合" in errs[0], errs
    # 未定义的开关
    errs, _ = chk("module m\n\nchoose nope\n\nfn f() -> u32 {\n    return 1;\n}\n")
    assert errs and "未定义的开关" in errs[0], errs
    # 同名两次
    errs, _ = chk("module m\n\n" + BODY + "\nchoose feat\nchoose close feat\n\n"
                  "fn f() -> u32 {\n    return 1;\n}\n")
    assert errs and "写了两次" in errs[0], errs
    # 上限: 500 条能过, 501 条报错（**绝不静默丢**）
    def many(n):
        return ("module m\n\n" + "".join("set choose s%d {}\n" % i for i in range(n))
                + "\nfn f() -> u32 {\n    return 1;\n}\n")
    errs, doc = chk(many(lomentc.MAX_CHOOSE))
    assert not errs, errs[:2]
    assert len(doc["switches"]) == lomentc.MAX_CHOOSE
    errs, _ = chk(many(lomentc.MAX_CHOOSE + 1))
    assert errs and "超过上限" in errs[0], errs
    print("      开关: 裁减/不裁减/potato 排序/上限 %d 都对" % lomentc.MAX_CHOOSE)


@test
def test_addin_carries_switches_across_units():
    """`addin <名字>` + `chooseset.lomt` + **装载器预扫**（`docs/182` §1.4/§1.7，C2）。

    **这条判据就是预扫存在的全部理由**（`docs/182` §1.6 ② 那个鸡生蛋）：根单元的
    `choose verbose` 要按名字找到 `set choose verbose` 的定义，而那份定义在 `addin`
    进来的、**还没读到**的单元里 —— 状态必须在装载**之前**定，可定义又在装载**之后**才
    出现。预扫失效时（表定不下来），**这条是唯一会红的**。

    "跨单元"是字面意思（§1.7）：`addin` 拉进来的单元**本身参与编译**，所以体里的函数
    要给根用就得 `pub` —— 它仍然是一个模块。
    """
    CS = ("module chooseset\n\naddin chooseset\n\n"
          "set choose verbose {\n"
          "    pub fn banner() -> u32 {\n        return 0x5EED;\n    }\n}\n")
    ROOT_SRC = ("module main\n\naddin chooseset\n\n%s\n"
                "fn _start() {\n    syscall4(60, banner() as u64, 0, 0);\n}\n")

    def build(value: str):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "chooseset.lomt").write_text(CS, encoding="utf-8", newline="\n")
            p = d / "main.lomt"
            p.write_text(ROOT_SRC % value, encoding="utf-8", newline="\n")
            try:
                mod, deps = lomentc.load_unit(p, ROOT)
            except lomc.LomError as e:
                return [e.msg], "", None
            errs = lomentc.check(mod, deps=deps)
            ir = "" if errs else lomentc.emit_llvm(mod, ROOT, deps)
            return errs, ir, json.loads(lomentc.emit_potato(mod, ROOT, deps))

    # 开着: `banner` 真的进了单元（预扫读到了 addin 单元里的定义）
    errs, ir, doc = build("choose verbose")
    assert not errs, errs
    assert "define i32 @banner()" in ir, ir[:300]
    assert doc["switches"] == [{"name": "verbose", "on": True}], doc["switches"]
    # 关着: 体**整段不在** —— 引用它反而成了未定义。"关掉 = 不依赖"字面成立。
    errs, ir, doc = build("choose close verbose")
    assert doc["switches"] == [{"name": "verbose", "on": False}], doc["switches"]
    assert "define i32 @banner" not in ir, ir[:300]
    assert any("未定义的函数 banner" in e for e in errs), errs
    print("      addin: 根 choose 读得到 addin 单元里的定义；关着时体整段不在")


@test
def test_addin_and_chooseset_rules():
    """C2 的四条拒绝规则（`docs/182` §1.4/§1.10）—— 每条都要能证伪。"""
    OK_CS = "module chooseset\n\naddin chooseset\n\nset choose feat {\n}\n"

    def two(root_src: str, cs_src: str, name: str = "chooseset"):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / f"{name}.lomt").write_text(cs_src, encoding="utf-8", newline="\n")
            p = d / "main.lomt"
            p.write_text(root_src, encoding="utf-8", newline="\n")
            try:
                mod, deps = lomentc.load_unit(p, ROOT)
            except lomc.LomError as e:
                return [e.msg]
            return lomentc.check(mod, deps=deps)

    # ① addin 目标里写了非 choose 代码 -> 拒。**两个方向都要堵**: `use` 那条禁 choose,
    #    这条路就得禁其余的一切, 否则"装代码"与"装开关"就又混成一件事了。
    errs = two("module main\n\naddin chooseset\n",
               OK_CS.replace("set choose feat {\n}",
                             "fn helper() -> u32 {\n    return 1;\n}"))
    assert errs and "只能写 choose 相关代码" in errs[0], errs

    # ② **库里的 `addin`** -> 拒。它写了也**不会生效**（预扫只走"根 + 根 addin 到的单元"
    #    那张图）—— 静默失效正是本仓反复要消灭的那种东西, 所以报出来。
    errs = two('module main\n\nuse "lib.lomt"\n\nfn _start() {\n}\n',
               "module lib\n\naddin chooseset\n\nfn helper() -> u32 {\n    return 1;\n}\n",
               name="lib")
    assert errs and "库不许 `addin`" in errs[0], errs

    # ③ 开关声明**嵌在另一个开关体里** -> 拒。放开它就把"状态"变成求不动点
    #    （`B` 算不算数取决于 `A` 开没开, 而 `A` 的状态正是预扫要算的东西）。
    errs = two("module main\n\nset choose a {\n    set choose b {\n    }\n}\n\n"
               "fn _start() {\n}\n", OK_CS)
    assert errs and "不许写在另一个开关体里" in errs[0], errs

    # ④ `addin` 递归超深度 -> **报错, 不静默丢**（与 `MAX_USE` 同一条纪律）
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        n = lomentc.MAXDEPTH + 1
        for i in range(n):
            nxt = f"addin a{i + 1}\n" if i + 1 < n else ""
            (d / f"a{i}.lomt").write_text(f"module a{i}\n\n{nxt}", encoding="utf-8",
                                          newline="\n")
        p = d / "main.lomt"
        p.write_text("module main\n\naddin a0\n\nfn _start() {\n}\n",
                     encoding="utf-8", newline="\n")
        try:
            lomentc.load_unit(p, ROOT)
            raise AssertionError(f"{n} 层 addin 应当报错, 却过了")
        except lomc.LomError as e:
            assert "嵌套超过" in e.msg, e.msg
    print("      addin/chooseset: 白名单/库不许 addin/嵌套拒绝/深度上限 四条都对")


@test
def test_mode_follows_choose_and_defaults_to_std():
    """`mode` 就是根单元 `choose` 的那一个值, 不写则 `std` (docs/143 §3.2 / docs/175 §8)。

    这是 docs/175 §8 那条判据的**正向**一半: 形式对象里的 mode 必须跟着源码走。
    反向那一半 (删掉字段 -> 校验器必须红) 在 `potato_test` 的 v2/v3 负例里。
    """
    src = "module m\n\nchoose no_std\n\nfn f() -> u32 {\n    return 1;\n}\n"
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "m.lomt"
        p.write_text(src, encoding="utf-8", newline="\n")
        doc = json.loads(lomentc.emit_potato(lomentc.load(p), ROOT))
        assert doc["potato"] == potato.VERSIONS[-1] and doc["mode"] == "no_std", doc.get("mode")
    # 不写 choose -> std
    src2 = "module m\n\nfn f() -> u32 {\n    return 1;\n}\n"
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "m.lomt"
        p.write_text(src2, encoding="utf-8", newline="\n")
        doc2 = json.loads(lomentc.emit_potato(lomentc.load(p), ROOT))
        assert doc2["mode"] == "std", doc2.get("mode")


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


# ---------------------------------------------------------------- 名字形式的搜索层 (2026-09-16)
#
# 触发原因: 装在用户机器上 (没有仓库) 时 `use std` 找不到任何东西 —— 名字形式原先只认四个
# **仓库相对**的根。现在按层搜: 项目本地 deps/ -> 工具链自带的库 -> 内置四根 (只它要求唯一)。
# 规范在 docs/143, 冻结面记录在 docs/158 §2/§5。

def _pkg(d: Path, name: str) -> None:
    """写一个最小可编译的包目录 `<d>/{<name>.lomt, area.lomt}` (入口与它的伴生模块)。"""
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.lomt").write_bytes(
        (f"module {name}\n\nuse \"area.lomt\"\n\npub fn g_area(w: u32, h: u32) -> u32 {{\n"
         f"    return ar(w, h);\n}}\n").encode())
    (d / "area.lomt").write_bytes(
        b"module area\n\npub fn ar(w: u32, h: u32) -> u32 {\n    return w * h;\n}\n")


def _entry(p: Path, name: str) -> Path:
    p.write_bytes((f"module hi\n\nuse {name}\n\nfn main() -> u32 {{\n"
                   f"    return g_area(3 as u32, 4 as u32);\n}}\n").encode())
    return p


@test
def test_name_form_searches_project_deps_first():
    """`<项目根>/deps/<名字>/<名字>.lomt` 命中即用, 包内的路径形式按**被导入文件所在目录**解析。

    后半句才是关键: `deps/geom/geom.lomt` 里的 `use "area.lomt"` 只相对 `deps/geom/` 成立
    —— 只按 CWD 找会去够 `<CWD>/area.lomt` (一个不存在的路径)。CWD 故意是别的目录。
    """
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "proj"
        _pkg(proj / "deps" / "geom", "geom")
        hi = _entry(proj / "hi.lomt", "geom")
        mod = lomentc.load(hi)
        deps = lomentc.resolve_deps(mod, ROOT, proj, entry=hi)
        names = [d.name for d in deps]
        assert names == ["area", "geom"], f"依赖序不对: {names}"
        ir = lomentc.emit_llvm(mod, ROOT, deps)
        assert "define i32 @ar(" in ir and "define i32 @g_area(" in ir, ir[:300]


@test
def test_name_form_falls_through_to_the_toolchain_store():
    """项目里没有 deps/ 时落到**工具链自带的库**: `<工具目录>/../share/lompi/store/`。

    这就是"装完 Loment 就能 `use std`"那一层 —— 用户 2026-09-16 定: 像 python/java 那样。
    """
    with tempfile.TemporaryDirectory() as td:
        tool = Path(td) / "tool"
        (tool / "bin").mkdir(parents=True)
        _pkg(tool / "share" / "lompi" / "store" / "geom" / "0.1.0", "geom")
        proj = Path(td) / "proj"
        proj.mkdir()
        got = lomentc.resolve_name("geom", ROOT, proj, tool / "bin")
        assert got == tool / "share" / "lompi" / "store" / "geom" / "0.1.0" / "geom.lomt", got


@test
def test_store_with_two_versions_is_an_error():
    """自带的库里同名多版本 = 报错, 不猜 —— 编译器不做版本选择 (docs/168 §4.2)。"""
    with tempfile.TemporaryDirectory() as td:
        tool = Path(td) / "tool"
        (tool / "bin").mkdir(parents=True)
        for v in ("0.1.0", "0.2.0"):
            _pkg(tool / "share" / "lompi" / "store" / "geom" / v, "geom")
        proj = Path(td) / "proj"
        proj.mkdir()
        try:
            lomentc.resolve_name("geom", ROOT, proj, tool / "bin")
        except lomentc.LomError as e:
            assert "版本" in str(e), e
        else:
            raise AssertionError("多版本应当报错")


@test
def test_project_local_deps_wins_over_a_builtin_root():
    """`deps/` 里有 `bytes`、仓库内置根里也有 —— 前两层先命中先用, **不报歧义**。

    唯一性只管第③层 (内置四根): 它抓的是"作者把名字写重了"; 使用者选哪一份是**优先级**的
    事, 像 PYTHONPATH。两件事混进一个错误, 使用者不知道该改什么。
    """
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "proj"
        _pkg(proj / "deps" / "bytes", "bytes")
        got = lomentc.resolve_name("bytes", ROOT, proj, None)
        assert got == proj / "deps" / "bytes" / "bytes.lomt", got
        assert hasattr(lomentc, "NAME_ROOTS") and len(lomentc.NAME_ROOTS) == 4


@test
def test_use_count_over_the_limit_is_an_error():
    """单文件 use 超过 MAX_USE **报错**, 不静默丢 —— 自举镜原先第 9 条起直接不要, 而参考
    实现照收, 两个实现于是对同一份源码给出不同的单元。"""
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        d = proj / "deps" / "many"
        d.mkdir(parents=True)
        for i in range(lomentc.MAX_USE + 1):
            (d / f"m{i}.lomt").write_bytes(
                f"module m{i}\n\npub fn f{i}() -> u32 {{\n    return {i} as u32;\n}}\n".encode())
        uses = "\n".join(f'use "m{i}.lomt"' for i in range(lomentc.MAX_USE + 1))
        (d / "many.lomt").write_bytes(f"module many\n\n{uses}\n".encode())
        hi = Path(td) / "hi.lomt"
        hi.write_bytes(b"module hi\n\nuse many\n\nfn main() -> u32 {\n    return 0;\n}\n")
        mod = lomentc.load(hi)
        try:
            lomentc.resolve_deps(mod, ROOT, proj, entry=hi)
        except lomentc.LomError as e:
            assert str(lomentc.MAX_USE) in str(e), e
        else:
            raise AssertionError(f"{lomentc.MAX_USE + 1} 条 use 应当报错")


# ---------------------------------------------------------------- 自定义后缀 (2026-09-16)
# 后缀**不属于语言** (docs/143 §2.3): 名字形式找的是"哪个名字", 后缀是实现细节, 由项目自己
# 那份 `loment.conf` 定。`use "x.foo"` 这种路径形式本来就自带后缀, 不受配置影响。

@test
def test_project_conf_sets_the_name_form_suffix():
    """项目根那份 `loment.conf` 把名字形式的后缀从 `.lomt` 换成 `.foo`。

    两半都要对: ① 名字形式 `use geom` 落到 `<项目根>/deps/geom/geom.foo`; ② 进了包以后,
    包内的**路径形式** `use "area.foo"` 仍按被导入文件所在目录解析 —— 配置只管名字形式,
    别把路径形式也一起改了 (那是两套规则, 混一起就没人能预期)。
    """
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "proj"
        d = proj / "deps" / "geom"
        d.mkdir(parents=True)
        (proj / "loment.conf").write_bytes(
            b'// \xe9\xa1\xb9\xe7\x9b\xae\xe9\x85\x8d\xe7\xbd\xae\n'
            b'module conf\n\n'
            b'pub fn source_ext() -> str {\n    return ".foo";\n}\n')
        (d / "area.foo").write_bytes(
            b"module area\n\npub fn ar(w: u32, h: u32) -> u32 {\n    return w * h;\n}\n")
        (d / "geom.foo").write_bytes(
            b'module geom\n\nuse "area.foo"\n\npub fn g_area(w: u32, h: u32) -> u32 {\n'
            b"    return ar(w, h);\n}\n")
        hi = proj / "hi.foo"
        _entry(hi, "geom")
        assert lomentc.source_ext_of(proj, None) == ".foo"
        assert lomentc.resolve_name("geom", ROOT, proj, None, ".foo") == d / "geom.foo"
        mod = lomentc.load(hi)
        deps = lomentc.resolve_deps(mod, ROOT, proj, entry=hi)
        assert [m.name for m in deps] == ["area", "geom"], [m.name for m in deps]
        ir = lomentc.emit_llvm(mod, ROOT, deps)
        assert ir and "@g_area" in ir, "自定义后缀的单元发不出 IR"


@test
def test_conf_is_optional_and_a_broken_one_falls_back():
    """没配 / 配坏了 / 配了个不以 `.` 开头的 —— 一律**当没配**, 退回 `.lomt`。

    配置文件也是源码, 它会被人改坏。改坏一个字母就把名字形式指到一堆奇怪的文件上, 比
    "退回默认"糟得多: 前者是编译期一连串看不懂的错, 后者至少还编得过。

    注意**不要求 `module` 头**: 与 `lompi.conf` 同一个形状 —— 那边也是一个词法器扫标签,
    根本没有 parser 去要求文件头。另外注释里的同名字符串不该误命中 (词法器天然不会)。
    """
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "proj"
        proj.mkdir()
        assert lomentc.source_ext_of(proj, None) == ".lomt", "没 loment.conf 就是默认"
        assert lomentc.source_ext_of(None, None) == ".lomt"
        for bad in (b'pub fn other() -> str { return ".foo"; }\n',
                    b'pub fn source_ext() -> str { return ext; }\n',
                    b'pub fn source_ext() -> str { return "foo"; }\n',
                    b'pub fn source_ext() -> str { return ""; }\n',
                    b'pub fn source_ext() -> str { return ".foo\n',
                    b"fn broken( {\n"):
            (proj / "loment.conf").write_bytes(bad)
            assert lomentc.source_ext_of(proj, None) == ".lomt", bad
        # 注释里出现的 `source_ext` + 字符串不算 —— 词法器扫, 注释进不了词法流
        (proj / "loment.conf").write_bytes(
            b'// source_ext ".bar"\nmodule conf\n\n'
            b'pub fn source_ext() -> str { return ".foo"; }\n')
        assert lomentc.source_ext_of(proj, None) == ".foo"
        # 没有 `module` 头也认 —— 与 lompi.conf 同形 (它那边也没要求)
        (proj / "loment.conf").write_bytes(b'pub fn source_ext() -> str { return ".foo"; }\n')
        assert lomentc.source_ext_of(proj, None) == ".foo"


@test
def test_custom_suffix_still_reaches_the_toolchain_lomt():
    """项目换成 `.foo` 之后, 工具链自带/内置根里的 `.lomt` 模块**照样找得到**。

    兜底那条不是可选的: 使用者只该改**自己**的源码后缀 —— 仓库内置四根、以及装在工具链
    旁边那份 store 全是 `.lomt`, 少了兜底, 换后缀等于把标准库整个弄丢。
    """
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "proj"
        proj.mkdir()
        (proj / "loment.conf").write_bytes(
            b'pub fn source_ext() -> str { return ".foo"; }\n')
        assert lomentc._ext_chain(".foo") == (".foo", ".lomt")
        assert lomentc._ext_chain(".lomt") == (".lomt",)
        got = lomentc.resolve_name("mathutil", ROOT, proj, None, ".foo")
        assert got == ROOT / "loment" / "examples" / "mathutil.lomt", got


# ---------------------------------------------------------------- 外部函数 (2026-09-16)
# `extern fn` 是 docs/173（FFI）第 1 阶段的语言面。形状：只有签名、末尾分号、由链接进来的
# 目标文件提供实现。第 1 阶段**只收标量与 ptr** —— 聚合按值与 `str` 都会改变调用点形状，
# 报错退出而不是静默错编。

@test
def test_extern_declares_and_calls():
    """`extern fn` 发 `declare`，调用点是普通 `call` —— C ABI 由 LLVM 自己按平台给。"""
    src = ("module m\n\nextern fn c_add(a: i32, b: i32) -> i32;\n\n"
           "fn f() -> i32 { return c_add(3 as i32, 4 as i32); }\n")
    mod = parse(src)
    assert [x.name for x in mod.externs] == ["c_add"], [x.name for x in mod.externs]
    assert mod.funcs[0].name == "f" and not mod.funcs[0].extern
    assert errs(src) == [], errs(src)
    ir = lomentc.emit_llvm(mod, ROOT)
    assert "declare i32 @c_add(i32, i32)" in ir, ir[:400]
    # 调用点必须是**普通 call**（没有额外包装）—— 包装一层就等于我们自己在做 ABI 转换，
    # 而原生 LLVM 路径上那件事是白拿的。
    assert "call i32 @c_add(i32 3, i32 4)" in ir, ir[:400]


@test
def test_extern_without_a_return_type_is_void():
    """不写 `-> T` 就是 void —— `extern fn c_free(p: ptr);` 必须收。

    这一条是**实测踩出来的**：parse_fn 原先靠"后面是 `{`"判断无返回类型，而外部函数没有
    函数体（后面是 `;`），于是最普通的释放函数写法直接被判成"缺了 `->`"。
    """
    src = ("module m\n\nextern fn c_free(p: ptr);\n\n"
           "fn f() -> u32 { let q: ptr = alloc(8); c_free(q); return 0; }\n")
    assert errs(src) == [], errs(src)
    ir = lomentc.emit_llvm(parse(src), ROOT)
    assert "declare void @c_free(ptr)" in ir, ir[:400]
    assert "call void @c_free(ptr " in ir, ir[:400]


@test
def test_extern_rejects_unsupported_signature():
    """`str` / 结构体 / 数组**按值**都不进签名 —— 出现就报错，不静默错编。"""
    for src, why in (
        ("module m\n\nextern fn f(s: str) -> i32;\nfn g() -> i32 { return 0; }\n", "str"),
        ("module m\n\nstruct P { x: u32 }\n\nextern fn f(p: P) -> i32;\nfn g() -> i32 { return 0; }\n", "结构体按值"),
        ("module m\n\nextern fn f(a: [u32; 4]) -> i32;\nfn g() -> i32 { return 0; }\n", "数组按值"),
    ):
        got = errs(src)
        assert any("不支持" in e for e in got), f"{why}: {got}"


@test
def test_extern_conflicts_with_a_real_function():
    """外部函数与普通函数同名 = 两个不同的实现绑到同一个名字上，报重名。"""
    src = ("module m\n\nfn f() -> i32 { return 1; }\n\nextern fn f() -> i32;\n\n"
           "fn g() -> i32 { return 0; }\n")
    got = errs(src)
    assert any("重名" in e for e in got), got


@test
def test_two_modules_may_declare_the_same_extern():
    """两个模块各自 `extern fn c_add` 指的是**同一个外部符号**（等于 C 里重复包头文件）：
    不报重名，且 `declare` **只出一条**。"""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "a.lomt").write_bytes(
            b"module a\n\nextern fn c_add(x: i32, y: i32) -> i32;\n\n"
            b"pub fn use_it() -> i32 { return c_add(1 as i32, 2 as i32); }\n")
        (d / "b.lomt").write_bytes(
            b'module b\n\nuse "a.lomt"\n\nextern fn c_add(x: i32, y: i32) -> i32;\n\n'
            b"fn g() -> i32 { return 0; }\n")
        m = lomentc.load(d / "b.lomt")
        deps = lomentc.resolve_deps(m, ROOT, d, entry=d / "b.lomt")
        assert lomentc.check(m, deps=deps) == [], lomentc.check(m, deps=deps)
        ir = lomentc.emit_llvm(m, ROOT, deps)
        assert sum(1 for l in ir.splitlines() if l.startswith("declare i32 @c_add")) == 1, ir[:400]


@test
def test_extern_takes_no_body_and_no_type_parameters():
    """两种写错的形态要在**解析期**挡住：给了函数体、带了类型参数。"""
    body = "module m\n\nextern fn f() -> i32 { return 1; }\n"
    try:
        parse(body)
    except lomc.LomError as e:
        assert "分号" in e.msg, e.msg
    else:
        raise AssertionError("外部函数带函数体应当被拒")
    gen = "module m\n\nextern fn f<T>(a: T) -> i32;\n"
    try:
        parse(gen)
    except lomc.LomError as e:
        assert "类型参数" in e.msg, e.msg
    else:
        raise AssertionError("外部函数带类型参数应当被拒（单态化要看得到源码）")


@test
def test_boundary_builtins_are_real_builtins():
    """`potato.BOUNDARY_BUILTINS` 必须是编译器内建表的一个**子集**。

    **Why** (`docs/205` R5): 那份清单划的是"越过语言保证的那几个内建"这条边界 ——
    它的价值全在"可 grep、可计数、可审计"上。而它必须是 `potato.py` 里**自己的一份**,
    因为那个文件按 M47 **不许 import 编译器**（独立性就是它存在的理由）。两份因此
    可能悄悄漂: 内建改了名、被删掉了 —— 数出来的数会变成 0, 而**没有任何东西会报**。
    这条判据就是那个"会报"。
    **How to apply**: 它钉的是"表里没有编译器不认识的名字"。反过来的那一半 ——
    **新加了一个同样危险的内建而没进表** —— 这条**测不到**, 那一步要人来判。
    """
    missing = [b for b in potato.BOUNDARY_BUILTINS if b not in lomentc.BUILTINS]
    assert not missing, f"BOUNDARY_BUILTINS 里有编译器不认识的名字: {missing}"
    assert potato.BOUNDARY_BUILTINS, "边界内建表空了 —— 那这条判据就没在干活"


# ---------------------------------------------------------------- L0（docs/210 §2）

#: L0 判据用的那一对程序：**逐字同源，只差 `choose` 那一行**（`docs/210` §5 第二行）。
#: 一个函数一格 —— 提升的与**不该提升的**放在同一份里，所以"少提升一格"和"多提升一格"
#: 都会被同一条判据抓住。
#:
#: **`loment_p8_test` 的孪生判据复用这一对源**（`import lomentc_test`）—— 两个实现比的是
#: 同一份程序，各抄一份必然漂。
L0_PAIR_SRC = """module gc_l0_pair
@@CHOOSE@@
choose runtime

const SZ: u32 = 64;

enum E { A(u32), B }

fn consume(y: ptr) -> u32 {
    return load8(y, 0);
}

fn f_pos() -> u32 {
    let p: ptr = alloc(64);
    store8(p, 0, 7);
    store8(p, 1, 9);
    return load8(p, 0);
}

fn f_alias(x: ptr) -> u32 {
    let p: ptr = alloc(64);
    store8(p, 0, 7);
    return consume(p) + load8(x, 0);
}

fn f_assign(q: ptr) -> u32 {
    let p: ptr = alloc(64);
    store8(p, 0, 7);
    p = q;
    return load8(p, 0);
}

fn f_ret() -> ptr {
    let p: ptr = alloc(64);
    store8(p, 0, 7);
    return p;
}

fn f_dyn(n: u32) -> u32 {
    let p: ptr = alloc(n);
    store8(p, 0, 7);
    return load8(p, 0);
}

fn f_shadow() -> u32 {
    let p: ptr = alloc(64);
    store8(p, 0, 7);
    if load8(p, 0) == 7 {
        let p: ptr = alloc(16);
        store8(p, 0, 9);
    }
    return load8(p, 0);
}

fn f_const_size() -> u32 {
    let p: ptr = alloc(SZ);
    store8(p, 0, 7);
    return load8(p, 0);
}

fn f_derived() -> u32 {
    let p: ptr = alloc(64);
    store8(ptr_add(p, 4), 0, 7);
    return load8(p, 0);
}

fn f_hex() -> u32 {
    let p: ptr = alloc(0x40);
    store8(p, 0, 7);
    return load8(p, 0);
}

fn f_round() -> u32 {
    let p: ptr = alloc(20);
    store8(p, 0, 7);
    return load8(p, 0);
}

fn f_freed() -> u32 {
    let p: ptr = alloc(64);
    store8(p, 0, 7);
    let v: u32 = load8(p, 0);
    free(p);
    return v;
}

fn f_atomic() -> u32 {
    let p: ptr = alloc(8);
    store8(p, 0, 1);
    atomic_add(p, 1);
    return load8(p, 0);
}

fn f_iflet(o: E) -> u32 {
    let p: ptr = alloc(64);
    store8(p, 0, 7);
    let r: u32 = load8(p, 0);
    if let E::A(p) = o {
        r = r + p;
    }
    return r;
}

fn f_partial(x: ptr) -> u32 {
    let p: ptr = alloc(64);
    let q: ptr = alloc(32);
    store8(p, 0, 7);
    store8(q, 0, 9);
    return load8(p, 0) + consume(q) + load8(x, 0);
}
"""

L0_ALPHA_SRC = L0_PAIR_SRC.replace("@@CHOOSE@@", "choose gc_auto_alpha")
L0_MANUAL_SRC = L0_PAIR_SRC.replace("@@CHOOSE@@", "choose gc_manual")

#: 每个函数的**提升表**（不进表 = 一个都不提升）。每一格的"为什么"对着源码里的那一行：
#: 出块的、重新赋值的、返回的、尺寸不是字面量的、被遮蔽的、派生指针的、`free` 过的、
#: `if let` 绑定同名的，全在"一个都不提升"那一栏 —— 而 `f_hex`（十六进制仍是字面量）、
#: `f_round`（20 -> 3×i64）、`f_atomic`（第三个只解引用的内建）在提升那一栏。
L0_WANT = {
    "f_pos": {"p": 64},
    "f_alias": {},
    "f_assign": {},
    "f_ret": {},
    "f_dyn": {},
    "f_shadow": {},
    "f_const_size": {},
    "f_derived": {},
    "f_hex": {"p": 64},
    "f_round": {"p": 20},
    "f_freed": {},
    "f_atomic": {"p": 8},
    "f_iflet": {},
    "f_partial": {"p": 64},
}

#: 那一对程序里的 `alloc` 站点总数、以及 alpha 档**留下不动的**那几个 —— 两个数都写死，
#: 于是"少提升一格"（alpha 数变大）与"多提升一格"（alpha 数变小）都会红。
L0_ALLOC_SITES = 16
L0_ALPHA_ALLOC_LEFT = 11


def _l0_emit(src: str) -> tuple[dict[str, dict[str, int]], str]:
    """编译一份源，回 (每个函数的提升表, IR)。"""
    mod = parse(src)
    errs_ = lomentc.check(mod)
    assert not errs_, errs_[:2]
    table = {f.name: f.l0 for f in mod.funcs if f.l0}
    return table, lomentc.emit_llvm(mod, ROOT)


@test
def test_l0_rule_pair():
    """`gc_auto_alpha` 的 **L0**：一对只差 `choose` 一行的程序，逐格钉住提升表（`docs/210` §2）。

    **这一条同时是"L0 真的发生"和"L0 没被夸大"**（`docs/210` §5 的第二、三行）：
    逐格的表钉住"哪一格该提、哪一格不该提"，而 `alloc` 调用点的两个数钉住"提了就要少一次
    调用" —— **把提升关掉而保留计数**（表还在、IR 里却还是 `call @__loment_alloc`）会让
    alpha 那一格涨到 16，红；**把计数改成 0** 而实际照提会让 `L0_ALPHA_ALLOC_LEFT` 对不上，
    也红。两个方向都有钩子，所以这不是一条"顺手报个绿"的判据。
    """
    alpha_tbl, alpha_ll = _l0_emit(L0_ALPHA_SRC)
    manual_tbl, manual_ll = _l0_emit(L0_MANUAL_SRC)

    assert alpha_tbl == {k: v for k, v in L0_WANT.items() if v}, f"alpha 提升表: {alpha_tbl}"
    assert manual_tbl == {}, f"`gc_manual` 下不许有任何提升，得到 {manual_tbl}"

    a_calls = alpha_ll.count("call ptr @__loment_alloc")
    m_calls = manual_ll.count("call ptr @__loment_alloc")
    assert m_calls == L0_ALLOC_SITES, f"`gc_manual` 的分配点应恒为 {L0_ALLOC_SITES}，得到 {m_calls}"
    assert a_calls == L0_ALPHA_ALLOC_LEFT, f"alpha 的分配点应为 {L0_ALPHA_ALLOC_LEFT}，得到 {a_calls}"
    # 两个数都非平凡：不然"全都提升"与"一个都不提升"都能让上面两条同时成立
    assert 0 < L0_ALPHA_ALLOC_LEFT < L0_ALLOC_SITES, "这一对里两种走向都要有"
    # 缓冲的 `[... x i64]` 计数（每格两行：alloca + store）钉住"尺寸取整"没被改坏
    assert alpha_ll.count(".buf") == 2 * sum(1 for v in L0_WANT.values() if v), alpha_ll.count(".buf")
    assert ".buf" not in manual_ll
    # 两份源**只差那一行** —— 不然"差别来自档位"这个说法就不成立
    assert L0_ALPHA_SRC.replace("choose gc_auto_alpha", "X") == \
        L0_MANUAL_SRC.replace("choose gc_manual", "X")


@test
def test_l0_param_shadow_is_not_promoted():
    """形参同名的那条 `let` **不提升** —— 它买的是"不会发出没有定义的 `%NAME.buf`"。

    `_collect_locals` 把与形参同名局部**排除**在 alloca 之外（`seen = params`），所以那条
    `let p` 写的是**形参的槽**；若把它提升，`%p.buf` 这条 alloca 根本不会被发射，
    IR 里就出现一个未定义的值。这条判据只钉参考侧（自举侧在这个形状上本来就与参考不一致 ——
    `collect_locals` 不排形参，是**另一条既有的**缺口，不归 L0 管）。
    """
    src = ("module m\nchoose gc_auto_alpha\nchoose runtime\n\n"
           "fn f(p: ptr) -> u32 {\n    let p: ptr = alloc(64);\n"
           "    store8(p, 0, 7);\n    return load8(p, 0);\n}\n")
    tbl, ll = _l0_emit(src)
    assert tbl == {}, f"形参同名不该提升，得到 {tbl}"
    assert ".buf" not in ll


@test
def test_l0_safe_set_is_exactly_the_deref_only_ptr_arg0_builtins():
    """`_L0_SAFE_BUILTINS` 必须**恰好**是"首参是 `ptr` 且只解引用它"的那几个内建。

    这条钉的是 `test_boundary_builtins_are_real_builtins` 自己承认**测不到的那一半**：
    那份判据只管"表里的名字编译器认识"，**新加一个同样危险的内建而没人过问**它抓不住。
    这里反过来：内建表里**每一个**首参为 `ptr` 的内建，都必须落在"算"或"不算"这两张表之一，
    而"不算"那一张要逐条给出理由（见 `tools/lomentc.py` 里那张表）—— **加了新内建就红**。

    **Why**: `load16/32/64`、`store16/32/64` 曾经被误当成安全名单（它们**不是**内建，是
    `loment/examples/bytes.lomt` 里用 `load8`/`store8` 拼的 pub 函数）—— 那会把一个指针
    交给别人的栈帧，而"那个函数不会把 p 存起来"编译器并不知道。
    """
    ptr0 = {b for b, (ps, _) in lomentc.BUILTINS.items() if ps and ps[0] == "ptr"}
    known = lomentc._L0_SAFE_BUILTINS | lomentc._L0_PTR_ARG0_UNSAFE
    assert ptr0 == known, f"首参是 ptr 的内建少了或多了: 表里 {ptr0 ^ known}"
    # 想真被"审"过：安全那一张必须非空且真的只是解引用的那几个
    assert lomentc._L0_SAFE_BUILTINS == {"load8", "store8", "atomic_add"}
    # `load16/32/64`/`store16/32/64` **不许**出现在任何一张里 —— 它们根本不是内建
    for b in ("load16", "load32", "load64", "store16", "store32", "store64"):
        assert b not in lomentc.BUILTINS, f"{b} 怎么成了内建？名单要重判"
        assert b not in known, f"{b} 不是内建，不该在 L0 名单里"


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
