#!/usr/bin/env python3
# loment_genesis.py — 链条的"第一个可执行文件" (docs/167 §5 ②)
#
# 背景: 自 0.1.4 Alpha2 起, `loment/tools/lomelf.lomt` 能把种子 `.ll` 编成一个能用的
# Loment 编译器 (判据 loment_elf_test 的 test_lomelf_selfhost_rebuilds_the_compiler)。
# 于是 `bootstrap.sh` 里 clang 只剩下"种子 -> stage1"这一步 —— 而这一步可以换成
# **一个提交进仓库的二进制**。
#
# 这个二进制就是 genesis: `loment/build/genesis/lomelf-linux-x64.elf` —— 由种子构建出来的
# `lomelf`。有了它, `sh loment/bootstrap.sh` **不再需要 clang, 也不需要任何解释器**
# (自举链的每一条 `.ll` 都由 genesis 自己汇编)。
#
# 它是**可复现**的: 同一份种子 + 同一个 clang/lld, 两次构建 sha256 相同 (judged in --check 的
# 注释里; 真正钉住它的是 tools/loment_genesis_test.py)。
#
# 残留的诚实点 (写明, 不装作没有): 链条总要有**第一个**可执行文件, genesis 就是那个。
# 它有来源 (种子 + 一次 clang)、有哈希、可复现, 但它是个二进制 —— 这正是 docs/167 §5 ②
# 记的那条。
#
#   python tools/loment_genesis.py --emit    # 重建 genesis + 写 SHA256SUMS (需要 clang/WSL)
#   python tools/loment_genesis.py --check   # 只核对哈希 (不需要 clang)
#   python tools/loment_genesis.py           # 同 --check

from __future__ import annotations

import os

import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

#: WSL 侧临时路径前缀 —— **每个进程一份**。WSL 的 `/tmp` 是所有 `wsl -e` 调用
#: 共用的, 固定文件名在**并发跑门禁**时会让两个进程互相跑对方的二进制 ——
#: 那是**错结果**, 不是慢。见 `ci.py` 的 `-j`。
_T = f"/tmp/loment-{os.getpid()}-"

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "loment" / "build" / "selfhost_driver.ll"
MIRROR = ROOT / "loment" / "tools" / "lomelf.lomt"
GEN_DIR = ROOT / "loment" / "build" / "genesis"
GEN_NAME = "lomelf-linux-x64.elf"
GEN = GEN_DIR / GEN_NAME
SUMS = GEN_DIR / "SHA256SUMS"
TARGET = "x86_64-unknown-linux-gnu"


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


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def emit() -> int:
    cc = _clang()
    if not (cc and _wsl()):
        print("[ERR] --emit 需要 clang 与 WSL", file=sys.stderr)
        return 1
    if not SEED.exists():
        print(f"[ERR] 缺种子 {SEED.relative_to(ROOT)}", file=sys.stderr)
        return 1
    GEN_DIR.mkdir(parents=True, exist_ok=True)
    work = GEN_DIR / "_work"
    work.mkdir(exist_ok=True)
    try:
        stage1 = work / "stage1"
        r = subprocess.run(
            [cc, f"--target={TARGET}", "-nostdlib", "-ffreestanding", "-static",
             "-fuse-ld=lld", "-o", str(stage1), str(SEED)],
            capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-300:]
        # stage1 编 lomelf.lomt -> IR (自举路, 无 Python)
        ir = work / "lomelf.ll"
        script = (f"cp {_wsl_path(stage1)} {_T}gen_s1.bin && chmod +x {_T}gen_s1.bin && "
                  f"cd {_wsl_path(ROOT)} && {_T}gen_s1.bin "
                  f"{MIRROR.relative_to(ROOT).as_posix()} > {_wsl_path(ir)}")
        rr = subprocess.run(["wsl", "-e", "bash", "-lc", script],
                            capture_output=True, text=True, timeout=900, shell=False)
        assert rr.returncode == 0, f"stage1 编镜像失败: {rr.stderr[-300:]}"
        # IR -> genesis ELF
        r2 = subprocess.run(
            [cc, f"--target={TARGET}", "-nostdlib", "-ffreestanding", "-static", "-fno-pie",
             "-fuse-ld=lld", "-Wl,-e,_start", str(ir), "-o", str(GEN)],
            capture_output=True, text=True, shell=False)
        assert r2.returncode == 0, r2.stderr[-300:]
    finally:
        shutil.rmtree(work, ignore_errors=True)
    SUMS.write_text(f"{_sha256(GEN)}  {GEN_NAME}\n", encoding="utf-8", newline="\n")
    print(f"[OK] {GEN.relative_to(ROOT)} ({GEN.stat().st_size} B)  sha256 {_sha256(GEN)[:16]}…")
    print(f"[OK] {SUMS.relative_to(ROOT)}")
    return 0


def check() -> int:
    if not GEN.exists():
        print(f"[ERR] 缺 genesis {GEN.relative_to(ROOT)} (用 --emit 重建)", file=sys.stderr)
        return 1
    notes = []
    ok = True
    if SUMS.exists():
        want = SUMS.read_text(encoding="utf-8").split()[0]
        got = _sha256(GEN)
        if want == got:
            notes.append(f"哈希一致 ({got[:16]}…)")
        else:
            notes.append(f"[ERR] 哈希不符: 记录 {want[:16]}… 实际 {got[:16]}…")
            ok = False
    else:
        notes.append("[ERR] 缺 SHA256SUMS")
        ok = False
    # 启动脚本必须真的用它 (否则 genesis 只是躺在那)
    boot = (ROOT / "loment" / "bootstrap.sh").read_text(encoding="utf-8")
    if "genesis" not in boot:
        notes.append("[ERR] bootstrap.sh 没有引用 genesis")
        ok = False
    else:
        notes.append("bootstrap.sh 引用了 genesis")
    for n in notes:
        print(f"  {'[OK]' if not n.startswith('[ERR]') else ''} {n}".rstrip())
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_genesis", description="链条的第一个可执行文件 (docs/167)")
    ap.add_argument("--emit", action="store_true", help="重建 genesis (需要 clang/WSL)")
    ap.add_argument("--check", action="store_true", help="核对哈希与启动脚本引用")
    a = ap.parse_args(argv)
    return emit() if a.emit else check()


if __name__ == "__main__":
    sys.exit(main())
