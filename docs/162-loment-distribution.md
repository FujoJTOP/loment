# 162 · Loment 发行包：命令安装与安装包安装

> 版本 `0.1.3.4-alpha`（显示名 **Loment 0.1.3.4 Alpha**）· 构建器 `tools/loment_dist.py`
> · 判据 `tools/loment_dist_test.py`（进门禁；审计里是 C15）

## 0. 一句话

同一个包**两种装法**：命令安装（`sh install.sh` / `powershell -File install.ps1`）与
安装包安装（Windows 双击 `setup.exe`）。装出来的东西完全一样 —— **四个自举产物 + 启动器 + 种子，
包里没有 Python**（docs/159 那条"写并跑 Loment 不需要 Python"的落地形态）。

## 1. 产物（`loment/dist/`）

| 文件 | 装法 | 说明 |
|---|---|---|
| `loment-0.1.3.4-alpha-linux-x64.tar.gz` | 解包 → `sh install.sh` | Linux / WSL；含 `install.sh` |
| `loment-0.1.3.4-alpha-windows-x64.zip` | 解包 → `powershell -File install.ps1` | Windows；含 `install.ps1` / `install.cmd` |
| `loment-0.1.3.4-alpha-windows-x64-setup.exe` | **双击** | 自解压安装包（Windows 自带 `iexpress` 做的） |
| `SHA256SUMS` | — | 上面三件的 sha256 |

**两个归档是确定性字节**：zip 固定时间戳（`1980-01-01`）+ 目录项排序 + unix 权限位，
tar.gz 的 `mtime=0` + 稳定 uid/gid/uname、gzip 头不带时间。同输入两次构建 sha256 相同
（判据里有这条）。`setup.exe` **不是**确定性的（iexpress 自己塞时间戳），所以它只进
`loment/dist/SHA256SUMS`，不进 `loment/build/release-manifest.json` —— 与 VSIX 同一处理。

## 2. 包里是什么

| 路径 | 作用 |
|---|---|
| `bin/loment-driver` | 编译器（装载 → 检查 → 发射 LLVM IR）。**就是自举链的 stage1** |
| `bin/loment-lsp` | 语言服务（补全/跳转/诊断/`--check`），stdio 上的 LSP |
| `bin/loment-fmt` | 格式化器（与 Python 版逐字节相同，docs/159） |
| `bin/loment-doc` | API 文档生成器 |
| `bin/loment` | 启动器（`version`/`ir`/`check`/`build`/`run`/`fmt`/`doc`/`lsp`） |
| `share/loment/seed.ll` | 自举种子 —— 只用 clang 就能从它重建整套工具链 |
| `share/loment/version` | `Loment 0.1.3.4 Alpha (0.1.3.4-alpha)` + 提交号与提交日期 |
| `share/loment/examples/user_hello.lomt` | 示例（用 syscall 打印） |
| `SHA256SUMS` | **随包**校验和，安装脚本第一步就校它 |

四个 ELF 都是自举产物，构建路径与冻结面判据同一条：

```
clang(种子 selfhost_driver.ll) -> stage1
stage1 <entry.lomt>            -> IR      (与参考实现逐字节相同, 见 docs/158)
clang -nostdlib -static        -> ELF     (无 libc, _start 即入口)
```

## 3. 安装语义

- **前缀**：Linux 默认 `$HOME/.local`（`--prefix` 可换）；Windows 默认 `%LOCALAPPDATA%\Loment`。
- **Windows 是两层**：工具链是 Linux ELF，所以装在 **WSL**（`$HOME/.local/share/loment`），
  Windows 侧只放一个 `loment.cmd` 转发（`wsl -e <wsl>/bin/loment %*`）。启动器自己把
  `D:\...` 路径转成 `/mnt/d/...`（`wslpath`）。
- **PATH**：Windows 走用户级 `Path`（`HKCU`，不需要管理员）；Linux 只打印该加哪一行，
  **不替用户改 shell 配置**（`--no-path` 关掉提示）。
- **文件类型**：Windows 上注册 `.lomt`/`.lom`（ProgID `Loment.Source` / `Loment.L0`，
  HKCU only），与 `tools/loment_filetype.py` 同一套键；`-NoFileType` 可关。
- **冒烟测试**：装完立刻 `loment version` + 编一个示例，失败就报错退出（不是"复制完就完事"）。
- **卸载**：`sh install.sh --uninstall` / `powershell -File install.ps1 -Uninstall`
  （后者同时清 PATH 与文件类型键）。
- **幂等**：重复安装成功且不改语义（判据里有）。

**不做的事**（写在文档里免得当成缺陷）：不装 clang（地基语言，`run`/`build` 需要它，
缺了会明确报错）；不改系统级 PATH；不注册系统级文件关联（`UserChoice` 那条带哈希保护，
只能由用户在"打开方式 → 始终"里点）。

## 4. 自解压安装包怎么做的

用 Windows **自带**的 `iexpress`（`C:\Windows\System32\iexpress.exe`），不引第三方工具：

```
payload.zip + install.ps1 + install.cmd  --SED-->  loment-...-setup.exe
```

