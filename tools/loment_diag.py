#!/usr/bin/env python3
# loment_diag.py — 诊断分类 + 修复建议 (M64, docs/148)
#
# 判据: 10 类错误各有错误码与修复建议。
# 实现: 对 lomentc.check() 的原始消息做模式分类 (不改编译器消息本身),
#       每类给出稳定错误码 (E0xx) 与可执行建议。
#
# 用法:
#   python tools/loment_diag.py FILE [--json]
# 退出码: 0 = 无错误 / 1 = 有错误 / 2 = 用法或读取错误。

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# (错误码, 模式, 标题, 修复建议)
#
# 分工原则: **码按"修法"分, 不按"消息措辞"分** —— 两条消息如果用户要做同一件事,
# 就该是同一个码。所以 `实参类型 u32，期望 bool` / `内建 str_len 实参类型 …` /
# `载荷类型 …` / `return 类型 …` 都是 E001 (都在说"这里类型对不上")。
#
# 顺序 = 优先级 (第一条匹配者胜), 所以更具体的模式要排在更宽的前面:
#   `字段 a 重复$` (E013) 必须在 `字段 a 重复初始化` (E015) 之前锚定结尾,
#   `结构体 S 为空` (E009) 不能吃掉 `数组字面量不能为空` (E016)。
RULES = [
    # E023 (2026-09-17 新增, **IR 后端还没实现的那一批**) —— 必须排在最前:
    # `native ...` 是编译器自己的限制, **不是用户源码的语义错**。用户在这里能做的事只有
    # 两件: 换个写法绕开, 或者报告。这与"照建议改那一行"是**不同的修法**, 所以单独一码。
    #
    # **为什么非要有它**: 这批原先全落到 E999「未分类, 请报告」—— 而 E999 的措辞让人以为
    # "是分类表漏了, 等着被补", 实际是"编译器还没做这块"。对用户是**完全不同的处境**:
    # 一个该去读 `docs/145` 的里程碑表换个写法, 一个该等着工具链更新。实测 26 条
    # (M0/M3/M4/M20/M23/M24/M25/M63 …), 占 `LomError` 消息的一半以上。
    #
    # **锚 `^`**: 只有消息**开头**就是 `native` 才算, 免得吃掉别处提到后端的句子。
    ("E023", r"^native[ :]",
     "这一版还没实现（编译器限制）",
     "这不是你源码的语义错 —— 是编译器的 IR 后端还没做这块。**换一种写法绕开**；"
     "绕不开就报告（附上这条消息）。进度见 `docs/145` 的里程碑表。"),
    # E002 必须排在 E001 前面: `载荷类型 Foo 未声明` / `结构体 S.a 类型 Foo 未声明` 里
    # 既有"类型"又有"未声明" —— 用户要做的事是"先声明 Foo"(E002), 不是"检查两侧类型"。
    ("E002", r"未定义的函数|未声明的变量|未声明$|未知结构体|未知枚举|类型 .*未声明|没有方法",
     "符号未声明", "先声明后使用；跨模块调用需要 `pub`，或补 `use 名字` / `use \"...lomt\"`。"),
    ("E001", r"类型 .*应为|表达式类型|return 类型|类型必须是整型|载荷类型|"
             r"实参(期望|类型)|实参 [^，]*不是数组或切片|内建 .* 实参",
     "类型不匹配", "检查两侧类型；整型字面量按上下文定宽，必要时用 `as` 显式转换。"),
    ("E003", r"需要 \d+ 个实参",
     "实参数量不符", "对照函数签名补/删实参；方法调用的 self 不计入实参。"),
    ("E004", r"guard 引用了未声明的能力|能力 .*域|能力 .*重复",
     "能力域非法", "在模块顶层声明 `capability name : space[lo..hi]`，并保证 lo ≤ hi。"),
    ("E005", r"使用了 excluded 的空间",
     "与出界声明冲突", "要么删掉 `excluded \"<space>: ...\"`，要么换一个能力空间。"),
    ("E006", r"已被移动",
     "移动后使用", "移动后改用引用（`&`/`&mut`）传参，或让类型实现 `Copy` 语义（全字段为整型）。"),
    ("E007", r"既被可变借用又被借用|被可变借用两次",
     "借用冲突", "同一次调用里对同一变量只保留一种借用；先复制到临时变量再传。"),
    ("E008", r"无变体|重复模式|不是枚举|match 主体|不穷尽|不能带参数|不能绑定|"
             r"模式需绑定变量|与主体枚举",
     "match/变体用法非法", "模式必须覆盖所有变体且不重复；有载荷的变体要绑定变量，无载荷的不能带括号。"),
    ("E009", r"与基类型同名|结构体 .* 为空|枚举 .* 为空",
     "类型名非法", "类型名不要与 `u32` 等基类型重名；类型至少要有一个字段/变体。"),
    ("E010", r"\? 只能用于",
     "`?` 误用", "`?` 只能出现在 `let x: T = expr?;` 的绑定位置，且函数返回 `Result`。"),
    ("E011", r"只读切片不能写|形参需声明 mut|形参要求|实参是只读切片",
     "只读切片写入", "把形参改成 `mut [T]`，调用处用 `&mut 数组`。"),
    ("E012", r"悬垂",
     "悬垂借用", "不要返回局部变量的引用；把值返回（按值），或在调用方分配。"),
    ("E013", r"重复定义|重名|参数 .*重复|字段 .* 重复$|变体 .* 重复$",
     "重名", "同一作用域内重命名其中之一。"),
    ("E014", r"赋值目标不是左值",
     "赋值目标非法", "赋值左边必须是变量、字段或下标（左值）；先把结果存进变量。"),
    ("E015", r"无字段|缺字段|重复初始化|对非结构体类型取字段|对非数组/切片类型取下标|只能作用于数组",
     "字段/下标/取址非法", "字段名与顺序按声明补齐；下标与 `&`/`&mut` 只能作用于数组或切片。"),
    ("E016", r"数组字面量|数组长度不符",
     "数组字面量非法", "字面量非空、元素类型一致，且个数等于声明的长度。"),
    ("E017", r"as 目标类型非法|as 只能作用于",
     "`as` 转换非法", "`as` 只能整数⇄整数/布尔，目标必须是基类型；其它转换要显式构造。"),
    # E018 (2026-09-15 新增, 名字形式 `use <名字>` 的装载失败): 找不到与有歧义共用**一个码**,
    # 因为用户要做的是同一类事 —— 改这个 use, 或改文件布局。是哪种由消息本身写明。
    ("E018", r"名字导入找不到模块|名字导入有歧义|依赖 .* 里没有同名模块|自带的库里 .*|"
             r"use 的 .* 不存在|导入的 .* 不存在|循环导入|有语义错误|`addin .*` 找不到",
     "名字导入无法唯一解析",
     "`use <名字>` 按层搜: 项目本地 `deps/<名字>/<名字>.lomt`、工具链自带的库 "
     "(`<工具目录>/../share/lompi/store/<名字>/<版本>/`)、然后是内置四根 "
     "loment/lib、loment/examples、loment/selfhost、loment/tools —— 前两层先命中先用, "
     "**唯一性只管最后一层**。找不到就补文件或用路径形式 `use \"...lomt\"` 指明位置；"
     "内置根命中多处是作者写错名字，改名字。"),
    # E020 (2026-09-16 新增, 单元装载的**规模**上限): 单文件 `use` 条数。自举镜的暂存区
    # 原按 8 条布局, 超出的**静默丢掉**, 而参考实现无上限 —— 同一份源码两个实现给出不同
    # 的单元。现在两边都是 300 且超限报错。修法只有一种 (把门面拆小), 所以单独一码。
    ("E020", r"的 use 有 .* 条, 超过上限|覆盖计数块数超过",
     "单个文件的 use 太多",
     "一个文件的 `use` 上限是 300 条 (路径形式 + 名字形式合起来算)。"
     "别把整个库塞进一个门面文件 —— 拆成几个, 每层少拉一点。"),
    # E021 (2026-09-16 新增, **外部函数**签名): `extern fn` 第 1 阶段只收标量与 ptr
    # (docs/143 §3.1 / docs/173 §3)。聚合按值与 `str` 都会**改变调用点代码形状**, 所以
    # 一律报错退出而不是静默错编。修法只有一种 (换成受支持的形态), 单独一码。
    # **"与既有函数重名"不在这里** —— 那种消息里带"重名", 按「码按修法分」落进 E013
    # (改名字就是修法), 由排在前面的规则接走。写在这里也不会被匹配到, 徒增误导。
    ("E021", r"外部函数 .* (的参数 .* 类型|的返回类型) .* 不支持|外部函数 .* 不能带类型参数",
     "外部函数的签名不支持",
     "`extern fn` 第 1 阶段只收**标量**（i8..i64 / u8..u64 / bool）与 **ptr**, 不返回就"
     "省略 `-> T`。`str` 是「指针 + 长度」、不是 C 字符串；结构体/枚举/数组**按值**传参要走"
     "另一套寄存器分类规则 —— 两者都留到后面。见 `docs/173-loment-ffi.md` §3。"),
    # E022 (2026-09-17 新增, **项目模式 `choose`**): docs/143 §3.2。三条消息共用一个码 ——
    # 按「码按修法分」的口径, 用户要做的是同一件事 (改这一行的 choose, 或把它挪到根单元),
    # 是哪种由消息本身写明。与 E018「找不到 / 有歧义」共用一码是同一条理由。
    ("E022", r"核心模式只能声明一次|模式只能是|库不许 `choose`|未定义的开关|"
             r"开关 .* 写了两次|`choose` 有 .* 条, 超过上限|`addin` 有 .* 条, 超过上限|"
             r"不许写在另一个开关体里|库不许 `addin`|`addin` 单元不许声明核心模式|"
             r"`addin` 嵌套超过|`addin` 拉进来的单元里只能写 choose 相关代码|"
             r"依赖嵌套超过",
     "`choose` / `addin` 用法不对",
     "**核心模式**（`std`/`no_std`）声明的是**整个程序**的运行模式，所以只能出现一次、"
     "只能在**根单元**。**开关**（`set choose <名字> { … }` + `choose <名字>` / "
     "`choose close <名字>`）可以有很多（上限见 `MAX_CHOOSE`），但**同名只许写一次**，"
     "而且取值前要先用 `set choose` 定义。库不许 `choose` —— 库要表达需要就**声明能力需求**"
     "（`docs/168`），由项目决定。见 `docs/143` §3.2 与 `docs/182` §1。\n"
     "**`addin <名字>`** 是同一个语法的另一半（`docs/182` §1.4）：它拉进来的**不是库**，"
     "而是**一份开关设定** —— 所以那里面**正是**要写 `choose`，反过来**别的一律不许**"
     "（`fn`/`struct`/`use` 都拒），而且只有**根单元**能写 `addin`（库和 addin 单元都不行）。"
     "开关声明**不许嵌在另一个开关体里** —— 预扫看不见它。"),
    # E019 (2026-09-15 新增, **解析期**): parser 吐的是裸的 `行:列: 文本`, 原先一条都不在
    # 分类表里 —— `loment diag` 于是把它显示成 E999「未分类, 请报告」。语法错的修法只有一种
    # (按提示改那一行的写法), 所以按「码按修法分」的口径它们共用一个码。
    # **必须排在 E001 之后**: E001 的 `实参(期望|类型)` 里也有「期望」二字, 排前面会把它抢走。
    # `非法字符` 是**词法**期的, 但它与解析期那批是同一类修法 (改那一行的写法), 所以
    # 共用一个码。2026-09-17 补: 用一份 C 装 `.lomt` 时先撞到的就是它 (第 63 行的 `'0'`
    # 字面量), 而它当时**一条都不在分类表里** —— 显示成 E999「未分类, 请报告」。
    ("E019", r"期望 .*得到|期望表达式|非法字符|顶层只允许|未知顶层关键字|"
             r"pub 之后需要一项声明|数组长度必须为正|第一个参数必须是 self|"
             r"`set choose` 后面要跟一个块|的块没闭合",
     "语法错误（词法/解析期）",
     "按消息给的 `行:列` 改那一行的写法。常见三种: `match` 的臂体要**块**（`=> { … }`，"
     "不是 `=> 表达式`）；`return` 必须带值（没有 `return;`）；用了 Loment 没有的"
     "字面量或符号（单引号字符、`#`、`@`…）。**若这个文件本来就不是 Loment**，"
     "`loment diag` 会另给一条提示（见 `foreign_note`）。"),
]


