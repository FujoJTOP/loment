/*
 * Unsupported.cs —— **反例语料**：这份 C# 里有本子集外的写法。
 *
 * 存在的唯一理由是让"不静默丢"这条纪律**可被判据钉住**（`docs/167`）。子集外的写法
 * 被悄悄跳过的话，`lomt_from --impl` 发出来的就是一份**少算了一步却照样能编过**的
 * 单元 —— 比拒绝坏得多。
 *
 * 这里放的是 C# **特有**的那一处：`string` 是这门语言里最常见的类型拼法，
 * 而**本语言没有字符串值**（`str` 只是表示层给它留的格子，见 `potato_from.CS_TYPES`）。
 *
 * 值得说清的是**两层分工**：
 *
 *   * `potato_from` 那一层**认得** `string` —— 它把类型映成 `str`，于是"对面那块内存
 *     里这个字段是个字符串"这件事**记下来了**（数据形状是它管的事）；
 *   * **翻译器**这一层收不了 —— 它要把正文翻成 Loment，而 `str` 不是本子集的值类型。
 *
 * 所以这份文件在**对象层**是合法的、在**翻译层**是被拒的 —— 拒得响亮。
 */
using System;

namespace LomentDemo
{
    public class Unsupported
    {
        public static int length(string s)
        {
            return 1;
        }
    }
}
