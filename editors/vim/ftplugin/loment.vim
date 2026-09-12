" editors/vim/ftplugin/loment.vim — Loment 文件的 :make 与错误格式
"
" 不用任何插件: 诊断走 `lsp --check FILE` 的输出 (`路径:行:列: E0NN 标题`), 由 errorformat
" 解析进 quickfix —— :make 之后 :copen / :cnext 就能跳错误。
"
"   :LomentCheck   只检查 (默认; 也绑在 <leader>k)
"   :LomentBuild   编译出 .ll/.elf (scripts/lomc.ps1; 也绑在 <leader>b)
"
" 服务与构建命令都可配: let g:loment_lsp_cmd = ... / let g:loment_build_cmd = ...

if exists('b:did_ftplugin')
  finish
endif
let b:did_ftplugin = 1

" 语言服务怎么起 —— 与 VS Code 的 loment.serverCommand/serverArgs 是同一件事。
if !exists('g:loment_lsp_cmd')
  if has('win32') || has('win64')
    " Windows: 经 WSL 跑 scripts/install-lsp.ps1 装好的服务 (无需 Python)
    let g:loment_lsp_cmd = 'wsl -e /home/' . $USERNAME . '/.local/share/loment/lsp'
  else
    let g:loment_lsp_cmd = expand('$HOME') . '/.local/share/loment/lsp'
  endif
endif
if !exists('g:loment_build_cmd')
  let g:loment_build_cmd = 'powershell -NoProfile -File scripts/lomc.ps1'
endif

" `路径:行:列: E0NN 标题` —— %n 把错误码当编号 (:copen 里就显示 E013)
setlocal errorformat=%f:%l:%c:\ E%n\ %m

function! s:SetMakeprg(tail) abort
  execute 'setlocal makeprg=' . escape(g:loment_lsp_cmd, ' \') . '\ --check\ %'
endfunction

" 默认 makeprg = 检查
call s:SetMakeprg('--check')

command! -buffer LomentCheck execute 'setlocal makeprg=' . escape(g:loment_lsp_cmd, ' \') . '\ --check\ %' | make
command! -buffer LomentBuild execute 'setlocal makeprg=' . escape(g:loment_build_cmd, ' \') . '\ %' | make

nnoremap <buffer> <leader>k :LomentCheck<CR>
nnoremap <buffer> <leader>b :LomentBuild<CR>
