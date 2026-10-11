#!/usr/bin/env python3
# loment_libl_test.py — LibL 的行为判据 (docs/232)
#
# **对照物是真的 libc，不是 Python。** 这是本文件与 `loment_std_test` 唯一重要的区别：
# 那边比的是"Loment 算的 == Python 算的"，而 LibL 的主张是"**像 libc**"——
# 那只能拿 libc 自己量。所以这里在 WSL 里用 cc 编一段 C（走系统 glibc），把同一组调用
# 跑一遍，两边 **stdout 逐字节比**。
#
# 为什么非这样不可: 拿 Python 当对照物时，期望值是**我们自己写的**——`strncpy` 补不补零、
# `strcmp` 返回符号还是差值、`strchr` 找不找得到结尾那个 NUL、`memmove` 重不重叠安全，
# 这些边角 Python 与 libc 根本不是同一个答案，于是"两处一起错"照样绿
# (docs/176 §7.3 / feedback_three_way_judgment)。libc 是这里唯一的外来真源。
#
# ⚠ 两段检查表**必须同标签、同顺序** —— 它们分处两种语言所以没法共用一份源码，
# 靠的是下面每个 "----" 分段的注释成对。分叉时判据会指出**第一个**不同行。
#
# 用法: python tools/loment_libl_test.py   (无 clang/WSL 时 SKIP, 退出码 0)

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_json_test as J                                          # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TESTS: list[tuple[str, object]] = []

#: 被量的 LibL 模块（libc 面的四个头）。
LIBL_MODULES = ("string", "ctype", "stdlib", "stdio")

#: ctype 掩码的 12 位，顺序即位序。**两侧同序**，改一处必须改两处。
#: (名字只在这里出现一次，用来生成两边那 12 个 if。)
_CT_BITS = ("isdigit", "isupper", "islower", "isalpha", "isalnum", "isspace",
            "isxdigit", "ispunct", "iscntrl", "isprint", "isgraph", "isblank")
_CT_BYTES = (0, 9, 10, 13, 31, 32, 33, 48, 57, 58, 65, 90, 91, 97, 102, 103,
             122, 123, 126, 127, 128, 200, 255)


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _ct_ifs(fmt: str) -> str:
    """生成那 12 个判定。`fmt` 里 %s 是谓词名，%d 是位。"""
    return "\n".join(fmt % (n, 1 << i) for i, n in enumerate(_CT_BITS))


# ---------------------------------------------------------------- C 侧（对照物）

