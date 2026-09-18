#!/usr/bin/env python3
# loment_dist_test.py — 发行包判据 (docs/162)
#
# 判据 (每条都是"打出来的包真能用", 不是"文件在"):
#   1. payload 布局与脚本卫生: 该有的都在; .sh/.ps1/.cmd 纯 ASCII; .ps1/.cmd 是 CRLF
#      (PS 5.1 用 ANSI 读无 BOM 的非 ASCII 会把后续行解析坏 —— docs/157 §3.4)
#   2. 归档内容 == payload (逐个 sha256 对得上, 不是"大概在")
#   3. 归档**确定性**: 同样输入两次写出字节相同 (zip 固定时间戳 + tar.gz mtime=0)
#   4. `--check` 对产物与 SHA256SUMS 一致
#   5. install.sh 端到端 (WSL 里): tar → 装进临时前缀 → `loment version` →
#      `loment ir` 的产物与参考实现**逐字节相同** → `loment run` 打出东西 →
#      --uninstall 摘掉 → 再装一次仍成功 (幂等)
#   6. Windows 安装脚本能在 PowerShell 5.1 下解析并 -DryRun 跑通 (本机实测)
#
# 退出码: 0 = 全过 / 1 = 有红。

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import loment_dist  # noqa: E402
import lomentc  # noqa: E402

#: WSL 侧临时路径前缀 —— **每个进程一份**。WSL 的 `/tmp` 是所有 `wsl -e` 调用
#: 共用的, 固定文件名在**并发跑门禁**时会让两个进程互相跑对方的二进制 ——
#: 那是**错结果**, 不是慢。见 `ci.py` 的 `-j`。
_T = f"/tmp/loment-{os.getpid()}-"

ROOT = loment_dist.ROOT
OUT = loment_dist.OUT
IT_OUT = loment_dist.STAGE / "it-out"   # 测试专用产物目录 (不碰 loment/dist)
VER = loment_dist.VER
PREFIX_IT = f"{_T}loment_dist_it"          # WSL 侧的安装前缀
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not ok else ""))


