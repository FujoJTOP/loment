" editors/vim/syntax/loment.vim — Loment (.lomt) 语法高亮
"
" 与 editors/vscode/syntaxes/loment.tmLanguage.json 是同一套语言的两种表达;
" 关键字/类型词表必须与它一致 —— tools/loment_editors_test.py 会交叉核对, 漂了就红。
"
" 用法 (不动系统配置也能试):
"   vim -N -c 'set rtp+=<仓库>/editors/vim' -c 'filetype plugin on' foo.lomt
"
" 两个 Vim 特有的坑 (实测踩过, 都写在对应位置):
"   1. 同一起点"后定义者胜" —— 注释规则必须放**最后**, 否则 `//` 被单字符运算符 `/` 抢走;
"   2. `\zs` 不能用来"跳过关键字突出后面的名字" —— 引擎在关键字匹配后已跳过那段文本,
"      要用 `nextgroup` + `contained`。

if exists('b:current_syntax')
  finish
endif

" ---- 关键字 (与 TextMate 语法同一张表)
syn keyword lomentKeyword let if else while for in match
syn keyword lomentKeyword const return mut pub use module as
" Loment 独有
syn keyword lomentKeyword guard excluded interrupt
syn keyword lomentBool true false
syn keyword lomentSelf self

" ---- 类型 / 内建
syn keyword lomentType u8 u16 u32 u64 i8 i16 i32 i64 bool str ptr
syn keyword lomentBuiltin load8 load16 load32 store8 store16 store32
syn keyword lomentBuiltin alloc free panic
syn keyword lomentBuiltin str_len str_byte str_ptr str_eq str_concat slice_len
syn keyword lomentBuiltin syscall4 get_bits set_bits atomic_add ptr_add ptr_sub

" ---- 声明名: `fn add` / `struct Pair` / `capability blk` 里的那个名字
" (nextgroup: 匹配完声明关键字后, 下一个词按 contained 项上色)
syn match lomentKeyword "fn" nextgroup=lomentFuncName skipwhite
syn match lomentKeyword "\<struct\>" nextgroup=lomentTypeName skipwhite
syn match lomentKeyword "\<enum\>" nextgroup=lomentTypeName skipwhite
syn match lomentKeyword "\<trait\>" nextgroup=lomentTypeName skipwhite
syn match lomentKeyword "\<capability\>" nextgroup=lomentCapName skipwhite
syn match lomentFuncName "\w\+" contained
syn match lomentTypeName "\w\+" contained
syn match lomentCapName "\w\+" contained

" ---- 数字 (十进制 / 0x) 与字符串 (含转义; 跨行也可以)
syn match lomentNumber "\<\d\+"
syn match lomentNumber "\<0[xX][0-9a-fA-F]\+"
syn match lomentEscape contained "\\\\\|\\\"" 
syn region lomentString start=+"+ skip=+\\\\\|\\"+ end=+"+ contains=lomentEscape

" ---- 运算符
syn match lomentOperator "->\|=>\|::\|==\|!=\|<=\|>=\|&&\|||\|<<\|>>"
syn match lomentOperator "[-+*/%=<>!&|^?]"

" ---- 注释 (必须在最后: 见文件头坑 1); /// 文档注释要排在 // 之后 (后定义者胜)
syn keyword lomentTodo contained TODO FIXME XXX 注意
syn match lomentComment "//.*$" contains=lomentTodo
syn match lomentDocComment "///.*$" contains=lomentTodo
syn region lomentComment start="/\*" end="\*/" contains=lomentTodo keepend

hi def link lomentKeyword    Keyword
hi def link lomentBool       Boolean
hi def link lomentSelf       Special
hi def link lomentType       Type
hi def link lomentBuiltin    Function
hi def link lomentFuncName   Function
hi def link lomentTypeName   Type
hi def link lomentCapName    Special
hi def link lomentNumber     Number
hi def link lomentEscape     SpecialChar
hi def link lomentString     String
hi def link lomentOperator   Operator
hi def link lomentComment    Comment
hi def link lomentDocComment Comment
hi def link lomentTodo       Todo

let b:current_syntax = 'loment'
