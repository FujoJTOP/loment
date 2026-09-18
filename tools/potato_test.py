#!/usr/bin/env python3
# potato_test.py — Potato 独立校验器完备性自检 (M47, docs/147)
#
# 判据: 校验器不 import 编译器代码; 规范里的每条规则都有反例被拒。
# 运行: python tools/potato_test.py   (退出码 0 = 全绿)
#
# 本文件只 import potato —— 若它需要编译器才能判合法, 独立性就已失效。

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import potato  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def fixture() -> dict:
    """一份覆盖 v1 全字段的合法形式对象 (手写, 不经编译器)。"""
    return {
        "potato": "v1",
        "unit": "demo",
        "language": "loment",
        "imports": ["mathutil"],
        "capabilities": [
            {"name": "blk_write", "domain": {"space": "disk", "lo": 0, "hi": 4},
             "revocable": True},
        ],
        "functions": [
            {"name": "sum", "params": [{"name": "xs", "type": "[u32]"}], "ret": "u32"},
            {"name": "fill", "params": [{"name": "xs", "type": "mut [u32]"},
                                        {"name": "v", "type": "u32"}], "ret": "()"},
            {"name": "len_of", "params": [{"name": "s", "type": "str"}], "ret": "u32"},
            {"name": "first", "params": [{"name": "b", "type": "Blk"}], "ret": "u32"},
            {"name": "make", "params": [], "ret": "Pair_u32"},
        ],
        "layouts": [
            {"name": "Header", "size": 16, "endian": "little", "packed": True,
             "fields": [{"name": "magic", "type": "u32", "offset": 0},
                        {"name": "len", "type": "u32", "offset": 4}]},
        ],
        "consts": [{"name": "MAX", "type": "u32", "value": 8}],
        "enums": [
            {"name": "Color", "variants": ["Red", "Green"]},
            {"name": "Opt_u32", "variants": ["None", "Some"], "payloads": {"Some": "u32"}},
        ],
        "types": [
            {"name": "Blk", "fields": [{"name": "off", "type": "u32"}]},
            {"name": "Pair_u32", "fields": [{"name": "a", "type": "u32"},
                                            {"name": "b", "type": "u32"}]},
        ],
        "traits": [{"name": "Measurable", "methods": ["measure"]}],
        "impls": [
            {"trait": "Measurable", "for": "Blk", "methods": ["measure"]},
            {"trait": "Drop", "for": "Blk", "methods": ["drop"]},
        ],
        "generics": [
            {"kind": "fn", "name": "max", "params": ["T"]},
            {"kind": "type", "name": "Pair", "params": ["T"]},
        ],
        "instances": [
            {"kind": "fn", "name": "max_u32", "of": "max", "args": ["u32"]},
            {"kind": "type", "name": "Pair_u32", "of": "Pair", "args": ["u32"]},
        ],
        "guards": 2,
        "excluded": ["network: 本单元不申请任何 net 能力"],
    }


def rejected(doc: dict, needle: str) -> bool:
    errs = potato.validate(doc)
    return any(needle in e for e in errs), errs


# ---------------------------------------------------------------- 独立性 (M47 判据 1)

@test
def test_validator_does_not_import_compiler():
    src = (Path(potato.__file__)).read_text(encoding="utf-8")
    for m in re.finditer(r"^\s*(?:import|from)\s+([A-Za-z_][\w.]*)", src, re.M):
        assert m.group(1).split(".")[0] != "lomentc", m.group(0)
    assert "lomentc" not in src.replace("# M47: 本文件不得 import 编译器 (lomentc)", "")


@test
def test_fixture_is_valid():
    assert potato.validate(fixture()) == [], potato.validate(fixture())


# ---------------------------------------------------------------- 反例表 (M47 判据 2)
# 每条 = (名字, 变异函数, 期望错误片段); M53 跨实现一致性复用同一张表。

