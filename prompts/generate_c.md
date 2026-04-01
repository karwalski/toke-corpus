Generate a C reference implementation for the following task.

**Category:** {category}

**Task:** {task_description}

**Expected function signature:** {expected_signature}

**Rules:**
- Output ONLY the C source code. No explanations, no markdown fences.
- Use C99 standard (`-std=c99`).
- The program must be standalone and compile with `gcc -std=c99 -o prog prog.c -lm`.
- Include a `main()` function with at least 5 test cases that print results to stdout.
- Use `printf()` for all output. Each test case prints its result on its own line.
- Include edge cases in the test cases (empty arrays, zero values, boundary conditions).
- Include all necessary `#include` headers.
- Do not use platform-specific extensions.
