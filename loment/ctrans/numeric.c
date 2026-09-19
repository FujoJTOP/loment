/* numeric.c —— 第四份语料：**多函数协作 + 两种循环 + 分支链 + 位运算**。
 *
 * 前三份各钉一处要害（`sample.c` 走正常写法、`coerce.c` 专挑 `int`/`bool` 那条缝、
 * `scoping.c` 钉改名外提与优先级），这一份**换一个方向使劲**：把"这一门到底支持到哪"
 * 往前推一格 —— 函数多一些、彼此调用、两种循环都上、位运算与除法取余混着用。
 *
 * 每一段各钉一件事：
 *
 *   isqrt           `while` + `if/else`，循环里**声明局部量**（`int mid`）—— 钉住
 *                   "循环体里可以有声明"，而且它在下一轮还要重新算
 *   digit_sum       `while` + `%` `/` 同时改**形参** n
 *   collatz_steps   `while` + `if/else`，循环里改**外层变量** n
 *   popcount        `while` + 位运算 `&` `>>`（只喂非负数 —— 算术右移在负数上不退位）
 *   popcount_range  `for` + **调用**别的函数（`popcount`）—— 钉住"单元内跨函数调用"
 *   divisors        `for` + `if`（取余判整除）
 *   div_sum         `for` 里**再调一层** `divisors` —— 双层循环，靠调用套出来
 *   main            `for` 累加 + 四条调用 + `if/else if/else` 链，返回值当退出码
 *
 * **全程只用 `int`（一个整数宽度）。** 这不是偷懒：本门翻译器只有 `int` / `bool` /
 * `void` 三档（`trans_core.Emitter.ty_of`），**看不出 `u32` 与 `i32` 的区别** ——
 * 一旦 `unsigned` 与 `int` 混着算（或把 `unsigned` 函数声明成返回 `bool` 表达式），
 * 翻出来的 Loment 会在 `lomentc.check` 那一步**硬失败**（"return 类型 u32，函数声明 i32"）。
 * 那条边界写在 `docs/188` §7.1.1 末尾，这一份语料**顺着它写**，不去踩。
 *
 * 判据是"两边跑出来的数一样"：直接用 clang 编成 freestanding ELF 跑，与"经 Potato
 * 翻成 Loment 再编再跑"比退出码（`tools/loment_ctrans_test.py`）。期望值照 C 语义
 * **再算一遍**（`_numeric_expected`），不是抄实现里的数。
 */
int isqrt(int n) {
    int lo = 0;
    int hi = n;
    int best = 0;
    while (lo <= hi) {
        int mid = (lo + hi) / 2;
        if (mid * mid <= n) {
            best = mid;
            lo = mid + 1;
        } else {
            hi = mid - 1;
        }
    }
    return best;
}

int digit_sum(int n) {
    int s = 0;
    while (n > 0) {
        s = s + n % 10;
        n = n / 10;
    }
    return s;
}

int collatz_steps(int n) {
    int steps = 0;
    while (n != 1) {
        if (n % 2 == 0) {
            n = n / 2;
        } else {
            n = n * 3 + 1;
        }
        steps = steps + 1;
    }
    return steps;
}

int popcount(int x) {
    int c = 0;
    while (x != 0) {
        c = c + (x & 1);
        x = x >> 1;
    }
    return c;
}

int popcount_range(int lo, int hi) {
    int acc = 0;
    for (int k = lo; k < hi; k = k + 1) {
        acc = acc + popcount(k);
    }
    return acc;
}

int divisors(int n) {
    int c = 0;
    for (int d = 1; d <= n; d = d + 1) {
        if (n % d == 0) {
            c = c + 1;
        }
    }
    return c;
}

int div_sum(int n) {
    int acc = 0;
    for (int k = 1; k <= n; k = k + 1) {
        acc = acc + divisors(k);
    }
    return acc;
}

int main() {
    int total = 0;

    for (int i = 1; i <= 20; i = i + 1) {
        total = total + isqrt(i * 100);
    }
    total = total + digit_sum(987654);
    total = total + collatz_steps(27);
    total = total + popcount_range(0, 64);
    total = total + div_sum(50);

    if (total > 1500) {
        total = total - 1500;
    } else if (total > 800) {
        total = total - 800;
    } else {
        total = total - 400;
    }
    return total % 256;
}
