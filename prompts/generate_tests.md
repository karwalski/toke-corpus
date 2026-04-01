Generate test cases for the following task.

**Category:** {category}

**Task:** {task_description}

**Expected function signature:** {expected_signature}

**Rules:**
- Output ONLY a JSON array. No explanations, no markdown fences.
- Generate at least 5 test cases.
- Each test case is an object with `"inputs"` (array of input values) and `"expected_output"` (string of expected stdout).
- Include edge cases:
  - Empty collections (empty arrays, empty strings, empty maps)
  - Zero and negative values
  - Boundary conditions (min/max values, single-element arrays)
  - Typical cases with varied inputs
- Input values should be JSON primitives (numbers, strings, booleans) or arrays/objects.
- The `expected_output` must be the exact string printed to stdout, including newlines if multiple lines.

**Example format:**
```json
[
  {{"inputs": [5, 3], "expected_output": "8"}},
  {{"inputs": [0, 0], "expected_output": "0"}},
  {{"inputs": [-1, 1], "expected_output": "0"}},
  {{"inputs": [1000000, 999999], "expected_output": "1999999"}},
  {{"inputs": [42, -42], "expected_output": "0"}}
]
```