MUTATORS = [
    ("version", lambda d: d.__setitem__("potato", "v9"), "版本"),
    ("v0 禁扩展字段", lambda d: d.__setitem__("potato", "v0"), "未知顶层字段"),
    ("v1 必填 generics", lambda d: d.pop("generics"), "缺字段 generics"),
    ("函数返回类型", lambda d: d["functions"][0].__setitem__("ret", "u9"), "ret 非法类型"),
    ("切片元素类型", lambda d: d["functions"][0]["params"][0].__setitem__("type", "[u9]"),
     "参数类型非法"),
    ("可变切片元素类型", lambda d: d["functions"][1]["params"][0].__setitem__("type", "mut [Foo]"),
     "参数类型非法"),
    ("泛型参数空", lambda d: d["generics"][0].__setitem__("params", []),
     "params 必须是非空数组"),
    ("泛型参数重复", lambda d: d["generics"][0].__setitem__("params", ["T", "T"]), "重复"),
    ("泛型声明重复", lambda d: d["generics"].append(dict(d["generics"][0])), "泛型声明重复"),
    ("实例引用未知泛型", lambda d: d["instances"][0].__setitem__("of", "nope"),
     "不是已声明的泛型"),
    ("实例实参数量", lambda d: d["instances"][0].__setitem__("args", []), "不符"),
    ("实例实参类型", lambda d: d["instances"][0].__setitem__("args", ["u9"]), "非法类型"),
    ("实例名重复", lambda d: d["instances"].append(dict(d["instances"][0])), "重复"),
    ("impl 引用未知 trait", lambda d: d["impls"][0].__setitem__("trait", "Nope"),
     "不是已声明的 trait"),
    ("impl 方法未声明", lambda d: d["impls"][0]["methods"].append("ghost"), "未在 trait"),
    ("impl for 非法类型", lambda d: d["impls"][0].__setitem__("for", "Nope"), "for 非法类型"),
    ("trait 方法空", lambda d: d["traits"][0].__setitem__("methods", []),
     "methods 必须是非空数组"),
    ("枚举载荷未声明", lambda d: d["enums"][1]["payloads"].__setitem__("Some", "Nope"),
     "类型未声明"),
    ("载荷键非变体", lambda d: d["enums"][1]["payloads"].__setitem__("Nope", "u32"),
     "不是已声明变体"),
    ("常量类型", lambda d: d["consts"][0].__setitem__("type", "str"), "必须是整型"),
    ("能力域区间", lambda d: d["capabilities"][0]["domain"].__setitem__("lo", 9), "区间非法"),
    ("布局重叠", lambda d: d["layouts"][0]["fields"][1].__setitem__("offset", 0), "重叠"),
    ("布局越界", lambda d: d["layouts"][0]["fields"][1].__setitem__("offset", 15), "越界"),
    ("类型引用未声明", lambda d: d["types"][0]["fields"][0].__setitem__("type", "Nope"), "未声明"),
    ("函数重名", lambda d: d["functions"].append(dict(d["functions"][0])), "重复"),
    ("excluded 空串", lambda d: d.__setitem__("excluded", [""]), "非空字符串"),
    ("guards 负数", lambda d: d.__setitem__("guards", -1), "非负整数"),
    ("guards 类型", lambda d: d.__setitem__("guards", "2"), "非负整数"),
    # 跨表/跨列表探针 (M53: 两份独立实现最容易在这里分歧)
    ("类型与枚举同名", lambda d: d["types"][0].__setitem__("name", "Color"), "同时出现在"),
    ("能力名重复", lambda d: d["capabilities"].append(dict(d["capabilities"][0])), "重复"),
    ("枚举名重复", lambda d: d["enums"].append(dict(d["enums"][0])), "重复"),
    ("类型名重复", lambda d: d["types"].append(dict(d["types"][0])), "重复"),
    ("布局名重复", lambda d: d["layouts"].append(dict(d["layouts"][0])), "重复"),
    # v2 = v1 + 项目模式 (docs/143 §3.2)。**`mode` 必填** —— docs/175 §8 的判据就是
    # "从形式对象里删掉该字段, 独立校验器必须红", 而"必填"正是**不能往 v1 加字段**的
    # 原因: 那会让既有的 v1 对象 (含冻结样本 demo.v0.json 那一路) 全变非法, 而 v0/v1
    # 是承诺过能回放的 (docs/147 §5)。所以升版本。
    ("v2 缺 mode", lambda d: d.__setitem__("potato", "v2"), "mode 必须是"),
    ("v2 mode 拼错", lambda d: (d.__setitem__("potato", "v2"),
                            d.__setitem__("mode", "fast")), "mode 必须是"),
    ("v2 mode 非字符串", lambda d: (d.__setitem__("potato", "v2"),
                                d.__setitem__("mode", 1)), "mode 必须是"),
    # v3 = v2 + **开关取值** (docs/182 §1)。用户 2026-09-17: "开关的取值是要进 Potato 的"。
    # 同一条纪律: **必填, 可为空数组** —— 不存在"缺这项"的形态, 所以"这份单元是在什么
    # 开关状态下编的"是可回放的, 不是"看当时的源码猜"。
    ("v3 缺 switches", lambda d: (d.__setitem__("potato", "v3"),
                              d.pop("switches", None)), "switches 必须是数组"),
    ("v3 switches 不是数组", lambda d: (d.__setitem__("potato", "v3"),
                                 d.__setitem__("switches", {})), "switches 必须是数组"),
    ("v3 on 不是布尔", lambda d: (d.__setitem__("potato", "v3"),
                              d.__setitem__("switches", [{"name": "a", "on": 1}])),
     "必须是布尔"),
    ("v3 开关名非法", lambda d: (d.__setitem__("potato", "v3"),
                             d.__setitem__("switches", [{"name": "1x", "on": True}])),
     "name 非法"),
    ("v3 开关名重复", lambda d: (d.__setitem__("potato", "v3"),
                             d.__setitem__("switches", [{"name": "a", "on": True},
                                                        {"name": "a", "on": False}])),
     "name 重复"),
    # v4 = v3 + **方言**（`docs/184` §9 S4.3）：这份单元用了 `comefor` 定义的那些语法。
    # 同一条纪律：**必填、可为空数组**。`body` 是定义处那段程序的**源文本** ——
    # 只记名字的话，读产物的人知道"用了 `def`"却不知道 `def` 是什么（`docs/184` §7 ②）。
    # 这几条都先补上 `switches`（v4 继承 v3 的检查），把要测的那一条**孤立出来**。
    ("v4 缺 dialects", lambda d: (d.__setitem__("potato", "v4"),
                              d.__setitem__("switches", []), d.pop("dialects", None)),
     "dialects 必须是数组"),
    ("v4 dialects 不是数组", lambda d: (d.__setitem__("potato", "v4"),
                                 d.__setitem__("switches", []),
                                 d.__setitem__("dialects", {})), "dialects 必须是数组"),
    ("v4 方言名非法", lambda d: (d.__setitem__("potato", "v4"),
                             d.__setitem__("switches", []),
                             d.__setitem__("dialects", [{"name": "1x", "body": ""}])),
     "name 非法"),
    ("v4 方言名重复", lambda d: (d.__setitem__("potato", "v4"),
                             d.__setitem__("switches", []),
                             d.__setitem__("dialects", [{"name": "a", "body": ""},
                                                        {"name": "a", "body": ""}])),
     "name 重复"),
    ("v4 body 不是字符串", lambda d: (d.__setitem__("potato", "v4"),
                                 d.__setitem__("switches", []),
                                 d.__setitem__("dialects", [{"name": "a", "body": 1}])),
     "body 必须是字符串"),
]


