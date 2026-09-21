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


# ---------------------------------------------------------------- 标准库随包（用户 2026-09-16）

#: 随包发的标准库：`std` 127 个模块 + `std.lomt` 门面 = 128 个 `.lomt`；`host` 7 个。
#: 各带一份 `pkg.lomp`，所以整棵树是 137 个文件。用户说的"127 个库"就是 std 的模块数。
STORE_DIR = ROOT / "lompi" / "store"
STORE_MODULES = {"std": 128, "host": 7}
STORE_FILES = 137


def _store_line(out: str) -> str:
    """从 `lompi config` 的输出里取 `store:` 那一行 —— 和安装器读的是同一行。"""
    for ln in out.replace("\r\n", "\n").split("\n"):
        if ln.startswith("store:"):
            return ln[len("store:"):].strip()
    return ""


def _lomt_count(d: Path, pkg: str) -> int:
    return len(list((d / pkg).glob("*/*.lomt")))


@test
def test_package_carries_the_whole_store():
    """137 个文件一件不少地进包，且 `--check` 盯得住它们（进了 _fresh_sources）。"""
    d = _dist()
    files = d._store_files()
    assert len(files) == STORE_FILES, f"随包的 store 是 {len(files)} 个文件，应为 {STORE_FILES}"
    for pkg, n in STORE_MODULES.items():
        got = [f for f in files if f.startswith(f"{pkg}/") and f.endswith(".lomt")]
        assert len(got) == n, f"{pkg} 有 {len(got)} 个 .lomt，应为 {n}"
        # 版本目录必须和 lompi 自己认的那个版本一致，否则装过去它看不见
        assert f"{pkg}/{STABLE_VERSION}/pkg.lomp" in files, f"{pkg} 缺 {STABLE_VERSION}/pkg.lomp"
    # 包里的路径（store 是 `<name>/<version>/`，别漏了版本那一层）
    for rel in (f"share/lompi/store/std/{STABLE_VERSION}/std.lomt",
                f"share/lompi/store/host/{STABLE_VERSION}/pkg.lomp"):
        assert rel in d.payload("linux", {}), f"{rel} 没进归档"
    # 源码直出：改了库里任何一个模块而不重打包，--check 要红
    fresh = d._fresh_sources("linux")
    missing = [f for f in files if f"share/lompi/store/{f}" not in fresh]
    assert not missing, f"这些 store 文件没被 _fresh_sources 盯住: {missing[:3]}"


@test
def test_release_manifest_covers_every_store_file():
    """发布清单必须**逐条**覆盖随包的 store —— 少一个就是漏发。

    `loment_release.GLOBS` 里那两条模式把版本号 `0.1.0` **写死了**（自举那边的 glob
    只认"一段目录 + 一个名字模式"，`**` 展开不出递归）。写死的代价是"库升版、清单没跟上"，
    而那种情况下 `loment_release --check` 会**看不见文件却照样绿** —— 所以钉在这里：
    库一改版，这条红。
    """
    d = _dist()
    import loment_release  # noqa: E402
    covered = {x["path"] for x in loment_release.build()["files"]}
    want = {f"lompi/store/{rel}" for rel in d._store_files()}
    missing = sorted(want - covered)
    assert not missing, (
        f"发布清单漏了 {len(missing)} 个 store 文件（例: {missing[:3]}）—— "
        "loment_release.GLOBS 与 loment/tools/lomrel.lomt 的 globs_text() 要同时改")
    assert loment_release.RELEASE == d.VER, "发行号两个真源不一致"


