#!/usr/bin/env python3
# loment_elf_test.py — 原生 ELF 后端: 不吃 clang 也能产出可执行文件 (docs/167)
#
# 判据: 同一份 `.ll`, 两条路各产一个 ELF, **行为逐值一致** ——
#   (a) 参考路: clang --target=x86_64-unknown-linux-gnu -nostdlib -static -fno-pie -fuse-ld=lld
#   (b) 原生路: tools/lomelf.py (纯 Python, 不调 clang)
#   比 stdout 的**字节**与**退出码**。
#
# 语料只放**会终止**的 `_start` 程序。`loment/examples/native_entry.lomt` 是 `while true`
# 的裸机入口 (M32 验证集), 按设计不终止, 不进这个判据 —— 它属于"能不能编出来", 不属于
# "跑出来一样"。
#
# 已知边界 (与 lomelf.py 头注同源, 逐条记账):
#   * 调用约定是我们自己的 (实参走栈), **不是 System V** —— 原生产物 v0 不给 C 调;
#   * 结构体动态下标 GEP / 间接调用 / 浮点 v0 不支持 (会报 [ERR] 而不是静默错编);
#   * 自举侧的镜像是 loment/tools/lomelf.lomt —— 第五条用例钉它与参考逐字节相同。
#
# 运行: python tools/loment_elf_test.py   (无 clang/WSL 时 SKIP, 退出码 0)

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402
import lomelf   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CORPUS = [
    "loment/examples/user_hello.lomt",
    "loment/examples/bootprobe.lomt",
    "loment/examples/selfcheck.lomt",
    "loment/examples/all_loment.lomt",
]
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


def _ref_ir(src: Path, td: Path) -> Path:
    """参考实现发射 IR (与 clang 路、原生路喂同一份字节)。"""
    mod = lomentc.load(src)
    deps = lomentc.resolve_deps(mod, ROOT, src.parent, entry=src)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"{src.name} 自己检查不过: {errs[:2]}"
    ll = td / (src.stem + ".ll")
    with ll.open("w", encoding="utf-8", newline="\n") as f:
        f.write(lomentc.emit_llvm(mod, ROOT, deps))
    return ll


def _link_clang(clang: str, ll: Path, out: Path) -> None:
    r = subprocess.run(
        [clang, "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fno-pie", "-fuse-ld=lld", "-Wl,-e,_start", str(ll), "-o", str(out)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, f"clang 链接失败: {r.stderr[-300:]}"


def _run_bin(elf: Path, name: str, td: Path, timeout: int = 20) -> tuple[int, bytes]:
    """在 WSL 里跑 ELF, 返回 (退出码, stdout 字节)。

    stdout 落文件、退出码单独 echo —— 不能把两者混在一个流里再切尾巴 (那会把程序自己的
    输出当成退出码, 判据会假绿)。
    """
    binn = f"/tmp/lomelf_{name}.bin"
    outp = td / f"{name}.out"
    script = (f"rm -f {binn} && cp {_wsl_path(elf)} {binn} && chmod +x {binn} && "
              f"timeout {timeout} {binn} > {_wsl_path(outp)} 2>/dev/null; echo -n $?")
    r = subprocess.run(["wsl", "-e", "bash", "-lc", script],
                       capture_output=True, timeout=timeout + 120, shell=False)
    out = outp.read_bytes() if outp.exists() else b""
    try:
        rc = int(r.stdout.decode().strip())
    except ValueError:
        rc = -1
    return rc, out


# ---------------------------------------------------------------- 用例

@test
def test_lomelf_matches_clang_behavior():
    """同一份 IR: 原生产物与 clang 产物的 stdout 字节 + 退出码完全一致。"""
    clang = _clang()
    if not (clang and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    ok = 0
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        for rel in CORPUS:
            src = ROOT / rel
            name = src.stem
            ll = _ref_ir(src, td)
            ref = td / f"{name}.clang"
            nat = td / f"{name}.native"
            _link_clang(clang, ll, ref)
            blob, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
            nat.write_bytes(blob)
            want_rc, want = _run_bin(ref, f"{name}_c", td)
            got_rc, got = _run_bin(nat, f"{name}_n", td)
            if (want_rc, want) != (got_rc, got):
                raise AssertionError(
                    f"[{name}] 行为不一致: rc clang={want_rc} native={got_rc}\n"
                    f"  clang  ({len(want)}B): {want[:200]!r}\n"
                    f"  native ({len(got)}B): {got[:200]!r}")
            ok += 1
    print(f"      {ok} 个程序的原生产物与 clang 产物 stdout 字节 + 退出码一致")


@test
def test_lomelf_reports_unsupported_instead_of_miscompiling():
    """不支持的东西必须**报错退出**, 不许静默编出一个错的 ELF。"""
    bad = [
        ("结构体动态下标 GEP",
         "define void @_start() {\nentry:\n  %p = alloca { i32, i32 }\n  %i = load i32, ptr %p\n  %q = getelementptr { i32, i32 }, ptr %p, i32 %i\n  ret void\n}\n"),
        ("间接调用",
         "define void @_start() {\nentry:\n  %f = alloca ptr\n  %g = load ptr, ptr %f\n  call void %g()\n  ret void\n}\n"),
        ("没有 _start",
         "define void @main() {\nentry:\n  ret void\n}\n"),
    ]
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        for label, text in bad:
            f = td / "bad.ll"
            f.write_text(text, encoding="utf-8", newline="\n")
            try:
                lomelf.compile_ll(text)
            except lomelf.Unsupported as e:
                assert str(e), f"{label}: 报了 Unsupported 但没有消息"
                continue
            raise AssertionError(f"[{label}] 本该报 Unsupported, 却编过去了")
    print(f"      {len(bad)} 类不支持的输入都报了错")


@test
def test_lomelf_cli_check_and_usage():
    """CLI: `--check` 不落盘且 rc=0; 用法错误 rc=2。"""
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        ll = _ref_ir(ROOT / CORPUS[0], td)
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "lomelf.py"), str(ll), "--check"],
                           capture_output=True, text=True, shell=False)
        assert r.returncode == 0, f"--check rc={r.returncode}: {r.stderr[-200:]}"
        assert "[OK]" in r.stdout, r.stdout[-200:]
        assert not (td / "user_hello").exists(), "--check 不该落盘"
        r2 = subprocess.run([sys.executable, str(ROOT / "tools" / "lomelf.py")],
                            capture_output=True, text=True, shell=False)
        assert r2.returncode == 2, f"无参数应当 rc=2, 得到 {r2.returncode}"
    print("      --check 不落盘 rc=0 · 无参数 rc=2")


