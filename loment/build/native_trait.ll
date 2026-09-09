; 由 tools/lomentc.py 生成 (native: LLVM IR, docs/144/145)
; clang -O1 driver.c this.ll -o exe

; Small_measure -> u32
define i32 @Small_measure({ i32 } %__self) {
entry:
  %__self.addr = alloca { i32 }
  store { i32 } %__self, ptr %__self.addr
  %t1 = getelementptr inbounds { i32 }, ptr %__self.addr, i32 0, i32 0
  %t2 = load i32, ptr %t1
  ret i32 %t2
}
; Big_measure -> u32
define i32 @Big_measure({ i32 } %__self) {
entry:
  %__self.addr = alloca { i32 }
  store { i32 } %__self, ptr %__self.addr
  %t1 = getelementptr inbounds { i32 }, ptr %__self.addr, i32 0, i32 0
  %t2 = load i32, ptr %t1
  %t3 = mul i32 %t2, 10
  ret i32 %t3
}
; call_small -> u32
define i32 @call_small() {
entry:
  %s.addr = alloca { i32 }
  %t1 = getelementptr inbounds { i32 }, ptr %s.addr, i32 0, i32 0
  store i32 7, ptr %t1
  %t2 = load { i32 }, ptr %s.addr
  %t3 = call i32 @Small_measure({ i32 } %t2)
  ret i32 %t3
}
; call_big -> u32
define i32 @call_big() {
entry:
  %b.addr = alloca { i32 }
  %t1 = getelementptr inbounds { i32 }, ptr %b.addr, i32 0, i32 0
  store i32 7, ptr %t1
  %t2 = load { i32 }, ptr %b.addr
  %t3 = call i32 @Big_measure({ i32 } %t2)
  ret i32 %t3
}
