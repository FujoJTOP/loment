#!/usr/bin/env python3
# loment_err_test.py — 报错器 `lomenterr` 的判据 (docs/182 §6/§8)
#
# 判的是**链出来的可执行文件本身**: 拿 `tools/lomentc.py --check --diag-out` 产出的真诊断
# 喂它, 逐条验渲染出来的东西 (标题/位置/源行/插入符/建议) 与退出码。
#
# 三类断言, 各挡一个真会犯的错:
#   * **字段都在** —— 少一样(比如插入符)它就从"报错器"退化成"把 JSON 换个排版的打印器";
#   * **两条报错通道都覆盖** —— check() 的语义错只给行号, LomError 给行:列; 只测一条,
#     另一条的缺口就是静默的 (docs/182 §5.1 那条判据自己犯过这个错);
#   * **说出来的话是真的** —— 未知码 / 坏行 / 没有诊断, 三种都要**明说**, 不能悄悄换掉
#     或悄悄跳过 (docs/179:113-115 "静默才是敌人")。
#
# 还有一条**在包那一侧**: 启动器起不动 lomenterr 时必须**报出来**, 而在场时它的输出与
# 编译器自己那些裸行**可区分** —— 否则判据测不到它到底跑没跑 (docs/182 §8)。
#
# 运行: python tools/loment_err_test.py   (退出码 0 = 全绿)

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment  # noqa: E402  (build_lomenterr: 报错器在这里编一次, 全测复用)

ROOT = Path(__file__).resolve().parent.parent
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------- 夹具

_exe: Path | None = None


def _bin() -> Path:
    global _exe
    if _exe is None:
        _exe = loment.build_lomenterr()
    return _exe


def _check(src: str) -> tuple[Path, Path]:
    """写一份源, 用参考实现产一份真诊断 (JSONL)。返回 (源路径, 诊断路径)。"""
    td = Path(tempfile.mkdtemp(prefix="lomenterr-"))
    f = td / "bad.lomt"
    f.write_text(src, encoding="utf-8", newline="\n")
    d = td / "d.jsonl"
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "lomentc.py"),
                        str(f), "--check", "--diag-out", str(d)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", shell=False, timeout=120)
    assert r.returncode == 1, (src, r.returncode, r.stderr[-200:])
    return f, d


