#!/usr/bin/env python3
# loment_dist.py — Loment 发行包: **命令安装** + **安装包安装** (docs/162)
#
# 产物 (loment/dist/):
#   loment-<ver>-linux-x64.tar.gz        含 install.sh      命令安装: sh install.sh
#   loment-<ver>-windows-x64.zip         含 install.ps1     命令安装: powershell -File install.ps1
#   loment-<ver>-windows-x64-setup.exe   自解压安装包       双击安装; 用 Windows 自带的 iexpress
#                                                           (前两件是确定性字节, 这件不是 —— 见 docs/162)
#   SHA256SUMS                           上面几件的 sha256
#
# 包里**没有 Python**: 四个可执行文件都是自举产物 (种子 + clang + stage1 → IR → 链接),
# 见 docs/159。构建期需要 Python 的只有这个打包工具本身 (仓库工具链, 不进包)。
#
#   python tools/loment_dist.py --emit                        # 全部 (需要 WSL + clang)
#   python tools/loment_dist.py --emit --only driver --no-exe # 快速子集 (门禁用)
#   python tools/loment_dist.py --check                       # 现有产物与 SHA256SUMS 一致?
#   python tools/loment_dist.py --list                        # 只列会打进去的文件
#
# 退出码: 0 = 成功 / 1 = 失败 / 2 = 用法错误。

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import loment_release  # noqa: E402  (版本名单一真源: RELEASE / RELEASE_NAME)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "loment" / "dist"
STAGE = ROOT / "loment" / "build" / "dist"
SEED = ROOT / "loment" / "build" / "selfhost_driver.ll"

DISPLAY = loment_release.RELEASE_NAME       # 人读: 0.1.4 Alpha
VER = loment_release.RELEASE                # 机器: 0.1.4-alpha

#: 包里的工具 -> 入口源文件。名字就是安装后的可执行名。
TOOLS: list[tuple[str, str]] = [
    ("loment-driver", "loment/selfhost/driver.lomt"),
    ("loment-lsp", "loment/tools/lsp.lomt"),
    ("loment-fmt", "loment/tools/lomfmt.lomt"),
    ("loment-doc", "loment/tools/lomdoc.lomt"),
]
EXAMPLE = "loment/examples/user_hello.lomt"
ICON = "editors/loment.ico"
LICENSE = "LICENSE"

#: 纯文本脚本一律 ASCII: Windows PowerShell 5.1 用 ANSI 读无 BOM 的 .ps1, 非 ASCII 会变乱码
#: 并连带把后续行解析坏 (docs/157 §3.4 踩过)。中文说明在 README.md 与 docs/162 里。
LAUNCHER_SH = r'''#!/usr/bin/env bash
# Loment launcher (@DISPLAY@, @VERSION@). Installed by install.sh / install.ps1.
# No Python: everything here is the self-hosted toolchain + clang.
set -u

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
share=$(CDPATH= cd -- "$here/../share/loment" && pwd)

to_posix() {
    case "$1" in
        [A-Za-z]:[\\/]*|\\) command -v wslpath >/dev/null 2>&1 && wslpath -a "$1" || printf '%s' "$1" ;;
        *) printf '%s' "$1" ;;
    esac
}

find_clang() {
    command -v clang >/dev/null 2>&1 && { command -v clang; return 0; }
    for c in "/mnt/c/Program Files/LLVM/bin/clang.exe" "/usr/bin/clang" "/usr/local/bin/clang"; do
        [ -x "$c" ] && { printf '%s' "$c"; return 0; }
    done
    return 1
}

usage() {
    cat <<EOF
Loment @DISPLAY@  (@VERSION@)
  loment version              print version
  loment ir FILE              compile to LLVM IR on stdout
  loment check FILE           check only (diagnostics on stderr, IR discarded)
  loment build FILE [-o OUT]  compile and link to an executable
  loment run FILE             compile, link and run
  loment fmt FILE             format (prints the formatted text)
  loment doc FILE             write API docs to stdout
  loment lsp                  language server over stdio
EOF
}

need() {
    [ -x "$1" ] || { echo "loment: this package does not include $2" >&2; exit 3; }
    return 0
}

case "${1:-help}" in
    version|-v|--version)
        cat "$share/version" ;;
    ir|check)
        mode=$1; [ $# -eq 2 ] || { usage >&2; exit 2; }
        need "$here/loment-driver" loment-driver
        if [ "$mode" = ir ]; then
            exec "$here/loment-driver" "$(to_posix "$2")"
        fi
        "$here/loment-driver" "$(to_posix "$2")" >/dev/null ;;
    fmt)
        [ $# -eq 2 ] || { usage >&2; exit 2; }
        need "$here/loment-fmt" loment-fmt
        exec "$here/loment-fmt" "$(to_posix "$2")" ;;
    doc)
        [ $# -eq 2 ] || { usage >&2; exit 2; }
        need "$here/loment-doc" loment-doc
        exec "$here/loment-doc" "$(to_posix "$2")" ;;
    lsp)
        shift; need "$here/loment-lsp" loment-lsp
        exec "$here/loment-lsp" "$@" ;;
    build|run)
        mode=$1; shift
        [ $# -ge 1 ] || { usage >&2; exit 2; }
        src=$1; shift
        out=
        while [ $# -gt 0 ]; do
            case "$1" in
                -o|--out) out=${2:-}; shift 2 ;;
                *) echo "loment: unknown option $1" >&2; exit 2 ;;
            esac
        done
        need "$here/loment-driver" loment-driver
        cc=$(find_clang) || {
            echo "loment: clang not found (build/run need the foundation compiler)" >&2; exit 3; }
        win() { case "$cc" in /mnt/*) wslpath -w "$1" ;; *) printf '%s' "$1" ;; esac; }
        tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
        "$here/loment-driver" "$(to_posix "$src")" > "$tmp/a.ll" || exit 1
        if [ "$mode" = run ]; then out="$tmp/a.bin"; fi
        [ -n "$out" ] || out="${src%.lomt}"
        "$cc" --target=x86_64-unknown-linux-gnu -nostdlib -ffreestanding -static \
              -fuse-ld=lld -o "$(win "$out")" "$(win "$tmp/a.ll")" || exit 1
        if [ "$mode" = run ]; then
            chmod 755 "$tmp/a.bin"
            "$tmp/a.bin"
        else
            echo "loment: $out"
        fi ;;
    help|-h|--help)
        usage ;;
    *)
        usage >&2; exit 2 ;;
esac
'''