#: 每条码的**一行 ASCII 说明** —— `loment codes` / `loment explain` 用它。
#:
#: **为什么是 ASCII 而不是复用 `RULES` 里那份中文标题**: CLI 的输出必须**纯 ASCII**
#: (`docs/169`; 936 控制台下 UTF-8 中文被按 GBK 解成乱码, 而 PE 垫片没有 `WriteConsoleW`,
#: 程序侧无法补救)。所以同一件事要两种文字, 这不是重复而是**两个受众**。
#:
#: **键是码的数字**(1..23), 不是 `"E001"` 那种串 —— 显示成 `E1` 还是 `E022` 是**呈现层**
#: 的事, 让它自己格式化。表里只存数, 免得又多一处字符串格式要同步。
#:
#: **这份表原先抄在 `loment/tools/lomcli.lomt` 里**(24 条 `codrow`), 与本表是同一件事的
#: 两份手抄 —— 而本仓对"同一份清单抄第二遍"的判词很硬(`docs/179` §7.3 那四个静默 bug
#: 全出自这一个原因)。2026-09-17 收拢到这里, 由 `--dump-surface` 生成给自举侧读。
#:
#: 与 `RULES` 的**键集必须相等**; 判据 `loment_tools_test::test_code_tables_cover_the_same_codes`。
ASCII_ONE_LINER: dict[int, str] = {
    1: "type mismatch (return / payload / builtin argument)",
    2: "something is not declared - usually a missing type annotation or a misspelt name",
    3: "wrong number of arguments",
    4: "capability domain problem (undeclared / out of range / duplicate)",
    5: "used space that an `excluded` declaration put out of bounds",
    6: "value has been moved",
    7: "borrow conflict (mutable borrow next to a borrow / two mutable borrows)",
    8: "match / enum (not exhaustive, duplicate pattern, bad payload binding)",
    9: "name clashes with a base type / empty struct / empty enum",
    10: "`?` used where there is no Result or Option",
    11: "slice mutability (cannot write a read-only slice; the parameter needs `mut`)",
    12: "dangling reference",
    13: "duplicate definition / duplicate name",
    14: "assignment target is not an lvalue",
    15: "field or index (no such field, missing field, duplicate init, field on a non-struct)",
    16: "array literal / length does not match the declaration",
    17: "illegal `as` cast",
    18: "`use <name>` does not resolve - rename it, add the file, or use the path form",
    19: "parse error - fix that line as the `line:col` in the message says",
    20: "more than 300 `use` in one file - split the facade",
    21: "`extern fn` signature outside FFI stage 1 - scalars and `ptr` only",
    22: "bad `choose`: written twice, bad mode name, or written in a library",
    23: "not implemented in this version - a compiler limit, not your code",
}


def code_num(code: str) -> int:
    """`"E023"` -> `23`。取不出来就是 0（调用方据此报"未知码"）。"""
    return int(code[1:]) if len(code) > 1 and code[0] == "E" and code[1:].isdigit() else 0


# ================================================================== 说明卡
#
# `RULES` 管**分类**（哪条消息属于哪个码），这里管**解释** —— 报错器渲染的就是这五块:
#
#     错了什么 / 为什么错 / 怎么改（至少 3 条）/ 支持 / 不支持
#
# **两张表的键必须相等**（判据钉着）：加了一个码却没写卡, 报错器就会渲染出一条“只有标题”的
# 诊断 —— 那种残件比不报还坏, 因为用户会以为“这条没有更多可说的了”。
#
# 写法上的三条自律（都是被实测逼出来的）:
#   * **`what` 一句话说清“是什么”**, 不要复述消息原文（原文已经渲染在上面了）;
#   * **`why` 说语言的规则**, 不是复述 `what`。用户看完要能自己判断**下一个**同类错;
#   * **`fixes` 每条都是能照做的一步**, 不是“检查一下”。三条的门槛是用户定的:
#     只给一条等于没有选择, 给三条才是“你自己按情况挑”。


@dataclass(frozen=True)
class Card:
    """一个码的说明卡。字段顺序 = 渲染顺序。"""

    what: str
    why: str
    fixes: tuple[str, ...]
    yes: str
    no: str


