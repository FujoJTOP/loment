/*
 * Bits.java —— **同意集**：这份 Java 与 Loment 算出同一个数。
 *
 * **它是 Java 写法写的 Loment**（`docs/188` §0）—— 拼法是 Java 的，语义是 Loment 的。
 * 与 `Sample.java` 是同一条判据下的**第二份语料**，故意把压力加在别处：
 *
 *   popcount  `while` + 位运算（`&` 与 `>>`）+ 在循环里累加局部量
 *   gcd       `while` 里改形参、`%`（向零截断 —— 两边一致）
 *   collatz   **递归** + 两层条件分支 + 整数除法 `/`
 *             —— 钉住"函数能调自己"（`self.fns` 里本单元的名字就够）
 *   fold      `for` + 三层 `else if` 级联 + `^` `|` `<<` 三种位运算混排
 *             —— 复合条件 `a && b` 出 `boolean`，Java **不补转换**
 *   mix       `^`（异或）+ 移位，末尾 `& 255` 收窄
 *             —— **不用 `~`**：Loment **没有按位取反**（见下）
 *   entry     把上面五个并起来 —— 判据比的就是它
 *
 * ## 这一门最要紧的一处（与 `Sample.java` 同一条）：Java **不需要** C 的强制转换
 *
 * Java 的 `&&`/`||` 出 `boolean`、条件**只收** `boolean` —— 与 Loment 一致；
 * `int` 恒为 32 位、`/` 与 `%` 向零截断、`>>` 是算术的（实测 `-8 >> 1 == -4`）
 * —— 也都一致。所以方言表里那两个开关**都是关的**。
 *
 * ## 这份语料**没写数组**，而且不是偷懒
 *
 * Java 的数组声明（`int[] xs` 或 `int xs[]`）在这门拼法里**表达不出来**：
 * `trans_core.Parser.spec()` 遇到名字后面跟的 `[` 就点名拒掉
 * （`不支持指针/数组声明`），而数组字面量 `{1,2,3}` 会撞上"不支持裸块"。
 * 所以这份程序只用**标量** —— 这是子集画的线，不是这份程序的取舍。
 *
 * ## 第二处画线：`~`（按位取反）—— 前端**点名拒**（这一处是写这份语料时补上的）
 *
 * **Loment 没有按位取反**（一元运算符只有 `-` 与 `!`）。原先 `trans_core.unary()`
 * **收下** `~` 并照发 `(~x)`，于是产物死在**编译器**的词法那一步：`非法字符 '~'`，
 * 而且报的是**生成出来那个单元**的行号 —— 用户在源码里找不到那一行。
 * 那是"能过前端、在后面才炸"，与 `>>>` 的处理正好相反（同一个文件里 `>>>` 是
 * **点名拒 + 给出路**的）。现在 `~` 也进了 `trans_core._REJECT_NAMED`：四门共享核
 * 都在分词那一步拒掉，并给出**按宽度**的两条出路（`v ^ -1` / `v ^ 255`）——
 * 它本可以自动转，但翻译器**不做整数宽度跟踪**（`docs/188` §7.1.1），替不了你选。
 *
 * 所以 `mix` 这一份仍写成等价的 `(v ^ 255) & 255` —— 这不是"绕开一个洞"，
 * 是**照那条出路走**（本处 `v` 收窄到 8 位，所以全 1 是 255 而不是 -1）。
 * 判据：`tools/loment_ctrans_test.py::test_bitwise_not_is_loud_in_every_brace_dialect`。
 *
 * 判据是"两边跑出来的数一样"：这份直接 javac + java 跑，与经 Potato 翻成 Loment
 * 再编再跑比退出码。**不是比文本。**
 *
 * 期望值（独立推导，见 `loment_jtrans_test._want_bits`）：
 *   a = popcount(0xBEEF) = 13
 *   b = gcd(1071, 462) = 21
 *   c = collatz(27)    = 111
 *   d = fold(24)       = 51
 *   e = mix(200)       = 166
 *   entry = ((13*3 + 21) ^ (111 & 63)) + (51 ^ 166) = 19 + 149 = 168
 */
public class Bits {
    public static final int MASK = 65535;

    public static int popcount(int v) {
        int n = 0;
        int x = v;
        while (x != 0) {
            n = n + (x & 1);
            x = x >> 1;
        }
        return n;
    }

    public static int gcd(int a, int b) {
        while (b != 0) {
            int t = a % b;
            a = b;
            b = t;
        }
        return a;
    }

    public static int collatz(int n) {
        if (n == 1) {
            return 0;
        }
        if (n % 2 == 0) {
            return 1 + collatz(n / 2);
        }
        return 1 + collatz(3 * n + 1);
    }

    public static int fold(int n) {
        int acc = 0;
        for (int i = 1; i <= n; i = i + 1) {
            if (i % 3 == 0 && i % 2 == 1) {
                acc = acc ^ (i & 15);
            } else if (i % 5 == 0) {
                acc = acc | (i << 1);
            } else {
                acc = acc + popcount(i);
            }
        }
        return acc;
    }

    public static int mix(int v) {
        int m = (v ^ 255) & 255;
        return ((m << 1) ^ v) & 255;
    }

    public static int entry() {
        int a = popcount(0xBEEF & MASK);
        int b = gcd(1071, 462);
        int c = collatz(27);
        int d = fold(24);
        int e = mix(200);
        return ((a * 3 + b) ^ (c & 63)) + (d ^ e);
    }
}
