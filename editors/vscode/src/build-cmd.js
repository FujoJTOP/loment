// build-cmd.js — 决定"**怎么编** / **怎么跑**"（纯 Node，无 vscode 依赖）
//
// 单独成文件是为了**可无头测试**（与 `server-path.js` 同一个理由）：选哪条编译路径
// 是扩展里最容易错、又最不需要 GUI 才能验的一环 —— 而它错了的表现是"点了没反应"
// 或者"编出来的是别的东西"，在编辑器里看不出来。
//
// ## 三层检测 —— 为什么是三层
//
// 仓库里**没有**"单个 .lomt 出可执行文件"的 Python CLI（`loment build DIR` 是**整目录
// 增量构建**，且只出 IR）。单文件出可执行是这两条：
//
//   ① **装了 Loment 命令**：`loment build FILE [-o NAME]` / `loment run FILE`
//      （`loment/tools/lomcli.lomt:606`）—— **产品路径，不需要 Python**
//   ② **Windows 开发树**：`scripts/lomc.ps1` —— 种子 + clang + WSL，出 `.ll` + `.elf`
//   ③ 两条都没有 -> **明确报错**，不猜。
//
// ③ 不是兜底失败，是**这一格该有的行为**：本仓的规矩是把"静默做错"换成"报错退出"
// （`docs/167`）。点"编译"什么都没发生、也没说为什么，是最难查的一种。

'use strict';

const fs = require('fs');
const path = require('path');

/** 相对路径一律用 `/` —— 传给外部命令，反斜杠在 WSL/clang 那侧会被当转义。 */
function posixRel(root, p) {
  return path.relative(root, p).replace(/\\/g, '/');
}

/** `loment build` 的 `-o` **不带扩展名**（`lomcli.lomt` 明说）：`-o hi` 给 `hi.exe`/`hi`。 */
function outName(buildDir, file) {
  const stem = path.basename(file).replace(/\.lomt$/i, '');
  return buildDir ? `${buildDir}/${stem}` : stem;
}

/**
 * 决定用哪条路。返回 `{cmd, args, cwd, how}` 或 `{error}`。
 *
 * @param {object} o
 * @param {string} o.root        工作区根（命令的 cwd 与相对路径的基准）
 * @param {string} o.file        要编的 `.lomt` 的绝对路径
 * @param {'build'|'run'} o.action
 * @param {string} [o.platform]  `process.platform`（测试可注入）
 * @param {string} [o.toolCommand] `loment.toolCommand`
 * @param {string[]} [o.toolArgs]  `loment.toolArgs`
 * @param {string} [o.buildDir]  `loment.buildDir`（相对工作区根）
 */
function invocation(o) {
  const root = o.root;
  const file = o.file;
  const action = o.action === 'run' ? 'run' : 'build';
  const platform = o.platform || process.platform;
  const tool = (o.toolCommand || '').trim();
  const rel = posixRel(root, file);
  if (!rel || rel.startsWith('..')) {
    return { error: '这个文件不在工作区里 —— 编译命令的相对路径要以工作区根为基准。'
                    + '把它的目录加进工作区，或把工作区设成它的上一层。' };
  }

  // ---- ① 装了 Loment 命令（产品路径，不需要 Python）
  if (tool) {
    const extra = Array.isArray(o.toolArgs) ? o.toolArgs.map(String) : [];
    const args = extra.concat([action, rel]);
    if (action === 'build') {
      args.push('-o', outName(o.buildDir, file));
    }
    return { cmd: tool, args, cwd: root, how: 'cli' };
  }

  // ---- ② Windows 开发树：scripts/lomc.ps1（种子 + clang + WSL）
  if (platform === 'win32') {
    const wrapper = path.join(root, 'scripts', 'lomc.ps1');
    if (fs.existsSync(wrapper)) {
      const args = ['-NoProfile', '-File', wrapper, rel];
      if (action === 'run') {
        args.push('-Run');            // 用 WSL 跑那个 Linux ELF（程序用 Linux syscall）
      }
      return { cmd: 'powershell', args, cwd: root, how: 'lomc' };
    }
  }

  // ---- ③ 两条都没有：说清楚该做什么，别静默
  return {
    error: '找不到能编译的东西。两种办法任选一种：\n'
      + '  · 装好工具链后在设置里填 `loment.toolCommand`'
      + '（装了包的话它是 `loment`；Windows 上经 WSL 跑时填 `wsl`，'
      + '`loment.toolArgs` 填 ["-e", "/home/<你>/.local/share/loment/bin/loment"]）；\n'
      + '  · 或者在 Windows 上打开**仓库根**当工作区 —— `scripts/lomc.ps1` 在的话会自动用它。',
  };
}

module.exports = { invocation, posixRel, outName };
