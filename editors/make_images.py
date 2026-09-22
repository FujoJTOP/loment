"""生成 README 里的示意图 (banner 之外那三张)。

配色与 `loment-banner.png` 同一套 —— 深蓝底 + 白字, 亮色/暗色主题下都读得清。
2 倍超采样再缩回, 免得字边发木。

    python editors/make_images.py

产物:
    editors/loment-pipeline.png      一个程序是怎么编出来的
    editors/loment-bootstrap.png     自举: 种子 -> stage1 -> stage2 -> stage3
    editors/loment-diagnostic.png    报错器渲染的一张说明卡 (真实输出)
"""
from PIL import Image, ImageDraw, ImageFont, ImageFilter

S = 2                                   # 超采样
BG_TOP = (13, 17, 40)
BG_BOT = (24, 31, 70)
PANEL = (26, 33, 72)
PANEL_EDGE = (58, 74, 140)
ACCENT = (108, 137, 253)
ACCENT_DK = (72, 100, 210)
WHITE = (255, 255, 255)
SOFT = (226, 232, 255)
DIM = (150, 164, 205)
GREEN = (126, 211, 154)
RED = (236, 126, 126)
CYAN = (120, 200, 226)
AMBER = (230, 190, 120)

UI = r"C:\Windows\Fonts\segoeui.ttf"
UIB = r"C:\Windows\Fonts\segoeuib.ttf"
MONO = r"C:\Windows\Fonts\consola.ttf"
MONOB = r"C:\Windows\Fonts\consolab.ttf"


def canvas(w, h):
    img = Image.new("RGB", (w * S, h * S), BG_TOP)
    d = ImageDraw.Draw(img)
    for y in range(h * S):
        t = y / (h * S - 1)
        d.line([(0, y), (w * S, y)],
               fill=tuple(round(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOT)))
    return img


def rc(d, box, r, fill=None, outline=None, width=1):
    d.rounded_rectangle([box[0] * S, box[1] * S, box[2] * S, box[3] * S],
                        radius=r * S, fill=fill, outline=outline, width=width * S)


def txt(d, xy, s, font, fill, anchor="la"):
    d.text((xy[0] * S, xy[1] * S), s, font=font, fill=fill, anchor=anchor)


def font(path, size):
    return ImageFont.truetype(path, size * S)


def arrow(d, x1, x2, y, color=ACCENT, head=7):
    d.line([(x1 * S, y * S), ((x2 - head) * S, y * S)], fill=color, width=2 * S)
    d.polygon([((x2 - head * 1.9) * S, (y - head) * S), ((x2 - head * 1.9) * S, (y + head) * S),
               (x2 * S, y * S)], fill=color)


def save(img, w, h, path):
    img.resize((w, h), Image.LANCZOS).save(path, optimize=True)
    print("wrote", path)


# ---------------------------------------------------------------- 标题图

def banner():
    """标题图: 图标 + 三行白字 + 一层很淡的代码底纹。

    底纹只放**代码片段**, 不放任何"说法" (不写 no libc / 几个语法 之类) ——
    那些是会随 0.1.4 的计划变的断言, 钉进一张图里就等着过期。所以它只是装饰,
    压得很低 (离背景只差十几个色阶), 亮暗主题下都不抢字。
    """
    W, H = 1040, 280
    img = canvas(W, H).convert("RGBA")

    # 右边一栏代码, 左端渐隐 —— 看得见是代码, 但整栏都在文字区右侧
    code = [
        "module hello",
        "fn _start() {",
        '    let s: str = "hello\\n";',
        "    syscall4(1, 1, str_ptr(s) as u64);",
        "    syscall4(60, 0, 0, 0);",
        "}",
        "capability blk : disk[0..4]",
        "guard blk(i);",
        "pub extern fn c_add(a: i32, b: i32) -> i32;",
    ]
    tex = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    td = ImageDraw.Draw(tex)
    f_code = font(MONO, 14)
    y = 28
    for line in code:
        td.text((648 * S, y * S), line, font=f_code, fill=(104, 126, 212))
        y += 27

    mask = Image.new("L", (W * S, H * S), 0)
    md = ImageDraw.Draw(mask)
    for x in range(W * S):                      # 横向 ramp: 左边全透明, 右边全不透明
        t = min(1.0, max(0.0, (x / S - 596) / 150))
        md.line([(x, 0), (x, H * S)], fill=int(255 * t))
    tex.putalpha(mask)
    img = Image.alpha_composite(img, tex)

    glow = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse(
        [(34 * S, 30 * S), (222 * S, 218 * S)], fill=(84, 115, 227, 74))
    img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(23 * S)))

    icon = Image.open("editors/loment.ico")
    icon.size = (256, 256)
    icon = icon.convert("RGBA").resize((168 * S, 168 * S), Image.LANCZOS)
    img.paste(icon, (44 * S, 50 * S), icon)
    d = ImageDraw.Draw(img)

    # 文字块左侧那道竖条
    d.rounded_rectangle([242 * S, 64 * S, 247 * S, 202 * S], radius=2 * S, fill=ACCENT)

    X = 268
    txt(d, (X, 100), "Loment", font(UIB, 72), WHITE, anchor="lm")
    txt(d, (X + 2, 156), "Programming Language", font(UI, 28), (236, 240, 255), anchor="lm")
    txt(d, (X + 2, 191), "Program by Fujo", font(UI, 21), (186, 199, 238), anchor="lm")

    save(img.convert("RGB"), W, H, "editors/loment-banner.png")


