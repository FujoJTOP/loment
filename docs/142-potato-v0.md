# 142 · Potato v0：形式对象与校验器

> **v1 已发布**（2026-09-09，docs/147）：泛型/切片/字符串/trait/审计站点纳入形式对象，
> 校验器接受 v0/v1 双版本，v0 对象仍可回放。本文件保留 v0 规范作为历史基线。
>
> 状态: **已实现并通过自检**（2026-09-08）· 生产者: `tools/lomentc.py --emit-potato`
> 校验器: `tools/potato.py` · 自检: `tools/lomentc_test.py` 18/18
> 一句话: **Agent 看系统的那一份形式对象——独立校验器仅凭它就能判定合法性，不读源码、不读二进制。**

## 1. 定位（与 Loment 的分工）

docs/140 §9 定调：**Loment 管"怎么写"，Potato 管"怎么看"**。
Potato 不是语言（docs/110 §1 的定位修订）：它没有语法，只有形式对象与校验规则。
Loment 编译器为每个编译单元**强制**产出一个 Potato 形式对象（v0 为 `--emit-potato`）。

> 决策变更：docs/110 §4 的"实现闭源"已被 2026-09-08 用户指示覆盖（docs/140 §10），
> 因此本实现与规范一并开源。

## 2. 形式对象 schema v0

```json
{
  "potato": "v0",
  "unit": "<标识符>",
  "language": "loment",
  "capabilities": [
    {"name": "blk_write",
     "domain": {"space": "disk", "lo": 0, "hi": 4},
     "revocable": true}
  ],
  "functions": [
    {"name": "fib", "params": [{"name": "n", "type": "u32"}], "ret": "u32"}
  ],
  "layouts": [
    {"name": "Header", "size": 64, "endian": "little", "packed": true,
     "fields": [{"name": "magic", "type": "u32", "offset": 0}]}
  ],
  "consts": [
    {"name": "MAX_BLKS", "type": "u32", "value": 8}
  ],
  "types": [
    {"name": "Blk", "fields": [{"name": "off", "type": "u32"}, {"name": "len", "type": "u32"}]}
  ],
  "excluded": ["network: 本单元不申请任何 net 能力"]
}
```

字段来源：

| 字段 | 来自 | 说明 |
|---|---|---|
| `unit` | `module <name>` | 编译单元名 |
| `imports` | `use "*.lomt"` | 导入的编译单元名（形式对象只描述本单元，不复制导入符号） |
| `capabilities` | `capability name : space[lo..hi] [revocable]` | 能力域与撤销语义 |
| `functions` | `fn` 签名 | 参数/返回类型（可引用 `types` 中声明的类型） |
| `layouts` | `use "*.lom"` 引入的 L0 记录 | 二进制布局（含偏移/大小/字节序） |
| `types` | L1 原生 `struct` | 语言级数据类型（无偏移，不是二进制布局） |
| `consts` | `const NAME: T = v;` | 整型常量 |
| `enums` | L1 `enum` | 枚举及其变体；带载荷变体另有可选 `payloads`（变体名 → 载荷类型） |
| `excluded` | `excluded "..."` | 明确排除的能力 |

## 3. 校验规则（`tools/potato.py`）

| 规则 | 判据 |
|---|---|
| 版本 | `potato == "v0"`；**未知顶层字段一律拒绝**（v0 不允许扩展） |
| 导入 | `imports`：每项为合法标识符 |
| 标识符 | `unit` / 能力名 / 函数名 / 参数名 / 布局名 / 字段名必须匹配 `[A-Za-z_][A-Za-z0-9_]*` |
| 唯一性 | 能力名、函数名、布局名、字段名、参数名不得重复 |
| 能力域 | `space` 为标识符；`lo`、`hi` 为整数且 `0 ≤ lo ≤ hi`；`revocable` 为布尔 |
| 函数签名 | `ret` 与参数 `type` ∈ {u8..u64, i8..i64, bool} |
| 布局 | `size > 0`；`endian ∈ {little,big}`；`packed` 为布尔；字段类型定宽；`offset ≥ 0` 且 `offset+width ≤ size`；字段不得重叠 |
| 原生类型 | `types`：名字唯一、不与基类型同名、字段非空且类型已声明（可引用其他已声明类型）；函数签名的参数/返回类型可引用它们 |
| 常量 | `consts`：名字唯一、类型为整型、值为整数 |
| 枚举 | `enums`：名字唯一、不与基类型同名；变体非空且唯一；可选 `payloads` 的键必须是已声明变体、类型必须已声明 |
| 出界 | 每项为非空字符串 |

**判据（docs/140 §9.4）**：给定一份形式对象，一个**独立**的校验器仅凭它即可判定能力域、
契约、布局是否合法 —— 无需读源码，无需读二进制。`potato.py` 不 import 任何 Loment 代码。

## 4. 使用

```
# 生产: .lomt -> 形式对象
python tools/lomentc.py loment/examples/demo.lomt --emit-potato loment/build/demo.potato.json

# 校验 / 查看
python tools/potato.py validate loment/build/demo.potato.json
python tools/potato.py show     loment/build/demo.potato.json
```

`show` 输出（实测）：

```
unit=demo lang=loment caps=1 fns=4 layouts=2
  cap  blk_write: disk[0..4] revocable
  fn   fib(n: u32) -> u32
  ...
  rec  Header size=64 fields=3
  rec  Section size=32 fields=4
```

## 5. 验收判据

- `potato.py validate` 接受 `demo.lomt` 产出的形式对象（实测 `[OK]`）。
- 校验器拒绝 5 类畸形对象（未知字段 / 域区间反转 / 字段重叠 / 越界 / 非法返回类型），
  见 `tools/lomentc_test.py` 的 `test_potato_rejects_*`。
- 形式对象中的 `layouts` 与 `lom/*.lom` 单源一致（同一份 L0 解析结果，不二次维护）。

## 6. 与 docs/110 的关系 / 后续

- docs/110 的三篇论文定位不变：Potato 仍是**论文三**的表示层；本 v0 是它的**接口草案**
  （docs/140 §9.6 建议的"提前冻结 v0 接口"），只含 schema + 校验器，**不做测量、不发论文**。
- 待做：波 C 的测量协议（结构识别转换率 / 校验通过率）、`excluded` 的出界语义、
  Potato 形式对象 → 内核 A1–A4 断言的绑定（docs/59 接口公理是候选语义层）。
