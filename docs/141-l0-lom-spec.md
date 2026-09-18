# 141 · L0 接口层：`.lom` 语言规范 v0

> 状态: **已实现并通过自检**（2026-09-08）· 实现: `tools/lomc.py` · 审计: `tools/lom_audit.py`
> 单源: `lom/fuai.lom`（46 原语）· `lom/fuc.lom`（Header 48B / Node 64B）
> 一句话: **一处声明，多处生成；漂移在生成期不可能，而不是在运行期才被发现。**

## 1. 定位与不变量

L0 是 docs/140 三层架构的最底层：**接口层**。它回答"一个接口事实写在哪里"，
不回答"内核怎么写成程序"（那是 L1 / Loment）。

四条不变量：

1. **单一真源**：`.lom` 是权威，生成物禁止手改。
2. **原生语法取已知语言的风味**：表面语法取 Rust 风味（`record` / `enum` / `const`），
   不发明新语法 —— 直接规避 docs/110 §1 的"零语料"否决（docs/140 §4）。
   （2026-09-18 更正：**不再声称"严格子集"** —— 表层语法已不止一种，见 `docs/188`。
   买到的从来是"重叠面"上的先验，不是"包含"。）
3. **生成期拦截**：重叠 / 越界 / 重复 / 同值在编译时报错，不留到运行期。
4. **输出确定性**：无时间戳、声明序稳定、LF 行尾 —— 因此可以逐字节 `--check`。

与上层的关系：L1（Loment）的声明语法是 `.lom` 的超集；Potato 形式对象是 `.lom`
语义模型的序列化形态（docs/140 §9.4）。L0 是两者的共同前身。

## 2. 语法

```
module <ident>

meta {                         // 文档级元数据: 字符串/整数, 可嵌套组
  <key> = <string | int>
  <key> { ... }
  "<key-with-dash>" = <string>  // 键可用字符串字面量
}

const <NAME> : <int-type> = <int>

param <name> {                 // 部署参数: 同一参数在不同实现上的取值
  <impl> = <int>
  note = <string>              // 可选: 参数说明
}

enum <Name> : <int-type> {
  <VARIANT> = <int> {
    <key> = <string | int>     // 元数据: sig / fujo / linux / layer / semantics / …
    ...
  }
  ...
}

record <Name> layout(packed, size=<int>, endian=little|big) {
  <field> : <int-type> @ <offset>
  ...
}
```

- 整型: `u8 u16 u32 u64 i8 i16 i32 i64`；字面量支持十进制与 `0x` 十六进制，可带前导 `-`。
- 注释: `//` 与 `/* */`；项之间逗号 / 分号可选。
- `record` 必须给 `size=`；`packed` 表示不做隐式对齐（空洞需显式留白，生成器用 `x` 填充）。

## 3. 语义规则（全部在生成期强制）

| 规则 | 判据 |
|---|---|
| 顶层名字唯一 | `const` / `record` / `enum` / `param` 共用一个命名空间 |
| 枚举成员名唯一 | 同名即错 |
| 枚举值唯一 | 同值即错（FUAI 操作码必须可反查） |
| 枚举值在基类型范围内 | `u8` 里写 256 即错 |
| 记录字段名唯一 | — |
| 字段不重叠 | 按偏移排序后逐对检查 |
| 字段不越界 | `offset + width ≤ size` |
| 常量在类型范围内 | — |
| 记录必须铺满声明大小 | 生成器用 `struct.calcsize` 反证：算得大小必须等于 `size` |

## 4. 生成契约

| 目标 | 产物 | 用途 |
|---|---|---|
| `--emit-rust` | `pub const` 常量与偏移 | 内核 |
| `--emit-c` | `#define` + 类型化字面量 | LinuxFUAI / 宿主工具 |
| `--emit-python` | 常量 + `struct.Struct` 格式串 | `tools/*.py` 打包/解包 |
| `--emit-json` | 语义模型（含元数据） | 审计 / Potato 形式对象前身 |
| `--check` | 不写盘，只对账 | CI 门禁：有漂移退出 1 |

`record` 的 Python 格式串由偏移自动拼装（空档填 `x`），因此**布局被完整捕获**才算通过：
`lom/fuc.lom` 生成的 `NODE_FMT = '<HHHHhhHHHHHHHHHHHHHHIIIHHII'` 与
`tools/fuic.py:416` 手写字面串逐字符相同（自检 `test_generated_fuc_fmt_matches_fuic` 守住）。

