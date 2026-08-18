"""Constraint-driven generation prompt templates for toke corpus.

Forces LLMs to generate toke programs exercising specific underrepresented
Phase 2 language features: sum types, error propagation, match expressions,
map types, arena blocks, multi-parameter functions, nested structs, and
error types with 3+ variants.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

SPEC_REFERENCE = (Path(__file__).parent.parent / "spec-reference.md").read_text()


@dataclass
class Constraint:
    name: str
    description: str
    required_features: list[str]
    prompt_template: str
    example_output: str
    difficulty: int  # 1-5
    validation_criteria: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Constraint definitions
# ---------------------------------------------------------------------------

_CONSTRAINTS: list[Constraint] = [
    # 1 ----------------------------------------------------------------
    Constraint(
        name="sum_type",
        description=(
            "Your program MUST define at least one sum type with 2+ variants "
            "and use it in a match expression."
        ),
        required_features=["sum_type", "match"],
        prompt_template="""\
You are a toke programming language expert. Generate a toke program for the
following task.

## toke syntax reference
{spec_reference}

## Constraint
Your program MUST define at least one sum type (a type whose fields start
with $-prefixed variant names) with at least 2 variants, and you MUST use
a match expression (`|{{...}}`) on a value of that sum type.

Example 1 -- sum type with match:
```
m=shapekind;
t=$shape{{$circle:f64;$rect:f64}};
f=area(s:$shape):f64{{
  s|{{$circle:r r*r*3.14159;$rect:side side*side}}
}};
```

Example 2 -- sum type for option-like pattern:
```
m=optint;
t=$optint{{$some:i64;$none:bool}};
f=unwrap(o:$optint):i64{{
  o|{{$some:v v;$none:zz 0}}
}};
```

## Task
{task_description}

## Rules
- Output ONLY the toke source code. No explanations, no markdown fences.
- The program must start with `m=` module declaration.
- Parameters and arguments use `;` as separator, NOT `,`.
- Return with `<`, not `return`.
- No comments of any kind.
- The program must be complete and compilable.
""",
        example_output="""\
m=shapekind;
t=$shape{$circle:f64;$rect:f64};
f=area(s:$shape):f64{
  s|{$circle:r r*r*3.14159;$rect:side side*side}
};""",
        difficulty=2,
        validation_criteria=[
            "Contains a type declaration with $-prefixed variant names",
            "Contains a match expression using |{...}",
        ],
    ),
    # 2 ----------------------------------------------------------------
    Constraint(
        name="error_propagation",
        description=(
            "Your program MUST define an error type and propagate errors "
            "using the ! operator."
        ),
        required_features=["error_type", "error_propagation"],
        prompt_template="""\
You are a toke programming language expert. Generate a toke program for the
following task.

## toke syntax reference
{spec_reference}

## Constraint
Your program MUST define an error type (sum type used as an error union)
and propagate errors using the `!` operator in at least one call site.

Example -- error propagation:
```
m=safediv;
t=$matherr{{$divzero:bool}};
f=divide(a:f64;b:f64):f64!$matherr{{
  if(b=0.0){{<$matherr{{$divzero:true}}}};
  <a/b
}};
f=calc(x:f64;y:f64):f64!$matherr{{
  let r=divide(x;y)!$matherr.$divzero;
  <r+1.0
}};
```

## Task
{task_description}

## Rules
- Output ONLY the toke source code. No explanations, no markdown fences.
- The program must start with `m=` module declaration.
- Parameters and arguments use `;` as separator, NOT `,`.
- Return with `<`, not `return`.
- No comments of any kind.
- The program must be complete and compilable.
""",
        example_output="""\
m=safediv;
t=$matherr{$divzero:bool};
f=divide(a:f64;b:f64):f64!$matherr{
  if(b=0.0){<$matherr{$divzero:true}};
  <a/b
};
f=calc(x:f64;y:f64):f64!$matherr{
  let r=divide(x;y)!$matherr.$divzero;
  <r+1.0
};""",
        difficulty=3,
        validation_criteria=[
            "Contains an error type declaration",
            "Contains a function with !$errtype return signature",
            "Contains error propagation with ! operator in a call",
        ],
    ),
    # 3 ----------------------------------------------------------------
    Constraint(
        name="match_3arms",
        description=(
            "Your program MUST contain a match expression with at least 3 arms."
        ),
        required_features=["match", "sum_type_3_variants"],
        prompt_template="""\
