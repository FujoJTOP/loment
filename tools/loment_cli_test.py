#!/usr/bin/env python3
# loment_cli_test.py — 自举 CLI 前端 loment-cli 的判据 (docs/169)
#
# 判的是 loment/tools/lomcli.lomt 链出来的**可执行文件**本身: 在本机原生跑, 逐条验命令的
# 输出、退出码、与"算出来的"文本 (sha256 对 hashlib, 行数对 Python 自己数)。**不比对 Python
# 实现** —— 这些命令没有 Python 版, 它自己就是实现 (与 lomfmt/lompkg 的孪生判据不同类)。
#
# 另有一条**防漂移**判据 (test_launcher_and_catalog_do_not_drift): 启动器里按名处理/转发的
# 命令, 必须与 `loment commands` 打出来的目录一致 —— 启动器有两份 (bash + batch), 命令面
# 又在第三处 (Loment 源码), 三处不同步就是"help 里没有、但确实能敲"的那种烂。
#
# 运行: python tools/loment_cli_test.py   (退出码 0 = 全绿)

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402
import lomelf  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "loment" / "tools" / "lomcli.lomt"
IS_WIN = sys.platform == "win32"
#: 目录里至少要有这么多命令 (用户 2026-09-15 的要求: 至少 30 条)
CATALOG_FLOOR = 30
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------- 构建

_built: Path | None = None
_pkg: Path | None = None


def _build() -> Path:
    """建一次, 全测复用。在本机直接出目标格式: Windows 出 PE (能直接跑), 别的出 ELF。"""
    global _built, _pkg
    if _built is not None:
        return _built
    td = Path(tempfile.mkdtemp(prefix="lomcli-"))
    mod = lomentc.load(SRC)
    deps = lomentc.resolve_deps(mod, ROOT, SRC.parent, entry=SRC)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"lomcli.lomt 自己检查不过: {errs[:3]}"
    ll = lomentc.emit_llvm(mod, ROOT, deps)
    raw = (lomelf.compile_pe if IS_WIN else lomelf.compile_ll)(ll)[0]
    assert raw, "链接器产出为空"
    # 放进一个**假包布局**里: CLI 靠 argv[0] 自定位, share/loment/version 要能找得到
    pkg = td / "pkg"
    (pkg / "bin").mkdir(parents=True)
    (pkg / "share" / "loment" / "examples").mkdir(parents=True)
    exe = pkg / "bin" / ("loment-cli.exe" if IS_WIN else "loment-cli")
    exe.write_bytes(raw)
    exe.chmod(0o755)
    (pkg / "share" / "loment" / "version").write_text(
        "Loment 0.1.4 Alpha2.3 (0.1.4-alpha2.3), commit 0123456\nbuild 2026-09-15\n",
        encoding="utf-8", newline="\n")
    (pkg / "share" / "loment" / "examples" / "tour.lomt").write_text(
        "module tour\n\nfn _start() {\n    syscall4(60, 0, 0, 0);\n}\n",
        encoding="utf-8", newline="\n")
    _built, _pkg = exe, pkg
    return exe


