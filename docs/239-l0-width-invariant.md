# 239 · L0：把 `dom_admit` 那半句写实（`width_invariant`）

> 状态：**待 admin 合**（2026-10-13）。改的是 `lom/fuai.lom` —— 跨线契约，`lom/` 那道禁区
> 会拦下它，按 `CLAUDE.md` 第一节这属于**对外契约变更**：单独一次提交、在 `docs/` 留
> "下游影响"、知会 compat 线。
>
> 一句话：**`dom_admit` 的语义从"含糊的那半句"变成一条两实现不变量**，而这一条的内容
> 是 `docs/238` 那件已经落地、两个实现逐字节相同的判定。

## 1. 改了什么（只有一段文本）

`lom/fuai.lom` 的 `meta.gates` 里，`dom_admit_qual_feed` 那句原文是：

```
"core primitives 0x8313/0x8314 carry a trust-adaptive authority gate
 (FujoOS: qual_test flag -> -12; LinuxFUAI: gate semantics for deployment)"
```

**FujoOS 那半是具体的（`qual_test` 没开就 `-12`），Linux 那半是"gate semantics for
deployment"** —— `docs/218` §3 点名过这句：*"L0 现在只写着一句 'LinuxFUAI: gate semantics
for deployment' —— 语义是含糊的"*。现在把它写实，并**照 `tau_invariant` 的形状**加一条
两实现不变量（仓里既有的那份先例就在它下面一行）：

```
width_invariant = "dom_admit = f(self_hash, peer_hash, qual_seq) -> width: earned from the
 duty ledger (per-peer rolling 64 hits), capped by what the artifact asks (its Potato
 net_ladder, docs/237); WIDEN_CONFIRM needs 3 consecutive seqs, shrink immediate; seq is a
 sequence number NOT a clock, so no time-based policy exists (docs/238, docs/239)"
```

**没有动别的**：原语的号、签名、`layer`、`semantics` 一个字没改 —— `sdk/fuai-spec/spec.json`
里 `primitives` 仍是 **46 个**，`loment/tools/lomsyscalls.lomt` 与 `loment/build/fuai_syscalls.lomt`
对账照旧一致（实测 `loment_syscalls.py --check`：46 个原语、内核 dispatch 覆盖 46/46）。

## 2. 为什么是现在

顺序不能反：**先有"要写实成什么"，再动那句话**。今天那个"什么"存在了 ——

* `docs/237`：宽度**组成**可判（Potato v14 `net_ladder`，PR #278 已入 main）；
* `docs/238`：判定本身是**纯函数**，两个实现（`tools/loment_admit.py` ↔
  `loment/tools/lomadmit.lomt`）**逐字节相同**，输入闭集（8 个字段就是错 ⇒ 结构上没有时钟）。

也就是说，这一条不变量**不是承诺**，它是**已经在跑的判据的复述**。这正是
`tau_invariant` 那条的成色：不是"我们打算这样"，是"两个实现都这么算，而且有一条判据
盯着"。

## 3. 下游影响（`CLAUDE.md` 第一节要求的这一行）

* **谁依赖它**：`FujoOS-compat` 内核侧已经依赖本契约 —— `kernel/src/syscall.rs:397-399`
  把 `0x8313/0x8314/0x8315` 派发给 `crate::ai::fujo_dom_admit` 一族（前后带
  `qual_test_enabled()` 开关，未开一律 `-12`）；`LinuxFUAI/include/fuai.h` 与
  `LinuxFUAI/spec/spec.json` 是同一个契约在那一侧的落点。
* **变了什么**：**只有 `meta.gates` 的文本**。`enum Opcode` 的变体数、每个原语的
  `opcode`/`sig`/`layer`/`semantics` **全部不变** —— 所以"内核侧针对枚举计数的断言"
  （`lomc_test` 的 `Opcode` 变体数那种）**不受影响**，不会触发假失败。
* **下游要做什么**：**代码不用动**。要做的是两件记账的事 ——
  ① 在 `LinuxFUAI` 那边重跑 `python tools/lom_spec_emit.py`，让那份 `spec.json` 与本契约
  对齐（本仓那份 `sdk/fuai-spec/spec.json` 已经重出）；
  ② 如果那一侧有"部署用的准入判定"，它现在**有一条写明的语义要满足**了 —— 判定输入是
  三个数、**没有时钟**、收窄即时、放宽要连续三个序号。**在那之前它是"随便怎么算都行"。**
* **本机验不了的两件事**（不是这次改出来的，是环境）：`LinuxFUAI/` 是对方的独立仓库，
  本机没有；`tools/fuai_contract_check.py` 因此**固有红**（读不到 `LinuxFUAI/include/fuai.h`）。
  `lom_spec_emit.py --check` 也会报那一份"缺失"。

## 4. 这一篇**不是**什么

* **不是加原语**：`dom_admit` 早就在契约里（`lom/fuai.lom:329`），这里只是把它的语义写清。
* **不是设闸**：`docs/218` §12 那条前提（执行者本身可改）没变；写实的这条不变量描述的是
  "判定怎么算"，不是"谁被拦住"。
* **不是把 `docs/237`/`docs/238` 的判据搬进 L0**：契约说的是**语义**，判据仍在仓里那两处 ——
  契约不指认实现（`docs/165` 那条分界）。