@test
def test_installers_ask_lompi_where_the_store_is():
    """装/卸都不许自己重推 cfg_root 的三条规则 —— 一律问 `lompi config`。

    重推一份的下场是**漂**：lompi 那边改了规则，装到别处的库它自己看不见，而这边
    一切正常。所以判据盯两件事：脚本里出现 `config` + `store:`（真的在问、真的在读
    那一行），且不出现 `.lompi`（那是 fallback 规则的尾巴，自己推才会写出来）。
    """
    d = _dist()
    for name, txt in (("install.sh", d.INSTALL_SH), ("install.ps1", d.INSTALL_PS1)):
        assert " config" in txt, f"{name} 没问 lompi config"
        assert "store:" in txt, f"{name} 没解析 lompi 打的那一行 store:"
        assert ".lompi" not in txt, (
            f"{name} 里出现了 `.lompi` —— 这是在自己重推 cfg_root 的 fallback 规则，"
            "该去问 lompi config")
        # 卸载也要**先问再删**（卸载时 bin/lompi 会被删掉，问晚了就问不着）
        assert txt.count(" config") >= 2 or txt.count("Get-LompiStore") >= 2, \
            f"{name} 只在装的时候问了，卸载没问"
    # 卸载只收回本包发过的 <name>/<version>，别人的版本不碰
    assert "store/lompi/store" in d.INSTALL_SH or "share/lompi/store" in d.INSTALL_SH