@test
def test_lomelf_compiles_selfhost_ir_without_clang():
    """**主线判据**: 种子 →(clang 一次)→ stage1 → 发 IR → lomelf 编成 ELF 并跑起来。

    这正是"用户编译一个 Loment 程序不再需要 clang"的形状: clang 只在**从种子重建 stage1**
    那一步出现 (那是 genesis, docs/167 §4 记账), 之后编译用户程序全程没有 clang。
    """
    clang = _clang()
    if not (clang and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    seed = ROOT / "loment" / "build" / "selfhost_driver.ll"
    assert seed.exists(), "缺自举种子 (loment/build/selfhost_driver.ll)"
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        s1 = td / "stage1"
        r = subprocess.run(
            [clang, "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
             "-static", "-fuse-ld=lld", "-o", str(s1), str(seed)],
            capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-300:]
        # stage1 发 IR (自举路, 无 Python 无 clang)
        entry = ROOT / "loment" / "examples" / "user_hello.lomt"
        binn = "/tmp/lomelf_s1.bin"
        script = (f"cp {_wsl_path(s1)} {binn} && chmod +x {binn} && "
                  f"cd {_wsl_path(ROOT)} && {binn} loment/examples/user_hello.lomt")
        rr = subprocess.run(["wsl", "-e", "bash", "-lc", script],
                            capture_output=True, timeout=300, shell=False)
        assert rr.returncode == 0, rr.stderr[-300:]
        ll = td / "from_stage1.ll"
        ll.write_bytes(rr.stdout)
        assert b"_start" in rr.stdout, "stage1 的产物不像 IR"
        # lomelf 编它并跑
        blob, _info = lomelf.compile_ll(rr.stdout.decode("utf-8"))
        elf = td / "from_stage1.elf"
        elf.write_bytes(blob)
        rc, out = _run_bin(elf, "from_stage1", td)
        assert rc == 0, f"stage1 发的 IR 编出来的 ELF 跑挂了 rc={rc}"
        assert out == b"M67 RESULT: PASS loment-user\n", f"输出不符: {out!r}"
        assert entry.name == "user_hello.lomt"
    print("      种子 -> stage1 -> IR -> 原生产物 -> 跑出 M67 PASS (编译用户程序全程无 clang)")


@test
def test_lomelf_selfhost_matches_reference():
    """自举侧镜像: `loment/tools/lomelf.lomt` 编出的 ELF 与参考实现**逐字节相同**。

    这条是"编译一个 Loment 程序不需要 clang"真正落脚的地方 —— 参考实现 (Python) 只是
    这格的规格书, 能替用户干活的是自举侧那份。链条里 clang 只出现在"种子 -> stage1"
    一步 (genesis, docs/167 §5 记账)。
    """
    clang = _clang()
    if not (clang and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    seed = ROOT / "loment" / "build" / "selfhost_driver.ll"
    assert seed.exists(), "缺自举种子 (loment/build/selfhost_driver.ll)"
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        s1 = td / "stage1"
        r = subprocess.run(
            [clang, "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
             "-static", "-fuse-ld=lld", "-o", str(s1), str(seed)],
            capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-300:]
        # stage1 编译镜像本体 (自举路, 无 Python) -> IR
        binn = "/tmp/lomelf_s1.bin"
        script = (f"cp {_wsl_path(s1)} {binn} && chmod +x {binn} && "
                  f"cd {_wsl_path(ROOT)} && {binn} loment/tools/lomelf.lomt")
        rr = subprocess.run(["wsl", "-e", "bash", "-lc", script],
                            capture_output=True, timeout=900, shell=False)
        assert rr.returncode == 0, f"stage1 编译镜像失败: {rr.stderr[-300:]}"
        mir_ll = td / "lomelf.ll"
        mir_ll.write_bytes(rr.stdout)
        assert len(rr.stdout) > 100000, f"镜像 IR 太小 ({len(rr.stdout)}B)"
        mir = td / "lomelf.bin"
        r2 = subprocess.run(
            [clang, "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
             "-static", "-fno-pie", "-fuse-ld=lld", "-Wl,-e,_start", str(mir_ll), "-o", str(mir)],
            capture_output=True, text=True, shell=False)
        assert r2.returncode == 0, f"镜像链接失败: {r2.stderr[-300:]}"
        # 语料: 参考 IR -> 镜像编 -> 与参考的字节比
        total = 0
        for rel in CORPUS:
            srcl = ROOT / rel
            ll = _ref_ir(srcl, td)
            want, _info = lomelf.compile_ll(ll.read_text(encoding="utf-8"))
            llrepo = ROOT / "loment" / "build" / f"_mirror_{srcl.stem}.ll"
            elfrepo = ROOT / "loment" / "build" / f"_mirror_{srcl.stem}.elf"
            llrepo.parent.mkdir(parents=True, exist_ok=True)
            try:
                llrepo.write_bytes(ll.read_bytes())
                got = subprocess.run(
                    ["wsl", "-e", "bash", "-lc",
                     f"cp {_wsl_path(mir)} /tmp/lomelf_m.bin && chmod +x /tmp/lomelf_m.bin && "
                     f"cd {_wsl_path(ROOT)} && /tmp/lomelf_m.bin "
                     f"loment/build/_mirror_{srcl.stem}.ll "
                     f"loment/build/_mirror_{srcl.stem}.elf"],
                    capture_output=True, text=True, timeout=300, shell=False)
                assert got.returncode == 0, (
                    f"[{srcl.stem}] 镜像退出 {got.returncode}: {got.stderr[-200:]}")
                nat = elfrepo.read_bytes()
            finally:
                elfrepo.unlink(missing_ok=True)
                llrepo.unlink(missing_ok=True)
            assert nat == want, f"[{srcl.stem}] 镜像产物与参考不同 ({len(nat)}B vs {len(want)}B)"
            total += 1
    print(f"      {total} 个程序: 自举镜像与参考逐字节相同")


@test
def test_lomelf_rebuilds_the_compiler_without_clang():
    """**重建不需要 clang**: lomelf(种子) -> 一个能用的 Loment 编译器, 且它自编译
    `driver.lomt` 的产物**与种子逐字节相同**（定点成立）。整条链没有 clang 参与。

    这是 0.1.4 Alpha2 那条最严口径（"连发布/重建都不需要 C 编译器"）在**参考侧**的落地；
    真正消掉"第一个二进制"的是 docs/167 §5 第 4 条的 genesis。
    """
    if not _wsl():
        print("      SKIP: 无 WSL")
        return
    seed = ROOT / "loment" / "build" / "selfhost_driver.ll"
    assert seed.exists(), "缺自举种子"
    drv, _info = lomelf.compile_ll(seed.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = td / "drv.elf"
        elf.write_bytes(drv)
        binn = "/tmp/lomelf_drv.bin"
        outp = td / "drv2.ll"
        script = (
            f"cp {_wsl_path(elf)} {binn} && chmod +x {binn} && "
            f"cd {_wsl_path(ROOT)} && {binn} loment/selfhost/driver.lomt "
            f"> {_wsl_path(outp)} 2>/dev/null; echo -n $?"
        )
        r = subprocess.run(["wsl", "-e", "bash", "-lc", script],
                           capture_output=True, timeout=900, shell=False)
        assert r.stdout.decode().strip() == "0", f"原生编译器退出非零: {r.stdout[:40]!r}"
        got = outp.read_bytes()
    want = seed.read_bytes()
    assert got == want, f"[定点不成立] 自编译产物 {len(got)}B != 种子 {len(want)}B"
    print(f"      lomelf(种子) -> 编译器 -> 自编译产物 == 种子 ({len(got)}B), 全程无 clang")


def main() -> int:
    failed = []
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, e))
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\nloment_elf_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
