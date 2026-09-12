#!/bin/sh
# loment/bootstrap.sh — 只用 clang 从**种子**重建 Loment 编译器 (无 Python, docs/159)
#
# 种子 = loment/build/selfhost_driver.ll, 是参考实现为 selfhost/driver.lomt 发射的 IR。
# 自举链从此不再需要 Python 起头:
#
#   1. clang(种子) -> stage1
#   2. stage1 编译 driver.lomt -> 必须与种子逐字节相同 (种子自身就是个定点)
#   3. stage1 产物链出 stage2; stage2 编译同一入口 -> 与 stage2 相同 (三阶段定点)
#   4. stage1 与 stage2 对**非自身**入口 (native_res) 的产物相同
#
# 用法:
#   sh loment/bootstrap.sh              # 跑完 1-4 (几十秒)
#   sh loment/bootstrap.sh ENTRY.lomt   # 追加: 用 stage1 把该入口的 IR 打到 stdout
#
# 依赖: clang (地基语言) + POSIX sh + cmp。**不调用任何解释器**。
#
# 两种运行环境都支持:
#   * Linux/macOS/WSL 自带 clang: 直接用本机 clang, 工作目录在 mktemp -d 里;
#   * WSL 里没有 clang 但有 Windows 版 LLVM: 用 /mnt/c/... 的 clang.exe (互操作),
#     此时输入/输出必须是 Windows 看得见的路径 (wslpath -w 转换), 而**执行** ELF
#     仍要拷到 /tmp (DrvFs 上不能直接跑), 所以工作目录落在 loment/build/。
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"

seed=loment/build/selfhost_driver.ll
entry=loment/selfhost/driver.lomt
target=x86_64-unknown-linux-gnu
cross=0

cc=${CC:-}
if [ -z "$cc" ]; then
    if command -v clang >/dev/null 2>&1; then
        cc=clang
    elif [ -x "/mnt/c/Program Files/LLVM/bin/clang.exe" ]; then
        cc="/mnt/c/Program Files/LLVM/bin/clang.exe"
    else
        echo "FAIL: 找不到 clang (可用 CC=/path/to/clang 指定)" >&2
        exit 1
    fi
fi
case "$cc" in
    /mnt/*) cross=1 ;;
esac

if [ ! -f "$seed" ]; then
    echo "FAIL: 缺种子 $seed (用 python tools/loment_seed.py --emit 固化)" >&2
    exit 1
fi

# Windows 版 clang 不认 /mnt/... 参数, 要转成 D:\... (固定仓库内路径, 无用户输入;
# 需要 wslpath, WSL 自带)。
win() {
    if [ "$cross" = 1 ]; then
        wslpath -w "$1"
    else
        printf '%s' "$1"
    fi
}

if [ "$cross" = 1 ]; then
    work="$root/loment/build/seed-work.$$"
    mkdir -p "$work"
else
    work=$(mktemp -d)
fi
trap 'rm -rf "$work"' EXIT

link() {   # link IN.ll OUT.bin
    "$cc" --target="$target" -nostdlib -ffreestanding -static -fuse-ld=lld \
          -o "$(win "$2")" "$(win "$1")"
}

drive() {  # drive ELF ENTRY -> stdout
    exe="$1"
    if [ "$cross" = 1 ]; then
        # DrvFs 上不能直接执行: 拷进 /tmp 再跑 (LD 之类的路径无关, 驱动只读入口文件)
        cp "$1" "/tmp/loment_seed_run.$$"
        chmod +x "/tmp/loment_seed_run.$$"
        exe="/tmp/loment_seed_run.$$"
    fi
    "$exe" "$2"
}

echo "== 1/4 clang(种子) -> stage1   [$(basename "$cc")$([ "$cross" = 1 ] && echo ' (Windows 侧交叉链接)')]"
link "$seed" "$work/stage1"

echo "== 2/4 stage1 编译 $entry -> 必须等于种子"
drive "$work/stage1" "$entry" > "$work/s2.ll"
if ! cmp -s "$work/s2.ll" "$seed"; then
    echo "FAIL: stage1 的产物与种子不一致 (种子过期? 见 docs/159)" >&2
    exit 1
fi

echo "== 3/4 stage2 -> stage3 定点"
link "$work/s2.ll" "$work/stage2"
drive "$work/stage2" "$entry" > "$work/s3.ll"
if ! cmp -s "$work/s3.ll" "$work/s2.ll"; then
    echo "FAIL: 第 3 阶段与第 2 阶段不一致 (未定点)" >&2
    exit 1
fi

echo "== 4/4 stage1 与 stage2 对非自身入口一致"
probe=loment/examples/native_res.lomt
drive "$work/stage1" "$probe" > "$work/p1.ll"
drive "$work/stage2" "$probe" > "$work/p2.ll"
if ! cmp -s "$work/p1.ll" "$work/p2.ll"; then
    echo "FAIL: stage1/stage2 对 $probe 的产物不一致" >&2
    exit 1
fi

if [ "$#" -ge 1 ]; then
    echo "== 发射 $1 (stage1)" >&2
    drive "$work/stage1" "$1"
    exit 0
fi

wc -c "$seed" "$work/p1.ll" | sed 's/^/   /'
echo "SEED BOOTSTRAP OK: 只用 clang + sh (无 Python); 种子自复现 + 三阶段定点"