CARDS: dict[int, Card] = {
    1: Card(
        what="两处类型对不上 —— 返回值与声明、实参与形参、载荷与变体、内建实参、赋值两侧。",
        why="Loment **没有隐式数值转换**，也没有类型推断：每个位置上的类型必须显式对得上。"
            "所以 `u32` 的值不会自动变成 `i64`，整数 `1` 也不是 `bool` 的 `true`。",
        fixes=(
            "照签名改一侧：看函数声明的返回类型/形参类型，把表达式改成那一侧。",
            "整型宽度不同就显式写 `as`：`x as u64`、`n as i32`（整数⇄整数、整数⇄布尔、指针⇄整数都可以）。",
            "布尔与整数分开：条件位置写 `x != 0` 或 `true`，不要指望 `1` 能当 `true`。",
            "数组/结构体的元素类型要统一，逐个 `as` 或加一个转换函数。",
        ),
        yes="显式 `as`（整数⇄整数、整数⇄布尔、指针⇄整数）；标量/字符串/切片/数组/结构体/枚举作为类型",
        no="隐式提升与隐式转换（`u32` → `i64` 也必须写 `as`）；整数与布尔互用",
    ),
    2: Card(
        what="用了一个没有声明的名字 —— 变量、函数、类型、常量都算。",
        why="这是**新手撞得最多**的一条，而它在 Loment 里几乎总是同一件事："
            "**没有类型推断** —— `let x = 1;` 是语法错，必须 `let x: u32 = 1;`。"
            "其次是**先定义后使用**（同一模块内），以及跨模块要 `pub` 导出 + `use` 导入。",
        fixes=(
            "补类型标注：`let x: u32 = 1;`（`let` 不带类型是 **E19 语法错**，但很容易被当成这条）。",
            "拼错了就改拼写；跨模块调用给声明加 `pub`，调用方补 `use <模块名>` 或 `use “./x.lomt”`。",
            "泛型函数的实参要能把 `T` 定出来：先 `let a: u32 = 4;` 再 `pick(a, b)` —— 直接传整数字面量会报这条，消息有误导。",
            "确实是别处来的名字：用路径形式 `use “./util.lomt”` 指明位置。",
        ),
        yes="泛型（调用点能定出 T 时）／trait 静态派发／`pub` 跨模块／两种 `use`",
        no="类型推断／隐式全局／同一模块内的前向引用／隐式数值转换",
    ),
    3: Card(
        what="调用时给的实参个数与函数签名对不上。",
        why="形参最多 **10 个**；方法调用的 `self` **不计入**实参；Loment 没有默认参数、没有可变参数、"
            "也没有重载 —— 名字不同就是不同函数。",
        fixes=(
            "照着签名数一遍，多的删、缺的补。",
            "方法调用别把 `self` 算进去（`obj.m(a)` 是 1 个实参，不是 2 个）。",
            "想写重载就换个名字：`parse_int` / `parse_float` —— Loment 按名字解析，同名不同参不允许。",
        ),
        yes="最多 10 个形参；方法调用的 `self` 由编译器补",
        no="默认参数／可变参数／函数重载",
    ),
    4: Card(
        what="能力域本身有问题 —— 没声明就 `guard`、`[lo..hi]` 的边界不对、同名重复声明。",
        why="能力域是 Loment 唯一“新加的东西”：`capability c : space[lo..hi]` 声明一个**索引域**，"
            "`guard c(i);` 检查 `i` 落在域内（字面量越界是**编译期错**，其余是运行期 trap）。"
            "所以域必须**先声明**、`lo <= hi`、同一空间同一个名字只声明一次。",
        fixes=(
            "在模块顶层补声明：`capability c : disk[0..4]`（`c` 是名字，`disk` 是空间名）。",
            "边界反了就调正：必须 `lo <= hi`（`[0..4]` 是 0,1,2,3,4 五个值）。",
            "重名就改名；同一空间可以有多个域，各用各的名字。",
            "索引是字面量且越界：改索引或把域放大 —— 这是编译期检查，不是运行期。",
        ),
        yes="`capability` + `guard`；`revocable` 标；字面量越界的编译期检查",
        no="**授权** —— `guard` 只约束索引落在域内，不检查“当前主体有没有这个能力”（主体-能力的绑定在内核侧）",
    ),
    5: Card(
        what="用了一个被 `excluded` 声明**排除在外**的空间。",
        why="`excluded “space: ...”` 是**出界声明**：它把一个空间从“本程序可以碰的”里拿掉，"
            "是给安全审计看的一份承诺。于是再用那个空间就与自己的承诺冲突 —— 这是**故意的**，"
            "不是编译器挑剔。",
        fixes=(
            "删掉那条 `excluded`（如果那份承诺本来就不该有）。",
            "换一个没有被排除的空间来声明能力域。",
            "保留承诺、把用到它的那处改掉 —— 能力域本来就是用来把访问收在明确范围内的。",
        ),
        yes="`excluded “<space>: ...”` 出界声明；多个能力域各占各的空间",
        no="局部豁免（“只在这一处放行”做不到）；对同一个空间既排除又使用",
    ),
    6: Card(
        what="一个值被**移动**给别人之后，又用它了。",
        why="Loment 的聚合类型（`struct` / `enum`）默认是**移动**语义：按值传进函数、按值赋给另一个变量，"
            "都相当于把所有权交出去。**全字段是整型**的类型自动按 `Copy` 处理，不会报这条。",
        fixes=(
            "改成传引用：`f(&v)` 或 `f(&mut v)` —— 最常用的改法。",
            "确实要留一份：先在**移动之前**把需要的字段拷到新变量，再传。",
            "让类型变成可 `Copy` 的：字段全用整型（`u32`/`i64`/`bool`…），去掉里面的字符串/切片/指针。",
            "把所有权安排清楚：让它最后被用到的那一处消费，前面都用引用。",
        ),
        yes="显式 `&` / `&mut`；全字段整型的聚合自动 `Copy`",
        no="隐式复制聚合；移动后再使用同一个值",
    ),
    7: Card(
        what="同一个变量在同一次调用里，既被可变借用又被借用，或者被可变借用两次。",
        why="`&mut` 是**独占**借用（与 Rust 同一套规则）：可变借用存在期间，别的借用（包括只读的）"
            "都不能同时存在。这不是性能问题，是“谁能改它”的答案必须唯一。",
        fixes=(
            "只留一种借用：把 `&a, &mut a` 改成两次都用 `&a`（只读），或都改成 `&mut`。",
            "先把值取出来再传：`let n: u32 = a.x;` 然后传 `&mut a, n`。",
            "把一句话拆成两句：先读完、把结果存进变量，再做可变借用。",
            "要同时读写同一块，就按“读出来 → 算 → 写回去”三段写。",
        ),
        yes="同一作用域里多个**只读**借用；一次可变借用",
        no="可变借用与任何其它借用并存；同一变量的两次可变借用",
    ),
    8: Card(
        what="`match` 或枚举的用法有问题：不穷尽、模式重复、载荷绑定不对、主体不是枚举、臂体写成了表达式。",
        why="`match` 必须**穷尽**（或者有 `_` 兜底）；**臂体是块**、不是表达式；"
            "带载荷的变体模式要绑变量、无载荷的不能带括号；"
            "**泛型枚举**的模式要用**单态化之后**的名字（`Result_i64_u32::Ok(v)`），不是 `Result::Ok(v)`。",
        fixes=(
            "臂体写成块：`E::A => { return 0; }`（写成 `E::A => 0,` 是 E19 语法错）。",
            "加一条 `_ => { ... }` 兜底，或把剩下的变体补全 —— 枚举加了变体之后这里就会红，这是故意的。",
            "泛型枚举用单态化名：`Result_i64_u32::Ok(v)` / `Result_i64_u32::Err(e)`（先看值的类型定宽）。",
            "载荷跟着变体走：`Kind::Big(w)` 要绑 `w`，`Kind::Small` 不能写 `Kind::Small()`。",
        ),
        yes="`match` / `if let` / 单载荷变体 / `_` 兜底 / 泛型枚举（用单态化名）",
        no="臂体是表达式；多载荷变体；非穷尽且没有 `_`；`==` 直接比较枚举（checker 放行但原生后端会拒）",
    ),
    9: Card(
        what="类型名与基类型重名，或者声明了一个**空**的 `struct`/`enum`。",
        why="`u8 u16 u32 u64 i8 i16 i32 i64 bool ptr str` 是**基类型名**，用户类型再叫这些名字，"
            "后面就没法区分了。空聚合同理：一个字段/变体都没有的类型表达不了任何东西，"
            "**当错误拒掉比让它悄悄存在好** —— 它多半是写了一半。",
        fixes=(
            "改名，别与基类型撞：`U32Box` / `Id` / `Amount`（带语义的名字比 `U32` 更该用）。",
            "`struct` 加字段；只是“先占个位”就等有字段了再声明。",
            "`enum` 至少给一个变体；空枚举连 `match` 都写不出来。",
        ),
        yes="任意多字段的 `struct`；任意多变体的 `enum`（含单载荷）",
        no="与基类型重名；空 `struct`；空 `enum`",
    ),
    10: Card(
        what="`?` 用在了不返回 `Result`/`Option` 的地方，或者不在 `let` 的绑定位置。",
        why="`?` 的语义是“**错了就直接返回，对了就取出载荷**” —— 所以它要求两件事："
            "当前函数返回 `Result`（或 `Option`），而且 `?` 只能出现在 `let x: T = expr?;` 的绑定位置。"
            "它不是一个“忽略错误”的记号。",
        fixes=(
            "把当前函数的返回类型改成 `Result<..., ...>`（或 `Option<...>`）。",
            "不用 `?`，显式 `match` 两个分支各写一段 —— 想在原地处理错误时用这条。",
            "把 `?` 挪到绑定位置：`let v: u32 = f()?;` 而不是 `g(f()?);`。",
        ),
        yes="`Result` / `Option` / 绑定位置的 `?` / `match` 显式处理",
        no="在非 `let` 位置用 `?`；在返回非 `Result`/`Option` 的函数里用 `?`",
    ),
    11: Card(
        what="往一个**只读**切片里写，或者形参没声明 `mut`。",
        why="`fn f(xs: [u32])` 拿到的是**只读**切片 —— 切片是“借来的视野”，能不能写由形参类型决定。"
            "要写就得 `mut [u32]`，而调用处必须传 `&mut arr`（只读切片没人能借成可写的）。",
        fixes=(
            "形参改 `mut [u32]`。",
            "调用处改 `&mut arr`（原来大概是 `&arr`）。",
            "不改原数组：把切片内容复制到一个本地数组，改完再自己决定怎么用。",
        ),
        yes="`[T]`（只读）与 `mut [T]`（可写）两种切片；`&arr` / `&mut arr` 两种传法",
        no="在只读切片上写；把 `&arr` 借成可写的",
    ),
    12: Card(
        what="返回了一个指向**局部变量**的引用。",
        why="局部变量在函数返回时就不存在了，返回给它的引用必然悬垂。所以能安全返回的引用，"
            "只能来自**形参**（或来自调用方传进来的内存）—— 这是生命周期规则在 Loment 里的落点。",
        fixes=(
            "返回值本身（按值返回）—— 最直接的一条。",
            "让调用方分配：形参收 `&mut`，把结果写进调用方给的内存。",
            "返回不依赖内存的信息：下标、长度、是否命中（`bool`）。",
            "确实要返回切片：让它指向形参而不是局部（`fn f(xs: [u32]) -> [u32] { return xs; }`）。",
        ),
        yes="返回来自形参的引用/切片；按值返回",
        no="返回指向局部变量的引用或切片",
    ),
    13: Card(
        what="同一作用域里同一个名字出现了两次 —— 函数、类型、常量、字段、变体、参数都算。",
        why="`use` 是**平名字空间**：一个单元里的顶层名字必须唯一，所以**两个库也不能有同名顶层项**。"
            "这也是为什么 std 里的公开名字都带前缀（`json_parse` / `sha256_hex`）——"
            "不带的迟早会撞。",
        fixes=(
            "重命名其中之一（最省事）。",
            "给名字加所属前缀：`json_parse` / `sha_parse`，撞名的可能立刻消失。",
            "两个库确实都要用：把其中一个的调用点改成路径形式 `use “./x.lomt”` 并改名导入后的符号。",
            "结构体字段/枚举变体重名：按语义改（`width`/`height` 而不是两个 `size`）。",
        ),
        yes="一个单元里任意多模块/函数/类型，只要名字不撞",
        no="同名顶层项共存；两个库同时导出同一个名字",
    ),
    14: Card(
        what="赋值左边不是一个能放东西的位置（不是左值）。",
        why="只有**变量、字段、下标**可以被赋值 —— 它们指向一块确定的内存。"
            "表达式的结果（`a + b`、函数返回值）没有地址，所以放不了东西。",
        fixes=(
            "先把结果存进变量，再改那个变量。",
            "目标写成 `arr[i]` 或 `s.field` 这种“有位置”的形式。",
            "想改切片元素：把切片形参声明成 `mut [T]`（否则连 `arr[i] = x` 都写不了）。",
        ),
        yes="变量、结构体字段、数组/切片下标赋值",
        no="给表达式或字面量赋值（`1 = x`、`f() = x`）",
    ),
    15: Card(
        what="字段或下标用错了 —— 没有这个字段、缺字段、重复初始化、对非结构体取字段、对非数组取下标。",
        why="结构体字面量的字段要**齐全且不重复**（没有默认值这回事）；"
            "`.` 只能用在结构体上，`[]` 只能用在数组/切片上，`&`/`&mut` 只能作用于数组或切片。"
            "另外两条形状上的规矩：**结构体字面量不能直接当实参**，**条件位置不能直接写结构体字面量**。",
        fixes=(
            "照声明补齐字段（名字和类型都要对）—— 缺哪个补哪个，多哪个删哪个。",
            "结构体字面量先落到变量：`let s: S = S { a: 1 };` 再 `f(s)`（当实参直接写会被后端拒）。",
            "条件里要用字段：加括号 `if (s.a) { }` —— 不然 `if s { }` 的歧义按 Rust 规则消解。",
            "取切片写 `&arr`（整块），`&arr[i]` 是取单个元素的地址，不是切片。",
        ),
        yes="字段读写、下标读写、`&arr` 取切片、结构体字面量（先绑变量）",
        no="结构体字面量直接当实参；条件位置直接写结构体字面量；缺字段/多字段字面量",
    ),
    16: Card(
        what="数组字面量有问题 —— 是空的、元素类型不一致、或者个数与声明长度不符。",
        why="`let xs: [u32; 3] = [1, 2, 3];` —— **长度是类型的一部分**，所以个数必须写死对得上，"
            "没有“按字面量长度自动推断”这回事（那需要类型推断，而 Loment 没有）。",
        fixes=(
            "让个数等于声明长度：声明 `[u32; 3]` 就给三个。",
            "元素类型统一：混了 `u32` 与 `i32` 就逐个 `as` 成同一种。",
            "空字面量不行：给个长度，或者改用切片参数 `[T]` 加 `&xs` 传进来。",
            "长度要变：那是切片不是数组 —— 形参写 `[T]`，调用处 `&arr`。",
        ),
        yes="定长数组 `[T; N]`；字面量 `[1, 2, 3]`；下标读写",
        no="空字面量；按字面量推断长度；元素类型不一致的数组",
    ),
    17: Card(
        what="`as` 的目标类型不对，或者这个方向转不过去。",
        why="`as` 只做**位级**的转换：整数⇄整数、整数⇄布尔、指针⇄整数。"
            "别的（字符串→数字、结构体→结构体）不是“转换”而是“解析/构造”，必须写出过程来。",
        fixes=(
            "目标改成基类型：`u8..u64` / `i8..i64` / `bool` / `ptr`。",
            "两个结构体之间要转：写一个显式函数，逐字段搬（顺便想清楚缺的字段怎么办）。",
            "字符串→数字：自己写解析循环（`str_byte` 逐字节，累加），没有内建 `parse`。",
            "整数→字符串：照 `write_dec` 的写法（取余 + 反序），也没有内建格式化。",
        ),
        yes="整数⇄整数、整数⇄布尔、指针⇄整数的 `as`",
        no="聚合之间的 `as`；字符串与数字之间的 `as`（要自己写解析）",
    ),
    18: Card(
        what="`use <名字>` 解析不出来 —— 要么找不到，要么命中多处不知道用哪个。",
        why="名字形式按**层**搜：① 项目本地 `deps/<名字>/<名字>.lomt` → ② 工具链自带 store → "
            "③ 内置四根（`loment/lib` → `examples` → `selfhost` → `tools`）。"
            "**前两层先命中先用**，唯一性只管第 ③ 层。所以撞名的症状通常是“我以为是另一个”。",
        fixes=(
            "补文件到该在的位置（`deps/<名字>/<名字>.lomt` 是最正规的落点）。",
            "改名字，别与内置根里的模块撞（第 ③ 层命中多处就是名字起坏了）。",
            "用路径形式指明位置：`use “./util.lomt”` —— 路径形式不参与这套搜索。",
            "循环导入：把两边都要用的部分抽成第三个文件，两边都 `use` 它。",
        ),
        yes="两种 `use`（名字 / 路径）；单文件最多 300 条；按层搜索 + 前缀优先",
        no="第 ③ 层的歧义；循环导入；超过 300 条（那是 E020）",
    ),
    19: Card(
        what="这一行按 Loment 的语法读不通 —— 词法期或解析期就停了。",
        why="Loment 是 **Rust 的严格子集**，但它**去掉了**几样东西：单引号字符字面量、"
            "无值 `return;`、表达式式的 `match` 臂、`mut` 作为绑定修饰词。"
            "照 Rust 的习惯写很容易踩到这几处 —— 它们不是“暂时不支持”，是这门语言没有。",
        fixes=(
            "按消息给的 `行:列` 看那一处（`@@@`、单引号、`#` 这类符号先查 `loment syntax`）。",
            "`match` 臂改成块：`E::A => { return 0; }`。",
            "`return` 补值：`return 0;`（Loment 没有 `return;`）。",
            "`let` 补类型：`let x: u32 = 1;`；本地变量不要写 `mut`（那是普通标识符，不是关键字）。",
        ),
        yes="Rust 子集那套语法；`//` `/* */` 注释；`let x: T = e;`",
        no="单引号字符字面量；无值 `return;`；表达式式 match 臂；`let mut x: T`（`mut` 不是关键字）",
    ),
    20: Card(
        what="一个文件里的 `use` 超过了 **300 条**。",
        why="这是一道**防静默丢**的闸门：自举镜像里那个暂存区原来就那么大，超出的会被悄悄丢掉，"
            "于是同一份源码在参考实现与自举镜下**给出不同的单元**。现在两边都是 300，超限报错 ——"
            "宁可报错也不要“少了一块但看起来编过了”。",
        fixes=(
            "把门面拆小：`core.lomt` 拉一半、`ext.lomt` 拉另一半，用的人按需 `use`。",
            "只 import 真正要用的门面（不用把整个库塞进一个文件）。",
            "确实很多：按层次组织（每一层只 `use` 下一层的几个），别做成一个几百条的表。",
        ),
        yes="单文件最多 300 条 `use`（路径形式 + 名字形式合起来算）",
        no="超过 300 条（超限是**报错**，不是截断）",
    ),
    21: Card(
        what="`extern fn` 的签名超出了 FFI 第 1 阶段收的那几种。",
        why="第 1 阶段只收**标量**（`i8..i64` / `u8..u64` / `bool`）与 `ptr`。"
            "`str` 在 Loment 里是“**指针 + 长度**”，**不是** C 字符串，两者不通用；"
            "结构体/枚举按值传要另一套寄存器分类规则 —— 都会**改变调用点的代码形状**，"
            "所以一律报错退出而不是静默错编。",
        fixes=(
            "换成标量或 `ptr`；不返回就省略 `-> T`（那样就是 void）。",
            "要传字符串：自己在内存里拼一个 **NUL 结尾**的字节串，形参写 `ptr`（C 那边按 `char*` 收）。",
            "要传结构体：改成传指针，或者把字段拆成几个标量参数。",
            "需要泛型：`extern fn` **不能带类型参数** —— 在 Loment 侧包一层泛型函数，里面调具体的那条。",
        ),
        yes="标量与 `ptr` 的形参/返回；多个 `--link`；调用点走平台 C ABI（Linux 前六个整数实参进 rdi/rsi/rdx/rcx/r8/r9；Windows 进 rcx/rdx/r8/r9）",
        no="`str` 形参/返回（那是“指针+长度”，不是 C 字符串）；结构体/枚举/数组**按值**；`extern fn` 带类型参数；PE 目标的 FFI；`.a` 归档与 `.so`/`.dll` 动态库",
    ),
    22: Card(
        what="开关（`choose`）或核心模式（`std`/`no_std`）的写法不对。",
        why="这是**两个不同的东西**被放在一起管：① **核心模式**是**整个程序**的属性 —— 只能出现一次、"
            "只能写在根单元（`loment.conf` 之外没有第二个地方能改它）；② **开关**是“打开才编进去的代码”，"
            "可以有很多条，但**同名只许写一次**、**取值前必须先 `set choose` 定义**。"
            "库不许 `choose` —— 库要表达需要就声明**能力需求**，由项目决定开不开。",
        fixes=(
            "核心模式只写一次，而且只在入口那一份：`choose std` 或 `choose no_std`。",
            "开关先定义再取值：`set choose verbose { … }` 然后 `choose verbose`（或 `choose close verbose`）。",
            "定义在别的单元就把它拉进来：入口写 `addin chooseset`，那份 `chooseset.lomt` 里写 `set choose`。",
            "库里的 `choose` 搬到根单元去（库这一侧改成声明能力域）。",
            "声明别嵌在另一个开关体里 —— 预扫看不见它，那会让“开没开”变成鸡生蛋。",
        ),
        yes="单文件最多 500 条开关；`addin` 跨文件带开关设定（只有根单元能写）；空体开关（只驱动编译器行为）；库声明能力域",
        no="核心模式写两次或写在库里；取值前没定义；同名两个取值；嵌套声明；库写 `choose` 或 `addin`",
    ),
    23: Card(
        what="你写的东西语法和语义都对 —— 是**这一版的编译器后端还没做这块**。",
        why="参考实现有两条后端：一条 Rust 转译路径走得远，原生 IR 路径（M0 起）是**逐块补**出来的。"
            "这条消息来自后者 —— 所以它**不是你的错**，把它当成“设计上还没到这里”看。"
            "这也是它单独一个码的理由：用户能做的事（换个写法绕开）与“照建议改那一行”完全不同。",
        fixes=(
            "换个写法绕开：用循环 + 数组代替还没支持的表达式，或用内建能做的那几种形态。",
            "查进度：`docs/145` 的里程碑表里看这一块排在哪一档。",
            "绕不开就报告：附上这条消息 + 最小可复现的那个文件（`loment ir` 的输出也有用）。",
        ),
        yes="标量运算、控制流、字符串、数组、切片、结构体、枚举（`match`）—— 按值传聚合看目标后端",
        no="**这条消息本身就是“不支持”** —— 具体范围以 `docs/145` 的里程碑表为准，不在源码那一侧",
    ),
}