@test
def test_installed_store_is_discoverable_by_lompi():
    """端到端：真跑一遍 install，再问**装出来的那个 lompi**「你的 store 在哪」，去那儿查。

    不硬编码 cfg_root 的任何一条规则 —— 判据硬写哪条都会在别的机器上假红。**三条都跑**：

    | 前缀形状 | 命中 | 全局根 |
    |---|---|---|
    | `<沙箱>\\Loment` | 规则 2 (fallback) | `<前缀>\\.lompi` |
    | `<沙箱>\\AppData\\Local\\Loment` | 规则 0 | `<沙箱>\\AppData\\Local\\lompi` |
    | `<沙箱>\\Users\\bob\\Loment` | 规则 1 | `<沙箱>\\Users\\bob\\.lompi` |

    **三条都能沙箱化**，因为 `cfg_root()` 返回的根永远是 **argv[0] 自己的前缀** ——
    把 `\\AppData\\Local\\` 放进前缀里，规则 0 就指到沙箱内部去了。规则 0 正是 Windows
    的**默认安装位置**（`%LOCALAPPDATA%\\Loment`），不能只测 fallback。
    （POSIX 上前两条命中不了：`cfg_find_ci` 找的是字面 `\\AppData\\Local\\`，那边路径是
    `/`，所以只有规则 2 会跑 —— 这是 lompi 的 Windows 形状，见 docs/170 §5。）

    沙箱建在 `loment/dist/` 下（已被 .gitignore）；**装任何东西之前**先问一次，目的地
    若在沙箱外就整体跳过 —— 绝不去动用户真实的 store。
    """
    d = _dist()
    arc_name = (f"loment-{d.VER}-windows-x64.zip" if IS_WIN
                else f"loment-{d.VER}-linux-x64.tar.gz")
    arc = ROOT / "loment" / "dist" / arc_name
    if not arc.exists():
        print(f"         (跳过: {arc_name} 还没构建，先跑 loment_dist.py --emit)")
        return
    td = Path(tempfile.mkdtemp(dir=ROOT / "loment" / "dist", prefix=".e2e-store-"))
    try:
        payload = td / "payload"
        payload.mkdir()
        if IS_WIN:
            import zipfile
            with zipfile.ZipFile(arc) as z:
                z.extractall(payload)
        else:
            import tarfile
            with tarfile.open(arc) as tf:
                tf.extractall(payload)
        top = next(p for p in payload.iterdir() if p.is_dir())
        binname = "lompi.exe" if IS_WIN else "lompi"
        ps = shutil.which("powershell") or "powershell.exe"
        ins = "install.ps1" if IS_WIN else "install.sh"

        def run(prefix: Path, *extra: str):
            if IS_WIN:
                cmd = [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                       str(top / ins), "-Prefix", str(prefix),
                       "-NoPath", "-NoFileType", "-NoSkill", *extra]
            else:
                cmd = ["sh", str(top / ins), "--prefix", str(prefix),
                       "--no-path", "--no-skill", *extra]
            return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", shell=False, timeout=600)

        # 装之前先问一次：目的地落在沙箱外就整体别装（那意味着这条判据会去动真东西）
        probe = subprocess.run([str(top / "bin" / binname), "config"], cwd=str(top),
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", shell=False, timeout=120)
        assert _store_line(probe.stdout or ""), \
            f"lompi config 没给出 store 路径: {(probe.stdout or '')[:200]!r}"
        if not str(Path(_store_line(probe.stdout)).resolve()).lower().startswith(
                str(td.resolve()).lower()):
            print(f"         (跳过: 本机 lompi 全局根是 {_store_line(probe.stdout)}，"
                  "在沙箱外 —— 不去动用户的 store)")
            return

        shapes: list[Path] = [td / "pfx"]
        if IS_WIN:   # 规则 0 与规则 1 只有 Windows 形状的 argv[0] 才命中得了
            shapes += [td / "AppData" / "Local" / "Loment", td / "Users" / "bob" / "Loment"]
        for prefix in shapes:
            r = run(prefix)
            assert r.returncode == 0, \
                f"装到 {prefix.name} 失败({r.returncode}): {(r.stdout or '')[-400:]}{(r.stderr or '')[-400:]}"

            # 包里那份纯净副本
            pristine = prefix / "share" / "lompi" / "store"
            got = sum(1 for _ in pristine.rglob("*") if _.is_file())
            assert got == STORE_FILES, f"装进 {prefix.name} 的 store 是 {got} 个文件，应为 {STORE_FILES}"

            # 装出来的 lompi 自己说 store 在哪 —— 那才是判据该查的地方
            c = subprocess.run([str(prefix / "bin" / binname), "config"], cwd=str(prefix),
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", shell=False, timeout=120)
            dst = Path(_store_line(c.stdout or ""))
            assert str(dst.resolve()).lower().startswith(str(td.resolve()).lower()), \
                f"{prefix.name}: 装出来的 lompi 指向沙箱外的 store: {dst}"
            for pkg, n in STORE_MODULES.items():
                assert _lomt_count(dst, pkg) == n, \
                    f"{prefix.name}: 装完 {dst / pkg} 里不是 {n} 个 .lomt"

            # 真正的验收：lompi 自己去 index 那个 store，认得 std 与 host
            c = subprocess.run([str(prefix / "bin" / binname), "index", str(dst)],
                               cwd=str(prefix), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", shell=False, timeout=180)
            out = (c.stdout or "") + (c.stderr or "")
            assert c.returncode == 0, f"{prefix.name}: lompi index 失败: {out[-300:]!r}"
            for pkg in STORE_MODULES:
                assert pkg in out, f"{prefix.name}: lompi index 没报出 {pkg}: {out[-300:]!r}"

            # 卸载只收回本包发过的那些 <name>/<version>
            r = run(prefix, "-Uninstall" if IS_WIN else "--uninstall")
            assert r.returncode == 0, f"{prefix.name}: 卸载失败: {(r.stdout or '')[-300:]}"
            for pkg in STORE_MODULES:
                assert _lomt_count(dst, pkg) == 0, f"{prefix.name}: 卸载之后 {dst / pkg} 还在"
    finally:
        shutil.rmtree(td, ignore_errors=True)


# ---------------------------------------------------------------- 编译器的库解析 (2026-09-16)
#
# 用户报"装了之后 `use std` 指向一个不存在的路径"。根因在编译器侧: 名字形式原先只认四个
# **仓库相对**的根。这三条在本机**原生**跑真 stage1 (发行包编出来的那个驱动), 钉住新的
# 三层解析 —— p8 那条驱动闸门走 WSL, 覆盖不到 PE 上的路径分隔符与 `deps/` 布局。

#: 一个最小多文件包: 入口 + 它的伴生模块 (包内用**路径形式**互相 import)。
PKG_ENTRY = ('module {n}\n\nuse "area.lomt"\n\npub fn g_area(w: u32, h: u32) -> u32 {{\n'
             '    return ar(w, h);\n}}\n')
PKG_AREA = "module area\n\npub fn ar(w: u32, h: u32) -> u32 {\n    return w * h;\n}\n"
USE_HI = ("module hi\n\nuse {n}\n\nfn main() -> u32 {{\n"
          "    return g_area(3 as u32, 4 as u32);\n}}\n")


def _stage1() -> Path | None:
    """发行包同一条链上的 stage1 (自举驱动)。没有 clang 就是 None。"""
    if not _clang():
        return None
    import loment_dist  # noqa: E402
    return loment_dist.build_stage1()


def _ref_ir(entry: Path) -> bytes:
    mod = lomentc.load(entry)
    deps = lomentc.resolve_deps(mod, ROOT, entry.parent, entry=entry)
    return lomentc.emit_llvm(mod, ROOT, deps).encode()


def _write_pkg(d: Path, name: str) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "area.lomt").write_bytes(PKG_AREA.encode())
    (d / f"{name}.lomt").write_bytes(PKG_ENTRY.format(n=name).encode())


@test
def test_selfhost_resolves_a_package_from_project_deps():
    """名字形式第 1 层 `<项目根>/deps/<名字>/<名字>.lomt`; 且包内的路径形式相对**被导入
    文件所在目录**解析 —— `deps/geom/geom.lomt` 里的 `use "area.lomt"` 只相对 `deps/geom/`
    成立, 只按 CWD 找会去够 `<CWD>/area.lomt` (那个"不存在的路径")。IR 与参考实现逐字节比。
    """
    s = _stage1()
    if s is None:
        print("         (跳过: 无 clang, stage1 编不出来)")
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        proj = root / "proj"
        _write_pkg(proj / "deps" / "geom", "geom")
        hi = proj / "hi.lomt"
        hi.write_bytes(USE_HI.format(n="geom").encode())
        # CWD 故意是**别的目录**: 解析必须靠"项目根 = 入口所在目录", 不是 CWD
        r = subprocess.run([str(s), str(hi)], cwd=str(root), capture_output=True, timeout=300)
        assert r.returncode == 0, f"deps/ 那层没解析出来: {r.stderr.decode('utf-8', 'replace')[-300:]}"
        assert r.stdout == _ref_ir(hi), "自举镜与参考实现的 IR 不一致"


#: 直接命中**包内模块**的入口: 包里那个与包不同名的模块按名字取 (2026-09-20)。
USE_AREA = ('module hi\n\nuse area\n\nfn main() -> u32 {\n    return ar(3 as u32, 4 as u32);\n}\n')


@test
def test_selfhost_reaches_a_module_inside_a_package():
    """名字形式要能命中**包内模块** (`deps/geom/area.lomt`), 不只是包入口。

    这是让 std 真正可用的那一层: 只认入口的话, 整包只能被"全拖进同一个单元"那一种方式
    消费, 而单元的发射符号是平的、编译代价还按模块数超线性涨 —— 128 个模块两样都过不去。
    与参考实现逐字节比 IR (用 `vec`/`io` 那类无分歧的语料, 见下一条的说明)。
    """
    s = _stage1()
    if s is None:
        print("         (跳过: 无 clang, stage1 编不出来)")
        return
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "proj"
        _write_pkg(proj / "deps" / "geom", "geom")
        hi = proj / "hi.lomt"
        hi.write_bytes(USE_AREA.encode())
        r = subprocess.run([str(s), str(hi)], cwd=str(proj), capture_output=True, timeout=300)
        assert r.returncode == 0, \
            f"包内模块没解析出来: {r.stderr.decode('utf-8', 'replace')[-300:]}"
        assert b"define" in r.stdout, r.stdout[:200]
        assert r.stdout == _ref_ir(hi), "包内模块: 自举镜与参考实现的 IR 不一致"


@test
def test_selfhost_reaches_a_module_in_the_dev_checkout_store():
    """开发 checkout 的商店 (`<CWD>/lompi/store`, 发布包里是 `share/lompi/store`)。

    仓里的 store 不摆 `share/` 那一层 —— 那条路只有装出来的前缀才有。没有这一条,
    在本仓写 `use vec` 一律"找不到或有歧义", 而"本仓能不能用 std"正是它要回答的问题。
    """
    s = _stage1()
    if s is None:
        print("         (跳过: 无 clang, stage1 编不出来)")
        return
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        pkg = proj / "lompi" / "store" / "geom" / "0.1.0"
        pkg.mkdir(parents=True)
        (pkg / "area.lomt").write_bytes(PKG_AREA.encode())
        (pkg / "geom.lomt").write_bytes(PKG_ENTRY.format(n="geom").encode())
        hi = proj / "hi.lomt"
        hi.write_bytes(USE_AREA.encode())
        r = subprocess.run([str(s), str(hi)], cwd=str(proj), capture_output=True, timeout=300)
        assert r.returncode == 0, \
            f"开发 checkout 的商店那层没解析出来: {r.stderr.decode('utf-8', 'replace')[-300:]}"
        # 参考实现那一侧**锚点是 `root` 参数**（仓内跑时是仓根），自举镜那一侧是 **CWD** ——
        # 仓内两者同一个目录，这个夹具刻意把它们都指到 `proj`，否则量的是"两个锚点不同"
        # 而不是"这层解析不成立"（第一次就是拿 `_ref_ir` 比，比出来的是锚点差异）。
        mod = lomentc.load(hi)
        want = lomentc.emit_llvm(mod, proj,
                                 lomentc.resolve_deps(mod, proj, hi.parent, entry=hi)).encode()
        assert r.stdout == want, "商店包内模块: 自举镜与参考实现的 IR 不一致"


@test
def test_selfhost_resolves_from_the_toolchains_own_store():
    """名字形式第 2 层: `<工具目录>/../share/lompi/store/<名字>/<版本>/<名字>.lomt`。

    **这就是"装完 Loment 就能 `use std`"那一层** (用户 2026-09-16 定: 像 python/java 那样)。
    要复刻发行包的布局 (`bin/` + `share/lompi/store/`) 才触发, 所以这里把 stage1 摆成那个形状。
    """
    s = _stage1()
    if s is None:
        print("         (跳过: 无 clang, stage1 编不出来)")
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        tool = root / "tool"
        (tool / "bin").mkdir(parents=True)
        shutil.copyfile(s, tool / "bin" / s.name)
        _write_pkg(tool / "share" / "lompi" / "store" / "geom" / "0.1.0", "geom")
        proj = root / "proj"
        proj.mkdir()
        hi = proj / "hi.lomt"
        hi.write_bytes(USE_HI.format(n="geom").encode())
        r = subprocess.run([str(tool / "bin" / s.name), str(hi)], cwd=str(proj),
                           capture_output=True, timeout=300)
        assert r.returncode == 0, \
            f"自带 store 那层没解析出来: {r.stderr.decode('utf-8', 'replace')[-300:]}"
        assert b"@g_area" in r.stdout, r.stdout[:200]


@test
def test_selfhost_refuses_a_file_over_the_use_limit():
    """超过 `MAX_USE` 条 use **报错退出**。原先进程只装前 8 条、其余**静默丢掉** ——
    丢掉之后单元少几块, 而参考实现照收, 两个实现于是对同一份源码给出不同的产物。"""
    s = _stage1()
    if s is None:
        print("         (跳过: 无 clang, stage1 编不出来)")
        return
    import lomentc as _c  # noqa: E402
    n = _c.MAX_USE + 1
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        d = proj / "deps" / "many"
        d.mkdir(parents=True)
        for i in range(n):
            (d / f"m{i}.lomt").write_bytes(
                f"module m{i}\n\npub fn f{i}() -> u32 {{\n    return {i} as u32;\n}}\n".encode())
        (d / "many.lomt").write_bytes(
            ("module many\n\n" + "\n".join(f'use "m{i}.lomt"' for i in range(n)) + "\n").encode())
        hi = proj / "hi.lomt"
        hi.write_bytes(USE_HI.format(n="many").encode())
        r = subprocess.run([str(s), str(hi)], cwd=str(proj), capture_output=True, timeout=300)
        err = r.stderr.decode("utf-8", "replace")
        assert r.returncode != 0, f"{n} 条 use 应当报错, 却过了"
        assert "超过上限" in err, err[-300:]


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
