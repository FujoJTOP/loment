#!/usr/bin/env python3
# vscode_ext_test.py — VS Code 扩展的无头验收 (M56 编辑器宿主)
#
# 判据: 编辑器集成不必靠肉眼看 GUI 才能验证 ——
#   1) 扩展清单/语法文件结构合法, 且**每个语法正则都能编译** (否则会静默不高亮);
#   2) src/server-path.js (纯 Node) 真的能从工作区找到 tools/loment_lsp.py;
#   3) 对**真实语言服务**做一次完整 LSP 往返: initialize -> didOpen(诊断) ->
#      completion -> definition -> formatting -> shutdown;
#   4) .vsix 结构合法 (若已打包)。
#
# 用法: python tools/vscode_ext_test.py
# 退出码: 0 = 全绿 / 1 = 失败 / 2 = 环境缺失。

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vscode_ext  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "editors" / "vscode"
VSIX = ROOT / "loment" / "build" / "loment-vscode.vsix"
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _node() -> str | None:
    return shutil.which("node")


def _python() -> str:
    return shutil.which("python") or "python"


def _frame(obj: dict) -> bytes:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return b"Content-Length: %d\r\n\r\n" % len(body) + body


def _parse_frames(data: bytes) -> list[dict]:
    out = []
    while True:
        sep = data.find(b"\r\n\r\n")
        if sep < 0:
            return out
        header = data[:sep].decode("ascii", "replace")
        n = 0
        for line in header.splitlines():
            k, _, v = line.partition(":")
            if k.strip().lower() == "content-length":
                n = int(v.strip())
        body = data[sep + 4:sep + 4 + n]
        out.append(json.loads(body.decode("utf-8")))
        data = data[sep + 4 + n:]


# ---------------------------------------------------------------- 1. 清单与语法

@test
def test_vscode_ext_manifest_and_grammar():
    # `node_modules/` 是 gitignore 的 (vsix 打包才需要它): 干净检出/别的机器上就是没有。
    # 这属于**环境缺失**, 不是逻辑红 —— 所以只跳过那一项, 结构问题照常判。
    missing_npm = not (EXT / "node_modules" / "vscode-languageclient" / "package.json").is_file()
    bad = [b for b in vscode_ext.check() if "node_modules" not in b]
    assert not bad, f"扩展结构问题: {bad}"
    if missing_npm:
        print("      SKIP 打包前置: 没装 node_modules (npm install --omit=dev 于 editors/vscode)"
              " —— 干净检出/别的机器上如此, 不是逻辑问题")
    node = _node()
    if not node:
        print("      SKIP 正则编译: 无 node")
        return
    pkg = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
    # 每个 match / begin / end 都交给 node 编译一次 (Oniguruma 与 JS 正则不完全等价,
    # 但括号/转义写错这类问题两者都会报 —— 这是防止"语法文件静默失效"的一层)
    script = """
const fs = require('fs');
const files = process.argv.slice(1);
let n = 0;
for (const f of files) {
  const doc = JSON.parse(fs.readFileSync(f, 'utf8'));
  const walk = (o) => {
    if (o && typeof o === 'object') {
      for (const k of Object.keys(o)) {
        const v = o[k];
        if ((k === 'match' || k === 'begin' || k === 'end') && typeof v === 'string') {
          try { new RegExp(v); n += 1; } catch (e) { throw new Error(f + ': ' + v + ' -> ' + e.message); }
        } else { walk(v); }
      }
    }
  };
  walk(doc);
}
process.stdout.write(String(n));
"""
    grammars = [str(EXT / g["path"]) for g in pkg["contributes"]["grammars"]]
    r = subprocess.run([node, "-e", script, *grammars], capture_output=True, text=True,
                       shell=False)
    assert r.returncode == 0, f"语法正则编译失败: {r.stderr[-300:]}"
    assert int(r.stdout.strip()) >= 20, f"编译到的正则数偏少: {r.stdout!r}"


