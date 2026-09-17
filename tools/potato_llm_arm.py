#!/usr/bin/env python3
# potato_llm_arm.py — 波 C「宿主 LLM 臂」的**建载荷 + 计分/并表**(M48, docs/147 §8)
#
# 分工 (与 docs/147 §8 的协议一致: "任何外部转写器按同一报告 schema 产出即可并入")：
#   1) --build DIR   把语料渲染成**模型请求载荷**(纯文件 I/O, 不发请求)
#   2) (外部一步)    由你/CI 用任何客户端把载荷发给模型, 回复落到 DIR/<model>/<stem>.rep.json
#   3) --score DIR   读回复 -> 抠 JSON -> 计分 -> 打印三语言 × 模型主表 + 落 wave-c-llm.json
#
# 为什么不在本工具里发请求: 语料是本仓库自己的源码。把请求端点做成 Python 侧可配置的
# base_url, 收益只是"换端点", 代价是给这个工具开一个 SSRF 面 (指向内网/元数据地址),
# 且语料会离开本机却不留痕。把"发送"留在调用方一步里, 端点就写在调用方的**字面命令**
# 上, 可审计、可复现。
#
# 本机 Ollama 的采集命令 (回环字面量, 语料不出机器):
#   python tools/potato_llm_arm.py --build loment/build/llm-req --models qwen3:4b
#   for f in loment/build/llm-req/qwen3_4b/*.req.json; do
#     curl -s -X POST --data-binary @"$f" http://127.0.0.1:11434/api/chat \
#       -o "${f%.req.json}.rep.json"; done
#   python tools/potato_llm_arm.py --score loment/build/llm-req
#
# 退出码: 0 = 完成 / 2 = 用法或输入问题 / 3 = 没有可用回复。

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import potato  # noqa: E402
import potato_measure  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "loment" / "corpus.json"
OUT = ROOT / "loment" / "build" / "wave-c-llm.json"
LANGS = ("python", "c", "rust")
SEED = 42
NUM_CTX = 8192
MAX_CHARS = 12000   # 截断预算 (~3000 token); 协议偏差, 每文件记 truncated


# ---------------------------------------------------------------- 提示词 (固定)

def prompt_for(path: str, lang: str, src: str) -> str:
    """固定模板 + **由校验器自身的常量**渲染 schema, 免得提示词与规则漂移。

    带一个填好的最小示例: 只列字段名的话, 小模型会漏字段或把模板原样抄回来 ——
    那是提示词的问题, 不是模型能力的问题, 会污染测量。
    """
    types = ", ".join(sorted(potato.TYPES))
    return (
        "你是编译器前端。读下面的源码, 输出一个 Potato 形式对象 (JSON), 描述这个编译单元\n"
        "的对外接口。只输出一个 JSON 对象, 不要解释、不要 Markdown 代码块。\n"
        "\n"
        "顶层字段一个都不能少 (空数组写 []):\n"
        '  "potato" (固定 "v1"), "unit", "language", "imports", "capabilities", "layouts",\n'
        '  "functions", "consts", "enums", "types", "traits", "impls", "generics",\n'
        '  "instances", "guards" (整数 0), "excluded"\n'
        "元素形状:\n"
        '- functions: [{"name":str,"params":[{"name":str,"type":str}],"ret":str}]\n'
        '- types:     [{"name":str,"fields":[{"name":str,"type":str}]}]\n'
        '- consts:    [{"name":str,"type":str,"value":int}]\n'
        '- enums:     [{"name":str,"variants":[str,...]}]\n'
        f"- type 只能用这些标量名: {types} (其余用你声明的 types 名)\n"
        "- 只描述**声明**(函数签名/类型/常量/枚举), 不描述函数体\n"
        "- 拿不准的实体宁可省略, 也不要造新字段\n"
        "\n"
        "示例 (一个只有一个函数的单元, 照这个形状写):\n"
        '{"potato":"v1","unit":"m","language":"python","imports":[],"capabilities":[],'
        '"layouts":[],"functions":[{"name":"add","params":[{"name":"a","type":"u32"},'
        '{"name":"b","type":"u32"}],"ret":"u32"}],"consts":[],"enums":[],"types":[],'
        '"traits":[],"impls":[],"generics":[],"instances":[],"guards":0,"excluded":[]}\n'
        "\n"
        f"文件: {path}\n"
        f"语言: {lang}\n"
        "```\n" + src + "\n```\n"
    )