def _c_src() -> str:
    rows = ", ".join(str(b) for b in _CT_BYTES)
    # 用 token 替换而不是 `%` 格式化: C 的 printf 里全是 `%`, 与 Python 的 `%` 运算符
    # 打架 —— `%` 那版在这里当场炸成 "a real number is required, not dict"。
    return r'''
/* loment_libl_test 的对照物：同一组调用走系统 glibc。 */
#include <stdio.h>
#include <string.h>
#include <ctype.h>
#include <stdlib.h>

static char A[64], B[64], C[64];

static long off(const char *base, const char *p) {
    return p ? (long)(p - base) : -1L;
}
static int sgn(long v) { return v < 0 ? -1 : (v > 0 ? 1 : 0); }

/* qsort 的比较函数 —— libc 那边要一个函数指针, LibL 那边是 kind。
   两边**必须表达同一个序**, 否则比的就不是一件事。 */
static int cmp_u32(const void *a, const void *b) {
    unsigned x = *(const unsigned *)a, y = *(const unsigned *)b;
    return x < y ? -1 : (x > y ? 1 : 0);
}
static int cmp_i64(const void *a, const void *b) {
    long long x = *(const long long *)a, y = *(const long long *)b;
    return x < y ? -1 : (x > y ? 1 : 0);
}
static int cmp_b3(const void *a, const void *b) { return memcmp(a, b, 3); }
static int cmp_cstr(const void *a, const void *b) {
    return strcmp(*(const char *const *)a, *(const char *const *)b);
}

int main(void) {
    long i;
    volatile int _k;

    /* ---- strlen ---- */
    printf("strlen_empty=%ld\n", (long)strlen(""));
    printf("strlen_hello=%ld\n", (long)strlen("hello"));
    A[0]='a'; A[1]=0; A[2]='b'; A[3]=0;
    printf("strlen_nul_middle=%ld\n", (long)strlen(A));

    /* ---- strcpy ---- */
    strcpy(A, "abc");
    printf("strcpy_len=%ld\n", (long)strlen(A));
    for (i = 0; i < 4; i++) printf("strcpy_b%ld=%d\n", i, (int)(unsigned char)A[i]);

    /* ---- strncpy: 源短 -> 补零到 n ---- */
    memset(B, 99, 16);
    strncpy(B, "ab", 5);
    for (i = 0; i < 6; i++) printf("strncpy_pad_b%ld=%d\n", i, (int)(unsigned char)B[i]);

    /* ---- strncpy: 源够长 -> 不给 NUL ---- */
    memset(B, 99, 16);
    strncpy(B, "abcd", 3);
    for (i = 0; i < 4; i++) printf("strncpy_full_b%ld=%d\n", i, (int)(unsigned char)B[i]);

    /* ---- strcat / strncat ---- */
    strcpy(B, "abc"); strcat(B, "de");
    printf("strcat_len=%ld\n", (long)strlen(B));
    for (i = 0; i < 6; i++) printf("strcat_b%ld=%d\n", i, (int)(unsigned char)B[i]);
    strcpy(B, "xy"); strncat(B, "abcdef", 3);
    printf("strncat_len=%ld\n", (long)strlen(B));
    for (i = 0; i < 6; i++) printf("strncat_b%ld=%d\n", i, (int)(unsigned char)B[i]);

    /* ---- strcmp: 符号与值 ---- */
    printf("strcmp_lt_sign=%d\n", sgn(strcmp("abc", "abd")));
    printf("strcmp_lt_val=%ld\n", (long)strcmp("abc", "abd"));
    printf("strcmp_gt_sign=%d\n", sgn(strcmp("abd", "abc")));
    printf("strcmp_eq_sign=%d\n", sgn(strcmp("abc", "abc")));
    printf("strcmp_prefix_sign=%d\n", sgn(strcmp("abc", "ab")));
    printf("strcmp_empty_sign=%d\n", sgn(strcmp("", "")));
    printf("strncmp_2_sign=%d\n", sgn(strncmp("abc", "abd", 2)));
    printf("strncmp_3_sign=%d\n", sgn(strncmp("abc", "abd", 3)));
    printf("strncmp_0_sign=%d\n", sgn(strncmp("abc", "zzz", 0)));

    /* ---- strcmp 无符号: 0x80 vs 'a' ---- */
    A[0]=(char)128; A[1]=0; B[0]='a'; B[1]=0;
    printf("strcmp_unsigned_sign=%d\n", sgn(strcmp(A, B)));

    /* ---- strchr / strrchr ---- */
    strcpy(A, "abc");
    printf("strchr_found=%ld\n", off(A, strchr(A, 'b')));
    printf("strchr_nul=%ld\n", off(A, strchr(A, 0)));
    printf("strchr_missing=%ld\n", off(A, strchr(A, 'z')));
    strcpy(A, "abca");
    printf("strrchr_last=%ld\n", off(A, strrchr(A, 'a')));
    printf("strrchr_missing=%ld\n", off(A, strrchr(A, 'z')));

    /* ---- strstr ---- */
    strcpy(A, "hello world");
    printf("strstr_found=%ld\n", off(A, strstr(A, "world")));
    printf("strstr_missing=%ld\n", off(A, strstr(A, "zzz")));
    printf("strstr_empty_needle=%ld\n", off(A, strstr(A, "")));

    /* ---- strspn / strcspn / strpbrk ---- */
    strcpy(A, "aabbcc");
    printf("strspn_ab=%ld\n", (long)strspn(A, "ab"));
    printf("strspn_x=%ld\n", (long)strspn(A, "x"));
    printf("strcspn_c=%ld\n", (long)strcspn(A, "c"));
    printf("strpbrk_cb=%ld\n", off(A, strpbrk(A, "cb")));
    printf("strpbrk_missing=%ld\n", off(A, strpbrk(A, "z")));

    /* ---- memchr / memcmp ---- */
    memset(A, 7, 8); A[3]=0;
    memset(B, 7, 8); B[2]=5;
    printf("memchr_zero=%ld\n", off(A, memchr(A, 0, 8)));
    printf("memchr_missing=%ld\n", off(A, memchr(A, 9, 8)));
    printf("memcmp_full_sign=%d\n", sgn(memcmp(A, B, 8)));
    printf("memcmp_prefix_sign=%d\n", sgn(memcmp(A, B, 2)));

    /* ---- memcpy / memmove / memset ---- */
    memset(B, 0, 32); memcpy(B, "ABCD", 5);
    printf("memcpy_len=%ld\n", (long)strlen(B));
    memmove(B + 1, B, 4);
    for (i = 0; i < 6; i++) printf("memmove_b%ld=%d\n", i, (int)(unsigned char)B[i]);
    memset(B, 257, 4);
    for (i = 0; i < 4; i++) printf("memset_b%ld=%d\n", i, (int)(unsigned char)B[i]);

    /* ---- strtok_r ---- */
    strcpy(C, "a,,b,c");
    {
        char *save = NULL;
        char *t1 = strtok_r(C, ",;", &save);
        printf("strtok_t1_is_base=%d\n", t1 == C);
        printf("strtok_t1_len=%ld\n", (long)strlen(t1));
        printf("strtok_c1_after=%d\n", (int)(unsigned char)C[1]);
        char *t2 = strtok_r(NULL, ",;", &save);
        printf("strtok_t2_first=%d\n", (int)(unsigned char)t2[0]);
        printf("strtok_t2_len=%ld\n", (long)strlen(t2));
        char *t3 = strtok_r(NULL, ",;", &save);
        printf("strtok_t3_first=%d\n", (int)(unsigned char)t3[0]);
        printf("strtok_t3_len=%ld\n", (long)strlen(t3));
        char *t4 = strtok_r(NULL, ",;", &save);
        printf("strtok_t4_is_null=%d\n", t4 == NULL);
    }

    /* ---- ctype: 每个字节一行 row + 一行掩码 ---- */
    {
        static const int CT[] = {@@ROWS@@};
        for (i = 0; i < @@N@@; i++) {
            int c = CT[i];
            int m = 0;
@@CT_IFS@@
            printf("ct_row=%d\n", c);
            printf("ct_mask=%d\n", m);
        }
    }
    printf("toupper_a=%d\n", toupper('a'));
    printf("tolower_Z=%d\n", tolower('Z'));
    printf("toupper_digit=%d\n", toupper('5'));
    printf("tolower_high=%d\n", tolower(200));

    /* ---- stdlib: 转换 ---- */
    printf("atoi_space=%ld\n", (long)atoi("  -42xyz"));
    printf("atoi_plus=%ld\n", (long)atoi("+7"));
    printf("atoi_junk=%ld\n", (long)atoi("xyz"));
    printf("atol_neg=%ld\n", (long)atol("-123"));
    printf("strtol_hex=%ld\n", (long)strtol("0x1F", NULL, 16));
    printf("strtol_auto_hex=%ld\n", (long)strtol("0x1F", NULL, 0));
    printf("strtol_auto_oct=%ld\n", (long)strtol("0755", NULL, 0));
    printf("strtol_bin=%ld\n", (long)strtol("1011", NULL, 2));
    printf("strtol_36=%ld\n", (long)strtol("zz", NULL, 36));
    printf("strtol_neg=%ld\n", (long)strtol("-99rest", NULL, 10));
    {
        char *p = NULL;
        long v = strtol("12ab", &p, 10);
        printf("strtol_end_val=%ld\n", v);
        printf("strtol_end_off=%ld\n", (long)(p - "12ab"));
    }
    {
        char *p = NULL;
        long v = strtol("   xyz", &p, 10);
        printf("strtol_nodigit_val=%ld\n", v);
        printf("strtol_nodigit_off=%ld\n", (long)(p - "   xyz"));
    }
    printf("abs_int=%d\n", abs(-5));
    printf("labs_neg=%ld\n", labs(-5L));
    printf("llabs_neg=%lld\n", llabs(-7LL));

    /* ---- stdlib: qsort（libc 给比较函数, LibL 给 kind —— 序必须一致） ---- */
    {
        unsigned w[6]; long k;
        for (k = 0; k < 6; k++) w[k] = (unsigned)((int[]){3, 1, 4, 1, 5, 9})[k];
        qsort(w, 6, sizeof(unsigned), cmp_u32);
        for (k = 0; k < 6; k++) printf("qs_u32_%ld=%u\n", k, w[k]);
    }
    {
        long long w[4]; long k;
        for (k = 0; k < 4; k++) w[k] = ((long long[]){1, -3, 0, 2})[k];
        qsort(w, 4, sizeof(long long), cmp_i64);
        for (k = 0; k < 4; k++) printf("qs_i64_%ld=%lld\n", k, w[k]);
    }
    {
        char w[9] = {'b','b','b','a','z','z','a','b','z'}; long k;
        qsort(w, 3, 3, cmp_b3);
        for (k = 0; k < 9; k++) printf("qs_b3_%ld=%d\n", k, (int)(unsigned char)w[k]);
    }
    {
        const char *w[3] = {"pear", "apple", "fig"}; long k;
        qsort(w, 3, sizeof(char *), cmp_cstr);
        for (k = 0; k < 3; k++) printf("qs_cstr_%ld=%s\n", k, w[k]);
    }

    /* ---- stdlib: bsearch ---- */
    {
        unsigned w[4] = {1, 3, 4, 7};
        unsigned key = 4;
        unsigned *r = (unsigned *)bsearch(&key, w, 4, sizeof(unsigned), cmp_u32);
        /* 与 LibL 比**同一个量**: 字节偏移 (不是元素下标) */
        printf("bs_hit_off=%ld\n", r ? (long)((char *)r - (char *)w) : -1L);
        key = 5;
        r = (unsigned *)bsearch(&key, w, 4, sizeof(unsigned), cmp_u32);
        printf("bs_miss_is_null=%d\n", r == NULL);
    }

    /* ---- stdio: snprintf（值 + 返回值 + 截断） ---- */
    {
        char b[64]; int n;
        n = snprintf(b, 64, "hello %s #%d", "world", 42);
        printf("sn_str=%s\n", b); printf("sn_str_ret=%d\n", n);
        n = snprintf(b, 64, "%d %i %u", -5, 7, 4294967295u);
        printf("sn_int=%s\n", b); printf("sn_int_ret=%d\n", n);
        n = snprintf(b, 64, "%x %X %o", 255u, 255u, 8u);
        printf("sn_radix=%s\n", b); printf("sn_radix_ret=%d\n", n);
        n = snprintf(b, 64, "[%5d][%-5d][%05d]", 42, 42, 42);
        printf("sn_pad=%s\n", b); printf("sn_pad_ret=%d\n", n);
        n = snprintf(b, 8, "0123456789");
        printf("sn_trunc=%s\n", b); printf("sn_trunc_ret=%d\n", n);
        n = snprintf(b, 64, "100%% done %c", 'x');
        printf("sn_pct=%s\n", b); printf("sn_pct_ret=%d\n", n);
        n = snprintf(b, 64, "%ld %lld %zu", -1L, -2LL, (size_t)3);
        printf("sn_lenmod=%s\n", b); printf("sn_lenmod_ret=%d\n", n);
        n = snprintf(b, 1, "abc");
        printf("sn_cap1=%d\n", n); printf("sn_cap1_b0=%d\n", (int)(unsigned char)b[0]);
    }

    return 0;
}
'''.replace("@@ROWS@@", rows) \
   .replace("@@N@@", str(len(_CT_BYTES))) \
   .replace("@@CT_IFS@@", _ct_ifs("            if (%s(c)) m |= %d;"))


