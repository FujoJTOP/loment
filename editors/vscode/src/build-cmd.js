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
 * 从 startDir 逐级向上找 `<dir>/scripts/lomc.ps1`，最多 8 层。找不到返回 null。
 *
 * **为什么必须向上找**：VS Code 里"直接打开一个文件"（不打开文件夹）是极常见的用法 ——
 * 那时窗口是**空的**，扩展只能拿**文件所在目录**当基准，而 `scripts/lomc.ps1` 在仓库根。
 * 2026-09-18 实测：用户双击打开 `loment/examples/mathutil.lomt`，点运行得到
 * 「找不到能编译的东西」—— 工具链明明在，只是没从那个目录往上看。
 *
 * `server-path.js` 与 `debug-cmd.js` 早就是向上找的，只有这里不是 —— 补齐这一处。
 */
function findWrapper(startDir, maxUp) {
  const limit = typeof maxUp === 'number' ? maxUp : 8;
  let cur = startDir ? path.resolve(startDir) : null;
  for (let i = 0; i < limit && cur; i += 1) {
    const cand = path.join(cur, 'scripts', 'lomc.ps1');
    if (fs.existsSync(cand)) {
      return cand;
    }
    const up = path.dirname(cur);
    if (up === cur) {
      break;
    }
    cur = up;
  }
  return null;
}

/**
 * 这个文件里有没有 `fn _start`（ELF 的入口）。
 *
 * **必须自己有**：2026-09-18 全仓清点过，**没有一例**是"入口来自 `use` 的模块"
 * （`loment` 下所有 `.lomt` 里，凡定义 `_start` 的文件都自己编成程序）。所以这一条能当判据用，
 * 不用去解 `use` 树。
 *
 * 读不了就不拦（返回 true）—— 宁可让它去编，也不假装它是个库。
 */
function hasEntry(file) {
  try {
    return /\bfn\s+_start\b/.test(fs.readFileSync(file, 'utf8'));
  } catch (e) {
    return true;
  }
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
  const file = o.file;
  const action = o.action === 'run' ? 'run' : 'build';
  const platform = o.platform || process.platform;
  const tool = (o.toolCommand || '').trim();
  // 没打开文件夹时（空窗口）调用方给的是**文件所在目录** —— 那也是个合法的基准。
  const root = o.root || path.dirname(file);

  // ---- ⓪ 「运行」一个没有 `_start` 的文件，是**没有意义**的（也编不出可执行）
  //
  // 实测症状：链接器给一句 `cannot find entry symbol _start; not setting start address`，
  // 然后那个 ELF 一跑就 `exit code 11`（段错误）—— **用户看到的是"跑不动"，而且不知道
  // 为什么**（`lomc.ps1` 自己退出码还是 0）。这里把它变成一句能读懂的话。
  //
  // 例子：`loment/examples/mathutil.lomt` 是个库模块（只有 `pub fn`/`pub const`），
  // 要运行的是**用它的那个入口文件**。**编译**不受影响（出 IR/目标文件是正当的）。
  if (action === 'run' && !hasEntry(file)) {
    return { error: `这个文件不是可运行的程序 —— 它里面没有 \`fn _start\`（ELF 的入口）。` +
                    `\n多半它是个**库模块**：要运行的是**用它的那个入口文件**` +
                    `（自己有 \`_start\` 的那个，例如同目录下的 \`tour.lomt\`）。` +
                    `\n只想编它（出 IR / 目标文件）就用「Loment: 编译当前文件」。` };
  }

  // ---- ① 装了 Loment 命令（产品路径，不需要 Python）
  if (tool) {
    const rel = posixRel(root, file);
    if (!rel || rel.startsWith('..')) {
      // 相对路径要传给外部命令，`..` 会指到别处（clang/WSL 那侧解出来不是这个文件）
      return { error: '这个文件不在工作区里 —— 编译命令的基准是工作区根。'
                      + '把它的目录加进工作区，或把工作区设成它的上一层。' };
    }
    const extra = Array.isArray(o.toolArgs) ? o.toolArgs.map(String) : [];
    const args = extra.concat([action, rel]);
    if (action === 'build') {
      args.push('-o', outName(o.buildDir, file));
    }
    return { cmd: tool, args, cwd: root, how: 'cli' };
  }

  // ---- ② Windows 开发树：scripts/lomc.ps1（种子 + clang + WSL）
  if (platform === 'win32') {
    // **从工作区和文件所在目录两处向上找** —— 见 `findWrapper` 那条注释。
    const wrapper = findWrapper(root) || findWrapper(path.dirname(file));
    if (wrapper) {
      // `<仓库根>/scripts/lomc.ps1` -> `<仓库根>`：**这个**才是 cwd 与相对路径的基准
      // （`lomc.ps1` 的 `-OutDir` 默认 `loment/build` 是相对 cwd 的）。
      const base = path.dirname(path.dirname(wrapper));
      const rel = posixRel(base, file);
      if (rel && !rel.startsWith('..')) {
        const args = ['-NoProfile', '-File', wrapper, rel];
        if (action === 'run') {
          args.push('-Run');          // 用 WSL 跑那个 Linux ELF（程序用 Linux syscall）
        }
        return { cmd: 'powershell', args, cwd: base, how: 'lomc' };
      }
    }
  }

  // ---- ③ 两条都没有：说清楚该做什么，别静默
  return {
    error: '找不到能编译的东西。两种办法任选一种：\n'
      + '  · 装好工具链后在设置里填 `loment.toolCommand`'
      + '（装了包的话它是 `loment`；Windows 上经 WSL 跑时填 `wsl`，'
      + '`loment.toolArgs` 填 ["-e", "/home/<你>/.local/share/loment/bin/loment"]）；\n'
      + '  · 或者把这个文件放在 Loment 仓库里（`scripts/lomc.ps1` 在的话会自动用它 ——'
      + '**不要求打开文件夹**，从文件往上找）。',
  };
}

module.exports = { invocation, posixRel, outName, findWrapper };