def wsl(*args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(["wsl", "-e", *args], capture_output=True, text=True,
                          shell=False, encoding="utf-8", errors="replace", timeout=timeout)


def wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    return "/mnt/" + s[0].lower() + s[2:]


# ------------------------------------------------------------------ 1. 布局

def test_layout() -> None:
    lin = loment_dist.payload("linux", {"loment-driver": (b"\x7fELF-fake", b"MZ-fake")})
    win = loment_dist.payload("windows", {"loment-driver": (b"\x7fELF-fake", b"MZ-fake")})
    want_common = {"bin/loment", "share/loment/version", "share/loment/seed.ll",
                   "share/loment/examples/user_hello.lomt", "README.md", "LICENSE"}
    check("payload 公共布局齐全", want_common <= set(lin))
    check("linux 有 install.sh 且可执行",
          lin.get("install.sh", (b"", 0))[1] == 0o755)
    check("windows 有 install.ps1/install.cmd/loment.ico",
          {"install.ps1", "install.cmd", "bin/loment.ico"} <= set(win))
    check("windows zip 里没有 install.sh (不混淆两种装法)",
          "install.sh" not in win and "install.ps1" not in lin)
    check("ELF 权限位是 755", all(m == 0o755 for (rel, (b, m)) in lin.items()
                                  if rel.startswith("bin/loment-")))

    # 脚本卫生: ASCII + 行尾
    bad_ascii = [rel for rel in ("bin/loment", "install.sh")
                 if any(c > 0x7E for c in lin[rel][0])]
    check("linux 脚本纯 ASCII", not bad_ascii, ",".join(bad_ascii))
    ps1 = win["install.ps1"][0]
    check("install.ps1 纯 ASCII", all(c <= 0x7E for c in ps1))
    for rel in ("install.ps1", "install.cmd"):
        blob = win[rel][0]
        check(f"{rel} 是 CRLF", b"\r\n" in blob and b"\n" not in blob.replace(b"\r\n", b""))
    check("bin/loment 是 LF (WSL/Linux 侧)",
          b"\r" not in lin["bin/loment"][0])
    # Windows 包里工具都带 .exe, 而 `test -x name` 只在 MSYS 下自动补后缀 —— WSL 不会。
    # 所以启动器必须经 tool() 解析路径; 裸 `$here/loment-` 一旦回归就是「假报缺组件」。
    launcher = lin["bin/loment"][0].decode("ascii")
    check("sh 启动器经 tool() 解析工具路径 (Windows 包的工具带 .exe)",
          "$here/loment-" not in launcher and "tool loment-driver" in launcher,
          "仍有裸路径" if "$here/loment-" in launcher else "")
    ver = lin["share/loment/version"][0].decode()
    check("version 文件带显示名与标识符",
          loment_dist.DISPLAY in ver and VER in ver, ver.splitlines()[0] if ver else "")


# ------------------------------------------------------------------ 2/3. 归档

def _safe_extract(arc, dest: Path) -> None:
    """只往 dest 之下解: 拒绝绝对路径与 `..`。

    归档是**我们自己打的**, 但解包代码不该假设这一点 —— 这条校验让"换成别人的包"也安全。
    """
    base = dest.resolve()
    names = arc.namelist() if isinstance(arc, zipfile.ZipFile) else arc.getnames()
    for name in names:
        if name.startswith(("/", "\\")) or ":" in name.split("/")[0]:
            raise ValueError(f"unsafe archive member: {name}")
        if not str((base / name).resolve()).startswith(str(base)):
            raise ValueError(f"archive member escapes the target dir: {name}")
    arc.extractall(dest)


def read_zip(p: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(p) as zf:
        return {n.split("/", 1)[1]: zf.read(n) for n in zf.namelist()}


def read_tar(p: Path) -> dict[str, bytes]:
    with tarfile.open(p) as tf:
        return {m.name.split("/", 1)[1]: tf.extractfile(m).read()
                for m in tf.getmembers() if m.isfile()}


def test_archives() -> None:
    lin = loment_dist.payload("linux", {"loment-driver": (b"ELF-A", b"PE-A")})
    win = loment_dist.payload("windows", {"loment-driver": (b"ELF-A", b"PE-A")})
    z = read_zip_bytes(loment_dist._zip("loment-x", win))
    t = read_tar_bytes(loment_dist._tar_gz("loment-x", lin))
    check("zip 内容与 payload 逐文件相同",
          z == {k: v[0] for k, v in win.items()})
    check("tar.gz 内容与 payload 逐文件相同",
          t == {k: v[0] for k, v in lin.items()})
    # 确定性: 同一输入两次 → 相同字节 (与仓库其余"确定性工件"同一纪律)
    check("zip 两次写出字节相同",
          loment_dist._zip("loment-x", win) == loment_dist._zip("loment-x", win))
    check("tar.gz 两次写出字节相同",
          loment_dist._tar_gz("loment-x", lin) == loment_dist._tar_gz("loment-x", lin))


def read_zip_bytes(blob: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(__import__("io").BytesIO(blob)) as zf:
        return {n.split("/", 1)[1]: zf.read(n) for n in zf.namelist()}


def read_tar_bytes(blob: bytes) -> dict[str, bytes]:
    import io
    with tarfile.open(fileobj=io.BytesIO(blob)) as tf:
        return {m.name.split("/", 1)[1]: tf.extractfile(m).read()
                for m in tf.getmembers() if m.isfile()}


# ------------------------------------------------------------------ 4. 构建 + check

def build() -> tuple[Path, Path]:
    # 要整包（不是 --only driver）：`loment build/run` 靠包内的 loment-lomelf 链接
    rc = loment_dist.main(["--emit", "--no-exe", "--out", str(IT_OUT)])
    assert rc == 0, f"loment_dist --emit rc={rc}"
    tar = IT_OUT / f"loment-{VER}-linux-x64.tar.gz"
    zipf = IT_OUT / f"loment-{VER}-windows-x64.zip"
    check("产物存在 (tar.gz + zip)", tar.exists() and zipf.exists())
    check("--check 与 SHA256SUMS 一致",
          loment_dist.main(["--check", "--out", str(IT_OUT)]) == 0)
    # 归档里的 driver == 同一份 IR 现链出来的（中间没被动过）
    driver = loment_dist._lomelf_link(
        (loment_dist.STAGE / "driver.ll").read_text(encoding="utf-8"), "elf")
    check("归档里的 loment-driver == 构建产物",
          read_tar(tar)["bin/loment-driver"] == driver)
    check("归档里有 loment-lomelf（`loment build/run` 的链接器）",
          "bin/loment-lomelf" in read_tar(tar))
    return tar, zipf


# ------------------------------------------------------------------ 5. install.sh 端到端

def test_install_sh(tar: Path) -> None:
    work = loment_dist.STAGE / "it"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    with tarfile.open(tar) as tf:
        _safe_extract(tf, work)
    root = next(p for p in work.iterdir() if p.is_dir())
    install = root / "install.sh"

    wsl("rm", "-rf", PREFIX_IT)
    r = wsl("sh", wsl_path(install), "--prefix", PREFIX_IT, "--no-path")
    check("install.sh 装进临时前缀 (含随包校验和)", r.returncode == 0,
          (r.stderr or r.stdout)[-200:])

    r = wsl(f"{PREFIX_IT}/bin/loment", "version")
    check("装出来的 loment version 打出版本行",
          r.returncode == 0 and loment_dist.DISPLAY in r.stdout, r.stdout[:120])

    # 装的编译器产出的 IR == 参考实现 (这才是"能用"的判据)
    mod = lomentc.load(ROOT / loment_dist.EXAMPLE)
    deps = lomentc.resolve_deps(mod, ROOT, (ROOT / loment_dist.EXAMPLE).parent,
                                entry=ROOT / loment_dist.EXAMPLE)
    want = lomentc.emit_llvm(mod, ROOT, deps)
    r = wsl(f"{PREFIX_IT}/bin/loment", "ir",
            f"{PREFIX_IT}/share/loment/examples/user_hello.lomt")
    check("包的编译器产物与参考实现逐字节相同",
          r.returncode == 0 and r.stdout == want,
          f"rc={r.returncode} len={len(r.stdout)}/{len(want)}")

    # check 子命令: 正例退出 0 且不吐 IR
    r = wsl(f"{PREFIX_IT}/bin/loment", "check",
            f"{PREFIX_IT}/share/loment/examples/user_hello.lomt")
    check("loment check 正例退出 0 且不打印 IR",
          r.returncode == 0 and r.stdout.strip() == "", r.stdout[:80])

    # run: 整链都在包里（驱动 + 自举链接器），不再需要外部 clang
    r = wsl(f"{PREFIX_IT}/bin/loment", "run",
            f"{PREFIX_IT}/share/loment/examples/user_hello.lomt")
    check("loment run 编译+链接+运行并打出东西",
          r.returncode == 0 and r.stdout.strip() != "", (r.stderr or "")[-200:])

    # `loment help [COMMAND]` 必须**走得到那一页**。启动器只转发 `help` 而不带后面的参数时,
    # 详细页永远看不到 —— 而目录页里印的正是 `loment help [COMMAND]`。`loment-cli help build`
    # 直呼是好的, 所以这条**只能由装好的启动器**来测 (源码级判据看不见这一层)。
    r = wsl(f"{PREFIX_IT}/bin/loment", "help", "build")
    check("装完后 `loment help build` 打到详细页 (sh 启动器转发了参数)",
          r.returncode == 0 and "Compile and link to an executable" in r.stdout,
          (r.stdout or r.stderr)[:140])

    # 缺件时的报错要指名：临时把 fmt 拿掉，`loment fmt` 应该明确说"这个包没包含"
    wsl("rm", "-f", f"{PREFIX_IT}/bin/loment-fmt")
    r = wsl(f"{PREFIX_IT}/bin/loment", "fmt",
            f"{PREFIX_IT}/share/loment/examples/user_hello.lomt")
    check("缺组件时报错指名 (不静默)",
          r.returncode == 3 and "loment-fmt" in (r.stdout + r.stderr),
          f"rc={r.returncode} out={(r.stdout + r.stderr)[:80]}")

    # 幂等: 再装一次仍成功
    r = wsl("sh", wsl_path(install), "--prefix", PREFIX_IT, "--no-path")
    check("重复安装幂等", r.returncode == 0, (r.stderr or "")[-160:])

    # 卸载: 清干净
    r = wsl("sh", wsl_path(install), "--uninstall", "--prefix", PREFIX_IT)
    r2 = wsl("test", "-e", f"{PREFIX_IT}/bin/loment")
    check("--uninstall 摘掉可执行文件", r.returncode == 0 and r2.returncode != 0)


# ------------------------------------------------------------------ 6. Windows 安装脚本

def test_windows_installer(zipf: Path) -> None:
    work = loment_dist.STAGE / "it-win"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    with zipfile.ZipFile(zipf) as zf:
        _safe_extract(zf, work)
    root = next(p for p in work.iterdir() if p.is_dir())
    ps1 = root / "install.ps1"
    ps = shutil.which("powershell") or shutil.which("pwsh")
    if not ps:
        print("  SKIP  install.ps1 -DryRun (没有 powershell)")
        return
    r = subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1),
                        "-DryRun"], capture_output=True, text=True, shell=False,
                       encoding="utf-8", errors="replace", timeout=180)
    out = (r.stdout or "") + (r.stderr or "")
    check("install.ps1 在 PowerShell 下解析并 -DryRun 通过",
          r.returncode == 0 and "dry-run" in out, out[-220:])

    # 自解压包那条路: install.cmd 用 -PayloadZip 把 payload.zip 解开再装 —— 单独验这段接线
    pz = work / "payload.zip"
    pz.write_bytes(loment_dist._zip("", loment_dist.payload("windows", {})))
    r = subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1),
                        "-DryRun", "-PayloadZip", str(pz)], capture_output=True, text=True,
                       shell=False, encoding="utf-8", errors="replace", timeout=180)
    out = (r.stdout or "") + (r.stderr or "")
    check("install.ps1 -PayloadZip (自解压包路径) 也能跑",
          r.returncode == 0 and "payload" in out, out[-220:])

    exe = OUT / f"loment-{VER}-windows-x64-setup.exe"  # 正式产物目录里的 (测试不重建 exe)
    if exe.exists():
        head = exe.read_bytes()[:2]
        check("setup.exe 是 PE 且非空", head == b"MZ" and exe.stat().st_size > 100000,
              f"head={head!r} size={exe.stat().st_size}")
    else:
        print("  SKIP  setup.exe 存在性 (本次 --emit 用了 --no-exe)")

    # ★ 用户最可能走的那一步: 解压 zip -> 双击 / 运行 install.cmd。
    #   它曾只认自解压布局 (去找 payload.zip), 在 zip 布局里必然失败 —— 2026-09-12 用户报障。
    cmd_file = root / "install.cmd"
    r = subprocess.run(["cmd", "/c", str(cmd_file), "-DryRun"], capture_output=True,
                       text=True, shell=False, encoding="utf-8", errors="replace",
                       timeout=180, cwd=str(root), stdin=subprocess.DEVNULL)
    out = (r.stdout or "") + (r.stderr or "")
    check("zip 布局下 install.cmd -DryRun 通过 (用户路径)",
          r.returncode == 0 and "dry-run" in out, out[-220:])

    # ★ 真装一遍 (临时前缀, -NoPath -NoFileType: 不动用户 PATH 与注册表)。
    #   包里就是本机 PE —— 不需要 WSL, 也不需要 clang。
    pfx = loment_dist.STAGE / "it-win-pfx"
    if pfx.exists():
        shutil.rmtree(pfx)
    # 沙箱用户目录: 安装器会把 agent skill 放进 ~/.claude/skills/loment —— 不能碰真机器上的那个
    home = loment_dist.STAGE / "it-win-home"
    if home.exists():
        shutil.rmtree(home)
    (home / ".claude").mkdir(parents=True)
    # 别家 agent 的落点: 造一个**已经有内容**的 Codex 全局指令文件, 用来验证
    # "只加带标记的一段、不碰原有内容、重装不重复、卸载精确摘掉"。
    (home / ".codex").mkdir(parents=True)
    codex = home / ".codex" / "AGENTS.md"
    codex.write_text("MY OWN RULES\nsecond line\n", encoding="utf-8", newline="\n")
    env = dict(os.environ, USERPROFILE=str(home))
    r = subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1),
                        "-Prefix", str(pfx), "-PayloadDir", str(root),
                        "-NoPath", "-NoFileType"], capture_output=True, text=True,
                       shell=False, encoding="utf-8", errors="replace", timeout=300, env=env)
    check("install.ps1 真装 (临时前缀, 原生 PE, 无 WSL) 退出 0", r.returncode == 0,
          ((r.stdout or "") + (r.stderr or ""))[-260:])
    cmd = pfx / "bin/loment.cmd"
    body = cmd.read_text(encoding="utf-8", errors="replace") if cmd.exists() else ""
    check("装出 loment.cmd, 它调本机 exe, 且**不再出现 wsl**",
          cmd.exists() and "loment-driver.exe" in body and "wsl" not in body.lower(),
          "" if cmd.exists() else "缺 loment.cmd")
    r2 = subprocess.run(["cmd", "/c", str(cmd), "version"], capture_output=True, text=True,
                        shell=False, encoding="utf-8", errors="replace", timeout=180)
    check("装完后 `loment version` 能跑",
          r2.returncode == 0 and loment_dist.DISPLAY in (r2.stdout or ""), (r2.stdout or "")[:120])

    # 与 sh 启动器那条对称: `loment help build` 的详细页必须走得到 (两份启动器都要转参数)
    r2b = subprocess.run(["cmd", "/c", str(cmd), "help", "build"], capture_output=True, text=True,
                         shell=False, encoding="utf-8", errors="replace", timeout=180)
    check("装完后 `loment help build` 打到详细页 (cmd 启动器转了参数)",
          r2b.returncode == 0 and "Compile and link to an executable" in (r2b.stdout or ""),
          (r2b.stdout or r2b.stderr or "")[:140])

    # ★ 全链判据: `loment run` 在本机编出 PE 并跑起来 —— 这才是"去 WSL"的意义
    ex = pfx / "share/loment/examples/user_hello.lomt"
    r3 = subprocess.run(["cmd", "/c", str(cmd), "run", str(ex)], capture_output=True, text=True,
                        shell=False, encoding="utf-8", errors="replace", timeout=300)
    check("装完后 `loment run` 原生跑通 (无 WSL / 无 clang)",
          r3.returncode == 0 and "PASS loment-user" in (r3.stdout or ""),
          ((r3.stdout or "") + (r3.stderr or ""))[-200:])

    # ★ agent skill: 装完 agent 读得到, 且与仓库里的那份**逐字节相同**
    #   (这条是补出来的 —— 第一版漏了给 share\loment\skill 建目录, 真装才炸出来)
    skill = home / ".claude" / "skills" / "loment" / "SKILL.md"
    want = (loment_dist.ROOT / loment_dist.SKILL).read_bytes()
    check("装完把 agent skill 放进 ~/.claude/skills/loment (沙箱 HOME)",
          skill.exists() and skill.read_bytes() == want, f"缺或不符: {skill}")
    check("随包的 skill 也留在前缀里 (share/loment/skill)",
          (pfx / "share/loment/skill/SKILL.md").exists(), "缺 share/loment/skill/SKILL.md")

    # ★ 跨 agent 广播: 别家 agent 读不到 .claude/skills/, 所以往它自己认的文件里写**指针**。
    #   这条同时钉住"不碰原有内容"与"带标记所以可精确摘除"。
    txt = codex.read_text(encoding="utf-8")
    check("广播到 Codex 的 ~/.codex/AGENTS.md (带标记的指针, 原有内容保留)",
          "<!-- loment:begin -->" in txt and "MY OWN RULES" in txt
          and txt.count("<!-- loment:begin -->") == 1, txt[:140])
    check("指针指向包里那份指南, 不是拷贝",
          str(pfx / "share/loment/skill/SKILL.md") in txt, txt[:200])

    # ★ lompi 的指南走**同一套装法**（用户 2026-09-15），但用**它自己的标记**。
    #   这一条只有在**真装**里才验得到 —— 源码级判据（loment_lompi_test）只能说脚本里
    #   提到了它，不能说明装完真的落到了该落的地方。
    lompi_skill = home / ".claude" / "skills" / "lompi" / "SKILL.md"
    lompi_want = (loment_dist.ROOT / loment_dist.SKILL_LOMPI).read_bytes()
    check("装完把 lompi 的指南放进 ~/.claude/skills/lompi (沙箱 HOME)",
          lompi_skill.exists() and lompi_skill.read_bytes() == lompi_want,
          f"缺或不符: {lompi_skill}")
    check("随包的 lompi 指南也留在前缀里 (share/lompi/skill)",
          (pfx / "share/lompi/skill/SKILL.md").exists(), "缺 share/lompi/skill/SKILL.md")
    txt2 = codex.read_text(encoding="utf-8")
    check("lompi 的 Codex 指针带**自己**的标记 (与 loment 那段分得开)",
          "<!-- lompi:begin -->" in txt2 and "<!-- lompi:end -->" in txt2
          and txt2.count("<!-- lompi:begin -->") == 1
          and txt2.count("<!-- loment:begin -->") == 1, txt2[:220])
    check("lompi 二进制装进了 bin/",
          (pfx / "bin" / "lompi.exe").exists() or (pfx / "bin" / "lompi").exists(),
          "bin/ 里没有 lompi")

    # ★ 通用兜底：payload 里 `share/` 下的**每一个**文件都要真落到前缀里。
    #   写 lompi 时踩过 —— 拷贝清单加了 `share/lompi/skill/SKILL.md`，却忘了先建
    #   `share\lompi\skill` 这个目录，`Copy-Item` 直接抛，**整个安装在第 2 步就死了**
    #   （后面的 agent skill 段根本没跑到）。逐个文件点名只能守住点过名的那些；
    #   这条按 payload 自己列，以后往包里加什么都会自动被守到。
    want_share = [k for k in loment_dist.payload("windows", {}) if k.startswith("share/")]
    missing_share = [k for k in want_share if not (pfx / k.replace("/", "\\")).exists()]
    check(f"payload 里 share/ 的 {len(want_share)} 个文件全落到了前缀",
          not missing_share, f"缺: {missing_share}")

    # ★ 与任何目录约定无关的兜底: 跑 CLI 就能拿到指南。这条是给"机器上既没有 Claude
    #   也没有 Codex"的情形准备的 —— agent 上手陌生语言的第一动作就是跑 CLI 看用法。
    r5 = subprocess.run(["cmd", "/c", str(cmd), "skill"], capture_output=True, text=True,
                        shell=False, encoding="utf-8", errors="replace", timeout=120)
    got_path = (r5.stdout or "").strip()
    check("`loment skill` 打出指南路径且该路径存在",
          r5.returncode == 0 and got_path.endswith("SKILL.md") and Path(got_path).exists(),
          got_path[:160])
    r6 = subprocess.run(["cmd", "/c", str(cmd), "skill", "--print"], capture_output=True,
                        text=True, shell=False, encoding="utf-8", errors="replace", timeout=120)
    got = (r6.stdout or "").replace("\r\n", "\n")
    check("`loment skill --print` 的输出 == 指南全文 (逐字节)",
          r6.returncode == 0 and got == want.decode("utf-8").replace("\r\n", "\n"),
          f"rc={r6.returncode} len={len(got)}")
    r7 = subprocess.run(["cmd", "/c", str(cmd), "help"], capture_output=True, text=True,
                        shell=False, encoding="utf-8", errors="replace", timeout=120)
    check("`loment help` 的用法里能看到 skill (agent 的第一动作)",
          "loment skill" in (r7.stdout or ""), (r7.stdout or "")[:160])

    # ★ 构建失败必须是**非零退出**, 且 "rc=0" 与 "产物真的在磁盘上" 必须同时成立。
    #   原先 cmd 启动器用 `if errorlevel 1` 判工具失败 —— 可崩溃的工具退出码是**负数**
    #   (0xC000001D = -1073741795), cmd 按有符号比较判定为**假**, 于是落到成功分支: 打出
    #   "loment: x.exe"、rc=0, 磁盘上却什么都没有 (2026-09-15 用户实测)。
    #   这条判据与链接器能力**无关**: 就算以后聚合能编了, 不变式 rc=0 <=> 有产物 仍成立。
    agg = pfx / "share/loment/examples/tour.lomt"      # 按值聚合, 现链接器编不了
    probe = pfx / "rc_probe"
    r8 = subprocess.run(["cmd", "/c", str(cmd), "build", str(agg), "-o", str(probe)],
                        capture_output=True, text=True, shell=False, encoding="utf-8",
                        errors="replace", timeout=180)
    produced = probe.with_suffix(".exe").exists()
    check("build 的退出码与产物一致 (rc=0 <=> 产物存在)", (r8.returncode == 0) == produced,
          f"rc={r8.returncode} 产物={produced} out={(r8.stdout or '').strip()[:80]}")

    # 同一类洞的另一半: 编译**本身**失败时, `loment run` 也绝不能回 0 (否则 CI 被静默骗过)。
    bad = pfx / "bad_probe.lomt"
    bad.write_text("module bad\n\nfn f() -> u32 {\n    return nope;\n}\n",
                   encoding="utf-8", newline="\n")
    r9 = subprocess.run(["cmd", "/c", str(cmd), "run", str(bad)], capture_output=True,
                        text=True, shell=False, encoding="utf-8", errors="replace", timeout=180)
    check("编译失败时 `loment run` 非零退出", r9.returncode != 0,
          f"rc={r9.returncode} out={(r9.stdout or '')[:80]}")

    r4 = subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1),
                         "-Uninstall", "-Prefix", str(pfx), "-NoPath", "-NoFileType"],
                        capture_output=True, text=True, shell=False, encoding="utf-8",
                        errors="replace", timeout=180, env=env)
    check("--uninstall 摘掉前缀", r4.returncode == 0 and not pfx.exists(),
          ((r4.stdout or "") + (r4.stderr or ""))[-200:])
    check("--uninstall 也摘掉 agent skill (只摘它自己建的那个目录)", not skill.exists(),
          "skill 还在")
    check("--uninstall 也摘掉 lompi 的指南", not lompi_skill.exists(), "lompi skill 还在")
    check("--uninstall 还原别家 agent 的文件 (只摘标记段, 原有内容不动)",
          codex.read_text(encoding="utf-8") == "MY OWN RULES\nsecond line\n",
          codex.read_text(encoding="utf-8")[:80])
    shutil.rmtree(pfx, ignore_errors=True)
    shutil.rmtree(home, ignore_errors=True)