@test
def test_vscode_grammar_lints():
    """语法高亮的**两类静默失效**必须挡住 (它们不报错, 只是颜色不对):

    1. scope 名用了自造根名 —— 主题不认那段, 显示为默认前景色 (看起来"没高亮");
    2. `match` 里吃掉引号 —— 它会截胡字符串的**开引号**, 于是整份文件剩下的部分都被
       当成一个未闭合的字符串染成字符串色。我加 `excluded "` 时就踩了一次:
       在 `demo.lomt` 上从第 21 行起整片变字符串。`begin`/`end` 才是能碰引号的地方。
    """
    roots = {"comment", "string", "constant", "keyword", "storage", "entity",
             "support", "variable", "punctuation", "meta", "invalid"}
    pkg = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
    for g in pkg["contributes"]["grammars"]:
        doc = json.loads((EXT / g["path"]).read_text(encoding="utf-8"))
        assert doc["scopeName"] == g["scopeName"], (g["path"], doc["scopeName"])
        names, quotes = [], []

        def walk(o, key=""):
            if isinstance(o, dict):
                for k, v in o.items():
                    walk(v, k)
            elif isinstance(o, list):
                for x in o:
                    walk(x, key)
            elif isinstance(o, str):
                if key == "name" and o not in ("Loment", "L0 (.lom)"):
                    names.append(o)
                if key == "match" and '"' in o:
                    quotes.append(o)

        walk(doc)
        bad = [n for n in names if n.split(".")[0] not in roots]
        assert not bad, f"{g['path']}: 非标准 scope 根名 {bad[:4]}"
        assert not quotes, f"{g['path']}: match 里出现引号 (会吃掉字符串引号): {quotes[:2]}"
        print(f"      {g['path'].split('/')[-1]}: {len(set(names))} 个 scope 名合规, 无引号截胡")


# ---------------------------------------------------------------- 2. 服务路径发现

@test
def test_vscode_ext_finds_language_server():
    node = _node()
    if not node:
        print("      SKIP: 无 node")
        return
    script = (
        "const {findServer} = require(process.argv[1]);"
        "const hit = findServer(process.argv[2]);"
        "const deep = findServer(process.argv[2] + '/loment/examples');"
        "process.stdout.write(JSON.stringify({hit, deep}));"
    )
    r = subprocess.run([node, "-e", script, str(EXT / "src" / "server-path.js"), str(ROOT)],
                       capture_output=True, text=True, shell=False)
    assert r.returncode == 0, f"server-path.js 执行失败: {r.stderr[-300:]}"
    got = json.loads(r.stdout)
    assert got["hit"] and got["hit"].replace("\\", "/").endswith("tools/loment_lsp.py"), got
    assert got["deep"] and got["deep"] == got["hit"], f"从子目录也应当找到: {got}"


# ---------------------------------------------------------------- 3. LSP 往返

