# Translation glossary and style guide

Companion to the `.en.md` sibling of every `docs/*.md`. It is a translator-facing artifact, not a document:
it gets no English sibling of its own, and it sits outside every release GLOB (`docs/*.md` is a single-level
glob and does not recurse), so it never enters `loment/build/release-manifest.json` and is not shipped.

## 1. Naming and placement

- `docs/NNN-slug.en.md`, next to `docs/NNN-slug.md`. **Keep the number and the slug unchanged.**
  The number prefix keeps ~1700 references of the form `docs/NNN` / `docs/NNN §N` valid for free;
  keeping the slug too keeps `tools/loment_src.py`'s by-name glob `docs/*loment*` matching.
- Generated trees follow the same shape: `docs/manual/index.en.md`, `docs/manual/api/<stem>.en.md`,
  `docs/154-loment-status.en.md`. Those come out of the generators, never from hand-editing.

## 2. Header stamp (machine-read)

Every English file opens with exactly these two lines, before the H1:

```
<!-- translated-from: docs/143-l1-loment-v0.md -->
<!-- source-sha256: 9c5bf23e30de0dbc616da1e831b94e964497f805b8c35afcd09e2273d41b902c -->
```

`source-sha256` is the SHA-256 of the **Chinese file as it stands today**. When the Chinese file changes the
stamp goes stale, and `tools/loment_i18n_test.py` goes red until that file is re-translated and re-stamped.
This is the same discipline as the release manifest and the bootstrap seed: a derived copy is only trustworthy
if something notices when its source moves.

## 3. Never translate

| | |
|---|---|
| Code, identifiers, keywords | `guard`, `capability`, `revocable`, `excluded`, `choose`, `comefor`, `addin`, `byuse`, `ptr`, `str`, `fn` … |
| Paths and filenames | `tools/lomentc.py`, `loment/selfhost/checker.lomt`, `docs/189` |
| Commands and their output | `loment build`, `[ok]`, `[--]`, `MISSING`, `->`, `%argc.addr = alloca i32` |
| Bookkeeping ids | `M<n>`, `P<n>`, `S<n>`, `E002`, `§N`, `0x8313`, `33556` |
| Code fence language tags | keep the tag byte-for-byte (`bash`, `rust`, `python`, `json` …). Loment code is shown with a ` ```rust ` fence — see `CLAUDE.md` |

**Quoted output and diagnostics stay verbatim.** The compiler's guard message really is Chinese in the
implementation (`tools/lomentc.py:3102`: `能力 X 域 [lo..hi]，索引 idx 越界`), so the English page shows it
in Chinese and adds a bracketed gloss. It is evidence of what the tool prints, not prose to be translated.

## 4. Shapes that must survive

- H1: `# NNN · <Title in English>` — keep the number and the `·`.
- Header blockquote: `> Status: … · Implementation: … · In one line: …` (from `> 状态: … · 实现: … · 一句话: …`).
- Code fence tags, table structure, list numbering, `§` cross-references.
- **`docs/145-loment-100-milestones.md` is machine-parsed — but only its Chinese original.** `tools/loment_status.py`
  reads rows with a `^\|\s*(M\d+)\s*\|…` regex, asserts there are exactly 100 of them, and classifies on the
  **status cell prefix** (`✅ 部分` / `✅` / `⚠️` / `待做` / `—`). Nothing reads the English sibling, so its `M<n>`
  first column stays verbatim (cross-references elsewhere are written `M<n>`) while the status words translate:
  `✅ 部分` → `✅ partial`, `待做` → `to do`; `✅` `⚠️` `—` are unchanged. The same rendering applies to milestone
  tables in other docs (e.g. `docs/149`).
- Line endings: LF only — `.gitattributes` marks `*.md text eol=lf` and `tools/loment_eol.py` reds on CRLF.

## 5. Voice

The Chinese is blunt, aphoristic and unsentimental: `一句话` headlines are asserted rather than hedged,
sections carry names like `诚实边界` and `还没做的`, and it says plainly when a past decision was wrong
(`那个取舍是错的`). Keep that. Do not flatten it into neutral technical prose, and do not add hedging the
original does not have. Where English cannot carry the same compression, prefer the shorter and more
direct phrasing over the more complete one.

## 6. Terms

| 中文 | English | Note |
|---|---|---|
| 判据 | criterion (pl. criteria) | The repo's central word: an *executable* check bound to a claim. The files are `tools/*_test.py` |
| 门禁 / 静态门禁 | gate / static gate | |
| 能力域 | capability domain | The language's one added layer |
| guard | guard | keyword, never translated |
| 越界 | out of range | |
| 域宽 | domain width | |
| 撤销 | revocation | |
| 主体 | principal | |
| 准入闸 | admission gate | |
| 台账 | ledger | |
| 内容信任 | content trust | |
| 真源 / 单一真源 | source of truth / single source of truth | |
| 生成物 | generated artifact | |
| 自举 / 自举种子 / 自举镜 | self-hosting / bootstrap seed / self-hosted mirror | |
| 参考实现 | reference implementation | |
| 逐字节相同 | byte-identical | |
| 漂移 | drift | |
| 静默（地） | silent(ly) | as in "fails silently" |
| 手抄 | hand-copied | the repo's named anti-pattern |
| 诚实边界 / 诚实清单 | honest boundaries / honest list | of what is *not* done |
| 取舍 | trade-off | |
| 硬边界 / 硬约束 | hard boundary / hard constraint | |
| 观感 | look and feel | |
| 命令面 | command surface | |
| 启动器 / 垫片 | launcher / shim | |
| 兜底 | fallback | |
| 护栏 | guardrail | |
| 同口径 | measured the same way | |
| 口径 | convention; the same measure | multi-sense — as in "E013's duplicate-name convention" |
| 门面 | facade | as in "the std facade" (`docs/143` §6) |
| 待定 | undecided | distinct from 待做 → `to do` |
| 出界 | excluded / outside the domain | the `excluded` keyword itself is never translated |
| 契约 / 跨线契约 | contract / cross-line contract | |
| 开发线 | development line | |
| 内建（函数） | builtin | |
| 表层语法 | surface syntax | |
| 里程碑 | milestone | |
| 冻结阈值 | freeze threshold | |
| 落出（函数末尾） | fall off the end (of a function) | |
| 遮蔽 | shadow | |
| 就地 | in place | |
| 白拿 / 顺带 | for free / as a side effect | |
| 不装样子 | no pretending | |
| 一句话 | In one line | the header-blockquote label |
