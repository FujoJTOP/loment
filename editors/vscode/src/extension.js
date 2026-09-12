// extension.js — Loment VS Code 扩展 (docs/155 §编辑器集成)
//
// 分工:
//   - 语言服务负责补全 / 跳转 / 诊断 / 格式化 —— 由官方 vscode-languageclient 驱动,
//     因此本文件里没有 child_process, 也不拼任何 shell 命令。
//     两种起法: 默认 `python tools/loment_lsp.py`; 也可以指向 **Loment 版**服务
//     (loment.serverCommand/serverArgs, 例如 Windows 上 `wsl -e <装好的 lsp>`) —— 那样
//     **不需要装 Python**, 见 scripts/install-lsp.ps1 与 docs/157 §3.5。
//   - 构建 / 检查 / 运行 走 VS Code 的任务 (ProcessExecution 传 argv 数组, 不经 shell);
//     "检查" 优先用语言服务的 `--check`(无 Python), "生成 IR" 优先用 scripts/lomc.ps1。

'use strict';

const vscode = require('vscode');
const path = require('path');
const fs = require('fs');
const { LanguageClient } = require('vscode-languageclient/node');
const { findServer } = require('./server-path');

const SERVER_COMMAND = 'python';
const clientName = 'loment';

/** @type {LanguageClient | undefined} */
let client;
/** @type {vscode.OutputChannel} */
let output;

function cfg() {
  return vscode.workspace.getConfiguration('loment');
}

/** 工作区根 (第一个文件夹); 没有就退回当前文件的目录。 */
function workspaceRoot(fallbackDir) {
  const folders = vscode.workspace.workspaceFolders;
  if (folders && folders.length) {
    return folders[0].uri.fsPath;
  }
  return fallbackDir || undefined;
}

function resolveServerPath() {
  const configured = (cfg().get('serverPath') || '').trim();
  if (configured) {
    return configured;
  }
  const folders = vscode.workspace.workspaceFolders || [];
  for (const f of folders) {
    const hit = findServer(f.uri.fsPath);
    if (hit) {
      return hit;
    }
  }
  const active = vscode.window.activeTextEditor;
  if (active) {
    return findServer(path.dirname(active.document.uri.fsPath));
  }
  return undefined;
}

/** 用 VS Code 任务跑一条 Loment 工具链命令 (argv 数组, 无 shell)。 */
async function runToolTask(name, args, cwd) {
  const exec = new vscode.ProcessExecution(SERVER_COMMAND, args, cwd ? { cwd } : undefined);
  const task = new vscode.Task({ type: 'loment', name }, vscode.TaskScope.Workspace, name,
                               'loment', exec, []);
  task.presentationOptions = { reveal: vscode.TaskRevealKind.Always, panel: vscode.TaskPanelKind.Shared };
  await vscode.tasks.executeTask(task);
}

/**
 * 语言服务**可执行文件**的调用方式 (argv):
 *   * 配了 loment.serverCommand/serverArgs -> 用它 (例如 wsl -e ~/.local/share/loment/lsp);
 *   * 否则 -> python tools/loment_lsp.py。
 * 返回 undefined 表示两种都不可用。`--check FILE` 也可以这样跑 (无需编辑器)。
 */
function serverInvocation(extraArgs) {
  const cmd = (cfg().get('serverCommand') || '').trim();
  if (cmd) {
    const argsCfg = cfg().get('serverArgs') || [];
    return { cmd, args: (Array.isArray(argsCfg) ? argsCfg.map(String) : []).concat(extraArgs) };
  }
  const serverPath = resolveServerPath();
  if (!serverPath) {
    return undefined;
  }
  return { cmd: SERVER_COMMAND, args: [serverPath].concat(extraArgs) };
}

/** 跑一条任意 argv 的命令 (无 shell), 用于"检查/出 IR"这类自带问题匹配器的命令。 */
async function runExec(name, cmd, args, cwd, matcher) {
  const exec = new vscode.ProcessExecution(cmd, args, cwd ? { cwd } : undefined);
  const task = new vscode.Task({ type: 'loment', name }, vscode.TaskScope.Workspace, name,
                               'loment', exec, matcher ? [matcher] : []);
  task.presentationOptions = { reveal: vscode.TaskRevealKind.Always, panel: vscode.TaskPanelKind.Shared };
  await vscode.tasks.executeTask(task);
}

function currentFile() {
  const ed = vscode.window.activeTextEditor;
  if (!ed) {
    vscode.window.showWarningMessage('Loment: 请先打开一个 .lomt 文件');
    return undefined;
  }
  return ed.document.uri.fsPath;
}

function toolPath(root, ...rest) {
  return path.join(root, 'tools', ...rest);
}

function buildOut(root, fsPath) {
  const stem = path.basename(fsPath, '.lomt');
  const dir = path.join(root, cfg().get('buildDir') || 'loment/build');
  return { dir, ll: path.join(dir, `${stem}.ll`), potato: path.join(dir, `${stem}.potato.json`) };
}