## 5. 落地现状（2026-09-08）

| 项 | 结果 |
|---|---|
| `lom/fuai.lom` | 46 原语 + 2 部署参数，迁移自 `sdk/fuai-spec/spec.json` |
| `lom/fuc.lom` | Header 48B / Node 64B，取代 fuic.py / fuc.rs / docs/138 三处声明 |
| 生成物 | `lom/build/{fuai,fuc}.{rs,h,py,json}` |
| **接管真源（fuc）** | `tools/fuic.py` 打包器改用 `lom/build/fuc.py` 的 `NODE_STRUCT`；`kernel/src/fui/fuc.rs` 改为 `pub use super::fuc_gen::*`，常量来自 `kernel/src/fui/fuc_gen.rs` |
| **权威翻转（fuai）** | `sdk/fuai-spec/spec.json` 与 `LinuxFUAI/spec/spec.json` 改由 `tools/lom_spec_emit.py` 从 `lom/fuai.lom` 生成（两份副本逐字节一致，9790 B） |
| 等价性证据 | `fuic.py --check` → `desktop.fuc` **逐字节一致**（12542 B）；`spec.json` 生成前后 **JSON 语义等价**（差异仅两处格式：手写文件的行尾空格、短 note 未换行）；内核 `cargo build --release` 通过；`fujoregress --only 0` PASS |
| 自检 | `python tools/lomc_test.py` → **19/19**（含 6 类语义负例、meta 解析、spec 生成一致性、确定性、漂移检出） |
| 审计 | `python tools/lom_audit.py` → **0 差异**（含"生成物/打包器不得退回手写"的接线检查） |
| CI 门禁 | `python tools/ci.py --static-only` → **3/3**（lomc_test / lom_audit / fuai_contract） |

**审计首次运行抓到的真实缺陷**：`kernel/src/capability.rs:68` 注释写"τ_high 46"，
而同文件代码与 `spec.json` 的富士侧取值都是 **45**（46 是 LinuxFUAI 的部署值）。
已修正为 45。这正是 L0 要消灭的那一类"陈旧数值"。

## 6. 验收判据

当前（v0）：
- `lomc_test` 15/15；`lom_audit` 0 差异；`ci.py --static-only` 3/3。
- 生成物与既有实现逐字面一致（fuc 布局、fuc.rs 常量）。

下一里程碑（v2，覆盖面）：
- ⬜ `ui/fui_spec.json`（FUI 词汇表）→ `lom/fui.lom`，`fuic.py --emit-rust` 改由生成物提供；
- ⬜ FUJR 容器格式（当前 5 处实现）→ `lom/fujr.lom`；
- ⬜ `sdk/contracts/*.json` 谓词 → `lom/contracts.lom`；
- ⬜ `sdk/rulebook/fidelity.csv` → `lom/rulebook.lom`；
- 覆盖完成后 `tools/fuai_contract_check.py` 可退役（`lom_audit.py` 是其超集）。

## 7. 路线

| 阶段 | 内容 | 状态 |
|---|---|---|
| v0 | 编译器 + 2 schema + 审计 + CI 门禁 | ✅ |
| v1 | 生成物接管真源（fuc 打包器/内核常量 + fuai spec.json 权威翻转） | ✅ |
| v2 | FUJR 容器（5 处实现 → 1 处单源，字节往返证明） | ✅ |
| v2 | `ui/fui_spec.json`、contracts 谓词、rulebook | 待做（这三者本就已是单源+生成，优先级低） |
| v3 | `.lom` 成为 Potato v0 形式对象的序列化基座 | ✅ 见 docs/142 |

## 8. 追加落地（2026-09-08 第二轮）

| 项 | 结果 |
|---|---|
| `lom/fujr.lom` | Header 64B / Section 32B / Tag 枚举；生成 `lom/build/fujr.{rs,h,py,json}` |
| 接管真源（fujr） | `tools/fujopack.py` 的 pack/info 改用生成结构；`"FUJR"` 魔数与偏移不再手写 |
| 等价性证据 | 用生成结构重打包 `sdk/build/m31_res.run` → **16428 B 逐字节一致**；`fujopack.pack()` 往返亦一致 |
| 通用生成物门禁 | `lom_audit.py` 新增 `GENERATED` 登记表：13 个生成物逐一与 `.lom` 生成结果对账 |
| L1 接入 | `lom_audit.py` 同时校验 `loment/build/*` 与 `.lomt` 转译结果一致（docs/143） |
