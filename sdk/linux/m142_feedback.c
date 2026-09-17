/* m142_feedback.c — W22: 自监督反馈闭环 (anom 建议 → 行动 → 验证 → 审计标签)
 *
 * 链路: 异常事件摘要 → 0x8304 哨兵 (anom=1) → 内核自动隔离
 *       cap_exec(ACT_ISOLATE) → 内核查 task_state(pid)==2 证实建议
 *       (fb_verified=1) → 审计环 (0x830D) 尾条 result 字段 = 自监督标签。
 * 这是"模型建议被行动证据证实/证伪"的最小闭环 —— 反馈标签直接进审计,
 * 供蒸馏候选 (命中的 novel 样本) 与论文的自监督数据回路。
 *
 * T1 启动 worker (0x8105 LAUNCH) -> tid
 * T2 开自动隔离 (cfg2=1 阈值 50)
 * T3 [auto 完整路径] 注入异常 (pid=tid rate=99 wr=dead) -> 自动隔离 ->
 *    审计尾条 result==1 (verified) 且 b==0 (iso ok); 0x8005 状态==2 (双证据)
 * T4 [force=rules] 正常事件 (pid=0 rate=3 wr=ok) -> 审计尾条 result==0
 * T5 resume -> 状态==1 (系统继续); kill 清理
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
    static const char m[] = "m142: worker running\n";
    wr(m, sizeof(m) - 1);
    volatile u64 x = 0;
    for (;;) {
        x += 1;
        if (x > 0xFFFF0000ul)
            x = 0;
    }
}

/* 审计环尾条 (0x830D, 88B: engine/duty/out/a/b/result + text 40B) */
static unsigned char aud[88];
static int aud_tail(void)
{
    int n = (int)sy(0x830D, (long)aud, 88, 0, 0, 0);
    return n;
}

static long aud_result(void)
{
    long v = 0;
    int i;
    for (i = 0; i < 8; i++)
        v |= ((long)aud[40 + i]) << (8 * i);
    return v;
}

static long aud_b(void)
{
    long v = 0;
    int i;
    for (i = 0; i < 8; i++)
        v |= ((long)aud[32 + i]) << (8 * i);
    return v;
}

static int run(void)
{
    static const char h[] = "m142: self-supervised feedback (anom -> act -> verify)\n";
    wr(h, sizeof(h) - 1);
    int pass_all = 1;
    u64 o[3];
    int i, n;

    sy(0x8101, 6, 0x3F, 0, 0, 0);

    /* T1 启动 worker */
    long tid = sy(0x8105, 3, (long)&worker, 0, 0, 0);
    wrstr("m142: T1 launch worker tid=");
    wrdec((u64)tid);
    wrstr("\n");
    if (tid < 0)
        pass_all = 0;

    /* T2 自动隔离开 (cfg2=1, 阈值 50) */
    sy(0x8105, 4, 2, 1, 0, 0);
    sy(0x8105, 4, 1, 50, 0, 0);
    wrstr("m142: T2 cfg auto-isolate=1 thresh=50\n");

    /* T3 [auto] 注入异常 -> 自动隔离 -> 验证位 */
    {
        static char ev[64];
        int k = 0;
        const char *p = "ev pid=";
        char nb[10];
        u64 v;
        for (i = 0; p[i]; i++)
            ev[k++] = p[i];
        v = (u64)tid;
        n = 0;
        do {
            nb[n++] = (char)('0' + v % 10);
            v /= 10;
        } while (v);
        while (n)
            ev[k++] = nb[--n];
        p = " rate=99 wr=dead";
        for (i = 0; p[i]; i++)
            ev[k++] = p[i];
        for (i = 0; i < 3; i++)
            o[i] = 0;
        sy(0x8304, (long)ev, k, (long)o, 24, 0);
        wrstr("m142: T3 sentinel anom=");
        wrdec(o[0]);
        wrstr(" conf=");
        wrdec(o[1]);
        wrstr(" engine=");
        wrdec(o[2]);
        wrstr("\n");
        if (!(o[0] == 1 && o[2] >= 2))
            pass_all = 0;
        int got = aud_tail();
        long res = aud_result();
        long b = aud_b();
        wrstr("m142: T3 audit verified=");
        wrdec((u64)res);
        wrstr(" iso_rc=");
        wrdec((u64)b);
        wrstr(" (expect 1/0)\n");
        if (!(got >= 1 && res == 1 && b == 0))
            pass_all = 0;
    }

    /* T3b 结构态双证据: task_state(tid)==2 */
    {
        u64 st[2] = { 0, 0 };
        /* 0x8005: 结构态文本; 找 "t<tid>:" 断言状态字段 —— 简化用任务状态查询
         * 无独立接口, 0x8005 文本含 "t<id>:<state>" (m112 同款)。 */
        char tbuf[256];
        int tn = (int)sy(0x8005, (long)tbuf, sizeof(tbuf), 0, 0, 0);
        (void)st;
        (void)tn;
        /* 构造 needle "t<tid>:2" */
        char need[16];
        int k = 0;
        const char *pre = "t";
        for (i = 0; pre[i]; i++)
            need[k++] = pre[i];
        {
            char nb[10];
            int n2 = 0;
            u64 v = (u64)tid;
            do {
                nb[n2++] = (char)('0' + v % 10);
                v /= 10;
            } while (v);
            while (n2)
                need[k++] = nb[--n2];
        }
        need[k++] = ':';
        need[k++] = '2';
        need[k] = 0;
        /* 子串查 (无视 0x8005 具体布局, 只查 "t<id>:2") */
        long found = -1;
        int j = 0;
        while (tbuf[j] && found < 0) {
            int m = 0;
            while (need[m] && tbuf[j + m] == need[m])
                m++;
            if (need[m] == 0)
                found = j;
            j++;
        }
        wrstr("m142: T3b task state needle=");
        wrstr(need);
        wrstr(" found=");
        wrdec(found >= 0 ? 1 : 0);
        wrstr("\n");
        if (found < 0)
            pass_all = 0;
    }

    /* T4 [force=rules] 正常事件 -> 审计尾条 result==0 */
    sy(0x830F, 2, 0, 0, 0, 0);
    {
        static const char ok[] = "ev pid=0 rate=3 wr=ok";
        for (i = 0; i < 3; i++)
            o[i] = 0;
        sy(0x8304, (long)ok, sizeof(ok) - 1, (long)o, 24, 0);
        wrstr("m142: T4 normal anom=");
        wrdec(o[0]);
        wrstr(" (expect 0)\n");
        if (!(o[0] == 0))
            pass_all = 0;
        int got = aud_tail();
        long res = aud_result();
        wrstr("m142: T4 audit verified=");
        wrdec((u64)res);
        wrstr(" (expect 0)\n");
        if (!(got >= 1 && res == 0))
            pass_all = 0;
    }
    sy(0x830F, 0, 0, 0, 0, 0);

    /* T5 resume -> 状态 1 (系统继续) */
    {
        long rc = sy(0x8105, 5, tid, 0, 0, 0);
        wrstr("m142: T5 resume rc=");
        wrdec((u64)rc);
        wrstr("\n");
        if (rc != 0)
            pass_all = 0;
    }
    sy(0x8105, 1, tid, 0, 0, 0); /* 清理 */
    sy(0x8105, 4, 2, 0, 0, 0); /* 关自动隔离 */

    if (pass_all) {
        static const char m2[] = "m142: M142 RESULT: PASS\n";
        wr(m2, sizeof(m2) - 1);
    } else {
        static const char f[] = "m142: M142 RESULT: FAIL\n";
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