def mutated_objects():
    """(名字, 非法对象) —— 供跨实现一致性对照 (M53)。"""
    for name, mut, _ in MUTATORS:
        d = fixture()
        mut(d)
        yield name, d


@test
def test_every_spec_rule_has_a_rejection_case():
    assert len(MUTATORS) >= 24, len(MUTATORS)
    for name, mut, needle in MUTATORS:
        d = fixture()
        mut(d)
        errs = potato.validate(d)
        assert any(needle in e for e in errs), (name, errs)


# ---------------------------------------------------------------- 版本回放 (M51 前置)

@test
def test_v0_object_still_validates():
    """旧版本 (v0) 对象必须仍能回放校验 —— 只是不允许 v1 新字段。"""
    d = fixture()
    for k in ("traits", "impls", "generics", "instances", "guards"):
        d.pop(k)
    d["potato"] = "v0"
    d["functions"] = d["functions"][:1]
    assert potato.validate(d) == [], potato.validate(d)


@test
def test_v2_object_validates_with_mode():
    """v2 = v1 + `mode` (docs/143 §3.2)。**当前版本**, 所以它必须是能过的正例;
    反例 (缺 mode / 拼错 / 非字符串) 在 `MUTATORS` 里 —— 一正一反才说明"必填"是真的。"""
    d = fixture()
    d["potato"] = "v2"
    for m in potato.MODES:
        d["mode"] = m
        assert potato.validate(d) == [], (m, potato.validate(d))


@test
def test_committed_objects_validate():
    """仓库里每份已提交的形式对象都必须合法 (M45/M46 产物面)。"""
    objs = sorted((ROOT / "loment" / "build").glob("*.potato.json"))
    assert len(objs) >= 15, [p.name for p in objs]
    for p in objs:
        doc = json.loads(p.read_text(encoding="utf-8"))
        assert potato.validate(doc) == [], (p.name, potato.validate(doc))


@test
def test_m51_legacy_v0_replays():
    """M51: 旧版本 (v0) 对象按自带版本回放校验, 新版本不受影响。"""
    legacy = ROOT / "loment" / "build" / "legacy" / "demo.v0.json"
    doc = json.loads(legacy.read_text(encoding="utf-8"))
    assert doc["potato"] == "v0"
    assert potato.validate(doc) == [], potato.validate(doc)
    assert potato.main(["replay", str(legacy)]) == 0
    assert potato.main(["replay", "--expect-version", "v1", str(legacy)]) == 1


