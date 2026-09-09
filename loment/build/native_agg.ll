; 由 tools/lomentc.py 生成 (native: LLVM IR, docs/144/145)
; clang -O1 driver.c this.ll -o exe

; blk_end -> u32
define i32 @blk_end() {
  %b.addr = alloca { i32, i32 }
  %t1 = getelementptr inbounds { i32, i32 }, ptr %b.addr, i32 0, i32 0
  store i32 3, ptr %t1
  %t2 = getelementptr inbounds { i32, i32 }, ptr %b.addr, i32 0, i32 1
  store i32 5, ptr %t2
  %t3 = getelementptr inbounds { i32, i32 }, ptr %b.addr, i32 0, i32 0
  %t4 = load i32, ptr %t3
  %t5 = getelementptr inbounds { i32, i32 }, ptr %b.addr, i32 0, i32 1
  %t6 = load i32, ptr %t5
  %t7 = add i32 %t4, %t6
  ret i32 %t7
}
; array_sum -> u32
define i32 @array_sum() {
  %a.addr = alloca [4 x i32]
  %s.addr = alloca i32
  %i.addr = alloca i32
  %t1 = getelementptr inbounds [4 x i32], ptr %a.addr, i32 0, i32 0
  store i32 1, ptr %t1
  %t2 = getelementptr inbounds [4 x i32], ptr %a.addr, i32 0, i32 1
  store i32 2, ptr %t2
  %t3 = getelementptr inbounds [4 x i32], ptr %a.addr, i32 0, i32 2
  store i32 3, ptr %t3
  %t4 = getelementptr inbounds [4 x i32], ptr %a.addr, i32 0, i32 3
  store i32 4, ptr %t4
  store i32 0, ptr %s.addr
  store i32 0, ptr %i.addr
  br label %L1_fcond
L1_fcond:
  %t5 = load i32, ptr %i.addr
  %t6 = icmp ult i32 %t5, 4
  br i1 %t6, label %L2_fbody, label %L3_fend
L2_fbody:
  %t7 = load i32, ptr %s.addr
  %t8 = load i32, ptr %i.addr
  %t9 = getelementptr inbounds [4 x i32], ptr %a.addr, i32 0, i32 %t8
  %t10 = load i32, ptr %t9
  %t11 = add i32 %t7, %t10
  store i32 %t11, ptr %s.addr
  %t12 = load i32, ptr %i.addr
  %t13 = add i32 %t12, 1
  store i32 %t13, ptr %i.addr
  br label %L1_fcond
L3_fend:
  %t14 = getelementptr inbounds [4 x i32], ptr %a.addr, i32 0, i32 0
  store i32 100, ptr %t14
  %t15 = load i32, ptr %s.addr
  %t16 = getelementptr inbounds [4 x i32], ptr %a.addr, i32 0, i32 0
  %t17 = load i32, ptr %t16
  %t18 = add i32 %t15, %t17
  ret i32 %t18
}
; shape_area -> u32
define i32 @shape_area(i32 %tag, i32 %v) {
  %tag.addr = alloca i32
  %v.addr = alloca i32
  %s.addr = alloca { i32, i64 }
  %r.addr = alloca i32
  %a.addr = alloca i32
  store i32 %tag, ptr %tag.addr
  store i32 %v, ptr %v.addr
  %t1 = insertvalue { i32, i64 } undef, i32 2, 0
  store { i32, i64 } %t1, ptr %s.addr
  %t2 = load i32, ptr %tag.addr
  %t3 = icmp eq i32 %t2, 0
  br i1 %t3, label %L1_then, label %L2_else
L1_then:
  %t4 = load i32, ptr %v.addr
  %t5 = insertvalue { i32, i64 } undef, i32 0, 0
  %t6 = zext i32 %t4 to i64
  %t7 = insertvalue { i32, i64 } %t5, i64 %t6, 1
  store { i32, i64 } %t7, ptr %s.addr
  br label %L3_end
L2_else:
  %t8 = load i32, ptr %v.addr
  %t9 = insertvalue { i32, i64 } undef, i32 1, 0
  %t10 = zext i32 %t8 to i64
  %t11 = insertvalue { i32, i64 } %t9, i64 %t10, 1
  store { i32, i64 } %t11, ptr %s.addr
  br label %L3_end
L3_end:
  %t12 = load { i32, i64 }, ptr %s.addr
  %t13 = extractvalue { i32, i64 } %t12, 0
  switch i32 %t13, label %L7_mwild [
      i32 0, label %L5_mCircle
      i32 1, label %L6_mSquare
    ]
L5_mCircle:
  %t14 = extractvalue { i32, i64 } %t12, 1
  %t15 = trunc i64 %t14 to i32
  store i32 %t15, ptr %r.addr
  %t16 = load i32, ptr %r.addr
  %t17 = load i32, ptr %r.addr
  %t18 = mul i32 %t16, %t17
  %t19 = mul i32 %t18, 3
  ret i32 %t19
L6_mSquare:
  %t20 = extractvalue { i32, i64 } %t12, 1
  %t21 = trunc i64 %t20 to i32
  store i32 %t21, ptr %a.addr
  %t22 = load i32, ptr %a.addr
  %t23 = load i32, ptr %a.addr
  %t24 = mul i32 %t22, %t23
  ret i32 %t24
L7_mwild:
  ret i32 0
L4_mend:
  unreachable
}
; short_circuit -> u32
define i32 @short_circuit(i32 %x) {
  %x.addr = alloca i32
  %n.addr = alloca i32
  store i32 %x, ptr %x.addr
  store i32 0, ptr %n.addr
  %t1 = load i32, ptr %x.addr
  %t2 = icmp ugt i32 %t1, 0
  br i1 %t2, label %L1_sc_rhs, label %L2_sc_short
L1_sc_rhs:
  %t3 = load i32, ptr %x.addr
  %t4 = icmp ult i32 %t3, 10
  br label %L3_sc_end
L2_sc_short:
  br label %L3_sc_end
L3_sc_end:
  %t5 = phi i1 [ %t4, %L1_sc_rhs ], [ false, %L2_sc_short ]
  br i1 %t5, label %L4_then, label %L5_else
L4_then:
  %t6 = load i32, ptr %n.addr
  %t7 = add i32 %t6, 1
  store i32 %t7, ptr %n.addr
  br label %L6_end
L5_else:
  br label %L6_end
L6_end:
  %t8 = load i32, ptr %x.addr
  %t9 = icmp eq i32 %t8, 0
  br i1 %t9, label %L8_sc_short, label %L7_sc_rhs
L7_sc_rhs:
  %t10 = load i32, ptr %x.addr
  %t11 = icmp eq i32 %t10, 5
  br label %L9_sc_end
L8_sc_short:
  br label %L9_sc_end
L9_sc_end:
  %t12 = phi i1 [ %t11, %L7_sc_rhs ], [ true, %L8_sc_short ]
  br i1 %t12, label %L10_then, label %L11_else
L10_then:
  %t13 = load i32, ptr %n.addr
  %t14 = add i32 %t13, 10
  store i32 %t14, ptr %n.addr
  br label %L12_end
L11_else:
  br label %L12_end
L12_end:
  %t15 = load i32, ptr %n.addr
  ret i32 %t15
}