You are a toke programming language expert. Generate a toke program for the
following task.

## toke syntax reference
{spec_reference}

## Constraint
Your program MUST contain a match expression with at least 3 arms. This
requires a sum type with at least 3 variants.

Example -- 3-arm match:
```
m=traffic;
t=$light{{$red:bool;$yellow:bool;$green:bool}};
f=action(l:$light):i64{{
  l|{{$red:zz 0;$yellow:zz 1;$green:zz 2}}
}};
```

## Task
{task_description}

## Rules
- Output ONLY the toke source code. No explanations, no markdown fences.
- The program must start with `m=` module declaration.
- Parameters and arguments use `;` as separator, NOT `,`.
- Return with `<`, not `return`.
- No comments of any kind.
- The program must be complete and compilable.
""",
        example_output="""\
m=traffic;
t=$light{$red:bool;$yellow:bool;$green:bool};
f=action(l:$light):i64{
  l|{$red:zz 0;$yellow:zz 1;$green:zz 2}
};""",
        difficulty=2,
        validation_criteria=[
            "Contains a sum type with 3+ variants",
            "Contains a match expression with 3+ arms (3+ semicolons inside |{...})",
        ],
    ),
    # 4 ----------------------------------------------------------------
    Constraint(
        name="map_type",
        description=(
            "Your program MUST use a map type @(K:V) and perform at least "
            "one lookup."
        ),
        required_features=["map_type", "map_lookup"],
        prompt_template="""\
You are a toke programming language expert. Generate a toke program for the
following task.

## toke syntax reference
{spec_reference}

## Constraint
Your program MUST use a map type `@(K:V)` in at least one function
signature or binding, and perform at least one lookup on the map.

Example -- map usage:
```
m=scores;
f=lookup(db:@($str:i64);name:$str):i64{{
  let val=db.get(name);
  val|{{$ok:v v;$err:e 0}}
}};
```

## Task
{task_description}

## Rules
- Output ONLY the toke source code. No explanations, no markdown fences.
- The program must start with `m=` module declaration.
- Parameters and arguments use `;` as separator, NOT `,`.
- Return with `<`, not `return`.
- No comments of any kind.
- The program must be complete and compilable.
""",
        example_output="""\
m=scores;
f=lookup(db:@($str:i64);name:$str):i64{
  let val=db.get(name);
  val|{$ok:v v;$err:e 0}
};""",
        difficulty=3,
        validation_criteria=[
            "Contains @( with a colon inside (map type syntax)",
            "Contains a .get( call on a map value",
        ],
    ),
    # 5 ----------------------------------------------------------------
    Constraint(
        name="arena_block",
        description=(
            "Your program MUST use an arena block for memory management."
        ),
        required_features=["arena"],
        prompt_template="""\
You are a toke programming language expert. Generate a toke program for the
following task.

## toke syntax reference
{spec_reference}

## Constraint
Your program MUST use an arena block `{{arena ... }}` in at least one
function. All allocations inside the arena block are freed when the scope
exits.

Example -- arena block:
```
m=arenaex;
t=$node{{val:i64;next:i64}};
f=build():i64{{
  let result=mut.0;
  {{arena
    let a=$node{{val:1;next:0}};
    let b=$node{{val:2;next:a.val}};
    result=a.val+b.val
  }};
  <result
}};
```

## Task
{task_description}

## Rules
- Output ONLY the toke source code. No explanations, no markdown fences.
- The program must start with `m=` module declaration.
- Parameters and arguments use `;` as separator, NOT `,`.
- Return with `<`, not `return`.
- No comments of any kind.
- The program must be complete and compilable.
""",
        example_output="""\
