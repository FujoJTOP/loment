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
# 包里**没有 Python**: 五个可执行文件都是自举产物 (种子 → stage1 → IR → 链接), 构建与链接
# 都不需要 clang/WSL 才能装。构建期需要 Python 的只有这个打包工具本身 (仓库工具链, 不进包)。
#
#   python tools/loment_dist.py --emit                        # 全部（本机原生后端，无 clang/WSL）
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
    # `loment build/run` 的链接器 —— 自举侧的 lomelf 镜像。有它之后 **包里不再需要 clang**:
    # 存出来的产物本来就是目标平台自己的格式（Linux 出 ELF / Windows 出 PE）。
    ("loment-lomelf", "loment/tools/lomelf.lomt"),
]
EXAMPLE = "loment/examples/user_hello.lomt"
ICON = "editors/loment.ico"
LICENSE = "LICENSE"
# 随包的 agent skill —— 装完 Loment, AI agent 读它就会写 Loment。
# **自足**: 内建函数表/语法/错误码/包内命令都在里面, 不引用仓库路径。原样拷进包,
# 不做 @VERSION@ 替换 —— 这样"包里的那份 == 仓库里的那份"是可判据的。
SKILL = ".claude/skills/loment/SKILL.md"

#: 纯文本脚本一律 ASCII: Windows PowerShell 5.1 用 ANSI 读无 BOM 的 .ps1, 非 ASCII 会变乱码
#: 并连带把后续行解析坏 (docs/157 §3.4 踩过)。中文说明在 README.md 与 docs/162 里。
LAUNCHER_SH = r'''#!/usr/bin/env bash
# Loment launcher (@DISPLAY@, @VERSION@). Installed by install.sh / install.ps1.
# No Python, no clang: everything here is the self-hosted toolchain.
set -u

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
share=$(CDPATH= cd -- "$here/../share/loment" && pwd)

to_posix() {
    case "$1" in
        [A-Za-z]:[\\/]*|\\) command -v wslpath >/dev/null 2>&1 && wslpath -a "$1" || printf '%s' "$1" ;;
        *) printf '%s' "$1" ;;
    esac
}

find_lomelf() {
    [ -x "$here/loment-lomelf" ] && { printf '%s' "$here/loment-lomelf"; return 0; }
    return 1
}

# NOTE: this launcher is packed as ASCII (PowerShell 5.1 reads BOM-less files as ANSI) -
# keep every comment here in English.

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
  loment skill [--print]      print the AI-agent guide (path, or the whole text)
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
    # The guide for AI agents, reachable WITHOUT any tool-specific directory convention:
    # an agent that meets a new language runs its CLI first, so this is the universal hook.
    # `--print` needs no file access at all.
    skill)
        shift
        case "${1:-}" in
            --print)
                [ -f "$share/skill/SKILL.md" ] ||
                    { echo "loment: this package does not include the guide" >&2; exit 3; }
                cat "$share/skill/SKILL.md" ;;
            "")
                echo "$share/skill/SKILL.md" ;;
            *)
                echo "loment: skill takes no argument except --print" >&2; exit 2 ;;
        esac ;;
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
        need "$here/loment-lomelf" loment-lomelf
        tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
        "$here/loment-driver" "$(to_posix "$src")" > "$tmp/a.ll" || exit 1
        if [ "$mode" = run ]; then out="$tmp/a.bin"; fi
        [ -n "$out" ] || out="${src%.lomt}"
        # link with the self-hosted lomelf - the package no longer needs clang
        "$here/loment-lomelf" "$tmp/a.ll" "$out" || exit 1
        if [ "$mode" = run ]; then
            chmod 755 "$out"
            "$out"
        else
            echo "loment: $out"
        fi ;;
    help|-h|--help)
        usage ;;
    *)
        usage >&2; exit 2 ;;
esac
'''

