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
" 三个分支的原因: MSYS/Cygwin 版 vim 在 spawn wsl.exe 时会把 `/home/...` 这个参数当
" **路径**转换掉 (实测变成 C:/Program Files/Git/home/... -> execvpe 失败), 所以要经一层
" cmd.exe 垫片 (scripts/install-lsp.ps1 会生成 loment/build/loment-lsp.cmd); 装在 WSL 里的
" vim 直接用原生路径; 非 Windows 平台同理。
if !exists('g:loment_lsp_cmd')
  if has('win32unix')
    " MSYS/Cygwin 版 vim (Git Bash 自带的就是)
    let g:loment_lsp_cmd = 'loment/build/loment-lsp.cmd'
  elseif has('win32') || has('win64')
    let g:loment_lsp_cmd = 'wsl -e /home/' . $USERNAME . '/.local/share/loment/lsp'
  else
    let g:loment_lsp_cmd = expand('$HOME') . '/.local/share/loment/lsp'
  endif
endif
if !exists('g:loment_build_cmd')
  let g:loment_build_cmd = 'powershell -NoProfile -File scripts/lomc.ps1'
endif

" `路径:行:列: E0NN 标题`
" 整条消息 (%m) 都要: 只抓 %n 的话 Vim 会把 E013 打印成 E13 (去前导零)
setlocal errorformat=%f:%l:%c:\ %m

function! s:SetMakeprg(tail) abort
  execute 'setlocal makeprg=' . escape(g:loment_lsp_cmd, ' \') . '\ --check\ %'
endfunction

" 默认 makeprg = 检查
call s:SetMakeprg('--check')

command! -buffer LomentCheck execute 'setlocal makeprg=' . escape(g:loment_lsp_cmd, ' \') . '\ --check\ %' | make
command! -buffer LomentBuild execute 'setlocal makeprg=' . escape(g:loment_build_cmd, ' \') . '\ %' | make

nnoremap <buffer> <leader>k :LomentCheck<CR>
nnoremap <buffer> <leader>b :LomentBuild<CR>
