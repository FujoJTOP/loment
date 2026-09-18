/*
 * Sample.cs —— **同意集**：这份 C# 与 Loment 算出同一个数。
 *
 * ## 它与 `loment/jtrans/Sample.java` 是**同一个程序**的两种拼法
 *
 * 这不是抄 —— 这正是那一条判据本身（`docs/188` §0）：
 *
 * > Loment 可以被多种语法编写，其最终行为却无异。
 *
 * 两边都该算出 **99**。任何一处"翻过去变了意思"都会让这个数对不上，而**两边各自
 * 与自己的编译器也仍然对得上** —— 所以只有"翻译错了"能让它红。
 *
 * 拼法上不同的只有：`namespace` + `class` 两层壳（Java 只有一层）、`const`
 * （Java 是 `static final`）。语义一格没动。
 *
 * ## 每一段钉住翻译器的一处
 *
 *   level    顶层常量 + 位运算 + 提前 return
 *            —— 常量由 `potato_from._CS_CONST` 收进 `consts`、`lomt_from` 发成
 *            `pub const`；**翻译器跳过那条声明、只认得名字**
 *   gcd      `while` 里改形参
 *   score    `for` + 复合条件（`&&`）+ 三层 `else if`
 *            —— 钉住 for 的降级与 C# 的 `&&`（出 `bool`，**不补转换**）
 *   entry    把三个结果并起来 —— 判据比的就是它
 *
 * ## 这一门与 Java 同一处：**不需要** C 那两条强制转换
 *
 * C# 的 `&&`/`||` 出 `bool`、条件**只收** `bool`（`int x = (a < b);` 在 C# 里
 * 本来就编不过）；`int` 恒 32 位、`/` 与 `%` 向零截断、`>>` 是算术的
 * —— 都与 Loment 一致。所以方言表里那两个开关**都是关的**。
 *
 * （与 Java **真正**不同的是 `byte`：C# 是 0..255 无符号、Java 是 -128..127 有符号。
 * 那一格在 `tools/cstrans.py` 的文件头，判据在 `loment_cstrans_test` 里 ——
 * 它走的是 `potato_from` 那一层，因为这个子集的翻译器不做整数宽度跟踪。）
 *
 * 期望值：`level(1000)=2`、`gcd(48,18)=6`、`score(100)=73` ⇒ `2*10 + 6 + 73 = 99`。
 *
 * `using System;` 这一行**是必须写的**，不是装饰：`potato_from.detect_lang` 认 C#
 * 靠的就是它（或 `static … Main(string[] …)`）—— `namespace` **不能**当判据，
 * 因为 C++ 也有 `namespace X {`，拿它认会把一份 C++ 源码按 C# 去翻。
 *
 * 命名空间叫 `LomentDemo` 而类叫 `Sample`，是**有意**的：C# 里两者同名的话全名就是
 * `Sample.Sample`，对照组那个文件夹具得写成 `Sample.Sample.entry()`。换个名字不影响
 * 翻译产物 —— 外壳整层都被抹掉了（判据里有"两个写法翻出来逐行相同"那一条）。
 */
using System;

namespace LomentDemo
{
    public class Sample
    {
        public const int WARN_LEVEL = 500;
        public const int ALARM_LEVEL = 900;

        public static int level(int frame)
        {
            int v = frame & 0xFFFF;
            if (v >= ALARM_LEVEL)
            {
                return 2;
            }
            if (v >= WARN_LEVEL)
            {
                return 1;
            }
            return 0;
        }

        public static int gcd(int a, int b)
        {
            while (b != 0)
            {
                int t = a % b;
                a = b;
                b = t;
            }
            return a;
        }

        public static int score(int n)
        {
            int acc = 0;
            for (int i = 1; i <= n; i = i + 1)
            {
                if (i % 3 == 0 && i % 5 == 0)
                {
                    acc = acc + 3;
                }
                else if (i % 3 == 0)
                {
                    acc = acc + 1;
                }
                else if (i % 5 == 0)
                {
                    acc = acc + 2;
                }
            }
            return acc;
        }

        public static int entry()
        {
            int a = level(1000);
            int b = gcd(48, 18);
            int c = score(100);
            return (a * 10 + b) + c;
        }
    }
}