INSTALL_SH = r'''#!/bin/sh
# Loment @DISPLAY@ installer (Linux / WSL). No Python, no network.
#   sh install.sh [--prefix DIR] [--no-path]      default prefix: $HOME/.local
#   sh install.sh --uninstall [--prefix DIR]
set -eu

prefix=${PREFIX:-$HOME/.local}
no_path=0
uninstall=0
while [ $# -gt 0 ]; do
    case "$1" in
        --prefix) prefix=${2:-}; shift 2 ;;
        --prefix=*) prefix=${1#*=}; shift ;;
        --no-path) no_path=1; shift ;;
        --uninstall) uninstall=1; shift ;;
        -h|--help) sed -n '2,4p' "$0"; exit 0 ;;
        *) echo "install: unknown option $1" >&2; exit 2 ;;
    esac
done
[ -n "$prefix" ] || { echo "install: empty --prefix" >&2; exit 2; }
src=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ "$uninstall" = 1 ]; then
    rm -f "$prefix/bin/loment" "$prefix/bin/loment-driver" "$prefix/bin/loment-lsp" \
          "$prefix/bin/loment-fmt" "$prefix/bin/loment-doc"
    rm -rf "$prefix/share/loment"
    echo "install: removed from $prefix"
    exit 0
fi

if command -v sha256sum >/dev/null 2>&1; then
    (cd "$src" && sha256sum -c --quiet SHA256SUMS) || {
        echo "install: SHA256SUMS mismatch -- package is corrupt" >&2; exit 1; }
else
    echo "install: (no sha256sum available; skipping package verification)" >&2
fi

mkdir -p "$prefix/bin" "$prefix/share/loment"
cp -f "$src/bin/"* "$prefix/bin/"
cp -R "$src/share/loment/." "$prefix/share/loment/"
chmod 755 "$prefix/bin/"*

"$prefix/bin/loment" version || {
    echo "install: installed but 'loment version' failed" >&2; exit 1; }
echo "install: Loment @DISPLAY@ -> $prefix"

case ":${PATH}:" in
    *":$prefix/bin:"*) ;;
    *) [ "$no_path" = 1 ] || {
        echo "install: put $prefix/bin on PATH, e.g."
        echo "         echo 'export PATH=\"$prefix/bin:\$PATH\"' >> ~/.profile" >&2 ; } ;;
esac
'''

