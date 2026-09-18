/* scoping.c —— 第三份语料：**改名外提与遮蔽，以及 C 的优先级**。
 *
 * `sample.c` 里有一个 `for`（单层、不遮蔽），这一份把它推到会出错的那几处：
 *
 *   grid         **嵌套 `for`**，两层都用 `i` / `j` —— 钉住改名不会互相撞
 *                （`i__1` 那套编号必须**跨层唯一**，不然内层会把外层改掉）
 *   shadow       `for (int i = …)` 里的 `i` **遮蔽了外层同名的 `i`**，
 *                而外层那个 `i` **循环之后还要用** —— 钉住循环结束时要**把外层还回去**
 *                （只"删掉"不"还原"的话 `return acc + i;` 里的 `i` 会找不到，
 *                 或者更坏：静默指到内层的那个）
 *   precedence   `a + b * c - (a | b) & 7 ^ 2` —— 钉住 C 那张优先级表**一个字不差**
 *                （推出来是 2：`(5 + 42 - 7) & 7` = 0，`0 ^ 2` = 2）
 *
 * 三样有一个错了，`main` 的返回值就变（期望值见判据里的 `_scoping_expected`）。
 */
int grid(int n) {
    int acc = 0;
    for (int i = 0; i < n; i = i + 1) {
        for (int j = 0; j < n; j = j + 1) {
            acc = acc + (i * n + j);
        }
    }
    return acc;
}

int shadow(int n) {
    int i = 100;
    int acc = 0;
    for (int i = 0; i < n; i = i + 1) {
        acc = acc + i;
    }
    return acc + i;
}

int precedence(int a, int b, int c) {
    return a + b * c - (a | b) & 7 ^ 2;
}

int main() {
    return (grid(4) + shadow(3) + precedence(5, 6, 7)) % 256;
}
