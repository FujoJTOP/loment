; 由 tools/lomentc.py 生成 (native: LLVM IR, docs/144/145)
; clang -O1 driver.c this.ll -o exe

; call_pair_max -> u32
define i32 @call_pair_max() {
  %p.addr = alloca { i32, i32 }
  %t1 = getelementptr inbounds { i32, i32 }, ptr %p.addr, i32 0, i32 0
  store i32 7, ptr %t1
  %t2 = getelementptr inbounds { i32, i32 }, ptr %p.addr, i32 0, i32 1
  store i32 3, ptr %t2
  %t3 = getelementptr inbounds { i32, i32 }, ptr %p.addr, i32 0, i32 0
  %t4 = load i32, ptr %t3
  %t5 = getelementptr inbounds { i32, i32 }, ptr %p.addr, i32 0, i32 1
  %t6 = load i32, ptr %t5
  %t7 = call i32 @max_u32(i32 %t4, i32 %t6)
  ret i32 %t7
}
; call_max_i32 -> i32
define i32 @call_max_i32() {
  %x.addr = alloca i32
  %y.addr = alloca i32
  %t1 = sub i32 0, 5
  store i32 %t1, ptr %x.addr
  store i32 3, ptr %y.addr
  %t2 = load i32, ptr %x.addr
  %t3 = load i32, ptr %y.addr
  %t4 = call i32 @max_i32(i32 %t2, i32 %t3)
  ret i32 %t4
}
; call_opt -> u32
define i32 @call_opt() {
  %a.addr = alloca { i32, i64 }
  %b.addr = alloca { i32, i64 }
  %s.addr = alloca i32
  %v.addr = alloca i32
  %t1 = insertvalue { i32, i64 } undef, i32 0, 0
  %t2 = zext i32 42 to i64
  %t3 = insertvalue { i32, i64 } %t1, i64 %t2, 1
  store { i32, i64 } %t3, ptr %a.addr
  %t4 = insertvalue { i32, i64 } undef, i32 1, 0
  store { i32, i64 } %t4, ptr %b.addr
  store i32 0, ptr %s.addr
  %t5 = load { i32, i64 }, ptr %a.addr
  %t6 = extractvalue { i32, i64 } %t5, 0
  switch i32 %t6, label %L3_mwild [
      i32 0, label %L2_mSome
    ]
L2_mSome:
  %t7 = extractvalue { i32, i64 } %t5, 1
  %t8 = trunc i64 %t7 to i32
  store i32 %t8, ptr %v.addr
  %t9 = load i32, ptr %v.addr
  store i32 %t9, ptr %s.addr
  br label %L1_mend
L3_mwild:
  store i32 1, ptr %s.addr
  br label %L1_mend
L1_mend:
  %t10 = load { i32, i64 }, ptr %b.addr
  %t11 = extractvalue { i32, i64 } %t10, 0
  switch i32 %t11, label %L6_mwild [
      i32 0, label %L5_mSome
    ]
L5_mSome:
  %t12 = extractvalue { i32, i64 } %t10, 1
  %t13 = trunc i64 %t12 to i32
  store i32 %t13, ptr %v.addr
  %t14 = load i32, ptr %s.addr
  %t15 = load i32, ptr %v.addr
  %t16 = add i32 %t14, %t15
  store i32 %t16, ptr %s.addr
  br label %L4_mend
L6_mwild:
  %t17 = load i32, ptr %s.addr
  %t18 = add i32 %t17, 2
  store i32 %t18, ptr %s.addr
  br label %L4_mend
L4_mend:
  %t19 = load i32, ptr %s.addr
  ret i32 %t19
}
; max_u32 -> u32
define i32 @max_u32(i32 %a, i32 %b) {
  %a.addr = alloca i32
  %b.addr = alloca i32
  store i32 %a, ptr %a.addr
  store i32 %b, ptr %b.addr
  %t1 = load i32, ptr %a.addr
  %t2 = load i32, ptr %b.addr
  %t3 = icmp ugt i32 %t1, %t2
  br i1 %t3, label %L1_then, label %L2_else
L1_then:
  %t4 = load i32, ptr %a.addr
  ret i32 %t4
L2_else:
  br label %L3_end
L3_end:
  %t5 = load i32, ptr %b.addr
  ret i32 %t5
}
; max_i32 -> i32
define i32 @max_i32(i32 %a, i32 %b) {
  %a.addr = alloca i32
  %b.addr = alloca i32
  store i32 %a, ptr %a.addr
  store i32 %b, ptr %b.addr
  %t1 = load i32, ptr %a.addr
  %t2 = load i32, ptr %b.addr
  %t3 = icmp sgt i32 %t1, %t2
  br i1 %t3, label %L1_then, label %L2_else
L1_then:
  %t4 = load i32, ptr %a.addr
  ret i32 %t4
L2_else:
  br label %L3_end
L3_end:
  %t5 = load i32, ptr %b.addr
  ret i32 %t5
}
