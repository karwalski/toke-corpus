"""Shared renderer for the ``prompts/generate_*.md`` task templates.

The task templates are ``str.format`` templates: they carry the
``{category}``, ``{task_description}`` and ``{expected_signature}``
placeholders and write every literal brace doubled (``{{ }}``) so that
toke code examples survive formatting. A template sent without rendering
shows the model raw placeholders and doubled braces, which are not valid
toke (story 131.115).

``main.py``, ``trial/runner.py`` and ``scripts/regenerate.py`` all render
through :func:`render_task_template`; there is one copy of the rule.
"""
from __future__ import annotations

import re

#: The placeholders a task template may use.
TEMPLATE_FIELDS = ("category", "task_description", "expected_signature")

_PLACEHOLDER_RE = re.compile(r"\{(?:%s)\}" % "|".join(TEMPLATE_FIELDS))


def render_task_template(
    template: str,
    *,
    category: str,
    task_description: str,
    expected_signature: str,
) -> str:
    """Fill a task template's placeholders and collapse its doubled braces."""
    return template.format(
        category=category,
        task_description=task_description,
        expected_signature=expected_signature,
    )


def unrendered_markers(text: str) -> list[str]:
    """Return the template markers left in ``text`` (empty when fully rendered).

    A marker is an unfilled ``{category}``/``{task_description}``/
    ``{expected_signature}`` placeholder or a doubled ``{{`` brace. A
    closing ``}}`` is not a marker: rendered toke legitimately closes two
    blocks at once (``{$err:e <$myerr{$badinput:true}}``).
    """
    found = _PLACEHOLDER_RE.findall(text)
    found += ["{{"] * text.count("{{")
    return found