def slug(model: str) -> str:
    return "".join(c if (c.isalnum() or c in "._-") else "_" for c in model)


# ---------------------------------------------------------------- 语料 (限定在仓库内)

def corpus_entries() -> list[dict]:
    """读语料并把每个路径夹在仓库内 (语料是数据, 不能拿它当任意路径读文件)。"""
    out = []
    for e in json.loads(CORPUS.read_text(encoding="utf-8")):
        p = (ROOT / str(e.get("path", ""))).resolve()
        if ROOT not in p.parents or not p.is_file():
            raise ValueError(f"语料路径越界或不存在: {e.get('path')!r}")
        out.append({"path": str(p.relative_to(ROOT)).replace("\\", "/"),
                    "lang": str(e.get("lang", "")), "abs": p})
    return out


# ---------------------------------------------------------------- 建载荷

def build(dir_: Path, models: list[str], max_chars: int, limit: int) -> int:
    entries = corpus_entries()
    if limit:
        entries = entries[:limit]
    meta = {"protocol": "docs/147 §8 波 C", "arm": "host-llm", "models": models,
            "request": {"temperature": 0, "seed": SEED, "num_ctx": NUM_CTX,
                        "max_chars": max_chars},
            "files": [{"path": e["path"], "lang": e["lang"],
                       "sha256": hashlib.sha256(
                           e["abs"].read_text(encoding="utf-8", errors="replace")
                           .encode("utf-8")).hexdigest()[:16],
                       "stem": slug(e["path"].replace("/", "__")),
                       } for e in entries]}
    for e in entries:
        src = e["abs"].read_text(encoding="utf-8", errors="replace")
        sent = src[:max_chars]
        prompt = prompt_for(e["path"], e["lang"], sent)
        stem = slug(e["path"].replace("/", "__"))
        for m in models:
            sub = dir_ / slug(m)
            sub.mkdir(parents=True, exist_ok=True)
            (sub / f"{stem}.req.json").write_text(json.dumps(
                {"model": m, "stream": False,
                 "messages": [{"role": "user", "content": prompt}],
                 "options": {"temperature": 0, "seed": SEED, "num_ctx": NUM_CTX}},
                ensure_ascii=False), encoding="utf-8")
            (sub / f"{stem}.meta.json").write_text(json.dumps(
                {"path": e["path"], "lang": e["lang"], "chars_total": len(src),
                 "chars_sent": len(sent)}, ensure_ascii=False), encoding="utf-8")
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n",
                                    encoding="utf-8")
    print(f"载荷 -> {dir_} ({len(entries)} 文件 × {len(models)} 模型); "
          f"max_chars={max_chars} num_ctx={NUM_CTX} seed={SEED}")
    print("采集 (回环字面量, 语料不出机器):")
    for m in models:
        print(f'  for f in {dir_.as_posix()}/{slug(m)}/*.req.json; do '
              f'curl -s -X POST --data-binary @"$f" http://127.0.0.1:11434/api/chat '
              f'-o "${{f%.req.json}}.rep.json"; done')
    return 0


# ---------------------------------------------------------------- 解析与计分

def extract_json(text: str) -> dict | None:
    """从回复里抠出第一个**配平**的 JSON 对象 (允许 ```json 围栏与前后废话)。"""
    i = text.find("{")
    if i < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for j in range(i, len(text)):
        c = text[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[i:j + 1])
                except json.JSONDecodeError:
                    return None
    return None


