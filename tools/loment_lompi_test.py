#!/usr/bin/env python3
# loment_lompi_test.py — lompi 随包发行的判据 (docs/170)
#
# lompi 是**另一条线**用 Loment 写的包管理器，源码正本在开发者的工作区（仓内 `lompi/`
# 是随包发布的快照，两边由 `tools/lompi_sync.py` 校验）。本文件判的是"它作为**发行件的
# 一部分**是好的"：编得出来、跑得对、版本对、装法对，以及**边界没被越** ——
# 它是独立命令，不是 `loment` 的子命令（用户 2026-09-15 明确）。
#
# 不判 lompi 自己的功能对不对（那是它自己的事，它有自己的自检驱动）—— 只判**随包**这一层。
#
# 运行: python tools/loment_lompi_test.py   (退出码 0 = 全绿)

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402
import lomelf  # noqa: E402
import lompi_sync  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "lompi"
ENTRY = SRC_DIR / "lompi.lomt"
IS_WIN = sys.platform == "win32"
#: 随包发行的 lompi 版本 —— 用户 2026-09-15 定「完全稳定 0.1.0」。
#: 它**同时写在 lpi_cli.lomt 里**（那是真源），这条常量的作用是：**改版本要过一次手动确认**，
#: 不能悄悄漂（红了就说明被测的那个版本号 ≠ 我们说好要发的那个）。
STABLE_VERSION = "0.1.0"
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------- 构建

_skip = ""


def _clang() -> str | None:
    p = shutil.which("clang") or r"C:\Program Files\LLVM\bin\clang.exe"
    return p if p and Path(p).exists() else None


_cache: dict[str, Path] = {}


def _build_entry(stem: str) -> Path | None:
    """用**发行包同一条路**（stage1）建 `lompi/<stem>.lomt`，本机原生格式。

    走 stage1 而不是参考实现，是因为发行包就是用它编的 —— 判据要判"发出去的那个东西"。
    没有 clang 时返回 None（stage1 要靠 clang 链一次）。
    """
    global _skip
    if stem in _cache:
        return _cache[stem]
    if _skip:
        return None
    if not _clang():
        _skip = "无 clang（stage1 要靠 clang 链一次）"
        return None
    import loment_dist  # noqa: E402
    stage1 = loment_dist.build_stage1()
    # 自举镜按 **CWD** 解析路径形式的 `use "lpi_cli.lomt"` —— 必须在 lompi/ 里编。
    r = subprocess.run([str(stage1), f"{stem}.lomt"], cwd=str(SRC_DIR),
                       capture_output=True, shell=False, timeout=300)
    if r.returncode != 0 or not r.stdout:
        raise AssertionError(f"stage1 编 {stem} 失败: {r.stderr[-300:]!r}")
    ll = r.stdout.decode("utf-8", "replace").replace(chr(13) + chr(10), chr(10))
    raw = (lomelf.compile_pe if IS_WIN else lomelf.compile_ll)(ll)[0]
    assert raw, "链接产物为空"
    td = Path(tempfile.mkdtemp(prefix="lompi-"))
    exe = td / (f"{stem}.exe" if IS_WIN else stem)
    exe.write_bytes(raw)
    exe.chmod(0o755)
    _cache[stem] = exe
    return exe


def _build() -> Path | None:
    return _build_entry("lompi")


def _run_exe(stem: str, args: list[str]) -> tuple[int, str]:
    """跑 `lompi/<stem>.lomt` 编出来的二进制。CWD 一律是 lompi/ —— `index fixture/store`
    里的 store 路径是相对的，换目录就找不着。"""
    exe = _build_entry(stem)
    if exe is None:
        return -1, ""
    r = subprocess.run([str(exe), *args], cwd=str(SRC_DIR), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", shell=False,
                       timeout=180)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _run(args: list[str]) -> tuple[int, str]:
    return _run_exe("lompi", args)


# ---------------------------------------------------------------- 编得出来

@test
def test_reference_implementation_accepts_lompi():
    """参考实现必须能**检查** lompi。

    这条曾经是红的：lompi 里到处用 `0 as ptr`（空指针的惯用写法），而参考实现把整型
    **字面量**转 ptr 判成非法目标 —— 自举镜一直放行，于是同一份源码参考报错、打包版能编。
    2026-09-15 修掉（检查器 + 发射，缺一条都不行：只放行不修发射会发出非法 IR）。
    """
    mod = lomentc.load(ENTRY)
    deps = lomentc.resolve_deps(mod, ROOT, SRC_DIR, entry=ENTRY)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"参考实现检查不过: {errs[:3]}"


@test
def test_builds_with_the_shipped_toolchain():
    exe = _build()
    if exe is None:
        print(f"      SKIP: {_skip}")
        return
    assert exe.stat().st_size > 100000, f"产物太小: {exe.stat().st_size}"


# ---------------------------------------------------------------- 跑得对

