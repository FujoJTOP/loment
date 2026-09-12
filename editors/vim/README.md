# editors/vim — 不用 VS Code 也能写 Loment

三样东西：**语法高亮**（无插件）、**:make → quickfix 诊断**（无插件、不用 Python）、
以及给"任意 LSP 客户端"的接法说明。详细背景见 `docs/157` §3.6。

## 1. 装法（不改系统配置也能先试）

```vim
" 放进 vimrc（或先用 -c 试）
set runtimepath+=<仓库>/editors/vim
filetype plugin on
```

打开任意 `.lomt` 应该立刻有高亮（`editors/vim/ftdetect/` 认 `.lomt` 与 `.lom`）。

```bash
# 一行试运行（Git Bash / WSL / Linux 都行）
vim -N -c 'set rtp+=/d/Dev/FujoOS-FujoLang/editors/vim' -c 'filetype plugin on' \
    loment/examples/mathutil.lomt
```

## 2. 诊断：`:make` 进 quickfix（零插件、零 Python）

`ftplugin/loment.vim` 已经把 `makeprg` 设成语言服务的 `--check`、`errorformat` 设成
`路径:行:列: E0NN 标题`：

```vim
:LomentCheck     " 检查当前文件 -> :copen 看列表, :cnext 跳下一个 (也绑在 <leader>k)
:LomentBuild     " 编译出 .ll/.elf (scripts/lomc.ps1; 也绑在 <leader>b)
```

服务路径默认是 `scripts/install-lsp.ps1` 装的位置（Windows 经 WSL 调，**不需要 Python**）。
要改：

```vim
let g:loment_lsp_cmd   = 'wsl -e /home/<you>/.local/share/loment/lsp'   " 非 MSYS 版 vim / WSL 里的 vim
let g:loment_build_cmd = 'powershell -NoProfile -File scripts/lomc.ps1'
```

> **MSYS/Cygwin 版 vim 的坑（Git Bash 自带的就是这种）**：它 spawn `wsl.exe` 时会把
> `/home/...` 这个参数当**路径**转换掉（实测变成 `C:/Program Files/Git/home/...`，
> `execvpe ... No such file or directory`）。所以本插件的默认值在这种 vim 下是
> `loment/build/loment-lsp.cmd` —— `scripts/install-lsp.ps1` 生成的垫片（经 cmd.exe 调 wsl，
> cmd 不做 MSYS 转换）。装了服务之后它就在 `loment/build/` 里；在别的项目里用时把
> `g:loment_lsp_cmd` 设成该垫片的绝对路径。

## 3. 想要补全/跳转：任意 LSP 客户端都能接

LSP 与编辑器无关，服务就在 `scripts/install-lsp.ps1` 装的那个可执行文件里
（Windows 上通过 WSL 起，参数是 `-e <路径>`）：

| 编辑器 | 接法 |
|---|---|
| Neovim ≥ 0.11 | `vim.lsp.start({ name='loment', cmd=vim.split(g:loment_lsp_cmd, ' '), root_dir=vim.fn.getcwd() })` |
| Neovim + nvim-lspconfig | 自定义 `lspconfig.loment = { default_config = { cmd = {...}, filetypes = {'loment'}, root_dir = ... } }` |
| Vim（`vim-lsp` / `coc.nvim`） | 注册 `loment` 语言并指向同一命令 |
| Emacs | `lsp-register-client`（`make-lsp-client :new-connection (lsp-stdio-connection '("wsl" "-e" "<路径>"))`） |
| Sublime / Zed / Kate / Helix | 各自的 LSP 配置里加一个 `loment` 语言 + 同一命令行 |

VS Code 用同一个服务时是设置 `loment.serverCommand` / `loment.serverArgs`（见 docs/157 §3.5）。

## 4. 文件关联

想让双击 `.lomt` 直接进某个编辑器（而不是 VS Code）：

```bash
python tools/loment_filetype.py --register --editor "C:\path\to\your-editor.exe"
python tools/loment_filetype.py --status
```

## 5. 本目录的内容

| 文件 | 作用 |
|---|---|
| `syntax/loment.vim` | 高亮（关键字/类型/内建/声明名/数字/字符串/注释，含 `///` 文档注释） |
| `ftdetect/loment.vim` | `*.lomt` / `*.lom` 认成 `loment` |
| `ftplugin/loment.vim` | `makeprg` + `errorformat` + `:LomentCheck` / `:LomentBuild` + 键位 |

**判据**：`tools/loment_editors_test.py` —— (1) 本目录的词表必须都在 VS Code 的 TextMate
语法里（防脱节）；(2) 用无头 vim 打开一个 `.lomt`，断言 `filetype` 与各 token 的语法组。
