/*
 * Sample.java —— **同意集**：这份 Java 与 Loment 算出同一个数。
 *
 * **它是 Java 写法写的 Loment**（`docs/188` §0）—— 拼法是 Java 的，语义是 Loment 的。
 * 挑这几段是有道理的，每一段钉住翻译器的一处：
 *
 *   level    顶层常量（`static final`）+ 位运算 + 提前 return
 *            —— 常量由 `potato_from._JAVA_CONST` 收进 `consts`、`lomt_from` 发成
 *            `pub const`；**翻译器跳过那条声明、只认得名字**
 *   gcd      `while` 里改形参
 *   score    `for` + 复合条件（`&&`）+ 三层 `else if`
 *            —— 钉住 for 的降级与 Java 的 `&&`（出 `boolean`，**不补转换**）
 *   entry    把三个结果并起来 —— 判据比的就是它
 *
 * ## 这一门最要紧的一处：Java **不需要** C 那两条强制转换
 *
 * Java 的 `&&`/`||` 出 `boolean`、条件**只收** `boolean` —— 与 Loment 一致；
 * `int` 恒为 32 位、`/` 与 `%` 向零截断、`>>` 是算术的（实测 `-8 >> 1 == -4`）
 * —— 也都一致。所以方言表里那两个开关**都是关的**，而这不是省事，是判据：
 * Java 的 `int x = (a < b);` 在 Java 里本来就编不过，遇到它**报错才对**。
 *
 * 判据是"两边跑出来的数一样"：这份直接 javac + java 跑，与经 Potato 翻成 Loment
 * 再编再跑比退出码。**不是比文本。**
 *
 * 期望值：`level(1000)=2`、`gcd(48,18)=6`、`score(100)=73` ⇒ `2*10 + 6 + 73 = 99`。
 */
public class Sample {
    public static final int WARN_LEVEL = 500;
    public static final int ALARM_LEVEL = 900;

    public static int level(int frame) {
        int v = frame & 0xFFFF;
        if (v >= ALARM_LEVEL) {
            return 2;
        }
        if (v >= WARN_LEVEL) {
            return 1;
        }
        return 0;
    }

    public static int gcd(int a, int b) {
        while (b != 0) {
            int t = a % b;
            a = b;
            b = t;
        }
        return a;
    }

    public static int score(int n) {
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

    public static int entry() {
        int a = level(1000);
        int b = gcd(48, 18);
        int c = score(100);
        return (a * 10 + b) + c;
    }
}