# ---------------------------------------------------------------- Loment 侧（被验）

def _loment_probe() -> str:
    ct_lit = ", ".join(f"{b} as u32" for b in _CT_BYTES)
    return r'''// loment/build/libl_probe.lomt — LibL 的探针（由 tools/loment_libl_test.py 生成）
//
// 与 C 侧对照物**同标签、同顺序**。分叉时判据会指出第一个不同行。
module libl_probe

use "lompi/store/std/0.1.0/string.lomt"
use "lompi/store/std/0.1.0/ctype.lomt"
use "lompi/store/std/0.1.0/stdlib.lomt"
use "lompi/store/std/0.1.0/stdio.lomt"

fn wr(p: ptr, n: u32) -> i64 {
    return syscall4(1, 1, p as u64, n as u64);
}

/// 字面量 -> NUL 结尾的 C 串。
///
/// **本语言的字符串字面量不带 NUL**（`"abc"` 的 IR 是 `[3 x i8]`，没有第四个字节），
/// 所以直接 `str_ptr("abc")` 交给 `strlen` 会读过头 —— 实测空串那条一开始读出 17。
/// 这里每次都新 `alloc` 一段：调用点可能同时要两个（`strcmp(a, b)`），
/// 共用一个缓冲会互相踩。（库那一侧对应的是 `cstr_of`。）
fn lit(s: str) -> ptr {
    let d: ptr = alloc(str_len(s) + 1);
    return cstr_of(d, s);
}

fn nul() -> ptr {
    return 0 as ptr;
}

fn b2i(b: bool) -> i64 {
    if b {
        return 1 as i64;
    }
    return 0 as i64;
}

/// u64 -> 十进制，自带（**不用 std 的 numfmt**）。
///
/// 为什么不 `use numfmt`: 那个模块的 `numfmt_hex_into` 把 `u64` 右移一个 **i32 变量**
/// （`v >> shift`），参考实现发出 `lshr i64 %v, i32 %shift` —— 非法 IR，clang 当场拒。
/// 一旦 use 它，整个模块的函数都会被编出来，于是一个**探针根本没调用**的函数把编译打断。
/// 那是既有的混宽缺陷（docs/180 §4.2 / docs/188 §7.1.1）落在 std 代码上，与 LibL 无关，
/// 已单独记（见 docs/232 §6）。探针自带十进制输出，就不必替它挡这一枪。
fn dec_into(dst: ptr, v: u64) -> u32 {
    if v == (0 as u64) {
        store8(dst, 0, 48 as u8);
        return 1;
    }
    let d: u32 = 0;
    let x: u64 = v;
    while x > (0 as u64) {
        x = x / 10;
        d = d + 1;
    }
    let y: u64 = v;
    let i: u32 = 0;
    while i < d {
        let digit: u32 = (y % 10) as u32;
        store8(dst, d - 1 - i, (48 + digit) as u8);
        y = y / 10;
        i = i + 1;
    }
    return d;
}

/// 有符号十进制 —— `dec_into` 是无符号的，符号这一层在这里补。
fn say_mag(sc: ptr, v: i64) -> i64 {
    let mag: u64 = v as u64;
    if v < (0 as i64) {
        let _s: i64 = wr(lit("-"), 1);
        mag = (0 as i64 - v) as u64;
    }
    let n: u32 = dec_into(sc, mag);
    return wr(sc, n);
}

fn say(sc: ptr, tag: str, v: i64) -> i64 {
    let _t: i64 = wr(str_ptr(tag), str_len(tag));
    let _e: i64 = wr(lit("="), 1);
    let _v: i64 = say_mag(sc, v);
    return wr(lit("\n"), 1);
}

/// 一行 `<标签>=<C 串>` —— 对照物那边对应 `printf("%s=%s\n", ...)`。
fn say_str(tag: str, p: ptr) -> i64 {
    let _t: i64 = wr(str_ptr(tag), str_len(tag));
    let _e: i64 = wr(lit("="), 1);
    let _v: i64 = wr(p, strlen(p));
    return wr(lit("\n"), 1);
}

/// 把至多 3 个实参放进 u64 数组并调 `snprintf`。
///
/// **这一层就是"没有变参"的代价**：libc 那边直接写在调用点，这边要先摆成数组。
/// 摆法（第 k 个实参在偏移 k*8）与 `stdio.lomt` 文件头写的是同一条。
fn sp(buf: ptr, cap: u32, f: str, a0: u64, a1: u64, a2: u64, n: u32) -> u32 {
    let av: ptr = alloc(24);
    mem_store64(av, 0, a0);
    mem_store64(av, 8, a1);
    mem_store64(av, 16, a2);
    let fp: ptr = alloc(str_len(f) + 1);
    return snprintf(buf, cap, cstr_of(fp, f), av, n);
}

/// libc 那些返回指针的：判据比的是**偏移**（两边地址不同，只有偏移可比）。
fn off(base: ptr, p: ptr) -> i64 {
    if (p as u64) == (0 as u64) {
        return 0 as i64 - 1;
    }
    return ((p as u64) - (base as u64)) as i64;
}

fn sgn(v: i64) -> i64 {
    if v < (0 as i64) {
        return 0 as i64 - 1;
    }
    if v > (0 as i64) {
        return 1 as i64;
    }
    return 0 as i64;
}

fn _start() {
    let sc: ptr = alloc(64);
    let a: ptr = alloc(64);
    let b: ptr = alloc(64);
    let c: ptr = alloc(64);
    let i: u32 = 0;

    // ---- strlen ----
    say(sc, "strlen_empty", strlen(lit("")) as i64);
    say(sc, "strlen_hello", strlen(lit("hello")) as i64);
    store8(a, 0, 97 as u8); store8(a, 1, 0 as u8);
    store8(a, 2, 98 as u8); store8(a, 3, 0 as u8);
    say(sc, "strlen_nul_middle", strlen(a) as i64);

    // ---- strcpy ----
    strcpy(a, lit("abc"));
    say(sc, "strcpy_len", strlen(a) as i64);
    say(sc, "strcpy_b0", load8(a, 0) as i64);
    say(sc, "strcpy_b1", load8(a, 1) as i64);
    say(sc, "strcpy_b2", load8(a, 2) as i64);
    say(sc, "strcpy_b3", load8(a, 3) as i64);

    // ---- strncpy: 源短 -> 补零到 n ----
    mem_fill(b, 0, 16, 99);
    strncpy(b, lit("ab"), 5);
    say(sc, "strncpy_pad_b0", load8(b, 0) as i64);
    say(sc, "strncpy_pad_b1", load8(b, 1) as i64);
    say(sc, "strncpy_pad_b2", load8(b, 2) as i64);
    say(sc, "strncpy_pad_b3", load8(b, 3) as i64);
    say(sc, "strncpy_pad_b4", load8(b, 4) as i64);
    say(sc, "strncpy_pad_b5", load8(b, 5) as i64);

    // ---- strncpy: 源够长 -> 不给 NUL ----
    mem_fill(b, 0, 16, 99);
    strncpy(b, lit("abcd"), 3);
    say(sc, "strncpy_full_b0", load8(b, 0) as i64);
    say(sc, "strncpy_full_b1", load8(b, 1) as i64);
    say(sc, "strncpy_full_b2", load8(b, 2) as i64);
    say(sc, "strncpy_full_b3", load8(b, 3) as i64);

    // ---- strcat / strncat ----
    strcpy(b, lit("abc"));
    strcat(b, lit("de"));
    say(sc, "strcat_len", strlen(b) as i64);
    say(sc, "strcat_b0", load8(b, 0) as i64);
    say(sc, "strcat_b1", load8(b, 1) as i64);
    say(sc, "strcat_b2", load8(b, 2) as i64);
    say(sc, "strcat_b3", load8(b, 3) as i64);
    say(sc, "strcat_b4", load8(b, 4) as i64);
    say(sc, "strcat_b5", load8(b, 5) as i64);
    strcpy(b, lit("xy"));
    strncat(b, lit("abcdef"), 3);
    say(sc, "strncat_len", strlen(b) as i64);
    say(sc, "strncat_b0", load8(b, 0) as i64);
    say(sc, "strncat_b1", load8(b, 1) as i64);
    say(sc, "strncat_b2", load8(b, 2) as i64);
    say(sc, "strncat_b3", load8(b, 3) as i64);
    say(sc, "strncat_b4", load8(b, 4) as i64);
    say(sc, "strncat_b5", load8(b, 5) as i64);

    // ---- strcmp: 符号与值 ----
    say(sc, "strcmp_lt_sign", sgn(strcmp(lit("abc"), lit("abd"))));
    say(sc, "strcmp_lt_val", strcmp(lit("abc"), lit("abd")));
    say(sc, "strcmp_gt_sign", sgn(strcmp(lit("abd"), lit("abc"))));
    say(sc, "strcmp_eq_sign", sgn(strcmp(lit("abc"), lit("abc"))));
    say(sc, "strcmp_prefix_sign", sgn(strcmp(lit("abc"), lit("ab"))));
    say(sc, "strcmp_empty_sign", sgn(strcmp(lit(""), lit(""))));
    say(sc, "strncmp_2_sign", sgn(strncmp(lit("abc"), lit("abd"), 2)));
    say(sc, "strncmp_3_sign", sgn(strncmp(lit("abc"), lit("abd"), 3)));
    say(sc, "strncmp_0_sign", sgn(strncmp(lit("abc"), lit("zzz"), 0)));

    // ---- strcmp 无符号: 0x80 vs 'a' ----
    store8(a, 0, 128 as u8); store8(a, 1, 0 as u8);
    store8(b, 0, 97 as u8); store8(b, 1, 0 as u8);
    say(sc, "strcmp_unsigned_sign", sgn(strcmp(a, b)));

    // ---- strchr / strrchr ----
    strcpy(a, lit("abc"));
    say(sc, "strchr_found", off(a, strchr(a, 98)));
    say(sc, "strchr_nul", off(a, strchr(a, 0)));
    say(sc, "strchr_missing", off(a, strchr(a, 122)));
    strcpy(a, lit("abca"));
    say(sc, "strrchr_last", off(a, strrchr(a, 97)));
    say(sc, "strrchr_missing", off(a, strrchr(a, 122)));

    // ---- strstr ----
    strcpy(a, lit("hello world"));
    say(sc, "strstr_found", off(a, strstr(a, lit("world"))));
    say(sc, "strstr_missing", off(a, strstr(a, lit("zzz"))));
    say(sc, "strstr_empty_needle", off(a, strstr(a, lit(""))));

    // ---- strspn / strcspn / strpbrk ----
    strcpy(a, lit("aabbcc"));
    say(sc, "strspn_ab", strspn(a, lit("ab")) as i64);
    say(sc, "strspn_x", strspn(a, lit("x")) as i64);
    say(sc, "strcspn_c", strcspn(a, lit("c")) as i64);
    say(sc, "strpbrk_cb", off(a, strpbrk(a, lit("cb"))));
    say(sc, "strpbrk_missing", off(a, strpbrk(a, lit("z"))));

    // ---- memchr / memcmp ----
    mem_fill(a, 0, 8, 7);
    store8(a, 3, 0 as u8);
    mem_fill(b, 0, 8, 7);
    store8(b, 2, 5 as u8);
    say(sc, "memchr_zero", off(a, memchr(a, 0, 8)));
    say(sc, "memchr_missing", off(a, memchr(a, 9, 8)));
    say(sc, "memcmp_full_sign", sgn(memcmp(a, b, 8)));
    say(sc, "memcmp_prefix_sign", sgn(memcmp(a, b, 2)));

    // ---- memcpy / memmove / memset ----
    mem_fill(b, 0, 32, 0);
    memcpy(b, lit("ABCD"), 5);
    say(sc, "memcpy_len", strlen(b) as i64);
    memmove(ptr_add(b, 1), b, 4);
    say(sc, "memmove_b0", load8(b, 0) as i64);
    say(sc, "memmove_b1", load8(b, 1) as i64);
    say(sc, "memmove_b2", load8(b, 2) as i64);
    say(sc, "memmove_b3", load8(b, 3) as i64);
    say(sc, "memmove_b4", load8(b, 4) as i64);
    say(sc, "memmove_b5", load8(b, 5) as i64);
    memset(b, 257, 4);
    say(sc, "memset_b0", load8(b, 0) as i64);
    say(sc, "memset_b1", load8(b, 1) as i64);
    say(sc, "memset_b2", load8(b, 2) as i64);
    say(sc, "memset_b3", load8(b, 3) as i64);

    // ---- strtok_r ----
    strcpy(c, lit("a,,b,c"));
    let save: ptr = alloc(8);
    let t1: ptr = strtok_r(c, lit(",;"), save);
    say(sc, "strtok_t1_is_base", b2i((t1 as u64) == (c as u64)));
    say(sc, "strtok_t1_len", strlen(t1) as i64);
    say(sc, "strtok_c1_after", load8(c, 1) as i64);
    let t2: ptr = strtok_r(nul(), lit(",;"), save);
    say(sc, "strtok_t2_first", load8(t2, 0) as i64);
    say(sc, "strtok_t2_len", strlen(t2) as i64);
    let t3: ptr = strtok_r(nul(), lit(",;"), save);
    say(sc, "strtok_t3_first", load8(t3, 0) as i64);
    say(sc, "strtok_t3_len", strlen(t3) as i64);
    let t4: ptr = strtok_r(nul(), lit(",;"), save);
    say(sc, "strtok_t4_is_null", b2i((t4 as u64) == (0 as u64)));

    // ---- ctype: 每个字节一行 row + 一行掩码 ----
    let ct: [u32; @@N@@] = [@@CT@@];
    i = 0;
    while i < @@N@@ {
        let ch: u32 = ct[i];
        let m: u32 = 0;
@@CT_IFS@@
        say(sc, "ct_row", ch as i64);
        say(sc, "ct_mask", m as i64);
        i = i + 1;
    }
    say(sc, "toupper_a", toupper(97) as i64);
    say(sc, "tolower_Z", tolower(90) as i64);
    say(sc, "toupper_digit", toupper(53) as i64);
    say(sc, "tolower_high", tolower(200) as i64);

    // ---- stdlib: 转换 ----
    say(sc, "atoi_space", atoi(lit("  -42xyz")));
    say(sc, "atoi_plus", atoi(lit("+7")));
    say(sc, "atoi_junk", atoi(lit("xyz")));
    say(sc, "atol_neg", atol(lit("-123")));
    say(sc, "strtol_hex", strtol(lit("0x1F"), nul(), 16));
    say(sc, "strtol_auto_hex", strtol(lit("0x1F"), nul(), 0));
    say(sc, "strtol_auto_oct", strtol(lit("0755"), nul(), 0));
    say(sc, "strtol_bin", strtol(lit("1011"), nul(), 2));
    say(sc, "strtol_36", strtol(lit("zz"), nul(), 36));
    say(sc, "strtol_neg", strtol(lit("-99rest"), nul(), 10));
    let st_slot: ptr = alloc(8);
    let st_a: ptr = lit("12ab");
    say(sc, "strtol_end_val", strtol(st_a, st_slot, 10));
    say(sc, "strtol_end_off", off(st_a, mem_load64(st_slot, 0) as ptr));
    let st_b: ptr = lit("   xyz");
    say(sc, "strtol_nodigit_val", strtol(st_b, st_slot, 10));
    say(sc, "strtol_nodigit_off", off(st_b, mem_load64(st_slot, 0) as ptr));
    say(sc, "abs_int", abs(0 as i32 - 5) as i64);
    say(sc, "labs_neg", labs(0 as i64 - 5));
    say(sc, "llabs_neg", llabs(0 as i64 - 7));

    // ---- stdlib: qsort ----
    let q1: ptr = alloc(24);
    mem_store32(q1, 0, 3); mem_store32(q1, 4, 1); mem_store32(q1, 8, 4);
    mem_store32(q1, 12, 1); mem_store32(q1, 16, 5); mem_store32(q1, 20, 9);
    qsort_u32(q1, 6);
    say(sc, "qs_u32_0", mem_load32(q1, 0) as i64);
    say(sc, "qs_u32_1", mem_load32(q1, 4) as i64);
    say(sc, "qs_u32_2", mem_load32(q1, 8) as i64);
    say(sc, "qs_u32_3", mem_load32(q1, 12) as i64);
    say(sc, "qs_u32_4", mem_load32(q1, 16) as i64);
    say(sc, "qs_u32_5", mem_load32(q1, 20) as i64);

    let q2: ptr = alloc(32);
    mem_store64(q2, 0, 1 as u64);
    mem_store64(q2, 8, (0 as i64 - 3) as u64);
    mem_store64(q2, 16, 0 as u64);
    mem_store64(q2, 24, 2 as u64);
    qsort(q2, 4, 8, qsort_kind::qk_i64);
    say(sc, "qs_i64_0", mem_load64(q2, 0) as i64);
    say(sc, "qs_i64_1", mem_load64(q2, 8) as i64);
    say(sc, "qs_i64_2", mem_load64(q2, 16) as i64);
    say(sc, "qs_i64_3", mem_load64(q2, 24) as i64);

    let q3: ptr = alloc(9);
    store8(q3, 0, 98 as u8); store8(q3, 1, 98 as u8); store8(q3, 2, 98 as u8);
    store8(q3, 3, 97 as u8); store8(q3, 4, 122 as u8); store8(q3, 5, 122 as u8);
    store8(q3, 6, 97 as u8); store8(q3, 7, 98 as u8); store8(q3, 8, 122 as u8);
    qsort(q3, 3, 3, qsort_kind::qk_bytes);
    say(sc, "qs_b3_0", load8(q3, 0) as i64);
    say(sc, "qs_b3_1", load8(q3, 1) as i64);
    say(sc, "qs_b3_2", load8(q3, 2) as i64);
    say(sc, "qs_b3_3", load8(q3, 3) as i64);
    say(sc, "qs_b3_4", load8(q3, 4) as i64);
    say(sc, "qs_b3_5", load8(q3, 5) as i64);
    say(sc, "qs_b3_6", load8(q3, 6) as i64);
    say(sc, "qs_b3_7", load8(q3, 7) as i64);
    say(sc, "qs_b3_8", load8(q3, 8) as i64);

    let q4: ptr = alloc(24);
    mem_store64(q4, 0, lit("pear") as u64);
    mem_store64(q4, 8, lit("apple") as u64);
    mem_store64(q4, 16, lit("fig") as u64);
    qsort(q4, 3, 8, qsort_kind::qk_cstr);
    say_str("qs_cstr_0", mem_load64(q4, 0) as ptr);
    say_str("qs_cstr_1", mem_load64(q4, 8) as ptr);
    say_str("qs_cstr_2", mem_load64(q4, 16) as ptr);

    // ---- stdlib: bsearch ----
    let bw: ptr = alloc(16);
    mem_store32(bw, 0, 1); mem_store32(bw, 4, 3);
    mem_store32(bw, 8, 4); mem_store32(bw, 12, 7);
    say(sc, "bs_hit_off", off(bw, bsearch_u32(4, bw, 4)));
    say(sc, "bs_miss_is_null", b2i((bsearch_u32(5, bw, 4) as u64) == (0 as u64)));

    // ---- stdio: snprintf ----
    let ob: ptr = alloc(64);
    let sn: u32 = 0;
    sn = sp(ob, 64, "hello %s #%d", lit("world") as u64, 42 as u64, 0 as u64, 2);
    say_str("sn_str", ob);
    say(sc, "sn_str_ret", sn as i64);
    sn = sp(ob, 64, "%d %i %u", (0 as i64 - 5) as u64, 7 as u64, 4294967295 as u64, 3);
    say_str("sn_int", ob);
    say(sc, "sn_int_ret", sn as i64);
    sn = sp(ob, 64, "%x %X %o", 255 as u64, 255 as u64, 8 as u64, 3);
    say_str("sn_radix", ob);
    say(sc, "sn_radix_ret", sn as i64);
    sn = sp(ob, 64, "[%5d][%-5d][%05d]", 42 as u64, 42 as u64, 42 as u64, 3);
    say_str("sn_pad", ob);
    say(sc, "sn_pad_ret", sn as i64);
    sn = sp(ob, 8, "0123456789", 0 as u64, 0 as u64, 0 as u64, 0);
    say_str("sn_trunc", ob);
    say(sc, "sn_trunc_ret", sn as i64);
    sn = sp(ob, 64, "100%% done %c", 120 as u64, 0 as u64, 0 as u64, 1);
    say_str("sn_pct", ob);
    say(sc, "sn_pct_ret", sn as i64);
    sn = sp(ob, 64, "%ld %lld %zu", (0 as i64 - 1) as u64, (0 as i64 - 2) as u64, 3 as u64, 3);
    say_str("sn_lenmod", ob);
    say(sc, "sn_lenmod_ret", sn as i64);
    sn = sp(ob, 1, "abc", 0 as u64, 0 as u64, 0 as u64, 0);
    say(sc, "sn_cap1", sn as i64);
    say(sc, "sn_cap1_b0", load8(ob, 0) as i64);

    syscall4(60, 0, 0, 0);
}
'''.replace("@@N@@", str(len(_CT_BYTES))) \
   .replace("@@CT@@", ct_lit) \
   .replace("@@CT_IFS@@", _ct_ifs("        if %s(ch) { m = m | %d; }"))


