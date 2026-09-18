/*
 * Sample.cpp —— C++ 那一门的同意集：这份 C++ 与 Loment 算出同一个数。
 *
 * ## 它**刻意不是** `Sample.java` / `Sample.cs` 的第三份拷贝
 *
 * 那两份是"同一个程序的两种拼法"（`docs/188` §0），C++ 这一门另有用处：
 * 它要钉住 **C++ 自己**的那几格 —— 而其中有一格 **C 那门没有**：
 *
 *   clamp        普通标量、提前 return（与 C 共有的那一半）
 *   in_range     **`bool` 返回 + `true` / `false` 字面量 + `const` 形参**
 *                —— 这是这一门与 C **真正**分家的地方：C++ 有独立的布尔类型，
 *                而 `true`/`false` 在 Loment 里也是字面量（不是 `1`/`0`）
 *   as_flag      **`bool -> int`**：`a && b` 出 `bool`，而 C++ 允许把它当 int 用
 *                ⇒ 翻译器要补 `(…) as i32`
 *   count_truthy **`int -> bool`**：`if (a)`（a 是 int）在 C++ 里合法
 *                ⇒ 翻译器要补 `a != 0`
 *   score        `for` + 复合条件 + 三层 `else if`（与 C 共有的那一半）
 *   entry        把几个结果并起来 —— 判据比的就是它
 *
 * ## 为什么两个方向的强制转换**都要补**（这一点与 C 相同、理由完全不同）
 *
 * C 是"根本没有布尔类型"（`&&` 出 `int`）；C++ 是"**有** `bool`，但把它与
 * `int` 的双向隐式转换都留着"。两条路都让 Loment 那边需要显式转换 ——
 * 而 Java / C# 是**关的**，因为那两门 `int x = (a < b);` 本来就编不过。
 *
 * ## `static` 与 `const` 都收下并丢掉
 *
 * `static` 是内部链接、`const` 是"不许改"—— 这里翻的是整个单元的全部函数，
 * 而在**只有标量、没有指针**的子集里丢掉它们都是**保义**的。
 * （`const` 原先在 C 那门是拒的，2026-09-18 做这一门时一并挪到"收下并丢掉"，
 * `docs/186` §6.3 记了那次更正。）
 *
 * 期望值：`clamp(42,0,9)=9`、`as_flag(1,0)=0`、`count_truthy(7,0,3)=2`、
 * `score(100)=73`（15 的倍数 6 个 +3、能 3 不能 5 的 27 个 +1、能 5 不能 3 的 14 个 +2
 * = 18+27+28）、`in_range(5,1,10)` 为真 ⇒ `(9*5 + 0) + (2*7) + 40 + 73 = **172**`。
 */

static int clamp(int v, int lo, int hi) {
    if (v < lo) { return lo; }
    if (v > hi) { return hi; }
    return v;
}

bool in_range(const int v, const int lo, const int hi) {
    if (v < lo) { return false; }
    if (v > hi) { return false; }
    return true;
}

int as_flag(int a, int b) {
    return a && b;
}

int count_truthy(int a, int b, int c) {
    int n = 0;
    if (a) { n = n + 1; }
    if (b) { n = n + 1; }
    if (c) { n = n + 1; }
    return n;
}

int score(int n) {
    int acc = 0;
    for (int i = 1; i <= n; i = i + 1) {
        if (i % 3 == 0 && i % 5 == 0) {
            acc = acc + 3;
        } else if (i % 3 == 0) {
            acc = acc + 1;
        } else if (i % 5 == 0) {
            acc = acc + 2;
        }
    }
    return acc;
}

int entry() {
    bool ok = in_range(5, 1, 10);
    int flag = as_flag(1, 0);
    int n = count_truthy(7, 0, 3);
    int clamped = clamp(42, 0, 9);
    int s = score(100);
    int bonus = 0;
    if (ok) { bonus = 40; }
    return (clamped * 5 + flag) + (n * 7) + bonus + s;
}
