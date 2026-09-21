#!/usr/bin/env python3
# loment_rel_test.py — Loment 版发布清单与 Python 版逐字节相同 (docs/167 Stage 3)
#
# 判据: `loment/tools/lomrel.lomt` (链成 ELF 后跑) 与 `tools/loment_release.py`:
#   1. 无参数 (门禁模式) 与 `--check`: stdout / stderr / 退出码逐字节相同;
#   2. `--emit`: 写出的 release-manifest.json 逐字节相同, stdout 也相同;
#   3. `--checksums PATH`: 写出的清单逐字节相同 (路径不同则比内容), stdout 除路径外相同;
#   4. `lomrel.lomt` 必须能走**种子自举链**编译 (无 Python 参与编译器本身)。
#
# 平台口径 (重要): Python 的 `sorted(Path)` 在 **Windows 上按 normcase 小写比**, 在 Linux 上
# 按字节比 —— 同一个 glob 两边顺序会不同。本工具镜像是 **Windows** 的语义 (判据跑在本机
# Windows Python 上, 用进程内 _py 而不是 WSL 的 python3); 跨平台清单顺序差异见 lomrel.lomt 头注。
#
# 运行: python tools/loment_rel_test.py   (无 clang/WSL 时 SKIP, 退出码 0)

from __future__ import annotations

import os
import re

import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402
import loment_release  # noqa: E402

#: WSL 侧临时路径前缀 —— **每个进程一份**。WSL 的 `/tmp` 是所有 `wsl -e` 调用
#: 共用的, 固定文件名在**并发跑门禁**时会让两个进程互相跑对方的二进制 ——
#: 那是**错结果**, 不是慢。见 `ci.py` 的 `-j`。
_T = f"/tmp/loment-{os.getpid()}-"

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "loment" / "tools" / "lomrel.lomt"
MANIFEST = ROOT / "loment" / "build" / "release-manifest.json"
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _clang() -> str | None:
    p = shutil.which("clang")
    if p:
        return p
    fallback = r"C:\Program Files\LLVM\bin\clang.exe"
    return fallback if Path(fallback).exists() else None


def _wsl() -> bool:
    if not shutil.which("wsl"):
        return False
    try:
        return subprocess.run(["wsl", "-e", "true"], capture_output=True,
                              text=True, timeout=60, shell=False).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    return "/mnt/" + s[0].lower() + s[2:]


