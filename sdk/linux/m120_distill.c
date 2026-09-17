/* m120_distill.c — W10 R5/R6: 策略蒸馏 (FJRU 字节码) + 审计自改进闭环 (docs/61)
 *
 * 蒸馏闭环: 审计/实测记录 → tools/distill_rules.py (7B 归纳 + 编译 FJRU v1)
 * → 本 demo 经 0x830B 载入 → 五职责"规则字节码优先" → 保真度断言。
 * 离线运行 (无模型): AI_CALLS 必须保持 0 —— 模型调用率 100% 下降,
 * 且蒸馏输出 == 实测记录期望 (行为不退化)。
 *
 *   R5  载入 14 条规则; anom rate=99→(1,80,engine=3) / rate=5→(0,20,3);
 *        io 0 1 2 3 4→5 / 3 4 5 0 1→2; plan isolate task 1→A2 1 (param!);
 *        plan threshold→A4 1 70 (a1!); nlc ban games→POL=3:1; classify run→1
 *   R6  导出审计: ≥8 条 engine=3; stats: 模型调用 0, 规则命中 ≥8
 */
typedef long int64_t;
typedef unsigned long u64;

static int64_t sy(int64_t nr, int64_t a, int64_t b, int64_t c, int64_t d, int64_t e)
{
    register long rax asm("rax") = nr;
    register long rdi asm("rdi") = a;
    register long rsi asm("rsi") = b;
    register long rdx asm("rdx") = c;
    register long r10 asm("r10") = d;
    register long r8 asm("r8") = e;
    asm volatile("syscall" : "+r"(rax) : "r"(rdi), "r"(rsi), "r"(rdx),
                 "r"(r10), "r"(r8) : "rcx", "r11", "memory");
    return rax;
}

static void wr(const char *s, long len) { sy(1, 1, (long)s, len, 0, 0); }
static void wrdec(u64 v)
{
    char b[22];
    int i = 22;
    if (v == 0) {
        wr("0", 1);
        return;
    }
    while (v > 0) {
        b[--i] = '0' + (char)(v % 10);
        v /= 10;
    }
    wr(b + i, 22 - i);
}

static void wrstr(const char *s)
{
    int n = 0;
    while (s[n])
        n++;
    wr(s, n);
}

#include "../rulebook/rulebook.h"

__attribute__((noinline, noreturn)) static void worker(void)
{
    volatile u64 x = 0;
    for (;;) {
        x += 1;
        if (x > 0xFFFF0000ul)
            x = 0;
    }
}

static long strstr_pos(const char *hay, const char *needle)
{
    long i = 0, j;
    while (hay[i]) {
        j = 0;
        while (needle[j] && hay[i + j] == needle[j])
            j++;
        if (!needle[j])
            return i;
        i++;
    }
    return -1;
}

static char tbuf[256];