# ================================================================== 语言卡
#
# 与**六语言翻译线**（`docs/186` C · `docs/187` Python · `docs/188` §7.1 的
# Python/Java/Go/C/C++/C#）衔接的那一半：源文件不是 Loment 时, 报错器要说清“它是什么、
# 怎么把它弄进来、这门语言的边界在哪“。
#
# **键集由判据钉住 == `potato_from.LANGS`**（加一门语言就红, 提示照哪张卡写）。
# 这是本仓那条“消费者清单要和判据一起长”（`docs/158` §5）的又一次落点:
# 翻译线加语言而报错器不知道, 症状是“新语言的文件报错时只字不提怎么翻” —— 静默缺口。
#
# **特征词要挑"Loment 里不会出现"的**: `fn` / `impl` / `class ` 这类 Loment 自己也有,
# 拿它们当特征会让一份正经 Loment 文件（注释里写一句 `import` 就够）被误判成外源。
# 所以这里的词是**这门语言独有**的形状（`#include` / `def ` / `use std::` / `System.out`…）。
#
# **`tokens` 只做粗提示, 不做判定**: 真正的语言判定是翻译器的 `--lang auto`（它跑
# `potato_from.detect_lang`）。所以渲染出来的命令**一律带 `--lang auto`**,
# 报错器说“看内容像 X", 而不是”这就是 X“ —— 一句话说错会让用户拿着错的命令去试。