def _render(diag: Path) -> tuple[int, str]:
    r = subprocess.run([str(_bin()), str(diag)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", shell=False, timeout=60)
    return r.returncode, r.stdout + r.stderr


# 两份源, 各走一条报错通道 (docs/182 §5): check() 的语义错与 LomError 的解析错。
SEMANTIC = "module m\n\nfn f() -> u32 {\n    return z;\n}\n"
PARSE = "module m\n\nfn f() -> u32 {\n    return 1;\n}\n@@@\n"


# ---------------------------------------------------------------- 渲染

@test
def test_render_carries_title_location_source_and_hint():
    """好路径: 标题、`--> 文件:行`、源行、插入符、建议 —— 五样都要在。

    这五样就是"报错器"与"把 JSON 换个排版打出来"的区别。少一样都该红: 少了建议它就不比
    编译器那些裸行强, 少了插入符它指不到那一列。
    """
    f, d = _check(SEMANTIC)
    rc, out = _render(d)
    assert rc == 1, (rc, out[:200])
    assert "error[E002]:" in out, out[:200]
    assert "符号未声明" in out, "没查 surface_data 的标题"
    assert f"-->{' '}" in out.replace("--> ", "--> ") or "-->" in out, out[:200]
    assert f":4" in out, "位置里没有行号"
    assert "return z;" in out, "没有把源行印出来"
    assert "^" in out, "没有插入符"
    assert "建议:" in out, "没有修复建议"
    assert "先声明后使用" in out, "建议不是 surface_data 里那一份"
    print(f"      渲染完整 (5 样齐: 标题/位置/源行/插入符/建议)")


@test
def test_both_error_channels_render_differently():
    """两条**报错通道**都要有输出, 而且位置那条要如实地分岔。

    这两个源走的是**不同的代码路径** (check() 与 raise LomError), 落进 --diag-out 时形状也
    不同: check() 只给行号 (`col = 0`), LomError 给 `行:列`。所以渲染必须**如实分岔**:
    有列时插入符落在那一列, 没列时划整行的可见部分 —— 而不是给一个"猜的列"。
    只测一条通道的话, 另一条的缺口就是静默的 (docs/182 §5.1 那个判据自己犯过)。
    """
    _, ds = _check(SEMANTIC)     # check()  -> col = 0
    _, dp = _check(PARSE)        # LomError -> col = 1

    rcs, outs = _render(ds)
    rcp, outp = _render(dp)
    assert rcs == 1 and rcp == 1

    # 语义错: 只给行号 -> **不写列**, 且划整行 (源行 `    return z;` 的可见部分)
    assert ":4\n" in outs, f"check() 那条不该写列号: {outs[:200]}"
    assert "^^^^^^^^^" in outs, f"没有划整行的可见部分: {outs[:200]}"
    # 解析错: 给了 1:1 -> 写列, 插入符只有一格 (`@@@` 的第一个字符)
    assert ":6:1" in outp, f"LomError 那条该写 行:列: {outp[:200]}"
    assert "| ^\n" in outp, f"插入符该落在第 1 列、只有一格: {outp[:200]}"
    assert "^^^^^" not in outp, "解析错那条不该划整行"

    # 位置形状不同 => 两条通道确实都渲染过, 不是同一个模板打出来的
    assert outs != outp
    print("      两条通道都渲染, 且位置形状如实分岔 (有列/无列)")


@test
def test_unknown_code_is_said_out_loud():
    """表里没有的码: **明说**不知道, 而不是标题空着、建议静默消失。

    这条是防御性的, 但它挡的是一个真会发生的处境: 诊断文件与报错器**不是同一版**工具链
    (旧 lomenterr 遇上新编译器吐的新码)。那时的正确行为是"这条我不认识, 按原文看",
    而不是渲染出一条**少了建议、看着像没问题**的诊断。
    """
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "d.jsonl"
        d.write_text(json.dumps({"file": "x.lomt", "line": 1, "col": 2,
                                 "code": "E042", "message": "未来才有这个码"}) + "\n",
                     encoding="utf-8", newline="\n")
        rc, out = _render(d)
    assert rc == 1, (rc, out)
    assert "E042" in out, "码本身要原样印出来"
    assert "未知错误码" in out, f"没说不认识这个码: {out[:200]}"
    assert "不在表面数据表里" in out, f"没解释为什么没有建议: {out[:200]}"
    assert "未来才有这个码" in out, "消息原文要保留"
    print("      未知码: 说出来了, 而且建议那条不是静默缺失")


@test
def test_broken_line_is_echoed_not_skipped():
    """诊断文件里混进非 JSON 行: **原样打出来**并计数, 不跳过。

    跳过的症状是"报错器什么都没说", 而调用方会把它读成"没有错误" —— 这正是本仓要消灭的
    那种静默 (docs/179:113-115)。
    """
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "d.jsonl"
        d.write_text("this is not json\n"
                     + json.dumps({"file": "x.lomt", "line": 1, "col": 1,
                                   "code": "E019", "message": "真的一条"}) + "\n",
                     encoding="utf-8", newline="\n")
        rc, out = _render(d)
    assert rc == 1, (rc, out)
    assert "this is not json" in out, "坏行没原样打出来"
    assert "不是合法 JSON" in out, out[:200]
    assert "真的一条" in out, "坏行不该把后面的好行带下水"
    print("      坏行原样回显并计数, 后面的好行照常渲染")


@test
def test_empty_diag_is_ok_and_usage_error_is_two():
    """没有诊断 = 退 0; 用法/读取失败 = 退 **2**, 与"有诊断"(1)分开。

    三个码混成一个, 调用方就分不清"你的源码错了"和"报错器自己没起来"。
    """
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "empty.jsonl"
        d.write_text("", encoding="utf-8", newline="\n")
        rc, out = _render(d)
        assert rc == 0, (rc, out)
        assert "无诊断" in out, out
        # 打不开
        r = subprocess.run([str(_bin()), str(Path(td) / "nope.jsonl")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", shell=False, timeout=60)
        assert r.returncode == 2, (r.returncode, r.stderr[:120])
        # 没有参数
        r2 = subprocess.run([str(_bin())], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", shell=False, timeout=60)
        assert r2.returncode == 2, (r2.returncode, r2.stderr[:120])
        assert "用法" in r2.stderr, r2.stderr[:120]
    print("      空诊断退 0; 用法/读取失败退 2 (与'有诊断'的 1 分开)")


@test
def test_rendering_differs_from_the_compilers_plain_lines():
    """"渲染过"这件事必须**看得出来** —— 否则判据测不到它到底跑没跑 (docs/182 §8)。

    比较对象是编译器的裸行 (参考实现打 `[ERR] path: N 项语义错误:` + `行: 文本`)。两者若
    长得一样, 那"接上报错器"就没有任何可观测效果, 判据也就无从写起。
    """
    f, _ = _check(SEMANTIC)
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "lomentc.py"),
                        str(f), "--check"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", shell=False, timeout=120)
    plain = r.stderr
    _, d = _check(SEMANTIC)
    _, rendered = _render(d)
    assert plain.strip() and rendered.strip()
    assert rendered != plain
    assert "error[E002]" not in plain, "编译器的裸行里不该已经有渲染后的标题行"
    assert "建议:" not in plain, "编译器的裸行里不该已经有建议"
    assert "-->" not in plain, "编译器的裸行里没有位置箭头"
    print("      与编译器的裸行可区分 (箭头/标题/建议都只有渲染侧才有)")

@test
def test_overlong_record_says_it_was_cut():
    """一条诊断撑爆渲染缓冲时: **明说被截断**, 不是悄悄少印一截。

    截断看着像"这条就到这儿" —— 那是本仓最反对的那种"悄悄改内容"。触发条件是真实的:
    一份生成出来的源码里可以有几十 KB 的一行 (判据这里就造了一行 40 KB 的)。
    """
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "wide.lomt"
        src.write_text("module m" + "x" * 40000 + "\n", encoding="utf-8", newline="\n")
        d = Path(td) / "d.jsonl"
        d.write_text(json.dumps({"file": str(src), "line": 1, "col": 1,
                                 "code": "E019", "message": "宽行"}) + "\n",
                     encoding="utf-8", newline="\n")
        rc, out = _render(d)
    assert rc == 1, (rc, out[:200])
    assert "被截断" in out, f"超长没被说破 —— 输出静默少了一截: {out[-200:]!r}"
    assert "error[E019]" in out, "截断之前那部分还是要印出来的"
    print("      超长记录: 明说被截断, 不静默少印")

# ---------------------------------------------------------------- 包那一侧 (§6 的兜底纪律)

@test
def test_launcher_renders_with_it_and_says_so_without_it():
    """启动器: 有 lomenterr 就用它; 没有就**退回内置并把话说出来**。

    两条都要测, 而且**用真的启动器 + 桩驱动**: 只测"有"那一半的话, "静默换掉用户以为在用的
    东西"这个错就没有判据 (docs/182 §6/§8)。桩驱动同时吐 stderr(裸行) 与 `--diag-out`(JSONL),
    与两个真实现的形状一致。
    """
    sys.path.insert(0, str(ROOT / "tools"))
    import loment_dist  # noqa: E402
    bash = shutil.which("bash")
    if not bash:
        print("        (跳过: 没有 bash, 跑不了 POSIX 启动器)")
        return

    def shp(p: Path) -> str:
        s = str(p).replace("\\", "/")
        return f"/{s[0].lower()}{s[2:]}" if len(s) > 2 and s[1] == ":" else s

    stub_driver = ("#!/bin/sh\n"
                   "out=\n"
                   "while [ $# -gt 0 ]; do\n"
                   "  case \"$1\" in --diag-out) shift; out=$1 ;; esac\n"
                   "  shift\n"
                   "done\n"
                   "printf '%s\\n' "
                   "'{\"file\":\"x.lomt\",\"line\":1,\"col\":1,\"code\":\"E019\","
                   "\"message\":\"boom\"}' > \"$out\"\n"
                   "echo PLAIN-DRIVER-OUTPUT >&2\n"
                   "exit 1\n")

    def make_pkg(with_err: bool) -> Path:
        t = Path(tempfile.mkdtemp(prefix="lomenterr-launcher-"))
        pf = t / "pf"
        (pf / "bin").mkdir(parents=True)
        (pf / "share" / "loment").mkdir(parents=True)
        (pf / "share" / "loment" / "version").write_bytes(b"stub\n")
        (pf / "bin" / "loment-driver").write_text(stub_driver, encoding="utf-8",
                                                  newline="\n")
        (pf / "bin" / "loment").write_text(
            loment_dist._subst(loment_dist.LAUNCHER_SH), encoding="utf-8", newline="\n")
        if with_err:
            (pf / "bin" / "lomenterr").write_text(
                "#!/bin/sh\necho RENDERED-BY-LOMENTERR \"$@\"\n",
                encoding="utf-8", newline="\n")
        for p in (pf / "bin").iterdir():
            p.chmod(0o755)
        (pf / "src.lomt").write_text("module m\n", encoding="utf-8", newline="\n")
        return pf

    env = dict(os.environ)
    env["PATH"] = f"{shp(Path('/usr/bin'))}:{shp(Path('/bin'))}:{env.get('PATH', '')}"

    def run(pf: Path) -> str:
        r = subprocess.run([bash, shp(pf / "bin" / "loment"), "check",
                            shp(pf / "src.lomt")],
                           cwd=str(pf), env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", shell=False, timeout=60)
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        return r.stdout + r.stderr

    with_it = run(make_pkg(True))
    without_it = run(make_pkg(False))

    assert "RENDERED-BY-LOMENTERR" in with_it, f"在场时没起它: {with_it!r}"
    assert "PLAIN-DRIVER-OUTPUT" not in with_it, (
        f"用了渲染器就不该再吐驱动那行裸诊断 (会说两遍): {with_it!r}")

    assert "PLAIN-DRIVER-OUTPUT" in without_it, f"缺席时该退回裸诊断: {without_it!r}"
    assert "no lomenterr" in without_it, (
        f"缺席时**必须说一句** —— 静默换掉渲染器正是这条纪律要挡的: {without_it!r}")
    print("      启动器: 在场则渲染(且不吃裸行), 缺席则退回并明说")


# ---------------------------------------------------------------- 入口


def main() -> int:
    print(f"loment_err_test: {len(TESTS)} 条\n")
    bad = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            bad += 1
            print(f"  FAIL  {name}: {e}")
        except Exception as e:                                   # noqa: BLE001
            bad += 1
            print(f"  ERR   {name}: {type(e).__name__}: {e}")
    print(f"\nloment_err_test: {len(TESTS) - bad}/{len(TESTS)} 通过")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