INSTALL_PS1 = r'''# install.ps1 - Loment @DISPLAY@ installer for Windows (WSL-backed toolchain).
#
# ASCII only (docs/157 3.4: PS 5.1 reads a BOM-less .ps1 as ANSI).
#   powershell -ExecutionPolicy Bypass -File install.ps1
#   powershell -File install.ps1 -Prefix D:\Loment -WslDir /home/me/.local/share/loment
#   powershell -File install.ps1 -DryRun          # print the plan, change nothing
#   powershell -File install.ps1 -Uninstall
#
# The toolchain is a Linux ELF, so it lives in the WSL filesystem; Windows gets only a
# thin loment.cmd that forwards into WSL.

[CmdletBinding()]
param(
    [string]$Prefix = '',
    [string]$WslDir = '',
    [string]$PayloadDir = '',
    [string]$PayloadZip = '',
    [switch]$NoPath,
    [switch]$NoFileType,
    [switch]$Uninstall,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($Prefix -eq '') { $Prefix = Join-Path $env:LOCALAPPDATA 'Loment' }
$BinDir = Join-Path $Prefix 'bin'

function Say([string]$m) { Write-Host $m }

function Need-Wsl {
    if (-not (Get-Command wsl -ErrorAction SilentlyContinue)) {
        throw "WSL not found. The Loment toolchain is a Linux ELF; install WSL ('wsl --install')."
    }
    & wsl -e true
    if ($LASTEXITCODE -ne 0) { throw "WSL is present but not usable ('wsl -e true' failed)." }
}

function To-WslPath([string]$p) {
    $out = & wsl -e wslpath -a $p
    if ($LASTEXITCODE -ne 0) { throw "wslpath failed: $p" }
    return "$out".Trim()
}

function Get-WslHome {
    $h = (& wsl -e printenv HOME).Trim()
    if (-not $h) { throw "cannot resolve HOME inside WSL" }
    return $h
}

function Wsl-Parent([string]$p) {
    # NOTE: do NOT use Split-Path here -- on Windows it rewrites '/' as '\', and then
    # "wsl -e mkdir -p \tmp\x" happily creates a *relative* junk directory instead of
    # the intended one (rc=0, silently wrong). Pure string math keeps WSL paths intact.
    $i = $p.LastIndexOf('/')
    if ($i -le 0) { return '/' }
    return $p.Substring(0, $i)
}

function Resolve-Payload {
    if ($PayloadDir -ne '') { return $PayloadDir }
    if ($PayloadZip -ne '') {
        $dst = Join-Path $env:TEMP ('loment-setup-' + $PID)
        if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Recurse -Force }
        Expand-Archive -LiteralPath $PayloadZip -DestinationPath $dst -Force
        return $dst
    }
    return $ScriptDir
}

function Verify-Sums([string]$payload) {
    $sums = Join-Path $payload 'SHA256SUMS'
    if (-not (Test-Path -LiteralPath $sums)) { throw "SHA256SUMS missing -- package is incomplete" }
    $bad = 0
    foreach ($line in Get-Content -LiteralPath $sums) {
        if ($line -notmatch '^([0-9a-f]{64})\s+(.+)$') { continue }
        $want = $Matches[1]; $rel = $Matches[2]
        $f = Join-Path $payload $rel
        if (-not (Test-Path -LiteralPath $f)) { Say "[sums] missing: $rel"; $bad++; continue }
        $got = (Get-FileHash -LiteralPath $f -Algorithm SHA256).Hash.ToLower()
        if ($got -ne $want) { Say "[sums] MISMATCH: $rel"; $bad++ }
    }
    if ($bad -gt 0) { throw "$bad file(s) failed SHA256SUMS verification" }
    Say "[1/6] SHA256SUMS verified"
}

function Remove-PathEntry([string]$dir) {
    $cur = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (-not $cur) { return }
    $parts = $cur.Split(';') | Where-Object { $_ -ne '' -and $_ -ne $dir }
    [Environment]::SetEnvironmentVariable('Path', ($parts -join ';'), 'User')
}

function Add-PathEntry([string]$dir) {
    $cur = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($cur -and ($cur.Split(';') -contains $dir)) { return }
    $new = if ($cur) { "$cur;$dir" } else { $dir }
    [Environment]::SetEnvironmentVariable('Path', $new, 'User')
}

function Find-Editor {
    foreach ($scope in @('LOCALAPPDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)')) {
        $base = [Environment]::GetEnvironmentVariable($scope)
        if (-not $base) { continue }
        foreach ($rel in @('Programs\Microsoft VS Code\Code.exe', 'Microsoft VS Code\Code.exe')) {
            $p = Join-Path $base $rel
            if (Test-Path -LiteralPath $p) { return $p }
        }
    }
    return $null
}

# Mirrors tools/loment_filetype.py: same ProgIDs, same keys, HKCU only (no admin).
function Register-FileType {
    param([string]$Editor, [string]$Icon)
    $map = @{ '.lomt' = @('Loment.Source', 'Loment source file', 'text/x-loment');
              '.lom'  = @('Loment.L0', 'Loment L0 declaration', 'text/x-lom') }
    $cmd = '"' + $Editor + '" "%1"'
    foreach ($ext in $map.Keys) {
        $progid = $map[$ext][0]; $friendly = $map[$ext][1]; $mime = $map[$ext][2]
        New-Item -Path "HKCU:\Software\Classes\$ext" -Force | Out-Null
        Set-ItemProperty -Path "HKCU:\Software\Classes\$ext" -Name '(default)' -Value $progid
        New-Item -Path "HKCU:\Software\Classes\$ext\OpenWithProgids" -Force | Out-Null
        New-ItemProperty -Path "HKCU:\Software\Classes\$ext\OpenWithProgids" -Name $progid `
            -Value '' -PropertyType String -Force | Out-Null
        New-Item -Path "HKCU:\Software\Classes\$progid" -Force | Out-Null
        Set-ItemProperty -Path "HKCU:\Software\Classes\$progid" -Name '(default)' -Value $friendly
        Set-ItemProperty -Path "HKCU:\Software\Classes\$progid" -Name 'FriendlyTypeName' -Value $friendly
        Set-ItemProperty -Path "HKCU:\Software\Classes\$progid" -Name 'Content Type' -Value $mime
        Set-ItemProperty -Path "HKCU:\Software\Classes\$progid" -Name 'PerceivedType' -Value 'text'
        New-Item -Path "HKCU:\Software\Classes\$progid\DefaultIcon" -Force | Out-Null
        Set-ItemProperty -Path "HKCU:\Software\Classes\$progid\DefaultIcon" -Name '(default)' `
            -Value ('"' + $Icon + '",0')
        New-Item -Path "HKCU:\Software\Classes\$progid\shell\open\command" -Force | Out-Null
        Set-ItemProperty -Path "HKCU:\Software\Classes\$progid\shell\open\command" `
            -Name '(default)' -Value $cmd
        $fk = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\$ext\OpenWithProgids"
        New-Item -Path $fk -Force | Out-Null
        New-ItemProperty -Path $fk -Name $progid -Value '' -PropertyType String -Force | Out-Null
    }
    Say "[6/6] .lomt/.lom registered (open with: $Editor)"
}

function Unregister-FileType {
    foreach ($ext in @('.lomt', '.lom')) {
        $progid = if ($ext -eq '.lomt') { 'Loment.Source' } else { 'Loment.L0' }
        foreach ($k in @("HKCU:\Software\Classes\$progid\shell\open\command",
                         "HKCU:\Software\Classes\$progid\shell\open",
                         "HKCU:\Software\Classes\$progid\shell",
                         "HKCU:\Software\Classes\$progid\DefaultIcon",
                         "HKCU:\Software\Classes\$progid",
                         "HKCU:\Software\Classes\$ext\OpenWithProgids",
                         "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\$ext\OpenWithProgids")) {
            Remove-Item -Path $k -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    Say "[--] .lomt/.lom registration removed"
}

$CmdBody = @"
@echo off
rem Loment launcher (@DISPLAY@) - generated by install.ps1
wsl -e WSLDIR/bin/loment %*
exit /b %ERRORLEVEL%
"@

if ($Uninstall) {
    Need-Wsl
    if ($WslDir -eq '') { $WslDir = (Get-WslHome) + '/.local/share/loment' }
    if ($DryRun) { Say "[dry-run] would remove $WslDir, $Prefix and the file-type keys"; exit 0 }
    & wsl -e rm -rf $WslDir
    Remove-Item -LiteralPath $Prefix -Recurse -Force -ErrorAction SilentlyContinue
    if (-not $NoPath) { Remove-PathEntry $BinDir }
    if (-not $NoFileType) { Unregister-FileType }
    Say "uninstalled"
    exit 0
}

$Payload = Resolve-Payload
Say "Loment @DISPLAY@ (@VERSION@)"
Say "  windows prefix: $Prefix"
Say "  wsl dir:        $(if ($WslDir -eq '') { '<home>/.local/share/loment' } else { $WslDir })"
Say "  payload:        $Payload"

if ($DryRun) {
    Say "[dry-run] would: verify SHA256SUMS; copy bin/ + share/ into WSL;"
    Say "[dry-run]       write $BinDir\loment.cmd; add $BinDir to user PATH; register .lomt/.lom"
    exit 0
}

Need-Wsl
if ($WslDir -eq '') { $WslDir = (Get-WslHome) + '/.local/share/loment' }
elseif ($WslDir.StartsWith('~')) { throw "WslDir must be absolute (~ is not expanded): '$WslDir'" }
if (-not $WslDir.StartsWith('/')) {
    throw "WslDir must be an absolute WSL path (got '$WslDir'). A Windows-style path cannot hold an ELF."
}

Verify-Sums $Payload

$wslBin = "$WslDir/bin"; $wslShare = "$WslDir/share/loment"
& wsl -e mkdir -p $wslBin $wslShare
if ($LASTEXITCODE -ne 0) { throw "mkdir in WSL failed" }
foreach ($n in @('loment', 'loment-driver', 'loment-lsp', 'loment-fmt', 'loment-doc')) {
    $f = Join-Path (Join-Path $Payload 'bin') $n
    if (-not (Test-Path -LiteralPath $f)) { continue }
    & wsl -e rm -f "$wslBin/$n"
    & wsl -e cp (To-WslPath $f) "$wslBin/$n"
    if ($LASTEXITCODE -ne 0) { throw "copying $n into WSL failed" }
    & wsl -e chmod 755 "$wslBin/$n"
}
foreach ($rel in @('share/loment/version', 'share/loment/seed.ll',
                   'share/loment/examples/user_hello.lomt')) {
    $f = Join-Path $Payload $rel
    if (-not (Test-Path -LiteralPath $f)) { continue }
    $dest = "$WslDir/$rel"
    # create the target dir first: without it cp fails as "No such file or directory",
    # which points at the wrong place (the directory, not the file)
    & wsl -e mkdir -p (Wsl-Parent $dest)
    if ($LASTEXITCODE -ne 0) { throw "mkdir for $rel in WSL failed" }
    & wsl -e cp (To-WslPath $f) $dest
    if ($LASTEXITCODE -ne 0) { throw "copying $rel into WSL failed" }
}
Say "[2/6] toolchain -> WSL: $wslBin"

New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
$cmdFile = Join-Path $BinDir 'loment.cmd'
[IO.File]::WriteAllText($cmdFile, $CmdBody.Replace('WSLDIR', $WslDir),
                        (New-Object System.Text.UTF8Encoding($false)))
$iconSrc = Join-Path $Payload 'bin/loment.ico'
$icon = Join-Path $BinDir 'loment.ico'
if (Test-Path -LiteralPath $iconSrc) { Copy-Item -LiteralPath $iconSrc -Destination $icon -Force }
foreach ($rel in @('README.md', 'LICENSE')) {
    $f = Join-Path $Payload $rel
    if (Test-Path -LiteralPath $f) { Copy-Item -LiteralPath $f -Destination (Join-Path $Prefix $rel) -Force }
}
$verSrc = Join-Path $Payload 'share/loment/version'
if (Test-Path -LiteralPath $verSrc) { Copy-Item -LiteralPath $verSrc -Destination (Join-Path $Prefix 'version') -Force }
Say "[3/6] windows launcher: $cmdFile"

if ($NoPath) { Say "[4/6] PATH unchanged (-NoPath)" }
else { Add-PathEntry $BinDir; Say "[4/6] user PATH += $BinDir" }

& wsl -e "$wslBin/loment" version
if ($LASTEXITCODE -ne 0) { throw "smoke test failed: 'loment version' exit $LASTEXITCODE" }
& wsl -e "$wslBin/loment" ir "$wslShare/examples/user_hello.lomt" > $null
if ($LASTEXITCODE -ne 0) { throw "smoke test failed: compiling user_hello.lomt" }
Say "[5/6] smoke test ok (version + compile)"

if ($NoFileType) { Say "[6/6] file type registration skipped (-NoFileType)"; exit 0 }
$editor = Find-Editor
if (-not $editor) {
    Say "[6/6] no editor found (VS Code) -- file type registration skipped."
    Say "      re-run with VS Code installed, or use tools/loment_filetype.py --register"
    exit 0
}
Register-FileType -Editor $editor -Icon $icon
Say ""
Say "installed. Try:  loment version"
'''

