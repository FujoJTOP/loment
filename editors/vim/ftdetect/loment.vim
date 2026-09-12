" editors/vim/ftdetect/loment.vim — 让 Vim 认 .lomt / .lom
"
" 只要这个目录在 runtimepath 上 (见 editors/vim/README.md) 就生效:
"   *.lomt -> loment   (L1 源文件)
"   *.lom  -> loment   (L0 声明; 语法是 L1 的子集, 共用高亮)

autocmd BufNewFile,BufRead *.lomt setfiletype loment
autocmd BufNewFile,BufRead *.lom  setfiletype loment