async function startClient(context) {
  // 语言服务有两种起法:
  //   1) Python 版 (默认): command=python, args=[tools/loment_lsp.py] —— 需要装 Python;
  //   2) Loment 版 (推荐, **不需要 Python**): 用 loment.serverCommand/serverArgs 指定,
  //      例如在 Windows 上经 WSL 跑 scripts/install-lsp.ps1 装好的 ELF:
  //        "loment.serverCommand": "wsl",
  //        "loment.serverArgs": ["-e", "/home/<you>/.local/share/loment/lsp"]
  const cmd = (cfg().get('serverCommand') || '').trim();
  const argsCfg = cfg().get('serverArgs') || [];
  let command = SERVER_COMMAND;
  let args = [];
  let label = '';
  if (cmd) {
    command = cmd;
    args = Array.isArray(argsCfg) ? argsCfg.map(String) : [];
    label = `${command} ${args.join(' ')}`;
  } else {
    const serverPath = resolveServerPath();
    if (!serverPath) {
      vscode.window.showWarningMessage(
        'Loment: 找不到 tools/loment_lsp.py（可在设置 loment.serverPath 里指定绝对路径，'
        + '或改用 loment.serverCommand/serverArgs 指向 Loment 版语言服务）');
      return undefined;
    }
    args = [serverPath];
    label = serverPath;
  }
  output = vscode.window.createOutputChannel('Loment');
  const c = new LanguageClient(
    clientName,
    'Loment',
    // v10 的 Executable 是**接口**: 用对象字面量; 默认 stdio 传输
    { run: { command, args }, debug: { command, args } },
    { documentSelector: [{ scheme: 'file', language: 'loment' }], outputChannel: output });
  context.subscriptions.push(c);
  await c.start();
  output.appendLine(`语言服务已启动: ${label}`);
  return c;
}

function activate(context) {
  const root = workspaceRoot();

  context.subscriptions.push(
    vscode.commands.registerCommand('loment.restartServer', async () => {
      if (client) {
        await client.stop();
        client = undefined;
      }
      client = await startClient(context);
    }),

    vscode.commands.registerCommand('loment.showIr', async () => {
      const file = currentFile();
      if (!file) {
        return;
      }
      const r = workspaceRoot(path.dirname(file));
      const rel = path.relative(r, file).replace(/\\/g, '/');
      const out = buildOut(r, file);
      // 优先走 Loment 侧的一键构建 (种子 + clang + WSL, **不需要 Python**)
      const wrapper = path.join(r, 'scripts', 'lomc.ps1');
      if (process.platform === 'win32' && fs.existsSync(wrapper)) {
        await runExec('Loment: 生成 LLVM IR', 'powershell',
                      ['-NoProfile', '-File', wrapper, rel], r);
        try {
          const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(out.ll));
          await vscode.window.showTextDocument(doc, { preview: true });
        } catch (e) { /* 构建失败时输出已在任务面板里 */ }
        return;
      }
      await runToolTask('Loment: 生成 LLVM IR',
                        [toolPath(r, 'lomentc.py'), file, '--emit-llvm', out.ll,
                         '--emit-potato', out.potato], r);
      const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(out.ll));
      await vscode.window.showTextDocument(doc, { preview: true });
    }),

    vscode.commands.registerCommand('loment.checkFile', async () => {
      const file = currentFile();
      if (!file) {
        return;
      }
      const r = workspaceRoot(path.dirname(file));
      const rel = path.relative(r, file).replace(/\\/g, '/');
      // 语言服务的 `--check` 与编辑器里的诊断同源 (自举 checker), 且**不需要 Python**
      const inv = serverInvocation(['--check', rel]);
      if (inv) {
        await runExec('Loment: 静态检查', inv.cmd, inv.args, r, '$loment');
        return;
      }
      await runToolTask('Loment: 静态检查', [toolPath(r, 'loment.py'), 'diag', file], r);
    }),

    vscode.commands.registerCommand('loment.runTests', async () => {
      const file = currentFile();
      if (!file) {
        return;
      }
      const r = workspaceRoot(path.dirname(file));
      await runToolTask('Loment: 运行 test_*', [toolPath(r, 'loment.py'), 'test', file], r);
    }),

    vscode.commands.registerCommand('loment.runInFujoOS', async () => {
      const file = currentFile();
      if (!file) {
        return;
      }
      const r = workspaceRoot(path.dirname(file));
      await runToolTask('Loment: 在 FujoOS 里运行', [toolPath(r, 'loment_boot.py'), file], r);
    }),

    vscode.commands.registerCommand('loment.formatDocument',
                                    () => vscode.commands.executeCommand('editor.action.formatDocument')));

  if (root) {
    output = vscode.window.createOutputChannel('Loment');
    output.appendLine(`Loment 扩展已激活 (工作区根: ${root})`);
  }

  if (cfg().get('enableLsp') !== false) {
    startClient(context).then((c) => {
      client = c;
    }, (e) => {
      vscode.window.showErrorMessage(`Loment 语言服务启动失败: ${e}`);
    });
  }
}

async function deactivate() {
  if (client) {
    await client.stop();
    client = undefined;
  }
}

module.exports = { activate, deactivate };
