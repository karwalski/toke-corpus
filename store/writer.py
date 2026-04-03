"""Corpus entry writer — validated entries to disk per schema.json.

Writes accepted corpus entries as JSON files, validated against the
normative schema before every write. Append-only: existing entries
are never overwritten.

Story 8.1.9 — Corpus writer and metrics dashboard.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

import jsonschema
import tiktoken

from generator.curriculum import TaskSpec
from validate.compiler import CompileResult
from validate.diff_test import DiffResult
from validate.quality import QualityScore

logger = logging.getLogger(__name__)

# Shared tiktoken encoding — loaded once on first use.
_encoding: tiktoken.Encoding | None = None


def _get_encoding() -> tiktoken.Encoding:
    """Return the shared cl100k_base encoding, loading lazily."""
    global _encoding  # noqa: PLW0603
    if _encoding is None:
        _encoding = tiktoken.get_encoding("cl100k_base")
    return _encoding


def count_tokens(text: str) -> int:
    """Count tokens using the cl100k_base encoding."""
    return len(_get_encoding().encode(text))


# ---------------------------------------------------------------------------
# Nested info dataclasses matching schema.json sub-objects
# ---------------------------------------------------------------------------


@dataclass
class ValidationInfo:
    """Compiler validation result stored with a corpus entry."""

    compiler_exit_code: int
    error_codes: list[str] = field(default_factory=list)


@dataclass
class DiffInfo:
    """Differential testing result stored with a corpus entry."""

    languages_agreed: list[str] = field(default_factory=list)
    majority_output: str = ""


@dataclass
class JudgeInfo:
    """Quality judge result stored with a corpus entry."""

    accepted: bool = False
    score: float = 0.0


# ---------------------------------------------------------------------------
# CorpusEntry
# ---------------------------------------------------------------------------


@dataclass
class ReferenceSource:
    """Reference language sources and token counts for token-efficiency measurement."""

    python_source: str = ""
    python_tokens: int = 0
    c_source: str = ""
    c_tokens: int = 0
    java_source: str = ""
    java_tokens: int = 0


@dataclass
class CorpusEntry:
    """A single validated corpus entry matching corpus/schema.json."""

    id: str
    version: int
    phase: str
    task_id: str
    tk_source: str
    tk_tokens: int
    attempts: int
    model: str
    validation: ValidationInfo
    differential: DiffInfo
    judge: JudgeInfo
    references: ReferenceSource = field(default_factory=ReferenceSource)

    def to_dict(self) -> dict:
        """Serialise to a plain dict suitable for JSON encoding."""
        return asdict(self)


# ---------------------------------------------------------------------------
# CorpusWriter
# ---------------------------------------------------------------------------

# Pattern to extract the category suffix from a task_id like "A-MTH-0001".
_CATEGORY_RE = re.compile(r"^[A-Z]-([A-Z]{3})")


def _short_hash(text: str, length: int = 8) -> str:
    """Return a short hex hash of *text* for entry ID uniqueness."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def _extract_category(task_id: str) -> str:
    """Extract the category code from a task_id (e.g. 'A-MTH-0001' -> 'A-MTH')."""
    parts = task_id.split("-")
    if len(parts) >= 2:
        return f"{parts[0]}-{parts[1]}"
    return task_id