@dataclass(frozen=True)
class LangCard:
    """一门源语言的卡。`key` 与 `potato_from.LANGS` 的键一致。"""

    key: str
    display: str
    exts: tuple[str, ...]        # 后缀（`potato_from.EXT` 的真源，这里只做镜像说明）
    tokens: tuple[str, ...]      # 内容特征（粗提示）
    edge: str                    # 这门语言的边界：什么翻不过去
    abi: str                     # 调用约定那一档的**事实**（**不许**是"这条路通不通"的结论）


LANG_CARDS: dict[str, LangCard] = {
    "c": LangCard(
        key="c", display="C", exts=(".c", ".h"),
        tokens=("#include", "int main(", "printf("),
        edge="指针算术、`union`、位域、宏、可变参数（`...`）、`goto` 进来的跳转都不翻；"
             "`int` 与条件混用（`if (x)`）、`&&` 出 int 要**补转换**（Loment 的条件只收 bool）。",
        abi="C ABI 是它默认的导出方式（`--link` 那份 `.o` 要自包含：不能有重定位、不能引用未定义的符号）。",
    ),
    "python": LangCard(
        key="python", display="Python", exts=(".py",),
        tokens=("def ", "__name__", "self."),
        edge="动态类型、类继承、异常、生成器、装饰器、闭包捕获都不翻；"
             "`/` 是真除法（要整除得写 `//`，而 Loment 的 `/` 是整除）；`True`/`False` 是 int 的子类，**翻过来要补转换**。",
        abi="**CPython 不导出 C ABI** —— 只出接口单元会得到一个**空 module**（stderr 上有 `[skip] f: 调用约定不是 C ABI`）。要真链接得走 C 扩展那条路。",
    ),
    "rust": LangCard(
        key="rust", display="Rust", exts=(".rs",),
        tokens=("use std::", "let mut ", "macro_rules!", "#[derive"),
        edge="trait 对象、闭包、宏（`macro_rules!`）、生命周期标注、`async` 都不翻；"
             "`&str` 与 `String` 都是“指针+长度”，翻过来是 `str`；借用检查那一套 Loment 同样管，所以这块一般能对上。",
        abi="Rust 要导 C ABI 得在源里显式写 `extern \"C\"` + `#[no_mangle]`。",
    ),
    "go": LangCard(
        key="go", display="Go", exts=(".go",),
        tokens=("func ", "package main", "import ("),
        edge="goroutine、channel、interface、`defer`、多返回值（Loment 只回一个值）、"
             "`string`、切片、`map`、指针都不翻；"
             "**类型写在名字后面**（`func f(a int) int`）、条件**不带括号**、"
             "**没有 `while`**（一个 `for` 管三种形状，含 `for { }` 死循环）。"
             "`int` 有平台宽度（x86-64 上是 64 位，所以映 `i64`）；`/` 与 `%` 向零截断（与 Loment 同）。"
             "**两处与别的门相反、而理由都在源语言那边**：① Go **没有隐式数值转换**，"
             "所以 `int(b)` 那种转换到处都在 —— 而它正好对上 Loment 的 `as`，"
             "源码里已经写好了、翻译器不用猜（C / C++ 要靠翻译器补，Java / C# 一个都不补）；"
             "② `i++` / `x += e` 在 Go 里是**语句、没有值**，所以照收（`i = i + 1`）——"
             "而 C / Java / C# 里它们是有值的表达式，那几门拒。",
        abi="默认**不**导 C ABI；要在源里显式写 `//export`。",
    ),
    "java": LangCard(
        key="java", display="Java", exts=(".java",),
        tokens=("public class", "static void main", "System.out"),
        edge="类继承、接口、泛型擦除、异常、`String`、集合类都不翻；"
             "`boolean` 与 `int` 是分开的（`&&` 出 boolean，所以**不用补转换** —— 这点与 C 相反）；"
             "`>>>` 是无符号右移（Loment 的 `>>` 分有符号/无符号两种写法）。",
        abi="**JVM 默认不导出 C ABI**；要链接得上 JNI 或 NativeAOT。",
    ),
    "csharp": LangCard(
        key="csharp", display="C#", exts=(".cs",),
        # **特征词必须是 C# 独有的**：`lang_by_tokens` 按**键的字典序**逐门问、取第一个命中,
        # 而 `csharp` 排在 `java` 前面 —— 所以这里要是写了 `public class`，一份**真 Java**
        # 就会被报成 C#。（`using System` / `static void Main` / `string[] args` 都是
        # C# 独有：Java 的入口是小写 `main(String[]`，而小写 `string` 在 Java 里不是类型。）
        tokens=("using System", "static void Main", "string[] args"),
        edge="类继承、接口、泛型、LINQ、属性（`get; set;`）、事件、委托、异常都不翻；"
             "`using` / `namespace` / `class` 三层外壳会被抹掉（花括号形与文件级 "
             "`namespace X;` 都收）；`bool` 与 `int` 是分开的（`&&` 出 bool，**不用补转换**，"
             "这点与 C 相反）；`>>>` 是 C# 11 起的无符号右移。"
             "**`byte` 是 0..255 无符号的** —— 与 Java 的 `byte`（-128..127 有符号）**相反**。"
             "**不做整数宽度跟踪**：`int f(byte b) { return b; }` 那种混宽度的单元，"
             "翻出来的 Loment 会在检查时报「return 类型 u8，函数声明 i32」—— "
             "**响亮地失败，不是静默算错**（宽度跟踪是下一版的事）。",
        abi="**.NET 默认不导出 C ABI**；要链接得上 NativeAOT 或 `[UnmanagedCallersOnly]`。",
    ),
    "cpp": LangCard(
        key="cpp", display="C++", exts=(".cpp", ".cc", ".cxx", ".hpp"),
        # 同上：**特征词必须是 C++ 独有的**，而且要能赶在 `c` 前面（见 `LANG_ORDER`）。
        tokens=("std::", "template<", "using namespace", "#include <vector>"),
        edge="类、模板、STL、异常、引用、指针、运算符重载都不翻；"
             "预处理指令（`#include` / `#define`）不收，所以语料不带 include；"
             "`std::cout` 那类成员访问也不收（`::` 连分词都过不去）。"
             "**它有真正的 `bool`**（不像 C 得靠 `<stdbool.h>`），而 `int x = (a < b);` "
             "与 `if (x)`（x 是 int）**两边都合法** —— bool 与 int 双向隐式转换都留着，"
             "所以两个方向的强制转换**都要补**：结论与 C 一样、**理由完全不同**。"
             "`char` 的符号性由实现决定（x86-64 上 g++ 是 signed，ARM 上常常不是），"
             "映 `i8` / `u8` 都会在某台机器上悄悄算错，所以**拒**。",
        abi="能导 C ABI —— 要显式写 `extern \"C\"`（默认名字是 mangle 过的）。",
    ),
}

