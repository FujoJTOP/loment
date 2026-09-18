/*
 * Sample.go —— Go 那一门的同意集：这份 Go 与 Loment 算出同一个数。
 *
 * ## 这一门钉的是 **Go 的形状**与**Go 的严格**
 *
 *   level     `if … { } else if … { }` **不带括号**、且最后不写 `else` 直接 return
 *   gcd       `for b != 0 { }` —— Go **没有 `while`**，一个 `for` 管三种形状
 *   score     `for i := 1; i <= n; i++ { }`（三截那种）+ `i%3 == 0 && …`
 *             —— 钉住 for 的降级（Loment 没有裸块，循环变量要改名外提）
 *             与 **`i++` / `acc += 2`**（见下面那一段）
 *   widen     **`byte` 是 `uint8` 的别名**（无符号，与 C# 同、与 Java 的 byte 反）
 *             + **`int(b)`**：Go 不做隐式数值转换，所以这一处是**源码里写好的**
 *   letter    `int('A')` —— rune 字面量就是一个整数（Go 的 `rune` 就是 `int32`）
 *   parity    返回 `bool`；`if ok` 里那个 `ok` 是**布尔变量**
 *   entry     把几个结果并起来 —— 判据比的就是它
 *
 * ## `i++` / `acc += 2` 在这里**收**，而 C / Java / C# 那几门**拒**
 *
 * 不是口径不一致，是**源语言那边不一样**：那几门里 `++` 与 `+=` 是**表达式**
 * （有值），`y = i++` 那种写法映射不过去；**Go 里它们是语句、没有值**
 * （`y = i++` 在 Go 里编不过），所以 `i++` 就是 `i = i + 1`，一字不差。
 * 按"能表达的就转"那条判据，同一处给出相反的结论 —— 差别在源语言，不在 Loment。
 *
 * ## 与 Loment 的语义差：**两处都不用补转换**
 *
 * Go 的 `&&` `||` `!` 与比较**出 `bool`**、条件**只收 `bool`**，而且 Go
 * **没有** `bool ↔ int` 的隐式转换（`if x`（x 是 int）在 Go 里编不过）——
 * 与 Java / C# 同一档。`/` 与 `%` 向零截断、`>>` 对**有符号**是算术的，也都对得上。
 * 更少见的是 **Go 的转换要求与 Loment 的 `as` 一致**：两边都要显式写。
 *
 * 期望值：`level(1000)=2`（×5=10）、`gcd(48,18)=6`、`score(100)=73`
 * （15 的倍数 6 个 +3、能 3 不能 5 的 27 个 +1、能 5 不能 3 的 14 个 +2 = 18+27+28）、
 * `widen(byte(200))=200/2=100`、`letter_index()=int('A')-64=1`、
 * `parity(4)` 为真 ⇒ bonus 40。
 * ⇒ `10 + 6 + 73 + 100 + 1 + 40 = **230**`。
 */
package main

func level(n int) int {
	if n < 10 {
		return 0
	} else if n < 100 {
		return 1
	}
	return 2
}

func gcd(a, b int) int {
	for b != 0 {
		t := a % b
		a = b
		b = t
	}
	return a
}

func score(n int) int {
	acc := 0
	for i := 1; i <= n; i++ {
		if i%3 == 0 && i%5 == 0 {
			acc += 3
		} else if i%3 == 0 {
			acc++
		} else if i%5 == 0 {
			acc += 2
		}
	}
	return acc
}

func widen(b byte) int {
	return int(b) / 2
}

func letterIndex() int {
	return int('A') - 64
}

func parity(n int) bool {
	return n%2 == 0
}

func entry() int {
	ok := parity(4)
	bonus := 0
	if ok {
		bonus = 40
	}
	return level(1000)*5 + gcd(48, 18) + score(100) + widen(200) + letterIndex() + bonus
}
