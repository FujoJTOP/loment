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

import os

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomc     # noqa: E402
import lomentc  # noqa: E402
import lomfmt   # noqa: E402

#: WSL 侧临时路径前缀 —— **每个进程一份**。WSL 的 `/tmp` 是所有 `wsl -e` 调用
#: 共用的, 固定文件名在**并发跑门禁**时会让两个进程互相跑对方的二进制 ——
#: 那是**错结果**, 不是慢。见 `ci.py` 的 `-j`。
_T = f"/tmp/loment-{os.getpid()}-"

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
    # `comefor` 的演示也进语料（`docs/184` §9 S4.3）：方言区**也**要被格式化 —— 见
    # `test_fmt_preserves_dialect_tokens` 那条（它说明为什么"不透明块"对 `comefor`
    # 是多余的）。
    files += sorted((ROOT / "loment" / "comefor").glob("*.lomt"))
    files.append(SRC)
    return files


@test
def test_fmt_preserves_dialect_tokens():
    """**格式化不改变 token 序列** —— 于是方言区被格式化了也无所谓（`docs/184` §9 S4.3）。

    `docs/184` §9 给 S4.3 定的判据里有"`lomfmt` 不碰方言区"。**对 `comefor` 来说那条
    是多余的**：方言区里的东西**仍然是 Loment 的 token**（`reg LEDS: 0x4000 { … }` 也是
    标识符/标点/数字），而宏体读的就是 token。所以只要格式化**保序**，宏体看到的输入
    一个字都没变 —— "别碰它"是在保护一件本来就不会坏的事。

    **那条判据要等到有"非 Loment 词法"的方言区**才有内容（`docs/183` §8 的 S1：
    外部代码块按原字节携带）。这里把这个区别钉下来，免得后来的人按 §9 的字面去写一个
    用不上的"不透明块"。

    判据就是保序 + 幂等，对**每一份**含有 `comefor` 的语料都跑 —— 不依赖 clang/WSL。
    """
    files = sorted((ROOT / "loment" / "comefor").glob("*.lomt"))
    assert files, "语料空了 —— 判据会空转"
    for f in files:
        src = f.read_text(encoding="utf-8")
        out = lomfmt.format_source(src)
        a = [(t.kind, t.val) for t in lomc.lex(src) if t.kind != "eof"]
        b = [(t.kind, t.val) for t in lomc.lex(out) if t.kind != "eof"]
        assert a == b, f"{f.name}: 格式化改变了 token 序列（{len(a)} -> {len(b)}）"
        assert lomfmt.format_source(out) == out, f"{f.name}: 格式化不幂等"
    print(f"      保序: {len(files)} 份方言语料格式化后 token 序列一字未变")


def _run(elf: Path, td: Path, entry: Path, name: str) -> tuple[str, str]:
    """在 WSL 里跑格式化器 (ELF 是 Linux 目标, 拷进 /tmp 才能执行)。"""
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