# ------------------------------------------------------------------ main

def test_check_detects_staleness() -> None:
    """`--check` 必须能发现"归档里那份来源文件不是当前源码"。

    它原先只拿归档跟**它自己的** SHA256SUMS 比 —— 两边一起过期就永远报"一致"。
    2026-09-15 用户问"安装包更新了吗"才发现的: `use` 改动之后 `loment/dist/` 里的
    skill 与 seed 全是旧的, 而 `--check` 照样绿。这条判据就是钉那个洞。
    """
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        lin = loment_dist.payload("linux", {"loment-driver": (b"ELF", b"PE")})
        lin["share/loment/skill/SKILL.md"] = (b"stale\n", 0o644)   # 冒充「过期的归档」
        (out / f"loment-{VER}-linux-x64.tar.gz").write_bytes(
            loment_dist._tar_gz(f"loment-{VER}-linux-x64", lin))
        loment_dist.write_sums(out)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = loment_dist.check(out)
        check("--check 能发现归档里的来源文件过期 (原先是个洞)", rc != 0, f"rc={rc}")
    # 版本文件也带提交号, 所以它同属"打完包又提交了一次"那一类 —— 2026-09-16 之前它
    # **不在** _fresh_sources 里, 于是 0.1.4-pre1 的包里印的是上一个提交号的 commit 行,
    # 而 `--check` 照样绿 (真实事故, 不是假想)。
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        lin = loment_dist.payload("linux", {"loment-driver": (b"ELF", b"PE")})
        lin["share/loment/version"] = (b"Loment 9.9.9 Stale (9.9.9-stale)\ncommit 0000000\n",
                                       0o644)
        (out / f"loment-{VER}-linux-x64.tar.gz").write_bytes(
            loment_dist._tar_gz(f"loment-{VER}-linux-x64", lin))
        loment_dist.write_sums(out)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = loment_dist.check(out)
        check("--check 能发现归档里的 version 文件过期 (提交号印的是上一代)", rc != 0,
              f"rc={rc}")
    ver = loment_dist.version_text()
    check("version 文件的提交号就是当前 HEAD",
          loment_dist._git("rev-parse", "--short", "HEAD") in ver, ver.strip())


