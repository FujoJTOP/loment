# 236 · lompi 0.2.0：`fetch` 自己联网

> 状态：**已落地**（2026-10-12）。0.1.5 的 `lompi fetch` 只会把 `git clone` **打印**出来让人跑，
> 理由写在指南里：「PE 运行时只实现 8 个 syscall，里面没有 socket」。联网面补完之后
> （`docs/234`：派发面 **29 个号**，socket 那一族两个平台都有），lompi **自己**就能做这件事了。
>
> 一句话：**这一版把「lompi 不能联网」这句话从指南里删掉了 —— 但它没有变成「lompi 什么都能拉」**：
> 能拉的是 **明文 `http://` + IPv4 字面量**。TLS 与解析器仍然不在工具链里（§3）。

## 1. 现在真的做什么

```
lompi fetch --registry http://127.0.0.1:8000/libs
```

会**真的**：

1. `GET <base>/lompi-index.txt` —— 注册表索引，每行 `<name> <version> <path> <sha256hex>`；
2. 按索引逐个 `GET <base>/<path>`；
3. **校验索引里写着的 sha256**（`lpi_sha` 那一支），对不上就**拒**、不落盘；
4. 写进 `<root>/cache/<slug>/<name>/<version>/`（`install` 照旧从那儿读）。

走的是 `syscall(41/42/44/45)` 那一族（socket/connect/sendto/recvfrom）—— **两个平台同一条路**，
没有分叉：PE 侧由垫片的 `ws2_32` 顶着（`docs/234`），ELF 侧就是裸系统调用。

## 2. 判据

| 判据 | 钉住什么 |
|---|---|
| `loment_lompi_test::test_fetch_pulls_the_registry_over_http_itself` | **起一个真的 HTTP 服务器**（`127.0.0.1`，端口由内核分配），喂一份索引 + 两个文件：lompi 必须 GET 索引、逐个 GET、校验摘要、落盘（内容逐字节相同）；索引里**改一位摘要**时必须**拒**且不落盘 |
| `loment_lompi_test::test_fetch_prints_the_plan_for_https_and_hostnames` | 边界**不是**"做不到"三个字：`https://` 那条要说清是 **TLS** 缺，主机名那条要说清是**解析器**缺，而且**照旧打印可执行的计划** |

实测（2026-10-12）：**Linux（ELF）`loment_lompi_test` 27/27**；Windows（PE）**26/27**，
那条红的是 `test_dev_copy_and_repo_copy_do_not_silently_drift` —— 它比的是**本机开发区那份
正本**（`D:\Dev\Lolment-ku\lompi`），而那份还停在 0.1.5、没有联网那一段（正本在本机不存在时
它按规矩跳过，所以 Linux 上是 27/27）。**这条红与本次改动无关，改动前就红**；两台机器上
同一份源的两个产物，联网这条路的行为是逐字节相同的。

> 服务器**故意用 Python 起**（判据这一侧一律是 Python）：拿 lompi 去测 lompi 就是自证。
> 它不随 lompi 发布，也不进 lompi 的任何运行路径 —— lompi 仍是"编出来的独立可执行文件"。

## 3. 四条**写明的**边界（都不是 lompi 的设计立场）

| 不认 | 为什么 | 它今天的样子 |
|---|---|---|
| `https://` | 工具链里**零 TLS**（没有握手、没有证书、没有密钥交换） | 打印计划 + 一句"https needs TLS" |
| 主机名（如 `libs.example.com`） | 工具链里**没有解析器**（没有 `getaddrinfo`；示例一律把 `127.0.0.1` 逐字节写进 `sockaddr_in`） | 打印计划 + 一句"a hostname needs a resolver" |
| 3xx 重定向 | 要解析 `Location` 与相对路径 —— 是另一件事 | 非 200 一律当失败 |
| 超过一次 GET 的响应 | 整条响应要装进调用方的缓冲 | 失败（不截断） |

还有一条**平台**边界：落盘的目录**必须已经存在** —— `mkdir` 在 PE 上没有（`sys_can_mkdir()` 恒假）。
所以写不进去时 lompi **不猜**，直接把要建的那一条打出来：

```
lompi: cannot write C:\…\cache\8000\mathutil\0.1.0\mathutil.lomt
lompi: make it first:  mkdir -p C:\…\cache\8000\mathutil\0.1.0
```

（判据就是照这行提示把目录建出来再跑第二次 —— 顺带把"提示真的有用"也钉住了。）

## 4. 路上撞出来的两件事（都记下来）

### 4.1 自举编译器的**形参上限是 10**，超了**无声地死**（真缺陷 → issue #265）

第一版把 `net_http_get` 写成 12 个形参、`net_fetch_registry` 写成 14 个 —— **参考实现照收，
自举实现（stage1）直接 SIGILL**（退出码 132、stdout 空、stderr 空）。实测：

```
8 个形参  → rc=0
10 个形参 → rc=0
11 个形参 → rc=132 (Illegal instruction)
```

**两边对"什么是一个合法程序"的看法不一致**，而自举那边不报错、只死 —— 这是**工具链的缺陷**，
不是 lompi 的。已开 issue **#265**（`FujoJTOP/loment#265`，标题写明"the reference implementation
accepts it"）。lompi 侧的处理：**不写那种形状** —— 参数挤进一块 128 字节的暂存（`NETREQ_*`），
函数签名回到 2~5 个参数。

### 4.2 Loment 的字符串转义**没有 `\r`**（语言陷阱 → 已在 issue #36）

`"\r\n"` 得到的不是"回车换行"，而是**一个字母 `r` 加一个换行** —— `\n` 认，`\r` 不认，
而且**不报错**。这条是实测撞出来的：HTTP 请求行发出去的是

```
GET /lompi-index.txt HTTP/1.0r        ← 服务器看到的
```

服务器直接回 `400 Bad request version ('HTTP/1.0r')`。两边实现**行为一致**（所以不是孪生分歧，
是一条**共同的**语言事实）—— lompi 的 HTTP 层因此不写字面量，CR 走字节（`net_crlf`）。

这条路**早有人走过**：issue **#36** 开着（`FujoJTOP/loment#36`，"String literals have no escape
for CR: `"\r"` compiles to the letter r, and unrecognised escapes are silently dropped"），
含"不认识的转义被静默丢掉"那半。**所以这里不另开一条**，只记"lompi 撞上了它、绕开了它"。

## 5. 版本与清单

* `lompi version` / `-h` / `config` 里的版本串 **0.1.5 → 0.2.0**（`lpi_cli.lomt` 三处 +
  `loment_lompi_test.LPI_VERSION` —— 判据要求这两处**同时**改，见那条"改版本要过一次手动确认"）；
* `selftest_net()` 接进自检驱动（`lpi_test.lomt`）：URL 拆解三条自检（认 IP+端口、拒 https、
  拒主机名）现在**真的会跑**；它当场抓出过一个真 bug —— `txt_parse_dec` 返回的是**打包值**
  （高位是数、低位是位数），第一版把它当数用，于是每段都"大于 255"、整个 URL 都判"不认"。