@test
def test_m48_llm_arm_scorer_offline():
    """M48: 宿主 LLM 臂的**建载荷 + 计分**离线可验 (不联网、不需要模型)。

    用罐装回复钉住计分语义 —— 三种典型失败模式各一条: 完整对象 / 原样抄模板 /
    实体畸形。真跑模型是外部一步 (docs/147 §8 的协议要求), 不进门禁。
    """
    import tempfile
    import potato_llm_arm as arm

    good = ('{"potato":"v1","unit":"m","language":"python","imports":[],"capabilities":[],'
            '"layouts":[],"functions":[{"name":"add","params":[{"name":"a","type":"u32"}],'
            '"ret":"u32"}],"consts":[],"enums":[],"types":[],"traits":[],"impls":[],'
            '"generics":[],"instances":[],"guards":0,"excluded":[]}')
    # 抄模板 (llama3.2:3b 的真实失败模式): 结构合法但没有本单元的任何实体
    echo = ('{"potato":"v1","unit":"<标识符>","language":"python","imports":[],'
            '"capabilities":[],"layouts":[],"functions":[],"consts":[],"enums":[],"types":[],'
            '"traits":[],"impls":[],"generics":[],"instances":[],"guards":0,"excluded":[]}')
    # 畸形实体 (qwen3:4b 的真实失败模式之一): 函数缺 ret
    # (连前面的逗号一起去掉, 否则剩下 `],}` 是非法 JSON —— 那测的就成了"抠不出 JSON")
    bad_entity = good.replace(',"ret":"u32"', '')

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        entries = {e["path"]: e for e in arm.corpus_entries()}
        path = "tools/potato.py"
        assert path in entries, sorted(entries)[:3]
        e = entries[path]
        meta = {"protocol": "test", "arm": "host-llm", "models": ["fake:1"],
                "files": [{"path": e["path"], "lang": e["lang"], "stem": "x"}]}
        (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        sub = d / arm.slug("fake:1")
        sub.mkdir(parents=True)
        (sub / "x.meta.json").write_text(json.dumps(
            {"path": e["path"], "lang": e["lang"],
             "chars_total": 10, "chars_sent": 10}), encoding="utf-8")
        for tag, text in (("good", good), ("echo", echo), ("bad", bad_entity)):
            (sub / f"x.rep.json").write_text(json.dumps({"message": {"content": text}}),
                                             encoding="utf-8")
            out = d / f"{tag}.json"
            assert arm.score_dir(d, out) == 0
            row = json.loads(out.read_text(encoding="utf-8"))["rows"][0]
            if tag == "good":
                assert row["object_valid"], row["validate_errors"]
                assert row["entities_ok"] == 1 and row["entities_malformed"] == 0, row
            elif tag == "echo":
                # 原样抄模板: 结构字段齐全, 但 unit 是占位符 `<标识符>` -> 校验器判非法,
                # 且里面没有任何本单元实体 (这一格正是"抄模板"与"真转写"的分界)
                assert not row["object_valid"], "占位符 unit 必须被判非法"
                assert row["entities_seen"] == 0, "模板里没有本单元实体"
            else:
                assert row["entities_ok"] == 0 and row["entities_malformed"] == 1, row

    # 载荷必须确定性 (同温度 0 同一 seed), 且语料路径不能越出仓库
    with tempfile.TemporaryDirectory() as td:
        assert arm.build(Path(td), ["fake:1"], 200, 1) == 0
        req = json.loads(next(Path(td).glob("fake_1/*.req.json")).read_text(encoding="utf-8"))
        assert req["options"] == {"temperature": 0, "seed": arm.SEED, "num_ctx": arm.NUM_CTX}
        assert "```" in req["messages"][0]["content"], "载荷里要有源码围栏"
    try:
        bad = json.loads(json.dumps([{"path": "../../etc/passwd", "lang": "python"}]))
        with tempfile.TemporaryDirectory() as td:
            corpus = Path(td) / "corpus.json"
            corpus.write_text(json.dumps(bad), encoding="utf-8")
            old, arm.CORPUS = arm.CORPUS, corpus
            try:
                arm.corpus_entries()
                raise AssertionError("越界路径没被拦住")
            except ValueError:
                pass
            finally:
                arm.CORPUS = old
    except AssertionError:
        raise


@test
def test_m50_assert_table_matches_objects():
    """M50: A1–A4 断言表由形式对象驱动, 且与已提交产物一致。"""
    import potato_assert
    objs = potato_assert.load_objects()
    rs = potato_assert.rows(objs)
    assert rs, "断言表为空"
    for r in rs:
        assert r["a1"] and r["a2"] and r["a3"] and r["a4"], r
    dest = ROOT / "loment" / "build" / "cap_asserts.rs"
    assert dest.exists(), "cap_asserts.rs 缺失"
    assert dest.read_text(encoding="utf-8") == potato_assert.emit_rust(objs)


def main() -> int:
    failed = []
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, e))
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\npotato_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过 "
          f"(反例 {len(MUTATORS)} 条)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
