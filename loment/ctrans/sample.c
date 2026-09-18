/* sample.c —— Stage A 的语料（`docs/186`）。
 *
 * 它是一份**普通的 C**，不是 Loment，也不装在 `.lomt` 里 —— 意思是"这是源语言那一侧的
 * 东西"，与 `loment/examples/multilang/01-c/pack.lomt`（装着 C 的 `.lomt`）分工不同。
 *
 * 挑这几段是有道理的，每一段钉住翻译器的一个要害：
 *
 *   gcd        while 里**改形参**（两个变量互相赋值）—— 钉住"形参是可变的"这条
 *   classify   `else if` 链 + 返回负数（`0 - 1`，因为子集里一元 `-` 会走 `(0 - x)` 那条路）
 *              —— 钉住**提前 return** 与分支嵌套
 *   score      `for` + 复合条件（`&&`）+ 三层 `else if`
 *              —— 钉住 `for` 的降级（Loment 没有裸块，循环变量要改名外提）
 *              与 **C 的 `int` / Loment 的 `bool`** 那处强制转换
 *   main       把三个结果并起来 —— 判据比的就是它的返回值
 *
 * **判据是"两边跑出来的数一样"**：这一份直接用 clang 编成 freestanding ELF 跑，
 * 与"经 Potato 翻成 Loment 再编再跑"比退出码（`tools/loment_ctrans_test.py`）。
 * 不是比文本 —— 比文本的话，翻译器把 `a % b` 写成 `a - (a / b) * b` 也算过。
 */
int gcd(int a, int b) {
    while (b != 0) {
        int t = a % b;
        a = b;
        b = t;
    }
    return a;
}

int classify(int x) {
    if (x < 0) {
        return 0 - 1;
    } else if (x == 0) {
        return 0;
    }
    return 1;
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

int main() {
    int a = gcd(48, 18);
    int b = classify(0);
    int c = score(100);
    return (a * 10 + b) + c;
}