#: 渲染顺序 —— **不是字典序，是有意的先后**。
#:
#: `lang_by_tokens` 是**逐门问、取第一个命中**，所以"判据更专有"的必须排在前面：
#: C++ 的 `#include <vector>` 会被 `c` 那三条（`#include` / `int main(` / `printf(`）
#: 先接走，`csharp` 的 `public class` 会被 `java` 接走 —— 不调顺序的话，这两门
#: 各自的外源提示会**指到另一门**上去。
#:
#: **没列到的语言自动排到最后（按字典序）**，所以加一门语言不必动这张表 ——
#: 这和"键集由判据钉着 == `potato_from.LANGS`"是两件事，别混。
LANG_ORDER = ("cpp", "c", "csharp", "java", "go", "rust", "python")




#: `码 -> (码, 中文标题, 中文建议)`，给 `--dump-surface` 用。**从 `RULES` 推导，不另写一份**
#: —— 又一处"抄第二遍"的诱惑，而这份文件这一节讲的就是别抄。
RULES_BY_CODE: dict[int, tuple[str, str, str]] = {
    code_num(c): (c, t, h) for c, _pat, t, h in RULES
}


def classify(msg: str) -> tuple[str, str, str]:
    for code, pat, title, hint in RULES:
        if re.search(pat, msg):
            return code, title, hint
    return "E999", "未分类", "请报告此消息以便补充分类规则。"