m=arenaex;
t=$node{val:i64;next:i64};
f=build():i64{
  let result=mut.0;
  {arena
    let a=$node{val:1;next:0};
    let b=$node{val:2;next:a.val};
    result=a.val+b.val
  };
  <result
};""",
        difficulty=4,
        validation_criteria=[
            "Contains {arena keyword",
            "Arena block contains allocations or bindings",
        ],
    ),
    # 6 ----------------------------------------------------------------
    Constraint(
        name="multi_param",
        description=(
            "Your program MUST have at least one function with 4+ parameters."
        ),
        required_features=["multi_param"],
        prompt_template="""\
You are a toke programming language expert. Generate a toke program for the
following task.

## toke syntax reference
{spec_reference}

## Constraint
Your program MUST have at least one function with 4 or more parameters.
Remember that parameters are separated by `;` in toke.

Example -- multi-parameter function:
```
m=rect;
f=area(x1:f64;y1:f64;x2:f64;y2:f64):f64{{
  let w=x2-x1;
  let h=y2-y1;
  <w*h
}};
```

## Task
{task_description}

## Rules
- Output ONLY the toke source code. No explanations, no markdown fences.
- The program must start with `m=` module declaration.
- Parameters and arguments use `;` as separator, NOT `,`.
- Return with `<`, not `return`.
- No comments of any kind.
- The program must be complete and compilable.
""",
        example_output="""\
m=rect;
f=area(x1:f64;y1:f64;x2:f64;y2:f64):f64{
  let w=x2-x1;
  let h=y2-y1;
  <w*h
};""",
        difficulty=1,
        validation_criteria=[
            "Contains a function declaration with 3+ semicolons between ( and )",
        ],
    ),
    # 7 ----------------------------------------------------------------
    Constraint(
        name="nested_struct",
        description=(
            "Your program MUST define a struct containing a field of another "
            "struct type."
        ),
        required_features=["nested_struct"],
        prompt_template="""\
You are a toke programming language expert. Generate a toke program for the
following task.

## toke syntax reference
{spec_reference}

## Constraint
Your program MUST define at least two struct types where one struct has a
field whose type is the other struct. You must access a nested field
(e.g. `outer.inner.field`).

Example -- nested structs:
```
m=geo;
t=$point{{x:f64;y:f64}};
t=$line{{start:$point;end:$point}};
f=length(l:$line):f64{{
  let dx=l.end.x-l.start.x;
  let dy=l.end.y-l.start.y;
  <dx*dx+dy*dy
}};
```

## Task
{task_description}

## Rules
- Output ONLY the toke source code. No explanations, no markdown fences.
- The program must start with `m=` module declaration.
- Parameters and arguments use `;` as separator, NOT `,`.
- Return with `<`, not `return`.
- No comments of any kind.
- The program must be complete and compilable.
""",
        example_output="""\
m=geo;
t=$point{x:f64;y:f64};
t=$line{start:$point;end:$point};
f=length(l:$line):f64{
  let dx=l.end.x-l.start.x;
  let dy=l.end.y-l.start.y;
  <dx*dx+dy*dy
};""",
        difficulty=2,
        validation_criteria=[
            "Contains at least two t= type declarations",
            "One type references the other as a field type ($typename)",
            "Contains nested field access (two dots in a row like a.b.c)",
        ],
    ),
    # 8 ----------------------------------------------------------------
    Constraint(
        name="error_3variant",
        description=(
            "Your program MUST define an error type with at least 3 variants."
        ),
        required_features=["error_type", "error_3_variants", "match"],
        prompt_template="""\
You are a toke programming language expert. Generate a toke program for the
following task.

## toke syntax reference
{spec_reference}

## Constraint
Your program MUST define an error type with at least 3 variants and use it
as a return error union. You must match on the error with all 3+ arms.

Example -- 3-variant error type:
```
m=validate;
t=$valerr{{$empty:bool;$toolong:$str;$badchar:$str}};
f=check(s:$str):bool!$valerr{{
  if(s.len=0){{<$valerr{{$empty:true}}}};
  if(s.len>100){{<$valerr{{$toolong:s}}}};
  <true
}};
f=run(s:$str):bool{{
  let r=check(s);
  r|{{$ok:v v;$err:e false}}
}};
```

## Task
{task_description}