def test_skill_example_sync() -> None:
    """skill §1 贴的那份 `tour` 必须**逐字节**等于 `loment/examples/tour.lomt`, 而且能过前端。

    **Why**: 这一块是给"只装了包、没有仓库"的 agent 当**唯一**参考的 —— 它错了, 用户
    照着敲就得到一个编不过的程序, 且没有任何别的东西能纠正他。
    2026-09-15 真的发生了: 复制进 SKILL.md 时漏掉 `const` 后的 `;`, 而 skill 正文
    自称"和仓库里那份是同一份" —— 没有任何判据守着这句话。
    **How to apply**: 改 `loment/examples/tour.lomt` 之后必须同步改 skill (以及
    `~/.claude/skills/`), 否则 `loment_dist_test` 红。
    """
    sk = (ROOT / loment_dist.SKILL).read_text(encoding="utf-8")
    blocks = [b for b in re.findall(r"```rust\n(.*?)```", sk, re.S) if "module tour" in b]
    check("skill 里有且只有一份 tour 全文", len(blocks) == 1, f"命中 {len(blocks)} 块")
    if len(blocks) != 1:
        return
    src_text = blocks[0].strip()
    ex = (ROOT / "loment/examples/tour.lomt").read_text(encoding="utf-8")
    body = ex[ex.index("module tour"):].strip()
    check("skill §1 == loment/examples/tour.lomt (逐字节)", src_text == body)

    src = Path(tempfile.mkdtemp(prefix="lom_skill_")) / "tour.lomt"
    src.write_text(src_text + "\n", encoding="utf-8")
    try:
        mod = lomentc.load(src)
        errs = lomentc.check(mod, deps=lomentc.resolve_deps(mod, ROOT, src.parent, entry=src))
    except lomentc.LomError as e:
        errs = [str(e)]
    check("skill §1 能过 check (不是一段装饰性代码)", not errs, errs[0] if errs else "")


