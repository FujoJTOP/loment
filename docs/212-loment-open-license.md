# 212 · Loment-Open：为什么允许"拿 Loment 做一门别的语言"

> 状态: 决定（2026-10-09，用户裁三项）· 落点: 根 `LICENSE` 与四个登记处 · 输入: 用户口述诉求
>
> 一句话: 把 MIT 原文**逐字**留下来，在它后面加两条**分隔的**附加条款 —— 一条要署名基座，
> 一条把"后缀/语法自由"写死成授权、把"名称"保留下来。**代价写在 §2，别绕过它。**

## 0. 诉求，以及一个反直觉的事实

诉求（2026-10-09，用户）：**基于 Loment 源码开发全新的语言、可以不用 `.lomt` / `.lom` 后缀；
但要在允许范围内规范地写上"基于 Loment 开发"。**

反直觉的事实先说清楚：**MIT 本来就允许这件事。** MIT 的授权是
"use, copy, modify, merge, publish, distribute, sublicense, and/or sell … **without
restriction**"（根 `LICENSE` §1）。它从不限制文件后缀、语法、语言名，也从不限制"照它做一门新语言"。
所以这一版**不是把 MIT 放宽** —— 关于后缀的那句只能是**澄清**（把默许写成明文）。

反过来，用户要的"务必写上"如果超出 MIT 已有的"保留版权与许可声明"，那就是**新增义务**、是**收紧**。
两股力方向相反，所以 §1 把 MIT 原文一字不动地留着，附加条款另起一节 —— 谁改了哪一半，一眼能分。

## 1. 改成了什么

**许可文本**（根 `LICENSE`）四节，其中两节是 MIT 逐字、两节是附加：

| 节 | 内容 | 来源 |
|---|---|---|
| 1 Grant | "Permission is hereby granted … subject to the following conditions:" + 通知段 | **MIT 逐字** |
| 2 Base attribution | 分发**派生源码**时保留一句 `Based on Loment (FujoOS Project) — <repo>` | 附加 |
| 3 Scope: suffixes, syntax, the name | 后缀/语法**自由**；不主张商标，只保留**名称** | 附加 |
| 4 Disclaimer | "THE SOFTWARE IS PROVIDED "AS IS" …" | **MIT 逐字** |

文本开头明写"**它不是 MIT 许可，也不得被当作 MIT 分发**" —— 这是**故意的**（用户 2026-10-09 裁：
"不再叫 MIT，这是故意的"）。所以 §2 的代价不是疏忽，是选择。

**四个登记处**（改 `<file>:<line>` 时对 HEAD 而言）：

* 根 `LICENSE` —— 正文。
* `README.md` 的 `## License` 段 —— 人第一眼看到的说法。
* `tools/loment_publish.py` 里**发布口 README 模板**的同名段 —— 公开仓 `FujoJTOP/loment`
  的 README 由这个工具直出（`tools/loment_publish.py:564`），**手敲那份不对**。
* `CONTRIBUTING.md` 的 `## Licence` 段 —— 贡献者读的那份。
* `editors/vscode/package.json` 的 `"license"` 字段 —— `"MIT"` -> `"Loment-Open"`（用户裁）。

## 2. 代价（读这一节，别只看 §1）

1. **它不是 MIT。** §1、§4 是 MIT 原文，但 §2、§3 是**条件**；两份文本不同，就不能互标。
2. **它不是 OSI 认证的开源许可。** OSI 按**文本**逐份认证，不接受自造变体；这一份没有提交、
   也不打算提交。于是它在严格意义上**不是** OSD 意义上的"开源许可"，只是一个源码公开的许可。
3. **它没有 SPDX 标识符。** `"license": "Loment-Open"` 在 npm / vsce 眼里是**未知值**，打包会告警。
   实测这台机器上 VS Code 扩展是**侧载 `.vsix`**（`tools/loment_dist.py` 的 dist 里带的是打完的包），
   不进 Marketplace —— 所以这条代价今天只落到告警上，不落到"上不了架"。
4. **对外说"Loment 是开源项目"时要留神措辞**：可以说"源码公开、许可宽松（MIT 加两条）"，
   但"OSI 开源许可"这个说法从这一天起不再准确。这条与 `docs/204` / 对外文案同属一类口子。

## 3. 不主张 / 不做

1. **不主张这有法律意见。** 这一页是**工程记录**：记的是改了哪几个字节、为什么、代价在哪。
   真要依赖它打官司，去找律师 —— 那不是我给的。
2. **不主张"基于 MIT"能免掉版权人的同意。** 既往贡献是按 MIT 收的（旧 `CONTRIBUTING.md` §Licence
   原文如此）。换出口许可严格说是版权人的处分权；本仓今天的版权人只有一个（FujoOS Project），
   所以改得动 —— 但**若将来有第三方贡献者**，这一条的边界要重新看。
3. **不主张商标。** §3 明确"不主张也不授予商标"，对名称的约束**只是名称**（用户 2026-10-09 裁：
   "没有商标，Loment 顶多约束名称"）。别把 §3 读成一条商标条款。
4. **没给 `lompi` 仓补 `LICENSE`。** `tools/loment_publish.py` 的 `lompi` 发布 spec（`:316`）只切
   `lompi/` 与 skill，**不切 `LICENSE`** —— 那个公开仓今天没有许可文件。这是**它原本就有的洞**，
   与这次改名无关；要补是另一笔。
5. **不改 `lom/`、不动判据。** 这次只碰文本与登记处；`lom/*.lom` 是跨线契约（`CLAUDE.md`），
   换许可不构成改它的理由。

## 4. 与其它文档的关系

* `CONTRIBUTING.md` §Licence —— 贡献者入口；这一页是它背后"为什么"的那份。
* `docs/201-doors-and-registries.md` —— "一个事实有几个登记处"的同一类问题；这次的登记处是 §1 那四处。
* `docs/171-loment-publish-outlets.md` —— 发布口，说明了公开仓 README 为什么是直出的。
* 根 `LICENSE` —— 正本；这一页只是它的说明，**改了文本这一页要跟着改**（§1 那张表）。