def _run(args: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    exe = _build()
    r = subprocess.run([str(exe), *args], cwd=str(cwd or exe.parent),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", shell=False, timeout=120)
    return r.returncode, r.stdout, r.stderr


def _names() -> list[str]:
    rc, out, _ = _run(["commands"])
    assert rc == 0, f"commands 退出 {rc}"
    return [x for x in out.splitlines() if x.strip()]


def _no_color() -> list[str]:
    return ["--no-color"]


# ---------------------------------------------------------------- 构建与入口

@test
def test_builds_and_has_entry():
    """链出的二进制非空, 且能跑起来 (没有 _start 的话链接器自己就会拒)。"""
    exe = _build()
    assert exe.stat().st_size > 50000, f"产物太小: {exe.stat().st_size}"
    rc, _, _ = _run(["version"])
    assert rc == 0, f"version 退出 {rc}"


@test
def test_reference_backend_agrees_with_selfhost_ir():
    """自举镜编 lomcli.lomt 的 IR, 与参考实现逐字节相同。

    这条保证 lomcli 是**自举链里的一等公民**: 装发行包时它由 stage1 编出来, 不是靠
    Python 特供。用的是发行包构建的同一条路 (loment_dist.build_stage1)。
    没有 clang 时跳过 —— stage1 要靠 clang 链一次。
    """
    clang = shutil.which("clang") or r"C:\Program Files\LLVM\bin\clang.exe"
    if not (clang and Path(clang).exists()):
        print("      SKIP: 无 clang")
        return
    sys.path.insert(0, str(ROOT / "tools"))
    import loment_dist  # noqa: E402
    stage1 = loment_dist.build_stage1()
    want = lomentc.emit_llvm(*_load_src())
    r = subprocess.run([str(stage1), SRC.relative_to(ROOT).as_posix()],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", shell=False, timeout=300)
    assert r.returncode == 0, f"stage1 编译失败: {r.stderr[-300:]}"
    # 参考经 Python stdout 出去时会被 Windows 文本模式翻成 CRLF, 镜直接 write 是 LF ——
    # 读时统一成 LF 再比 (语义上两边都是 LF)。
    got = r.stdout.replace("\r\n", "\n")
    assert got == want, (
        f"自举镜与参考的 IR 不一致 (want {len(want)}B got {len(got)}B)")


def _load_src():
    mod = lomentc.load(SRC)
    deps = lomentc.resolve_deps(mod, ROOT, SRC.parent, entry=SRC)
    return mod, ROOT, deps


# ---------------------------------------------------------------- 目录与帮助

@test
def test_catalog_has_at_least_thirty_commands():
    ns = _names()
    assert len(ns) >= CATALOG_FLOOR, f"命令只有 {len(ns)} 条 (要求 >= {CATALOG_FLOOR})"
    for must in ("help", "codes", "explain", "syntax", "builtins", "stat", "grep",
                 "hash", "ls", "tree", "new", "examples", "doctor", "color"):
        assert must in ns, f"目录里少了 {must}"


@test
def test_help_mentions_every_command():
    """`help` 总览里必须出现 `commands` 列的每一个名字 —— 否则就是"能敲但查不到"。"""
    _, out, _ = _run(["help"] + _no_color())
    missing = [n for n in _names() if not re.search(rf"\b{re.escape(n)}\b", out)]
    assert not missing, f"help 里没提到: {missing}"


@test
def test_every_catalog_command_is_actually_dispatchable():
    """`commands` 列出来的每一条都必须真能跑 —— 不能有"查得到、敲了说未知命令"的。

    这条是写 lib/pkg 时踩出来的: 它们当时列在目录里, 但发行包根本没有这两条
    (库系统在仓库侧, 而且是 Python), 敲下去只会得到"未知命令"。
    """
    bad = []
    for n in _names():
        rc, _, err = _run([n] + _no_color())
        if "未知命令" in err:
            bad.append(n)
    assert not bad, f"目录里列了但敲不了: {bad}"


@test
def test_help_page_for_one_command():
    rc, out, _ = _run(["help", "grep"] + _no_color())
    assert rc == 0 and "PAT" in out and "FILE" in out, out[:200]
    # 表里有一行、但没有详细页的命令, 不能崩, 也要给条出路
    rc, out, _ = _run(["help", "caps"] + _no_color())
    assert rc == 0, rc


@test
def test_every_command_outputs_pure_ascii():
    """**每一条命令的输出都必须是纯 ASCII。**

    用户 2026-09-15 实测报的乱码（八个感叹号）：Windows 上 PE 把字节直接写进控制台，
    而控制台按**当前代码页**解 —— 中文 Windows 是 936(GBK)，于是 UTF-8 的中文被按 GBK
    解成 `婧愮爜缁熻`。垫片**没有 `WriteConsoleW`**，程序这边没有任何补救手段。

    ASCII 是唯一**在任何代码页下都解码成同一个结果**的集合，所以这条不是风格问题：
    非 ASCII 就算"在我这台机器上看着是好的"，到了 936 的控制台上就是乱码。

    lompi 线已经因为同一条把它的输出全改成纯 ASCII 了 —— 这里是同一个坑的另一半。
    """
    bad = []
    for n in _names():
        rc, out, err = _run([n] + _no_color())
        # delegate 的几条（ir/build/... 由启动器转发）只打一行说明，也要守
        t = out + err
        if not t.isascii():
            where = sorted({c for c in t if ord(c) > 127})[:6]
            bad.append((n, where))
    assert not bad, f"这些命令的输出带非 ASCII（936 控制台下必乱码）: {bad}"


@test
def test_source_has_no_non_ascii_string_literals():
    """静态判据：`lomcli.lomt` 的字符串字面量里不许有非 ASCII。

    上面那条动态判据只跑 38 次调用 —— 没走到的分支（某个错误路径、某个 `help <cmd>`）
    里面藏着中文它抓不到。这条按源码扫，一个都不放过。注释里的中文无所谓：注释不进二进制。
    """
    NL = chr(10)
    Q = chr(34)
    BS = chr(92)
    src = SRC.read_text(encoding="utf-8")
    body = re.sub(r"/[*].*?[*]/", "", src, flags=re.S)
    body = re.sub("//[^" + NL + "]*", "", body)
    strlit = re.compile(Q + "(?:[^" + BS + Q + "]|" + BS + BS + ".)*" + Q)
    bad = []
    for i, line in enumerate(body.split(NL), 1):
        for m in strlit.finditer(line):
            if any(ord(c) > 127 for c in m.group(0)):
                bad.append((i, m.group(0)[:60]))
    assert not bad, f"这些字符串字面量里有非 ASCII（936 控制台下必乱码）: {bad[:5]}"

@test
def test_unknown_command_is_an_error():
    rc, out, err = _run(["frobnicate"] + _no_color())
    assert rc == 2, f"未知命令应退 2, 实得 {rc}"
    assert "unknown command" in err and "frobnicate" in err


# ---------------------------------------------------------------- 颜色

@test
def test_color_on_by_default_and_off_with_flag():
    _, on, _ = _run(["about"])
    assert "\x1b[" in on, "默认应当上色"
    _, off, _ = _run(["--no-color", "about"])
    assert "\x1b[" not in off, "--no-color 之后不该还有转义序列"


@test
def test_color_flag_anywhere_in_argv_does_not_eat_the_command():
    """`loment --no-color stat F` 里 stat 仍要被当成命令 (位置无关的全局开关)。"""
    f = _pkg / "share" / "loment" / "examples" / "tour.lomt"
    rc, out, err = _run(["--no-color", "stat", str(f)])
    assert rc == 0, f"rc={rc} err={err[:200]}"
    assert "Source statistics" in out


# ---------------------------------------------------------------- 真算出来的东西

@test
def test_hash_matches_hashlib():
    f = _pkg / "share" / "loment" / "examples" / "tour.lomt"
    rc, out, _ = _run(["hash", str(f)])
    assert rc == 0
    assert out.strip() == hashlib.sha256(f.read_bytes()).hexdigest()


@test
def test_stat_counts_match_what_python_counts():
    f = _pkg / "share" / "loment" / "examples" / "tour.lomt"
    rc, out, _ = _run(["stat", str(f)] + _no_color())
    assert rc == 0
    raw = f.read_bytes()
    lines = raw.decode().split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    nums = [int(m) for m in re.findall(r"\b(\d+)\s*$", out, re.M)]
    assert str(len(raw)) in out, f"字节数不对: {out}"
    assert str(len(lines)) in out, f"行数不对 (Python 数出 {len(lines)}): {out}"


@test
def test_cat_prints_numbered_lines():
    f = _pkg / "share" / "loment" / "examples" / "tour.lomt"
    rc, out, _ = _run(["cat", str(f)] + _no_color())
    assert rc == 0
    assert out.splitlines()[0].strip().startswith("1"), out[:80]
    assert len(out.splitlines()) == 5, f"行数不对: {out!r}"


@test
def test_grep_line_numbers_and_exit_codes():
    d = Path(tempfile.mkdtemp(prefix="lomcli-grep-"))
    f = d / "x.lomt"
    f.write_text("one\nalpha\ntwo\nalpha\nthree\n", encoding="utf-8", newline="\n")
    rc, out, _ = _run(["grep", "alpha", str(f)] + _no_color())
    assert rc == 0, rc
    assert [int(x.split(":")[0]) for x in out.splitlines()] == [2, 4], out
    rc, _, _ = _run(["grep", "nope", str(f)] + _no_color())
    assert rc == 1, f"没命中应退 1, 实得 {rc}"
    rc, _, err = _run(["grep", "onlypat"] + _no_color())
    assert rc == 2, f"缺 FILE 应退 2, 实得 {rc}"


@test
def test_count_and_fns_on_a_known_file():
    d = Path(tempfile.mkdtemp(prefix="lomcli-cnt-"))
    f = d / "x.lomt"
    f.write_text(
        "module x\n\nstruct S {\n    a: u32,\n}\n\n"
        "enum E {\n    A,\n}\n\n"
        "fn one() -> u32 {\n    return 1;\n}\n\n"
        "pub fn two(a: u32) -> u32 {\n    return a;\n}\n",
        encoding="utf-8", newline="\n")
    rc, out, _ = _run(["count", str(f)] + _no_color())
    assert rc == 0
    got = dict(re.findall(r"^\s*(\S+)\s+(\d+)\s*$", out, re.M))
    assert got.get("fn") == "2" and got.get("struct") == "1" and got.get("enum") == "1", out
    rc, out, _ = _run(["fns", str(f)] + _no_color())
    assert rc == 0
    assert "fn one() -> u32" in out and "fn two(a: u32) -> u32" in out, out


@test
def test_tokens_picks_up_strings_and_comments():
    d = Path(tempfile.mkdtemp(prefix="lomcli-tok-"))
    f = d / "x.lomt"
    f.write_text('module x\n\n// 注释\nfn f() -> u32 {\n    let s: str = "ab";\n    return 1;\n}\n',
                 encoding="utf-8", newline="\n")
    rc, out, _ = _run(["tokens", str(f)] + _no_color())
    assert rc == 0
    got = dict(re.findall(r"^\s*(\S+)\s+(\d+)\s*$", out, re.M))
    assert got.get("strings") == "1", out
    assert got.get("comments") == "1", out
    assert got.get("keywords") and int(got["keywords"]) >= 4, out


# ---------------------------------------------------------------- 文件与目录

@test
def test_missing_file_is_a_clean_error_not_a_crash():
    rc, out, err = _run(["cat", "/definitely/not/here.lomt"] + _no_color())
    assert rc == 1, f"rc={rc}"
    assert "cannot open" in err, err[:200]


@test
def test_ls_and_tree_mark_directories():
    d = Path(tempfile.mkdtemp(prefix="lomcli-ls-"))
    (d / "sub").mkdir()
    (d / "sub" / "deep").mkdir()
    (d / "sub" / "deep" / "f.lomt").write_text("module f\n", encoding="utf-8")
    (d / "a.lomt").write_text("module a\n", encoding="utf-8")
    rc, out, _ = _run(["ls", str(d)] + _no_color())
    assert rc == 0
    assert "sub/" in out and "a.lomt" in out and "sub\n" not in out, out
    rc, out, _ = _run(["tree", str(d)] + _no_color())
    assert rc == 0
    assert "deep/" in out and "f.lomt" in out, out
    assert re.search(r"^\s+f\.lomt", out, re.M), "tree 的缩进没体现层级"


@test
def test_new_writes_a_skeleton_that_compiles():
    """`new` 出来的东西必须**真能编** —— 生成的骨架过了参考实现的 check + emit。"""
    d = Path(tempfile.mkdtemp(prefix="lomcli-new-"))
    rc, out, _ = _run(["new", "hello"], cwd=d)
    assert rc == 0, out
    f = d / "hello.lomt"
    assert f.exists(), "没写出 hello.lomt"
    mod = lomentc.load(f)
    deps = lomentc.resolve_deps(mod, ROOT, f.parent, entry=f)
    assert not lomentc.check(mod, deps=deps), "生成的骨架检查不过"
    assert lomentc.emit_llvm(mod, ROOT, deps), "生成的骨架发射不出 IR"
    assert "_start" in f.read_text(encoding="utf-8")


@test
def test_new_refuses_to_overwrite():
    d = Path(tempfile.mkdtemp(prefix="lomcli-new2-"))
    (d / "hi.lomt").write_text("module hi\n", encoding="utf-8")
    rc, _, err = _run(["new", "hi"], cwd=d)
    assert rc == 1, f"已存在应当退 1, 实得 {rc}"
    assert (d / "hi.lomt").read_text(encoding="utf-8") == "module hi\n", "把原文件覆盖了"


# ---------------------------------------------------------------- 包内自定位

@test
def test_version_reads_share_version():
    rc, out, _ = _run(["version"])
    assert rc == 0
    assert out.startswith("Loment 0.1.4 Alpha2.3"), out
    assert "commit 0123456" in out


@test
def test_doctor_reports_missing_tools_then_green_when_present():
    """体检必须有**分辨力**: 缺组件时红且退 1, 组件齐了就绿且退 0。"""
    rc, out, _ = _run(["doctor"] + _no_color())
    assert rc == 1, f"缺驱动时应当退 1, 实得 {rc}"
    assert "MISSING" in out
    # 把六个名字都补上 (内容无所谓, 只要有这个文件)
    for n in ("loment-driver", "loment-lsp", "loment-fmt", "loment-doc",
              "loment-lomelf", "loment-cli"):
        p = _pkg / "bin" / (n + ".exe" if IS_WIN else n)
        if not p.exists():
            p.write_bytes(b"stub")
    rc, out, _ = _run(["doctor"] + _no_color())
    assert rc == 0, f"组件齐了还退 {rc}: {out}"
    assert "all green" in out


@test
def test_where_resolves_and_reports_the_expected_path():
    rc, out, _ = _run(["where", "driver"])
    assert rc == 0, rc
    assert out.strip().endswith("loment-driver" + (".exe" if IS_WIN else "")), out
    rc, _, err = _run(["where", "nosuchtool"] + _no_color())
    assert rc == 2, rc
    assert "no such tool" in err


@test
def test_examples_and_example_read_the_package():
    rc, out, _ = _run(["examples"] + _no_color())
    assert rc == 0, rc
    assert "tour.lomt" in out
    rc, out, _ = _run(["example", "tour"])
    assert rc == 0 and "module tour" in out, out[:200]
    rc, _, err = _run(["example", "nope"] + _no_color())
    assert rc == 1 and "no such example" in err, err[:200]


# ---------------------------------------------------------------- 参考页

@test
def test_codes_lists_all_nineteen():
    rc, out, _ = _run(["codes"] + _no_color())
    assert rc == 0
    for i in range(1, 20):
        assert f"E{i} " in out or f"E{i}\n" in out, f"错误码表里没有 E{i}"


@test
def test_explain_accepts_three_spellings_and_rejects_junk():
    for spelling in ("E4", "e4", "4"):
        rc, out, _ = _run(["explain", spelling] + _no_color())
        assert rc == 0 and "Capability domain" in out, (spelling, out[:120])
    rc, _, err = _run(["explain", "E99"] + _no_color())
    assert rc == 2 and "E1..E19" in err, err[:120]
    rc, _, _ = _run(["explain"] + _no_color())
    assert rc == 2


@test
def test_reference_pages_are_nonempty():
    for c in ("syntax", "builtins", "types", "keywords", "caps", "cheat", "about", "env"):
        rc, out, _ = _run([c] + _no_color())
        assert rc == 0, f"{c} 退出 {rc}"
        assert len(out.strip()) > 60, f"{c} 输出太短: {out!r}"


# ---------------------------------------------------------------- 防漂移

@test
def test_launcher_and_catalog_do_not_drift():
    """启动器按名处理/转发的命令, 必须在 `loment commands` 的目录里。

    三处会各自漂: bash 启动器 (bin/loment)、batch 启动器 (bin/loment.cmd)、命令目录
    (lomcli.lomt 的 names_dump)。前两处是**生成**的 (loment_dist 里的常量), 所以直接
    读那两份常量即可 —— 不用真去打一个包。
    """
    sys.path.insert(0, str(ROOT / "tools"))
    import loment_dist  # noqa: E402
    catalog = set(_names())

    sh = loment_dist.LAUNCHER_SH
    case = re.search(r"case \"\$\{1:-help\}\" in(.*?)\nesac", sh, re.S)
    assert case, "解析不出 bash 启动器的 case"
    sh_names: set[str] = set()
    for line in case.group(1).splitlines():
        m = re.match(r"\s{4}([A-Za-z0-9|_-]+)\)", line)
        if m:
            for alt in m.group(1).split("|"):
                if not alt.startswith("-") and alt != "*":
                    sh_names.add(alt)
    assert sh_names, "bash 启动器里一个命令都没解析出来"
    missing = sorted(sh_names - catalog)
    assert not missing, f"bash 启动器处理了但目录里没有: {missing}"

    cmd = loment_dist.LAUNCHER_CMD
    cmd_names = set(re.findall(r'if "%cmd%"=="([A-Za-z0-9_-]+)" goto', cmd))
    cmd_names |= set(re.findall(r'if "%cmd%"=="(-[A-Za-z-]+)" goto', cmd))
    cmd_names = {n for n in cmd_names if not n.startswith("-")}
    assert cmd_names, "batch 启动器里一个命令都没解析出来"
    missing = sorted(cmd_names - catalog)
    assert not missing, f"cmd 启动器处理了但目录里没有: {missing}"

    # 两个启动器认得的命令集必须一致 —— 不然 Windows 与 Linux 行为分叉
    assert sh_names == cmd_names, (
        f"两个启动器不一致: 只有 bash 有 {sorted(sh_names - cmd_names)}, "
        f"只有 cmd 有 {sorted(cmd_names - sh_names)}")


@test
def test_launcher_forwards_unknown_to_cli():
    """启动器的兜底必须是**转发给 loment-cli**, 不是自己再写一份 usage。"""
    sys.path.insert(0, str(ROOT / "tools"))
    import loment_dist  # noqa: E402
    assert 'exec "$cli" "$@"' in loment_dist.LAUNCHER_SH
    assert '"%here%loment-cli.exe" %*' in loment_dist.LAUNCHER_CMD
    # 两个启动器都必须是纯 ASCII (PowerShell 5.1 按 ANSI 读无 BOM 脚本, docs/157 §3.4)
    for name, txt in (("launcher.sh", loment_dist.LAUNCHER_SH),
                      ("launcher.cmd", loment_dist.LAUNCHER_CMD)):
        bad = [(i, c) for i, c in enumerate(txt) if ord(c) > 127]
        assert not bad, f"{name} 里有非 ASCII 字符: {bad[:3]}"


def main() -> int:
    failed = []
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, e))
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\nloment_cli_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