# ---------------------------------------------------------------- 管线图

def pipeline():
    W, H = 1400, 330
    img = canvas(W, H)
    d = ImageDraw.Draw(img)

    f_title = font(UIB, 25)
    f_name = font(MONOB, 19)
    f_sub = font(UI, 14)

    txt(d, (44, 40), "How a program is built", f_title, WHITE)

    bx, by, bw, bh, gap = 44, 108, 232, 108, 48
    boxes = [
        ("hello.lomt", ["Loment source", "any of the six", "surface syntaxes"]),
        ("loment-driver", ["load  ·  check", "emit  LLVM IR", "written in Loment"]),
        ("hello.ll", ["LLVM IR", "text, not bytes", "the only handover"]),
        ("loment-lomelf", ["the linker,", "also in Loment", "no libc, no ld"]),
        ("hello", ["ELF  ·  PE  ·  bare", "static, one file", "8 KB"]),
    ]
    for i, (name, subs) in enumerate(boxes):
        x = bx + i * (bw + gap)
        last = i == len(boxes) - 1
        rc(d, (x, by, x + bw, by + bh), 12,
           fill=(30, 40, 90) if last else PANEL,
           outline=ACCENT if last else PANEL_EDGE, width=2 if last else 1)
        txt(d, (x + bw / 2, by + 30), name, f_name, ACCENT if last else WHITE, anchor="mm")
        for k, s in enumerate(subs):
            if s:
                txt(d, (x + bw / 2, by + 58 + k * 19), s, f_sub, DIM, anchor="mm")
        if not last:
            arrow(d, x + bw + 10, x + bw + gap - 8, by + bh / 2)

    f_note = font(UI, 15)
    txt(d, (44, 258), "No runtime and no libc go into the output; no Python and no C compiler into the build.",
        f_note, SOFT)
    txt(d, (44, 284), "That same compiler is what compiles the compiler.",
        f_note, DIM)
    save(img, W, H, "editors/loment-pipeline.png")


# ---------------------------------------------------------------- 自举图

def bootstrap():
    """自举那四条证明 —— 按 `loment/bootstrap.sh` 打印的 1/4..4/4 排。"""
    W, H = 1400, 412
    img = canvas(W, H)
    d = ImageDraw.Draw(img)

    f_title = font(UIB, 25)
    f_step = font(MONOB, 16)
    f_flow = font(MONOB, 17)
    f_why = font(UI, 15)
    f_note = font(UI, 15)

    txt(d, (44, 40), "How the compiler is rebuilt from itself", f_title, WHITE)

    rows = [
        ("1/4", "seed  ->  stage1", "the genesis assembler, committed to the repo, builds a compiler", ACCENT),
        ("2/4", "stage1  compiles the compiler", "the result must equal the seed, byte for byte", GREEN),
        ("3/4", "stage2  ->  stage3", "and again — the chain reaches a fixed point", CYAN),
        ("4/4", "stage1  vs  stage2", "handed a foreign entry file, the two stages agree", AMBER),
    ]
    y = 116
    for step, flow, why, col in rows:
        rc(d, (44, y - 19, 106, y + 11), 8, fill=(28, 36, 80), outline=PANEL_EDGE)
        txt(d, (75, y - 4), step, f_step, col, anchor="mm")
        txt(d, (128, y - 5), flow, f_flow, WHITE, anchor="lm")
        txt(d, (560, y - 5), why, f_why, DIM, anchor="lm")
        y += 52

    txt(d, (44, y + 16),
        "All four come from one script — `sh loment/bootstrap.sh`. It needs POSIX sh on x86-64 Linux",
        f_note, SOFT)
    txt(d, (44, y + 40),
        "and nothing else: no Python, no interpreter, no C compiler.",
        f_note, DIM)
    save(img, W, H, "editors/loment-bootstrap.png")