INSTALL_CMD = r'''@echo off
rem Loment @DISPLAY@ installer entry point. Works in BOTH layouts:
rem   * self-extracting setup.exe  -> payload.zip sits next to this file: unpack it first
rem   * plain .zip                 -> this directory IS the payload
rem Double-clicking this file installs with defaults; see README.md for the switches
rem (-Prefix, -WslDir, -NoPath, -NoFileType, -DryRun, -Uninstall).
setlocal
set HERE=%~dp0
set PSARGS=
if exist "%HERE%payload.zip" set PSARGS=-PayloadZip "%HERE%payload.zip"
powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%install.ps1" %PSARGS% %*
if errorlevel 1 (
  echo.
  echo install failed. See README.md, or run it manually:
  echo   powershell -ExecutionPolicy Bypass -File "%HERE%install.ps1" %PSARGS%
  pause
  exit /b 1
)
rem A double-click passes no arguments -- keep the window open so the result stays visible.
if "%~1"=="" (
  echo.
  pause
)
'''

README_MD = """# Loment {DISPLAY}

版本 `{VERSION}`。这是一份**自包含**的 Loment 工具链发行包：包里**没有 Python** ——
四个可执行文件都是自举产物（种子 + clang），构建与安装的全部细节见仓库 `docs/162`。

## 包内容

| 文件 | 作用 |
| --- | --- |
| `bin/loment-driver` | 编译器（装载 → 检查 → 发射 LLVM IR）；自举链的 stage1 |
| `bin/loment-lsp` | 语言服务（补全/跳转/诊断/`--check`），stdio 上的 LSP |
| `bin/loment-fmt` | 格式化器（与 Python 版逐字节相同，docs/159） |
| `bin/loment-doc` | API 文档生成器 |
| `bin/loment` | 启动器（下面那些子命令） |
| `share/loment/seed.ll` | 自举种子：只用 clang 就能从它重建整套工具链 |
| `share/loment/examples/user_hello.lomt` | 示例程序（用 syscall 打印） |

## 安装（三种方式，装出来一样）

**① 命令安装 · Linux / WSL**

```sh
tar xzf loment-{VERSION}-linux-x64.tar.gz
cd loment-{VERSION}-linux-x64
sh install.sh                        # 默认装到 ~/.local
sh install.sh --prefix /opt/loment   # 换前缀
sh install.sh --uninstall
```

**② 命令安装 · Windows / PowerShell**

```powershell
Expand-Archive loment-{VERSION}-windows-x64.zip -DestinationPath .
cd loment-{VERSION}-windows-x64
powershell -ExecutionPolicy Bypass -File install.ps1
# 可选: -Prefix D:\\Loment  -WslDir /home/me/.local/share/loment
#       -NoPath  -NoFileType  -DryRun  -Uninstall
```

也可以直接**双击 `install.cmd`**（按默认参数装；`install.cmd` 在 zip 布局与自解压布局里都能用）。
遇到"被策略阻止"时用上面那条 `-ExecutionPolicy Bypass` 的命令。

**③ 安装包安装 · Windows 双击**

```text
loment-{VERSION}-windows-x64-setup.exe
```

自解压安装包（用 Windows 自带的 `iexpress` 做，不引第三方工具）：双击即装 ——
校验 SHA256SUMS → 把工具链拷进 WSL → 写 `loment.cmd` → 加用户 PATH →
装 `.lomt`/`.lom` 文件类型 → 冒烟测试。**未签名**，SmartScreen 会提示"未知发布者"，
选"更多信息 → 仍要运行"。

## 用法

```sh
loment version                     # 版本
loment ir demo.lomt                # 编译到 LLVM IR（stdout）
loment check demo.lomt             # 只做检查（不打印 IR）
loment run demo.lomt               # 编译 + 链接 + 运行
loment build demo.lomt -o demo     # 只出可执行文件
loment fmt demo.lomt               # 格式化
loment doc demo.lomt               # 生成 API 文档
loment lsp                         # 语言服务（编辑器用）
```

**前置条件**：`ir`/`check`/`fmt`/`doc`/`lsp` 只需要本包；`run`/`build` 还需要 **clang**
（地基语言；`-nostdlib` 直接链成 Linux ELF）。没有 clang 会明确报错，不会静默失败。

**Windows 说明**：工具链是 Linux ELF，所以装在 **WSL** 里；Windows 侧只有一个
`loment.cmd` 转发到 WSL。传进去的 `D:\\...` 路径由启动器自己转成 `/mnt/d/...`。

## 撤销

```sh
sh install.sh --uninstall                  # Linux / WSL
powershell -File install.ps1 -Uninstall    # Windows（同时清 PATH 与文件类型）
```
"""


