"""Auto-fixer for common LLM syntax mistakes in toke source code.

Applies mechanical regex-based replacements before compilation,
rescuing programs that would otherwise need expensive LLM correction.
"""

import logging
import re

logger = logging.getLogger(__name__)


class AutoFixer:
    """Fix common LLM mistakes in toke source without an LLM call."""

    def fix(self, source: str) -> tuple[str, list[str]]:
        """Apply all fixes. Returns (fixed_source, list_of_fixes_applied)."""
        fixes: list[str] = []
        s = source

        # Fix double braces (LLM copies {{/}} from format-string templates)
        s, f = self._fix_double_braces(s)
        fixes.extend(f)

        # Strip comments first (they're illegal in toke)
        s, f = self._strip_comments(s)
        fixes.extend(f)

        # Keyword replacements
        s, f = self._fix_for_loop(s)
        fixes.extend(f)

        s, f = self._fix_while_loop(s)
        fixes.extend(f)

        s, f = self._fix_else(s)
        fixes.extend(f)

        s, f = self._fix_elif(s)
        fixes.extend(f)

        s, f = self._fix_semicolon_before_el(s)
        fixes.extend(f)

        s, f = self._fix_return(s)
        fixes.extend(f)

        # Mutable binding syntax
        s, f = self._fix_let_mut(s)
        fixes.extend(f)

        # Modulo operator
        s, f = self._fix_modulo(s)
        fixes.extend(f)

        # While-style lp (only condition, no init/update)
        s, f = self._fix_lp_while(s)
        fixes.extend(f)

        # Fix && and || operators
        s, f = self._fix_logical_ops(s)
        fixes.extend(f)

        # Fix underscore identifiers (not in Profile 1)
        s, f = self._fix_underscore_ids(s)
        fixes.extend(f)

        # Fix empty-init lp(;cond;) patterns
        s, f = self._fix_lp_empty_init(s)
        fixes.extend(f)

        # Operator replacements (order matters: != before <=, <= before = fix)
        s, f = self._fix_not_equals(s)
        fixes.extend(f)

        s, f = self._fix_lte_gte(s)
        fixes.extend(f)

        s, f = self._fix_double_equals(s)
        fixes.extend(f)

        s, f = self._fix_increment(s)
        fixes.extend(f)

        s, f = self._fix_compound_assign(s)
        fixes.extend(f)

        # Type/literal replacements
        s, f = self._fix_bool_literals(s)
        fixes.extend(f)

        s, f = self._fix_string_type(s)
        fixes.extend(f)

        # Reassignment mut fix
        s, f = self._fix_reassign_mut(s)
        fixes.extend(f)

        # Type mismatch: .len returns u64, comparisons with i64 literals
        s, f = self._fix_len_type(s)
        fixes.extend(f)

        # Separator fixes
        s, f = self._fix_commas_in_params(s)
        fixes.extend(f)

        return s, fixes

    def has_fixes(self, source: str) -> bool:
        """Quick check if any patterns match."""
        _, fixes = self.fix(source)
        return len(fixes) > 0

    def _fix_double_braces(self, s: str) -> tuple[str, list[str]]:
        """Replace {{ with { and }} with } — LLMs copy format-string escaping."""
        new = s.replace('{{', '{').replace('}}', '}')
        if new != s:
            return new, ['{{->{ }}->}']
        return s, []

    def _strip_comments(self, s: str) -> tuple[str, list[str]]:
        fixes = []
        # Block comments /* ... */
        new = re.sub(r'/\*.*?\*/', '', s, flags=re.DOTALL)
        if new != s:
            fixes.append('strip_block_comments')
            s = new
        # Line comments //
        new = re.sub(r'//[^\n]*', '', s)
        if new != s:
            fixes.append('strip_line_comments')
            s = new
        # Hash comments #
        new = re.sub(r'^#[^\n]*', '', s, flags=re.MULTILINE)
        if new != s:
            fixes.append('strip_hash_comments')
            s = new
        return s, fixes

    def _fix_for_loop(self, s: str) -> tuple[str, list[str]]:
        new = re.sub(r'\bfor\s*\(', 'lp(', s)
        if new != s:
            return new, ['for->lp']
        return s, []

    def _fix_while_loop(self, s: str) -> tuple[str, list[str]]:
        new = re.sub(r'\bwhile\s*\(', 'lp(', s)
        if new != s:
            return new, ['while->lp']
        return s, []

    def _fix_else(self, s: str) -> tuple[str, list[str]]:
        # First catch 'else if(' → 'el{if('
        new = re.sub(r'\belse\s+if\s*\(', 'el{if(', s)
        # Then catch remaining 'else{' or 'else {'
        new = re.sub(r'\belse\s*\{', 'el{', new)
        if new != s:
            return new, ['else->el']
        return s, []

    def _fix_elif(self, s: str) -> tuple[str, list[str]]:
        # elif( → el{if(  — but we need to add a closing } later
        # This is approximate; complex elif chains may not be perfectly fixed
        new = re.sub(r'\belif\s*\(', 'el{if(', s)
        if new != s:
            return new, ['elif->el{if']
        return s, []

    def _fix_semicolon_before_el(self, s: str) -> tuple[str, list[str]]:
        """Fix '};el{' or '}; el{' → '}el{' — semicolons break if/el chains."""
        new = re.sub(r'\}\s*;\s*el\s*\{', '}el{', s)
        if new != s:
            return new, ['};el->}el']
        return s, []

    def _fix_return(self, s: str) -> tuple[str, list[str]]:
        new = re.sub(r'\breturn\s+', '<', s)
        if new != s:
            # Also handle bare 'return;' → '<;' is wrong, just strip
            new = re.sub(r'\breturn;', '<;', new)
            return new, ['return-><']
        return s, []

    def _fix_modulo(self, s: str) -> tuple[str, list[str]]:
        """Fix a%b → a-a/b*b (toke has no % operator)."""
        new = re.sub(r'(\w+)\s*%\s*(\w+)', r'(\1-\1/\2*\2)', s)
        if new != s:
            return new, ['%->a-a/b*b']
        return s, []

    def _fix_lp_while(self, s: str) -> tuple[str, list[str]]:
        """Fix lp(cond){body} → lp(let _w=0;cond;_w=0){body}.

        When lp has only a condition (no semicolons inside parens),
        it's a while-style loop missing init and update parts.
        """
        def _fix_match(m):
            cond = m.group(1)
            # Only fix if there are no semicolons in the condition
            # (which would indicate it already has init;cond;update form)
            if ';' in cond:
                return m.group(0)  # already has 3 parts, return as-is (incl. {)
            return f'lp(let _w=0;{cond};_w=0){{'

        new = re.sub(r'lp\(([^)]+)\)\s*\{', _fix_match, s)
        if new != s:
            return new, ['lp(cond)->lp(init;cond;update)']
        return s, []

    def _fix_let_mut(self, s: str) -> tuple[str, list[str]]:
        """Fix 'let mut x=val' → 'let x=mut.val'."""
        new = re.sub(r'\blet\s+mut\s+(\w+)\s*=\s*', r'let \1=mut.', s)
        if new != s:
            return new, ['let mut->let x=mut.']
        return s, []

    def _fix_not_equals(self, s: str) -> tuple[str, list[str]]:
        """Fix != operator: a!=b → !(a=b)."""
        new = re.sub(r'(\w[\w.\[\]]*)\s*!=\s*(\w[\w.\[\]()]*)', r'!(\1=\2)', s)
        if new != s:
            return new, ['!=->!(=)']
        return s, []

    def _fix_logical_ops(self, s: str) -> tuple[str, list[str]]:
        """Replace && and || in if-conditions — not supported in toke.

        && in if(A && B){body} → if(A){if(B){body}}
        || in if(A || B){body} → let zor=mut.false;if(A){zor=true};if(B){zor=true};if(zor){body}
        """
        fixes = []
        if '&&' in s:
            new = self._fix_and_in_ifs(s)
            if new != s:
                fixes.append('&&->nested if')
                s = new
        if '||' in s:
            new = self._fix_or_in_ifs(s)
            if new != s:
                fixes.append('||->flag+if')
                s = new
        return s, fixes

    def _extract_balanced(self, s: str, start: int, open_ch: str, close_ch: str) -> tuple:
        """Extract content between balanced delimiters. Returns (content, end_pos) or (None, start)."""
        if start >= len(s) or s[start] != open_ch:
            return None, start
        depth = 0
        i = start
        in_str = False
        while i < len(s):
            if s[i] == '"' and (i == 0 or s[i - 1] != '\\'):
                in_str = not in_str
            elif not in_str:
                if s[i] == open_ch:
                    depth += 1
                elif s[i] == close_ch:
                    depth -= 1
                    if depth == 0:
                        return s[start + 1:i], i + 1
            i += 1
        return None, start

    def _fix_and_in_ifs(self, s: str) -> str:
        """Transform if(A && B){body} → if(A){if(B){body}}.

        With el: if(A && B){body}el{alt} →
          let zand=mut.false;if(A){if(B){zand=true}};if(zand){body}el{alt}
        """
        i = 0
        result = []
        while i < len(s):
            # Look for 'if(' not preceded by alphanumeric
            if (s[i:i + 3] == 'if(' and (i == 0 or not s[i - 1].isalnum())):
                cond, paren_end = self._extract_balanced(s, i + 2, '(', ')')
                if cond is not None and '&&' in cond and '||' not in cond:
                    # Skip whitespace to find body brace
                    j = paren_end
                    while j < len(s) and s[j] in ' \t\n':
                        j += 1
                    if j < len(s) and s[j] == '{':
                        body, body_end = self._extract_balanced(s, j, '{', '}')
                        if body is not None:
                            # Split condition on first &&
                            parts = cond.split('&&', 1)
                            a = parts[0].strip()
                            b = parts[1].strip()

                            # Check for el{...} after body
                            k = body_end
                            while k < len(s) and s[k] in ' \t\n':
                                k += 1
                            if k < len(s) and s[k:k + 2] == 'el':
                                ek = k + 2
                                while ek < len(s) and s[ek] in ' \t\n':
                                    ek += 1
                                if ek < len(s) and s[ek] == '{':
                                    alt, alt_end = self._extract_balanced(s, ek, '{', '}')
                                    if alt is not None:
                                        repl = (f'let zand=mut.false;if({a})'
                                                f'{{if({b}){{zand=true}}}};'
                                                f'if(zand){{{body}}}el{{{alt}}}')
                                        result.append(repl)
                                        i = alt_end
                                        continue
                            # No else — simple nested if
                            repl = f'if({a}){{if({b}){{{body}}}}}'
                            result.append(repl)
                            i = body_end
                            continue
            result.append(s[i])
            i += 1
        return ''.join(result)

    def _fix_or_in_ifs(self, s: str) -> str:
        """Transform if(A || B){body} → let zor=mut.false;if(A){zor=true};if(B){zor=true};if(zor){body}."""
        i = 0
        result = []
        while i < len(s):
            if (s[i:i + 3] == 'if(' and (i == 0 or not s[i - 1].isalnum())):
                cond, paren_end = self._extract_balanced(s, i + 2, '(', ')')
                if cond is not None and '||' in cond and '&&' not in cond:
                    j = paren_end
                    while j < len(s) and s[j] in ' \t\n':
                        j += 1
                    if j < len(s) and s[j] == '{':
                        body, body_end = self._extract_balanced(s, j, '{', '}')
                        if body is not None:
                            parts = cond.split('||', 1)
                            a = parts[0].strip()
                            b = parts[1].strip()

                            # Check for el{...}
                            k = body_end
                            while k < len(s) and s[k] in ' \t\n':
                                k += 1
                            el_part = ''
                            end_pos = body_end
                            if k < len(s) and s[k:k + 2] == 'el':
                                ek = k + 2
                                while ek < len(s) and s[ek] in ' \t\n':
                                    ek += 1
                                if ek < len(s) and s[ek] == '{':
                                    alt, alt_end = self._extract_balanced(s, ek, '{', '}')
                                    if alt is not None:
                                        el_part = f'el{{{alt}}}'
                                        end_pos = alt_end

                            repl = (f'let zor=mut.false;if({a}){{zor=true}};'
                                    f'if({b}){{zor=true}};'
                                    f'if(zor){{{body}}}{el_part}')
                            result.append(repl)
                            i = end_pos
                            continue
            result.append(s[i])
            i += 1
        return ''.join(result)

    def _fix_underscore_ids(self, s: str) -> tuple[str, list[str]]:
        """Replace _X identifiers with valid names (underscore not in Profile 1)."""
        # Replace all _identifier patterns with z-prefixed versions
        new = re.sub(r'\b_([a-zA-Z]\w*)\b', r'z\1', s)
        # Also handle bare _ used as discard
        new = re.sub(r'\blet\s+_\s*=', 'let zz=', new)
        if new != s:
            return new, ['_id->zid identifier']
        return s, []

    def _fix_lp_empty_init(self, s: str) -> tuple[str, list[str]]:
        """Fix lp(;cond;){...} → lp(let _w=0;cond;_w=0){...}."""
        new = re.sub(r'lp\(\s*;([^;]+);\s*\)\s*\{', r'lp(let zw=0;\1;zw=0){', s)
        if new != s:
            return new, ['lp(;cond;)->lp(init;cond;update)']
        return s, []

    def _fix_lte_gte(self, s: str) -> tuple[str, list[str]]:
        """Fix <= and >= operators using negation: a<=b → !(a>b), a>=b → !(a<b)."""
        fixes = []
        # Process <= : a<=b → !(a>b)
        new = re.sub(r'(\w[\w.\[\]]*)\s*<=\s*(\w[\w.\[\]()+-]*)', r'!(\1>\2)', s)
        if new != s:
            fixes.append('<=->!(>)')
            s = new
        # Process >= : a>=b → !(a<b)
        new = re.sub(r'(\w[\w.\[\]]*)\s*>=\s*(\w[\w.\[\]()+-]*)', r'!(\1<\2)', s)
        if new != s:
            fixes.append('>=->!(<)')
            s = new
        return s, fixes

    def _fix_double_equals(self, s: str) -> tuple[str, list[str]]:
        new = re.sub(r'==', '=', s)
        if new != s:
            return new, ['==->single=']
        return s, []

    def _fix_increment(self, s: str) -> tuple[str, list[str]]:
        fixes = []
        # i++ → i=i+1 (postfix)
        new = re.sub(r'\b(\w+)\+\+', r'\1=\1+1', s)
        if new != s:
            fixes.append('i++->i=i+1')
            s = new
        # i-- → i=i-1 (postfix)
        new = re.sub(r'\b(\w+)--', r'\1=\1-1', s)
        if new != s:
            fixes.append('i--->i=i-1')
            s = new
        return s, fixes

    def _fix_compound_assign(self, s: str) -> tuple[str, list[str]]:
        fixes = []
        for op, name in [(r'\+=', '+'), (r'-=', '-'), (r'\*=', '*'), (r'/=', '/')]:
            pattern = r'\b(\w+)\s*' + op + r'\s*'
            def repl(m, op=name):
                var = m.group(1)
                return f'{var}={var}{op}'
            new = re.sub(pattern, repl, s)
            if new != s:
                fixes.append(f'{name}= expanded')
                s = new
        return s, fixes

    def _fix_bool_literals(self, s: str) -> tuple[str, list[str]]:
        fixes = []
        new = re.sub(r'\bTrue\b', 'true', s)
        if new != s:
            fixes.append('True->true')
            s = new
        new = re.sub(r'\bFalse\b', 'false', s)
        if new != s:
            fixes.append('False->false')
            s = new
        return s, fixes

    def _fix_string_type(self, s: str) -> tuple[str, list[str]]:
        new = re.sub(r'\bString\b', 'Str', s)
        if new != s:
            return new, ['String->Str']
        return s, []

    def _fix_len_type(self, s: str) -> tuple[str, list[str]]:
        """Fix .len comparisons: .len returns u64, so add 'as u64' to integer RHS."""
        fixes = []
        # .len=0 → .len=0 as u64 (and similar with other small literals)
        new = re.sub(r'\.len\s*=\s*(\d+)(?!\s*as)', r'.len=\1 as u64', s)
        if new != s:
            fixes.append('.len=N->as u64')
            s = new
        # .len-1 → .len-(1 as u64)  or  .len < n where n is i64
        # Simpler: cast .len to i64 in arithmetic: arr.len → (arr.len as i64)
        # Actually, safer to just add cast on .len when used with - or + and integer
        new = re.sub(r'(\w+)\.len\s*-\s*(\d+)(?!\s*as)', r'(\1.len-\2 as u64)', s)
        if new != s:
            fixes.append('.len-N->cast')
            s = new
        new = re.sub(r'(\w+)\.len\s*\+\s*(\d+)(?!\s*as)', r'(\1.len+\2 as u64)', s)
        if new != s:
            fixes.append('.len+N->cast')
            s = new
        return s, fixes

    def _fix_reassign_mut(self, s: str) -> tuple[str, list[str]]:
        """Fix 'x=mut.val' on reassignment (not 'let' lines) → 'x=val'."""
        # Only fix lines that do NOT start with 'let' — those are reassignments
        lines = s.split('\n')
        fixed = False
        result = []
        for line in lines:
            stripped = line.lstrip()
            if not stripped.startswith('let ') and 'mut.' in stripped:
                new_line = re.sub(r'(\w+\s*=\s*)mut\.', r'\1', line)
                if new_line != line:
                    fixed = True
                    line = new_line
            result.append(line)
        if fixed:
            return '\n'.join(result), ['reassign mut. removed']
        return s, []

    def _fix_commas_in_params(self, s: str) -> tuple[str, list[str]]:
        """Replace commas with semicolons inside parentheses (params/args)."""
        # Only replace commas that appear inside balanced parentheses
        # and look like parameter separators (not inside strings)
        result = []
        depth = 0
        in_string = False
        fixed = False
        i = 0
        while i < len(s):
            ch = s[i]
            if ch == '"' and (i == 0 or s[i - 1] != '\\'):
                in_string = not in_string
                result.append(ch)
            elif not in_string:
                if ch == '(':
                    depth += 1
                    result.append(ch)
                elif ch == ')':
                    depth = max(0, depth - 1)
                    result.append(ch)
                elif ch == ',' and depth > 0:
                    result.append(';')
                    fixed = True
                else:
                    result.append(ch)
            else:
                result.append(ch)
            i += 1
        if fixed:
            return ''.join(result), [',->; in params']
        return s, []