# ---------------------------------------------------------------- 报错卡

def diagnostic():
    """`loment check` 的一张说明卡 —— **逐字原文**, 只按窗口宽度折行。

    折行是终端自己做的事, 所以续行**不加 `  | ` 前缀**, 也不删任何一条建议:
    这是截图, 不是摘要。
    """
    mono, monob = font(MONO, 15), font(MONOB, 15)
    W = 1120
    pad, top, lh, bottom = 30, 66, 25, 34
    bar = [("  | ", DIM, False)]

    def cont(s):
        return [("     ", DIM, False), (s, SOFT, False)]

    lines = [
        [("error[E002]: ", RED, True), ("undeclared name", WHITE, True)],
        [("  --> ", DIM, False), ("bad.lomt:4", CYAN, False)],
        [("  |", DIM, False)],
        [("4 | ", DIM, False), ("    return z;", SOFT, False)],
        [("  | ", DIM, False), ("    ", DIM, False), ("^^^^^^^^^", RED, False)],
        bar + [("message: ", WHITE, True), ("z", SOFT, False)],
        bar + [("what went wrong: ", WHITE, True),
               ("A name was used without being declared - a variable, a function, a type", SOFT, False)],
        cont("or a constant."),
        bar + [("why: ", WHITE, True),
               ("This is the one beginners hit most, and in Loment it is almost", SOFT, False)],
        cont("always the same thing: **there is no type inference** - `let x = 1;` is a"),
        cont("syntax error and you must write `let x: u32 = 1;`. Next comes defining after"),
        cont("use inside one module, and then a cross-module name that needs `pub` on the"),
        cont("declaration and `use` at the call site."),
        bar + [("how to fix:", WHITE, True)],
        [("  |   ", DIM, False),
         ("1. Add the type annotation: `let x: u32 = 1;` (a `let` with no type is", SOFT, False)],
        cont("really E019, a syntax error, but it is easy to read as this one)."),
        [("  |   ", DIM, False),
         ("2. Misspelt: fix the spelling. Cross-module: put `pub` on the declaration", SOFT, False)],
        cont('and `use <module>` or `use "./x.lomt"` at the call site.'),
        [("  |   ", DIM, False),
         ("3. Generic functions need their arguments to pin down `T`: `let a: u32 = 4;`", SOFT, False)],
        cont("then `pick(a, b)`. Passing integer literals straight in reports this code,"),
        cont("and that message is misleading."),
        [("  |   ", DIM, False),
         ('4. The name really does come from elsewhere: name its location with the path', SOFT, False)],
        cont('form `use "./util.lomt"`.'),
        bar + [("supported: ", GREEN, True),
               ("generics (when the call site determines T) / static trait dispatch /", SOFT, False)],
        cont("`pub` across modules / both `use` forms"),
        bar + [("not supported: ", RED, True),
               ("type inference / implicit globals / forward references inside one", SOFT, False)],
        cont("module / implicit numeric conversion"),
        [],
        [("1 errors: ", SOFT, False), ("E002 x1", RED, False)],
        [("more: ", DIM, False), ("loment explain E002", SOFT, False)],
    ]

    H = top + len(lines) * lh + bottom
    img = canvas(W, H).convert("RGB")
    d = ImageDraw.Draw(img)

    rc(d, (16, 16, W - 16, H - 16), 12, fill=(12, 16, 34), outline=PANEL_EDGE, width=2)
    d.line([(16 * S, 56 * S), ((W - 16) * S, 56 * S)], fill=PANEL_EDGE, width=S)
    for i, c in enumerate([(255, 95, 87), (254, 188, 46), (40, 200, 64)]):
        d.ellipse([(34 + i * 22) * S, 30 * S, (44 + i * 22) * S, 40 * S], fill=c)
    txt(d, (W / 2, 36), "loment check bad.lomt", font(MONO, 14), DIM, anchor="mm")

    y = top + 20
    for runs in lines:
        x = pad + 8
        for s, col, bold in runs:
            f = monob if bold else mono
            d.text((x * S, y * S), s, font=f, fill=col, anchor="la")
            x += d.textlength(s, font=f) / S
        y += lh

    save(img, W, H, "editors/loment-diagnostic.png")


if __name__ == "__main__":
    banner()
    pipeline()
    bootstrap()
    diagnostic()
