Generate a toke program for the following task.

**Category:** {category}

**Task:** {task_description}

**Expected function signature:** {expected_signature}

**Rules:**
- Output ONLY the toke source code. No explanations, no markdown fences, no comments.
- The program must start with `M=` module declaration.
- Parameters and arguments use `;` as separator, NOT `,`.
- Array elements use `;` as separator: `[1;2;3]`.
- Return with `<`, not `return`.
- Conditionals: `if(cond){{...}}` / `if(cond){{...}}el{{...}}`
- Mutable bindings: `let x=mut.0;` (NOT `let mut x=0` or `mut x:i64=0`)
- No type annotations on let bindings: `let x=42` not `let x:i64=42`
- Loops: `lp(let i=0;i<n;i=i+1){{...}}` — MUST have exactly 3 semicolon-separated parts: init;condition;update. No while-style loops.
- No `%` modulo operator. Compute modulo as `a-a/b*b`.
- Equality comparison: `=` (not `==`). No `!=`, `<=`, `>=` operators. Use `!(a>b)` instead of `a<=b`. Use `!(a<b)` instead of `a>=b`. Use `!(a=b)` instead of `a!=b`.
- No `&&` or `||` operators. For AND: nest ifs — `if(a){{if(b){{...}}}}`. For OR: use a flag — `let zor=mut.false;if(a){{zor=true}};if(b){{zor=true}};if(zor){{...}}`.
- String type: `Str` (not `string` or `String`)
- No comments of any kind (`//`, `#`, `/* */` are all illegal).
- Loop: `lp` NOT `for` or `while`. There is no `for` or `while` keyword.
- Else: `el` NOT `else`. For else-if chains: `if(a){{...}}el{{if(b){{...}}el{{...}}}}`
- Boolean values: `true`/`false` (lowercase only, NOT `True`/`False`)
- No `++`, `--`, `+=`, `-=` operators. Write `i=i+1` instead.
- `if` is NOT an expression. You CANNOT write `let x=if(cond){{a}}el{{b}}`. Instead: `let x=mut.0; if(cond){{x=a}}el{{x=b}};`
- No underscore `_` in any identifier. `_idx`, `_tmp`, `_` are all illegal. Use `idx`, `tmp`, `zz`.
- Error handling rules:
  - Error types are struct-like: `T=MyErr{{Variant:bool}};`
  - Error union return type: `F=f():T!MyErr{{...}};`
  - Return an error: `<MyErr{{Variant:true}};`
  - Propagate errors with `!`: `let val=fallibleCall()!MyErr;`
  - Match on error results: `result|{{Ok:v expr;Err:e expr}}`
  - Match arm bodies are single EXPRESSIONS — `result|{{Ok:v v;Err:e 0}}`. NO blocks `{{...}}` inside match arms, NO `<` inside match arms, NO statements.
  - If you need multiple statements for a match result, compute them BEFORE the match and store in a mutable variable.
  - Error variant field names start with uppercase: `T=MyErr{{BadInput:bool}};` not `T=MyErr{{badInput:bool}};`
- The program must be complete and compilable.