def test_skill_samples_compile() -> None:
    """skill 里**每个** ```rust 样例都必须是能过前端的真代码。

    **Why**: 指南是"只装了包、没有仓库"的 agent 看到的唯一参考。样例错一个字符,
    他就卡在那里, 而且没有任何东西能告诉他哪边错了 —— 2026-09-15 实测过一次
    (`tour` 里漏了个 `;`, 指南还自称"和仓库里那份是同一份")。
    **How to apply**: 有两种片段要跳过, 都在围栏前一行加 `<!-- no-compile -->`:

    1. **故意写错**的片段 (展示"这样会报 E22"之类);
    2. **半块**片段 —— 需要**伴生文件**才编得过的那种 (实测: `addin` 的入口那一半,
       它的 `set choose` 定义在旁边的 `chooseset.lomt` 里)。这类**必须写清为什么**,
       并指到仓库里真能跑的那一对 (`loment/examples/addin/`) —— 否则"跳过"就成了
       悄悄放宽。

    只对 `rust` 围栏生效; 别的语言围栏不在扫描范围内。
    """
    sk = (ROOT / loment_dist.SKILL).read_text(encoding="utf-8")
    td = Path(tempfile.mkdtemp(prefix="lom_skill_"))
    n = 0
    for i, m in enumerate(re.finditer(r"```rust\n(.*?)```", sk, re.S)):
        before = sk[max(0, m.start() - 80):m.start()]
        if "no-compile" in before:
            continue
        src = td / f"sample{i}.lomt"
        src.write_text(m.group(1), encoding="utf-8", newline="\n")
        try:
            mod = lomentc.load(src)
            errs = lomentc.check(mod, deps=lomentc.resolve_deps(mod, ROOT, td, entry=src))
        except lomentc.LomError as e:
            errs = [str(e)]
        n += 1
        check(f"skill 样例 #{i} 过前端", not errs, errs[0] if errs else "")
    check("skill 里找到了 rust 样例 (判据没空转)", n > 0, "一块都没扫到")


def main() -> int:
    print("loment_dist_test —— 发行包判据 (docs/162)")
    for name, fn in (("布局与脚本卫生", test_layout), ("归档内容与确定性", test_archives),
                     ("--check 的新鲜度", test_check_detects_staleness),
                     ("skill 与示例同步", test_skill_example_sync),
                     ("skill 样例都能编", test_skill_samples_compile)):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            check(name, False, f"{type(e).__name__}: {e}")
    tar, zipf = build()
    try:
        test_install_sh(tar)
    except Exception as e:  # noqa: BLE001
        check("install.sh 端到端", False, f"{type(e).__name__}: {e}")
    try:
        test_windows_installer(zipf)
    except Exception as e:  # noqa: BLE001
        check("Windows 安装脚本", False, f"{type(e).__name__}: {e}")
    bad = [r for r in RESULTS if not r[1]]
    print(f"\nloment_dist_test: {len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    for name, _ok, detail in bad:
        print(f"  FAIL  {name}  [{detail}]")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