# ------------------------------------------------------------------ 自举构建

def _clang() -> str:
    return shutil.which("clang") or r"C:\Program Files\LLVM\bin\clang.exe"


def _link(ir: Path, out: Path) -> None:
    """IR -> x86_64 Linux ELF (无 libc, `_start` 即入口) —— 与 p8 判据同一套参数。"""
    r = subprocess.run([_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib",
                        "-ffreestanding", "-static", "-fuse-ld=lld",
                        "-o", str(out), str(ir)],
                       capture_output=True, text=True, shell=False, cwd=str(ROOT))
    if r.returncode != 0:
        raise SystemExit(f"link failed ({out.name}): {r.stderr[-400:]}")


def _wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    return "/mnt/" + s[0].lower() + s[2:]


def build_stage1() -> Path:
    """clang(种子) -> stage1（种子是 driver 的定点, 所以 stage1 就是驱动自身）。"""
    if not SEED.exists():
        raise SystemExit(f"missing seed {SEED.relative_to(ROOT)} (docs/159)")
    STAGE.mkdir(parents=True, exist_ok=True)
    stage1 = STAGE / "stage1.elf"
    _link(SEED, stage1)
    return stage1


def emit_ir(stage1: Path, entry: str) -> Path:
    """用 stage1 编译 entry → IR 落盘（走文件不走管道, 免得编码/换行被中间层动过）。"""
    STAGE.mkdir(parents=True, exist_ok=True)
    out = STAGE / (Path(entry).stem + ".ll")
    tmp = "/tmp/loment_dist_stage1"
    # rm -f 先删: 目标名固定, 上一次刚退出的进程可能还占着 inode (Text file busy)
    script = (f"rm -f {tmp} && cp {_wsl_path(stage1)} {tmp} && chmod 755 {tmp} && "
              f"cd {_wsl_path(ROOT)} && {tmp} {entry} > {_wsl_path(out)}")
    r = subprocess.run(["wsl", "-e", "bash", "-lc", script], capture_output=True,
                       text=True, shell=False, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not out.exists():
        raise SystemExit(f"stage1 failed on {entry}: {r.stderr[-400:]}")
    return out


def build_tools(only: set[str] | None) -> dict[str, bytes]:
    """名字 -> ELF 字节。only 给名字就只构建那几个。"""
    stage1 = build_stage1()
    out: dict[str, bytes] = {}
    for name, entry in TOOLS:
        if only and name not in only:
            continue
        ir = emit_ir(stage1, entry)
        elf = STAGE / f"{name}.elf"
        _link(ir, elf)
        out[name] = elf.read_bytes()
        print(f"  [{name}] {len(out[name])} 字节")
    return out


# ------------------------------------------------------------------ payload

def _read(p: str) -> bytes:
    return (ROOT / p).read_bytes()


def _git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True,
                       shell=False, encoding="utf-8", errors="replace")
    return r.stdout.strip() if r.returncode == 0 else ""