@test
def test_vscode_lsp_roundtrip():
    """对真实语言服务做一次完整会话 —— 这正是扩展依赖的契约。"""
    src = "module m\n\npub fn add(a: u32, b: u32) -> u32 {\n    return a+b;\n}\n\nfn use_it() -> u32 {\n    return add(1, 2);\n}\n"
    uri = "file:///tmp/roundtrip.lomt"
    frames = b"".join([
        _frame({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        _frame({"jsonrpc": "2.0", "method": "textDocument/didOpen",
                "params": {"textDocument": {"uri": uri, "text": src}}}),
        _frame({"jsonrpc": "2.0", "id": 2, "method": "textDocument/completion",
                "params": {"textDocument": {"uri": uri}}}),
        _frame({"jsonrpc": "2.0", "id": 3, "method": "textDocument/definition",
                "params": {"textDocument": {"uri": uri},
                           "position": {"line": 7, "character": 12}}}),
        _frame({"jsonrpc": "2.0", "id": 4, "method": "textDocument/formatting",
                "params": {"textDocument": {"uri": uri}}}),
        _frame({"jsonrpc": "2.0", "id": 5, "method": "shutdown", "params": {}}),
    ])
    r = subprocess.run([_python(), str(ROOT / "tools" / "loment_lsp.py")], input=frames,
                       capture_output=True, shell=False, timeout=60)
    msgs = _parse_frames(r.stdout)
    by_id = {m.get("id"): m for m in msgs if m.get("id") is not None}
    assert 1 in by_id, f"没有 initialize 应答: {r.stderr[-300:]!r}"
    caps = by_id[1]["result"]["capabilities"]
    for cap in ("textDocumentSync", "definitionProvider", "completionProvider",
                "documentFormattingProvider"):
        assert cap in caps, f"能力缺失: {cap} ({caps})"
    diags = [m for m in msgs if m.get("method") == "textDocument/publishDiagnostics"]
    assert diags, "didOpen 之后应当推送诊断"
    assert diags[-1]["params"]["uri"] == uri and diags[-1]["params"]["diagnostics"] == [], diags[-1]
    labels = {it["label"] for it in by_id[2]["result"]["items"]}
    assert "fn" in labels and "add" in labels and "u32" in labels, sorted(labels)[:10]
    loc = by_id[3]["result"]
    assert loc and loc["range"]["start"]["line"] == 2, f"定义应跳到第 3 行: {loc}"
    edits = by_id[4]["result"]
    assert edits and "a + b" in edits[0]["newText"], f"格式化应当整理运算符间距: {edits}"
    assert 5 in by_id, "shutdown 应当有应答"


@test
def test_vscode_lsp_reports_type_errors():
    """错误路径: 未声明的变量要变成诊断, 而不是静默。"""
    src = "module m\n\nfn f() -> u32 {\n    return nope;\n}\n"
    uri = "file:///tmp/bad.lomt"
    frames = b"".join([
        _frame({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        _frame({"jsonrpc": "2.0", "method": "textDocument/didOpen",
                "params": {"textDocument": {"uri": uri, "text": src}}}),
        _frame({"jsonrpc": "2.0", "id": 2, "method": "shutdown", "params": {}}),
    ])
    r = subprocess.run([_python(), str(ROOT / "tools" / "loment_lsp.py")], input=frames,
                       capture_output=True, shell=False, timeout=60)
    msgs = _parse_frames(r.stdout)
    diags = [m for m in msgs if m.get("method") == "textDocument/publishDiagnostics"]
    assert diags, "应当推送诊断"
    got = diags[-1]["params"]["diagnostics"]
    assert got and got[0]["severity"] == 1, f"未解析变量应当报错: {got}"
    assert got[0]["range"]["start"]["line"] == 3, f"应当定位到第 4 行: {got[0]}"


# ---------------------------------------------------------------- 4. VSIX

@test
def test_vscode_vsix_structure():
    if not VSIX.is_file():
        print("      SKIP: 尚未打包 (python tools/vscode_ext.py --emit …)")
        return
    bad = vscode_ext.verify_vsix(VSIX)
    assert not bad, f"VSIX 结构问题: {bad}"


# ---------------------------------------------------------------- 5. 编译 / 运行

@test
def test_vscode_build_and_run_commands():
    """**「编译 / 运行」两条命令真的接得上** —— 不是只写在清单里。

    这一条钉两件事，它们各自都是**真的会坏**的：

    1. **清单声明的命令都有实现**。清单里有、`extension.js` 里没 `registerCommand` ——
       用户点下去 VS Code 只说 "command not found"，而清单看着完全正常。
    2. **`build-cmd.js` 的分支判定**（纯模块，无头跑）。选错路的失败模式是
       "点了没反应"或"编出来的是别的东西"，在编辑器里看不出来 —— 所以值得一条判据。

    分支表见 `editors/vscode/src/build-cmd.js` 头注：装了 Loment 命令走它；
    Windows 开发树走 `scripts/lomc.ps1`；两条都没有就**报错**（不静默返回空命令）。
    """
    pkg = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
    declared = {c["command"] for c in pkg["contributes"]["commands"]}
    js = (EXT / "src" / "extension.js").read_text(encoding="utf-8")
    for c in ("loment.build", "loment.run"):
        assert c in declared, f"清单里没有声明 {c}（用户在命令面板里找不到它）"
        assert f"registerCommand('{c}'" in js, f"清单声明了 {c}，但扩展里没有实现它"
    for k in ("loment.toolCommand", "loment.toolArgs"):
        assert k in pkg["contributes"]["configuration"]["properties"], f"缺设置 {k}"

    # **`Ctrl+Shift+B` 那条路**：它只认**注册过的任务**，不认命令。少了下面这两样，
    # 用户按 `Ctrl+Shift+B` 什么都不会发生 —— 而命令面板里那两条明明是好的。
    tds = pkg["contributes"].get("taskDefinitions") or []
    assert any(t.get("type") == "loment" for t in tds), \
        f"清单里没有 loment 的 taskDefinitions（Ctrl+Shift+B 找不到它）: {tds}"
    assert "registerTaskProvider('loment'" in js, \
        "清单声明了 loment 任务，但扩展里没有注册任务提供者"

    # **编辑器右上角那个 ▶**。第三方 Code Runner 也给 `.lomt` 挂了一个 ——
    # 它的 `when` **不带语言过滤**（`config.code-runner.showRunIconInEditorTitleMenu`），
    # 而它对 Loment 没有任何执行器，所以点下去**真的什么都不做**（2026-09-18 实测：
    # 用户说"点了运行没反应"）。所以必须有自己的那一个，且 `when` 要钉在 loment 上。
    run_menu = (pkg["contributes"].get("menus") or {}).get("editor/title/run") or []
    assert any(m.get("command") == "loment.run" and "loment" in str(m.get("when", ""))
               for m in run_menu), \
        f"`editor/title/run` 里没有钉在 loment 上的 loment.run —— 右上角的 ▶ 会是别人的" \
        f"（点了没反应的正是它）: {run_menu}"

    node = _node()
    if not node:
        print("      SKIP 分支判定: 无 node")
        return
    script = """
const {invocation} = require(process.argv[1]);
const R = process.argv[2];
const F = R + '/loment/examples/tour.lomt';
process.stdout.write(JSON.stringify({
  cli_build: invocation({root:R, file:F, action:'build', platform:'win32',
                         toolCommand:'loment', buildDir:'loment/build'}),
  cli_run:   invocation({root:R, file:F, action:'run', platform:'win32',
                         toolCommand:'wsl', toolArgs:['-e','/x/loment']}),
  dev_build: invocation({root:R, file:F, action:'build', platform:'win32'}),
  dev_run:   invocation({root:R, file:F, action:'run', platform:'win32'}),
  none:      invocation({root:R, file:F, action:'build', platform:'linux'}),
  outside:   invocation({root:R, file:'D:/elsewhere/x.lomt', action:'build', platform:'linux'}),
  // **没打开文件夹**（VS Code 空窗口）：调用方只能给"文件所在目录"，或者干脆给不出
  empty_window: invocation({root:R + '/loment/examples', file:F, action:'run',
                            platform:'win32', buildDir:'loment/build'}),
  no_root:      invocation({file:F, action:'run', platform:'win32',
                            buildDir:'loment/build'}),
  // **库模块**（没有 `fn _start`）不该被"运行" —— 编得出 ELF，但一跑就 exit 11
  lib_run:   invocation({root:R, file:R + '/loment/examples/mathutil.lomt',
                         action:'run', platform:'win32', buildDir:'loment/build'}),
  lib_build: invocation({root:R, file:R + '/loment/examples/mathutil.lomt',
                         action:'build', platform:'win32', buildDir:'loment/build'})
}));
"""
    r = subprocess.run([node, "-e", script, str(EXT / "src" / "build-cmd.js"), str(ROOT)],
                       capture_output=True, text=True, shell=False)
    assert r.returncode == 0, f"build-cmd.js 执行失败: {r.stderr[-300:]}"
    got = json.loads(r.stdout)

    # ① 装了 Loment 命令 -> 走它。`build` 带 `-o`（**不带扩展名**，`lomcli.lomt:694`），
    #    `run` 不带 —— 这条差别错了会编出个名字不对的文件，而命令看着是成功的。
    b = got["cli_build"]
    assert b.get("how") == "cli" and b["cmd"] == "loment", b
    assert b["args"][0] == "build", b
    assert "-o" in b["args"] and b["args"][-1].endswith("/tour"), b
    assert b["args"][1] == "loment/examples/tour.lomt", f"要传相对路径（cwd 是工作区根）: {b}"

    # ② 经 WSL 跑同一个命令：toolArgs 在前，子命令在后
    run = got["cli_run"]
    assert run["args"] == ["-e", "/x/loment", "run", "loment/examples/tour.lomt"], run
    assert "-o" not in run["args"], f"`run` 不该带 -o: {run}"

    # ③ Windows 开发树 -> scripts/lomc.ps1；**只有 run 带 `-Run`**
    if (ROOT / "scripts" / "lomc.ps1").is_file():
        d = got["dev_build"]
        assert d.get("how") == "lomc", d
        assert d["args"][:2] == ["-NoProfile", "-File"], d
        assert "-Run" not in d["args"], f"`build` 不该带 -Run: {d}"
        assert got["dev_run"]["args"][-1] == "-Run", got["dev_run"]
    else:
        print("      SKIP Windows 开发树那一支: 本仓没有 scripts/lomc.ps1")

    # ④ 两条都没有 -> **报错**，不是静默给一个空命令（那等于"点了没反应"）
    assert "error" in got["none"], f"没有可编译的东西时必须报错: {got['none']}"
    assert "toolCommand" in got["none"]["error"], f"报错要说清该改哪个设置: {got['none']}"

    # ⑤ 文件在工作区外 -> 报错（相对路径要传给外部命令，`..` 会指到别处）
    assert "error" in got["outside"], got["outside"]

    # ⑥ **没打开文件夹时（VS Code 空窗口）也要能用**：从文件所在目录往上找
    #    `scripts/lomc.ps1`。2026-09-18 实测：双击打开 `loment/examples/mathutil.lomt`
    #    点运行 -> 「找不到能编译的东西」—— 工具链明明在，只是没往上看。
    #    `server-path.js` / `debug-cmd.js` 早就是向上找的，`build-cmd.js` 原先不是。
    for key in ("empty_window", "no_root"):
        e = got[key]
        assert e.get("how") == "lomc", f"{key}: 空窗口下没找到 scripts/lomc.ps1: {e}"
        # cwd 必须是**仓库根** —— lomc.ps1 的 `-OutDir` 默认 `loment/build` 是相对 cwd 的
        assert e["cwd"].replace("\\", "/").rstrip("/") == str(ROOT).replace("\\", "/").rstrip("/"), \
            f"{key}: cwd 应当是仓库根: {e}"
        assert e["args"][-1] == "-Run", e
        # 相对路径的基准同样是仓库根（不是文件所在目录）
        assert e["args"][3] == "loment/examples/tour.lomt", f"{key}: 相对路径基准不对: {e}"
    print("      空窗口（没打开文件夹）：从文件往上找到 lomc.ps1，cwd 回到仓库根")

    # ⑦ **库模块不能被"运行"**。`loment/examples/mathutil.lomt` 只有 `pub fn`，
    #    没有 `_start` —— 编得出 ELF 但一跑就段错误（链接器那句
    #    `cannot find entry symbol _start` 在任务输出里，而 `lomc.ps1` 自己退出码是 0）。
    #    实测症状就是用户那句"跑不动"，而且不知道为什么。
    lib = got["lib_run"]
    assert "error" in lib, f"库模块不该被放行去运行: {lib}"
    assert "_start" in lib["error"], f"报错要说清缺的是什么: {lib['error'][:80]}"
    # **编译**不受影响（出 IR / 目标文件是正当用途）
    assert got["lib_build"].get("how") == "lomc", \
        f"库模块只是不能「运行」，编译照旧: {got['lib_build']}"
    print("      库模块：`运行` 拦住并说明缺 `_start`；`编译` 照旧放行")
    print(f"      编译/运行：清单↔实现对得上；分支判定 {len(got)} 例"
          f"（CLI / WSL / 开发树 / 报错 / 空窗口）")


# ---------------------------------------------------------------- 6. 扩展真的激活

#: 一个**假的 `vscode` 模块** —— 足够让 `extension.js` 跑完 `activate`。
#: `Module._load` 把 `require('vscode')` 拦下来换成它（另外把语言服务那个包也换掉，
#: 免得为了跑一次激活去装 `node_modules`）。
#:
#: **为什么要跑真的 `activate`**：清单对、命令都注册了、纯模块单测全绿，扩展照样可能
#: 一行都执行不到。实测过一次 —— `activate()` 里引用了没定义的 `root`（`if (root)`），
#: JavaScript 到那一行抛 `ReferenceError`，于是它**后面**的任务提供者与语言服务
#: 全都没注册；而用户在编辑器里看到的只是「按了没反应 / 没有用于调试 Loment 的扩展」。
#: 那正是本仓最讨厌的静默（`docs/167`）。
_ACTIVATE_HARNESS = r"""
const Module = require('module');
const EXT = process.argv[2];
const ROOT = process.argv[3];

const reg = [];
const dis = () => ({ dispose() {} });
const file = ROOT + '/loment/examples/user_hello.lomt';
const SETTINGS = JSON.parse(process.argv[4] || '{}');

function Task(def, scope, name, source, exec, matcher) {
  this.definition = def; this.name = name; this.exec = exec; this.matcher = matcher;
}
const vscode = {
  workspace: {
    getConfiguration: () => ({ get: (k) => SETTINGS[k] }),
    workspaceFolders: [{ uri: { fsPath: ROOT } }],
    openTextDocument: async () => ({ uri: {} }),
  },
  window: {
    activeTextEditor: { document: { uri: { fsPath: file }, languageId: 'loment' } },
    // **假得照实**：只有 `{log: true}` 建的才是 LogOutputChannel —— 普通通道**没有**
    // error/warn/info/debug/trace 与 onDidChangeLogLevel。vscode-languageclient v10 要的
    // 正是后者，所以这条假通道能把"建了普通通道"当场变成失败（真实现里它是一句
    // TypeError，而且是**语言服务起来之后**才炸）。
    createOutputChannel: (name, opts) => {
      const isLog = !!(opts && opts.log);
      reg.push('channel:' + name + (isLog ? ':log' : ':plain'));
      const c = { appendLine() {}, append() {}, replace() {}, clear() {},
                  show() {}, hide() {}, dispose() {} };
      if (isLog) {
        c.error = () => {}; c.warn = () => {}; c.info = () => {};
        c.debug = () => {}; c.trace = () => {};
        c.logLevel = 3; c.onDidChangeLogLevel = () => dis();
      }
      vscode.__channel = c;
      return c;
    },
    showWarningMessage: (m) => reg.push('warn:' + m),
    showErrorMessage: (m) => reg.push('err:' + m),
    showTextDocument: async () => ({}),
  },
  commands: {
    registerCommand: (id) => { reg.push('cmd:' + id); return dis(); },
    executeCommand: () => {},
  },
  tasks: {
    registerTaskProvider: (t, p) => {
      reg.push('taskProvider:' + t); vscode.__provider = p; return dis();
    },
    executeTask: async () => {},
  },
  debug: {
    registerDebugAdapterDescriptorFactory: (t, f) => {
      reg.push('debugFactory:' + t); vscode.__factory = f; return dis();
    },
  },
  ProcessExecution: function (c, a, o) { this.cmd = c; this.args = a; this.opts = o; },
  Task,
  TaskScope: { Workspace: 1 },
  TaskRevealKind: { Always: 1 },
  TaskPanelKind: { Shared: 1 },
  TaskGroup: { Build: 2 },
  Uri: { file: (p) => ({ fsPath: p }) },
  DebugAdapterExecutable: function (c, a) { this.cmd = c; this.args = a; },
};

const load = Module._load;
Module._load = function (req) {
  if (req === 'vscode') { return vscode; }
  if (req === 'vscode-languageclient/node') {
    return { LanguageClient: class { async start() { reg.push('client:start'); }
                                     async stop() {} } };
  }
  return load.apply(this, arguments);
};

(async () => {
  const out = { ok: false, reg: [], subs: 0, tasks: null, buildGroup: null, dap: null };
  try {
    const ext = require(EXT);
    const ctx = { subscriptions: [] };
    await ext.activate(ctx);
    out.ok = true;
    out.subs = ctx.subscriptions.length;
    if (vscode.__provider) {
      const ts = await vscode.__provider.provideTasks();
      out.tasks = ts.length;
      out.buildGroup = ts.some((t) => t.group === vscode.TaskGroup.Build);
    }
    if (vscode.__factory) {
      const d = vscode.__factory.createDebugAdapterDescriptor();
      out.dap = d ? { cmd: d.cmd, args: d.args } : null;
    }
  } catch (e) {
    out.error = String((e && e.stack) || e);
  }
  out.reg = reg;
  process.stdout.write(JSON.stringify(out));
})();
"""


@test
def test_vscode_extension_activates():
    """**扩展真的激活得起来，而且该注册的都注册了。**

    `activate()` 抛异常时 VS Code 只会把扩展标红，功能**静默全丢** ——
    而清单、纯模块单测、语法文件全都还是绿的。所以这一条要真的跑一遍它。
    """
    node = _node()
    if not node:
        print("      SKIP: 无 node")
        return
    with tempfile.TemporaryDirectory() as td:
        h = Path(td) / "activate-harness.js"
        h.write_text(_ACTIVATE_HARNESS, encoding="utf-8")
        # **语言服务开着**（`enableLsp: true`）：关掉它 `startClient` 根本不跑，而这一格里
        # 藏着真 bug —— v10 的客户端要求输出通道是 `LogOutputChannel`。
        settings = json.dumps({"enableLsp": True, "toolCommand": "loment",
                               "buildDir": "loment/build"})
        r = subprocess.run([node, str(h), str(EXT / "src" / "extension.js"), str(ROOT), settings],
                           capture_output=True, text=True, shell=False)

    def fail(msg):
        return AssertionError(f"{msg}\n  node stderr: {r.stderr.strip()[-400:]}")

    assert r.returncode == 0, fail(f"激活脚手架本身就挂了 (rc={r.returncode})")
    got = json.loads(r.stdout)
    assert got.get("ok"), fail(f"`activate()` 抛了: {got.get('error', '')[:600]}")

    pkg = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
    reg = set(got["reg"])

    # ① 清单里声明的**每一条命令**都要真的注册上 —— 少一条，用户点下去只说
    #    "command not found"，而清单看着完全正常。
    missing = sorted(c["command"] for c in pkg["contributes"]["commands"]
                     if f"cmd:{c['command']}" not in reg)
    assert not missing, fail(f"这些命令在清单里，但 activate 时没注册: {missing}")

    # ② 任务提供者 / 调试适配器工厂：`Ctrl+Shift+B` 与 F5 各自只认这两样
    assert "taskProvider:loment" in reg, fail(f"没有注册 loment 任务提供者: {sorted(reg)}")
    assert "debugFactory:loment" in reg, fail(f"没有注册 loment 调试适配器工厂: {sorted(reg)}")

    # ④ **语言服务的输出通道必须是 LogOutputChannel**（`createOutputChannel(name, {log:true})`）。
    #    `vscode-languageclient` v10 会给它挂 `onDidChangeLogLevel(...)` 并调 `.error(...)`，
    #    普通通道这两样都没有 —— 实测症状是语言服务一起就弹两条
    #    `TypeError: this.outputChannel.error is not a function`。判据用**假得照实**的通道
    #    （只有 `{log:true}` 才带那几个方法），所以这一格坏了会当场红。
    assert "channel:Loment:log" in reg, fail(
        "输出通道不是 LogOutputChannel —— vscode-languageclient v10 要求 "
        "`createOutputChannel('Loment', {log: true})`；注册记录里是 "
        f"{[x for x in reg if x.startswith('channel:')] or '（压根没建）'}")
    assert "client:start" in reg, fail(f"语言服务没起来: {sorted(reg)}")

    # ③ 任务提供者真给出两条任务，且「编译」是**默认生成任务**（`Ctrl+Shift+B` 靠它）
    assert got["tasks"] == 2, fail(f"应当给「编译 / 编译并运行」两条任务，给了 {got['tasks']}")
    assert got["buildGroup"] is True, fail("「编译」没有挂 TaskGroup.Build —— Ctrl+Shift+B 不会挑它")

    # ④ 调试工厂给得出**真命令**（不是 undefined）。清单里 `debuggers[].type` 必须与
    #    注册时用的那个名字一致 —— 不一致时 F5 还是会弹"没有用于调试的扩展"。
    types = [d.get("type") for d in pkg["contributes"].get("debuggers") or []]
    assert "loment" in types, fail(f"清单里的调试器类型对不上: {types}")
    dap = got["dap"]
    assert dap, fail("调试适配器工厂返回了 null/undefined（F5 会是一个没反应的会话）")
    assert dap["args"] and any("loment_dap.py" in a for a in dap["args"]), fail(f"适配器命令不对: {dap}")
    print(f"      activate() 跑通：{len(reg)} 项注册、{got['tasks']} 条任务（含默认生成任务）、"
          f"调试适配器 `{dap['cmd']} …{Path(dap['args'][-1]).name}`")


@test
def test_vscode_registration_points_at_the_real_dir():
    """**登记必须指向真目录** —— 它指向别处时高亮/命令/F5 整个消失，而磁盘上一切"看着在"。

    2026-09-18 实测踩到的坑：侧载时把旧副本挪成 `<ident>-<ver>.old` **留在了
    `extensions/` 里**，VS Code 扫到那个文件夹（它也有 `package.json`），把扩展登记到了
    这个**马上要被删掉**的名字上。当时 `--doctor` 只查"有没有登记"，所以照常报 OK ——
    这一条把那个盲点钉住。
    """
    ident = "fujojtop.loment"
    dest = vscode_ext.ext_dir() / f"{ident}-0.1.0"
    stale = {"identifier": {"id": ident}, "version": "0.1.0",
             "location": {"$mid": 1, "path": f"/c:/x/{ident}-0.1.0.old", "scheme": "file"},
             "relativeLocation": f"{ident}-0.1.0.old"}
    other = {"identifier": {"id": "someone.else"},
             "relativeLocation": "someone.else-1.0.0"}
    entries, n = vscode_ext.fix_registration([dict(stale), dict(other)], ident, dest)
    assert n == 1, f"应当只改 1 条，改了 {n}"
    got = entries[0]
    assert got["relativeLocation"] == dest.name, got
    # URI 要**照抄 VS Code 自己写的那种**：只有 $mid/path/scheme，`/c:/...` 形状。
    # 自己发明 `fsPath`/`external` 就不是它认的那一份了。
    assert got["location"] == vscode_ext.vs_uri(dest), got["location"]
    assert set(got["location"]) == {"$mid", "path", "scheme"}, got["location"]
    assert re.match(r"^/[a-z]:/", got["location"]["path"]), got["location"]
    assert entries[1] == other, f"别人的条目不许动: {entries[1]}"

    # 盘符小写、正斜杠、顶一个 `/` —— 与 VS Code 写出来的一致
    assert vscode_ext.vs_uri(Path("D:/Dev/Loment-DEV/x")) == {
        "$mid": 1, "path": "/d:/Dev/Loment-DEV/x", "scheme": "file"}, \
        vscode_ext.vs_uri(Path("D:/Dev/Loment-DEV/x"))
    print(f"      登记修复：{ident}-0.1.0.old -> {dest.name}；别人的条目未动")


def main(argv: list[str] | None = None) -> int:
    passed = 0
    for name, fn in TESTS:
        try:
            fn()
        except AssertionError as e:
            print(f"  FAIL  {name}: {e}")
        else:
            print(f"  PASS  {name}")
            passed += 1
    print(f"\nvscode_ext_test: {passed}/{len(TESTS)} 通过")
    return 0 if passed == len(TESTS) else 1


if __name__ == "__main__":
    sys.exit(main())
