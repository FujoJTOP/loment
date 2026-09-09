; 由 tools/lomentc.py 生成 (native: LLVM IR, docs/144/145)
; clang -O1 driver.c this.ll -o exe

; fill -> u32
define i32 @fill({ ptr, i64 } %xs, i32 %v) {
  %xs.addr = alloca { ptr, i64 }
  %v.addr = alloca i32
  %n.addr = alloca i32
  %i.addr = alloca i32
  store { ptr, i64 } %xs, ptr %xs.addr
  store i32 %v, ptr %v.addr
  %t1 = load { ptr, i64 }, ptr %xs.addr
  %t2 = extractvalue { ptr, i64 } %t1, 1
  %t3 = trunc i64 %t2 to i32
  store i32 %t3, ptr %n.addr
  store i32 0, ptr %i.addr
  br label %L1_wcond
L1_wcond:
  %t4 = load i32, ptr %i.addr
  %t5 = load i32, ptr %n.addr
  %t6 = icmp ult i32 %t4, %t5
  br i1 %t6, label %L2_wbody, label %L3_wend
L2_wbody:
  %t7 = load { ptr, i64 }, ptr %xs.addr
  %t8 = extractvalue { ptr, i64 } %t7, 0
  %t9 = load i32, ptr %i.addr
  %t10 = getelementptr inbounds i32, ptr %t8, i32 %t9
  %t11 = load i32, ptr %v.addr
  %t12 = load i32, ptr %i.addr
  %t13 = add i32 %t11, %t12
  store i32 %t13, ptr %t10
  %t14 = load i32, ptr %i.addr
  %t15 = add i32 %t14, 1
  store i32 %t15, ptr %i.addr
  br label %L1_wcond
L3_wend:
  %t16 = load i32, ptr %n.addr
  ret i32 %t16
}
; call_fill -> u32
define i32 @call_fill() {
  %a.addr = alloca [4 x i32]
  %n.addr = alloca i32
  %t1 = getelementptr inbounds [4 x i32], ptr %a.addr, i32 0, i32 0
  store i32 0, ptr %t1
  %t2 = getelementptr inbounds [4 x i32], ptr %a.addr, i32 0, i32 1
  store i32 0, ptr %t2
  %t3 = getelementptr inbounds [4 x i32], ptr %a.addr, i32 0, i32 2
  store i32 0, ptr %t3
  %t4 = getelementptr inbounds [4 x i32], ptr %a.addr, i32 0, i32 3
  store i32 0, ptr %t4
  %t5 = getelementptr inbounds [4 x i32], ptr %a.addr, i32 0, i32 0
  %t6 = insertvalue { ptr, i64 } undef, ptr %t5, 0
  %t7 = insertvalue { ptr, i64 } %t6, i64 4, 1
  %t8 = call i32 @fill({ ptr, i64 } %t7, i32 10)
  store i32 %t8, ptr %n.addr
  %t9 = load i32, ptr %n.addr
  %t10 = getelementptr inbounds [4 x i32], ptr %a.addr, i32 0, i32 0
  %t11 = load i32, ptr %t10
  %t12 = add i32 %t9, %t11
  %t13 = getelementptr inbounds [4 x i32], ptr %a.addr, i32 0, i32 3
  %t14 = load i32, ptr %t13
  %t15 = add i32 %t12, %t14
  ret i32 %t15
}
; read_only -> u32
define i32 @read_only({ ptr, i64 } %xs) {
  %xs.addr = alloca { ptr, i64 }
  store { ptr, i64 } %xs, ptr %xs.addr
  %t1 = load { ptr, i64 }, ptr %xs.addr
  %t2 = extractvalue { ptr, i64 } %t1, 0
  %t3 = getelementptr inbounds i32, ptr %t2, i32 0
  %t4 = load i32, ptr %t3
  ret i32 %t4
}
; call_read_only -> u32
define i32 @call_read_only() {
  %a.addr = alloca [2 x i32]
  %t1 = getelementptr inbounds [2 x i32], ptr %a.addr, i32 0, i32 0
  store i32 5, ptr %t1
  %t2 = getelementptr inbounds [2 x i32], ptr %a.addr, i32 0, i32 1
  store i32 6, ptr %t2
  %t3 = getelementptr inbounds [2 x i32], ptr %a.addr, i32 0, i32 0
  %t4 = insertvalue { ptr, i64 } undef, ptr %t3, 0
  %t5 = insertvalue { ptr, i64 } %t4, i64 2, 1
  %t6 = call i32 @read_only({ ptr, i64 } %t5)
  %t7 = getelementptr inbounds [2 x i32], ptr %a.addr, i32 0, i32 0
  %t8 = insertvalue { ptr, i64 } undef, ptr %t7, 0
  %t9 = insertvalue { ptr, i64 } %t8, i64 2, 1
  %t10 = call i32 @read_only({ ptr, i64 } %t9)
  %t11 = add i32 %t6, %t10
  ret i32 %t11
}