def foreign_note(path: Path, errs: list[str]) -> str | None:
    """文件报错、而**看内容是别的语言**时, 给一条对得上的建议。

    **为什么非要有这一条**: 一份 C 源码叫 `.lomt` 时, `loment diag` 会照着 E019 的通用
    建议说"按消息给的行:列**改那一行的写法**" —— 那条在这里是**错的**: 那份 C 的语法本来
    就对, 它只是不是 Loment。照建议改会把一份好 C 改坏。

    **触发条件是"有错就闻一下", 不是某一条特定消息** —— 这条是实测改过来的:
    第一版只认 `期望 module`, 而那份 C 在第 63 行有个 `'0'` 字符字面量, **词法**先于解析
    就把它拒了 (`63:25: 非法字符 "'"`), 于是根本没走到"期望 module", 提示不出现。
    改文件的人会以为"这条判据没生效", 而真相是触发面太窄。
    """
    if not errs:
        return None
    try:
        import potato_from
        # 注意用 `resolve_lang` 而不是 `detect_lang`: 后缀优先 (一份 `.c` 有没有错都该
        # 按 C 说), 内容兜底 —— 判语法的规则只有一处, 见 potato_from.resolve_lang。
        lang, why = potato_from.resolve_lang(path, "auto")
    except Exception:                                                # noqa: BLE001
        return None
    # **从 `potato_from` 推导, 不写死** —— 原先这里硬编码了 `("c","rust","python")`,
    # 于是加了 Go/Java 之后, 一份 Go 源码的诊断提示**静默消失**(`resolve_lang` 明明
    # 认出来了)。硬编码一份"支持哪些语言"的清单, 必然在加语言时漏掉。
    if lang in potato_from.LANGS:
        card = LANG_CARDS.get(lang)
        head = (f"这个文件**不是 Loment 语法**, 看内容是 "
                f"**{card.display if card else lang.upper()}**（{why}）。"
                f"别照上面那条改 —— 它的语法本来就是对的。")
        # 语言的**边界**与**调用约定**与报错器**共用同一段文字**（`LANG_CARDS`）——
        # 抄第二份必然漂, 而这两句正是用户最需要对齐的部分。
        if card:
            # 用普通拼接而不是 f-string 插值换行：f-string 的表达式段里放不了 `\n` 转义，
            # 而插一个模块级常量就要多一处"只有这里用"的名字（上一版就是那么写的，
            # `NL` 从没定义过，而这条路只有"外源文件 + 有错"才走到，判据没覆盖到）。
            head += ("\n      这门语言的边界: " + card.edge
                     + "\n      调用约定: " + card.abi)
        # **该建议 `--impl` 还是接口单元，要看接口那条路走不走得通** —— 这不是锦上添花：
        # Python 那类的 `abi` 不是 C，接口单元**一条函数都发不出来**，照默认那条命令敲会
        # 得到一个**空 module**（stderr 上有 `[skip] f: 调用约定不是 C ABI`，但产物看着
        # 像"编过了"）。一份"编过了但什么都没有"的东西，比报错还难查。
        #
        # **判据是问出来、不是按语言写死**：直接拿 `lomt_from.emit_lomt(impl=False)` 数
        # 它到底发了几条 `pub extern fn`。写死一张"哪些语言有翻译器/哪些 ABI 是 C"的表，
        # 必然在加语言时漏掉（上面那段注释就是为同一个毛病写的）。
        try:
            _doc, _rep = potato_from.transcribe(path, "auto", "strict")
            n_body = sum(1 for f in (_doc.get("functions") or [])
                         if isinstance(f.get("body"), str) and f["body"].strip())
            import lomt_from
            iface, _sk = lomt_from.emit_lomt(_doc, impl=False)
            n_iface = iface.count("pub extern fn ")
        except Exception:                                            # noqa: BLE001
            return None
        #: 命令行前缀。**`--impl` 由每个分支自己拼** —— 拼进这里会让"不带 --impl"那行
        #: 也带上它（第一版就是这么错的：两条命令都变成了 `--impl`，还重了一遍）。
        cmd = f"       python tools/lomt_from.py \"{path}\" --lang auto "
        if n_body and n_iface == 0:
            # 接口那条路一条都发不出来（ABI 不是 C，或者签名过不了 FFI 那道闸门）
            return (head + f"**它带着函数的正文**，而这个语言走不了接口那条路"
                    f"（ABI 不是 C，或签名超出 FFI 第 1 阶段）—— 只发接口会得到一个"
                    f"**空 module**。翻成 Loment 实现：\n"
                    f"{cmd}--impl --out {path.stem}.impl.lomt\n"
                    f"      （`--impl` 把正文翻成真的 `pub fn`。见 docs/186 / docs/187）")
        if n_body:
            # 两条路都走得通：接口单元（实现在外面，链目标文件）或翻成实现
            return (head + f"它**既可以**只出接口、也**可以**把正文翻成实现：\n"
                    f"{cmd}--out {path.with_suffix('.iface.lomt').name}\n"
                    f"           （不带 `--impl`：接口单元 —— 实现在源语言那一侧，"
                    f"配外面编好的目标文件用。见 docs/179 §2）\n"
                    f"{cmd}--impl --out {path.stem}.impl.lomt\n"
                    f"           （带 `--impl`：把正文翻成真的 `pub fn`。"
                    f"见 docs/186 / docs/187）")
        return (head + f"走多语法前端:\n"
                f"{cmd}--out {path.with_suffix('.iface.lomt').name}\n"
                f"      （它把外源语法转成 L1 接口单元, 再由 Loment 编译器编。"
                f"见 docs/179）")
    return None


def diagnose(errs: list[str]) -> list[dict]:
    """裸消息 -> 一条带说明卡的诊断。

    **说明书那一半也从这里出**（`what/why/fixes/yes/no`）—— 报错器渲染的就是它们，而
    `--json` 的消费者拿到的与报错器看到的是**同一份**内容。码不在卡表里时那五项缺席，
    由调用方走"未知码"那条（**不静默**：什么都不给，看起来像"这条没有更多可说的"）。
    """
    out = []
    for e in errs:
        code, title, hint = classify(e)
        card = CARDS.get(code_num(code))
        d = {"code": code, "title": title, "message": e, "hint": hint}
        if card:
            d.update({"what": card.what, "why": card.why, "fixes": list(card.fixes),
                      "yes": card.yes, "no": card.no})
        out.append(d)
    return out


def _lom_str(s: str) -> str:
    """把一段文字放进 Loment 字符串字面量。**必须转义** —— 标题/建议里既有 `"`（E005 的
    `excluded "<space>: ..."`）也有 `\\`，不转义就会把生成的文件切坏。"""
    out = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return '"' + out + '"'