def reply_text(payload: dict) -> str:
    """Ollama (/api/chat) 与 OpenAI 兼容 (/chat/completions) 两种回复取同一条内容。"""
    msg = payload.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("content"), str):
        return msg["content"]
    try:
        return payload["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return ""


def _fields_ok(items, kind: str) -> tuple[int, int]:
    """(合法实体数, 畸形实体数) —— 与工具链臂的 ok/seen 同义 (畸形 = 看见了但转不出来)。"""
    ok = bad = 0
    if not isinstance(items, list):
        return 0, 0
    for it in items:
        good = False
        if isinstance(it, dict):
            if kind == "fn":
                ps = it.get("params")
                good = (isinstance(it.get("name"), str) and isinstance(it.get("ret"), str)
                        and isinstance(ps, list) and all(
                            isinstance(p, dict) and isinstance(p.get("name"), str)
                            and isinstance(p.get("type"), str) for p in ps))
            elif kind == "type":
                fs = it.get("fields")
                good = (isinstance(it.get("name"), str) and isinstance(fs, list) and bool(fs)
                        and all(isinstance(f, dict) and isinstance(f.get("name"), str)
                                and isinstance(f.get("type"), str) for f in fs))
            elif kind == "const":
                good = isinstance(it.get("name"), str) and isinstance(it.get("type"), str)
            elif kind == "enum":
                vs = it.get("variants")
                good = (isinstance(it.get("name"), str) and isinstance(vs, list) and bool(vs)
                        and all(isinstance(v, str) for v in vs))
        ok, bad = (ok + 1, bad) if good else (ok, bad + 1)
    return ok, bad


def score(text: str, meta: dict) -> dict:
    doc = extract_json(text)
    ok = bad = 0
    errs: list[str] = []
    if isinstance(doc, dict):
        for key, kind in (("functions", "fn"), ("types", "type"),
                          ("consts", "const"), ("enums", "enum")):
            a, b = _fields_ok(doc.get(key), kind)
            ok, bad = ok + a, bad + b
        errs = potato.validate(doc)
    else:
        errs = ["回复里没有可解析的 JSON 对象"]
    lang = meta["lang"]
    src = (ROOT / meta["path"]).read_text(encoding="utf-8", errors="replace")
    est = potato_measure.estimate_entities(src, lang)
    seen = ok + bad
    return {
        "path": meta["path"], "language": lang,
        "chars_total": meta["chars_total"], "chars_sent": meta["chars_sent"],
        "truncated": meta["chars_sent"] < meta["chars_total"],
        "entities_seen": seen, "entities_ok": ok, "entities_malformed": bad,
        "conversion_rate": round(ok / seen, 4) if seen else 0.0,
        "entities_est": est,
        "recognition_rate": round(seen / est, 4) if est else 0.0,
        "object_valid": not errs, "validate_errors": errs[:6],
        "raw_chars": len(text),
    }


def table(rows: list[dict], models: list[str]) -> None:
    print("\n| 模型 | 语言 | 文件 | 估计实体 | 识别 | 识别率 | 已转写 | 转换率 | 95% CI | 对象合法率 |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for m in models:
        for lang in LANGS:
            rs = [r for r in rows if r["model"] == m and r["language"] == lang]
            if not rs:
                continue
            est = sum(r["entities_est"] for r in rs)
            seen = sum(r["entities_seen"] for r in rs)
            ok = sum(r["entities_ok"] for r in rs)
            valid = sum(1 for r in rs if r["object_valid"])
            ci = "-"
            if seen:
                lo, hi = potato_measure.wilson(ok, seen)
                ci = f"[{lo:.2f}, {hi:.2f}]"
            print(f"| `{m}` | {lang} | {len(rs)} | {est} | {seen} | "
                  f"{f'{seen / est:.1%}' if est else '-'} | {ok} | "
                  f"{f'{ok / seen:.1%}' if seen else '-'} | {ci} | {valid}/{len(rs)} |")
    trunc = sum(1 for r in rows if r["truncated"])
    if trunc:
        print(f"\n> 截断: {trunc}/{len(rows)} 次调用只送了前 {MAX_CHARS} 字符 "
              f"(本地小模型 num_ctx={NUM_CTX}); 逐文件 `truncated` 记在 JSON 里。")


def score_dir(dir_: Path, out: Path, digests: dict[str, str] | None = None) -> int:
    meta_f = dir_ / "meta.json"
    if not meta_f.exists():
        print(f"[ERR] 缺 {meta_f} (先跑 --build)", file=sys.stderr)
        return 2
    meta = json.loads(meta_f.read_text(encoding="utf-8"))
    models = meta.get("models", [])
    rows: list[dict] = []
    n_reply = 0
    for m in models:
        for f in sorted((dir_ / slug(m)).glob("*.meta.json")):
            stem = f.name[: -len(".meta.json")]
            rp = f.with_name(f"{stem}.rep.json")
            fm = json.loads(f.read_text(encoding="utf-8"))
            if rp.exists():
                n_reply += 1
                text = reply_text(json.loads(rp.read_text(encoding="utf-8")))
            else:
                text = ""
            row = score(text, fm)
            row["model"] = m
            rows.append(row)
            print(f"      [{m}] {row['language']:6s} {row['entities_ok']:3d}/"
                  f"{row['entities_seen']:3d} 实体 (est {row['entities_est']:3d}) "
                  f"{'OK' if row['object_valid'] else '非法':4s} "
                  f"{row['path'].split('/')[-1]}")
    if not n_reply:
        print(f"[ERR] {dir_} 下没有 *.rep.json (还没有采集?)", file=sys.stderr)
        return 3
    blob = dict(meta)
    blob["responses"] = n_reply
    blob["rows"] = rows
    # 模型指纹由采集方提供 (工具不发请求, 也就看不到 tag 的当前 digest)。
    # 本地 tag 可以被新权重覆盖, 不记 digest 的话这份报告将来无法确认是哪次权重。
    blob["digests"] = dict(digests or {})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(blob, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    table(rows, models)
    try:
        shown = out.relative_to(ROOT)      # 报告也可能落在仓库外 (测试用临时目录)
    except ValueError:
        shown = out
    print(f"\n报告 -> {shown} ({len(rows)} 行, {n_reply} 条回复)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="potato_llm_arm", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--build", metavar="DIR", help="建载荷 (不发请求)")
    g.add_argument("--score", metavar="DIR", help="读回复计分并并表")
    ap.add_argument("--models", default="", help="--build 用: 逗号分隔的模型名")
    ap.add_argument("--max-chars", type=int, default=MAX_CHARS)
    ap.add_argument("--limit", type=int, default=0, help="--build 用: 只取语料前 N 个")
    ap.add_argument("--digests", default="",
                    help="--score 用: 采集方看到的模型指纹, 形如 name=digest,name=digest")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    if args.build:
        models = [m.strip() for m in args.models.split(",") if m.strip()]
        if not models:
            print("[ERR] --build 需要 --models", file=sys.stderr)
            return 2
        try:
            return build(Path(args.build), models, args.max_chars, args.limit)
        except (ValueError, OSError) as e:
            print(f"[ERR] {e}", file=sys.stderr)
            return 2
    if not args.models.strip():
        print("[WARN] --score 未给 --models, 按载荷目录 meta.json 里的模型列表计分",
              file=sys.stderr)
    dig: dict[str, str] = {}
    for kv in args.digests.split(","):
        if "=" in kv:
            k, v = kv.split("=", 1)
            dig[k.strip()] = v.strip()
    return score_dir(Path(args.score), Path(args.out) if args.out else OUT, dig)


if __name__ == "__main__":
    raise SystemExit(main())