#: Windows 的原生启动器。**直接调本机的 .exe，不再往 WSL 转发** —— 包里的工具本来就是 PE。
#: 必须 ASCII（PowerShell 5.1 按 ANSI 读无 BOM 的脚本），所以注释一律英文。
LAUNCHER_CMD = r'''@echo off
rem Loment @DISPLAY@ launcher (Windows, native toolchain).
setlocal
set "here=%~dp0"
set "share=%here%..\share\loment"
set "cmd=%~1"
if "%cmd%"=="" goto usage
if "%cmd%"=="help" goto usage
if "%cmd%"=="-h" goto usage
if "%cmd%"=="--help" goto usage
if "%cmd%"=="version" goto version
if "%cmd%"=="-v" goto version
if "%cmd%"=="--version" goto version
if "%cmd%"=="ir" goto ir
if "%cmd%"=="check" goto check
if "%cmd%"=="fmt" goto fmt
if "%cmd%"=="doc" goto doc
if "%cmd%"=="lsp" goto lsp
if "%cmd%"=="skill" goto skill
if "%cmd%"=="build" goto build
if "%cmd%"=="run" goto run
goto usage

:version
type "%share%\version"
exit /b 0

:ir
if "%~2"=="" goto usage
"%here%loment-driver.exe" "%~2"
exit /b %ERRORLEVEL%

:check
if "%~2"=="" goto usage
"%here%loment-driver.exe" "%~2" >nul
exit /b %ERRORLEVEL%

:fmt
if "%~2"=="" goto usage
"%here%loment-fmt.exe" "%~2"
exit /b %ERRORLEVEL%

:doc
if "%~2"=="" goto usage
"%here%loment-doc.exe" "%~2"
exit /b %ERRORLEVEL%

:lsp
"%here%loment-lsp.exe" %2 %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%

rem The guide for AI agents, reachable WITHOUT any tool-specific directory convention: an
rem agent that meets a new language runs its CLI first, so this is the universal hook.
rem --print needs no file access at all.
:skill
if "%~2"=="--print" goto skill_print
rem %%~fI expands to the fully-qualified path (drops the .. in %share%)
for %%I in ("%share%\skill\SKILL.md") do echo %%~fI
exit /b 0

:skill_print
if not exist "%share%\skill\SKILL.md" (
  echo loment: this package does not include the guide 1>&2
  exit /b 3
)
type "%share%\skill\SKILL.md"
exit /b %ERRORLEVEL%

:build
set "src=%~2"
if "%src%"=="" goto usage
set "out="
if /I "%~3"=="-o" set "out=%~4"
if /I "%~3"=="--out" set "out=%~4"
if "%out%"=="" set "out=%src:.lomt=%"
set "tmp=%TEMP%\loment-b%RANDOM%%RANDOM%"
mkdir "%tmp%" >nul 2>nul
"%here%loment-driver.exe" "%src%" > "%tmp%\a.ll"
if errorlevel 1 goto fail
"%here%loment-lomelf.exe" "%tmp%\a.ll" "%out%.exe"
if errorlevel 1 goto fail
goto done

:run
set "src=%~2"
if "%src%"=="" goto usage
set "out=%TEMP%\loment-r%RANDOM%%RANDOM%"
set "tmp=%TEMP%\loment-r%RANDOM%%RANDOM%"
mkdir "%tmp%" >nul 2>nul
"%here%loment-driver.exe" "%src%" > "%tmp%\a.ll"
if errorlevel 1 goto fail
"%here%loment-lomelf.exe" "%tmp%\a.ll" "%tmp%\a.exe"
if errorlevel 1 goto fail
"%tmp%\a.exe"
set "rc=%ERRORLEVEL%"
del /q "%tmp%\a.ll" "%tmp%\a.exe" >nul 2>nul
rmdir "%tmp%" >nul 2>nul
exit /b %rc%

:done
del /q "%tmp%\a.ll" >nul 2>nul
rmdir "%tmp%" >nul 2>nul
echo loment: %out%.exe
exit /b 0

:fail
del /q "%tmp%\a.ll" "%tmp%\a.exe" >nul 2>nul
rmdir "%tmp%" >nul 2>nul
exit /b 1

:usage
echo Loment @DISPLAY@  (@VERSION@)
echo   loment version              print version
echo   loment ir FILE              compile to LLVM IR on stdout
echo   loment check FILE           check only (diagnostics on stderr, IR discarded)
echo   loment build FILE [-o OUT]  compile and link to an executable
echo   loment run FILE             compile, link and run
echo   loment fmt FILE             format (prints the formatted text)
echo   loment doc FILE             write API docs to stdout
echo   loment lsp                  language server over stdio
echo   loment skill [--print]      print the AI-agent guide (path, or the whole text)
exit /b 2
'''

