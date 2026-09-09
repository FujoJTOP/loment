; 由 tools/lomentc.py 生成 (native: LLVM IR, docs/144/145)
; clang -O1 driver.c this.ll -o exe

; sum -> u32
define i32 @sum({ ptr, i64 } %xs) {
entry:
  %xs.addr = alloca { ptr, i64 }
  %s.addr = alloca i32
  %i.addr = alloca i32
  store { ptr, i64 } %xs, ptr %xs.addr
  store i32 0, ptr %s.addr
  store i32 0, ptr %i.addr
  br label %L1_fcond
L1_fcond:
  %t1 = load i32, ptr %i.addr
  %t2 = load { ptr, i64 }, ptr %xs.addr
  %t3 = extractvalue { ptr, i64 } %t2, 1
  %t4 = trunc i64 %t3 to i32
  %t5 = icmp ult i32 %t1, %t4
  br i1 %t5, label %L2_fbody, label %L3_fend
L2_fbody:
  %t6 = load i32, ptr %s.addr
  %t7 = load { ptr, i64 }, ptr %xs.addr
  %t8 = extractvalue { ptr, i64 } %t7, 0
  %t9 = load i32, ptr %i.addr
  %t10 = getelementptr inbounds i32, ptr %t8, i32 %t9
  %t11 = load i32, ptr %t10
  %t12 = add i32 %t6, %t11
  store i32 %t12, ptr %s.addr
  %t13 = load i32, ptr %i.addr
  %t14 = add i32 %t13, 1
  store i32 %t14, ptr %i.addr
  br label %L1_fcond
L3_fend:
  %t15 = load i32, ptr %s.addr
  ret i32 %t15
}
; call_sum -> u32
define i32 @call_sum() {
entry:
  %a.addr = alloca [4 x i32]
  %t1 = getelementptr inbounds [4 x i32], ptr %a.addr, i32 0, i32 0
  store i32 1, ptr %t1
  %t2 = getelementptr inbounds [4 x i32], ptr %a.addr, i32 0, i32 1
  store i32 2, ptr %t2
  %t3 = getelementptr inbounds [4 x i32], ptr %a.addr, i32 0, i32 2
  store i32 3, ptr %t3
  %t4 = getelementptr inbounds [4 x i32], ptr %a.addr, i32 0, i32 3
  store i32 4, ptr %t4
  %t5 = getelementptr inbounds [4 x i32], ptr %a.addr, i32 0, i32 0
  %t6 = insertvalue { ptr, i64 } undef, ptr %t5, 0
  %t7 = insertvalue { ptr, i64 } %t6, i64 4, 1
  %t8 = call i32 @sum({ ptr, i64 } %t7)
  ret i32 %t8
}
; first_of -> u32
define i32 @first_of({ ptr, i64 } %xs) {
entry:
  %xs.addr = alloca { ptr, i64 }
  store { ptr, i64 } %xs, ptr %xs.addr
  %t1 = load { ptr, i64 }, ptr %xs.addr
  %t2 = extractvalue { ptr, i64 } %t1, 0
  %t3 = getelementptr inbounds i32, ptr %t2, i32 0
  %t4 = load i32, ptr %t3
  ret i32 %t4
}
; call_first -> u32
define i32 @call_first() {
entry:
  %a.addr = alloca [3 x i32]
  %t1 = getelementptr inbounds [3 x i32], ptr %a.addr, i32 0, i32 0
  store i32 7, ptr %t1
  %t2 = getelementptr inbounds [3 x i32], ptr %a.addr, i32 0, i32 1
  store i32 8, ptr %t2
  %t3 = getelementptr inbounds [3 x i32], ptr %a.addr, i32 0, i32 2
  store i32 9, ptr %t3
  %t4 = getelementptr inbounds [3 x i32], ptr %a.addr, i32 0, i32 0
  %t5 = insertvalue { ptr, i64 } undef, ptr %t4, 0
  %t6 = insertvalue { ptr, i64 } %t5, i64 3, 1
  %t7 = call i32 @first_of({ ptr, i64 } %t6)
  ret i32 %t7
}