def surface_lomt() -> str:
    """把码表导出成自举侧/报错器能 `use` 的**纯数据**单元（`docs/176` B，`docs/182` §5.2）。

    形状照抄 `win_shim_data`：一个 `module`，按**码**索引的访问器。**不做 str_concat** ——
    运行时的 bump 堆只有 64 KiB，拼接会撞顶；这里每个访问器各自返回一个字面量，调用方逐条取。

    **两个受众**（`docs/182` §5.3）:

      * 中文那一组（`code_title` / `code_what` / `code_why` / `code_fix` / `code_nfix` /
        `code_yes` / `code_no`）给**诊断**看 —— stderr，936 控制台下本就是乱码，
        与参考实现现状一致；
      * `code_ascii` —— 一行 ASCII，给 **CLI** 看（`docs/169` 要求命令输出纯 ASCII）。

    另外一整套 `lang_*` 是**语言卡**（与六语言翻译线衔接，`docs/182` §11.2）：键集 ==
    `potato_from.LANGS`，由判据钉着。

    未知码一律返回空串：调用方据此走"未知码"分支，而不是拿到一个看起来正常的默认值。
    `code_fix` 用**平键 `c * 8 + i`**（`FIX_STRIDE = 8`）—— 按码嵌 `if` 要嵌两层，
    平键一层就够，而且生成器这边一眼看得出哪条码少了哪一档。
    """
    kinds = (("code_title", lambda c: dict(RULES_BY_CODE).get(c, ("", "", ""))[1]),
              ("code_what", lambda c: CARDS[c].what if c in CARDS else ""),
              ("code_why", lambda c: CARDS[c].why if c in CARDS else ""),
              ("code_yes", lambda c: CARDS[c].yes if c in CARDS else ""),
              ("code_no", lambda c: CARDS[c].no if c in CARDS else ""),
              ("code_ascii", lambda c: ASCII_ONE_LINER.get(c, "")))
    codes = sorted(dict(RULES_BY_CODE))
    src = [
        "// surface_data.lomt — 由 `tools/loment_diag.py --dump-surface` 生成，别手改。",
        "//",
        "// 码 -> 文字 + 语言卡。**唯一真源是 `tools/loment_diag.py`**（RULES / CARDS /",
        "// ASCII_ONE_LINER / LANG_CARDS），这里只是它的可读副本（`docs/176` B 那条管线：",
        "// 数据从逻辑里拆出来，自举侧 `use` 它）。",
        "//",
        "// 为什么要生成而不是手抄第二份：`loment/tools/lomcli.lomt` 原先手抄了 24 条 `codrow`，",
        "// 与 `loment_diag.RULES` 是同一件事的两份 —— 而本仓那四个静默 bug（docs/179 7.3）",
        '// 全出自"同一份清单抄第二遍"。',
        "//",
        "// 中文与 ASCII **不是重复**：CLI 输出必须纯 ASCII（docs/169，936 控制台下 UTF-8 中文",
        "// 被按 GBK 解成乱码，PE 垫片没有 WriteConsoleW），诊断那侧本来就是中文。两个受众。",
        "//",
        "// `code_fix` 用的是**平键 `c * 8 + i`**（一条码最多 8 条修法）：按码嵌 `if` 要嵌",
        "// 两层，平键一层就够，而且生成器这边一眼看得出哪条码少了哪一档。",
        "// **码从 1 起连续编号**, 所以 `n_codes()` 也就是最后那个码 —— 两个消费者（`lomcli`",
        "// 的 codes/explain、报错器）都拿它当上界用, 那条不变式有判据钉着。",
        "",
        "module surface_data",
        "",
        f"pub fn n_codes() -> u32 {{ return {len(codes)}; }}",
        "",
    ]
    for fn, get in kinds:
        src.append(f"pub fn {fn}(c: u32) -> str {{")
        for c in codes:
            val = get(c)
            if val:
                src.append(f"    if c == {c} {{ return {_lom_str(val)}; }}")
        src += ['    return "";', "}", ""]

    # 修法: 平键 c*8+i
    src += ["const FIX_STRIDE: u32 = 8;", "",
            "/// 第 `i` 条修法（`i` 从 0 起）。`i` 超出该码的条数就是空串。",
            "pub fn code_fix(c: u32, i: u32) -> str {",
            "    let k: u32 = c * FIX_STRIDE + i;"]
    for c in codes:
        fixes = CARDS[c].fixes if c in CARDS else ()
        for i, fx in enumerate(fixes):
            src.append(f"    if k == {c * 8 + i} {{ return {_lom_str(fx)}; }}")
    src += ['    return "";', "}", "",
            "/// 这个码有几条修法（0 = 表里没有这个码）。",
            "pub fn code_nfix(c: u32) -> u32 {"]
    for c in codes:
        n = len(CARDS[c].fixes) if c in CARDS else 0
        src.append(f"    if c == {c} {{ return {n}; }}")
    src += ["    return 0;", "}", ""]

    # 语法声明的**别名表**（`choose write grammar <别名>`）—— 报错器拿它把文件头那一行
    # 翻成语言。**从 `potato_from` 推导，不另抄一份**：别名是"作者写哪个词"，而它到语言的
    # 映射由**出厂锁**定（`docs/188` §1.1，不可扩展、不可覆盖）。
    #
    # **导不出来就炸，不给空表**：空表会让报错器在"文件头声明了语法"时沉默 —— 那正是
    # 这一步要治的病（实测：一份 `choose write grammar python` 的 `.lomt`，工具链知道
    # 是 Python，而报错器因为内容里没有 Python 特征词而一个字都不说）。
    import potato_from
    aliases = sorted(potato_from.GRAMMAR_ALIASES.items())

    # 语言卡（与六语言翻译线衔接的那一半，docs/188 §7.1）
    # **按 `LANG_ORDER` 排，不按字典序** —— 逐门问、取第一个命中，所以顺序就是优先级。
    _ord = {k: i for i, k in enumerate(LANG_ORDER)}
    langs = sorted(LANG_CARDS, key=lambda k: (_ord.get(k, len(_ord)), k))
    src += ["// ---------------------------------------------------------------- 语言卡",
            "// **键集 == `potato_from.LANGS`**（判据钉着）：翻译线加一门语言而这里没跟上,",
            '// 症状是"新语言的文件报错时只字不提怎么翻" —— 静默缺口。',
            "",
            f"pub fn lang_count() -> u32 {{ return {len(langs)}; }}",
            ""]
    src += [f"pub fn alias_count() -> u32 {{ return {len(aliases)}; }}", "",
            "/// 第 `i` 个语法别名（报错器按**小写**比 —— 作者写 `C#` 还是 `c#` 都该认）。",
            "pub fn alias_word(i: u32) -> str {"]
    for i, (word, _canon) in enumerate(aliases):
        src.append(f"    if i == {i} {{ return {_lom_str(word.lower())}; }}")
    src += ['    return "";', "}", "",
            "/// 该别名对应的语言（= `LANG_CARDS` 的键 / `potato_from.LANGS` 的键）。",
            "pub fn alias_lang(i: u32) -> str {"]
    for i, (_word, canon) in enumerate(aliases):
        src.append(f"    if i == {i} {{ return {_lom_str(canon)}; }}")
    src += ['    return "";', "}", ""]

    for fn, get in (("lang_key", lambda lk: lk.key),
                    ("lang_name", lambda lk: lk.display),
                    ("lang_exts", lambda lk: " ".join(lk.exts)),
                    ("lang_tokens", lambda lk: "|".join(lk.tokens)),
                    ("lang_edge", lambda lk: lk.edge),
                    ("lang_abi", lambda lk: lk.abi)):
        src.append(f"pub fn {fn}(i: u32) -> str {{")
        for i, key in enumerate(langs):
            src.append(f"    if i == {i} {{ return {_lom_str(get(LANG_CARDS[key]))}; }}")
        src += ['    return "";', "}", ""]
    return "\n".join(src)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_diag", description="诊断分类 + 修复建议")
    ap.add_argument("file", nargs="?")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--lom-root", default=None)
    ap.add_argument("--dump-surface", metavar="PATH",
                    help="把码表导出成 Loment 数据单元（给自举侧与报错器 use，docs/182 §5.2）")
    a = ap.parse_args(argv)
    if a.dump_surface:
        Path(a.dump_surface).parent.mkdir(parents=True, exist_ok=True)
        Path(a.dump_surface).write_text(surface_lomt(), encoding="utf-8", newline="\n")
        print(f"[OK] {a.dump_surface} ({len(ASCII_ONE_LINER)} 条码)")
        return 0
    if not a.file:
        ap.error("需要 FILE，或 --dump-surface PATH")
    root = Path(a.lom_root) if a.lom_root else ROOT
    p = Path(a.file)
    try:
        mod, deps = lomentc.load_unit(p, root)      # 唯一入口（docs/182 §1.10）
        errs = lomentc.check(mod, deps=deps)
    except lomentc.LomError as e:
        errs = [str(e)]
    diags = diagnose(errs)
    note = foreign_note(p, errs)
    if a.json:
        print(json.dumps({"diagnostics": diags, "foreign_note": note},
                         ensure_ascii=False, indent=2))
    else:
        for d in diags:
            print(f"{d['code']} [{d['title']}] {d['message']}")
            if "what" not in d:
                print(f"      建议: {d['hint']}")
                continue
            print(f"      错了什么: {d['what']}")
            print(f"      为什么错: {d['why']}")
            print("      怎么改:")
            for i, fx in enumerate(d["fixes"], 1):
                print(f"        {i}. {fx}")
            print(f"      支持:   {d['yes']}")
            print(f"      不支持: {d['no']}")
        if note:
            print()
            print("  ⚠ " + note)
        if not diags:
            print("[OK] 无错误")
    return 1 if diags else 0


if __name__ == "__main__":
    sys.exit(main())
