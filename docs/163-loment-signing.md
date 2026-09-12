# 163 · 发行包签名

> 工具 `tools/loment_sign.py` · 判据 `tools/loment_sign_test.py`（15 条，进门禁；审计里是 C16）
> · 产物在 `loment/dist/`（docs/162）· 私钥永不进仓库

## 0. 一句话

给发行包做**两种**签名：Windows 可执行文件做 **Authenticode**（让系统/浏览器/杀软看到"发布者"），
整个包做 **SHA256SUMS 分离签名**（Linux/macOS 也能验）。**先说清楚**：用自签名证书签，
SmartScreen **照样**会提示"未知发布者" —— 要消掉警告必须买 CA 的代码签名证书（OV/EV）。

## 1. 签名到底解决什么、不解决什么

| | 自签名（本机，免费） | CA 证书（OV/EV，付费） |
|---|---|---|
| 文件有可验证的数字签名 | ✅ | ✅ |
| `Get-AuthenticodeSignature` 看到发布者 | ✅（显示"未知发布者"） | ✅ 显示真实主体 |
| **SmartScreen 警告消失** | ❌ | ✅（EV 立刻，OV 需要攒信誉） |
| 内容被篡改后能验出来 | ✅ | ✅ |
| 需要身份核验/年费 | 不需要 | 需要（个人/企业实名） |

**为什么还值得做**：签名把"这份二进制确实出自这把钥匙、且没被改过"变成可验证的事实 ——
即使钥匙是本机的，它也能挡住"传到一半被替换"和"下载后被篡改"这两类事；而且换成真证书时
**只需换证书，代码与流程一行不改**。

## 2. 两种签名

### 2.1 Authenticode（Windows 可执行文件）

- 目标：`loment-<ver>-windows-x64-setup.exe`（`.cmd`/`.exe` 以外的文件不是 PE，签不了）。
- 工具选择：有 `signtool`（Windows SDK）就用它；没有就回退 **PowerShell 自带的
  `Set-AuthenticodeSignature`**（本机就是这条路：SDK 的 signtool 不在 PATH）。
- 时间戳：`--ts <RFC3161 URL>` 可选。**离线/自签名时不要给**（时间戳要联网，而且对自签名没意义）；
  真证书签名时应该给，否则证书过期后签名会一起失效。
- 观察到的状态语义：自签名链不受信，`Status = UnknownError` 但**签名存在且发布者可读** ——
  判据因此分两档：默认只要求"有签名"，`--strict` 才要求 `Status = Valid`（装了 CA 证书才满足）。

### 2.2 SHA256SUMS 分离签名（跨平台）

```
openssl dgst -sha256 -sign <私钥> -out SHA256SUMS.sig SHA256SUMS
openssl dgst -sha256 -verify loment-signing.pub.pem -signature SHA256SUMS.sig SHA256SUMS
```

第三方拿到**公钥**（`loment/dist/loment-signing.pub.pem`）就能验整包；证书
（`loment-signing.pem`）用来看"这把钥匙的主体是谁"。

⚠️ 一个真实的坑：`openssl dgst -verify` 只吃**公钥 PEM**，给**证书**会报
`Could not find private key of public key from ...`（OpenSSL 3）。所以发布的是
`openssl x509 -pubkey -noout` 抽出来的公钥，而不是证书本身。

## 3. 凭据纪律（写进判据）

- **私钥与口令只从环境变量或命令行参数来**：`LOMENT_SIGN_PFX` / `LOMENT_SIGN_PFX_PASS`。
  源码、示例、测试里没有任何凭据字面量（判据会扫 `tools/loment_sign.py`）。
- **私钥只落在 `loment/build/sign/`，该目录被 `.gitignore` 忽略**（判据用 `git check-ignore` 证）。
  这条是签名的命门：签名工具自己跑一次 `--keygen` 就能把私钥落到工作区，忘了 ignore 就等于
  把签名钥匙发到仓库里。
- 自签名那条路走 Windows 证书存储（`CurrentUser\My`）+ 指纹，**全程不出现口令**；
  `--remove --purge` 可撤（指纹记录与私钥目录一起删）。

## 4. 怎么用

```bash
# ① 本机自签（免费、够用、只在本机可信）
python tools/loment_sign.py --keygen              # 指纹 + 公钥证书 + openssl 密钥对
python tools/loment_sign.py --dist --sign --sign-sums   # 签 setup.exe + 签清单
python tools/loment_sign.py --dist --verify --verify-sums
python tools/loment_sign.py --trust               # 可选: 放进本机受信存储 (改本机信任模型)
python tools/loment_sign.py --remove --purge      # 撤掉自签证书与私钥

# ② 换成 CA 证书（买来之后；代码/流程不变）
python tools/loment_sign.py --print-cmd           # 打印等价命令（含时间戳位）
export LOMENT_SIGN_PFX=/path/to/cert.pfx LOMENT_SIGN_PFX_PASS=...
python tools/loment_sign.py --dist --sign --sign-sums --ts https://<RFC3161 服务>
```

**顺序很重要**：签名会改 PE 的字节 → 清单必须在**签名之后**重算，否则 `loment_dist --check`
会对不上。`--dist --sign` 已经把这个顺序做进流程里（签完自动重算 `SHA256SUMS`），
所以别手动先签后改。

## 5. 判据（15 条）

| 组 | 判的是 |
|---|---|
| 凭据纪律 | 源码无密钥/证书字面量；口令只从环境变量/参数读；**私钥目录被 git 忽略** |
| 拒签 | 没配证书时 `--sign` 必须**拒绝**并给出配置办法（不能"签了个没签名的文件"还报成功） |
| 签 PE | `--keygen` 出证书 → 签一份拷贝 → **发布者主题 = 我们的 CN**；**剥掉签名的 PE 必须被验出无签名**；**签后被篡改一字节严格验证必须红** |
| 分离签名 | 签 `SHA256SUMS` → `Verified OK`；**篡改后必须验不过**；还原后又能验过 |
| 真证书路径 | `--print-cmd` 在没证书的情况下也能打印（给 CI/真证书用） |

负例都要求"真负"：无签名那一条是把证书表清零 + 截断真正剥掉签名得到的，不是拿一份
"碰巧没签"的文件 —— 第一版判据就是这么假通过的（复核时值得翻这段历史）。

**测试自清理**：用独立 CN（`CN=Loment Sign Test (temporary)`）的证书，结束前从证书存储删掉、
临时私钥目录删掉；**不动**你 `--keygen` 出来的那把，也不往 `loment/dist/` 写临时密钥的材料。

## 6. 边界（诚实清单）

- **未签名/自签名的包会触发 SmartScreen**，这是预期，不是缺陷；真证书是唯一解。
- 没有 EV 证书 ⇒ 即使买了 OV 证书，起步阶段仍可能被 SmartScreen 拦几次（信誉累积）。
- 只签 Windows PE；Linux 侧靠分离签名（tar.gz 没有等价的原生签名机制）。
- 不签 `.cmd`/`.ps1`（不是 PE）；它们的完整性由 `SHA256SUMS` + 分离签名覆盖。
- 本机证书存储里会多一张 `CN=Loment Self-Signed (dev)` 证书（`--remove` 可撤）；
  `--trust` 会改**本机**信任模型（默认不做）。
