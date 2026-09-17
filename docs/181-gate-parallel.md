# 181 · 门禁并行：从"跑不完"到三分钟

> 状态: **已实现**（2026-09-17）· 入口: `python tools/ci.py --static-only`（默认并行）
> 一句话: **41 条静态判据彼此独立，串行跑纯是浪费 —— 墙钟 25+ 分钟降到约 3 分钟。**

## 1. 先量，别猜

改之前 `ci.py` 的静态跑法**没有计时**，只有 `PASS/FAIL` 一行。所以"门禁慢"是个印象，
"哪条慢"更是印象 —— 而我第一次猜的两个"大头"（自举镜像、自举 checker）实测是
**3.2s 和 1.2s**，完全不是瓶颈。

补上计时之后的第一次全量（`-j 8`）：

```
fujoci: 静态门禁 墙钟 188.6s / 各条合计 1029.3s / 最慢一条 188.0s（-j 8）
  最慢的 8 条：
    loment_p8_test             188.0s  18.3%
    loment_elf_test            168.5s  16.4%
    loment_seed_test           160.3s  15.6%
    loment_dist_test           160.1s  15.6%
    loment_genesis_test        158.6s  15.4%
    loment_pe_test              57.5s   5.6%
    loment_rel_test             30.5s   3.0%
    loment_doc_test             16.6s   1.6%
```

**"墙钟 / 合计 / 最慢一条"三个数要一起看**：
- **合计 ÷ 墙钟 = 并行实际拿到的加速**（这里是 5.5×，`-j 8` 下算合理）；
- **墙钟 ≈ 最慢一条** ⇒ 并行已经榨干，**再快只能优化那条本身**（下一步在 §6）。

## 2. 并行之前必须先修的一件事：WSL 的 `/tmp` 是共享的

十几条判据在 WSL 里用**固定的临时文件名**（`/tmp/json_probe.bin`、
`/tmp/lomelf_mir_s1.bin`、`/tmp/ffigo` …）。WSL 的 `/tmp` 是**所有 `wsl -e` 调用共用**的，
所以两个进程并发跑同一条路时，会出现**一个进程执行另一个进程的二进制** ——
那是**错结果**，不是慢。

改法：每个模块的 WSL 临时路径加**进程号前缀** `_T = f"/tmp/loment-{os.getpid()}-"`。

**必须是"每进程一份"而不是"每次 shell 调用一份"**：`loment_lsp_test` 会**跨两次
`wsl -e` 调用**读回 `/tmp/loment_lsp_out.bin`（先写、再 `wsl -e cat`），前缀在那两次之间
必须保持不变。用 `$$`（shell 自己展开）就会把它拆坏。

顺带：文件名不再被下次覆写，所以每轮会留一批 —— 门禁开头按**时间**清一次旧的
（`find /tmp -maxdepth 1 -name 'loment-*' -mmin +60 -delete`）。**不能直接
`rm -f /tmp/loment-*`**：同一个 `/tmp` 是共享的，那会把**另一台并发在跑的门禁**正在用的
文件删掉（本仓明确考虑过"同机并行多个 agent"，见 `FUJO_MON_PORT`）。

## 3. 并行踩到的第一件事：`build_stage1()` 无条件重写共享产物

第一次 `-j 8` 全量跑出来三条红，而它们**单独跑都是绿的**：

| 判据 | 并行 | 单独 |
|---|---|---|
| `loment_lompi_test` | **10/20** | 20/20 |
| `loment_cli_test` | 31/32 | 32/32 |
| `loment_ffi_test` | 17/18 | 17/18（**这条不是竞态**，见 §4） |

手动让两条并行就复现了：

```
FAIL test_builds_with_the_shipped_toolchain:
     PermissionError: [Errno 13] Permission denied: 'loment\build\dist\stage1.exe'
```

**根因**：`loment_dist.build_stage1()` 写的是**固定路径** `loment/build/dist/stage1.exe`，
而 `loment_cli_test` / `loment_lompi_test` / `loment_dist_test` **都调它**。一条判据正
**执行**着那个 exe，另一条把它重写掉 —— Windows 上不允许替换正在执行的文件。