class CorpusWriter:
    """Write validated corpus entries to disk.

    File layout::

        corpus_dir/phase_a/{category}/{entry_id}.json

    Each entry is validated against the normative schema before writing.
    Writes are atomic (write to temp file, then rename).

    A *holdout_task_ids* set **must** be provided.  The writer refuses
    to persist any entry whose ``task_id`` is in the holdout set,
    providing a last-line defence against evaluation data leaking into
    the training corpus.
    """

    def __init__(
        self,
        corpus_dir: str,
        schema_path: str | None = None,
        holdout_task_ids: set[str] | None = None,
    ) -> None:
        if not holdout_task_ids:
            raise ValueError(
                "CorpusWriter requires a non-empty holdout_task_ids set. "
                "Refusing to write corpus entries without holdout isolation."
            )
        self._holdout_task_ids: set[str] = holdout_task_ids
        self._corpus_dir = Path(corpus_dir)
        self._count = 0

        # Load the normative JSON schema.
        if schema_path is None:
            schema_path_resolved = (
                Path(__file__).resolve().parent.parent / "corpus" / "schema.json"
            )
        else:
            schema_path_resolved = Path(schema_path)

        with open(schema_path_resolved, encoding="utf-8") as fh:
            self._schema: dict = json.load(fh)

        self._validator = jsonschema.Draft7Validator(self._schema)
        logger.debug(
            "CorpusWriter initialised: corpus_dir=%s, schema=%s",
            self._corpus_dir,
            schema_path_resolved,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_entry(
        self,
        task: TaskSpec,
        toke_source: str,
        model_name: str,
        attempts: int,
        compile_result: CompileResult,
        diff_result: DiffResult,
        quality: QualityScore,
        tk_tokens: int,
        python_src: str = "",
        c_src: str = "",
        java_src: str = "",
    ) -> CorpusEntry:
        """Assemble a CorpusEntry from component pipeline results.

        The entry ID is deterministic: ``{phase}-{task_id}-{short_hash}``.
        """
        # Derive phase letter from the task_id prefix (e.g. "A" from "A-MTH-0001").
        phase = task.task_id[0] if task.task_id else "A"
        short = _short_hash(toke_source)
        entry_id = f"{phase}-{task.task_id}-{short}"

        # Extract error codes from compiler stderr (E-codes like E1001).
        error_codes: list[str] = []
        if compile_result.stderr:
            error_codes = re.findall(r"E\d{4}", compile_result.stderr)

        return CorpusEntry(
            id=entry_id,
            version=1,
            phase=phase,
            task_id=task.task_id,
            tk_source=toke_source,
            tk_tokens=tk_tokens,
            attempts=attempts,
            model=model_name,
            validation=ValidationInfo(
                compiler_exit_code=compile_result.exit_code,
                error_codes=error_codes,
            ),
            differential=DiffInfo(
                languages_agreed=list(diff_result.languages_agreed),
                majority_output=diff_result.majority_output,
            ),
            judge=JudgeInfo(
                accepted=quality.accepted,
                score=quality.score,
            ),
            references=ReferenceSource(
                python_source=python_src,
                python_tokens=count_tokens(python_src) if python_src else 0,
                c_source=c_src,
                c_tokens=count_tokens(c_src) if c_src else 0,
                java_source=java_src,
                java_tokens=count_tokens(java_src) if java_src else 0,
            ),
        )

    def write(self, entry: CorpusEntry) -> str:
        """Validate and write *entry* to disk. Return the file path.

        Raises:
            ValueError: If the entry's task_id is in the holdout set.
            jsonschema.ValidationError: If the entry does not conform to
                the normative schema.
            FileExistsError: If an entry with the same ID already exists
                (append-only guarantee).
        """
        # Hard reject: holdout tasks must never be written to the corpus.
        if entry.task_id in self._holdout_task_ids:
            raise ValueError(
                f"HOLDOUT VIOLATION: task_id '{entry.task_id}' is in the "
                f"holdout set and must not be written to the training corpus."
            )

        entry_dict = entry.to_dict()

        # Schema validation — fail loudly on violation.
        errors = list(self._validator.iter_errors(entry_dict))
        if errors:
            messages = "; ".join(e.message for e in errors)
            raise jsonschema.ValidationError(
                f"Schema validation failed for entry {entry.id}: {messages}"
            )

        # Determine output path.
        category = _extract_category(entry.task_id)
        phase_dir = f"phase_{entry.phase.lower()}"
        out_dir = self._corpus_dir / phase_dir / category
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{entry.id}.json"

        # Append-only: never overwrite.
        if out_path.exists():
            raise FileExistsError(
                f"Entry already exists at {out_path} — refusing to overwrite"
            )

        # Atomic write: write to temp file in the same directory, then rename.
        fd, tmp_path_str = tempfile.mkstemp(
            dir=str(out_dir), suffix=".tmp", prefix=".entry_"
        )
        tmp_path = Path(tmp_path_str)
        try:
            with open(fd, "w", encoding="utf-8") as fh:
                json.dump(entry_dict, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            tmp_path.rename(out_path)
        except BaseException:
            # Clean up the temp file on any failure.
            tmp_path.unlink(missing_ok=True)
            raise

        self._count += 1
        logger.debug("Wrote corpus entry %s -> %s", entry.id, out_path)
        return str(out_path)

    def count(self) -> int:
        """Return the number of entries written in this session."""
        return self._count

    def load(self, entry_id: str) -> CorpusEntry | None:
        """Read back a previously written entry by ID.

        Searches all phase/category subdirectories. Returns None if not found.
        """
        for json_path in self._corpus_dir.rglob(f"{entry_id}.json"):
            try:
                with open(json_path, encoding="utf-8") as fh:
                    data = json.load(fh)
                return CorpusEntry(
                    id=data["id"],
                    version=data["version"],
                    phase=data["phase"],
                    task_id=data["task_id"],
                    tk_source=data["tk_source"],
                    tk_tokens=data["tk_tokens"],
                    attempts=data.get("attempts", 1),
                    model=data.get("model", ""),
                    validation=ValidationInfo(
                        compiler_exit_code=data["validation"]["compiler_exit_code"],
                        error_codes=data["validation"].get("error_codes", []),
                    ),
                    differential=DiffInfo(
                        languages_agreed=data["differential"]["languages_agreed"],
                        majority_output=data["differential"]["majority_output"],
                    ),
                    judge=JudgeInfo(
                        accepted=data["judge"]["accepted"],
                        score=data["judge"]["score"],
                    ),
                )
            except (json.JSONDecodeError, KeyError) as exc:
                logger.warning("Failed to load entry %s from %s: %s", entry_id, json_path, exc)
                return None
        return None
