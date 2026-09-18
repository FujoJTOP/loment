/* coerce.c —— 第二份语料：**专门混着写 `int` 与条件**。
 *
 * `sample.c` 走的是"正常写法"，这一份**专挑那条缝**：C 里 `&&` `||` `!` 比较出的是
 * **`int`**（0/1），任何整数都能当条件用（`if (a)`）；Loment 是 Rust 的严格子集，
 * 这些出 **`bool`**，而条件**只收 `bool`**。于是两个方向都要补：
 *
 *     int x = a && b;     ->  let x: i32 = ((a != 0 && b != 0)) as i32;   (bool -> int)
 *     if (a)              ->  if a != 0                                    (int  -> bool)
 *     !a                  ->  (a == 0)   —— Loment 的 `!` 只收 bool，不能直接写 `!a`
 *     (a < b) + 1         ->  ((a < b)) as i32 + 1                        (bool -> int)
 *
 * **这两处转换错了，程序照样编得过** —— 只是结果不对。所以它必须有一条**比数**的判据
 * （`tools/loment_ctrans_test.py`），而不是比文本。
 *
 * 另外这一份里 `while (b) { … b = b - 1; }` **改掉了 `b`，而它后面还被用到** ——
 * 期望值是照顺序推出来的，不推一遍就会算错（我自己第一次算就漏了这个，见判据里的注释）。
 */
int f(int a, int b) {
    int x = a && b;
    int y = a || b;
    int z = !a;
    int w = (a < b) + (a > b) * 2;
    if (a) {
        x = x + 1;
    }
    while (b) {
        y = y + 1;
        b = b - 1;
    }
    int xorv = a ^ b;
    int shifted = (a << 2) | (b >> 1);
    return x + y + z + w + xorv + shifted;
}

int main() {
    return f(3, 2) % 256;
}
