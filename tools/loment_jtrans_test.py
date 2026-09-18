#!/usr/bin/env python3
"""loment_jtrans_test.py — **Java 写法 -> Loment，跑出来的数一样**（`docs/188` §7.1）。

六门里的第三门（C / Python 之后）。判据与那两门**同一条**：

> 判据是「翻译出来的 Loment 跑出的结果 == 直接跑那份源码的结果」

这一门把"直接跑那份源码"换成 `javac` + `java`：

    Java  ──javac/java──> Harness.main 的退出码 ──┐
                                                   ├─> 比一比
    Java ──potato_from──> Potato ──lomt_from --impl──> Loment ──> 退出码 ──┘

**比的是数，不是文本。**

## 这一门为什么比 C 那门"轻"

`docs/186` §5 说 C 那门最要紧的是"`int` 与 `bool` 不是一回事"—— 那处缝在 Java 里
**不存在**（`&&` 出 `boolean`、条件只收 `boolean`、`int` 恒为 32 位、`/` 与 `%`
向零截断、`>>` 是算术的）。所以方言表里两个强制转换开关**都是关的**，
而这不是省事：

> Java 的 `int x = (a < b);` 在 Java 里**本来就编不过** —— 遇到它报错才对，
> 不该悄悄补一个 `as i32`。

## 对照组怎么跑

Java 的入口是 `public static void main(String[])`，要拿一个"数"出来得有 `System.exit`。
而 `System.exit` 是属性访问（本子集不收）—— 所以**对照组那边**补一个 `Harness`
（与 C 那门的 `_start` 夹具同一个道理：**两边各有一个入口，比的是同一个数**）。

用法: python tools/loment_jtrans_test.py    （无 javac/WSL 时 SKIP，退出码 0）
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jtrans      # noqa: E402
import lomelf      # noqa: E402
import lomentc     # noqa: E402
import lomt_from   # noqa: E402
import potato      # noqa: E402
import potato_from  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "loment" / "jtrans"

#: 期望值。**推出来的，不是抄的**：`level(1000)=2`、`gcd(48,18)=6`、
#: `score(100)`（1..100 里 15 的倍数 6 个 +3、能 3 不能 5 的 27 个 +1、能 5 不能 3 的 14 个 +2）
#: = 18 + 27 + 28 = 73 ⇒ `2*10 + 6 + 73 = **99**`。
WANT_RC = 2 * 10 + 6 + (6 * 3 + 27 * 1 + 14 * 2)

TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


def _javac() -> str | None:
    return shutil.which("javac")


def _java() -> str | None:
    return shutil.which("java")


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


def _run(exe: Path) -> int:
    r = subprocess.run(
        ["wsl", "-e", "bash", "-lc",
         f"chmod +x {_wsl_path(exe)} && {_wsl_path(exe)}; echo -n $?"],
        capture_output=True, text=True, timeout=300, shell=False)
    return int(r.stdout.strip() or -1)


#: **对照组那一边的入口**。Java 要一个 `main` 才能起，而要拿"数"出来得走
#: `System.exit` —— 那是属性访问，本子集不收。所以夹具补一个 `Harness`。
#: 与 C 那门的 `_start` 夹具同一个道理：**两边各有一个入口，比的是同一个数**。
_HARNESS = """
public class Harness {
    public static void main(String[] a) {
        System.exit(Sample.entry() % 256);
    }
}
"""


def _javac_run(td: Path, src: Path) -> int:
    """Java 侧：javac 编「语料 + 夹具」，java 跑，读退出码。"""
    (td / "Harness.java").write_text(_HARNESS, encoding="utf-8", newline="\n")
    (td / src.name).write_text(src.read_text(encoding="utf-8"),
                               encoding="utf-8", newline="\n")
    r = subprocess.run([_javac(), "-d", str(td), str(td / src.name),
                        str(td / "Harness.java")],
                       capture_output=True, text=True, shell=False, timeout=300)
    assert r.returncode == 0, f"javac 失败: {r.stderr[-400:]}"
    r = subprocess.run([_java(), "-cp", str(td), "Harness"],
                       capture_output=True, text=True, shell=False, timeout=300)
    return r.returncode


#: Loment 侧的入口：60 号系统调用 = exit。
_L_ENTRY = """
fn _start() {
    syscall4(60, entry() as u64, 0, 0);
}
"""


def _build_loment(td: Path, doc: dict) -> Path:
    text, skipped = lomt_from.emit_lomt(doc, impl=True)
    assert not skipped, f"发的时候跳过了东西: {skipped[:3]}"
    # **一条 `extern fn` 都不该有** —— Java 写法写出来的是 Loment（`docs/188` §3）
    assert "pub extern fn " not in text, text[:300]
    p = td / "l_side.lomt"
    p.write_text(text + _L_ENTRY, encoding="utf-8", newline="\n")
    mod = lomentc.load(p)
    deps = lomentc.resolve_deps(mod, ROOT, p.parent, entry=p)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"翻译出来的 Loment 检查不过: {errs[:3]}"
    blob, _info = lomelf.compile_ll(lomentc.emit_llvm(mod, ROOT, deps), [])
    exe = td / "l_side.elf"
    exe.write_bytes(blob)
    return exe


@test
def test_translation_runs_same_as_javac():
    """**翻译出来的 Loment 跑出的数 == javac 编那份 Java 跑出的数**。

    两边编出来的是**同一种东西**（各自一个走 exit 的入口），所以两个退出码直接可比。
    语料 `Sample.java` 里顶层常量、`while` 改形参、`for` + `&&`、三层 `else if` 都有
    （见那份文件的头注），任一处翻错都会把 99 改掉。
    """
    if not _javac() or not _java() or not _wsl():
        print("      SKIP: 需要 javac + java + WSL")
        return
    src = EX / "Sample.java"
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        doc, rep = potato_from.from_java(src.read_text(encoding="utf-8"), "Sample.java",
                                         "strict")
        assert not potato.validate(doc), potato.validate(doc)[:3]
        # **`from_java` 会把没有字段的类报成一条 skip**（`无可用字段`）—— 那是既有
        # 行为、不是丢东西：这个类只有常量与方法，本来就没有字段可收。所以这里只断言
        # **函数**一条都没被跳（那才是"少算一步"的来源），类别那一档不算。
        lost = [s for s in rep.skipped if s["kind"] == "fn"]
        assert not lost, f"转写那一步丢了函数: {lost}"
        # **它是 Loment**，只是写法是 Java（`docs/188` §3）
        assert doc["language"] == "loment" and doc["grammar"] == "java", doc["language"]
        j_rc = _javac_run(td, src)
        l_rc = _run(_build_loment(td, doc))
    assert j_rc == WANT_RC, f"javac 那边就不对: {j_rc} != {WANT_RC}（语料或期望值错了）"
    assert l_rc == j_rc, (
        f"**翻译出来的 Loment 与 javac 编的 Java 结果不同**: {l_rc} != {j_rc}。\n"
        f"  （两个都对不上 {WANT_RC} 的话是语料问题；只有一边对不上就是翻译翻错了）")
    print(f"      javac -> {j_rc}，翻译成 Loment -> {l_rc}（相等）")


@test
def test_java_specific_difference_is_loud():
    """**Java 特有的那处差要说得出**：`>>>` 是逻辑右移，本语言的 `>>` 是算术的。

    这一条钉两件事：

    1. **报得出来** —— `>>>` 不能被切成 `>>` `>`（那样报的是"期望 `;`，得到 `>`"，
       指不到点子）。它是**一个 token**，点名拒掉，理由写在消息里。
    2. **判据里带得出路** —— 消息要说"要它得写 `(a as u32) >> n`"，不然看的人只知道
       "不行"不知道"怎么办"。
    """
    src = (EX / "Unsupported.java").read_text(encoding="utf-8")
    try:
        jtrans.translate(src)
    except jtrans.Unsupported as e:
        msg = str(e)
        assert ">>>" in msg, f"要点名 `>>>`: {msg}"
        assert "u32" in msg, f"要给出路（`(a as u32) >> n`）: {msg}"
        print(f"      `>>>` 报得出且给出路: {msg[-80:]}")
    else:
        raise AssertionError("`>>>` 在子集外，却一个字都没报")

    # **另一半**：`>>`（算术，与 Loment 一致）要**照收**，不许被一起拒了
    ok = ("public class T {\n"
          "    public static int f(int a) { return a >> 1; }\n"
          "}\n")
    text = jtrans.translate(ok)
    assert "a) >> 1" in text or ">> 1" in text, text
    print("      `>>`（算术，与 Loment 一致）照收")


@test
def test_class_shell_is_stripped_without_moving_lines():
    """**`class` 外壳抹掉，而行号不许动**（`jtrans._unwrap`）。

    这是 Java / C# 独有的一处：函数住在 `class X { … }` 里，而共享 parser 看的是顶层。
    抹法是**换成等长空白**，所以：

    * 成员成了顶层 ✓
    * **行号与偏移一字不变** ✓ —— 报错里的行号就是源里的行号

    不这么抹（比如把成员抽出来拼一拼）的话，**报错的行号会指到别处** ——
    而那正是 `docs/179` §6.5 记过的那种"把人引向错方向"的东西。
    """
    src = (EX / "Sample.java").read_text(encoding="utf-8")
    u = jtrans._unwrap(src)
    assert len(u) == len(src), "抹外壳不能改长度（改了行号就会漂）"
    assert u.count("\n") == src.count("\n"), "换行数也不许变"
    assert "class Sample {" not in u, "外壳没抹掉"
    assert "public static int level(" in u, "成员该留在原处"
    # 行号对齐：每一行的**行尾换行位置**都一样
    assert [i for i, c in enumerate(u) if c == "\n"] == \
           [i for i, c in enumerate(src) if c == "\n"], "换行位置漂了"
    print("      外壳抹掉、长度与换行位置一字不变")


def main() -> int:
    failed: list[str] = []
    for fn in TESTS:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed.append(fn.__name__)
            print(f"  FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\nloment_jtrans_test: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