INSTALL_SH = r'''#!/bin/sh
# Loment @DISPLAY@ installer (Linux / WSL). No Python, no network.
#   sh install.sh [--prefix DIR] [--no-path] [--no-skill]   default prefix: $HOME/.local
#   sh install.sh --uninstall [--prefix DIR]
# --no-skill: do not install the agent skill into ~/.claude/skills/loment
set -eu

prefix=${PREFIX:-$HOME/.local}
no_path=0
no_skill=0
uninstall=0
while [ $# -gt 0 ]; do
    case "$1" in
        --prefix) prefix=${2:-}; shift 2 ;;
        --prefix=*) prefix=${1#*=}; shift ;;
        --no-path) no_path=1; shift ;;
        --no-skill) no_skill=1; shift ;;
        --uninstall) uninstall=1; shift ;;
        -h|--help) sed -n '2,4p' "$0"; exit 0 ;;
        *) echo "install: unknown option $1" >&2; exit 2 ;;
    esac
done
[ -n "$prefix" ] || { echo "install: empty --prefix" >&2; exit 2; }
src=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ "$uninstall" = 1 ]; then
    rm -f "$prefix/bin/loment" "$prefix/bin/loment-driver" "$prefix/bin/loment-lsp" \
          "$prefix/bin/loment-fmt" "$prefix/bin/loment-doc" "$prefix/bin/loment-lomelf"
    rm -rf "$prefix/share/loment"
    # only what this installer created -- never ~/.claude/skills or AGENTS.md at large
    if [ "$no_skill" != 1 ]; then
        rm -rf "$HOME/.claude/skills/loment"
        if [ -f "$HOME/.codex/AGENTS.md" ]; then
            sed -i '/<!-- loment:begin -->/,/<!-- loment:end -->/d' "$HOME/.codex/AGENTS.md" 2>/dev/null || true
        fi
    fi
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

# Ship the self-contained agent skill into the user-level Claude skills dir, so a coding
# agent in ANY project can read it. Skipped when ~/.claude is absent (then it just stays
# in the package). --no-skill to skip.
skill_src="$prefix/share/loment/skill/SKILL.md"
mark_b='<!-- loment:begin -->'
mark_e='<!-- loment:end -->'
if [ "$no_skill" = 1 ]; then
    echo "install: agent skill not installed (--no-skill); kept at $skill_src"
else
    hits=""
    if [ -d "$HOME/.claude" ]; then
        mkdir -p "$HOME/.claude/skills/loment"
        cp -f "$skill_src" "$HOME/.claude/skills/loment/SKILL.md"
        hits="claude"
    fi
    # Codex CLI reads ~/.codex/AGENTS.md as global instructions. Marker-delimited so a
    # re-install is idempotent and --uninstall takes back exactly this block.
    if [ -d "$HOME/.codex" ]; then
        agents="$HOME/.codex/AGENTS.md"
        [ -f "$agents" ] || : > "$agents"
        # Drop a previous block (same markers), then append. On uninstall we delete only
        # the marked range: a leftover blank line is preferable to guessing which blank
        # lines are the user's -- never touch bytes we did not write.
        sed -i '/<!-- loment:begin -->/,/<!-- loment:end -->/d' "$agents" 2>/dev/null || true
        # If the file does not end with a newline, add one so the marker is not glued
        # onto the user's last line.
        if [ -s "$agents" ] && [ -n "$(tail -c 1 "$agents")" ]; then printf '\n' >> "$agents"; fi
        {
            echo "$mark_b"
            echo "## Loment"
            echo "Before writing or changing a Loment program (\`.lomt\`), read the guide:"
            echo "$skill_src"
            echo "It is self-contained: builtins, syntax, error codes (E1-E17), and the toolchain commands."
            echo "$mark_e"
        } >> "$agents"
        hits="${hits:+$hits, }codex"
    fi
    if [ -n "$hits" ]; then
        echo "install: agent skill -> $hits (one guide at $skill_src)"
    else
        # No tool-specific dir to hook? The CLI is the hook.
        echo "install: no known agent dir here -- the guide is still reachable:"
        echo "install:   loment skill --print   (any agent: it runs the CLI)"
    fi
    echo "install:   a plain copy lives at $skill_src"
    echo "install:   for shells: export LOMENT_SKILL=\"$skill_src\""
fi

case ":${PATH}:" in
    *":$prefix/bin:"*) ;;
    *) [ "$no_path" = 1 ] || {
        echo "install: put $prefix/bin on PATH, e.g."
        echo "         echo 'export PATH=\"$prefix/bin:\$PATH\"' >> ~/.profile" >&2 ; } ;;
esac
'''