# ---------------------------------------------------------------- 跑

def _run_c(td: Path, name: str, src: str) -> str:
    """在 WSL 里用 cc 编一段 C（走系统 glibc）并跑，返回 stdout。"""
    with (td / f"{name}.c").open("w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    script = (f"cd {J._wsl_path(td)} && rm -f {name}.bin && "
              f"cc -O0 -o {name}.bin {name}.c && ./{name}.bin > {name}.out; echo -n $?")
    r = subprocess.run(["wsl", "-e", "bash", "-lc", script],
                       capture_output=True, text=True, timeout=300, shell=False)
    assert r.stdout.strip() == "0", f"C 对照物编译/运行失败:\n{r.stderr[-600:]}"
    return (td / f"{name}.out").read_bytes().decode("utf-8")


def _run_loment(td: Path) -> str:
    probe = ROOT / "loment" / "build" / "libl_probe.lomt"
    probe.parent.mkdir(parents=True, exist_ok=True)
    with probe.open("w", encoding="utf-8", newline="\n") as f:
        f.write(_loment_probe())
    try:
        elf = J._compile(probe, td, "libl_probe")
        return J._run(elf, td)
    finally:
        probe.unlink(missing_ok=True)


def _first_diff(got: str, want: str) -> str:
    g, w = got.splitlines(), want.splitlines()
    for i in range(max(len(g), len(w))):
        gl = g[i] if i < len(g) else "<无>"
        wl = w[i] if i < len(w) else "<无>"
        if gl != wl:
            return f"第 {i + 1} 行: LibL={gl!r} glibc={wl!r}"
    return ""