**改法**（`loment_dist._write_shared`）：**内容一样就一个字节都不写**，否则写 `.pid.tmp`
再 `os.replace` 原子换入。稳态下内容不变 ⇒ 零写入 ⇒ 竞态根本不存在（只有第一次构建会写，
而那时还没有人能执行它）。`emit_ir` 的 `STAGE/*.ll` 同样处理（`loment_dist_test` 要读它）。

## 4. 那一条**不是**竞态：`loment_ffi_test` 是我自己改坏的

`17/18` 单独跑也是红的，所以不是并行的问题。原因是我批量改 `/tmp` 名字时，正则把
**双引号串里的单引号**也当成了边界，于是

```python
_bridge_case(f"cd /tmp/ffigo && '{go}' run m.go", 5, "Go 桥")
```

那一行**没被改到**，而它前后两行改到了 —— 于是 Go 桥去 `cd /tmp/ffigo`（不存在）。

**教训**：批量机械改代码，**改完必须逐条跑一遍受影响的判据**，不能只看"语法过没过"。
这一次是靠全量门禁把三条红一起打出来才发现的，而其中两条是竞态、一条是改错 ——
**混在一起更容易误判成一类**。

## 5. 并行的两条前提（都写进了代码）

1. **每条在自己的进程里跑**（`ProcessPoolExecutor`）。这不只是更快：某条的全局状态
   （`sys.argv`、模块级缓存）不会串到别条上 —— 并行**更安全**的一面。
2. **会写仓库共享位置的判据独占跑**（`EXCLUSIVE_STATIC`）。目前只有 `lompi_sync`
   ——它把正本同步进 `lompi/`，而好几条判据要读 `lompi/`。（`loment_dist_test` 用的
   `loment/build/dist` 只有它自己碰，不必独占。）

## 6. 下一步：瓶颈现在在那 5 条 160–190s 的判据上

并行已经把"和"变成了"最慢一条"，所以再往下要动的是**那一条本身**：

| 判据 | 时间 | 大致在干什么 |
|---|---|---|
| `loment_p8_test` | 188s | 自举语料 55/55 逐字节（WSL 里跑两遍）+ 三阶段定点 + 驱动闸门 |
| `loment_elf_test` | 168s | 自举镜像 + 8 条 WSL 运行 |
| `loment_seed_test` | 160s | genesis 起头的**无 Python 自举**（4 条证明 + 定点） |
| `loment_dist_test` | 160s | 69 条：造包、装包、跑安装器 |
| `loment_genesis_test` | 158s | 同上那条链 |

它们**慢在 WSL 与 clang**，而 `-j 8` 下这些是互相抢的（8 路已经接近饱和：合计 1029s ÷ 8
= 129s，而墙钟 188s 高于它 ⇒ 有争用）。

所以下一步不是"再加并行度"，而是三选一（**都还没做**）：
1. **把 WSL 换成原生**：ELF 只能在 WSL 里跑这一条是 `docs/181` 之前的老约束，而
   `loment_dist` 已经在**本机原生**跑 stage1（Windows PE）—— 同一条路能不能覆盖更多判据？
2. **去掉重复的自举链**：`seed` / `genesis` / `elf` / `p8` 四条都在跑自举，它们的
   **中间产物**能不能像 §3 那样共享（现在每条的中间件都在自己的临时目录里，各建一遍）。
3. **判据瘦身**：`loment_dist_test` 69 条里有相当一部分是安装器的形状测试，
   要不要每条都跑整遍安装。

**在没量之前，别在这三条里选** —— §1 那次的教训就是"猜的两个大头都不是大头"。

## 7. 怎么用

```bash
python tools/ci.py --static-only            # 默认并行 (-j min(8, CPU))
python tools/ci.py --static-only -j 1       # 串行同进程 (老行为, 排查用)
python tools/ci.py --static-only --only-static loment_p8_test   # 只跑一条 (优化门禁用)
```

`--only-static` 可以重复；优化门禁时**必须**能单独把一条跑起来看 —— 整轮三分钟，
改一行重跑一轮仍然太慢。
