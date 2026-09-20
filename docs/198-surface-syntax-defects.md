# 198 · 表层语法：四条实测缺陷（从 LumtUI 那轮报上来）

> **这是从**`docs/196`（LumtUI，GUI 库那一条线）**报上来的**，不是语言线自己发现的。
> 写那份库的时候撞到四条**与"子集小不小"无关**的缺陷 —— 即：不是因为"这门拼法能表达的
> 东西少"，而是**该报的没报 / 报错的地方不对 / 同一个问题给两个答案**。
>
> 每一条都有**最小复现**，都在本机实测过；`docs/196` §4 是它们的原始记录（那一节还
> 有"Python 写法今天表达不了 LumtUI"那份**可达面**的实测，那份**不是缺陷报告**，
> 是边界说明 —— 四条缺陷与它是两件事）。
>
> **本文件只上报，没有动任何一份语言线的代码** —— 其中三条的修法都会碰到翻译器与
> 检查器（`tools/pytrans.py` / `tools/potato_from.py` / `tools/lomentc.py`），而那几份
> 正在被并行改动（`loment/tools/lomtrans.lomt`、`tools/nltrans.py` 都在半道上），
> 从外面动它们只会撞车。**归属与优先级由语言线定。**

---

## 1. `~` 能过翻译器、过不了词法器（**按 bug 报**）

`tools/pytrans.py` 的 `_BIN`/`raw()` 把 `ast.Invert` 直接映射成 `"~"`：

```python
# tools/pytrans.py:268 附近
op = {ast.USub: "-", ast.UAdd: "", ast.Invert: "~"}[type(e.op)]
```

而 **Loment 的词法器没有 `~` 这个字符** —— 一元运算符只有 `-` 与 `!`
（`docs/143` §6）。最小复现：

```rust
choose write grammar python

def f(a: int) -> int:
    return ~a
```

```
$ python tools/pytrans.py f.lomt          # 翻译器: 成功, 发出 ( ~a )
$ lomc.LomError: 7:13: 非法字符 '~'        # 词法器: 炸
```

**为什么这是缺陷而不只是"不支持"**：这一门是"全有或全无"
（`lomentc.load` 的前门要么整份翻出来、要么拒，见 `docs/188` §2 末），而它现在
**发出了一份编不过的源**。用户看到的是词法错、指向一个他根本没写的字符。

**修法两条，选一条即可**：

* **转**：按宽度展开成异或（这是 `loment check` 对原生写法给的出路）——
  i32 写 `a ^ -1`，u8 写 `a ^ 255`。宽度从注解拿得到（`int` 就是 i64）。
* **拒**：在 `_UNARY` 那一格直接 `Unsupported("Loment 没有按位取反；按宽度写 x ^ -1")`。
  与 `**` / `/` 两处同一个形状（它们就是拒的）。

**建议选后者**：与前两处一致，而且不会悄悄替作者决定宽度。
**顺带**：`tools/pytrans.py` 的文件头"子集"那一节把 `~` 列在**表达式**里
（`一元 - + ~ not`）—— 改完记得把那一行也改掉，否则文档继续声称支持。

---

## 2. `choose write grammar` 关键词写错**静默退回**（`docs/188` §1 那四种报错漏了一种）

`potato_from.read_grammar_decl` 认的是**三个词**：`choose` + `write` + `grammar`
（`GRAMMAR_DECL_WORDS`，`tools/potato_from.py:1454`）。实测这一张表（**不是推的**）：

| 文件头写的 | 解析出什么 |
|---|---|
| `choose write grammar python` | `python` ✓ |
| `choose write grammar py` | `python` ✓（别名）|
| `choose language python` | **`loment`**（`declared=False`）|
| `choose grammar python` | **`loment`** |
| `grammar python` | **`loment`** |
| `choose write grammars python` | **`loment`** |

后四种**一声不响**。文件于是被当 Loment 读，用户看到的是
`未知顶层关键字 'def'` 之类**指向别处**的错 —— 他会去查 `def`，而真正的问题在第一行。

**为什么这是缺陷**：`docs/188` §1 明写"写错是四种报错之一"（名字不在表里 / 写在
`module` 之后 / 写两次 / 后面没写名字）—— **漏了"关键词本身写错"这一种**。
而 §2 那条"兜底从'猜'变**拒绝**"正是为这种情况写的：现在它既不猜也不拒，
是**换了一个猜法**（当成 Loment）。

**修法**：`_GRAMMAR_ANY` 那个正则的头部是个**纯字面**（`_GRAMMAR_HEAD`），
所以 `choose write grammars python` 这种"前缀对、尾巴错"的形状匹配不到。
加一条**宽松的头**：行首是 `choose`/`write`/`grammar` 的任意子集、但**不构成**那条
严格头时，直接报"这一行像是想写 `choose write grammar <名>`，但词序/拼写对不上"。
报错时把**正确的那一行**原样打出来 —— 与报错器给别的诊断的做法一致
（`lomenterr` 的 `fixes` 至少三条）。

---

