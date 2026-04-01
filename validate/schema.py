"""Validate corpus entries against corpus/schema.json.

The JSON Schema is loaded once on module import and reused for every
validation call.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import jsonschema

logger = logging.getLogger(__name__)

# Resolve the schema path relative to this file's location.
# validate/ sits beside corpus/ under the repo root.
_SCHEMA_PATH: Path = Path(__file__).resolve().parent.parent / "corpus" / "schema.json"

_schema: dict | None = None


def _load_schema() -> dict:
    """Load and cache the corpus entry JSON schema."""
    global _schema  # noqa: PLW0603
    if _schema is None:
        with open(_SCHEMA_PATH, encoding="utf-8") as fh:
            _schema = json.load(fh)
        logger.debug("Loaded corpus schema from %s", _SCHEMA_PATH)
    return _schema


def validate_entry(entry: dict) -> tuple[bool, list[str]]:
    """Validate a single corpus entry dict against the normative schema.

    Returns:
        A tuple of (valid, error_messages).  When *valid* is True the
        error list is empty.
    """
    schema = _load_schema()
    validator = jsonschema.Draft7Validator(schema)
    errors: list[str] = []

    for error in sorted(validator.iter_errors(entry), key=lambda e: list(e.path)):
        path = ".".join(str(p) for p in error.absolute_path) or "(root)"
        errors.append(f"{path}: {error.message}")

    if errors:
        logger.debug("Schema validation failed with %d error(s)", len(errors))

    return (len(errors) == 0, errors)
