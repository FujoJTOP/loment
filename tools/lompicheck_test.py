#!/usr/bin/env python3
# lompicheck_test.py — `loment lompicheck` 的判据 (docs/217)
#
# 这条命令是三块拼起来的，判据也按三块判:
#
#   1. **映射不许漂**（离线，不编译）—— 引擎里那两对根与三条根例外是**写死**的
#      （`loment_publish.REPOS["lompi"]` 的 `paths` + `renames` 的 Loment 版），所以
#      有一条判据从**发布清单**推出应有的样子再核引擎源码：清单加了第三棵子树而引擎
#      没跟上，这里就红。
#   2. **注册器真能生成**（离线）—— `--install` 出的两个文件：纯 ASCII、.cmd 是 CRLF、
#      POSIX 那份可执行、两边都把引擎的路径写对了。
#   3. **引擎真的会判**（要编译）—— 用**仓库里的参考编译器**把引擎编成本机原生程序
#      （与 `loment_cli_test` 同一条路：`lomentc` -> `lomelf`），然后拿本仓**真的**
#      `lompi/` 当源侧，造一个发布口目录出来跑：一致 / 改一个 / 删一个 / 多一个，
#      四种结论各一条。夹具是**真源码树**，不是手写的小玩意 —— 手写的那个不会告诉你
#      "170 个文件里第 3 个开始不对"。
#
# 运行: python tools/lompicheck_test.py   (退出码 0 = 全绿)

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomelf  # noqa: E402
import loment_publish  # noqa: E402
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "loment" / "tools" / "lompicheck.lomt"
INSTALLER = ROOT / "tools" / "lompicheck.py"
IS_WIN = sys.platform == "win32"
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------- 1. 映射

def _publish_pairs() -> list[tuple[str, str]]:
    """发布清单里"哪棵树搬到哪里"—— (源目录, 发布口里的目录)。"""
    spec = loment_publish.REPOS["lompi"]
    pairs = []
    for p in spec["paths"]:
        src = p
        for a, b in spec.get("renames", []):
            if src.startswith(a):
                src = b + src[len(a):]
                break
        pairs.append((p.rstrip("/"), src.rstrip("/")))
    return pairs


@test
def test_engine_names_every_root_the_publish_list_names():
    """引擎里那两对根必须**正好**是发布清单里的那两棵子树。

    引擎是 Loment 写的，引不了 Python 的 `REPOS`，所以那几条只能是字面量；这条判据就是
    那份"不能漂"的保证。清单改了而引擎没跟上 —— 这条红，而不是两个工具从此各说各话。
    """
    spec = loment_publish.REPOS["lompi"]
    pairs = _publish_pairs()
    assert len(pairs) == 2, (
        f"发布清单现在收 {len(pairs)} 棵子树 {pairs}——引擎只会认其中的 `lompi/` 与 "
        f"`.claude/skills/lompi/`，清单加一棵就得同步改 "
        f"`loment/tools/lompicheck.lomt` 的多根列表与这条判据")
    assert [s for s, _ in pairs] == ["lompi", ".claude/skills/lompi"], pairs
    assert spec["renames"] == [("lompi/", "")], (
        f"摊平规则变了: {spec['renames']}——引擎是按「lompi/ 摊平到根」写的")

    src = SRC.read_text(encoding="utf-8")
    for sdir, _ in pairs:
        assert f'"/{sdir}"' in src, (
            f"引擎源码里没有 `/{sdir}` 这个根 —— 发布清单里有它。"
            f"改 `loment/tools/lompicheck.lomt` 的根列表")
    # 根上的三条例外: 生成物 / clone 自己的元数据 / 属于另一对的子树。
    gen = "README.md" if spec.get("readme") else None
    assert gen is not None and f'"{gen}"' in src, (
        f"发布工具会自己写根上的 {gen}，引擎必须当它是例外")
    for lit in (".git", ".claude"):
        assert f'"{lit}"' in src, f"引擎没把根上的 `{lit}` 当例外"