双击后：解压到临时目录 → `install.cmd` → `powershell -File install.ps1 -PayloadZip payload.zip`
→ 后续与命令安装**同一条路**（同一个 install.ps1，不是第二套逻辑）。

**为什么不用 MSI / Inno / NSIS**：本机没有 WiX/Inno/NSIS，装它们要联网 + 管理员；而
`iexpress` 是系统自带的，能做"双击安装"这件事。代价要如实说：

- **未签名/自签名** → SmartScreen 会提示"未知发布者"（README 里写了怎么继续；
  签名怎么做、为什么自签名消不掉警告，见 `docs/163-loment-signing.md`）；
- `setup.exe` 的字节**不确定**（不能当"可复现工件"）；
- 没有 MSI 那套企业分发/静默安装（`install.ps1 -DryRun` 可以当"先看计划"）。

### 4b. 签名与顺序

`loment/dist/` 里的 `setup.exe` 可以用 `tools/loment_sign.py --dist --sign --sign-sums` 签名
（Authenticode + SHA256SUMS 分离签名）。**顺序是硬约束**：签名会改 PE 的字节，所以清单必须在
**签名之后**重算 —— `--dist --sign` 已经内置（签完自动重算 `SHA256SUMS`），别手动先签后改，
否则 `loment_dist --check` 会对不上。本仓当前用的是**本机自签名**证书（`CN=Loment Self-Signed (dev)`），
所以下载者仍会看到"未知发布者"；换 CA 证书只需换 `LOMENT_SIGN_PFX`/`LOMENT_SIGN_PFX_PASS`
环境变量，流程不变（`--print-cmd` 打印等价命令）。

## 5. 判据（33 条，`tools/loment_dist_test.py`，进门禁；审计里是 C15）

| 组 | 判的是 |
|---|---|
| 布局 | 该有的文件都在；`.sh`/`.ps1`/`.cmd` 纯 ASCII；`.ps1`/`.cmd` 是 CRLF；`bin/loment` 是 LF；ELF 权限 755 |
| 归档 | 归档内容与 payload **逐文件 sha256 相同**；zip/tar.gz 两次写出**字节相同** |
| 产物 | `--check` 与 `SHA256SUMS` 一致；归档里的 driver == 构建产物 |
| **端到端 · Linux** | tar → 装进临时前缀 → `loment version` 出版本行 → **`loment ir` 的产物与参考实现逐字节相同** → `loment check` 正例 0 且不吐 IR → `loment run` 真跑出输出 → 缺组件时报错**指名**（`this package does not include loment-fmt`）→ `--uninstall` 摘干净 → 再装一次仍成功 |
| **端到端 · Windows** | 解包 → `install.ps1` **真装**（`-Prefix <临时>` + `-WslDir /tmp/...` + `-NoPath -NoFileType`，不动用户 PATH 与注册表）→ 写出 `loment.cmd` 且指向 WSL 目录 → WSL 侧的 `loment version` 能跑 → Windows 风格的 `WslDir` 被**明确拒绝** |
| Windows 解析 | `install.ps1 -DryRun` 与 `-DryRun -PayloadZip`（自解压那条路）在**真 PowerShell 5.1** 下都能跑；`setup.exe` 是 PE 且非空 |

最强的一条是 Linux 那行的**逐字节相同**：它同时证明了"包里的编译器是自举产物"和"装出来的东西能用"。

### 为什么 Windows 那条要"真装"而不是只跑 `-DryRun`

只校验"脚本能解析 + 计划打印得出来"会漏掉真实的拷贝/路径 bug。本文档第一版就是这么漏的，
**真装一遍立刻抓到两个**：

1. **`Split-Path -Parent` 会把 `/` 改写成 `\`**（Windows 的 provider 语义），于是
   `wsl -e mkdir -p \tmp\a\b` 在 WSL 里建的是一个**名字带反斜杠的目录**（rc=0！），
   随后 `cp` 报 `No such file or directory` 指向错的地方。修法：WSL 路径的父目录用
   **纯字符串**算（`Wsl-Parent`），不碰 `Split-Path`。
2. **`WslDir` 传成 Windows 风格路径**（`C:/...`）时静默建出垃圾目录树。修法：显式校验
   必须是绝对 WSL 路径，否则直接报错 —— 这条现在也是判据之一。

## 6. 复现与边界

```bash
python tools/loment_dist.py --emit                       # 需要 WSL + clang (交叉链接)
python tools/loment_dist.py --list                       # 只列会打进去的文件
python tools/loment_dist.py --check                      # 产物与 SHA256SUMS 一致?
python tools/loment_dist_test.py                         # 29 条判据
```

也支持只打子集（门禁用它省时间）：`--emit --only driver --no-exe`。

**边界（诚实清单）**：只在 x86_64 上打过；Linux 侧只给 tar.gz（没有 `.deb`/`.rpm`）；
没有代码签名/公证；没有 aarch64 包（M92 的 aarch64 还没执行过，见 docs/145）；
安装包不含 clang，也不含 Python —— 两者都不需要才能装，但 `run`/`build` 需要 clang。
