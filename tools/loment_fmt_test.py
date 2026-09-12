#!/usr/bin/env python3
# loment_fmt_test.py — Loment 版格式化器与 Python 版的行为等价 (docs/159)
#
# 判据: 同一份源码喂 `loment/tools/lomfmt.lomt` (链成 ELF 后跑) 与 `tools/lomfmt.py`,
# **stdout 逐字节相同**。语料 = loment/examples、loment/selfhost、以及格式化器自己。
#
# 这是"脱离第三方语言"里工具链那一格的第一块: 格式化器不再需要 Python 就能跑,
# 判据是它和旧实现的输出一模一样 (包括旧实现把 tok.val 当字符串比较的那些怪癖)。
#
# 运行: python tools/loment_fmt_test.py   (无 clang/WSL 时 SKIP, 退出码 0)

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402
import lomfmt   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "loment" / "tools" / "lomfmt.lomt"
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
    assert not errs, f"lomfmt.lomt 自己检查不过: {errs[:2]}"
    ll = td / "lomfmt.ll"
    with ll.open("w", encoding="utf-8", newline="\n") as f:
        f.write(lomentc.emit_llvm(mod, ROOT, deps))
    elf = td / "fujofmt.elf"
    r = subprocess.run(
        [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fuse-ld=lld", "-o", str(elf), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    return elf


def _corpus() -> list[Path]:
    files = sorted((ROOT / "loment" / "examples").glob("*.lomt"))
    files += sorted((ROOT / "loment" / "selfhost").glob("*.lomt"))
    files.append(SRC)
    return files


def _run(elf: Path, td: Path, entry: Path, name: str) -> tuple[str, str]:
    """在 WSL 里跑格式化器 (ELF 是 Linux 目标, 拷进 /tmp 才能执行)。"""
    got = td / f"{name}.out"
    err = td / f"{name}.err"
    script = (f"rm -f /tmp/{name}.bin && cp {_wsl_path(elf)} /tmp/{name}.bin && "
              f"chmod +x /tmp/{name}.bin && "
              f"cd {_wsl_path(ROOT)} && /tmp/{name}.bin {entry.relative_to(ROOT).as_posix()} "
              f"> {_wsl_path(got)} 2> {_wsl_path(err)}; echo -n $?")
    r = subprocess.run(["wsl", "-e", "bash", "-lc", script],
                       capture_output=True, text=True, timeout=300, shell=False)
    out = got.read_text(encoding="utf-8") if got.exists() else ""
    etext = err.read_text(encoding="utf-8", errors="replace") if err.exists() else ""
    return out, f"rc={r.stdout.strip()} {etext[-200:]}"


@test
def test_loment_fmt_matches_python():
    """语料上逐字节相同 —— 这就是"格式化器可以脱离 Python"的判据。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)
        bad = []
        for f in _corpus():
            want = lomfmt.format_source(f.read_text(encoding="utf-8"))
            got, note = _run(elf, td, f, f.stem)
            if got != want:
                k = next((i for i in range(min(len(got), len(want))) if got[i] != want[i]),
                         min(len(got), len(want)))
                bad.append(f"{f.name} ({note}, want {len(want)}B got {len(got)}B @{k})")
        assert not bad, "与 Python 版不一致: " + "; ".join(bad[:4])
        print(f"      {len(_corpus())} 个语料逐字节相同")


@test
def test_loment_fmt_edge_cases():
    """空文件/只有注释/只有字符串/CRLF —— Python 版这些边界上的行为也得一致。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    cases = {
        "empty": "",
        "comment_only": "// 只有注释\n",
        "string_only": 'module x\n\nconst C: u32 = 1;\n\nfn f() -> str {\n    return "a\\\\b\\"c";\n}\n',
        "crlf": 'module x\r\n\r\nfn f() -> u32 {\r\n    return 1;\r\n}\r\n',
    }
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)
        probe = ROOT / "loment" / "build" / "fmt_edge_case.lomt"
        bad = []
        for name, text in cases.items():
            with probe.open("w", encoding="utf-8", newline="") as f:
                f.write(text)
            want = lomfmt.format_source(text)
            got, note = _run(elf, td, probe, f"edge_{name}")
            if got != want:
                bad.append(f"{name} ({note}): want {want!r} got {got!r}")
        probe.unlink(missing_ok=True)
        assert not bad, "; ".join(bad)
        print(f"      {len(cases)} 个边界用例一致")


@test
def test_loment_fmt_is_idempotent():
    """幂等: 格式化自己的输出还是它 (Python 版的判据之一, 这里靠等价性继承)。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)
        probe = ROOT / "loment" / "build" / "fmt_idem_case.lomt"
        bad = []
        for f in _corpus()[:6]:
            once = lomfmt.format_source(f.read_text(encoding="utf-8"))
            with probe.open("w", encoding="utf-8", newline="\n") as g:
                g.write(once)
            twice, note = _run(elf, td, probe, f"idem_{f.stem}")
            if twice != once:
                bad.append(f"{f.name} ({note})")
        probe.unlink(missing_ok=True)
        assert not bad, "不幂等: " + ", ".join(bad)
        print("      6 个语料格式化两次结果相同")


def main() -> int:
    failed = []
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, e))
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\nloment_fmt_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