@test
def test_installer_and_engine_agree_on_the_paths():
    """安装器生成的注册器指的那个引擎，得就是仓库里这个源文件。"""
    src = SRC.read_text(encoding="utf-8")
    ins = INSTALLER.read_text(encoding="utf-8")
    assert SRC.exists() and src.startswith("// loment/tools/lompicheck.lomt"), SRC
    assert "loment/tools/lompicheck.lomt" in ins, "安装器没点名引擎的源文件"
    assert "loment/build/lompicheck" in ins, "安装器没点名引擎的产物路径"
    assert "module lompicheck" in src, "引擎没有 module 声明"
    # 命令名 = 文件名去掉前缀: `loment lompicheck` -> `loment-lompicheck`
    for name in ("loment-lompicheck", "loment-lompicheck.cmd"):
        assert name in ins, f"安装器不生成 {name}"


# ---------------------------------------------------------------- 2. 注册器

@test
def test_install_writes_two_usable_registrants():
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        r = subprocess.run([sys.executable, str(INSTALLER), "--install", str(td)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", shell=False, timeout=120)
        assert r.returncode == 0, f"安装器退出 {r.returncode}: {r.stderr[-300:]}"
        sh, cmd = td / "loment-lompicheck", td / "loment-lompicheck.cmd"
        assert sh.exists() and cmd.exists(), sorted(p.name for p in td.iterdir())

        for p in (sh, cmd):
            raw = p.read_bytes()
            assert not any(c > 127 for c in raw), f"{p.name} 里有非 ASCII 字节"
            assert "loment/build/lompicheck" in raw.decode("utf-8", "replace")
        assert b"\r\n" in cmd.read_bytes(), ".cmd 得是 CRLF（cmd.exe 按行解析）"
        assert b"\r\n" not in sh.read_bytes(), "POSIX 注册器不该有 CRLF"
        # **autocrlf 那条**: 发布口仓库没有 .gitattributes, 默认设置会把工作树翻成 CRLF,
        # 于是每个文件都被报成"不同"。注册器必须显式把它关掉。
        for p in (sh, cmd):
            assert "core.autocrlf=false" in p.read_text(encoding="utf-8", errors="replace"), (
                f"{p.name} 没有关掉 autocrlf —— Windows 上会把一个正常的发布口报成"
                f"170 处差异")
        # POSIX 那份真能拉起引擎（--help 都行: 它只要别崩在 shell 语法上）
        sh_bin = shutil.which("bash") or shutil.which("sh")
        if sh_bin:
            r = subprocess.run([sh_bin, "-n", str(sh)], capture_output=True, text=True,
                               timeout=60, shell=False)
            assert r.returncode == 0, f"注册器不是合法的 sh: {r.stderr[-300:]}"
        else:
            print("      (跳过: 本机没有 bash/sh，验不了脚本语法)")


# ---------------------------------------------------------------- 3. 引擎

_built: Path | None = None


def _build() -> Path:
    """把引擎编成本机原生程序（与 `loment_cli_test` 同一条路，用**仓库里的参考编译器**）。"""
    global _built
    if _built is not None:
        return _built
    mod = lomentc.load(SRC)
    deps = lomentc.resolve_deps(mod, ROOT, SRC.parent, entry=SRC)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"引擎自己检查不过: {errs[:3]}"
    ll = lomentc.emit_llvm(mod, ROOT, deps)
    raw = (lomelf.compile_pe if IS_WIN else lomelf.compile_ll)(ll)[0]
    assert raw, "链接器产出为空"
    td = Path(tempfile.mkdtemp(prefix="lompicheck-"))
    exe = td / ("lompicheck.exe" if IS_WIN else "lompicheck")
    exe.write_bytes(raw)
    exe.chmod(0o755)
    _built = exe
    return exe


def _make_outlet(td: Path) -> Path:
    """造一个"发布口"目录: `lompi/` 摊平到根 + `.claude/skills/lompi/` 原地 + 生成的 README。

    内容直接**拷自本仓真的那两棵树** —— 不手写夹具（手写的不会告诉你 170 个文件里
    从哪一个开始不对）。
    """
    out = td / "outlet"
    out.mkdir()
    for p in (ROOT / "lompi").iterdir():
        if p.is_dir():
            shutil.copytree(p, out / p.name)
        else:
            shutil.copy2(p, out / p.name)
    dst = out / ".claude" / "skills" / "lompi"
    dst.parent.mkdir(parents=True)
    shutil.copytree(ROOT / ".claude" / "skills" / "lompi", dst)
    # 发布工具自己写的那个文件: 引擎必须无视它
    (out / "README.md").write_text("# generated by loment_publish\n", encoding="utf-8")
    # clone 自己的元数据: 同理
    (out / ".git").mkdir()
    (out / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    return out


def _run(outlet: Path, *extra: str) -> tuple[int, str, str]:
    exe = _build()
    r = subprocess.run([str(exe), str(ROOT), str(outlet), *extra],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", shell=False, timeout=300)
    return r.returncode, r.stdout, r.stderr


@test
def test_engine_verdicts_and_exit_codes():
    """一致 / 改一个 / 删一个 / 多一个 —— 四种结论各一条，用本仓真的 `lompi/` 当源侧。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        outlet = _make_outlet(td)

        rc, out, err = _run(outlet)
        assert rc == 0, f"造出来的发布口应当一致: rc={rc}\n{out}\n{err[-300:]}"
        assert "[OK]" in out and "identical" in out, out
        assert "extra" not in out, f"README.md / .git 不该被当成多出来的东西:\n{out}"

        # 改一个
        victim = outlet / "lpi_cli.lomt"
        victim.write_bytes(victim.read_bytes() + b"\n// edited\n")
        rc, out, _ = _run(outlet)
        assert rc == 1, f"内容不同应当退出 1: rc={rc}\n{out}"
        assert "differs  lpi_cli.lomt" in out, out
        shutil.copy2(ROOT / "lompi" / "lpi_cli.lomt", victim)

        # 删一个
        gone = outlet / "lpi_test.lomt"
        saved = gone.read_bytes()
        gone.unlink()
        rc, out, _ = _run(outlet)
        assert rc == 1 and "missing  lpi_test.lomt" in out, out
        gone.write_bytes(saved)

        # 多一个（文件与整棵目录各一次）
        (outlet / "stray.lomt").write_text("x\n", encoding="utf-8")
        (outlet / "brandnew").mkdir()
        (outlet / "brandnew" / "x.lomt").write_text("y\n", encoding="utf-8")
        rc, out, _ = _run(outlet)
        assert rc == 1, out
        assert "extra    stray.lomt" in out, out
        assert "extra-dir brandnew" in out, out
        assert "2 extra" in out, out

        # 一致那一次报的总数: 与真的文件数对得上（源侧 170 个——不是"大概")
        n_src = len([p for p in (ROOT / "lompi").rglob("*") if p.is_file()])
        n_src += len([p for p in (ROOT / ".claude" / "skills" / "lompi").rglob("*")
                      if p.is_file()])
        (outlet / "stray.lomt").unlink()
        shutil.rmtree(outlet / "brandnew")
        rc, out, _ = _run(outlet)
        assert rc == 0 and f"{n_src} file(s) identical" in out, f"期望 {n_src}\n{out}"


@test
def test_engine_usage_and_stale_note():
    """argv 的三种形状: 参数不够要报用法; 第 4 个参数是 `stale` 时要说清这份可能是旧的。"""
    with tempfile.TemporaryDirectory() as tds:
        outlet = _make_outlet(Path(tds))
        exe = _build()

        r = subprocess.run([str(exe)], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", shell=False, timeout=120)
        assert r.returncode == 2, f"没有参数应当退出 2，实际 {r.returncode}"
        assert "usage:" in r.stderr, r.stderr[-200:]

        rc, out, _ = _run(outlet, "stale")
        assert rc == 0, out
        assert "could not refresh" in out, f"stale 时应当说明这份可能是旧的:\n{out}"
        rc, out, _ = _run(outlet, "fresh")
        assert "could not refresh" not in out, out


def main() -> int:
    failed = []
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, e))
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\nlompicheck_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