## Rules
- Output ONLY the toke source code. No explanations, no markdown fences.
- The program must start with `m=` module declaration.
- Parameters and arguments use `;` as separator, NOT `,`.
- Return with `<`, not `return`.
- No comments of any kind.
- The program must be complete and compilable.
""",
        example_output="""\
m=validate;
t=$valerr{$empty:bool;$toolong:$str;$badchar:$str};
f=check(s:$str):bool!$valerr{
  if(s.len=0){<$valerr{$empty:true}};
  if(s.len>100){<$valerr{$toolong:s}};
  <true
};
f=run(s:$str):bool{
  let r=check(s);
  r|{$ok:v v;$err:e false}
};""",
        difficulty=3,
        validation_criteria=[
            "Contains an error type with 3+ $-prefixed variant fields",
            "Contains a function with !$ error union return",
            "Contains a match expression handling the error",
        ],
    ),
    # 9 ----------------------------------------------------------------
    Constraint(
        name="error_return_union",
        description=(
            "Your program MUST have a function returning an error union "
            "and another function that calls it and handles the result."
        ),
        required_features=["error_type", "error_union_return", "match"],
        prompt_template="""\
You are a toke programming language expert. Generate a toke program for the
following task.

## toke syntax reference
{spec_reference}

## Constraint
Your program MUST have at least one function whose return type is an error
union (e.g. `:$sometype!$someerr`) and at least one caller that handles
the result using a match expression.

Example -- error union return:
```
m=parse;
t=$parseerr{{$badformat:bool;$overflow:bool}};
f=parseint(s:$str):i64!$parseerr{{
  if(s.len=0){{<$parseerr{{$badformat:true}}}};
  <42
}};
f=tryparse(s:$str):i64{{
  let r=parseint(s);
  r|{{$ok:v v;$err:e 0}}
}};
```

## Task
{task_description}

## Rules
- Output ONLY the toke source code. No explanations, no markdown fences.
- The program must start with `m=` module declaration.
- Parameters and arguments use `;` as separator, NOT `,`.
- Return with `<`, not `return`.
- No comments of any kind.
- The program must be complete and compilable.
""",
        example_output="""\
m=parse;
t=$parseerr{$badformat:bool;$overflow:bool};
f=parseint(s:$str):i64!$parseerr{
  if(s.len=0){<$parseerr{$badformat:true}};
  <42
};
f=tryparse(s:$str):i64{
  let r=parseint(s);
  r|{$ok:v v;$err:e 0}
};""",
        difficulty=3,
        validation_criteria=[
            "Contains a function with !$ in its return type",
            "Contains a match expression with $ok and $err arms",
        ],
    ),
]


class ConstraintGenerator:
    """Generates prompts from constraint templates for toke corpus generation."""

    def __init__(self) -> None:
        self._constraints: dict[str, Constraint] = {
            c.name: c for c in _CONSTRAINTS
        }

    @property
    def constraints(self) -> dict[str, Constraint]:
        return dict(self._constraints)

    def list_constraints(self) -> list[str]:
        """Return all constraint names."""
        return list(self._constraints.keys())

    def get_constraint(self, name: str) -> Constraint:
        """Return a single constraint by name."""
        if name not in self._constraints:
            raise KeyError(f"Unknown constraint: {name!r}")
        return self._constraints[name]

    def generate_prompt(
        self, task_description: str, constraint_name: str
    ) -> str:
        """Fill in a constraint template with the given task description."""
        constraint = self.get_constraint(constraint_name)
        return constraint.prompt_template.format(
            task_description=task_description,
            spec_reference=SPEC_REFERENCE,
        )

    def generate_batch(
        self,
        task_descriptions: list[str],
        constraints_per_task: int = 2,
        rng: random.Random | None = None,
    ) -> list[tuple[str, str]]:
        """Pair each task with *constraints_per_task* random constraints.

        Returns a list of (prompt, constraint_name) tuples.
        """
        if rng is None:
            rng = random.Random()
        names = self.list_constraints()
        results: list[tuple[str, str]] = []
        for task in task_descriptions:
            chosen = rng.sample(names, min(constraints_per_task, len(names)))
            for cname in chosen:
                prompt = self.generate_prompt(task, cname)
                results.append((prompt, cname))
        return results
