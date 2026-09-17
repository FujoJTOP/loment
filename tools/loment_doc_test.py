#!/usr/bin/env python3
# loment_doc_test.py — Loment 版 lomdoc 与 Python 版逐字节相同 (docs/159 §4b)
#
# 判据: 同一份 .lomt 喂 `loment/tools/lomdoc.lomt`(链成 ELF 后跑) 与 `tools/lomdoc.py`,
# stdout 必须一模一样。语料 = loment/examples + loment/selfhost + loment/tools。
#
# 运行: python tools/loment_doc_test.py   (无 clang/WSL 时 SKIP, 退出码 0)

from __future__ import annotations

import os

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402
import lomdoc   # noqa: E402

#: WSL 侧临时路径前缀 —— **每个进程一份**。WSL 的 `/tmp` 是所有 `wsl -e` 调用
#: 共用的, 固定文件名在**并发跑门禁**时会让两个进程互相跑对方的二进制 ——
#: 那是**错结果**, 不是慢。见 `ci.py` 的 `-j`。
_T = f"/tmp/loment-{os.getpid()}-"

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "loment" / "tools" / "lomdoc.lomt"
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
    assert not errs, f"lomdoc.lomt 自己检查不过: {errs[:2]}"
    ll = td / "lomdoc.ll"
    with ll.open("w", encoding="utf-8", newline="\n") as f:
        f.write(lomentc.emit_llvm(mod, ROOT, deps))
    elf = td / "lomdoc.elf"
    r = subprocess.run(
        [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fuse-ld=lld", "-o", str(elf), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    return elf


def _corpus() -> list[Path]:
    files = sorted((ROOT / "loment" / "examples").glob("*.lomt"))
    files += sorted((ROOT / "loment" / "selfhost").glob("*.lomt"))
    files += sorted((ROOT / "loment" / "tools").glob("*.lomt"))
    return files


def _want(p: Path, name: str) -> str | None:
    """Python 版输出; 语义错误 (lomdoc 会拒绝) 返回 None。

    `name` 必须是**命令行给的那个路径** —— 参考实现把参数原样写进文档头, Loment 版也一样,
    所以两边要比同一个字符串 (否则比的是"相对 vs 绝对", 不是工具差异)。
    """
    mod = lomentc.load(p)
    deps = lomentc.resolve_deps(mod, ROOT, p.parent, entry=p)
    if lomentc.check(mod, deps=deps):
        return None
    return lomdoc.render(mod, p.read_text(encoding="utf-8"), name)


def _run(elf: Path, td: Path, entry: Path, name: str) -> tuple[str, str]:
    got = td / f"{name}.out"
    err = td / f"{name}.err"
    script = (f"rm -f {_T}{name}.bin && cp {_wsl_path(elf)} {_T}{name}.bin && "
              f"chmod +x {_T}{name}.bin && "
              f"cd {_wsl_path(ROOT)} && {_T}{name}.bin {entry.relative_to(ROOT).as_posix()} "
              f"> {_wsl_path(got)} 2> {_wsl_path(err)}; echo -n $?")
    r = subprocess.run(["wsl", "-e", "bash", "-lc", script],
                       capture_output=True, text=True, timeout=300, shell=False)
    out = got.read_text(encoding="utf-8") if got.exists() else ""
    etext = err.read_text(encoding="utf-8", errors="replace") if err.exists() else ""
    return out, f"rc={r.stdout.strip()} {etext[-200:]}"


@test
def test_loment_lomdoc_matches_python():
    """全语料逐字节相同 —— "文档生成器可以脱离 Python" 的判据。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    files = _corpus()
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)
        bad, n = [], 0
        for f in files:
            rel = f.relative_to(ROOT).as_posix()
            want = _want(f, rel)
            if want is None:
                continue
            n += 1
            got, note = _run(elf, td, f, f.stem)
            if got != want:
                k = next((i for i in range(min(len(got), len(want))) if got[i] != want[i]),
                         min(len(got), len(want)))
                bad.append(f"{f.name} ({note}, want {len(want)}B got {len(got)}B @{k})\n"
                           f"      want: {want[max(0,k-60):k+40]!r}\n"
                           f"      got : {got[max(0,k-60):k+40]!r}")
        assert not bad, "\n".join(bad[:3])
        print(f"      {n} 个语料逐字节相同")


EDGE_CASE = '''module lomdoc_edge

/// 能力域文档: 可撤销的写权限
capability w : disk[0..4] revocable

/// 不可撤销的那个
capability r : disk[8..12]

excluded "net: 本单元不申请 net 能力"

/// 常量文档
/// 第二行也要拼上
const HDR: u32 = 8;

/// 十六进制字面量在文档里要打印成十进制
const MASK: u32 = 0x10;

/// 类型文档
struct Pair {
    a: u32,
    b: u32,
}

/// 枚举文档
enum Color {
    Red,
    Rgb(u32),
}

/// trait 文档
trait Show {
    fn show(self) -> u32;
    fn tag(self, k: u32) -> u32;
}

impl Show for Pair {
    fn show(self) -> u32 {
        return self.a;
    }

    fn tag(self, k: u32) -> u32 {
        return k;
    }
}

/// 泛型函数的形参不进文档头
fn max_of<T>(a: T, b: T) -> T {
    return a;
}

fn no_doc() {
    let x: u32 = 1;
}
'''


@test
def test_loment_lomdoc_edge_cases():
    """语料里没出现的段: excluded / 十六进制常量 / 多行 doc / 双方法 trait / 空 doc。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    probe = ROOT / "loment" / "build" / "lomdoc_edge.lomt"
    probe.parent.mkdir(parents=True, exist_ok=True)
    try:
        with probe.open("w", encoding="utf-8", newline="\n") as f:
            f.write(EDGE_CASE)
        rel = probe.relative_to(ROOT).as_posix()
        want = _want(probe, rel)
        assert want is not None, "边界用例自己没过语义检查 (改用例本身)"
        with tempfile.TemporaryDirectory() as tds:
            td = Path(tds)
            elf = _build(td)
            got, note = _run(elf, td, probe, "edge")
            if got != want:
                k = next((i for i in range(min(len(got), len(want))) if got[i] != want[i]),
                         min(len(got), len(want)))
                gl = want[:k].count("\n") + 1
                raise AssertionError(
                    f"边界用例不一致 ({note}) 首个差异在第 {gl} 行 @{k}:\n"
                    f"  want: {want[max(0,k-70):k+50]!r}\n"
                    f"  got : {got[max(0,k-70):k+50]!r}")
    finally:
        probe.unlink(missing_ok=True)
    print("      边界用例 (excluded/hex/多行 doc/双方法 trait/泛型/空 doc) 一致")


def main() -> int:
    failed = []
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, e))
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\nloment_doc_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