static void run(void)
{
    static const char h[] = "m120: strategy distillation (R5) + audit self-improve (R6)\n";
    wr(h, sizeof(h) - 1);
    int pass_all = 1;
    u64 st[4];
    int i;

    sy(0x8101, 6, 0x3F, 0, 0, 0); /* 授权 exec 槽 (系统域) */
    sy(0x830C, (long)st, 0, 0, 0, 0);
    long calls_pre = (long)st[0];

    /* R5: 载入蒸馏字节码 */
    long rn = sy(0x830B, (long)RULEBOOK, RULEBOOK_LEN, 0, 0, 0);
    wrstr("m120: rulebook=");
    wrdec((u64)rn);
    wrstr(" (expect 19)\n");
    if (rn != 19)
        pass_all = 0;

    /* anom (duty2) */
    {
        static const char ev1[] = "ev pid=0 rate=99 wr=dead";
        static const char ev2[] = "ev pid=0 rate=5 wr=1";
        u64 a[3] = { 0, 0, 0 };
        sy(0x8304, (long)ev1, sizeof(ev1) - 1, (long)a, 24, 0);
        if (!(a[0] == 1 && a[1] == 80 && a[2] == 3))
            pass_all = 0;
        a[0] = a[1] = a[2] = 0;
        sy(0x8304, (long)ev2, sizeof(ev2) - 1, (long)a, 24, 0);
        if (!(a[0] == 0 && a[1] == 20 && a[2] == 3))
            pass_all = 0;
    }

    /* io (duty4): 周期 6 前缀 (0 1 2 3 4 -> 5) + (3 4 5 0 1 -> 2) */
    {
        u64 o[1] = { 0 };
        static const char s1[] = "0 1 2 3 4";
        static const char s2[] = "3 4 5 0 1";
        sy(0x8306, (long)s1, sizeof(s1) - 1, (long)o, 8, 0);
        if (o[0] != 5)
            pass_all = 0;
        o[0] = 0;
        sy(0x8306, (long)s2, sizeof(s2) - 1, (long)o, 8, 0);
        if (o[0] != 2)
            pass_all = 0;
    }

    /* plan (duty3): 启动 worker -> isolate task 1 (参数化 needle) -> resume */
    {
        long tid = sy(0x8105, 3, (long)&worker, 0, 0, 0);
        static const char g[] = "isolate task 1";
        u64 o[3] = { 0, 0, 0 };
        sy(0x8305, (long)g, sizeof(g) - 1, (long)o, 24, 0);
        wrstr("m120: plan isolate ok=");
        wrdec(o[0]);
        wrstr(" fail=");
        wrdec(o[1]);
        wrstr(" tid=");
        wrdec((u64)tid);
        wrstr(" (expect ok=1 fail=0 tid=1)\n");
        if (!(o[0] == 1 && o[1] == 0 && tid == 1))
            pass_all = 0;
        for (i = 0; i < 100; i++) {
            sy(0x8005, (long)tbuf, sizeof(tbuf), 0, 0, 0);
            if (strstr_pos(tbuf, "t1:2") >= 0)
                break;
        }
        if (strstr_pos(tbuf, "t1:2") < 0)
            pass_all = 0;
        sy(0x8105, 5, tid, 0, 0, 0); /* RESUME */
    }

    /* plan: a1 参数化 (threshold -> A4 1 70) */
    {
        static const char g[] = "set anomaly threshold to 70";
        u64 o[3] = { 0, 0, 0 };
        sy(0x8305, (long)g, sizeof(g) - 1, (long)o, 24, 0);
        long thr = sy(0x8106, 1, 0, 0, 0, 0);
        wrstr("m120: plan threshold ok=");
        wrdec(o[0]);
        wrstr(" cfg1=");
        wrdec((u64)thr);
        wrstr(" (expect ok=1 cfg1=70)\n");
        if (!(o[0] == 1 && o[1] == 0 && thr == 70))
            pass_all = 0;
    }

    /* nlc (duty5): ban games -> POL=3:1; 恢复 */
    {
        static const char g[] = "ban games 9 to 18";
        u64 o[1] = { 0 };
        sy(0x8307, (long)g, sizeof(g) - 1, (long)o, 8, 0);
        if (o[0] < 1)
            pass_all = 0;
        sy(0x8105, 4, 3, 0, 0, 0);
    }

    /* classify (duty1): 0x5101 离线也走规则字节码 */
    {
        static const char cmd[] = "run the game";
        int r = (int)sy(0x5101, (long)cmd, sizeof(cmd) - 1, 0, 0, 0);
        if (r != 1)
            pass_all = 0;
    }

    /* R6: 模型调用率 = 0 (蒸馏覆盖) + 规则命中 + 审计导出 engine=3 */
    {
        u64 st2[4];
        sy(0x830C, (long)st2, 0, 0, 0, 0);
        u64 aud[16 * 11]; /* 88B/条目 = 11 u64: engine/duty/out/a/b/result + 40B 文本 */
        long n = sy(0x830D, (long)aud, sizeof(aud), 0, 0, 0);
        int all_rulebook = 1;
        int saw_duty[7] = { 0, 0, 0, 0, 0, 0, 0 };
        for (i = 0; i < n && i < 16; i++) {
            if (aud[i * 11 + 0] == 0)
                continue; /* W19: boot 标记条目 (engine=0), 不属于规则引擎审计 */
            if (aud[i * 11 + 0] != 3)
                all_rulebook = 0;
            if (aud[i * 11 + 1] >= 1 && aud[i * 11 + 1] <= 6)
                saw_duty[aud[i * 11 + 1]] = 1;
        }
        wrstr("m120: stats calls=");
        wrdec(st2[0]);
        wrstr(" hits=");
        wrdec(st2[2]);
        wrstr(" aud=");
        wrdec((u64)n);
        wrstr(" allEngine3=");
        wrdec((u64)all_rulebook);
        wrstr(" duties=");
        {
            int k;
            for (k = 1; k <= 5; k++) {
                wrdec(saw_duty[k] ? 1u : 0u);
            }
        }
        wrstr("\n");
        if (!(st2[0] == 0 && (long)st2[0] == calls_pre && st2[2] >= 8 && n >= 8 && all_rulebook == 1
              && saw_duty[1] && saw_duty[2] && saw_duty[3] && saw_duty[4] && saw_duty[5]))
            pass_all = 0;
    }

    if (pass_all) {
        static const char m2[] = "m120: M120 RESULT: PASS\n";
        wr(m2, sizeof(m2) - 1);
    } else {
        static const char f[] = "m120: M120 RESULT: FAIL\n";
        wr(f, sizeof(f) - 1);
    }
    sy(60, 7, 0, 0, 0, 0);
    for (;;) {
    }
}

void _start(void)
{
    run();
    for (;;) {
    }
}