def version_text() -> str:
    sha = _git("rev-parse", "--short", "HEAD") or "unknown"
    date = _git("show", "-s", "--format=%cs", "HEAD") or "unknown"
    return f"Loment {DISPLAY} ({VER})\ncommit {sha} ({date})\n"


def payload(kind: str, bins: dict[str, bytes]) -> dict[str, tuple[bytes, int]]:
    """kind = linux | windows。归档内相对路径 -> (字节, 权限)。"""
    files: dict[str, tuple[bytes, int]] = {}
    for name, blob in bins.items():
        files[f"bin/{name}"] = (blob, 0o755)
    files["bin/loment"] = (_subst(LAUNCHER_SH).encode("ascii"), 0o755)
    files["share/loment/version"] = (version_text().encode(), 0o644)
    files["share/loment/seed.ll"] = (_read("loment/build/selfhost_driver.ll"), 0o644)
    files[f"share/loment/examples/{Path(EXAMPLE).name}"] = (_read(EXAMPLE), 0o644)
    files["README.md"] = (_subst(README_MD).encode(), 0o644)
    files["LICENSE"] = (_read(LICENSE), 0o644)
    if kind == "linux":
        files["install.sh"] = (_subst(INSTALL_SH).encode("ascii"), 0o755)
    else:
        files["bin/loment.ico"] = (_read(ICON), 0o644)
        files["install.ps1"] = (_crlf(_subst(INSTALL_PS1)).encode("ascii"), 0o644)
        files["install.cmd"] = (_crlf(_subst(INSTALL_CMD)).encode("ascii"), 0o644)
    # 随包校验和 (安装脚本第一步就校它; 覆盖上面所有文件, 不含自己)
    files["SHA256SUMS"] = (sums_text(files).encode(), 0o644)
    return files


