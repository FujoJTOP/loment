/* m149_trust.c — W32: 信任自适应域 (zcode 框架: 质量 → 域宽 = f(质量), docs/99)
 *
 * 安全三命题落地验证:
 *   S1 机制: 任何动作集 ⊆ 域∩授权 (域门不变, 由 m116/m144 验证);
 *   S2 委托: 质量 = 规则盲区覆盖度 (本 demo 构造高/低质量场景);
 *   S3 政策: 阈值由人定 (cfg7/8, 默认 70/30)。
 * 新机制: 质量台账 (0x8314) → dom_admit (0x8313) → 当前域宽随质量加宽/收缩。
 *
 *   T1 绑定域 1 (perm=0, 无授权)
 *   T2 高质量 (io 12 命中) -> dom_admit(4) -> 加宽 (perm=ALL 0x7F; W36: ALL_ACTS
 *     含 act7 BOX_CMD, 0x3F→0x7F)
 *   T3 加宽验证: 0x810A 域表读回 perm==0x7F
 *   T4 低质量 (anom 40 次 miss, 率<30) -> dom_admit(2) -> 收缩 (perm=仅 ACK)
 *   T5 收缩验证: perm==0x20 (ACK) 且 cap_exec(ISOLATE) 被拒 (-1) 且审计记 deny
 *   T6 回系统域
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

__attribute__((noinline, noreturn)) static void worker(void)
{
    static const char m[] = "m149: worker running\n";
    wr(m, sizeof(m) - 1);
    volatile u64 x = 0;
    for (;;) {
        x += 1;
        if (x > 0xFFFF0000ul)
            x = 0;
    }
}

static int run(void)
{
    static const char h[] = "m149: trust-adaptive domains (quality -> domain width)\n";
    wr(h, sizeof(h) - 1);
    int pass_all = 1;
    u64 info[25];
    int i;

    sy(0x8101, 6, 0x3F, 0, 0, 0);

    /* T1 域 1: perm=0 绑定 */
    long d1 = sy(0x8107, 0, 1, 0, 0, 0);
    sy(0x8108, d1, 0, 0, 0, 0);
    sy(0x810A, (long)info, 0, 0, 0, 0);
    wrstr("m149: T1 domain=");
    wrdec((u64)d1);
    wrstr(" perm0=");
    wrdec(info[d1 * 5 + 1]);
    wrstr("\n");
    if (d1 < 1 || info[d1 * 5 + 1] != 0)
        pass_all = 0;

    /* T2 高质量: io duty=4 命中 12 次 (率=100) —— A7-② 滞后对齐:
     * 第 1 次 admit 应"维持"(连续高 < WIDEN_CONFIRM=2), 第 2 次才"加宽"。 */
    if (d1 >= 1) {
        for (i = 0; i < 12; i++)
            sy(0x8314, 4, 1, 0, 0, 0);
        u64 o[3] = { 0, 0, 0 };
        long rc1 = sy(0x8313, 4, (long)o, 0, 0, 0);
        sy(0x810A, (long)info, 0, 0, 0, 0);
        wrstr("m149: T2a admit#1 rc=");
        wrdec((u64)rc1);
        wrstr(" rate=");
        wrdec(o[0]);
        wrstr(" perm=");
        wrdec(info[d1 * 5 + 1]);
        wrstr(" (expect 0/>=70/0: widened LAGGED)\n");
        if (!(rc1 == 0 && o[0] >= 70 && info[d1 * 5 + 1] == 0))
            pass_all = 0;
        o[0] = 0;
        long rc2 = sy(0x8313, 4, (long)o, 0, 0, 0);
        sy(0x810A, (long)info, 0, 0, 0, 0);
        wrstr("m149: T2b admit#2 rc=");
        wrdec((u64)rc2);
        wrstr(" rate=");
        wrdec(o[0]);
        wrstr(" perm=");
        wrdec(info[d1 * 5 + 1]);
        wrstr(" (expect 1/>=70/0x7F: widened after confirm)\n");
        if (!(rc2 == 1 && o[0] >= 70 && info[d1 * 5 + 1] == 0x7F))
            pass_all = 0;
    }

    /* T3 加宽后: 域 1 下 KILL 应授权成功 (域宽=质量) */
    {
        long tid = sy(0x8105, 3, (long)&worker, 0, 0, 0);
        long rc = sy(0x8105, 1, tid, 0, 0, 0);
        wrstr("m149: T3 widened kill rc=");
        wrdec((u64)rc);
        wrstr(" (expect 0)\n");
        if (rc != 0)
            pass_all = 0;
    }

    /* T4 低质量: anom duty=2 miss 40 次 (率→0 < τ_low=30 → 收缩) */
    {
        for (i = 0; i < 40; i++)
            sy(0x8314, 2, 0, 0, 0, 0);
        u64 o[3] = { 0, 0, 0 };
        long rc = sy(0x8313, 2, (long)o, 0, 0, 0);
        sy(0x810A, (long)info, 0, 0, 0, 0);
        wrstr("m149: T4 shrink rc=");
        wrdec((u64)rc);
        wrstr(" rate=");
        wrdec(o[0]);
        wrstr(" perm=");
        wrdec(info[d1 * 5 + 1]);
        wrstr(" granted=");
        wrdec(info[d1 * 5 + 2]);
        wrstr(" (expect 2/0/0x20/1)\n");
        if (!(rc == 2 && o[0] < 30 && info[d1 * 5 + 1] == 0x20 && info[d1 * 5 + 2] == 1))
            pass_all = 0;
    }

    /* T5 收缩后: 域 1 下 ISOLATE 被拒 (域宽收缩 → 越权被拒, α 兜底) */
    {
        long tid = sy(0x8105, 3, (long)&worker, 0, 0, 0);
        long rc = sy(0x8105, 2, tid, 0, 0, 0); /* ISOLATE 未授权 (仅 ACK) */
        wrstr("m149: T5 shrunk isolate rc=");
        wrdec((u64)rc);
        wrstr(" (expect -1)\n");
        if (rc != -1)
            pass_all = 0;
        sy(0x8105, 1, tid, 0, 0, 0);
    }

    sy(0x8108, 0, 0, 0, 0, 0); /* 回系统域 */
    sy(0x8109, d1, 0, 0, 0, 0);

    if (pass_all) {
        static const char m2[] = "m149: M149 RESULT: PASS\n";
        wr(m2, sizeof(m2) - 1);
    } else {
        static const char f[] = "m149: M149 RESULT: FAIL\n";
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
