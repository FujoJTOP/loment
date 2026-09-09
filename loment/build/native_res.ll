; 由 tools/lomentc.py 生成 (native: LLVM IR, docs/144/145)
; clang -O1 driver.c this.ll -o exe

; parse_small -> Result_u32_u32
define { i32, i64 } @parse_small(i32 %v) {
  %v.addr = alloca i32
  store i32 %v, ptr %v.addr
  %t1 = load i32, ptr %v.addr
  %t2 = icmp ugt i32 %t1, 10
  br i1 %t2, label %L1_then, label %L2_else
L1_then:
  %t3 = insertvalue { i32, i64 } undef, i32 1, 0
  %t4 = zext i32 1 to i64
  %t5 = insertvalue { i32, i64 } %t3, i64 %t4, 1
  ret { i32, i64 } %t5
L2_else:
  br label %L3_end
L3_end:
  %t6 = load i32, ptr %v.addr
  %t7 = insertvalue { i32, i64 } undef, i32 0, 0
  %t8 = zext i32 %t6 to i64
  %t9 = insertvalue { i32, i64 } %t7, i64 %t8, 1
  ret { i32, i64 } %t9
}
; twice -> Result_u32_u32
define { i32, i64 } @twice(i32 %v) {
  %v.addr = alloca i32
  %__t15.addr = alloca { i32, i64 }
  %x.addr = alloca i32
  %__v15.addr = alloca i32
  %__e15.addr = alloca i32
  store i32 %v, ptr %v.addr
  %t1 = load i32, ptr %v.addr
  %t2 = call { i32, i64 } @parse_small(i32 %t1)
  store { i32, i64 } %t2, ptr %__t15.addr
  %t3 = load { i32, i64 }, ptr %__t15.addr
  %t4 = extractvalue { i32, i64 } %t3, 0
  switch i32 %t4, label %L1_mend [
      i32 0, label %L2_mOk
      i32 1, label %L3_mErr
    ]
L2_mOk:
  %t5 = extractvalue { i32, i64 } %t3, 1
  %t6 = trunc i64 %t5 to i32
  store i32 %t6, ptr %__v15.addr
  %t7 = load i32, ptr %__v15.addr
  store i32 %t7, ptr %x.addr
  br label %L1_mend
L3_mErr:
  %t8 = extractvalue { i32, i64 } %t3, 1
  %t9 = trunc i64 %t8 to i32
  store i32 %t9, ptr %__e15.addr
  %t10 = load i32, ptr %__e15.addr
  %t11 = insertvalue { i32, i64 } undef, i32 1, 0
  %t12 = zext i32 %t10 to i64
  %t13 = insertvalue { i32, i64 } %t11, i64 %t12, 1
  ret { i32, i64 } %t13
L1_mend:
  %t14 = load i32, ptr %x.addr
  %t15 = mul i32 %t14, 2
  %t16 = insertvalue { i32, i64 } undef, i32 0, 0
  %t17 = zext i32 %t15 to i64
  %t18 = insertvalue { i32, i64 } %t16, i64 %t17, 1
  ret { i32, i64 } %t18
}
; call_ok -> u32
define i32 @call_ok() {
  %r.addr = alloca { i32, i64 }
  %v.addr = alloca i32
  %t1 = call { i32, i64 } @twice(i32 3)
  store { i32, i64 } %t1, ptr %r.addr
  %t2 = load { i32, i64 }, ptr %r.addr
  %t3 = extractvalue { i32, i64 } %t2, 0
  switch i32 %t3, label %L3_mwild [
      i32 0, label %L2_mOk
    ]
L2_mOk:
  %t4 = extractvalue { i32, i64 } %t2, 1
  %t5 = trunc i64 %t4 to i32
  store i32 %t5, ptr %v.addr
  %t6 = load i32, ptr %v.addr
  ret i32 %t6
L3_mwild:
  br label %L1_mend
L1_mend:
  ret i32 0
}
; call_err -> u32
define i32 @call_err() {
  %r.addr = alloca { i32, i64 }
  %v.addr = alloca i32
  %t1 = call { i32, i64 } @twice(i32 50)
  store { i32, i64 } %t1, ptr %r.addr
  %t2 = load { i32, i64 }, ptr %r.addr
  %t3 = extractvalue { i32, i64 } %t2, 0
  switch i32 %t3, label %L3_mwild [
      i32 0, label %L2_mOk
    ]
L2_mOk:
  %t4 = extractvalue { i32, i64 } %t2, 1
  %t5 = trunc i64 %t4 to i32
  store i32 %t5, ptr %v.addr
  %t6 = load i32, ptr %v.addr
  ret i32 %t6
L3_mwild:
  ret i32 99
L1_mend:
  unreachable
}