def _subst(text: str) -> str:
    return text.replace("@DISPLAY@", DISPLAY).replace("@VERSION@", VER)


def _crlf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def sums_text(files: dict[str, tuple[bytes, int]]) -> str:
    return "".join(f"{hashlib.sha256(files[k][0]).hexdigest()}  {k}\n" for k in sorted(files))


def _zip(prefix: str, files: dict[str, tuple[bytes, int]]) -> bytes:
    """确定性 zip: 固定时间戳 + 排序 + unix 权限位 (同输入两次构建字节相同)。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in sorted(files):
            data, mode = files[rel]
            name = f"{prefix}/{rel}" if prefix else rel
            zi = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.create_system = 3
            zi.external_attr = (mode & 0xFFFF) << 16
            zf.writestr(zi, data)
    return buf.getvalue()


def _tar_gz(prefix: str, files: dict[str, tuple[bytes, int]]) -> bytes:
    """确定性 tar.gz: mtime=0 + 稳定 uid/gid/uname (gzip 头也不带时间)。"""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as tf:
        for rel in sorted(files):
            data, mode = files[rel]
            ti = tarfile.TarInfo(f"{prefix}/{rel}")
            ti.size, ti.mtime, ti.mode = len(data), 0, mode
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            ti.type = tarfile.REGTYPE
            tf.addfile(ti, io.BytesIO(data))
    return gzip.compress(raw.getvalue(), mtime=0)


def emit_exe(zip_bytes: bytes, target: Path) -> bool:
    """iexpress (Windows 自带) 打自解压安装包: payload.zip + install.ps1 + install.cmd。"""
    src = STAGE / "exe-payload"
    if src.exists():
        shutil.rmtree(src)
    src.mkdir(parents=True)
    (src / "payload.zip").write_bytes(zip_bytes)
    (src / "install.ps1").write_bytes(_crlf(_subst(INSTALL_PS1)).encode("ascii"))
    (src / "install.cmd").write_bytes(_crlf(_subst(INSTALL_CMD)).encode("ascii"))
    names = sorted(p.name for p in src.iterdir())
    strings = "".join(f'FILE{i}="{n}"\n' for i, n in enumerate(names))
    refs = "".join(f"%FILE{i}%=\n" for i in range(len(names)))
    sed = src / "loment.sed"
    sed.write_text(
        "[Version]\nClass=IEXPRESS\nSEDVersion=3\n[Options]\nPackagePurpose=InstallApp\n"
        "ShowInstallProgramWindow=1\nHideExtractAnimation=1\nUseLongFileName=1\n"
        "InsideCompressed=0\nCAB_FixedSize=0\nCAB_ResvCodeSigning=0\nRebootMode=N\n"
        "InstallPrompt=\nDisplayLicense=\nFinishMessage=\n"
        f"TargetName={target.resolve()}\nFriendlyName=Loment {DISPLAY} Setup\n"
        "AppLaunched=cmd.exe /c install.cmd\nPostInstallCmd=<None>\n"
        "AdminQuietInstCmd=\nUserQuietInstCmd=\nSourceFiles=SourceFiles\n"
        "[Strings]\n" + strings +
        f"[SourceFiles]\nSourceFiles0={src.resolve()}\\\n[SourceFiles0]\n" + refs,
        encoding="ascii", newline="\r\n")
    ie = shutil.which("iexpress") or r"C:\Windows\System32\iexpress.exe"
    if not Path(ie).exists():
        print("  [setup.exe] SKIP: iexpress 不存在")
        return False
    r = subprocess.run([ie, "/N", str(sed)], capture_output=True, text=True,
                       shell=False, cwd=str(src))
    if not (target.exists() and target.stat().st_size > 0):
        print(f"  [setup.exe] iexpress 失败 rc={r.returncode}: "
              f"{(r.stdout or r.stderr)[-300:]}")
        return False
    return True


# ------------------------------------------------------------------ 入口

def emit(only: set[str] | None, want_exe: bool, out_dir: Path | None = None) -> int:
    out = out_dir or OUT
    bins = build_tools(only)
    lin = payload("linux", bins)
    win = payload("windows", bins)
    out.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []

    tar = out / f"loment-{VER}-linux-x64.tar.gz"
    tar.write_bytes(_tar_gz(f"loment-{VER}-linux-x64", lin))
    made.append(tar)
    print(f"  [{tar.name}] {tar.stat().st_size} 字节, {len(lin)} 个文件")

    zbytes = _zip(f"loment-{VER}-windows-x64", win)
    zipf = out / f"loment-{VER}-windows-x64.zip"
    zipf.write_bytes(zbytes)
    made.append(zipf)
    print(f"  [{zipf.name}] {len(zbytes)} 字节, {len(win)} 个文件")

    if want_exe:
        exe = out / f"loment-{VER}-windows-x64-setup.exe"
        if exe.exists():
            _unlink_retry(exe)
        if emit_exe(_zip("", win), exe):
            made.append(exe)
            print(f"  [{exe.name}] {exe.stat().st_size} 字节 (自解压, 未签名)")

    sums = write_sums(out)
    print(f"  [{sums.name}] {len(made)} 行")
    return 0


def _unlink_retry(p: Path, tries: int = 5) -> None:
    """删文件带重试: 刚签过名的 exe 常被 Defender 扫一下, 那几百毫秒里删会 WinError 5
    (2026-09-12 撞到过一次, 症状是"重建失败"但手工再删就没了)。"""
    import time
    for i in range(tries):
        try:
            p.unlink()
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(1.0)


def write_sums(out_dir: Path | None = None) -> Path:
    """重算产物目录的 SHA256SUMS (排除清单自身、分离签名 .sig 与公钥证书 .pem)。

    单独抽出来是因为**签名会改 PE 的字节**: 签名之后必须重算清单, 否则清单对不上产物。
    """
    out = out_dir or OUT
    keep_out = ("SHA256SUMS", "verify.sh", "verify.ps1", "FINGERPRINT")
    arts = [p for p in sorted(out.iterdir())
            if p.is_file() and p.name not in keep_out
            and not p.name.endswith((".sig", ".pem", ".asc"))]
    sums = out / "SHA256SUMS"
    sums.write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n"
                            for p in arts), encoding="utf-8", newline="\n")
    return sums


def check(out_dir: Path | None = None) -> int:
    out = out_dir or OUT
    sums = out / "SHA256SUMS"
    if not sums.exists():
        print(f"[ERR] {sums.relative_to(ROOT)} 缺失 (先跑 --emit)")
        return 1
    bad = []
    n = 0
    for line in sums.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        want, rel = line.split("  ", 1)
        p = out / rel
        n += 1
        got = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "(缺失)"
        if got != want:
            bad.append(f"{rel}: {got} != {want}")
    for b in bad:
        print(f"[DIFF] {b}")
    print(f"loment_dist: {len(bad)} 处不一致" if bad
          else f"loment_dist: {n}/{n} 产物与 SHA256SUMS 一致")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_dist")
    ap.add_argument("--emit", action="store_true", help="构建并打包")
    ap.add_argument("--check", action="store_true", help="校验现有产物与 SHA256SUMS")
    ap.add_argument("--list", action="store_true", help="只列会打进去的文件")
    ap.add_argument("--only", metavar="NAME[,NAME]", help="只构建这些工具 (driver,lsp,fmt,doc)")
    ap.add_argument("--no-exe", action="store_true", help="跳过 Windows 自解压安装包")
    ap.add_argument("--out", metavar="DIR", help="产物目录 (默认 loment/dist)")
    a = ap.parse_args(argv)

    if a.list:
        for rel in sorted(payload("linux", {})):
            print(f"  {rel}")
        return 0
    out_dir = Path(a.out) if a.out else None
    if a.check:
        return check(out_dir)
    if a.emit:
        # --only 允许短名 (driver/lsp/fmt/doc) —— 名字对齐工具名 loment-<x>
        only = None
        if a.only:
            only = {(t if t.startswith("loment-") else f"loment-{t}")
                    for t in a.only.split(",") if t}
            known = {n for n, _e in TOOLS}
            if only - known:
                print(f"[ERR] 未知工具: {sorted(only - known)}", file=sys.stderr)
                return 2
        return emit(only, not a.no_exe, out_dir)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