def _build(td: Path) -> Path:
    mod = lomentc.load(SRC)
    deps = lomentc.resolve_deps(mod, ROOT, SRC.parent, entry=SRC)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"lomrel.lomt 自己检查不过: {errs[:2]}"
    ll = td / "lomrel.ll"
    with ll.open("w", encoding="utf-8", newline="\n") as f:
        f.write(lomentc.emit_llvm(mod, ROOT, deps))
    elf = td / "lomrel.elf"
    r = subprocess.run(
        [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fuse-ld=lld", "-o", str(elf), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    return elf


def _norm(v: str) -> str:
    """只归一路径分隔符 (Windows 的 Python 打印 docs\\x, Linux 侧是 docs/x)。"""
    return v.replace(chr(92), "/")


def _py(args: list[str]) -> tuple[int, str, str]:
    """进程内跑 Python 版并捕获两路输出 (见 loment_pkg_test 的同一理由: 避免换行翻译)。"""
    buf, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = loment_release.main(args)
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    return rc, buf.getvalue(), err.getvalue()


def _run(elf: Path, td: Path, name: str, args: list[str], timeout: int = 600) -> tuple[int, str, str]:
    outp = td / f"{name}.out"
    errp = td / f"{name}.err"
    binn = f"{_T}lomrel_{name}.bin"
    quoted = " ".join(args)
    script = (f"rm -f {binn} && cp {_wsl_path(elf)} {binn} && chmod +x {binn} && "
              f"cd {_wsl_path(ROOT)} && {binn} {quoted} "
              f"> {_wsl_path(outp)} 2> {_wsl_path(errp)}; echo -n $?")
    r = subprocess.run(["wsl", "-e", "bash", "-lc", script],
                       capture_output=True, text=True, timeout=timeout, shell=False)
    out = outp.read_text(encoding="utf-8") if outp.exists() else ""
    err = errp.read_text(encoding="utf-8") if errp.exists() else ""
    try:
        rc = int(r.stdout.strip())
    except ValueError:
        rc = -1
    return rc, out, err


def _pair(elf: Path, td: Path, name: str, args: list[str]) -> None:
    wr, wo, we = _py(args)
    gr, go, ge = _run(elf, td, name, args)
    if (wr, _norm(wo), _norm(we)) == (gr, go, ge):
        return
    raise AssertionError(
        f"[{name}] 不一致: rc py={wr} loment={gr}\n"
        f"  py     out={_norm(wo)[-200:]!r} err={_norm(we)[-200:]!r}\n"
        f"  loment out={go[-200:]!r} err={ge[-200:]!r}")


# ---------------------------------------------------------------- 用例

@test
def test_lomrel_matches_python():
    """无参数 (门禁模式) 与 `--check`: stdout / stderr / 退出码逐字节相同。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)
        _pair(elf, td, "noargs", [])
        _pair(elf, td, "check", ["--check"])
    print("      无参数 / --check: stdout + stderr + 退出码一致")


@test
def test_lomrel_emit_writes_same_bytes():
    """`--emit`: 写出的清单逐字节相同, stdout 也相同 (并把原清单还原)。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    orig = MANIFEST.read_bytes()
    try:
        with tempfile.TemporaryDirectory() as tds:
            td = Path(tds)
            elf = _build(td)
            rc, po, pe = _py(["--emit"])
            assert rc == 0, f"python --emit 失败 rc={rc}"
            py_bytes = MANIFEST.read_bytes()
            gr, go, ge = _run(elf, td, "emit", ["--emit"])
            sh_bytes = MANIFEST.read_bytes()
            assert (rc, _norm(po), _norm(pe)) == (gr, go, ge), (
                f"stdout 不一致:\n  py {po!r} {pe!r}\n  sh {go!r} {ge!r}")
            assert py_bytes == sh_bytes, f"落盘不同: {len(py_bytes)}B vs {len(sh_bytes)}B"
            assert py_bytes == orig, "两边写出的清单与仓库里的不同 (不该)"
    finally:
        MANIFEST.write_bytes(orig)
    print("      --emit: 清单字节 + 输出行一致 (与仓库现有清单相同)")


@test
def test_lomrel_checksums_same_bytes():
    """`--checksums`: 写出的清单逐字节相同 (喂同一个输出路径)。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    out_rel = "loment/build/_rel_cks.sum"
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)
        try:
            _pair(elf, td, "cks", ["--checksums", out_rel])
            # 落盘内容比一次 (两边都写了同一个路径, 后写的必须与前次的 Python 字节相同)
            r1, p1, _e1 = _py(["--checksums", out_rel])
            a = (ROOT / out_rel).read_bytes()
            r2, g2, _e2 = _run(elf, td, "cks2", ["--checksums", out_rel])
            b = (ROOT / out_rel).read_bytes()
            assert (r1, _norm(p1)) == (r2, g2), "stdout 不一致"
            assert a == b, "校验清单字节不同"
        finally:
            (ROOT / out_rel).unlink(missing_ok=True)
    print("      --checksums: 清单字节 + 输出行一致")


@test
def test_seed_fits_lomrel_normalization_buffer():
    """清单里最大的工件要装得进 `lomrel` 的**归一化副本** —— 越界是**静默**的，所以要有判据。

    2026-09-20 实测（`docs/200` §6）：`lomrel` 的 `M_NORM` 是固定的 2 MiB，而清单里有
    **比它大的工件**（自举种子）。越界写进紧挨着的 `M_CUR`（已装载的清单）——
    症状不是"这个文件的哈希错"，是**它之后每一条都对不上**：486 条里从种子那一条起
    226 条全红，而两边的**写**（`--emit`）还是逐字节相同的，所以看上去像"清单过期"。

    与 `loment_genesis_test::test_seed_fits_lomelf_input_buffer` 同一个形状：
    从 `lomrel.lomt` 里**读**上限，不另抄一份，并把余量打出来。
    """
    src = SRC.read_text(encoding="utf-8")
    m = re.search(r"const NORM_CAP: u32 = (\d+);", src)
    assert m, f"在 {SRC.name} 里找不到 `const NORM_CAP: u32 = <数>;`"
    cap = int(m.group(1))
    worst, worst_sz = "", 0
    for p in _manifest_paths():
        f = ROOT / p
        if f.is_file() and f.stat().st_size > worst_sz:
            worst, worst_sz = p, f.stat().st_size
    assert worst_sz < cap, (
        f"清单里最大的工件 {worst}（{worst_sz} B）超过 lomrel 的归一化上限 {cap} B。"
        f"**越界不报错**，会写坏已装载的清单（症状是「后面每一条都对不上」）—— "
        f"抬 `M_NORM` / `NORM_CAP` / `M_CUR` / `ARENA`，见 docs/200 §6")
    print(f"      最大工件 {worst} {worst_sz} B / 归一化上限 {cap} B, 余量 {cap - worst_sz}")


def _manifest_paths() -> list[str]:
    """清单里点名的相对路径（**从清单里读**，与 `lomrel` 看的是同一份数据）。"""
    import json  # noqa: PLC0415
    d = json.loads(MANIFEST.read_text(encoding="utf-8"))
    files = d["files"] if isinstance(d, dict) and "files" in d else d
    return [f["path"] for f in files] if isinstance(files, list) else list(files)


@test
def test_lomrel_selfhost_compiles():
    """lomrel.lomt 必须能走**种子自举链**编译 (无 Python 参与编译器本身)。"""
    clang = _clang()
    if not (clang and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    seed = ROOT / "loment" / "build" / "selfhost_driver.ll"
    assert seed.exists(), "缺自举种子"
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        s1 = td / "stage1"
        r = subprocess.run(
            [clang, "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
             "-static", "-fuse-ld=lld", "-o", str(s1), str(seed)],
            capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-300:]
        binn = f"{_T}lomrel_s1.bin"
        script = (f"cp {_wsl_path(s1)} {binn} && chmod +x {binn} && "
                  f"cd {_wsl_path(ROOT)} && {binn} loment/tools/lomrel.lomt")
        rr = subprocess.run(["wsl", "-e", "bash", "-lc", script],
                            capture_output=True, timeout=900, shell=False)
        assert rr.returncode == 0, f"stage1 编译 lomrel.lomt 失败: {rr.stderr[-300:]}"
        assert len(rr.stdout) > 80000, f"产物太小 ({len(rr.stdout)}B)"
    print(f"      种子自举链编译 lomrel.lomt 成功 ({len(rr.stdout)}B IR)")


def main() -> int:
    failed = []
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, e))
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\nloment_rel_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