@test
def test_self_test_driver_passes():
    """lompi 自带的**逐模块自检**在仓里这份上必须全绿 —— 这条线上最硬的一条。

    `lpi_test.lomt` 把 7 个模块（sys / txt / dir / sha / pkg / idn / cli）各自的
    `selftest_<模块>() -> u32` 汇总，**退出码即结论**。它比冒烟一条命令强得多：真的把每层
    都跑了一遍。跑的是**仓内快照**编出来的二进制，所以它同时证明快照是完整的、自洽的。

    `lpi_test.lomt` 不在发行包里（它不是 `bin/lompi` 的组成部分），但跟着一起收 ——
    留在这里当判据用，见 `tools/lompi_sync.py` 里 MODULES 的注解。
    """
    rc, out = _run_exe("lpi_test", [])
    if rc == -1:
        print(f"      SKIP: {_skip}")
        return
    assert rc == 0, f"自检退出 {rc}: {out[-400:]}"
    assert "LPI SELFTEST: PASS" in out, out[-400:]
    assert "failures: 0" in out, out[-400:]


@test
def test_index_lists_the_fixture_store():
    """拿仓里的 fixture 当 store 跑 `lompi index` —— 输出必须**真算出来**的那些内容。

    最要紧的一条是 **同一个库的两个版本给出两个不同的内容哈希**：这正是这套库系统的
    核心承诺（身份 = 内容，不是版本号）。夹具里 `mathutil` 有 0.1.0 与 0.2.0 两份。
    """
    rc, out = _run(["index", "fixture/store"])
    if rc == -1:
        print(f"      SKIP: {_skip}")
        return
    assert rc == 0, f"index 退出 {rc}: {out[:200]}"
    assert "5 package(s) in store" in out, out
    rows = [l for l in out.splitlines() if re.fullmatch(r"\S+ \S+ [0-9a-f]{16}", l.strip())]
    assert len(rows) == 5, f"认出来的包行数不对: {rows}"
    math = [r.split() for r in rows if r.split()[0] == "mathutil"]
    assert len(math) == 2, f"mathutil 应当有两份: {math}"
    assert math[0][1] != math[1][1], "两个版本号应当不同"
    assert math[0][2] != math[1][2], (
        f"**同名不同版本必须给不同身份**，实得 {math[0][2]} / {math[1][2]}")