## 3. 内建"**当值用**"与"**当语句用**"给**两个答案**（`tools/pytrans.py` 内部不一致）

```python
def g() -> int:
    store8(0, 0, 1)      # 过 —— 表达式语句那条路走 raw(), 不查 self.fns
    return alloc(16)     # 报: 调用了本单元没有的函数 `alloc`
```

同一个内建、同一份源、两种结论。根因在 `pytrans.Emitter`：

* `ty_of(e)` 对 `ast.Call` 会查 `self.fns`（"Stage A 不跨单元"那条闸）；
* `raw(e)` 对 `ast.Call` **不查**，直接发 `名字(实参…)`。

**为什么这是缺陷**：那条闸的**用意**是"跨单元的调用要显式声明"，而判决现在取决于
"这个调用恰好写在表达式位置还是语句位置"。语句位置那条路**绕过了闸**：
`store8(0,0,1)` 之所以"能过"，不是因为它被允许，是因为没人问。

**修法**：把那道闸提到**一处** —— 让 `raw()` 走 `ty_of()` 已经算过的结果，
或在 `raw()` 的 `ast.Call` 分支里补同一条检查。两处各写一遍必然漂，这与仓库里
"一条规则只许有一处落点"是同一条纪律。

**注意**：修完之后"内建当语句"会**开始报错**。那是**对的**（Stage A 本来就不跨单元），
但要确认 `loment_pytrans_test` 的语料里没有依赖这个漏洞的写法 —— 实测那批语料
（`loment/pytrans/*.py`）里没有。

---

## 4. `check` **不查 `use` 进来的模块的正文**（这条最严重，且与语法无关）

最小复现（六行，实测）：

```
deps/badlib/badlib.lomt:   module badlib
                           pub fn f(p: ptr) -> u32 { return p; }      // 明显的类型错

main.lomt:                 module main
                           use badlib
                           fn _start() { let x: u32 = f(alloc(8)); syscall4(60, x as u64, 0, 0); }
```

```
A) 把坏库当**依赖**检查 : clean          <- 没报
B) 把坏库当**入口**检查 : ['4: return 类型 ptr，函数 f 声明 u32']
```

⇒ **`lomentc.check` 只报入口那一份正文里的类型错；`use` 进来的模块的正文不查**
（或者查了不往上报）。

**为什么这是缺陷**：后果是**一个库可以带着正文类型错发布，而每一个使用它的程序
`check` 都是绿的**。这不是"少报一条"的量级 —— 它是"库"这个形态的整个安全网。

**这一轮是怎么被抓住的**：LumtUI 有两个正文类型错（`let bits: u32 = <i64>`、
`return <ptr>` 而声明 `-> u32`），**我自己的探针全绿**（探针拿 `main.lomt` 当入口、
把库当依赖）。抓住它们的是两条**别的形状**的判据：
`loment_std_test::test_std_modules_are_checkable`（把 `loment/lib/*.lomt` **逐个当入口**）
与 `loment_p8_test`（逐文件与参考比 IR）。

**修法**：`check` 把依赖的正文一并查，报错时**标出是哪个模块**
（`模块名:行: 消息`，或复用 `FrontUnit.translated` 那类"行号指的是哪一份"的机制 ——
见 `docs/179` §6.5 对同一问题在另一处的处理）。

**在这条修好之前，给库写判据的人只能靠形状**：把**库文件自己当入口**去 check
（LumtUI 的 `tools/loment_lumtui_test.py` 里
`test_each_lib_module_is_checkable_as_its_own_entry` 就是把那个形状钉在自己身上）。

---

## 5. 影响面与建议的次序

| | 缺陷 | 命中面 | 该动谁 | 建议 |
|---|---|---|---|---|
| 1 | `~` 发出来编不过 | 每一份用到 `~` 的 Python 写法 | `tools/pytrans.py` | **先修**（一行；修法二选一） |
| 2 | 声明关键词写错静默退回 | 每一份想用别的拼法、但第一行写歪的源 | `tools/potato_from.py` | **先修**（影响"前门"的可信度） |
| 3 | 内建两个答案 | 语句位置的内建调用 | `tools/pytrans.py` | 修（一处判决） |
| 4 | 依赖正文不查 | **所有库** | `tools/lomentc.py` | **最重要**，但动的是检查器核心，另开 |

1 与 2 是**低成本、高确定性**的；3 是把一条判决收拢到一处；4 值得单独立一项
（它同时是"库系统靠什么保证"的问题，`docs/168` / `docs/183` 那条线会关心）。

**另记一行（不是缺陷，是这一轮量出来的边界）**：Python 写法的**正文**子集只收
`int`/`bool`，没有指针、结构体、字符串、下标、字段访问，也不能调本单元以外的函数。
所以"整个 GUI 库用 Python 写法写"这句话今天不成立 —— 不是取舍，是**可达面**。
实测清单与每一条的最小复现在 `docs/196` §4.1–4.8；那一节里另有三条
**自举驱动**的产物分歧（链式 `as` / 整数字面量定型 / 依赖发射顺序），
归 `docs/189` 那条线，`docs/196` §4.10。