@test
def test_libl_matches_libc():
    """LibL 的 libc 面与**系统 glibc** 逐字节一致。"""
    if not (J._clang() and J._wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        want = _run_c(td, "libc_ref", _c_src())
        got = _run_loment(td)
    diff = _first_diff(got, want)
    assert not diff, f"LibL 与 glibc 不一致 @ {diff}"
    print(f"      libc 面: {len(want.splitlines())} 项与 glibc 逐字节一致")


@test
def test_libl_modules_are_checkable():
    """每个被量的 LibL 模块**自己**必须是合法的 L1 单元（不靠 clang，永远跑）。"""
    import lomentc
    for name in LIBL_MODULES:
        p = ROOT / "lompi" / "store" / "std" / "0.1.0" / f"{name}.lomt"
        assert p.exists(), f"缺模块 {p}"
        mod = lomentc.load(p)
        deps = lomentc.resolve_deps(mod, ROOT, p.parent, entry=p)
        errs = lomentc.check(mod, deps=deps)
        assert not errs, f"{p.name}: {errs[:2]}"
    print(f"      {len(LIBL_MODULES)} 个 LibL 模块都过检查")


def main(argv: list[str] | None = None) -> int:
    only = argv[0] if argv else None
    failed = []
    for name, fn in TESTS:
        if only and only not in name:
            continue
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed.append(name)
            print(f"  FAIL  {name}: {e}")
        except Exception as e:                                       # noqa: BLE001
            failed.append(name)
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    n = len([x for x, _ in TESTS if not only or only in x])
    print(f"\nloment_libl_test: {n - len(failed)}/{n} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