@test
def test_check_has_discriminating_power():
    """`lompi check` 必须**分得清好坏** —— 合法库退 0 说 OK，坏库退 1 说 BAD。

    只在一条上取真值不算判据（"永远说 OK"也能过）。所以这里两边都取：拿仓库 fixture 里
    一个真库，再**现造一个坏的**（`foo.lomt` 里写 `module bar`，文件名与模块名不符 ——
    这是 lompi 自己列的规则之一），看它认不认。
    """
    rc, out = _run(["check", "fixture/store/mathutil/0.2.0"])
    if rc == -1:
        print(f"      SKIP: {_skip}")
        return
    assert rc == 0 and "[OK]" in out, f"合法库竟然不过: {out[:240]}"

    td = Path(tempfile.mkdtemp(prefix="lompi-bad-"))
    (td / "foo.lomt").write_text("module bar" + chr(10), encoding="utf-8", newline=chr(10))
    exe = _build()
    r = subprocess.run([str(exe), "check", str(td)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", shell=False, timeout=60)
    assert r.returncode == 1, f"坏库（模块名≠文件名）竟然过了: rc={r.returncode}"
    assert "[BAD]" in (r.stdout + r.stderr), (r.stdout + r.stderr)[:240]


@test
def test_show_reports_the_content_identity():
    """`lompi show` 报的身份是**完整 64 位十六进制** —— 内容哈希，不是版本号。"""
    rc, out = _run(["show", "fixture/store", "mathutil"])
    if rc == -1:
        print(f"      SKIP: {_skip}")
        return
    assert rc == 0, out[:200]
    m = re.search(r"^\s*id:\s+([0-9a-f]{64})\s*$", out, re.M)
    assert m, f"没找到 64 位内容哈希: {out[:240]}"


@test
def test_version_command_and_the_shipped_version_agree():
    """`lompi version` 打的那个号，必须是我们说好要发的那个。

    "检测最新的"落在两条上：① 版本号从**源码真源** `lpi_cli.lomt` 里解出来（不是这里
    另抄一份）；② 它与 `lompi version` 的实际输出一致。再拿 STABLE_VERSION 钉一次 ——
    改版本要过一次手动确认，不能悄悄漂。
    """
    src = (SRC_DIR / "lpi_cli.lomt").read_text(encoding="utf-8")
    m = re.search(r"lompi (\d+\.\d+\.\d+) - package manager", src)
    assert m, "在 lpi_cli.lomt 里找不到版本串（真源变了？）"
    found = m.group(1)
    assert found == STABLE_VERSION, (
        f"源码里的版本是 {found}，本判据钉的是 {STABLE_VERSION} —— "
        f"要发新版就改 STABLE_VERSION 并重出包")
    rc, out = _run(["version"])
    if rc == -1:
        print(f"      SKIP: {_skip}")
        return
    assert rc == 0 and out.strip() == f"lompi {STABLE_VERSION}", out[:120]


@test
def test_unknown_command_is_a_clean_error():
    rc, out = _run(["definitely-not-a-command"])
    if rc == -1:
        print(f"      SKIP: {_skip}")
        return
    assert rc != 0, "未知命令不该退 0"
    assert "unknown command" in out, out[:200]


# ---------------------------------------------------------------- 边界（这是用户定的）

@test
def test_lompi_is_not_a_loment_subcommand():
    """`loment` 的命令面里**不许**出现 lompi。

    lompi 随包一起装、就在 PATH 上，但它是**独立命令**，不是 Loment 的官方工具 ——
    所以 `loment help` / `loment commands` 里没有它，`loment <任何东西>` 也不转发给它。
    装在一起 ≠ 是同一件工具的部件（用户 2026-09-15 明确定的边界，docs/169 §2）。
    """
    cli = (ROOT / "loment" / "tools" / "lomcli.lomt").read_text(encoding="utf-8")
    i = cli.index("fn names_dump(")
    dump = cli[i:cli.index("\n}", i)]
    assert "lompi" not in dump, "lompi 混进了 loment 的命令目录"
    j = cli.index("fn catalog(")
    cat = cli[j:cli.index("\n}\n", j)]
    assert "lompi" not in cat, "lompi 混进了 loment 的 help 总览"
    for name, txt in (("launcher.sh", _dist().LAUNCHER_SH), ("launcher.cmd", _dist().LAUNCHER_CMD)):
        assert "lompi" not in txt, f"{name} 里出现了 lompi —— 启动器不该知道它"


def _dist():
    import loment_dist  # noqa: E402
    return loment_dist


# ---------------------------------------------------------------- 装法（与 loment skill 同一套）

@test
def test_package_carries_the_lompi_guide():
    d = _dist()
    assert d.SKILL_LOMPI == ".claude/skills/lompi/SKILL.md"
    repo = ROOT / d.SKILL_LOMPI
    assert repo.is_file(), "仓里没有 lompi 的指南正本"
    assert d._read(d.SKILL_LOMPI) == repo.read_bytes()
    # 随包发的是**自己**的 share 树，不塞进 share/loment/
    assert "share/lompi/skill/SKILL.md" in d._fresh_sources("linux")


@test
def test_installers_handle_the_lompi_guide_like_the_loment_one():
    """两条指南同一套装法，但**各自带自己的标记** —— 卸载一份不该动另一份。"""
    d = _dist()
    for name, txt in (("install.sh", d.INSTALL_SH), ("install.ps1", d.INSTALL_PS1)):
        for needle in ("skills/lompi", "skills\\lompi", "share/lompi/skill/SKILL.md",
                       "<!-- lompi:begin -->", "<!-- lompi:end -->"):
            if needle in txt:
                break
        else:
            raise AssertionError(f"{name} 里完全没提到 lompi 指南")
        assert "<!-- lompi:begin -->" in txt, f"{name} 没有 lompi 自己的标记"
        assert "<!-- lompi:end -->" in txt, f"{name} 没有 lompi 自己的标记"
    # 卸载要摘得掉
    assert "skills/lompi" in d.INSTALL_SH, "install.sh 卸载没摘 lompi 的指南"
    assert "skills\\lompi" in d.INSTALL_PS1, "install.ps1 卸载没摘 lompi 的指南"
    for txt in (d.INSTALL_SH, d.INSTALL_PS1):
        assert "lompi:begin -->.*?<!-- lompi:end" in txt or \
               "/<!-- lompi:begin -->/,/<!-- lompi:end -->/" in txt, "卸载没删 lompi 那段标记"


@test
def test_install_scripts_stay_pure_ascii():
    """安装脚本一律 ASCII（PowerShell 5.1 按 ANSI 读无 BOM 脚本，非 ASCII 会解析坏）。

    写 lompi 这一段时就踩了一次：注释里写了中文，装脚本立刻不再是纯 ASCII。
    """
    d = _dist()
    for name, txt in (("install.sh", d.INSTALL_SH), ("install.ps1", d.INSTALL_PS1),
                      ("install.cmd", d.INSTALL_CMD), ("launcher.sh", d.LAUNCHER_SH),
                      ("launcher.cmd", d.LAUNCHER_CMD)):
        bad = [c for c in txt if ord(c) > 127]
        assert not bad, f"{name} 里有非 ASCII 字符: {bad[:3]}"


# ---------------------------------------------------------------- 与正本的一致性

@test
def test_dev_copy_and_repo_copy_do_not_silently_drift():
    """仓内快照 vs 开发区正本 —— 不一致要吵，正本不在本机则**明说跳过**。

    这条就是 CLAUDE.md 那句「复制出第二份必然漂」的机器化：正本每次改完，仓里这份
    要么跟着同步，要么这条判据红。写它的时候它**当场就红了一次**（`lpi_pkg.lomt`）。
    """
    rc = lompi_sync.main([])
    assert rc == 0, (
        "仓内 lompi/ 与开发区正本不一致（或仓内那份不完整）—— "
        "跑 `python tools/lompi_sync.py --from-dev` 收进来")


def main() -> int:
    failed = []
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, e))
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\nloment_lompi_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
