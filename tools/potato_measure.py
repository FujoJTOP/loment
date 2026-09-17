#!/usr/bin/env python3
# potato_measure.py — 波 C 结构识别转换率测量 (M48/M54, docs/147 §测量协议)
#
# 主表: 三语言 (Python/C/Rust) × 两臂 (工具链-严格 / 工具链-宽松)。
# 每臂对同一份语料 (loment/corpus.json, 含 sha256 固定) 跑 potato_from.py, 统计:
#   转换率 = 已转写实体 / 识别到实体;  对象合法率 = 通过独立校验器的对象 / 文件数;
#   Wilson 95% 置信区间 (样本量 n = 识别到实体数)。
#
# 宿主 LLM 臂 (docs/110 §5) 未在本环境运行: 需要外部模型凭据。协议已就绪 ——
# 见 docs/147 §测量协议, 任何外部转写器按同一报告 schema 产出 reports.json 即可并入主表。
#
# 用法: python tools/potato_measure.py [--out-dir loment/build] [--json]
# 退出码: 0 = 全部文件对象合法 / 1 = 有非法对象 / 2 = 用法错误。

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import potato  # noqa: E402
import potato_from  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "loment" / "corpus.json"
ARMS = ("strict", "lenient")
ARM_NAMES = {"strict": "工具链-严格", "lenient": "工具链-宽松"}
LANGS = ("python", "c", "rust")


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson 95% 置信区间 (M54)。n=0 时返回 (0,0)。"""
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (c - r) / d), min(1.0, (c + r) / d)


def estimate_entities(src: str, lang: str) -> int:
    """独立估计候选实体数 (宽松模式匹配) —— 用来暴露识别器自身的盲区。

    "识别率" = 识别器看见的实体 / 估计实体; 转换率是条件于已识别实体的比率。
    估计仍是启发式 (多行签名/宏展开不可见), 但两臂共用同一估计, 可比。
    """
    import re as _re
    if lang == "python":
        import ast as _ast
        try:
            tree = _ast.parse(src)
        except SyntaxError:
            return 0
        defs = sum(1 for n in tree.body
                   if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)))
        consts = sum(1 for n in tree.body
                     if isinstance(n, _ast.Assign) and len(n.targets) == 1
                     and isinstance(n.targets[0], _ast.Name) and n.targets[0].id.isupper()
                     and isinstance(n.value, _ast.Constant)
                     and isinstance(n.value.value, int)
                     and not isinstance(n.value.value, bool))
        return defs + consts
    body = _re.sub(r"//[^\n]*|/\*.*?\*/", " ", src, flags=_re.S)
    body = _re.sub(r"^[ \t]*#.*$", "", body, flags=_re.M)
    if lang == "c":
        fns = len(_re.findall(r"^[ \t]*(?:static\s+|inline\s+|const\s+)*"
                              r"[A-Za-z_][\w \t\*]*?\s+[A-Za-z_]\w*\s*\([^;{]*\)\s*(\{|;)",
                              body, _re.M))
        structs = len(_re.findall(r"\bstruct\s+[A-Za-z_]\w*\s*\{", body))
        return fns + structs
    if lang == "rust":
        fns = len(_re.findall(r"^[ \t]*(?:pub\s+)?(?:unsafe\s+)?(?:async\s+)?(?:const\s+)?fn\s+"
                              r"[A-Za-z_]\w*", body, _re.M))
        types = len(_re.findall(r"\b(?:pub\s+)?(?:struct|enum)\s+[A-Za-z_]\w*", body))
        return fns + types
    return 0


def measure(entries: list[dict], arm: str) -> list[dict]:
    rows = []
    for ent in entries:
        p = ROOT / ent["path"]
        lang = ent["lang"]
        src = p.read_text(encoding="utf-8", errors="replace")
        doc, rep = potato_from.LANGS[lang](src, p.name, arm)
        row = rep.as_dict(not rep.validate_errors)
        row["language"] = lang
        row["path"] = ent["path"]
        row["sha256"] = hashlib.sha256(src.encode("utf-8")).hexdigest()[:16]
        row["entities_est"] = estimate_entities(src, lang)
        rows.append(row)
    return rows


def aggregate(rows: list[dict]) -> dict:
    per: dict[str, dict] = {}
    for lang in LANGS:
        rs = [r for r in rows if r["language"] == lang]
        n = sum(r["entities_seen"] for r in rs)
        k = sum(r["entities_ok"] for r in rs)
        est = sum(r["entities_est"] for r in rs)
        lo, hi = wilson(k, n)
        per[lang] = {
            "files": len(rs),
            "entities_est": est,
            "entities_seen": n,
            "recognition_rate": round(n / est, 4) if est else 0.0,
            "entities_ok": k,
            "conversion_rate": round(k / n, 4) if n else 0.0,
            "ci95": [round(lo, 4), round(hi, 4)],
            "objects_valid": sum(1 for r in rs if r["object_valid"]),
            "object_valid_rate": round(
                sum(1 for r in rs if r["object_valid"]) / len(rs), 4) if rs else 0.0,
        }
    return per


def _fmt(pct: float) -> str:
    return f"{pct * 100:.1f}%"


def render_table(per_arm: dict[str, dict]) -> str:
    lines = [
        "| 语言 | 臂 | 文件 | 估计实体 | 识别 | 识别率 | 已转写 | 转换率 | 95% CI (Wilson) | 对象合法率 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for lang in LANGS:
        for arm in ARMS:
            a = per_arm[arm][lang]
            lines.append(
                f"| {lang} | {ARM_NAMES[arm]} | {a['files']} | {a['entities_est']} | "
                f"{a['entities_seen']} | {_fmt(a['recognition_rate'])} | "
                f"{a['entities_ok']} | {_fmt(a['conversion_rate'])} | "
                f"[{_fmt(a['ci95'][0])}, {_fmt(a['ci95'][1])}] | "
                f"{a['objects_valid']}/{a['files']} ({_fmt(a['object_valid_rate'])}) |")
    lines.append("")
    lines.append("| 语言 | 臂 | 跳过原因分布 (top) |")
    lines.append("|---|---|---|")
    for lang in LANGS:
        for arm in ARMS:
            rows = [r for r in ALL_ROWS[arm] if r["language"] == lang]
            hist: dict[str, int] = {}
            for r in rows:
                for s in r["skipped"]:
                    hist[s["why"]] = hist.get(s["why"], 0) + 1
            top = sorted(hist.items(), key=lambda x: (-x[1], x[0]))[:4]
            lines.append(f"| {lang} | {ARM_NAMES[arm]} | "
                         + "; ".join(f"{w}×{c}" for w, c in top) + " |")
    return "\n".join(lines)


ALL_ROWS: dict[str, list[dict]] = {}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="potato_measure", description="波 C 转换率测量")
    ap.add_argument("--out-dir", default=str(ROOT / "loment" / "build"))
    ap.add_argument("--json", action="store_true", help="只打印 JSON")
    a = ap.parse_args(argv)

    entries = json.loads(CORPUS.read_text(encoding="utf-8"))
    per_arm, out = {}, {"corpus": str(CORPUS.relative_to(ROOT)), "arms": {}}
    for arm in ARMS:
        rows = measure(entries, arm)
        ALL_ROWS[arm] = rows
        per_arm[arm] = aggregate(rows)
        out["arms"][arm] = {"name": ARM_NAMES[arm], "per_language": per_arm[arm],
                            "files": rows}
    table = render_table(per_arm)
    out["table_md"] = table

    d = Path(a.out_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / "wave-c-table.md").write_text(table + "\n", encoding="utf-8")
    (d / "wave-c-results.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if a.json:
        print(json.dumps(out["arms"], ensure_ascii=False, indent=2))
    else:
        print(f"# 波 C 主表 ({len(entries)} 文件, 语料 sha256 见 wave-c-results.json)")
        print(table)
        print(f"\n[OK] -> {d / 'wave-c-table.md'}")
    bad = sum(1 for arm in ARMS for r in ALL_ROWS[arm] if not r["object_valid"])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
