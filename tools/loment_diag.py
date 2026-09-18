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
             r"use 的 .* 不存在|导入的 .* 不存在|循环导入|有语义错误",
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
             r"开关 .* 写了两次|`choose` 有 .* 条, 超过上限",
     "`choose` 用法不对",
     "**核心模式**（`std`/`no_std`）声明的是**整个程序**的运行模式，所以只能出现一次、"
     "只能在**根单元**。**开关**（`set choose <名字> { … }` + `choose <名字>` / "
     "`choose close <名字>`）可以有很多（上限见 `MAX_CHOOSE`），但**同名只许写一次**，"
     "而且取值前要先用 `set choose` 定义。库不许 `choose` —— 库要表达需要就**声明能力需求**"
     "（`docs/168`），由项目决定。见 `docs/143` §3.2 与 `docs/182` §1。"),
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
        return (f"这个文件**不是 Loment 语法**, 看内容是 **{lang.upper()}**（{why}）。"
                f"别照上面那条改 —— 它的语法本来就是对的。走多语法前端:\n"
                f"       python tools/lomt_from.py \"{path}\" --lang auto --out "
                f"{path.with_suffix('.iface.lomt').name}\n"
                f"      （它把外源语法转成 L1 接口单元, 再由 Loment 编译器编。"
                f"见 docs/179）")
    return None


def diagnose(errs: list[str]) -> list[dict]:
    out = []
    for e in errs:
        code, title, hint = classify(e)
        out.append({"code": code, "title": title, "message": e, "hint": hint})
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

    三条访问器，对应**两个受众**（`docs/182` §5.3）:

      * `code_title` / `code_hint` —— 中文，给**诊断**看（stderr，936 控制台下本就是乱码，
        与参考实现现状一致）；
      * `code_ascii` —— 一行 ASCII，给 **CLI** 看（`docs/169` 要求命令输出纯 ASCII）。

    未知码一律返回空串：调用方据此走"未知码"分支，而不是拿到一个看起来正常的默认值。
    """
    kinds = (("code_title", lambda c: dict(RULES_BY_CODE).get(c, ("", "", ""))[1]),
             ("code_hint", lambda c: dict(RULES_BY_CODE).get(c, ("", "", ""))[2]),
             ("code_ascii", lambda c: ASCII_ONE_LINER.get(c, "")))
    codes = sorted(dict(RULES_BY_CODE))
    src = [
        "// surface_data.lomt — 由 `tools/loment_diag.py --dump-surface` 生成，别手改。",
        "//",
        "// 码 -> 文字。**唯一真源是 `tools/loment_diag.py` 的 RULES + ASCII_ONE_LINER**，",
        "// 这里只是它的可读副本（`docs/176` B 那条管线：数据从逻辑里拆出来，自举侧 use 它）。",
        "//",
        "// 为什么要生成而不是手抄第二份：`loment/tools/lomcli.lomt` 原先手抄了 24 条 `codrow`，",
        "// 与 `loment_diag.RULES` 是同一件事的两份 —— 而本仓那四个静默 bug（docs/179 §7.3）",
        '// 全出自"同一份清单抄第二遍"。',
        "//",
        "// 中文与 ASCII **不是重复**：CLI 输出必须纯 ASCII（docs/169，936 控制台下 UTF-8 中文",
        "// 被按 GBK 解成乱码，PE 垫片没有 WriteConsoleW），诊断那侧本来就是中文。两个受众。",
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
        mod = lomentc.load(p)
        deps = lomentc.resolve_deps(mod, root, p.parent, entry=p)
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
            print(f"      建议: {d['hint']}")
        if note:
            print()
            print("  ⚠ " + note)
        if not diags:
            print("[OK] 无错误")
    return 1 if diags else 0


if __name__ == "__main__":
    sys.exit(main())
