#!/usr/bin/env python3
# loment_seed.py — 无 Python 自举的**种子** (M83 延伸, docs/159)
#
# 自举链原先只能从"Python 版编译器"起步 (M83 的 stage1 由 lomentc 现编)。本工具把
# 那个起点**固化成一个已提交的工件**: 参考实现为 `selfhost/driver.lomt` 发射的 IR。
# 有了它, 整条链只需要 clang + POSIX sh (`loment/bootstrap.sh`), 不需要 Python:
#
#   clang(种子 .ll) -> stage1 --driver.lomt--> IR(== 种子) -> stage2 -> ... 定点
#
# 用法:
#   python tools/loment_seed.py --emit       写 loment/build/selfhost_driver.ll
#   python tools/loment_seed.py --check      种子与参考实现一致? (不写盘)
#   python tools/loment_seed.py --bootstrap  跑 loment/bootstrap.sh (Windows 走 WSL)
#   python tools/loment_seed.py --script-ok  纯静态: 启动脚本无第三方语言/无 CRLF
# 退出码: 0 = 一致/成功 / 1 = 有差异或失败 / 2 = 环境缺失。

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "loment" / "build" / "selfhost_driver.ll"
ENTRY = ROOT / "loment" / "selfhost" / "driver.lomt"
SCRIPT = ROOT / "loment" / "bootstrap.sh"
SH_REL = "loment/bootstrap.sh"


def reference_ir() -> str:
    """参考实现为自举驱动发射的单元 IR —— 种子应与它逐字符相同。"""
    mod = lomentc.load(ENTRY)
    deps = lomentc.resolve_deps(mod, ROOT, ENTRY.parent, entry=ENTRY)
    errs = lomentc.check(mod, deps=deps)
    if errs:
        raise SystemExit(f"[ERR] driver.lomt 自己检查不过: {errs[0]}")
    return lomentc.emit_llvm(mod, ROOT, deps)


def check() -> int:
    want = reference_ir()
    if not SEED.exists():
        print(f"[ERR] 缺种子 {SEED.relative_to(ROOT)} (运行 --emit)")
        return 1
    got = SEED.read_text(encoding="utf-8")
    if got != want:
        bad = next((i for i in range(min(len(got), len(want))) if got[i] != want[i]), None)
        print(f"[DIFF] 种子过期: 盘上 {len(got)}B vs 参考 {len(want)}B @{bad} "
              f"(运行 --emit 重新固化; 自举产物变了就必须跟上)")
        return 1
    print(f"loment_seed: 种子与参考逐字符一致 ({len(want)}B)")
    return 0


def emit() -> int:
    want = reference_ir()
    SEED.parent.mkdir(parents=True, exist_ok=True)
    with SEED.open("w", encoding="utf-8", newline="\n") as f:
        f.write(want)
    print(f"[OK] {SEED.relative_to(ROOT)} ({len(want)}B)")
    return 0


#: 启动脚本: **只允许 clang + 宿主 shell**, 不许出现任何解释器。这两个文件是"没有 Python
#: 也能构建/使用 Loment"的全部入口, 所以它们自己不能偷偷调解释器。
LAUNCH_SCRIPTS = ("loment/bootstrap.sh", "scripts/lomc.ps1")

#: 命令位置不许出现的解释器。
FORBIDDEN = ("python", "python3", "perl", "ruby", "node", "cargo", "rustc", "gcc")


def script_ok() -> int:
    """静态判据: 启动脚本只用 clang (无第三方语言调用), 且是 LF 换行。

    注释里的提法不算 (`# python ...` 只是说明); 只看**命令位置** —— 行首或 `| & ; (` 之后。
    """
    bad: list[str] = []
    for rel in LAUNCH_SCRIPTS:
        p = ROOT / rel
        if not p.exists():
            bad.append(f"缺 {rel}")
            continue
        raw = p.read_bytes()
        if b"\r\n" in raw:
            bad.append(f"{rel} 含 CRLF (WSL/Linux 的 sh 会把 \\r 当命令字符; "
                       f"见 .gitattributes 的 *.sh/*.ps1 eol=lf)")
        text = raw.decode("utf-8", errors="replace")
        for ln, line in enumerate(text.splitlines(), 1):
            code = line.split("#", 1)[0]
            for name in FORBIDDEN:
                if re.search(rf"(?:^|[|&;(])\s*{re.escape(name)}\b", code):
                    bad.append(f"{rel}:{ln} 调用了 {name}: {line.strip()[:70]}")
    if bad:
        for b in bad:
            print(f"[FAIL] {b}")
        return 1
    print(f"loment_seed: 启动脚本 {len(LAUNCH_SCRIPTS)} 个只用 clang "
          f"(无第三方语言调用, LF 换行)")
    return 0


def bootstrap() -> int:
    """跑无 Python 自举 (本机不能执行 Linux ELF 时走 WSL)。

    参数表调用, 不拼 shell 字符串: WSL 把进程的当前目录翻成 /mnt/... 传进去,
    脚本自己再 `cd` 到仓库根 (它按 $0 定位), 所以不依赖调用方的工作目录。
    """
    if not SCRIPT.exists():
        print(f"[ERR] 缺 {SCRIPT.relative_to(ROOT)}")
        return 2
    if shutil.which("wsl"):
        cmd = ["wsl", "-e", "sh", SH_REL]
    elif shutil.which("sh"):
        cmd = ["sh", SH_REL]
    else:
        print("[ERR] 既无 wsl 也无 sh")
        return 2
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900,
                       shell=False, cwd=str(ROOT))
    sys.stdout.write(r.stdout)
    if r.returncode:
        sys.stderr.write(r.stderr[-800:])
        print(f"[FAIL] 无 Python 自举失败 (退出 {r.returncode})")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_seed", description="无 Python 自举的种子")
    ap.add_argument("--emit", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--bootstrap", action="store_true")
    ap.add_argument("--script-ok", action="store_true")
    a = ap.parse_args(argv)
    if a.emit:
        return emit()
    if a.check:
        return check()
    if a.bootstrap:
        return bootstrap()
    if a.script_ok:
        return script_ok()
    print("[ERR] 需要 --emit / --check / --bootstrap / --script-ok", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