INSTALL_PS1 = r'''# install.ps1 - Loment @DISPLAY@ installer for Windows (native toolchain).
#
# ASCII only (docs/157 3.4: PS 5.1 reads a BOM-less .ps1 as ANSI).
#   powershell -ExecutionPolicy Bypass -File install.ps1
#   powershell -File install.ps1 -Prefix D:\Loment
#   powershell -File install.ps1 -DryRun          # print the plan, change nothing
#   powershell -File install.ps1 -Uninstall
#
# The package ships native PE binaries - no WSL, no clang. Everything lands under
# $Prefix, and bin/loment.cmd calls those .exe files directly.

[CmdletBinding()]
param(
    [string]$Prefix = '',
    [string]$PayloadDir = '',
    [string]$PayloadZip = '',
    [switch]$NoPath,
    [switch]$NoFileType,
    [switch]$NoSkill,
    [switch]$Uninstall,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($Prefix -eq '') { $Prefix = Join-Path $env:LOCALAPPDATA 'Loment' }
$BinDir = Join-Path $Prefix 'bin'

function Say([string]$m) { Write-Host $m }

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

if ($Uninstall) {
    if ($DryRun) { Say "[dry-run] would remove $Prefix, the file-type keys and the agent skill"; exit 0 }
    Remove-Item -LiteralPath $Prefix -Recurse -Force -ErrorAction SilentlyContinue
    if (-not $NoPath) { Remove-PathEntry $BinDir }
    if (-not $NoFileType) { Unregister-FileType }
    if (-not $NoSkill) {
        # Only what this installer created -- never a skills dir / AGENTS.md at large.
        Remove-Item -LiteralPath (Join-Path $env:USERPROFILE '.claude\skills\loment') `
                    -Recurse -Force -ErrorAction SilentlyContinue
        $agents = Join-Path $env:USERPROFILE '.codex\AGENTS.md'
        if (Test-Path -LiteralPath $agents) {
            $t = Get-Content -LiteralPath $agents -Raw
            if ($null -eq $t) { $t = '' }
            $t = [regex]::Replace($t, '(?s)\r?\n?<!-- loment:begin -->.*?<!-- loment:end -->\r?\n?', '')
            [System.IO.File]::WriteAllText($agents, $t, (New-Object System.Text.UTF8Encoding($false)))
        }
        [Environment]::SetEnvironmentVariable('LOMENT_SKILL', $null, 'User')
    }
    Say "uninstalled"
    exit 0
}

$Payload = Resolve-Payload
Say "Loment @DISPLAY@ (@VERSION@)"
Say "  prefix:  $Prefix"
Say "  payload: $Payload"

if ($DryRun) {
    Say "[dry-run] would: verify SHA256SUMS; copy bin/ + share/ under $Prefix;"
    Say "[dry-run]       add $BinDir to user PATH; register .lomt/.lom"
    exit 0
}

Verify-Sums $Payload

New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
foreach ($f in Get-ChildItem -LiteralPath (Join-Path $Payload 'bin') -File) {
    Copy-Item -LiteralPath $f.FullName -Destination (Join-Path $BinDir $f.Name) -Force
}
$shareDir = Join-Path $Prefix 'share\loment'
New-Item -ItemType Directory -Path (Join-Path $shareDir 'examples') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $shareDir 'skill') -Force | Out-Null
foreach ($rel in @('share/loment/version', 'share/loment/seed.ll',
                   'share/loment/examples/user_hello.lomt',
                   'share/loment/skill/SKILL.md')) {
    $f = Join-Path $Payload $rel.Replace('/', '\')
    if (Test-Path -LiteralPath $f) {
        Copy-Item -LiteralPath $f -Destination (Join-Path $Prefix $rel.Replace('/', '\')) -Force
    }
}
foreach ($rel in @('README.md', 'LICENSE')) {
    $f = Join-Path $Payload $rel
    if (Test-Path -LiteralPath $f) { Copy-Item -LiteralPath $f -Destination (Join-Path $Prefix $rel) -Force }
}
Say "[2/6] toolchain -> $BinDir"

if ($NoPath) { Say "[3/6] PATH unchanged (-NoPath)" }
else { Add-PathEntry $BinDir; Say "[3/6] user PATH += $BinDir" }

& (Join-Path $BinDir 'loment.cmd') version
if ($LASTEXITCODE -ne 0) { throw "smoke test failed: 'loment version' exit $LASTEXITCODE" }
& (Join-Path $BinDir 'loment.cmd') ir (Join-Path $shareDir 'examples\user_hello.lomt') > $null
if ($LASTEXITCODE -ne 0) { throw "smoke test failed: compiling user_hello.lomt" }
Say "[5/6] smoke test ok (version + compile)"

# --- agent skill: hand it to whatever agent is here, NOT just Claude --------------------
# There is no OS-wide way to push a skill into an arbitrary LLM: every tool reads its own
# path. So we write a POINTER into the user-level file that each tool already uses, and
# only when that tool's directory already exists -- we never create another tool's config
# dir. The guide itself stays in ONE place (the package). -NoSkill skips all of this.
$skillSrc = Join-Path $shareDir 'skill\SKILL.md'
$markB = '<!-- loment:begin -->'
$markE = '<!-- loment:end -->'
$skillHits = @()
if ($NoSkill) {
    Say "[5b/6] agent skill not installed (-NoSkill); kept at $skillSrc"
} else {
    # Claude Code: user-level skills dir, auto-discovered.
    $claudeDir = Join-Path $env:USERPROFILE '.claude'
    if (Test-Path -LiteralPath $claudeDir) {
        $skillDir = Join-Path $claudeDir 'skills\loment'
        New-Item -ItemType Directory -Path $skillDir -Force | Out-Null
        Copy-Item -LiteralPath $skillSrc -Destination (Join-Path $skillDir 'SKILL.md') -Force
        $skillHits += "claude"
    }
    # Codex CLI: global instructions at ~/.codex/AGENTS.md. Marker-delimited so re-install
    # is idempotent and uninstall removes exactly this block, leaving the rest untouched.
    $codexDir = Join-Path $env:USERPROFILE '.codex'
    if (Test-Path -LiteralPath $codexDir) {
        $agents = Join-Path $codexDir 'AGENTS.md'
        $prev = ''
        if (Test-Path -LiteralPath $agents) { $prev = Get-Content -LiteralPath $agents -Raw }
        if ($null -eq $prev) { $prev = '' }
        $prev = [regex]::Replace($prev, '(?s)<!-- loment:begin -->.*?<!-- loment:end -->\r?\n?', '')
        $block = $markB + "`r`n" +
                 "## Loment`r`n" +
                 "Before writing or changing a Loment program (``.lomt``), read the guide:`r`n" +
                 "$skillSrc`r`n" +
                 "It is self-contained: builtins, syntax, error codes (E1-E17), and the toolchain commands.`r`n" +
                 $markE + "`r`n"
        # Append only: the file's original bytes are left untouched, so removing this
        # block again restores it byte for byte. (v1 trimmed -- the gate caught it.)
        $body = $prev
        if ($body -ne '') { $body += "`r`n" }
        [System.IO.File]::WriteAllText($agents, $body + $block, (New-Object System.Text.UTF8Encoding($false)))
        $skillHits += "codex"
    }
    # A user-level variable so anything can find the guide without knowing the prefix.
    # .NET writes HKCU\Environment and broadcasts WM_SETTINGCHANGE (new processes see it).
    [Environment]::SetEnvironmentVariable('LOMENT_SKILL', $skillSrc, 'User')
    if ($skillHits.Count -eq 0) {
        # No tool-specific dir to hook? The CLI is the hook: any agent runs it, so
        # "loment skill --print" needs no convention and no file access.
        Say "[5b/6] no known agent dir here -- the guide is still reachable:"
        Say "      loment skill --print     (any agent: run the CLI)"
        Say "      or paste into that agent's rules: read $skillSrc before writing Loment"
    } else {
        Say "[5b/6] agent skill -> $($skillHits -join ', ') (+ LOMENT_SKILL)"
    }
}

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
rem (-Prefix, -NoPath, -NoFileType, -NoSkill, -DryRun, -Uninstall).
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

版本 `{VERSION}`。这是一份**自包含**的 Loment 工具链发行包：包里**没有 Python**，也
**不需要 clang、不需要 WSL** —— 五个可执行文件都是自举产物，`build`/`run` 用包内的
`loment-lomelf` 在本机直接出 ELF/PE。构建与安装的全部细节见仓库 `docs/162`。

## 包内容

| 文件 | 作用 |
| --- | --- |
| `bin/loment-driver` | 编译器（装载 → 检查 → 发射 LLVM IR）；自举链的 stage1 |
| `bin/loment-lsp` | 语言服务（补全/跳转/诊断/`--check`），stdio 上的 LSP |
| `bin/loment-fmt` | 格式化器（与 Python 版逐字节相同，docs/159） |
| `bin/loment-doc` | API 文档生成器 |
| `bin/loment` | 启动器（下面那些子命令） |
| `bin/loment-lomelf` | 链接器：把 `.ll` 变成可执行文件（`build`/`run` 用它） |
| `share/loment/seed.ll` | 自举种子：只用 clang 就能从它重建整套工具链 |
| `share/loment/examples/user_hello.lomt` | 示例程序（用 syscall 打印） |
| `share/loment/skill/SKILL.md` | **给 AI agent 的 Loment 说明书**（见下） |

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
# 可选: -Prefix D:\\Loment
#       -NoPath  -NoFileType  -NoSkill  -DryRun  -Uninstall
```

也可以直接**双击 `install.cmd`**（按默认参数装；`install.cmd` 在 zip 布局与自解压布局里都能用）。
遇到"被策略阻止"时用上面那条 `-ExecutionPolicy Bypass` 的命令。

**③ 安装包安装 · Windows 双击**

```text
loment-{VERSION}-windows-x64-setup.exe
```

自解压安装包（用 Windows 自带的 `iexpress` 做，不引第三方工具）：双击即装 ——
校验 SHA256SUMS → 把工具链拷进安装前缀 → 写 `loment.cmd` → 加用户 PATH →
装 `.lomt`/`.lom` 文件类型 → 冒烟测试。**未签名**，SmartScreen 会提示"未知发布者"，
选"更多信息 → 仍要运行"。

## 让 AI agent 写 Loment

包里带一份**自足**的 skill（`share/loment/skill/SKILL.md`）：内建函数表、语法、
错误码表、以及这个包自己的命令，都在里面，不依赖源码仓库。

安装时它会同时被写进 **`~/.claude/skills/loment/`**（用户级），于是**任何工程**里的
Claude Code 都能读到它 —— 你只要说"用 Loment 写个程序"就行。不想装用 `-NoSkill`
（Linux: `--no-skill`）；没装 Claude 的话它会留在包里，把那个文件拷到
`<你的工程>/.claude/skills/loment/SKILL.md` 也一样。卸载时一并摘掉。

**既没有 Claude 也没有 Codex？** 那就不靠目录约定 —— **跑 CLI 就行**：

```sh
loment skill            # 打印指南路径
loment skill --print    # 直接把指南全文打到 stdout
```

`loment help` 的用法里也印了这一行，所以任何 agent 上手这门语言的第一条命令
（`loment --help`）就能看到入口 —— 与它是什么工具无关。

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

**前置条件**：包里就是目标平台自己的可执行文件，**没有任何外部依赖** —— 不需要 clang，
Windows 包也不需要 WSL。`run`/`build` 由包内的自举链接器 `loment-lomelf` 出产物：
Linux 出 ELF、Windows 出 PE。

**Windows 说明**：装到 `$Prefix`（默认 `%LOCALAPPDATA%\Loment`），`bin\loment.cmd`
直接调那些 `.exe`。

## 撤销

```sh
sh install.sh --uninstall                  # Linux / WSL
powershell -File install.ps1 -Uninstall    # Windows（同时清 PATH、文件类型与 agent skill）
```
"""


# ------------------------------------------------------------------ 自举构建

def _lomelf_link(ir_text: str, target: str) -> bytes:
    """IR -> 可执行文件。用仓库自己的原生后端（`tools/lomelf.py`），**不再经 clang**。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import lomelf
    fn = lomelf.compile_pe if target == "pe" else lomelf.compile_ll
    return fn(ir_text)[0]


def _host_target() -> str:
    return "pe" if sys.platform == "win32" else "elf"


def build_stage1() -> Path:
    """种子 -> stage1（种子是 driver 的定点, 所以 stage1 就是驱动自身）。

    stage1 按**本机**格式出：在 Windows 上出 PE，这样它直接就能跑，不必再去借 WSL。
    """
    if not SEED.exists():
        raise SystemExit(f"missing seed {SEED.relative_to(ROOT)} (docs/159)")
    STAGE.mkdir(parents=True, exist_ok=True)
    tgt = _host_target()
    stage1 = STAGE / ("stage1.exe" if tgt == "pe" else "stage1.elf")
    stage1.write_bytes(_lomelf_link(SEED.read_text(encoding="utf-8"), tgt))
    return stage1


def emit_ir(stage1: Path, entry: str) -> Path:
    """用 stage1 编译 entry → IR 落盘（**在本机直接跑**，不经 WSL）。"""
    STAGE.mkdir(parents=True, exist_ok=True)
    out = STAGE / (Path(entry).stem + ".ll")
    r = subprocess.run([str(stage1), entry], capture_output=True,
                       cwd=str(ROOT), shell=False)
    if r.returncode != 0 or not r.stdout:
        raise SystemExit(f"stage1 failed on {entry}: {r.stderr[-400:]!r}")
    out.write_bytes(r.stdout)
    return out


def build_tools(only: set[str] | None) -> dict[str, tuple[bytes, bytes]]:
    """名字 -> (Linux ELF 字节, Windows PE 字节)。

    IR 是**目标无关**的，所以只跑一次 stage1，然后同一份 IR 各链一遍 —— 两个平台的包
    都能从本机构建出来，不需要另一个平台、也不需要 WSL。
    """
    stage1 = build_stage1()
    out: dict[str, tuple[bytes, bytes]] = {}
    for name, entry in TOOLS:
        if only and name not in only:
            continue
        ir = emit_ir(stage1, entry)
        text = ir.read_text(encoding="utf-8")
        elf, pe = _lomelf_link(text, "elf"), _lomelf_link(text, "pe")
        out[name] = (elf, pe)
        print(f"  [{name}] elf {len(elf)} 字节 / pe {len(pe)} 字节")
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


def payload(kind: str, bins: dict[str, tuple[bytes, bytes]]) -> dict[str, tuple[bytes, int]]:
    """kind = linux | windows。归档内相对路径 -> (字节, 权限)。

    同一批工具按平台取**各自的产物**：Linux 包放 ELF，Windows 包放 PE（带 `.exe` 后缀）。
    Windows 包因此不再需要 WSL —— 里面全是本机可执行文件。
    """
    files: dict[str, tuple[bytes, int]] = {}
    idx = 1 if kind == "windows" else 0
    for name, pair in bins.items():
        files[f"bin/{name}{'.exe' if kind == 'windows' else ''}"] = (pair[idx], 0o755)
    files["bin/loment"] = (_subst(LAUNCHER_SH).encode("ascii"), 0o755)
    if kind == "windows":
        # Windows 用原生 .cmd 启动器：包里全是 PE，直接调本机 exe，不再往 WSL 转发
        files["bin/loment.cmd"] = (_crlf(_subst(LAUNCHER_CMD)).encode("ascii"), 0o755)
    files["share/loment/version"] = (version_text().encode(), 0o644)
    files["share/loment/seed.ll"] = (_read("loment/build/selfhost_driver.ll"), 0o644)
    files[f"share/loment/examples/{Path(EXAMPLE).name}"] = (_read(EXAMPLE), 0o644)
    files["share/loment/skill/SKILL.md"] = (_read(SKILL), 0o644)
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
